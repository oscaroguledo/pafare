"""Booking codes, bookings, cancellation and refunds, payments, and reviews."""
import re

import pytest

from conftest import day, pay, summary
from services.bookings.booking import BookingService

city_prefix, generate_booking_code = BookingService.city_prefix, BookingService.generate_booking_code


# ===== booking code =====


@pytest.mark.parametrize("place, prefix", [
    ("Port Harcourt", "PH"),       # two words: initials
    ("New York City", "NY"),
    ("Lagos", "LA"),               # one word: first two letters
    ("Abuja", "AB"),
    ("São Paulo", "SP"),           # accents dropped
    ("St. Louis", "SL"),
    ("Z", "ZX"),                   # padded
    ("", "XX"),
    ("42", "XX"),
    (None, "XX"),
])
def test_city_prefix(place, prefix):
    assert city_prefix(place) == prefix


def test_generated_codes_use_the_readable_alphabet():
    codes = [generate_booking_code("Port Harcourt") for _ in range(2000)]
    assert all(re.fullmatch(r"PH-[A-HJ-NP-Z2-9]{6}", code) for code in codes)


async def test_booking_gets_a_code_from_the_hotel_city(api):
    guest = await api.guest()
    ph = await api.setup(city="Port Harcourt")
    lagos = await api.setup(city="Lagos")
    assert re.fullmatch(r"PH-[A-HJ-NP-Z2-9]{6}", (await api.book(guest, ph["rooms"][0]["id"]))["booking_code"])
    assert re.fullmatch(r"LA-[A-HJ-NP-Z2-9]{6}", (await api.book(guest, lagos["rooms"][0]["id"]))["booking_code"])


async def test_hotel_without_a_city_uses_its_name(api):
    guest = await api.guest()
    hotel = await api.setup(name="Grand Harbour", city=None)
    assert (await api.book(guest, hotel["rooms"][0]["id"]))["booking_code"].startswith("GH-")


async def test_booking_codes_are_unique(api):
    guest = await api.guest()
    hotel = await api.setup(rooms=30)
    codes = [(await api.book(guest, room["id"], start=5))["booking_code"] for room in hotel["rooms"]]
    assert len(set(codes)) == 30


async def test_a_booking_is_found_by_its_code_in_any_case(api):
    guest = await api.guest()
    hotel = await api.setup()
    booking = await api.book(guest, hotel["rooms"][0]["id"])
    found = await api.client.get("/v1/bookings/", headers=guest, params={"code": booking["booking_code"].lower()})
    assert [b["id"] for b in found.json()["data"]] == [booking["id"]]


# ===== bookings =====


async def test_a_guest_books_a_room(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], start=10, nights=3, guests=2)
    assert booking["status"] == "pending" and booking["currency"] == "NGN"
    assert booking["base_price"] == 30000 and booking["total_price"] == 30000
    assert booking["hotel_name"] == s["hotel"]["name"] and booking["room_type_name"] == s["type"]["name"]
    assert booking["refund_amount"] == 0 and booking["cancellation_fee"] == 0


async def test_booking_needs_a_login_and_valid_input(api):
    s = await api.setup()
    room = s["rooms"][0]["id"]
    body = {"room_id": room, "check_in": day(5), "check_out": day(6)}
    assert (await api.client.post("/v1/bookings/", json=body)).status_code == 401
    guest = await api.guest()
    assert (await api.client.post("/v1/bookings/", headers=guest, json={**body, "check_out": day(5)})).status_code == 422
    assert (await api.client.post("/v1/bookings/", headers=guest, json={**body, "check_in": day(-3), "check_out": day(-1)})).status_code == 400
    assert (await api.client.post("/v1/bookings/", headers=guest, json={**body, "guests": 0})).status_code == 422
    assert (await api.client.post("/v1/bookings/", headers=guest, json={**body, "room_id": "00000000-0000-0000-0000-000000000000"})).status_code == 404


async def test_a_room_cannot_be_booked_for_more_guests_than_it_sleeps(api):
    guest = await api.guest()
    s = await api.setup()                       # rooms sleep 2
    await api.book(guest, s["rooms"][0]["id"], guests=3, expect=400)
    assert (await api.book(guest, s["rooms"][0]["id"], guests=2))["guests"] == 2


