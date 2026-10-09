"""Encryption and password utilities."""
from passlib.context import CryptContext

import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import jwt, JWTError, ExpiredSignatureError

from core.config import settings

# Password hashing context using Argon2 (more modern and secure)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class PasswordManager:
    """Password management utility for hashing and verification."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using Argon2."""
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its Argon2 hash."""
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def generate_random_password(length: int = 12) -> str:
        """Generate a random password."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        return password

    @staticmethod
    def generate_token(length: int = 32) -> str:
        """Generate a random token for verification, reset, etc."""
        return secrets.token_urlsafe(length)


class TokenError(Exception):
    """A token is malformed, badly signed, expired, or of the wrong type."""

    def __init__(self, message: str, expired: bool = False):
        super().__init__(message)
        self.expired = expired


class JWTManager:
    """Stateless JWT creation and verification: nothing is stored server-side.

    Tokens carry `sub`, `type` ('access' or 'refresh'), `iat`, `exp` and a random `jti`.
    Callers map TokenError to an HTTP 401.
    """
    ACCESS = "access"
    REFRESH = "refresh"
    VERIFICATION = "verification"
    PASSWORD_RESET = "password_reset"

    def __init__(self, secret: Optional[str] = None, algorithm: Optional[str] = None,
                 access_ttl: Optional[timedelta] = None, refresh_ttl: Optional[timedelta] = None,
                 verification_ttl: Optional[timedelta] = None, reset_ttl: Optional[timedelta] = None):
        self.secret = secret or settings.SECRET_KEY
        self.algorithm = algorithm or settings.ALGORITHM
        self.access_ttl = access_ttl or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        self.refresh_ttl = refresh_ttl or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        self.verification_ttl = verification_ttl or timedelta(minutes=15)
        self.reset_ttl = reset_ttl or timedelta(minutes=15)

    def _create(self, data: Dict[str, Any], token_type: str, ttl: timedelta) -> str:
        now = datetime.now(timezone.utc)
        claims = {**data, "type": token_type, "iat": now, "exp": now + ttl, "jti": str(uuid.uuid4())}
        return jwt.encode(claims, self.secret, algorithm=self.algorithm)

    def create_access_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        return self._create(data, self.ACCESS, expires_delta or self.access_ttl)

    def create_refresh_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        return self._create(data, self.REFRESH, expires_delta or self.refresh_ttl)

    def create_verification_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Signed, expiring token for an email-verification link; nothing is stored server-side."""
        return self._create(data, self.VERIFICATION, expires_delta or self.verification_ttl)

    def create_password_reset_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Signed, expiring token for a password-reset link; nothing is stored server-side."""
        return self._create(data, self.PASSWORD_RESET, expires_delta or self.reset_ttl)

    def create_token_pair(self, data: Dict[str, Any]) -> Dict[str, str]:
        return {
            "access_token": self.create_access_token(data),
            "refresh_token": self.create_refresh_token(data),
            "token_type": "bearer",
        }

    def decode(self, token: str, expected_type: str = ACCESS, verify_exp: bool = True) -> Dict[str, Any]:
        """Verify signature, expiry and type; return the claims or raise TokenError.
        verify_exp=False still checks the signature and type but accepts an expired token (to read its claims)."""
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm], options={"verify_exp": verify_exp})
        except ExpiredSignatureError:
            raise TokenError("Token has expired", expired=True)
        except JWTError:
            raise TokenError("Could not validate credentials")
        if payload.get("type") != expected_type:
            raise TokenError(f"Invalid token type. Expected {expected_type}")
        return payload

    def refresh(self, refresh_token: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Exchange a valid refresh token for a new pair. Pass fresh `data` (e.g. current role) to
        rebuild the claims; otherwise the old token's claims are reused."""
        payload = self.decode(refresh_token, self.REFRESH)
        if data is None:
            data = {k: v for k, v in payload.items() if k not in {"type", "iat", "exp", "jti"}}
        return self.create_token_pair(data)


