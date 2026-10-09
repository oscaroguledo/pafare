"""Needs a real PostgreSQL (TEST_DATABASE_URL): SQLite cannot lock rows, so the races below only mean something there."""
import asyncio

import pytest

from conftest import day, pay

pytestmark = pytest.mark.postgres


async def test_many_guests_booking_the_same_room_at_once_only_one_wins(api):
    room = (await api.setup())["rooms"][0]["id"]
    guests = [await api.guest() for _ in range(12)]
    body = {"room_id": room, "check_in": day(10), "check_out": day(13), "guests": 1}
    responses = await asyncio.gather(*[api.client.post("/v1/bookings/", headers=g, json=body) for g in guests])
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1 and codes.count(409) == 11, codes


async def test_overlapping_but_different_dates_race_cleanly(api):
    room = (await api.setup())["rooms"][0]["id"]
    guests = [await api.guest() for _ in range(6)]
    # six stays that all overlap their neighbours: any two adjacent ones conflict
    responses = await asyncio.gather(*[
        api.client.post("/v1/bookings/", headers=g, json={"room_id": room, "check_in": day(10 + i), "check_out": day(12 + i)})
        for i, g in enumerate(guests)
    ])
    made = sorted((10 + i) for i, r in enumerate(responses) if r.status_code == 201)
    assert made and all(b - a >= 2 for a, b in zip(made, made[1:])), made          # no two accepted stays overlap


async def test_simultaneous_payments_cannot_overpay_a_booking(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup(price=10000))["rooms"][0]["id"], nights=2)
    responses = await asyncio.gather(*[
        api.client.post("/v1/payments/", headers=guest, json={"booking_id": booking["id"]}) for _ in range(6)
    ])
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1 and codes.count(409) == 5, codes


async def test_cancelling_twice_at_once_refunds_once(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup(price=10000))["rooms"][0]["id"], start=10, nights=2)
    await pay(api, guest, booking)
    responses = await asyncio.gather(*[
        api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest) for _ in range(4)
    ])
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1 and codes.count(409) == 3, codes
    money = (await api.client.get(f"/v1/payments/booking/{booking['id']}/summary/", headers=guest)).json()["data"]
    assert money["refunded"] == 20000 and money["paid"] == 0


async def test_booking_codes_stay_unique_under_load(api):
    s = await api.setup(rooms=40)
    guests = [await api.guest() for _ in range(8)]
    responses = await asyncio.gather(*[
        api.client.post("/v1/bookings/", headers=guests[i % 8], json={"room_id": room["id"], "check_in": day(5), "check_out": day(6)})
        for i, room in enumerate(s["rooms"])
    ])
    codes = [r.json()["data"]["booking_code"] for r in responses if r.status_code == 201]
    assert len(codes) == 40 and len(set(codes)) == 40