async def test_overlapping_bookings_are_refused_but_back_to_back_is_fine(api):
    one, two = await api.guest(), await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    await api.book(one, room, start=10, nights=3)
    await api.book(two, room, start=11, nights=1, expect=409)
    await api.book(two, room, start=9, nights=2, expect=409)
    await api.book(two, room, start=13, nights=2)          # starts the day the first stay ends


async def test_guests_only_see_their_own_bookings(api):
    one, two = await api.guest(), await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    mine = await api.book(one, room)
    assert (await api.client.get(f"/v1/bookings/{mine['id']}/", headers=two)).status_code == 404
    assert (await api.client.post(f"/v1/bookings/{mine['id']}/cancel/", headers=two)).status_code == 404
    assert (await api.client.patch(f"/v1/bookings/{mine['id']}/", headers=two, json={"guests": 1})).status_code == 404
    assert (await api.client.get("/v1/bookings/", headers=two)).json()["data"] == []
    assert len((await api.client.get("/v1/bookings/", headers=one)).json()["data"]) == 1


async def test_rescheduling_checks_availability_capacity_and_status(api):
    one, two = await api.guest(), await api.guest()
    s = await api.setup()
    room = s["rooms"][0]["id"]
    mine = await api.book(one, room, start=10, nights=2)
    await api.book(two, room, start=13, nights=2)
    path = f"/v1/bookings/{mine['id']}/"
    assert (await api.client.patch(path, headers=one, json={"check_out": day(14)})).status_code == 409      # runs into the other stay
    assert (await api.client.patch(path, headers=one, json={"check_out": day(13)})).status_code == 200
    assert (await api.client.patch(path, headers=one, json={"guests": 3})).status_code == 400
    assert (await api.client.patch(path, headers=one, json={})).status_code == 400
    assert (await api.client.patch(path, headers=one, json={"check_in": day(14)})).status_code == 400      # not before the existing check-out
    await api.client.post(f"/v1/bookings/{mine['id']}/cancel/", headers=one)
    assert (await api.client.patch(path, headers=one, json={"guests": 1})).status_code == 409             # cancelled


async def test_staff_move_a_booking_through_its_life(api):
    guest = await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    booking = await api.book(guest, room)
    path = f"/v1/bookings/{booking['id']}"
    assert (await api.client.post(f"{path}/check-in/", headers=api.admin)).status_code == 409          # not confirmed yet
    assert (await api.client.post(f"{path}/confirm/", headers=guest)).status_code == 403
    assert (await api.client.post(f"{path}/confirm/", headers=api.admin)).json()["data"]["status"] == "confirmed"
    checked_in = (await api.client.post(f"{path}/check-in/", headers=api.admin)).json()["data"]
    assert checked_in["status"] == "checked_in" and checked_in["check_in_time"] is not None
    assert (await api.client.post(f"{path}/cancel/", headers=guest)).status_code == 409                # too late to cancel
    out = (await api.client.post(f"{path}/check-out/", headers=api.admin)).json()["data"]
    assert out["status"] == "checked_out" and out["check_out_time"] is not None
    assert (await api.client.post(f"{path}/check-out/", headers=api.admin)).status_code == 409


async def test_no_show_frees_the_room(api):
    one, two = await api.guest(), await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    booking = await api.book(one, room, start=10)
    await api.client.post(f"/v1/bookings/{booking['id']}/confirm/", headers=api.admin)
    assert (await api.client.post(f"/v1/bookings/{booking['id']}/no-show/", headers=api.admin)).status_code == 200
    await api.book(two, room, start=10)


async def test_staff_can_book_for_a_guest(api):
    guest_id = await api.user("walkin@example.com")
    room = (await api.setup())["rooms"][0]["id"]
    body = {"room_id": room, "check_in": day(5), "check_out": day(6)}
    assert (await api.client.post(f"/v1/bookings/for/{guest_id}/", headers=await api.guest(), json=body)).status_code == 403
    made = await api.client.post(f"/v1/bookings/for/{guest_id}/", headers=api.admin, json=body)
    assert made.status_code == 201 and made.json()["data"]["user_id"] == guest_id


