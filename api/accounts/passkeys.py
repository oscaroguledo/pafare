from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.dependencies import require_auth
from core.utils.response import Response
from models.accounts.user import User
from schemas.accounts.auth import PasskeyLogin, PasskeyLoginOptions, PasskeyRegister
from services.accounts.passkeys import PasskeyService

router = APIRouter(prefix="/auth/passkeys", tags=["Passkeys"])


@router.get("/")
async def list_passkeys(current_user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Your saved passkeys."""
    return Response.success(data=[p.to_dict() for p in await PasskeyService(db).list(current_user.id)])


@router.post("/register/options/")
async def registration_options(current_user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Start adding a passkey: options for the browser and a challenge token to send back."""
    options, challenge_token = await PasskeyService(db).registration_options(current_user)
    return Response.success(data={"options": options, "challenge_token": challenge_token})


@router.post("/register/")
async def register(body: PasskeyRegister, current_user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Finish adding a passkey."""
    passkey = await PasskeyService(db).register(current_user, body.credential, body.challenge_token, body.name)
    return Response.success(data=passkey.to_dict(), message="Passkey saved")


@router.delete("/{passkey_id}/")
async def delete_passkey(passkey_id: UUID, current_user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Remove one of your passkeys."""
    await PasskeyService(db).delete(current_user.id, passkey_id)
    return Response.success(message="Passkey removed")


@router.post("/login/options/")
async def login_options(body: PasskeyLoginOptions, db: AsyncSession = Depends(get_db)):
    """Start signing in with a passkey; the email is optional."""
    options, challenge_token = await PasskeyService(db).login_options(body.email)
    return Response.success(data={"options": options, "challenge_token": challenge_token})


@router.post("/login/")
async def login(body: PasskeyLogin, db: AsyncSession = Depends(get_db)):
    """Finish signing in with a passkey: returns the same tokens as a password sign-in."""
    return Response.success(data=await PasskeyService(db).login(body.credential, body.challenge_token), message="Login successful")
