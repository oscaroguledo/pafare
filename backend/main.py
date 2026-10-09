import warnings
from contextlib import asynccontextmanager

# Suppress all warnings before imports
warnings.filterwarnings('ignore')

from core.logging import get_structured_logger
from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from core.db import db_manager, initialize_db
from core.config import settings
from core.exceptions import (
    APIException,
    api_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    pydantic_validation_handler,
    sqlalchemy_exception_handler,
    general_exception_handler
)

from api import (
    auth_router, user_router, role_router, permission_router,
    hotel_router, room_type_router, room_router, booking_router, review_router,
    payment_router,
    health_router,
)
import models
from core import redis_cache
import uvicorn

logger = get_structured_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("Application warnings suppressed")
    logger.info("Validating environment configuration...")
    validation_result = settings.validate()

    if not validation_result["is_valid"]:
        logger.error("Environment validation failed!")
        missing = validation_result.get("missing", [])
        if missing:
            logger.error("Missing required vars: %s", missing)
        if settings.ENVIRONMENT.lower() in ["local", "development", "dev"]:
            logger.warning("Continuing with invalid environment in development mode")
        else:
            raise RuntimeError("Invalid environment configuration. Check your .env file.")

    logger.info("Environment validation passed ✅")

    # Initialize database
    try:
        await initialize_db(
            settings.SQLALCHEMY_DATABASE_URI,
            settings.ENVIRONMENT == "local"
        )
        logger.info("Database initialized ✅")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise RuntimeError(f"Database initialization failed: {e}")

    yield

    # --- Shutdown ---
    logger.info("Application shutting down...")
    await redis_cache.close()


app = FastAPI(
    title="Pafare API",
    description="Hotel room booking.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS if hasattr(settings, 'BACKEND_CORS_ORIGINS') else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if hasattr(settings, 'ALLOWED_HOSTS'):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

v1_router = APIRouter(prefix="/v1", redirect_slashes=False)
v1_router.include_router(auth_router)
v1_router.include_router(user_router)
v1_router.include_router(role_router)
v1_router.include_router(permission_router)
v1_router.include_router(hotel_router)
v1_router.include_router(room_type_router)
v1_router.include_router(room_router)
v1_router.include_router(booking_router)
v1_router.include_router(review_router)
v1_router.include_router(payment_router)
v1_router.include_router(health_router)

app.include_router(v1_router)


@app.get("/")
async def read_root():
    return {
        "service": "Pafare API",
        "status": "Running",
        "version": "1.0.0",
        "description": "Hotel room booking.",
    }


app.add_exception_handler(APIException, api_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(ValidationError, pydantic_validation_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=True
    )