async def test_staff_listing_filters(api):
    one, two = await api.guest(), await api.guest()
    s = await api.setup(rooms=2)
    a = await api.book(one, s["rooms"][0]["id"], start=5)
    await api.book(two, s["rooms"][1]["id"], start=20)
    await api.client.post(f"/v1/bookings/{a['id']}/confirm/", headers=api.admin)
    everything = (await api.client.get("/v1/bookings/", headers=api.admin)).json()
    assert everything["pagination"]["total"] == 2
    confirmed = (await api.client.get("/v1/bookings/", headers=api.admin, params={"status": "confirmed"})).json()["data"]
    assert [b["id"] for b in confirmed] == [a["id"]]
    late = (await api.client.get("/v1/bookings/", headers=api.admin, params={"check_in_from": day(15)})).json()["data"]
    assert len(late) == 1
    assert (await api.client.get("/v1/bookings/", headers=api.admin, params={"check_in_from": day(9), "check_in_to": day(1)})).status_code == 400


# ===== cancellation =====


async def test_cancelling_early_is_free_and_refunds_everything(api):
    guest = await api.guest()
    s = await api.setup(price=10000, free_cancellation_hours=48)
    booking = await api.book(guest, s["rooms"][0]["id"], start=10, nights=2)
    await pay(api, guest, booking)
    quote = (await api.client.get(f"/v1/bookings/{booking['id']}/cancellation/", headers=guest)).json()["data"]
    assert quote["within_free_window"] is True and quote["fee"] == 0 and quote["refund"] == 20000 and quote["paid"] == 20000

    cancelled = await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)
    data = cancelled.json()["data"]
    assert cancelled.status_code == 200 and data["status"] == "cancelled" and data["cancelled_at"] is not None
    assert data["cancellation_fee"] == 0 and data["refund_amount"] == 20000
    money = await summary(api, guest, booking)
    assert (money["paid"], money["refunded"]) == (0, 20000)


async def test_cancelling_late_keeps_the_hotels_share(api):
    guest = await api.guest()
    s = await api.setup(price=10000, free_cancellation_hours=72, late_cancellation_fee_percent=50)
    booking = await api.book(guest, s["rooms"][0]["id"], start=1, nights=2)         # check-in tomorrow: inside 72h
    await pay(api, guest, booking)
    quote = (await api.client.get(f"/v1/bookings/{booking['id']}/cancellation/", headers=guest)).json()["data"]
    assert quote["within_free_window"] is False and quote["fee"] == 10000 and quote["refund"] == 10000
    data = (await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)).json()["data"]
    assert (data["cancellation_fee"], data["refund_amount"]) == (10000, 10000)
    money = await summary(api, guest, booking)
    assert (money["paid"], money["refunded"]) == (10000, 10000)       # the hotel kept half


async def test_staff_can_waive_the_fee_but_guests_cannot(api):
    guest = await api.guest()
    s = await api.setup(price=10000, rooms=2, free_cancellation_hours=72, late_cancellation_fee_percent=100)
    mine = await api.book(guest, s["rooms"][0]["id"], start=1, nights=1)
    theirs = await api.book(guest, s["rooms"][1]["id"], start=1, nights=1)
    await pay(api, guest, mine)
    await pay(api, guest, theirs)
    by_guest = await api.client.post(f"/v1/bookings/{mine['id']}/cancel/", headers=guest, json={"waive_fee": True})
    assert by_guest.json()["data"]["cancellation_fee"] == 10000            # the flag is ignored for a guest
    by_staff = await api.client.post(f"/v1/bookings/{theirs['id']}/cancel/", headers=api.admin, json={"waive_fee": True})
    assert by_staff.json()["data"]["cancellation_fee"] == 0 and by_staff.json()["data"]["refund_amount"] == 10000


async def test_nothing_paid_means_no_fee(api):
    guest = await api.guest()
    s = await api.setup(free_cancellation_hours=72)
    booking = await api.book(guest, s["rooms"][0]["id"], start=1, nights=1)
    data = (await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)).json()["data"]
    assert (data["cancellation_fee"], data["refund_amount"]) == (0, 0)


async def test_unconfirmed_payments_are_marked_failed_on_cancel(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], start=10, nights=2)
    started = await pay(api, guest, booking, amount=5000, confirm=False)
    await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)
    payment = (await api.client.get(f"/v1/payments/{started['id']}/", headers=guest)).json()["data"]
    assert payment["status"] == "failed"


