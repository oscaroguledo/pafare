"""Hotels, room types, rooms, pricing, search, and which hotels a staff member may work on."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from conftest import day, pay
from core.scopes import Scope
from services.bookings.room_type import RoomTypeService

money, price_stay, rate_for_night = RoomTypeService.money, RoomTypeService.price_stay, RoomTypeService.rate_for_night


# ===== catalog =====


async def test_hotels_are_public_to_read_and_staff_only_to_change(api):
    guest = await api.guest()
    assert (await api.client.post("/v1/hotels/", headers=guest, json={"name": "H"})).status_code == 403
    assert (await api.client.post("/v1/hotels/", json={"name": "H"})).status_code == 401
    hotel = await api.hotel(name="Grand Harbour")
    assert (await api.client.get("/v1/hotels/")).status_code == 200
    got = await api.client.get(f"/v1/hotels/{hotel['id']}/")
    assert got.status_code == 200 and got.json()["data"]["name"] == "Grand Harbour"


async def test_hotel_defaults_and_policy_fields(api):
    hotel = await api.hotel()
    assert hotel["tax_percent"] in (0, "0", "0.00", 0.0)
    assert hotel["free_cancellation_hours"] == 48 and hotel["late_cancellation_fee_percent"] == 100
    bad = await api.client.post("/v1/hotels/", headers=api.admin, json={"name": "X", "tax_percent": 120})
    assert bad.status_code == 422


async def test_hotel_search_and_paging(api):
    await api.hotel(name="Alpha", city="Lagos")
    await api.hotel(name="Beta", city="Abuja")
    found = await api.client.get("/v1/hotels/", params={"city": "lag"})
    assert [h["name"] for h in found.json()["data"]] == ["Alpha"]
    page = await api.client.get("/v1/hotels/", params={"limit": 1, "page": 2})
    assert page.json()["pagination"]["total"] == 2 and len(page.json()["data"]) == 1


async def test_hotel_update_rules(api):
    hotel = await api.hotel()
    ok = await api.client.patch(f"/v1/hotels/{hotel['id']}/", headers=api.admin, json={"city": "Abuja", "tax_percent": 7.5})
    assert ok.status_code == 200 and ok.json()["data"]["city"] == "Abuja"
    assert (await api.client.patch(f"/v1/hotels/{hotel['id']}/", headers=api.admin, json={})).status_code == 400
    assert (await api.client.patch(f"/v1/hotels/{hotel['id']}/", headers=api.admin, json={"name": None})).status_code == 400


async def test_currency_cannot_change_once_there_are_bookings(api):
    guest = await api.guest()
    s = await api.setup()
    await api.book(guest, s["rooms"][0]["id"])
    response = await api.client.patch(f"/v1/hotels/{s['hotel']['id']}/", headers=api.admin, json={"currency": "USD"})
    assert response.status_code == 409


async def test_room_types_belong_to_a_hotel_and_names_are_unique_per_hotel(api):
    h1, h2 = await api.hotel(), await api.hotel()
    await api.room_type(h1["id"], name="Deluxe King")
    assert (await api.client.post("/v1/room-types/", headers=api.admin, json={"hotel_id": h1["id"], "name": "deluxe king", "bed": "king"})).status_code == 409
    other = await api.room_type(h2["id"], name="Deluxe King")          # the same name at another hotel is fine
    assert other["hotel_id"] == h2["id"]
    assert (await api.client.post("/v1/room-types/", headers=api.admin, json={"hotel_id": "00000000-0000-0000-0000-000000000000", "name": "X", "bed": "king"})).status_code == 404


async def test_room_type_capacity_validation_and_filters(api):
    hotel = await api.hotel()
    assert (await api.client.post("/v1/room-types/", headers=api.admin, json={"hotel_id": hotel["id"], "name": "Zero", "bed": "king", "max_guests": 0})).status_code == 422
    await api.room_type(hotel["id"], name="Single", bed="single", max_guests=1, price_per_night=5000)
    await api.room_type(hotel["id"], name="Family", bed="queen", max_guests=5, wifi=True, price_per_night=30000)
    big = await api.client.get("/v1/room-types/", params={"hotel_id": hotel["id"], "min_guests": 4})
    assert [t["name"] for t in big.json()["data"]] == ["Family"]
    wifi = await api.client.get("/v1/room-types/", params=[("hotel_id", hotel["id"]), ("amenities", "wifi")])
    assert [t["name"] for t in wifi.json()["data"]] == ["Family"]
    cheap = await api.client.get("/v1/room-types/", params={"hotel_id": hotel["id"], "max_price": 10000})
    assert [t["name"] for t in cheap.json()["data"]] == ["Single"]
    assert (await api.client.get("/v1/room-types/", params=[("amenities", "sauna")])).status_code == 422


async def test_rooms_need_a_room_type_from_the_same_hotel(api):
    h1, h2 = await api.hotel(), await api.hotel()
    foreign = await api.room_type(h2["id"])
    response = await api.client.post("/v1/rooms/", headers=api.admin, json={"hotel_id": h1["id"], "room_type_id": foreign["id"], "number": "1"})
    assert response.status_code == 400 and "another hotel" in response.json()["message"]
    own = await api.room_type(h1["id"])
    room = await api.room(h1["id"], own["id"], "1")
    assert (await api.client.patch(f"/v1/rooms/{room['id']}/", headers=api.admin, json={"room_type_id": foreign["id"]})).status_code == 400


async def test_room_numbers_are_unique_per_hotel(api):
    h1, h2 = await api.hotel(), await api.hotel()
    t1, t2 = await api.room_type(h1["id"]), await api.room_type(h2["id"])
    await api.room(h1["id"], t1["id"], "101")
    assert (await api.client.post("/v1/rooms/", headers=api.admin, json={"hotel_id": h1["id"], "room_type_id": t1["id"], "number": "101"})).status_code == 409
    assert (await api.room(h2["id"], t2["id"], "101"))["number"] == "101"


async def test_room_shows_capacity_price_and_currency(api):
    s = await api.setup(price=12345, currency="GHS")
    room = (await api.client.get(f"/v1/rooms/{s['rooms'][0]['id']}/")).json()["data"]
    assert room["max_guests"] == 2 and room["price_per_night"] == 12345 and room["currency"] == "GHS"


async def test_room_list_filters_by_guests_and_availability(api):
    guest = await api.guest()
    hotel = await api.hotel()
    small = await api.room_type(hotel["id"], name="Small", max_guests=1)
    large = await api.room_type(hotel["id"], name="Large", max_guests=4)
    r_small, r_large = await api.room(hotel["id"], small["id"], "1"), await api.room(hotel["id"], large["id"], "2")
    sleeps_3 = await api.client.get("/v1/rooms/", params={"hotel_id": hotel["id"], "guests": 3})
    assert [r["number"] for r in sleeps_3.json()["data"]] == ["2"]
    await api.book(guest, r_large["id"], start=10, nights=3)
    free = await api.client.get("/v1/rooms/", params={"hotel_id": hotel["id"], "available_from": day(11), "available_to": day(12)})
    assert [r["number"] for r in free.json()["data"]] == ["1"]
    assert (await api.client.get("/v1/rooms/", params={"available_from": day(11)})).status_code == 422


async def test_deleting_is_refused_while_things_depend_on_it(api):
    guest = await api.guest()
    s = await api.setup()
    await api.book(guest, s["rooms"][0]["id"])
    assert (await api.client.delete(f"/v1/rooms/{s['rooms'][0]['id']}/", headers=api.admin)).status_code == 409      # has bookings
    assert (await api.client.delete(f"/v1/room-types/{s['type']['id']}/", headers=api.admin)).status_code == 409    # used by a room
    assert (await api.client.delete(f"/v1/hotels/{s['hotel']['id']}/", headers=api.admin)).status_code == 409        # has rooms
    empty = await api.hotel()
    spare = await api.room_type(empty["id"])
    assert (await api.client.delete(f"/v1/room-types/{spare['id']}/", headers=api.admin)).status_code == 200
    assert (await api.client.delete(f"/v1/hotels/{empty['id']}/", headers=api.admin)).status_code == 200


# ===== pricing =====


def rate(price, start=None, end=None, weekdays=None, priority=0, name="rule", created=0):
    return SimpleNamespace(
        price_per_night=price, start_date=start, end_date=end, weekdays=weekdays, priority=priority,
        name=name, created_at=created, id=name,
    )


# 2030-01-07 is a Monday
MON, TUE, FRI, SAT, SUN = (date(2030, 1, d) for d in (7, 8, 11, 12, 13))


def test_standard_price_when_no_rule_matches():
    assert rate_for_night(100, [], MON) == (100, None)
    assert rate_for_night(100, [rate(150, weekdays="4,5")], MON) == (100, None)


def test_weekday_rule_applies_only_on_those_days():
    weekend = rate(150, weekdays="4,5", name="Weekend")      # Friday, Saturday
    assert rate_for_night(100, [weekend], FRI) == (150, "Weekend")
    assert rate_for_night(100, [weekend], SAT) == (150, "Weekend")
    assert rate_for_night(100, [weekend], SUN) == (100, None)


def test_date_range_rule_is_inclusive():
    xmas = rate(300, start=date(2030, 12, 20), end=date(2030, 12, 26), name="Christmas")
    assert rate_for_night(100, [xmas], date(2030, 12, 20))[0] == 300
    assert rate_for_night(100, [xmas], date(2030, 12, 26))[0] == 300
    assert rate_for_night(100, [xmas], date(2030, 12, 19))[0] == 100
    assert rate_for_night(100, [xmas], date(2030, 12, 27))[0] == 100


def test_highest_priority_wins_and_the_newest_breaks_a_tie():
    weekend = rate(150, weekdays="4,5", priority=0, name="Weekend")
    xmas = rate(300, start=date(2030, 1, 1), end=date(2030, 1, 31), priority=10, name="January")
    assert rate_for_night(100, [weekend, xmas], FRI) == (300, "January")
    old, new = rate(120, name="old", created=1), rate(130, name="new", created=2)
    assert rate_for_night(100, [old, new], MON) == (130, "new")


def test_stay_is_priced_night_by_night_with_discount_then_tax():
    weekend = rate(200, weekdays="4,5")
    priced = price_stay(100, [weekend], FRI, SUN, discount=0, tax_percent=Decimal("7.5"))
    assert [n["price"] for n in priced["nights"]] == [200, 200]
    assert priced["base_price"] == 400 and priced["tax_amount"] == 30 and priced["total_price"] == 430
    discounted = price_stay(100, [], MON, date(2030, 1, 9), discount=10, tax_percent=Decimal("10"))
    assert discounted["base_price"] == 200
    assert discounted["tax_amount"] == 18 and discounted["total_price"] == 198      # (200 - 10%) = 180, tax 10% of 180


@pytest.mark.parametrize("value, rounded", [(Decimal("2.5"), 3), (Decimal("2.49"), 2), (Decimal("0"), 0)])
def test_money_rounds_halves_up(value, rounded):
    assert money(value) == rounded


async def test_quote_matches_the_booking_price(api):
    guest = await api.guest()
    s = await api.setup(price=10000, tax_percent=7.5)
    room = s["rooms"][0]["id"]
    quote = (await api.client.get(f"/v1/rooms/{room}/quote/", params={"check_in": day(10), "check_out": day(13)})).json()["data"]
    assert len(quote["nights"]) == 3 and quote["base_price"] == 30000
    assert quote["tax_amount"] == 2250 and quote["total_price"] == 32250 and quote["currency"] == "NGN"
    booking = await api.book(guest, room, start=10, nights=3)
    assert (booking["base_price"], booking["tax_amount"], booking["total_price"]) == (30000, 2250, 32250)
    assert Decimal(str(booking["tax_percent"])) == Decimal("7.5")


async def test_quote_checks_its_input(api):
    s = await api.setup()
    room = s["rooms"][0]["id"]
    bad_order = await api.client.get(f"/v1/rooms/{room}/quote/", params={"check_in": day(5), "check_out": day(5)})
    too_many = await api.client.get(f"/v1/rooms/{room}/quote/", params={"check_in": day(5), "check_out": day(6), "guests": 9})
    too_long = await api.client.get(f"/v1/rooms/{room}/quote/", params={"check_in": day(5), "check_out": day(200)})
    unknown = await api.client.get("/v1/rooms/00000000-0000-0000-0000-000000000000/quote/", params={"check_in": day(5), "check_out": day(6)})
    assert (bad_order.status_code, too_many.status_code, too_long.status_code, unknown.status_code) == (400, 400, 400, 404)


async def test_rate_rules_change_the_price_of_new_bookings(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    room, type_id = s["rooms"][0]["id"], s["type"]["id"]
    created = await api.client.post(f"/v1/room-types/{type_id}/rates/", headers=api.admin, json={
        "name": "Everything", "price_per_night": 20000, "priority": 5,
    })
    assert created.status_code == 201 and created.json()["data"]["weekdays"] is None
    booking = await api.book(guest, room, start=10, nights=2)
    assert booking["base_price"] == 40000
    # The booking made before a rule keeps its price
    later = await api.client.patch(f"/v1/room-types/{type_id}/rates/{created.json()['data']['id']}/", headers=api.admin, json={"price_per_night": 50000})
    assert later.status_code == 200
    assert (await api.client.get(f"/v1/bookings/{booking['id']}/", headers=guest)).json()["data"]["base_price"] == 40000


async def test_weekday_rule_through_the_api(api):
    s = await api.setup(price=10000)
    type_id = s["type"]["id"]
    rule = await api.client.post(f"/v1/room-types/{type_id}/rates/", headers=api.admin, json={
        "name": "Weekend", "price_per_night": 15000, "weekdays": [5, 4, 4],
    })
    assert rule.status_code == 201 and rule.json()["data"]["weekdays"] == [4, 5]
    quote = (await api.client.get(f"/v1/rooms/{s['rooms'][0]['id']}/quote/", params={"check_in": day(10), "check_out": day(17)})).json()["data"]
    prices = sorted(n["price"] for n in quote["nights"])
    assert prices == [10000] * 5 + [15000] * 2          # a week has exactly one Friday and one Saturday
    rules = (await api.client.get(f"/v1/room-types/{type_id}/rates/")).json()["data"]
    assert [r["name"] for r in rules] == ["Weekend"]


async def test_rate_rule_validation_and_permissions(api):
    s = await api.setup()
    type_id = s["type"]["id"]
    guest = await api.guest()
    path = f"/v1/room-types/{type_id}/rates/"
    assert (await api.client.post(path, headers=guest, json={"name": "x", "price_per_night": 1})).status_code == 403
    assert (await api.client.post(path, headers=api.admin, json={"name": "x", "price_per_night": -1})).status_code == 422
    assert (await api.client.post(path, headers=api.admin, json={"name": "x", "price_per_night": 1, "weekdays": [7]})).status_code == 422
    assert (await api.client.post(path, headers=api.admin, json={"name": "x", "price_per_night": 1, "start_date": day(5), "end_date": day(1)})).status_code == 422
    rule = (await api.client.post(path, headers=api.admin, json={"name": "x", "price_per_night": 1})).json()["data"]
    assert (await api.client.patch(f"{path}{rule['id']}/", headers=api.admin, json={})).status_code == 400
    assert (await api.client.delete(f"{path}{rule['id']}/", headers=api.admin)).status_code == 200


async def test_discount_comes_off_before_tax(api):
    guest = await api.guest()
    s = await api.setup(price=10000, tax_percent=10)
    booking = await api.book(guest, s["rooms"][0]["id"], nights=2)
    assert booking["total_price"] == 22000                                     # 20000 + 10% tax
    response = await api.client.put(f"/v1/bookings/{booking['id']}/discount/", headers=api.admin, json={"discount": 50})
    data = response.json()["data"]
    assert response.status_code == 200
    assert (data["base_price"], data["tax_amount"], data["total_price"]) == (20000, 1000, 11000)
    assert (await api.client.put(f"/v1/bookings/{booking['id']}/discount/", headers=api.admin, json={"discount": 101})).status_code == 422
    assert (await api.client.put(f"/v1/bookings/{booking['id']}/discount/", headers=guest, json={"discount": 50})).status_code == 403


async def test_rescheduling_reprices_and_keeps_the_discount(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], nights=2)
    await api.client.put(f"/v1/bookings/{booking['id']}/discount/", headers=api.admin, json={"discount": 50})
    moved = await api.client.patch(f"/v1/bookings/{booking['id']}/", headers=guest, json={"check_out": day(13)})
    data = moved.json()["data"]
    assert moved.status_code == 200 and data["base_price"] == 30000 and data["total_price"] == 15000


# ===== search =====


async def search(api, **params):
    params = {"check_in": day(10), "check_out": day(12), **params}
    return await api.client.get("/v1/rooms/search/", params=params)


async def test_search_lists_room_types_that_are_free_with_prices(api):
    s = await api.setup(price=10000, rooms=3, city="Port Harcourt", tax_percent=10)
    response = await search(api)
    assert response.status_code == 200
    [result] = response.json()["data"]
    assert result["hotel"]["name"] == s["hotel"]["name"] and result["hotel"]["average_rating"] is None
    assert result["room_type"]["id"] == s["type"]["id"] and result["rooms_available"] == 3
    assert result["nights"] == 2 and result["nightly_average"] == 10000
    assert (result["base_price"], result["tax_amount"], result["total_price"]) == (20000, 2000, 22000)
    assert result["room_id"] in [r["id"] for r in s["rooms"]]


async def test_search_result_room_can_be_booked_straight_away(api):
    guest = await api.guest()
    await api.setup()
    [result] = (await search(api)).json()["data"]
    booking = await api.book(guest, result["room_id"], start=10, nights=2)
    assert booking["total_price"] == result["total_price"]


async def test_booked_rooms_drop_out_of_the_count_and_then_the_results(api):
    guest = await api.guest()
    s = await api.setup(rooms=2)
    await api.book(guest, s["rooms"][0]["id"], start=10, nights=2)
    [result] = (await search(api)).json()["data"]
    assert result["rooms_available"] == 1 and result["room_id"] == s["rooms"][1]["id"]
    await api.book(guest, s["rooms"][1]["id"], start=11, nights=2)
    assert (await search(api)).json()["data"] == []
    assert len((await search(api, check_in=day(20), check_out=day(22))).json()["data"]) == 1    # other dates are free


async def test_search_filters_by_capacity_city_bed_amenities_and_price(api):
    lagos = await api.hotel(name="Lagoon", city="Lagos")
    abuja = await api.hotel(name="Capital", city="Abuja")
    t1 = await api.room_type(lagos["id"], name="Single", bed="single", max_guests=1, price_per_night=5000)
    t2 = await api.room_type(lagos["id"], name="Family", bed="queen", max_guests=5, wifi=True, tv=True, price_per_night=30000)
    t3 = await api.room_type(abuja["id"], name="Suite", bed="king", max_guests=2, price_per_night=60000)
    for t, h in ((t1, lagos), (t2, lagos), (t3, abuja)):
        await api.room(h["id"], t["id"])

    names = lambda response: sorted(r["room_type"]["name"] for r in response.json()["data"])
    assert names(await search(api)) == ["Family", "Single", "Suite"]
    assert names(await search(api, guests=3)) == ["Family"]
    assert names(await search(api, city="abu")) == ["Suite"]
    assert names(await search(api, bed="single")) == ["Single"]
    assert names(await search(api, amenities=["wifi", "tv"])) == ["Family"]
    assert names(await search(api, min_price=20000, max_price=40000)) == ["Family"]
    assert (await search(api, amenities=["sauna"])).status_code == 422


async def test_search_uses_seasonal_rates(api):
    s = await api.setup(price=10000)
    await api.client.post(f"/v1/room-types/{s['type']['id']}/rates/", headers=api.admin, json={"name": "Peak", "price_per_night": 25000})
    [result] = (await search(api)).json()["data"]
    assert result["base_price"] == 50000 and result["room_type"]["price_per_night"] == 10000


async def test_search_sorts_and_pages(api):
    h = await api.hotel(name="Sorted")
    for name, price in (("Cheap", 1000), ("Mid", 5000), ("Dear", 9000)):
        t = await api.room_type(h["id"], name=name, price_per_night=price)
        await api.room(h["id"], t["id"])
    order = lambda response: [r["room_type"]["name"] for r in response.json()["data"]]
    assert order(await search(api)) == ["Cheap", "Mid", "Dear"]
    assert order(await search(api, sort="price_desc")) == ["Dear", "Mid", "Cheap"]
    page = await search(api, limit=2, page=2)
    assert order(page) == ["Dear"] and page.json()["pagination"]["total"] == 3
    # every result on a page carries a bookable room
    assert all(r["room_id"] for r in (await search(api, limit=2)).json()["data"])


async def test_search_ranks_by_rating_when_asked(api):
    guest = await api.guest()
    good, plain = await api.setup(name="Good"), await api.setup(name="Plain")
    booking = await api.book(guest, good["rooms"][0]["id"], start=1, nights=1)
    for step in ("confirm", "check-in", "check-out"):
        assert (await api.client.post(f"/v1/bookings/{booking['id']}/{step}/", headers=api.admin)).status_code == 200
    review = (await api.client.post("/v1/reviews/", headers=guest, json={"booking_id": booking["id"], "rating": 5})).json()["data"]
    await api.client.put(f"/v1/reviews/{review['id']}/approval/", headers=api.admin, json={"is_approved": True})
    data = (await search(api, sort="rating", check_in=day(30), check_out=day(31))).json()["data"]
    assert [r["hotel"]["name"] for r in data] == ["Good", "Plain"]
    assert data[0]["hotel"]["average_rating"] == 5.0 and data[0]["hotel"]["review_count"] == 1


async def test_search_needs_valid_dates(api):
    assert (await search(api, check_out=day(10))).status_code == 422
    assert (await api.client.get("/v1/rooms/search/", params={"check_in": day(1)})).status_code == 422
    assert (await search(api, check_in=day(1), check_out=day(300))).status_code == 422


# ===== hotel staff =====


MANAGER = [
    Scope.HOTELS_EDIT, Scope.HOTELS_ASSIGN_STAFF, Scope.ROOM_TYPES_CREATE, Scope.ROOM_TYPES_EDIT, Scope.ROOMS_CREATE,
    Scope.ROOMS_EDIT, Scope.ROOM_RATES_CREATE, Scope.BOOKINGS_VIEW, Scope.BOOKINGS_CONFIRM, Scope.BOOKINGS_CANCEL,
    Scope.BOOKINGS_CREATE, Scope.PAYMENTS_VIEW, Scope.PAYMENTS_CONFIRM, Scope.REVIEWS_VIEW, Scope.REVIEWS_MODERATE,
]


async def two_hotels(api):
    a, b = await api.setup(name="Hotel A"), await api.setup(name="Hotel B")
    guest = await api.guest()
    return a, b, guest, await api.book(guest, a["rooms"][0]["id"], start=10), await api.book(guest, b["rooms"][0]["id"], start=10)


async def test_a_manager_can_change_only_their_own_hotel(api):
    a, b, *_ = await two_hotels(api)
    manager = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    own = await api.client.patch(f"/v1/hotels/{a['hotel']['id']}/", headers=manager, json={"city": "Abuja"})
    other = await api.client.patch(f"/v1/hotels/{b['hotel']['id']}/", headers=manager, json={"city": "Abuja"})
    assert own.status_code == 200 and other.status_code == 403
    assert "don't manage" in other.json()["message"]


async def test_room_types_rooms_and_rates_follow_the_same_rule(api):
    a, b, *_ = await two_hotels(api)
    manager = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    post = lambda path, body: api.client.post(path, headers=manager, json=body)
    assert (await post("/v1/room-types/", {"hotel_id": a["hotel"]["id"], "name": "New", "bed": "king"})).status_code == 201
    assert (await post("/v1/room-types/", {"hotel_id": b["hotel"]["id"], "name": "New", "bed": "king"})).status_code == 403
    assert (await post("/v1/rooms/", {"hotel_id": a["hotel"]["id"], "room_type_id": a["type"]["id"], "number": "900"})).status_code == 201
    assert (await post("/v1/rooms/", {"hotel_id": b["hotel"]["id"], "room_type_id": b["type"]["id"], "number": "900"})).status_code == 403
    assert (await post(f"/v1/room-types/{a['type']['id']}/rates/", {"name": "x", "price_per_night": 1})).status_code == 201
    assert (await post(f"/v1/room-types/{b['type']['id']}/rates/", {"name": "x", "price_per_night": 1})).status_code == 403
    assert (await api.client.patch(f"/v1/rooms/{b['rooms'][0]['id']}/", headers=manager, json={"number": "77"})).status_code == 403
    assert (await api.client.patch(f"/v1/room-types/{b['type']['id']}/", headers=manager, json={"name": "Renamed"})).status_code == 403


async def test_bookings_are_limited_to_the_staffs_hotels(api):
    a, b, guest, booking_a, booking_b = await two_hotels(api)
    manager = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    listed = (await api.client.get("/v1/bookings/", headers=manager)).json()
    assert [x["id"] for x in listed["data"]] == [booking_a["id"]]
    assert (await api.client.get(f"/v1/bookings/{booking_a['id']}/", headers=manager)).status_code == 200
    assert (await api.client.get(f"/v1/bookings/{booking_b['id']}/", headers=manager)).status_code == 404     # not revealed
    assert (await api.client.post(f"/v1/bookings/{booking_b['id']}/confirm/", headers=manager)).status_code == 404
    assert (await api.client.post(f"/v1/bookings/{booking_a['id']}/confirm/", headers=manager)).status_code == 200
    assert (await api.client.post(f"/v1/bookings/{booking_b['id']}/cancel/", headers=manager)).status_code == 404
    body = {"room_id": b["rooms"][0]["id"], "check_in": day(30), "check_out": day(31)}
    assert (await api.client.post(f"/v1/bookings/for/{booking_a['user_id']}/", headers=manager, json=body)).status_code == 403


async def test_payments_and_reviews_are_limited_too(api):
    a, b, guest, booking_a, booking_b = await two_hotels(api)
    pay_a, pay_b = await pay(api, guest, booking_a, confirm=False), await pay(api, guest, booking_b, confirm=False)
    manager = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    assert (await api.client.post(f"/v1/payments/{pay_a['id']}/confirm/", headers=manager, json={})).status_code == 200
    assert (await api.client.post(f"/v1/payments/{pay_b['id']}/confirm/", headers=manager, json={})).status_code == 404
    assert [p["id"] for p in (await api.client.get("/v1/payments/", headers=manager)).json()["data"]] == [pay_a["id"]]
    assert (await api.client.get(f"/v1/payments/booking/{booking_b['id']}/summary/", headers=manager)).status_code == 404


async def test_review_moderation_is_limited_to_the_staffs_hotels(api):
    a, b, guest, *_ = await two_hotels(api)
    reviews = []
    for s in (a, b):
        booking = await api.book(guest, s["rooms"][0]["id"], start=1, nights=1)
        for step in ("confirm", "check-in", "check-out"):
            await api.client.post(f"/v1/bookings/{booking['id']}/{step}/", headers=api.admin)
        reviews.append((await api.client.post("/v1/reviews/", headers=guest, json={"booking_id": booking["id"], "rating": 4})).json()["data"])
    manager = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    approve = lambda r: api.client.put(f"/v1/reviews/{r['id']}/approval/", headers=manager, json={"is_approved": True})
    assert (await approve(reviews[0])).status_code == 200 and (await approve(reviews[1])).status_code == 404
    queue = (await api.client.get("/v1/reviews/all/", headers=manager)).json()
    assert [r["id"] for r in queue["data"]] == [reviews[0]["id"]]
    assert (await api.client.get(f"/v1/reviews/{reviews[1]['id']}/", headers=manager)).status_code == 404   # unapproved, other hotel


async def test_staff_with_no_hotel_assigned_can_do_nothing(api):
    a, b, guest, booking_a, _ = await two_hotels(api)
    nobody = await api.staff(MANAGER)
    assert (await api.client.get("/v1/bookings/", headers=nobody)).json()["data"] == []
    assert (await api.client.get(f"/v1/bookings/{booking_a['id']}/", headers=nobody)).status_code == 404
    assert (await api.client.patch(f"/v1/hotels/{a['hotel']['id']}/", headers=nobody, json={"city": "X"})).status_code == 403


async def test_access_all_works_on_every_hotel(api):
    a, b, guest, booking_a, booking_b = await two_hotels(api)
    everywhere = await api.staff(MANAGER, access_all=True)
    assert (await api.client.get("/v1/bookings/", headers=everywhere)).json()["pagination"]["total"] == 2
    assert (await api.client.patch(f"/v1/hotels/{b['hotel']['id']}/", headers=everywhere, json={"city": "Abuja"})).status_code == 200


async def test_missing_permission_still_blocks_even_at_your_own_hotel(api):
    a, *_ = await two_hotels(api)
    limited = await api.staff([Scope.BOOKINGS_VIEW], hotels=[a["hotel"]["id"]])
    assert (await api.client.patch(f"/v1/hotels/{a['hotel']['id']}/", headers=limited, json={"city": "X"})).status_code == 403
    assert "Missing permission" in (await api.client.patch(f"/v1/hotels/{a['hotel']['id']}/", headers=limited, json={"city": "X"})).json()["message"]


async def test_a_hotel_creator_is_assigned_to_it(api):
    creator = await api.staff([Scope.HOTELS_CREATE, Scope.HOTELS_EDIT, Scope.ROOMS_CREATE, Scope.ROOM_TYPES_CREATE])
    made = (await api.client.post("/v1/hotels/", headers=creator, json={"name": "Mine"})).json()["data"]
    assert (await api.client.patch(f"/v1/hotels/{made['id']}/", headers=creator, json={"city": "Lagos"})).status_code == 200
    other = await api.hotel()
    assert (await api.client.patch(f"/v1/hotels/{other['id']}/", headers=creator, json={"city": "Lagos"})).status_code == 403


async def test_assigning_and_removing_staff(api):
    a, b, *_ = await two_hotels(api)
    boss = await api.staff(MANAGER, hotels=[a["hotel"]["id"]])
    new_id = await api.user("newhire@example.com")
    path = lambda hotel: f"/v1/hotels/{hotel['hotel']['id']}/staff/"
    assert (await api.client.post(path(b), headers=boss, json={"user_id": new_id})).status_code == 403    # not their hotel
    assigned = await api.client.post(path(a), headers=boss, json={"user_id": new_id})
    assert assigned.status_code == 201 and assigned.json()["data"]["email"] == "newhire@example.com"
    again = await api.client.post(path(a), headers=boss, json={"user_id": new_id})
    assert again.status_code == 201                                                                       # harmless to repeat
    members = (await api.client.get(path(a), headers=boss)).json()["data"]
    assert "newhire@example.com" in [m["email"] for m in members]
    assert (await api.client.post(path(a), headers=boss, json={"user_id": "00000000-0000-0000-0000-000000000000"})).status_code == 404
    assert (await api.client.delete(f"{path(a)}{new_id}/", headers=boss)).status_code == 200
    assert (await api.client.delete(f"{path(a)}{new_id}/", headers=boss)).status_code == 404
    assert (await api.client.get(path(a), headers=await api.guest())).status_code == 403
