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

from core.db import db_manager, initialize_db
from services.accounts.superadmin import ensure_superadmin
from core.config import settings
from core.exceptions import (
    APIException,
    api_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    sqlalchemy_exception_handler,
    general_exception_handler
)

from api import (
    auth_router, oauth_router, passkeys_router, user_router, addresses_router,
    products_router, review_router, inventory_router, category_router,
    cart_router, orders_router, payments_router, refunds_router, shipping_router,
    shipping_tracking_router, tax_router, promocodes_router, subscriptions_router, webhooks_router,
    analytics_router,
    health_router,
)
import models
from core.worker import start_scheduler
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

    # The owner's superadmin account; a weak password stops production from starting
    try:
        async with db_manager.get_session_with_retry() as db:
            await ensure_superadmin(db)
    except ValueError as e:
        if settings.ENVIRONMENT.lower() == "production":
            raise RuntimeError(str(e))
        logger.warning(f"Superadmin not set up: {e}")

    # Start background scheduler (subscriptions, promocodes)
    start_scheduler()

    yield

    # --- Shutdown ---
    logger.info("Application shutting down...")
    await redis_cache.close()


app = FastAPI(
    title="Banwee API",
    description="Groceries and essentials from home for newcomers to Canada, delivered to your door.",
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
v1_router.include_router(oauth_router)
v1_router.include_router(passkeys_router)
v1_router.include_router(user_router)
v1_router.include_router(addresses_router)
v1_router.include_router(products_router)
v1_router.include_router(review_router)
v1_router.include_router(inventory_router)
v1_router.include_router(category_router)
v1_router.include_router(cart_router)
v1_router.include_router(orders_router)
v1_router.include_router(payments_router)
v1_router.include_router(refunds_router)
v1_router.include_router(shipping_router)
v1_router.include_router(shipping_tracking_router)
v1_router.include_router(tax_router)
v1_router.include_router(promocodes_router)
v1_router.include_router(subscriptions_router)
v1_router.include_router(webhooks_router)
# Admin routes are distributed to their domain-specific modules
# (e.g. accounts/user.py, catalog/products.py, commerce/orders.py) rather than grouped here.
v1_router.include_router(analytics_router)
v1_router.include_router(health_router)

app.include_router(v1_router)


@app.get("/")
async def read_root():
    return {
        "service": "Banwee API",
        "status": "Running",
        "version": "1.0.0",
        "description": "Groceries and essentials from home for newcomers to Canada, delivered to your door.",
    }


app.add_exception_handler(APIException, api_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
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