async def test_refund_is_spread_across_payments_newest_first(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], start=10, nights=2)
    first = await pay(api, guest, booking, amount=12000)
    second = await pay(api, guest, booking, amount=8000)
    await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)
    one = (await api.client.get(f"/v1/payments/{first['id']}/", headers=guest)).json()["data"]
    two = (await api.client.get(f"/v1/payments/{second['id']}/", headers=guest)).json()["data"]
    assert (one["status"], one["refunded_amount"]) == ("refunded", 12000)
    assert (two["status"], two["refunded_amount"]) == ("refunded", 8000)


async def test_cancelled_booking_frees_the_room_and_cannot_be_cancelled_twice(api):
    one, two = await api.guest(), await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    booking = await api.book(one, room, start=10)
    assert (await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=one)).status_code == 200
    assert (await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=one)).status_code == 409
    assert (await api.client.get(f"/v1/bookings/{booking['id']}/cancellation/", headers=one)).status_code == 409
    await api.book(two, room, start=10)


async def test_cancellation_emails_the_guest_with_fee_and_refund(api):
    guest = await api.guest("canceller@example.com")
    s = await api.setup(price=10000, free_cancellation_hours=72, late_cancellation_fee_percent=25)
    booking = await api.book(guest, s["rooms"][0]["id"], start=1, nights=2)
    await pay(api, guest, booking)
    api.sent.clear()
    await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)
    kind, to, context = api.sent[-1]
    assert (kind, to) == ("booking_cancelled", "canceller@example.com")
    assert context["booking_code"] == booking["booking_code"] and context["fee"] == 5000 and context["refund"] == 15000


# ===== payments =====


async def test_pay_in_parts_and_see_the_balance(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], start=10, nights=3)             # 30000
    first = await pay(api, guest, booking, amount=10000, confirm=False)
    assert first["status"] == "pending" and first["amount"] == 10000 and first["currency"] == "NGN"
    assert first["booking_code"] == booking["booking_code"] and first["booking_status"] == "pending"
    money = await summary(api, guest, booking)
    assert (money["total"], money["paid"], money["pending"], money["remaining"]) == (30000, 0, 10000, 20000)

    rest = await pay(api, guest, booking, confirm=False)                               # no amount = what is still owed
    assert rest["amount"] == 20000
    assert (await api.client.post("/v1/payments/", headers=guest, json={"booking_id": booking["id"]})).status_code == 409
    over = await api.client.post("/v1/payments/", headers=guest, json={"booking_id": booking["id"], "amount": 1})
    assert over.status_code == 409


async def test_amount_checks(api):
    guest = await api.guest()
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], nights=2)
    post = lambda amount: api.client.post("/v1/payments/", headers=guest, json={"booking_id": booking["id"], "amount": amount})
    assert (await post(0)).status_code == 422
    assert (await post(-5)).status_code == 422
    assert (await post(20001)).status_code == 400


async def test_payment_confirms_the_booking_only_when_fully_paid(api):
    guest = await api.guest("payer@example.com")
    s = await api.setup(price=10000)
    booking = await api.book(guest, s["rooms"][0]["id"], nights=2)
    api.sent.clear()
    part = await pay(api, guest, booking, amount=5000)
    assert part["booking_status"] == "pending" and not [k for k, *_ in api.sent if k == "booking_confirmation"]
    rest = await pay(api, guest, booking)
    assert rest["booking_status"] == "confirmed"
    assert (await api.client.get(f"/v1/bookings/{booking['id']}/", headers=guest)).json()["data"]["status"] == "confirmed"
    kind, to, context = [m for m in api.sent if m[0] == "booking_confirmation"][-1]
    assert to == "payer@example.com" and context["booking_code"] == booking["booking_code"] and context["total"] == 20000


async def test_staff_confirmation_of_a_booking_emails_the_guest(api):
    guest = await api.guest("g@example.com")
    booking = await api.book(guest, (await api.setup())["rooms"][0]["id"])
    api.sent.clear()
    await api.client.post(f"/v1/bookings/{booking['id']}/confirm/", headers=api.admin)
    assert [(k, to) for k, to, _ in api.sent] == [("booking_confirmation", "g@example.com")]


async def test_only_staff_can_confirm_fail_or_refund(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup())["rooms"][0]["id"])
    payment = await pay(api, guest, booking, confirm=False)
    for action in ("confirm", "fail", "refund"):
        assert (await api.client.post(f"/v1/payments/{payment['id']}/{action}/", headers=guest, json={})).status_code == 403


