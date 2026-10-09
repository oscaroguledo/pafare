from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text, TypeDecorator, CHAR, DateTime, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import SQLAlchemyError, DisconnectionError, OperationalError
import asyncio
import time
import uuid
from datetime import datetime, timezone as dt_timezone
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from core.logging import get_structured_logger
from core.exceptions import DatabaseException, APIException

logger = get_structured_logger(__name__)

Base = declarative_base()
CHAR_LENGTH = 255


class GUID(TypeDecorator):
    """Platform-independent GUID: PostgreSQL UUID, or CHAR(36) elsewhere."""
    impl = CHAR

    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        # Always use PostgreSQL - convert UUID to string
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            # Always return UUID object for consistency
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
            return value


class UTCDateTime(TypeDecorator):
    """DateTime(timezone=True) that treats an accidentally-naive datetime as UTC.
    Drivers interpret naive datetimes as local system time, not UTC - normalize before storage and on read-back."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=dt_timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=dt_timezone.utc)
        return value


# Database connection configuration - NOW INITIALIZED LATER
engine_db = None
AsyncSessionDB = None

class DatabaseManager:
    """Enhanced database manager with connection resilience and monitoring."""

    def __init__(self):
        self.engine = None
        self.session_factory = None
        self._connection_failures = 0
        self._last_health_check = 0
        self._health_check_interval = 60  # Check health every 60 seconds
    
    def initialize(self, database_uri: str, env_is_local: bool):
        """Initializes the database engine and session factory."""
        global engine_db, AsyncSessionDB
        if self.engine and self.session_factory: # Prevent re-initialization
            return

        # PostgreSQL with connection pooling
        engine_db = create_async_engine(
            database_uri,
            echo=env_is_local,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
        )

        # Set search_path and session timezone on every new connection. The timezone matters beyond just SELECT display formatting: any naive Python datetime bound as a timestamptz parameter (or written via a raw SQL literal) gets interpreted as being in *this* session timezone before conversion to UTC for storage, silently shifting it by the offset if this isn't UTC. App code should always pass timezone-aware UTC datetimes regardless - this is the defense-in-depth backstop for the times it doesn't.
        @event.listens_for(engine_db.sync_engine, "connect")
        def set_search_path(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("SET search_path TO accounts, catalog, commerce, admin, system, public")
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.close()

        AsyncSessionDB = sessionmaker(
            bind=engine_db,
            class_=AsyncSession,
            expire_on_commit=False
        )
        self.set_engine_and_session_factory(engine_db, AsyncSessionDB)

    def set_engine_and_session_factory(self, engine, session_factory):
        self.engine = engine
        self.session_factory = session_factory

    async def health_check(self) -> dict:
        """Perform database health check."""
        if not self.engine or not self.session_factory:
            return {"status": "uninitialized", "message": "Database not initialized."}

        start_time = time.time()

        try:
            async with self.session_factory() as session:
                # Simple query to test connection
                result = await session.execute(text("SELECT 1"))
                result.fetchone()

                response_time = (time.time() - start_time) * \
                    1000  # Convert to milliseconds

                logger.info(
                    message="Database health check successful",
                    metadata={
                        "response_time_ms": response_time,
                        "connection_failures": self._connection_failures,
                    }
                )

                self._connection_failures = 0  # Reset failure count on success
                self._last_health_check = time.time()

                return {
                    "status": "healthy",
                    "response_time_ms": response_time,
                    "connection_failures": self._connection_failures,
                    "last_check": self._last_health_check,
                }

        except Exception as e:
            self._connection_failures += 1
            response_time = (time.time() - start_time) * 1000

            logger.error(
                message="Database health check failed",
                metadata={
                    "response_time_ms": response_time,
                    "connection_failures": self._connection_failures,
                    "error_type": type(e).__name__,
                },
                exception=e,
            )

            return {
                "status": "unhealthy",
                "response_time_ms": response_time,
                "connection_failures": self._connection_failures,
                "error": str(e),
                "last_check": time.time(),
            }

    async def get_connection_pool_status(self) -> dict:
        """Get connection pool status information."""
        if not self.engine:
            return {"status": "uninitialized", "message": "Database not initialized."}
        
        pool = self.engine.pool

        try:
            return {
                "pool_size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                # Note: 'invalid' method may not be available in all pool types
                "invalid": getattr(pool, 'invalid', lambda: 0)(),
            }
        except Exception as e:
            # Fallback for pool types that don't support all methods - each field is
            # evaluated independently so one unsupported metric can't blow up the whole response.
            def _safe(getter):
                try:
                    return getter()
                except Exception:
                    return 0

            return {
                "pool_size": _safe(lambda: getattr(pool, 'size', lambda: 0)()),
                "checked_in": _safe(lambda: getattr(pool, 'checkedin', lambda: 0)()),
                "checked_out": _safe(lambda: getattr(pool, 'checkedout', lambda: 0)()),
                "overflow": _safe(lambda: getattr(pool, 'overflow', lambda: 0)()),
                "invalid": 0,
                "error": f"Pool status partially unavailable: {str(e)}",
            }

    @asynccontextmanager
    async def get_session_with_retry(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backoff_factor: float = 2.0,
    ) -> AsyncGenerator[AsyncSession, None]:
        """Get database session with retry logic and exponential backoff."""
        if not self.session_factory:
            raise DatabaseException(message="Database session factory not initialized.")

        for attempt in range(max_retries + 1):
            session = None
            try:
                session = self.session_factory()
                if attempt > 0:
                    logger.info(
                        message=f"Database connection successful on attempt {attempt + 1}",
                        metadata={"attempt": attempt + 1,
                                  "max_retries": max_retries}
                    )
                
                # Yield the session - this is where the caller's code runs
                try:
                    yield session
                except Exception:
                    # Rollback on any exception from caller
                    await session.rollback()
                    raise
                finally:
                    # Always close the session
                    await session.close()
                
                # Success - exit the retry loop
                return

            except (SQLAlchemyError, DisconnectionError, OperationalError) as e:
                self._connection_failures += 1
                
                # Clean up the session if it was created
                if session is not None:
                    await session.close()

                if attempt == max_retries:
                    logger.error(
                        message=f"Database connection failed after {max_retries + 1} attempts",
                        metadata={
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "total_failures": self._connection_failures,
                        },
                        exception=e,
                    )
                    raise DatabaseException(
                        message=f"Database connection failed after {max_retries + 1} attempts: {str(e)}",
                    )

                # Calculate delay with exponential backoff
                delay = retry_delay * (backoff_factor ** attempt)

                logger.warning(
                    message=f"Database connection failed on attempt {attempt + 1}, retrying in {delay}s",
                    metadata={
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "retry_delay": delay,
                        "error_type": type(e).__name__,
                    },
                    exception=e,
                )

                await asyncio.sleep(delay)


# Global database manager instance
db_manager = DatabaseManager()

async def initialize_db(database_uri: str, env_is_local: bool, engine=None):
    """Initializes the database manager. PostgreSQL only; requires Alembic migrations."""
    if engine:
        # Use provided engine
        global engine_db, AsyncSessionDB
        engine_db = engine
        AsyncSessionDB = sessionmaker(
            bind=engine_db,
            class_=AsyncSession,
            expire_on_commit=False
        )
        db_manager.set_engine_and_session_factory(engine_db, AsyncSessionDB)
    else:
        # Use default initialization
        db_manager.initialize(database_uri, env_is_local)
        
# Enhanced dependency to get the async session with retry logic
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session with enhanced error handling and retry logic."""
    
    # Ensure database is initialized before getting a session
    if not db_manager.session_factory:
        raise DatabaseException(message="Database session factory not initialized.")

    try:
        async with db_manager.get_session_with_retry() as session:
            logger.info(
                message="Database session created successfully",
            )
            try:
                yield session
            except GeneratorExit:
                # Normal generator exit
                raise
            except Exception:
                # Let exceptions from the calling code propagate
                raise
    except DatabaseException:
        # Re-raise database exceptions (already logged)
        raise
    except HTTPException:
        # Re-raise HTTP exceptions (like 401, 403, etc.) without wrapping
        raise
    except APIException:
        # Re-raise API exceptions without wrapping
        raise
    except (ValueError, ValidationError, RequestValidationError) as e:
        # Re-raise validation errors (including Pydantic validation errors)
        raise
    except SQLAlchemyError as e:
        # Handle database-specific errors
        logger.error(
            message=f"Database error in session: {str(e)}",
            exception=e,
        )
        raise DatabaseException(
            message=f"Database error: {str(e)}",
        )
    except Exception as e:
        # Handle any other unexpected exceptions (but not HTTP/API exceptions)
        logger.error(
            message=f"Unexpected error in database session: {str(e)}",
            exception=e,
        )
        raise DatabaseException(
            message=f"Database session error: {str(e)}",
        )


# Dependency for database health checks
async def get_db_health() -> dict:
    """Get database health status."""
    return await db_manager.health_check()

