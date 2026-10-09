"""Sign-up and login, user/role/permission management, and the emails the app sends."""
import re
from datetime import date

import pytest

from conftest import PASSWORD
from core.scopes import Scope
from core.utils.messages.email import CATALOG, EmailService, render_email


# ===== auth =====


def link_token(url: str) -> str:
    return re.search(r"token=([^&]+)", url).group(1)


async def register(api, email="ada@example.com"):
    return await api.client.post("/v1/auth/register/", json={
        "email": email, "password": "Strongpass1", "firstname": "Ada", "lastname": "Obi",
    })


async def test_register_creates_an_inactive_customer_and_emails_a_link(api):
    response = await register(api)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["account_status"] == "inactive" and data["role_name"] == "customer"
    assert "passwordhash" not in data
    kind, to, context = api.sent[-1]
    assert (kind, to) == ("verification", "ada@example.com")
    assert "/v1/auth/verify-email/?token=" in context["action_url"]
    assert context["valid_hours"] == 0.25


async def test_weak_and_duplicate_registrations_are_refused(api):
    weak = await api.client.post("/v1/auth/register/", json={"email": "a@example.com", "password": "weak", "firstname": "A", "lastname": "B"})
    assert weak.status_code == 422
    assert (await register(api)).status_code == 201
    assert (await register(api)).status_code == 409


async def test_login_is_blocked_until_the_email_is_verified(api):
    await register(api)
    blocked = await api.client.post("/v1/auth/login/", json={"email": "ada@example.com", "password": "Strongpass1"})
    assert blocked.status_code == 403
    token = link_token(api.sent[-1][2]["action_url"])
    assert (await api.client.get("/v1/auth/verify-email/", params={"token": token})).status_code == 200
    ok = await api.client.post("/v1/auth/login/", json={"email": "ada@example.com", "password": "Strongpass1"})
    assert ok.status_code == 200 and {"access_token", "refresh_token"} <= set(ok.json()["data"])