async def test_status_changes_and_provider_references(api):
    guest = await api.guest()
    s = await api.setup(price=10000, rooms=2)
    a = await api.book(guest, s["rooms"][0]["id"], nights=1)
    b = await api.book(guest, s["rooms"][1]["id"], nights=1)
    p1, p2 = await pay(api, guest, a, confirm=False), await pay(api, guest, b, confirm=False)
    confirm = lambda p, ref: api.client.post(f"/v1/payments/{p['id']}/confirm/", headers=api.admin, json={"reference": ref})
    done = (await confirm(p1, "REF-1")).json()["data"]
    assert done["status"] == "succeeded" and done["reference"] == "REF-1" and done["paid_at"] is not None
    assert (await confirm(p1, "REF-1")).status_code == 409           # already succeeded
    assert (await confirm(p2, "REF-1")).status_code == 409           # the reference is taken
    assert (await api.client.post(f"/v1/payments/{p2['id']}/fail/", headers=api.admin)).json()["data"]["status"] == "failed"
    assert (await api.client.post(f"/v1/payments/{p2['id']}/fail/", headers=api.admin)).status_code == 409
    assert (await api.client.post(f"/v1/payments/{p2['id']}/refund/", headers=api.admin, json={})).status_code == 409   # never succeeded


async def test_failed_payment_frees_the_amount_to_pay_again(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup(price=10000))["rooms"][0]["id"], nights=1)
    first = await pay(api, guest, booking, confirm=False)
    await api.client.post(f"/v1/payments/{first['id']}/fail/", headers=api.admin)
    again = await pay(api, guest, booking, confirm=False)
    assert again["amount"] == 10000


async def test_partial_and_full_refunds(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup(price=10000))["rooms"][0]["id"], nights=2)
    payment = await pay(api, guest, booking)                            # 20000, booking confirmed
    refund = lambda body: api.client.post(f"/v1/payments/{payment['id']}/refund/", headers=api.admin, json=body)
    part = (await refund({"amount": 5000})).json()["data"]
    assert part["status"] == "succeeded" and part["refunded_amount"] == 5000
    assert (await refund({"amount": 15001})).status_code == 400
    money = await summary(api, guest, booking)
    assert (money["paid"], money["refunded"], money["remaining"]) == (15000, 5000, 5000)
    rest = (await refund({})).json()["data"]
    assert rest["status"] == "refunded" and rest["refunded_amount"] == 20000
    assert (await refund({})).status_code == 409
    assert (await summary(api, guest, booking))["remaining"] == 20000


async def test_payment_privacy(api):
    one, two = await api.guest(), await api.guest()
    booking = await api.book(one, (await api.setup())["rooms"][0]["id"])
    payment = await pay(api, one, booking, confirm=False)
    assert (await api.client.post("/v1/payments/", headers=two, json={"booking_id": booking["id"]})).status_code == 404
    assert (await api.client.get(f"/v1/payments/{payment['id']}/", headers=two)).status_code == 404
    assert (await api.client.get(f"/v1/payments/booking/{booking['id']}/summary/", headers=two)).status_code == 404
    assert (await api.client.get("/v1/payments/", headers=two)).json()["data"] == []
    assert (await api.client.post("/v1/payments/", json={"booking_id": booking["id"]})).status_code == 401
    assert len((await api.client.get("/v1/payments/", headers=api.admin)).json()["data"]) == 1


async def test_a_cancelled_booking_cannot_be_paid_for(api):
    guest = await api.guest()
    booking = await api.book(guest, (await api.setup())["rooms"][0]["id"])
    await api.client.post(f"/v1/bookings/{booking['id']}/cancel/", headers=guest)
    assert (await api.client.post("/v1/payments/", headers=guest, json={"booking_id": booking["id"]})).status_code == 409


# ===== reviews =====


async def finished_stay(api, guest, room_id, start=1):
    """A booking taken all the way to check-out, so it can be reviewed."""
    booking = await api.book(guest, room_id, start=start, nights=1)
    for step in ("confirm", "check-in", "check-out"):
        assert (await api.client.post(f"/v1/bookings/{booking['id']}/{step}/", headers=api.admin)).status_code == 200
    return booking


