"""Create (or reset) the first administrator, who can then manage everyone else through the API.

Run from the project root:
    python -m scripts.create_superadmin you@example.com 'A-strong-Password1' Firstname Lastname

Needs the migrations applied (they seed the 'superadmin' role and its permissions).
Safe to run again: an existing user with that email gets the superadmin role, an active account and the new password.
"""
import asyncio
import sys

from sqlalchemy import select

import models  # noqa: F401 (registers every model on Base)
from core.config import settings
from core.db import DatabaseManager
from core.utils.encryption import PasswordManager
from core.utils.uuid_utils import uuid7
from models.accounts.role import Role
from models.accounts.user import User, AccountStatus
from schemas.accounts.user import strong_password


async def main(email: str, password: str, firstname: str, lastname: str) -> None:
    strong_password(password)
    manager = DatabaseManager()
    manager.initialize(settings.SQLALCHEMY_DATABASE_URI, env_is_local=False)
    async with manager.session_factory() as db:
        role = (await db.execute(select(Role).where(Role.name == "superadmin"))).scalar_one_or_none()
        if role is None:
            raise SystemExit("No 'superadmin' role found. Run the migrations first.")

        email = email.lower()
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(id=uuid7(), email=email, firstname=firstname.lower(), lastname=lastname.lower(),
                        middlename="", passwordhash="", role_id=role.id, language="en")
            db.add(user)
        user.passwordhash = PasswordManager.hash_password(password)
        user.role_id = role.id
        user.account_status = AccountStatus.ACTIVE
        await db.commit()
        print(f"Superadmin ready: {email}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    asyncio.run(main(*sys.argv[1:]))
