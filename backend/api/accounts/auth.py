from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import get_current_user
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.response import Response
from models.accounts.user import User, AccountStatus
from schemas.accounts.auth import Register, Refresh, ForgotPassword, ResetPassword
from schemas.accounts.user import UserCreate, UserGet, UserLogin, UserResponse
from services.accounts.user import UserService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register/", status_code=status.HTTP_201_CREATED, summary="Sign up",
    responses=errors(409, 422, 500),
)
async def register(payload: Register, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Public sign-up. The account is a customer and stays inactive until the emailed link is verified."""
    try:
        user = await UserService(db).create(
            UserCreate(**payload.model_dump(), role_name="customer", account_status=AccountStatus.INACTIVE),
            background_tasks,
        )
        return Response.success(
            data=UserResponse.model_validate(user),
            message="Registered. Check your email to verify your account.",
            status_code=status.HTTP_201_CREATED,
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Registration failed")
        raise APIException(status_code=500, message="Could not create the account")


@router.post("/login/", summary="Log in", responses=errors(401, 403, 422, 500))
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    """Exchange an email and password for an access token and a refresh token. A wrong email or password gives the
    same 401. A suspended or not-yet-verified account gets a 403 that says why."""
    try:
        tokens = await UserService(db).login(payload)
        return Response.success(data=tokens, message="Login successful")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Login failed unexpectedly")
        raise APIException(status_code=500, message="Could not log in")


@router.post("/refresh/", summary="Get new tokens", responses=errors(401, 403, 422, 500))
async def refresh(payload: Refresh, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access/refresh pair. Role changes and suspensions take effect here."""
    try:
        tokens = await UserService(db).refresh_token(payload.refresh_token)
        return Response.success(data=tokens, message="Token refreshed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Token refresh failed unexpectedly")
        raise APIException(status_code=500, message="Could not refresh the token")


@router.post("/logout/", summary="Log out", responses=errors(401, 403))
async def logout(current_user: User = Depends(get_current_user)):
    """Tokens are stateless and not stored, so logging out means the client discards them."""
    return Response.success(message="Logged out. Discard your access and refresh tokens.")


@router.get("/me/", summary="The signed-in user", responses=errors(401, 403, 500))
async def me(current_user: User = Depends(get_current_user)):
    try:
        return Response.success(data=UserResponse.model_validate(current_user))
    except Exception:
        logger.exception("Could not build the current user response")
        raise APIException(status_code=500, message="Could not load your profile")


@router.get("/verify-email/", summary="Verify an email address", responses=errors(400, 403, 500))
async def verify_email(
    background_tasks: BackgroundTasks,
    token: str = Query(..., min_length=10, description="Token from the verification link"),
    db: AsyncSession = Depends(get_db),
):
    """Activate the account from the emailed link. An expired link triggers a fresh one (answered with a 400)."""
    try:
        await UserService(db).verify(token, background_tasks)
        return Response.success(message="Email verified. You can now log in.")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Email verification failed unexpectedly")
        raise APIException(status_code=500, message="Could not verify the email")


@router.post("/forgot-password/", summary="Start a password reset", responses=errors(422, 500))
async def forgot_password(payload: ForgotPassword, db: AsyncSession = Depends(get_db)):
    """Always answers the same, whether or not the email is registered, so it can't be used to find accounts."""
    message = "If an account exists with this email, a reset link has been sent."
    try:
        service = UserService(db)
        try:
            user = await service.get(UserGet(email=payload.email))
        except APIException as e:
            if e.status_code != 404:
                raise
            return Response.success(message=message)
        await service.reset_password(user.id)
        return Response.success(message=message)
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Forgot-password failed unexpectedly")
        raise APIException(status_code=500, message="Could not start the password reset")


@router.post("/reset-password/", summary="Set a new password", responses=errors(400, 422, 500))
async def reset_password(payload: ResetPassword, db: AsyncSession = Depends(get_db)):
    """Set a new password using the token from the reset link."""
    try:
        await UserService(db).set_password(payload.token, payload.new_password)
        return Response.success(message="Password updated. You can now log in.")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Password reset failed unexpectedly")
        raise APIException(status_code=500, message="Could not reset the password")