async def test_only_a_finished_stay_can_be_reviewed_once_by_its_guest(api):
    guest, other = await api.guest(), await api.guest()
    room = (await api.setup())["rooms"][0]["id"]
    booking = await api.book(guest, room, start=1, nights=1)
    review = lambda who, rating=5: api.client.post("/v1/reviews/", headers=who, json={"booking_id": booking["id"], "rating": rating})
    assert (await review(guest)).status_code == 400                       # not finished yet
    for step in ("confirm", "check-in", "check-out"):
        await api.client.post(f"/v1/bookings/{booking['id']}/{step}/", headers=api.admin)
    assert (await review(other)).status_code == 404                       # someone else's stay
    assert (await review(guest, rating=9)).status_code == 422
    created = await review(guest, rating=4)
    assert created.status_code == 201 and created.json()["data"]["is_approved"] is False
    assert (await review(guest)).status_code == 409                       # one per booking


async def test_reviews_are_hidden_until_approved(api):
    guest, other = await api.guest(), await api.guest()
    s = await api.setup()
    booking = await finished_stay(api, guest, s["rooms"][0]["id"])
    review = (await api.client.post("/v1/reviews/", headers=guest, json={"booking_id": booking["id"], "rating": 5, "comment": "Lovely"})).json()["data"]
    path = f"/v1/reviews/{review['id']}/"
    public = lambda: api.client.get("/v1/reviews/", params={"hotel_id": s["hotel"]["id"]})
    assert (await public()).json()["data"] == []
    assert (await api.client.get(path)).status_code == 404                # anonymous
    assert (await api.client.get(path, headers=other)).status_code == 404
    assert (await api.client.get(path, headers=guest)).status_code == 200  # the author
    assert (await api.client.get(path, headers=api.admin)).status_code == 200
    assert (await api.client.put(f"{path}approval/", headers=guest, json={"is_approved": True})).status_code == 403
    assert (await api.client.put(f"{path}approval/", headers=api.admin, json={"is_approved": True})).status_code == 200
    assert len((await public()).json()["data"]) == 1
    assert (await api.client.get(path)).status_code == 200
    rating = (await api.client.get(f"/v1/hotels/{s['hotel']['id']}/rating/")).json()["data"]
    assert rating["average"] == 5.0 and rating["count"] == 1


async def test_editing_a_review_sends_it_back_for_approval(api):
    guest, other = await api.guest(), await api.guest()
    booking = await finished_stay(api, guest, (await api.setup())["rooms"][0]["id"])
    review = (await api.client.post("/v1/reviews/", headers=guest, json={"booking_id": booking["id"], "rating": 5})).json()["data"]
    path = f"/v1/reviews/{review['id']}/"
    await api.client.put(f"{path}approval/", headers=api.admin, json={"is_approved": True})
    assert (await api.client.patch(path, headers=other, json={"rating": 1})).status_code == 404
    edited = (await api.client.patch(path, headers=guest, json={"rating": 3})).json()["data"]
    assert edited["rating"] == 3 and edited["is_approved"] is False
    assert (await api.client.patch(path, headers=guest, json={})).status_code == 400
    assert (await api.client.get("/v1/reviews/mine/", headers=guest)).json()["pagination"]["total"] == 1


async def test_staff_review_queue_and_deleting(api):
    guest, other = await api.guest(), await api.guest()
    booking = await finished_stay(api, guest, (await api.setup())["rooms"][0]["id"])
    review = (await api.client.post("/v1/reviews/", headers=guest, json={"booking_id": booking["id"], "rating": 2})).json()["data"]
    queue = (await api.client.get("/v1/reviews/all/", headers=api.admin, params={"is_approved": False})).json()
    assert queue["pagination"]["total"] == 1
    assert (await api.client.get("/v1/reviews/all/", headers=guest)).status_code == 403
    assert (await api.client.delete(f"/v1/reviews/{review['id']}/", headers=other)).status_code == 404
    assert (await api.client.delete(f"/v1/reviews/{review['id']}/", headers=guest)).status_code == 200


async def test_rating_of_a_hotel_with_no_reviews(api):
    hotel = await api.hotel()
    rating = (await api.client.get(f"/v1/hotels/{hotel['id']}/rating/")).json()["data"]
    assert rating["average"] is None and rating["count"] == 0
    assert (await api.client.get("/v1/hotels/00000000-0000-0000-0000-000000000000/rating/")).status_code == 404
