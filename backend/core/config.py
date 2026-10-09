"""Configuration - PostgreSQL only."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment-specific .env file
env = os.getenv("ENVIRONMENT", "dev").lower()
base_dir = Path(__file__).resolve().parents[1]  # backend directory

if env in ("production", "prod"):
    env_file = base_dir / ".env.prod"
else:
    env_file = base_dir / ".env.dev"

if env_file.exists():
    load_dotenv(env_file)
elif (base_dir / ".env").exists():
    load_dotenv(base_dir / ".env")


class Settings:
    """Minimal settings with SQLite/PostgreSQL support."""

    # Core
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "dev")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

    # Frontend
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    # Where this API is reachable from outside; the verification link in emails points here
    API_PUBLIC_URL: str = os.getenv("API_PUBLIC_URL", "http://localhost:8000")
    BACKEND_CORS_ORIGINS: list = ["*"] if os.getenv("ENVIRONMENT", "dev") == "dev" else \
        [os.getenv("FRONTEND_URL", "http://localhost:5173")]

    # Database - PostgreSQL only
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/pafare")
    # Connection pooling
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))

    SEARCH_PATH = "accounts,catalog,commerce,admin,system,public"

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """Async PostgreSQL URI."""
        url = self.DATABASE_URL
        if url.startswith("postgresql"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            url = url.replace("postgresql+asyncpg+asyncpg://", "postgresql+asyncpg://", 1)
        return url

    @property
    def SQLALCHEMY_DATABASE_URI_SYNC(self) -> str:
        """Sync PostgreSQL URI (for migrations)."""
        url = self.DATABASE_URL
        if url.startswith("postgresql"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            url = url.replace("postgresql+psycopg2+psycopg2://", "postgresql+psycopg2://", 1)
        return url

    # Auth
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    # Sessions end after this many days without use: each refresh issues a new refresh token.
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
    ALGORITHM: str = "HS256"

    # Payments (Stripe)
    # The one currency prices are set, shown, recorded and charged in (ISO 4217).
    STORE_CURRENCY: str = os.getenv("STORE_CURRENCY", "CAD").upper()
    # Seller details printed on invoices; lines separated by "|".
    STORE_NAME: str = os.getenv("STORE_NAME", "Pafare")
    # Passkeys: the site's domain (no scheme or port) and the web origins allowed to use them.
    WEBAUTHN_RP_ID: str = os.getenv("WEBAUTHN_RP_ID", "localhost")
    WEBAUTHN_ORIGINS: list = [o.strip() for o in os.getenv("WEBAUTHN_ORIGINS", os.getenv("FRONTEND_URL", "http://localhost:5173")).split(",") if o.strip()]
    # The shop's clock for scheduled jobs (e.g. renewals at 2:00, 8:00, 14:00, 20:00).
    STORE_TIMEZONE: str = os.getenv("STORE_TIMEZONE", "America/Toronto")
    STORE_ADDRESS: str = os.getenv("STORE_ADDRESS", "")
    # Shown in emails as the address customers write to.
    SUPPORT_EMAIL: str = os.getenv("SUPPORT_EMAIL", "support@pafare.com")
    # Supported account languages: Canadian official + home country languages
    SUPPORTED_LANGUAGES: tuple = (
        "en", "fr", "es", "zh", "zh-Hant", "pa", "ar", "tl", "pt", "hi", "ur", "ru", "uk",
        "de", "tr", "vi", "ja", "ko", "he", "fa", "ps", "ckb", "bn", "ne",
    )


    # Cloudflare R2 (optional) for product images; empty disables direct uploads.
    R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "pafare-files")
    R2_CUSTOM_DOMAIN: str = os.getenv("R2_CUSTOM_DOMAIN", "")  # e.g., images.pafare.com; empty uses R2 default

    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Email (optional)
    BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
    BREVO_FROM_EMAIL: str = os.getenv("BREVO_FROM_EMAIL", "Pafare <noreply@pafare.com>")

    # The store owner's account, created or refreshed on every start (services/accounts/superadmin.py)
    SUPERADMIN_EMAIL: str = os.getenv("SUPERADMIN_EMAIL", "")
    SUPERADMIN_PASSWORD: str = os.getenv("SUPERADMIN_PASSWORD", "")

    # OAuth - Google
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # OAuth - Facebook
    FACEBOOK_APP_ID: str = os.getenv("FACEBOOK_APP_ID", "")
    FACEBOOK_APP_SECRET: str = os.getenv("FACEBOOK_APP_SECRET", "")

    def validate(self) -> dict:
        """Check required settings."""
        missing = []
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY == "dev-secret-key-change-in-production":
                missing.append("SECRET_KEY")
        return {"is_valid": len(missing) == 0, "missing": missing}


settings = Settings()