async def test_wrong_password_and_unknown_email_look_the_same(api):
    await api.user("known@example.com")
    wrong = await api.client.post("/v1/auth/login/", json={"email": "known@example.com", "password": "Nope12345"})
    unknown = await api.client.post("/v1/auth/login/", json={"email": "nobody@example.com", "password": "Nope12345"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["message"] == unknown.json()["message"]


async def test_garbage_verification_token_is_refused(api):
    response = await api.client.get("/v1/auth/verify-email/", params={"token": "not-a-real-token"})
    assert response.status_code == 400


async def test_expired_link_sends_a_fresh_one_but_only_once(api):
    from datetime import timedelta
    from core.utils.encryption import JWTManager
    from services.accounts import user as user_service

    user_service._recent_resends.clear()
    registered = (await register(api)).json()["data"]
    expired = JWTManager().create_verification_token(
        {"sub": registered["id"], "email": "ada@example.com"}, timedelta(seconds=-5)
    )
    emails_before = len(api.sent)

    first = await api.client.get("/v1/auth/verify-email/", params={"token": expired})
    assert first.status_code == 400 and "new one has been sent" in first.json()["message"]
    assert len(api.sent) == emails_before + 1 and api.sent[-1][0] == "verification"

    second = await api.client.get("/v1/auth/verify-email/", params={"token": expired})
    assert second.status_code == 429
    assert len(api.sent) == emails_before + 1          # no second email

    # The fresh link works
    fresh = link_token(api.sent[-1][2]["action_url"])
    assert (await api.client.get("/v1/auth/verify-email/", params={"token": fresh})).status_code == 200


async def test_refresh_gives_new_tokens_and_rejects_an_access_token(api):
    await api.user("r@example.com")
    tokens = (await api.client.post("/v1/auth/login/", json={"email": "r@example.com", "password": PASSWORD})).json()["data"]
    refreshed = await api.client.post("/v1/auth/refresh/", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200 and "access_token" in refreshed.json()["data"]
    wrong_type = await api.client.post("/v1/auth/refresh/", json={"refresh_token": tokens["access_token"]})
    assert wrong_type.status_code == 401


async def test_me_needs_a_valid_token(api):
    assert (await api.client.get("/v1/auth/me/")).status_code == 401
    assert (await api.client.get("/v1/auth/me/", headers={"Authorization": "Bearer nonsense"})).status_code == 401
    headers = await api.guest("me@example.com")
    me = await api.client.get("/v1/auth/me/", headers=headers)
    assert me.status_code == 200 and me.json()["data"]["email"] == "me@example.com"


async def test_password_reset_by_email(api):
    await api.user("forgot@example.com")
    assert (await api.client.post("/v1/auth/forgot-password/", json={"email": "forgot@example.com"})).status_code == 200
    kind, to, context = api.sent[-1]
    assert (kind, to) == ("password_reset", "forgot@example.com")
    assert "/reset-password?token=" in context["action_url"]
    token = link_token(context["action_url"])

    weak = await api.client.post("/v1/auth/reset-password/", json={"token": token, "new_password": "weak"})
    assert weak.status_code == 422
    done = await api.client.post("/v1/auth/reset-password/", json={"token": token, "new_password": "Brandnew123"})
    assert done.status_code == 200
    old = await api.client.post("/v1/auth/login/", json={"email": "forgot@example.com", "password": PASSWORD})
    new = await api.client.post("/v1/auth/login/", json={"email": "forgot@example.com", "password": "Brandnew123"})
    assert old.status_code == 401 and new.status_code == 200


async def test_forgot_password_does_not_reveal_whether_an_email_exists(api):
    known = await api.client.post("/v1/auth/forgot-password/", json={"email": "admin@example.com"})
    before = len(api.sent)
    unknown = await api.client.post("/v1/auth/forgot-password/", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]
    assert len(api.sent) == before                      # nothing sent for the unknown address


async def test_expired_reset_token_is_refused(api):
    from datetime import timedelta
    from core.utils.encryption import JWTManager
    user_id = await api.user("late@example.com")
    token = JWTManager().create_password_reset_token({"sub": user_id}, timedelta(seconds=-5))
    response = await api.client.post("/v1/auth/reset-password/", json={"token": token, "new_password": "Brandnew123"})
    assert response.status_code == 400


async def test_suspended_users_cannot_log_in_or_use_old_tokens(api):
    victim = await api.user("victim@example.com")
    headers = await api.login("victim@example.com")
    assert (await api.client.post(f"/v1/users/{victim}/suspend/", headers=api.admin)).status_code == 200
    login = await api.client.post("/v1/auth/login/", json={"email": "victim@example.com", "password": PASSWORD})
    assert login.status_code == 403
    assert (await api.client.get("/v1/auth/me/", headers=headers)).status_code == 403


# ===== accounts =====


async def test_admin_user_management_needs_permissions(api):
    guest = await api.guest()
    assert (await api.client.get("/v1/users/", headers=guest)).status_code == 403
    assert (await api.client.get("/v1/users/")).status_code == 401
    listed = await api.client.get("/v1/users/", headers=api.admin)
    assert listed.status_code == 200 and listed.json()["pagination"]["total"] == 2
    assert all("passwordhash" not in u for u in listed.json()["data"])


async def test_user_list_filters(api):
    await api.user("zed@example.com")
    by_role = (await api.client.get("/v1/users/", headers=api.admin, params={"role": "superadmin"})).json()["data"]
    assert [u["email"] for u in by_role] == ["admin@example.com"]
    found = (await api.client.get("/v1/users/", headers=api.admin, params={"search": "zed"})).json()["data"]
    assert [u["email"] for u in found] == ["zed@example.com"]
    assert (await api.client.get("/v1/users/", headers=api.admin, params={"status": "bogus"})).status_code == 422
    assert (await api.client.get("/v1/users/", headers=api.admin, params={"limit": 500})).status_code == 422


async def test_admin_creates_a_user_and_the_email_must_be_unique(api):
    body = {"email": "made@example.com", "password": "Strongpass1", "firstname": "Made", "lastname": "Up", "role_name": "customer"}
    created = await api.client.post("/v1/users/", headers=api.admin, json=body)
    assert created.status_code == 201 and created.json()["data"]["role_name"] == "customer"
    assert (await api.client.post("/v1/users/", headers=api.admin, json=body)).status_code == 409
    assert (await api.client.post("/v1/users/", headers=api.admin, json={**body, "email": "x@example.com", "role_name": "nope"})).status_code == 400
    assert (await api.client.post("/v1/users/", headers=api.admin, json={**body, "email": "y@example.com", "role_name": None})).status_code == 400


async def test_users_read_and_edit_themselves_but_not_others(api):
    me_id = await api.user("me@example.com")
    other_id = await api.user("other@example.com")
    me = await api.login("me@example.com")
    assert (await api.client.get(f"/v1/users/{me_id}/", headers=me)).status_code == 200
    assert (await api.client.get(f"/v1/users/{other_id}/", headers=me)).status_code == 403
    edited = await api.client.patch(f"/v1/users/{me_id}/", headers=me, json={"phone": "+2348000000000", "firstname": "  NEW "})
    assert edited.status_code == 200 and edited.json()["data"]["phone"] == "+2348000000000" and edited.json()["data"]["firstname"] == "new"
    assert (await api.client.patch(f"/v1/users/{other_id}/", headers=me, json={"phone": "1"})).status_code == 403
    assert (await api.client.patch(f"/v1/users/{me_id}/", headers=me, json={})).status_code == 400
    assert (await api.client.patch(f"/v1/users/{me_id}/", headers=me, json={"firstname": None})).status_code == 400


async def test_each_sensitive_action_has_its_own_permission(api):
    target = await api.user("target@example.com")
    only_deactivate = await api.staff([Scope.USERS_DEACTIVATE])
    assert (await api.client.post(f"/v1/users/{target}/deactivate/", headers=only_deactivate)).status_code == 200
    for action in ("suspend", "unsuspend", "activate", "reset-password"):
        assert (await api.client.post(f"/v1/users/{target}/{action}/", headers=only_deactivate)).status_code == 403, action
    assert (await api.client.patch(f"/v1/users/{target}/", headers=only_deactivate, json={"phone": "1"})).status_code == 403
    assert (await api.client.put(f"/v1/users/{target}/role/", headers=only_deactivate, json={"role_id": "00000000-0000-0000-0000-000000000000"})).status_code == 403


async def test_suspend_and_unsuspend(api):
    target = await api.user("sus@example.com")
    assert (await api.client.post(f"/v1/users/{target}/suspend/", headers=api.admin)).status_code == 200
    assert (await api.client.post(f"/v1/users/{target}/activate/", headers=api.admin)).status_code == 403        # not by activate
    assert (await api.client.post(f"/v1/users/{target}/deactivate/", headers=api.admin)).status_code == 403
    assert (await api.client.post(f"/v1/users/{target}/unsuspend/", headers=api.admin)).status_code == 200
    assert (await api.client.post(f"/v1/users/{target}/unsuspend/", headers=api.admin)).status_code == 400     # not suspended
    assert (await api.client.post("/v1/auth/login/", json={"email": "sus@example.com", "password": PASSWORD})).status_code == 200


async def test_admins_cannot_suspend_deactivate_or_re_role_themselves(api):
    me = (await api.client.get("/v1/auth/me/", headers=api.admin)).json()["data"]
    for action in ("suspend", "deactivate"):
        assert (await api.client.post(f"/v1/users/{me['id']}/{action}/", headers=api.admin)).status_code == 400
    assert (await api.client.put(f"/v1/users/{me['id']}/role/", headers=api.admin, json={"role_id": me["role_id"]})).status_code == 400


async def test_changing_a_role_and_an_email(api):
    target = await api.user("changing@example.com")
    role = (await api.client.post("/v1/roles/", headers=api.admin, json={"name": "front_desk"})).json()["data"]
    changed = await api.client.put(f"/v1/users/{target}/role/", headers=api.admin, json={"role_id": role["id"]})
    assert changed.status_code == 200 and changed.json()["data"]["role_name"] == "front_desk"
    assert (await api.client.put(f"/v1/users/{target}/role/", headers=api.admin, json={"role_id": "00000000-0000-0000-0000-000000000000"})).status_code == 404

    api.sent.clear()
    moved = await api.client.put(f"/v1/users/{target}/email/", headers=api.admin, json={"new_email": "new-address@example.com"})
    data = moved.json()["data"]
    assert moved.status_code == 200 and data["email"] == "new-address@example.com" and data["account_status"] == "inactive"
    assert [(k, to) for k, to, _ in api.sent] == [("verification", "new-address@example.com")]
    taken = await api.client.put(f"/v1/users/{target}/email/", headers=api.admin, json={"new_email": "admin@example.com"})
    assert taken.status_code == 409


async def test_roles_crud_and_protection(api):
    created = await api.client.post("/v1/roles/", headers=api.admin, json={"name": "Night_Auditor", "description": "Works nights"})
    role = created.json()["data"]
    assert created.status_code == 201 and role["name"] == "night_auditor"
    assert (await api.client.post("/v1/roles/", headers=api.admin, json={"name": "night_auditor"})).status_code == 409
    assert (await api.client.post("/v1/roles/", headers=api.admin, json={"name": "bad name!"})).status_code == 422
    assert (await api.client.patch(f"/v1/roles/{role['id']}/", headers=api.admin, json={"description": "Nights"})).status_code == 200
    assert (await api.client.patch(f"/v1/roles/{role['id']}/", headers=api.admin, json={})).status_code == 400
    assert (await api.client.get("/v1/roles/", headers=await api.guest())).status_code == 403
    user = await api.user("holder@example.com", role="night_auditor")
    assert (await api.client.delete(f"/v1/roles/{role['id']}/", headers=api.admin)).status_code == 409      # still held
    await api.client.put(f"/v1/users/{user}/role/", headers=api.admin, json={"role_id": (await api.client.get("/v1/roles/", headers=api.admin, params={"search": "customer"})).json()["data"][0]["id"]})
    assert (await api.client.delete(f"/v1/roles/{role['id']}/", headers=api.admin)).status_code == 200


async def test_granting_permissions_to_a_role(api):
    role = (await api.client.post("/v1/roles/", headers=api.admin, json={"name": "greeter"})).json()["data"]
    perms = {p["name"]: p["id"] for p in (await api.client.get("/v1/permissions/", headers=api.admin, params={"resource": "bookings"})).json()["data"]}
    path = f"/v1/roles/{role['id']}/permissions/"
    granted = await api.client.post(f"{path}{perms['bookings:view']}/", headers=api.admin)
    assert granted.status_code == 200 and [p["name"] for p in granted.json()["data"]] == ["bookings:view"]
    assert (await api.client.post(f"{path}{perms['bookings:view']}/", headers=api.admin)).status_code == 200        # harmless twice
    replaced = await api.client.put(path, headers=api.admin, json={"permission_ids": [perms["bookings:confirm"], perms["bookings:cancel"]]})
    assert sorted(p["name"] for p in replaced.json()["data"]) == ["bookings:cancel", "bookings:confirm"]
    assert (await api.client.put(path, headers=api.admin, json={"permission_ids": ["00000000-0000-0000-0000-000000000000"]})).status_code == 400
    assert (await api.client.delete(f"{path}{perms['bookings:cancel']}/", headers=api.admin)).status_code == 200
    assert (await api.client.delete(f"{path}{perms['bookings:cancel']}/", headers=api.admin)).status_code == 404
    editor = await api.staff([Scope.ROLES_EDIT])
    assert (await api.client.put(path, headers=editor, json={"permission_ids": []})).status_code == 403     # editing a role is not granting


async def test_permissions_crud(api):
    created = await api.client.post("/v1/permissions/", headers=api.admin, json={"name": "spa:book", "description": "Book the spa"})
    permission = created.json()["data"]
    assert created.status_code == 201
    assert (await api.client.post("/v1/permissions/", headers=api.admin, json={"name": "spa:book"})).status_code == 409
    assert (await api.client.post("/v1/permissions/", headers=api.admin, json={"name": "nonsense"})).status_code == 422
    assert (await api.client.patch(f"/v1/permissions/{permission['id']}/", headers=api.admin, json={"description": "Spa"})).json()["data"]["description"] == "Spa"
    assert (await api.client.delete(f"/v1/permissions/{permission['id']}/", headers=api.admin)).status_code == 200
    assert (await api.client.get(f"/v1/permissions/{permission['id']}/", headers=api.admin)).status_code == 404


async def test_every_scope_in_the_code_exists_as_a_permission(api):
    names = set()
    page = 1
    while True:
        data = (await api.client.get("/v1/permissions/", headers=api.admin)).json()["data"]
        names = {p["name"] for p in data}
        break
    assert names == {scope.value for scope in Scope}


# ===== emails =====


BOOKING = {
    "name": "Ada", "booking_code": "PH-K1G2N9", "hotel_name": "Grand Harbour", "room_label": "101 (Deluxe King)",
    "check_in": date(2030, 1, 5), "check_out": date(2030, 1, 8), "nights": 3, "guests": 2, "total": 7500000,
    "currency": "NGN", "action_url": "http://app/bookings/1", "subject_values": {"code": "PH-K1G2N9"},
}


def test_verification_and_reset_emails_render_in_every_language():
    for language in CATALOG:
        for kind in ("verification", "password_reset"):
            subject, html = render_email(kind, {"name": "Ada", "action_url": "http://x/y?token=abc", "valid_hours": 0.25}, language)
            assert subject and "http://x/y?token=abc" in html and "Pafare" in html


def test_booking_emails_render_with_money_in_the_hotels_currency():
    subject, html = render_email("booking_confirmation", BOOKING, "en")
    assert subject == "Booking PH-K1G2N9 confirmed"
    assert "Grand Harbour" in html and "101 (Deluxe King)" in html and "NGN75,000.00" in html
    subject, html = render_email("booking_cancelled", {**BOOKING, "fee": 2500000, "refund": 5000000}, "en")
    assert "cancelled" in subject and "NGN25,000.00" in html and "NGN50,000.00" in html and "Refunds go back" in html
    _, no_refund = render_email("booking_cancelled", {**BOOKING, "fee": 7500000, "refund": 0}, "en")
    assert "Refunds go back" not in no_refund


def test_untranslated_text_falls_back_to_english():
    subject, html = render_email("booking_confirmation", BOOKING, "fr")
    assert "confirmed" in subject and "Book your stay with Pafare." in html


def test_no_leftover_grocery_wording():
    english = str(CATALOG["en"])
    assert "grocer" not in english.lower() and "shopping" not in english.lower()


async def test_without_a_mail_key_the_email_is_only_logged(monkeypatch, caplog):
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(args)

    monkeypatch.setattr("core.utils.messages.email.send_email", fake_send)
    monkeypatch.setattr("core.utils.messages.email.settings.BREVO_API_KEY", "")
    await EmailService.deliver("a@example.com", "verification", {"action_url": "http://link"}, "en")
    assert sent == []


async def test_with_a_mail_key_it_is_sent_and_a_failure_never_raises(monkeypatch):
    calls = []

    async def fake_send(to, kind, context, language):
        calls.append((to, kind))

    monkeypatch.setattr("core.utils.messages.email.settings.BREVO_API_KEY", "key")
    monkeypatch.setattr("core.utils.messages.email.send_email", fake_send)
    await EmailService.deliver("a@example.com", "verification", {"action_url": "http://link"}, "en")
    assert calls == [("a@example.com", "verification")]

    async def failing_send(*args, **kwargs):
        raise RuntimeError("Brevo is down")

    monkeypatch.setattr("core.utils.messages.email.send_email", failing_send)
    await EmailService.deliver("a@example.com", "verification", {"action_url": "http://link"}, "en")    # does not raise
