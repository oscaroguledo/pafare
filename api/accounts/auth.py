from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.dependencies import get_current_user
from core.exceptions import APIException
from core.utils.response import Response
from models.accounts.user import User, AccountStatus
from schemas.accounts.auth import Register, Refresh, ForgotPassword, ResetPassword
from schemas.accounts.user import UserCreate, UserGet, UserLogin, UserResponse
from services.accounts.user import UserService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register/", status_code=status.HTTP_201_CREATED)
async def register(
    payload: Register,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Public sign-up. The account is a customer and stays inactive until the emailed link is verified."""
    user = await UserService(db).create(
        UserCreate(**payload.model_dump(), role_name="customer", account_status=AccountStatus.INACTIVE),
        background_tasks,
    )
    return Response.success(
        data=UserResponse.model_validate(user),
        message="Registered. Check your email to verify your account.",
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/login/")
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    """Exchange an email and password for an access token and a refresh token."""
    tokens = await UserService(db).login(payload)
    return Response.success(data=tokens, message="Login successful")


@router.post("/refresh/")
async def refresh(payload: Refresh, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access/refresh pair."""
    tokens = await UserService(db).refresh_token(payload.refresh_token)
    return Response.success(data=tokens, message="Token refreshed")


@router.post("/logout/")
async def logout(current_user: User = Depends(get_current_user)):
    """Tokens are stateless and not stored, so logging out means the client discards them."""
    return Response.success(message="Logged out. Discard your access and refresh tokens.")


@router.get("/me/")
async def me(current_user: User = Depends(get_current_user)):
    """The signed-in user."""
    return Response.success(data=UserResponse.model_validate(current_user))


@router.get("/verify-email/")
async def verify_email(
    background_tasks: BackgroundTasks,
    token: str = Query(..., description="Token from the verification link"),
    db: AsyncSession = Depends(get_db),
):
    """Activate the account from the emailed link. An expired link triggers a fresh one."""
    await UserService(db).verify(token, background_tasks)
    return Response.success(message="Email verified. You can now log in.")


@router.post("/forgot-password/")
async def forgot_password(payload: ForgotPassword, db: AsyncSession = Depends(get_db)):
    """Start a password reset. The answer is the same whether or not the email is registered."""
    service = UserService(db)
    try:
        user = await service.get(UserGet(email=payload.email))
    except APIException as e:
        if e.status_code != 404:
            raise
    else:
        await service.reset_password(user.id)
    return Response.success(message="If an account exists with this email, a reset link has been sent.")


@router.post("/reset-password/")
async def reset_password(payload: ResetPassword, db: AsyncSession = Depends(get_db)):
    """Set a new password using the token from the reset link."""
    await UserService(db).set_password(payload.token, payload.new_password)
    return Response.success(message="Password updated. You can now log in.")
