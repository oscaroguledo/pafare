"""Cloudflare R2 storage for product images. Optional: disabled if R2_ACCOUNT_ID is empty."""

from datetime import timedelta
from typing import Optional
import boto3
from botocore.config import Config
from core.config import settings
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class R2Storage:
    """Cloudflare R2 object storage client for product images."""

    def __init__(
        self,
        account_id: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        bucket_name: str = "banwee-products",
        custom_domain: str = "",
    ):
        """Initialize R2 storage client.

        Args:
            account_id: Cloudflare R2 Account ID
            access_key_id: R2 API access key
            secret_access_key: R2 API secret key
            bucket_name: S3 bucket name
            custom_domain: Optional custom domain for public URLs (e.g., images.example.com)
        """
        self.account_id = account_id or settings.R2_ACCOUNT_ID
        self.access_key_id = access_key_id or settings.R2_ACCESS_KEY_ID
        self.secret_access_key = secret_access_key or settings.R2_SECRET_ACCESS_KEY
        self.bucket_name = bucket_name or settings.R2_BUCKET_NAME
        self.custom_domain = custom_domain or settings.R2_CUSTOM_DOMAIN
        self._client: Optional[object] = None

    def is_configured(self) -> bool:
        """Check if R2 is configured (account_id is set)."""
        return bool(self.account_id)

    def _get_client(self) -> Optional[object]:
        """Create or reuse S3 client for R2 (R2 is S3-compatible)."""
        if not self.is_configured():
            return None
        if self._client is not None:
            return self._client

        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )
            return self._client
        except Exception as e:
            logger.error(f"Failed to create R2 client: {e}")
            return None

    def get_signed_upload_url(self, key: str, expires_in_hours: int = 1) -> Optional[str]:
        """Generate a signed URL for uploading a file to R2.

        Args:
            key: S3 key/path (e.g., "products/variant-123/image.jpg")
            expires_in_hours: how long the URL is valid (default 1 hour)

        Returns:
            Signed PUT URL, or None if R2 is not configured or error occurs.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            url = client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=int(timedelta(hours=expires_in_hours).total_seconds()),
            )
            return url
        except Exception as e:
            logger.error(f"Error generating R2 upload URL for key '{key}': {e}")
            return None

    def get_public_url(self, key: str) -> str:
        """Get the public (non-signed) URL for a file in R2.

        Args:
            key: S3 key/path (e.g., "products/variant-123/image.jpg")

        Returns:
            Public URL via custom domain if set, otherwise R2 default.
        """
        if self.custom_domain:
            return f"https://{self.custom_domain}/{key}"
        if not self.account_id:
            return ""
        return f"https://{self.bucket_name}.{self.account_id}.r2.cloudflarestorage.com/{key}"


# Module-level instance for backward compatibility
_instance = R2Storage()


def get_signed_upload_url(key: str, expires_in_hours: int = 1) -> Optional[str]:
    """Module-level function for backward compatibility."""
    return _instance.get_signed_upload_url(key, expires_in_hours)


def get_public_url(key: str) -> str:
    """Module-level function for backward compatibility."""
    return _instance.get_public_url(key)
