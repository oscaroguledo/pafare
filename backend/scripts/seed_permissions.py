"""Create any missing permission from core/scopes.py and give the superadmin all of them.

Run from the project root:  python -m scripts.seed_permissions
Safe to run repeatedly. For versioned, reproducible changes (production) add a migration that inserts the new
scopes; use this script in development, or to catch a database up after adding a scope.
"""
import asyncio

from sqlalchemy import select

import models  # noqa: F401 (registers every model on Base)
from core.config import settings
from core.db import DatabaseManager
from core.scopes import Scope, description
from core.utils.uuid_utils import uuid7
from models.accounts.role import Role, Permission, Role_Permission


async def seed(db) -> int:
    existing = {p.name: p for p in (await db.execute(select(Permission))).scalars()}
    added = 0
    for scope in Scope:
        if scope.value not in existing:
            db.add(Permission(id=uuid7(), name=scope.value, description=description(scope)))
            added += 1
        elif not existing[scope.value].description:
            existing[scope.value].description = description(scope)
    await db.flush()

    superadmin = (await db.execute(select(Role).where(Role.name == "superadmin"))).scalar_one_or_none()
    if superadmin:
        granted = set((await db.execute(
            select(Role_Permission.permission_id).where(Role_Permission.role_id == superadmin.id))).scalars())
        for p in (await db.execute(select(Permission))).scalars():
            if p.id not in granted:
                db.add(Role_Permission(id=uuid7(), role_id=superadmin.id, permission_id=p.id))
    await db.commit()
    return added


async def main():
    manager = DatabaseManager()
    manager.initialize(settings.SQLALCHEMY_DATABASE_URI, env_is_local=False)
    async with manager.session_factory() as db:
        print(f"Added {await seed(db)} permissions")


if __name__ == "__main__":
    asyncio.run(main())
