"""Test setup. The suite runs against an in-memory SQLite database by default, so it needs nothing installed.
Set TEST_DATABASE_URL to a PostgreSQL URL (postgresql://user:pass@host:5432/dbname) to run the same tests on
PostgreSQL, including the ones marked `postgres` that need real row locking. That database is emptied for every test,
so point it at a throwaway one.
"""
import itertools
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models  # noqa: E402,F401  (registers every model)
import main  # noqa: E402
from core.db import Base, get_db  # noqa: E402
from core.scopes import Scope  # noqa: E402
from core.utils.encryption import PasswordManager  # noqa: E402
from core.utils.messages.email import EmailService  # noqa: E402
from core.utils.uuid_utils import uuid7  # noqa: E402
from models.accounts.role import Permission, Role, Role_Permission  # noqa: E402
from models.accounts.user import AccountStatus, User  # noqa: E402
from models.bookings.hotel import HotelStaff  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASSWORD = "Passw0rdX"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
USING_POSTGRES = TEST_DATABASE_URL.startswith("postgresql")


def pytest_collection_modifyitems(config, items):
    if USING_POSTGRES:
        return
    skip = pytest.mark.skip(reason="needs PostgreSQL: set TEST_DATABASE_URL")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


def day(offset: int) -> str:
    """An ISO date `offset` days from today."""
    return str(date.today() + timedelta(days=offset))


class Api:
    """The running app plus helpers for setting up users, staff and hotels through it."""

    def __init__(self, client: httpx.AsyncClient, sessions, sent: list):
        self.client = client
        self.sessions = sessions
        self.sent = sent                 # (kind, to_email, context) for every email the app tried to send
        self._numbers = itertools.count(1)
        self.admin: dict = {}            # the superadmin's Authorization header

    # ---- people ----
    async def user(self, email: str, role: str = "customer", status: AccountStatus = AccountStatus.ACTIVE) -> str:
        async with self.sessions() as db:
            role_row = (await db.execute(select(Role).where(Role.name == role))).scalar_one()
            user = User(
                id=uuid7(), email=email, firstname="test", lastname="user", middlename="", language="en",
                passwordhash=PasswordManager.hash_password(PASSWORD), role_id=role_row.id, account_status=status,
            )
            db.add(user)
            await db.commit()
            return str(user.id)

    async def login(self, email: str) -> dict:
        response = await self.client.post("/v1/auth/login/", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

    async def guest(self, email: str = None) -> dict:
        email = email or f"guest{next(self._numbers)}@example.com"
        await self.user(email)
        return await self.login(email)

    async def staff(self, scopes, hotels=(), access_all: bool = False, email: str = None) -> dict:
        """A staff member whose role holds `scopes`, assigned to `hotels` (hotel ids)."""
        n = next(self._numbers)
        email = email or f"staff{n}@example.com"
        names = [Scope(s).value for s in scopes] + ([Scope.HOTELS_ACCESS_ALL.value] if access_all else [])
        async with self.sessions() as db:
            role = Role(id=uuid7(), name=f"staff_{n}")
            db.add(role)
            await db.flush()
            for name in names:
                permission = (await db.execute(select(Permission).where(Permission.name == name))).scalar_one()
                db.add(Role_Permission(id=uuid7(), role_id=role.id, permission_id=permission.id))
            await db.commit()
        user_id = await self.user(email, role=f"staff_{n}")
        async with self.sessions() as db:
            for hotel_id in hotels:
                db.add(HotelStaff(id=uuid7(), hotel_id=hotel_id, user_id=user_id))
            await db.commit()
        return await self.login(email)

    # ---- hotels ----
    async def post(self, path: str, headers: dict, body: dict, expect: int = 201) -> dict:
        response = await self.client.post(path, headers=headers, json=body)
        assert response.status_code == expect, f"{path}: {response.status_code} {response.text}"
        return response.json()["data"]

    async def hotel(self, **overrides) -> dict:
        body = {"name": f"Hotel {next(self._numbers)}", "city": "Port Harcourt", "country": "Nigeria", "currency": "NGN"}
        return await self.post("/v1/hotels/", self.admin, {**body, **overrides})

    async def room_type(self, hotel_id: str, **overrides) -> dict:
        body = {"hotel_id": hotel_id, "name": f"Type {next(self._numbers)}", "bed": "king", "max_guests": 2, "price_per_night": 10000}
        return await self.post("/v1/room-types/", self.admin, {**body, **overrides})

    async def room(self, hotel_id: str, room_type_id: str, number: str = None) -> dict:
        number = number or str(100 + next(self._numbers))
        return await self.post("/v1/rooms/", self.admin, {"hotel_id": hotel_id, "room_type_id": room_type_id, "number": number})

    async def setup(self, price: int = 10000, rooms: int = 1, **hotel) -> dict:
        """A hotel with one room type and `rooms` rooms: {'hotel', 'type', 'rooms'}."""
        h = await self.hotel(**hotel)
        t = await self.room_type(h["id"], price_per_night=price)
        return {"hotel": h, "type": t, "rooms": [await self.room(h["id"], t["id"]) for _ in range(rooms)]}

    async def book(self, headers: dict, room_id: str, start: int = 10, nights: int = 2, guests: int = 1, expect: int = 201) -> dict:
        response = await self.client.post("/v1/bookings/", headers=headers, json={
            "room_id": room_id, "check_in": day(start), "check_out": day(start + nights), "guests": guests,
        })
        assert response.status_code == expect, f"book: {response.status_code} {response.text}"
        return response.json()["data"] if expect == 201 else response.json()


@pytest_asyncio.fixture
async def api(monkeypatch):
    if USING_POSTGRES:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        async with engine.begin() as conn:
            for schema in ("accounts", "bookings", "billings"):
                await conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool,
            execution_options={"schema_translate_map": {"accounts": None, "bookings": None, "billings": None}},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    # The roles the migration seeds, with every permission for the superadmin
    async with sessions() as db:
        roles = {name: Role(id=uuid7(), name=name) for name in ("customer", "superadmin")}
        db.add_all(roles.values())
        permissions = [Permission(id=uuid7(), name=scope.value) for scope in Scope]
        db.add_all(permissions)
        await db.flush()
        for permission in permissions:
            db.add(Role_Permission(id=uuid7(), role_id=roles["superadmin"].id, permission_id=permission.id))
        await db.commit()

    async def override_db():
        async with sessions() as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db

    sent: list = []

    async def capture(to_email, kind, context, language=None):
        sent.append((kind, to_email, context))

    monkeypatch.setattr(EmailService, "deliver", staticmethod(capture))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        helper = Api(client, sessions, sent)
        await helper.user("admin@example.com", role="superadmin")
        helper.admin = await helper.login("admin@example.com")
        yield helper

    main.app.dependency_overrides.clear()
    await engine.dispose()


# ---- steps shared by the payment, cancellation and staff tests ----

async def pay(api, guest, booking, amount=None, confirm=True):
    """Start a payment for a booking and (optionally) have staff confirm it."""
    body = {"booking_id": booking["id"]}
    if amount is not None:
        body["amount"] = amount
    payment = (await api.client.post("/v1/payments/", headers=guest, json=body)).json()["data"]
    if confirm:
        confirmed = await api.client.post(f"/v1/payments/{payment['id']}/confirm/", headers=api.admin, json={})
        assert confirmed.status_code == 200, confirmed.text
        payment = confirmed.json()["data"]
    return payment


async def summary(api, guest, booking):
    return (await api.client.get(f"/v1/payments/booking/{booking['id']}/summary/", headers=guest)).json()["data"]
