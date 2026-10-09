"""Create any missing <model>:<action> permissions and give the superadmin all of them.

Run from the project root:  python -m scripts.seed_permissions
Safe to run repeatedly. For versioned, reproducible changes prefer a migration; use this for dev or to
catch up a database after adding models.
"""
import asyncio

from sqlalchemy import select

import models  # noqa: F401 (registers every model on Base)
from core.config import settings
from core.db import Base, DatabaseManager
from models.accounts.roles import Role, Permission, Role_Permission

ACTIONS = ("view", "create", "edit", "delete")
SKIP = {"role_permissions"}  # join tables / internals


def all_scopes() -> list[str]:
    tables = {m.local_table.name for m in Base.registry.mappers} - SKIP
    return sorted(f"{t}:{a}" for t in tables for a in ACTIONS)


async def seed(db) -> int:
    existing = set((await db.execute(select(Permission.name))).scalars())
    added = [Permission(name=n) for n in all_scopes() if n not in existing]
    db.add_all(added)
    await db.flush()

    superadmin = (await db.execute(select(Role).where(Role.name == "superadmin"))).scalar_one_or_none()
    if superadmin:
        granted = set((await db.execute(
            select(Role_Permission.permission_id).where(Role_Permission.role_id == superadmin.id))).scalars())
        for p in (await db.execute(select(Permission))).scalars():
            if p.id not in granted:
                db.add(Role_Permission(role_id=superadmin.id, permission_id=p.id))
    await db.commit()
    return len(added)


async def main():
    manager = DatabaseManager()
    manager.initialize(settings.SQLALCHEMY_DATABASE_URI, env_is_local=False)
    async with manager.session_factory() as db:
        print(f"Added {await seed(db)} permissions")


if __name__ == "__main__":
    asyncio.run(main())
