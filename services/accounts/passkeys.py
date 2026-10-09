"""Passkeys (WebAuthn): register a device's Face ID / fingerprint / PIN and sign in with it instead of a password."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from core.config import settings
from core.logging import get_structured_logger
from models.accounts.passkeys import Passkey
from models.accounts.tokens import RevokedToken
from models.accounts.user import User
from schemas.accounts.auth import Auth as AuthResponse
from services.accounts.auth import AuthService

logger = get_structured_logger(__name__)

# A ceremony (the browser's Face ID / fingerprint prompt) must finish within this time.
CHALLENGE_TTL = timedelta(minutes=5)
MAX_PASSKEYS_PER_USER = 10


def _challenge_token(purpose: str, challenge: bytes, user_id: Optional[UUID] = None) -> str:
    """Sign the challenge so the server needn't store it; the client sends it back with the response."""
    claims = {
        "purpose": purpose,
        "challenge": bytes_to_base64url(challenge),
        "jti": uuid.uuid4().hex,
        "exp": datetime.now(timezone.utc) + CHALLENGE_TTL,
    }
    if user_id:
        claims["sub"] = str(user_id)
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _descriptor(passkey: Passkey) -> PublicKeyCredentialDescriptor:
    transports = [AuthenticatorTransport(t) for t in passkey.transports or [] if t in AuthenticatorTransport._value2member_map_]
    return PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id), transports=transports or None)


class PasskeyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _use_challenge(self, token: str, purpose: str, user_id: Optional[UUID] = None) -> bytes:
        """Check a challenge token and mark it used, so a captured response can't be replayed
        (many passkeys, e.g. Apple's, always report a sign count of 0)."""
        invalid = HTTPException(status_code=400, detail="This passkey request expired. Please try again.")
        try:
            claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except JWTError:
            raise invalid
        if claims.get("purpose") != purpose or (user_id and claims.get("sub") != str(user_id)) or not claims.get("jti"):
            raise invalid
        if await self.db.get(RevokedToken, claims["jti"]):
            raise invalid
        self.db.add(RevokedToken(jti=claims["jti"], expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc)))
        await self.db.flush()
        return base64url_to_bytes(claims["challenge"])

    async def list(self, user_id: UUID) -> List[Passkey]:
        return (await self.db.execute(
            select(Passkey).where(Passkey.user_id == user_id).order_by(Passkey.created_at)
        )).scalars().all()

    async def registration_options(self, user: User) -> Tuple[Dict[str, Any], str]:
        """Options for the browser to create a passkey, and the signed challenge to send back."""
        existing = await self.list(user.id)
        if len(existing) >= MAX_PASSKEYS_PER_USER:
            raise HTTPException(status_code=400, detail=f"You can save up to {MAX_PASSKEYS_PER_USER} passkeys. Remove one first.")
        options = generate_registration_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            rp_name=settings.STORE_NAME,
            user_id=user.id.bytes,
            user_name=user.email,
            user_display_name=f"{user.firstname} {user.lastname}".strip() or user.email,
            # Discoverable, so sign-in needs no email; verified, so it always asks for Face ID / fingerprint / PIN.
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[_descriptor(p) for p in existing],
        )
        return json.loads(options_to_json(options)), _challenge_token("register", options.challenge, user.id)

    async def register(self, user: User, credential: Dict[str, Any], challenge_token: str, name: Optional[str]) -> Passkey:
        """Check the new passkey against the challenge and save its public key."""
        challenge = await self._use_challenge(challenge_token, "register", user.id)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                expected_origin=settings.WEBAUTHN_ORIGINS,
                require_user_verification=True,
            )
        except InvalidRegistrationResponse as e:
            await self.db.commit()
            logger.warning(f"Passkey registration rejected for {user.email}: {e}")
            raise HTTPException(status_code=400, detail="We couldn't verify this passkey. Please try again.")

        credential_id = bytes_to_base64url(verified.credential_id)
        if await self.db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
            raise HTTPException(status_code=400, detail="This passkey is already saved.")
        passkey = Passkey(
            user_id=user.id,
            credential_id=credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=(credential.get("response") or {}).get("transports"),
            name=(name or "").strip()[:100] or "Passkey",
        )
        self.db.add(passkey)
        await self.db.commit()
        await self.db.refresh(passkey)
        return passkey

    async def delete(self, user_id: UUID, passkey_id: UUID) -> None:
        passkey = await self.db.scalar(select(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user_id))
        if not passkey:
            raise HTTPException(status_code=404, detail="Passkey not found")
        await self.db.delete(passkey)
        await self.db.commit()

    async def login_options(self, email: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
        """Options for the browser to sign in. With an email, only that account's passkeys are offered."""
        allow: List[PublicKeyCredentialDescriptor] = []
        if email:
            passkeys = (await self.db.execute(
                select(Passkey).join(User, User.id == Passkey.user_id).where(User.email == email.strip().lower())
            )).scalars().all()
            allow = [_descriptor(p) for p in passkeys]
        options = generate_authentication_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            allow_credentials=allow or None,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return json.loads(options_to_json(options)), _challenge_token("login", options.challenge)

    async def login(self, credential: Dict[str, Any], challenge_token: str) -> AuthResponse:
        """Verify the signed challenge with the saved public key and sign the user in."""
        challenge = await self._use_challenge(challenge_token, "login")
        denied = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="We couldn't sign you in with this passkey.")
        passkey = await self.db.scalar(select(Passkey).where(Passkey.credential_id == credential.get("id")))
        if not passkey:
            await self.db.commit()
            raise denied
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                expected_origin=settings.WEBAUTHN_ORIGINS,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as e:
            await self.db.commit()
            logger.warning(f"Passkey sign-in rejected for credential {passkey.credential_id[:12]}: {e}")
            raise denied

        user = await self.db.get(User, passkey.user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is deactivated")
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = datetime.now(timezone.utc)
        user.last_login = datetime.now(timezone.utc)
        await self.db.commit()
        return await AuthService(self.db).issue_tokens(user)
