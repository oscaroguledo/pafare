"""Encryption and password utilities."""
from passlib.context import CryptContext

import secrets
import string

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


def hash_password(password: str) -> str:
    """Convenience function to hash a password."""
    return PasswordManager.hash_password(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Convenience function to verify a password."""
    return PasswordManager.verify_password(plain_password, hashed_password)
