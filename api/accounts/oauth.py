"""Complete OAuth implementation (what's missing to make OAuth fully functional)."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
import google.auth.transport.requests
import google.oauth2.id_token
from core.db import get_db
from core.config import settings
from services.accounts.auth import AuthService
from models.accounts.user import User
from schemas.accounts.user import Create as UserCreate
from datetime import datetime, timezone
from core.utils.response import Response

# Social login: exchanges a Google/Facebook access token for our own JWTs.
router = APIRouter(prefix="/auth/social", tags=["OAuth"])

# OAuth provider configurations
OAUTH_PROVIDERS = {
    "google": {
        "user_info_url": "https://www.googleapis.com/oauth2/v2/userinfo",
    },
    "facebook": {
        "user_info_url": "https://graph.facebook.com/me",
    }
}

@router.post("/google")
async def google_oauth_credential(
    background_tasks: BackgroundTasks,
    credential: str = None,
    mode: str = "login",
    db: AsyncSession = Depends(get_db)
):
    """Handle Google OAuth credential from client-side (Google Identity Services)"""
    if not credential:
        raise HTTPException(status_code=400, detail="Google credential is required")

    try:
        # Verify Google credential and get user info
        user_info = await verify_google_credential(credential)

        # Find or create user
        user = await find_or_create_user(db, "google", user_info, background_tasks)

        return Response.success(data=await AuthService(db).issue_tokens(user))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Google authentication failed: {str(e)}"
        )

@router.post("/facebook")
async def facebook_oauth_credential(
    background_tasks: BackgroundTasks,
    access_token: str = None,
    user_id: str = None,
    mode: str = "login",
    db: AsyncSession = Depends(get_db)
):
    """Handle Facebook OAuth credential from client-side (Facebook Login SDK)"""
    if not access_token:
        raise HTTPException(status_code=400, detail="Facebook access token is required")

    try:
        # Get user info from Facebook using access token
        user_info = await get_user_info("facebook", access_token)

        # Find or create user
        user = await find_or_create_user(db, "facebook", user_info, background_tasks)

        return Response.success(data=await AuthService(db).issue_tokens(user))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Facebook authentication failed: {str(e)}"
        )

async def verify_google_credential(credential: str) -> dict:
    """Verify Google ID token and get user info"""
    try:
        # Verify the ID token
        id_info = google.oauth2.id_token.verify_oauth2_token(
            credential,
            google.auth.transport.requests.Request(),
            settings.GOOGLE_CLIENT_ID
        )

        # Extract user info
        return {
            "email": id_info.get("email"),
            "name": id_info.get("name"),
            "picture": id_info.get("picture"),
            "given_name": id_info.get("given_name"),
            "family_name": id_info.get("family_name")
        }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to verify Google credential: {str(e)}"
        )

async def get_user_info(provider: str, access_token: str) -> dict:
    """Get user information from OAuth provider"""
    provider_config = OAUTH_PROVIDERS[provider]

    if provider == "google":
        params = {"access_token": access_token}

    elif provider == "facebook":
        params = {
            "fields": "id,name,email,picture",
            "access_token": access_token
        }

    async with httpx.AsyncClient() as client:
        response = await client.get(provider_config["user_info_url"], params=params)
        response.raise_for_status()
        return response.json()

async def find_or_create_user(db: AsyncSession, provider: str, user_info: dict, background_tasks: BackgroundTasks) -> User:
    """Find existing user or create new one from OAuth data"""
    # Extract email from provider-specific response
    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by OAuth provider")
    
    # Check if user exists
    result = await db.execute(select(User).where(User.email == email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        # Update OAuth info if user exists
        if provider == "google" and user_info.get("picture"):
            existing_user.avatar_url = user_info["picture"]
        elif provider == "facebook":
            # Facebook profile picture URL format
            existing_user.avatar_url = f"https://graph.facebook.com/{user_info['id']}/picture?type=large"
        
        existing_user.last_login = datetime.now(timezone.utc)
        await db.commit()
        return existing_user
    
    # Create new user from OAuth data
    name_parts = user_info.get("name", "").split()
    firstname = name_parts[0] if name_parts else ""
    lastname = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
    
    # Set avatar URL
    avatar_url = None
    if provider == "google" and user_info.get("picture"):
        avatar_url = user_info["picture"]
    elif provider == "facebook":
        avatar_url = f"https://graph.facebook.com/{user_info['id']}/picture?type=large"
    
    user_data = UserCreate(
        email=email,
        firstname=firstname,
        lastname=lastname,
        password="",  # No password for OAuth users
    )

    auth_service = AuthService(db)
    # auth_service.create() returns a UserResponse, not the ORM row - re-fetch the real User to set fields UserCreate has no place for (verification_status, avatar_url), since the provider already verified this email.
    await auth_service.create(user_data, background_tasks=background_tasks)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()

    user.verified = True
    if avatar_url:
        user.avatar_url = avatar_url
    await db.commit()

    return user
