import re
import time

import pytest

from tests.fakes import FakeSupabase, SECRET_KEY_VALUE
from webapp import create_app, password_problem, safe_next, valid_email

GOOD_PW = "correct horse battery"
CONSENT = {"confirmed_18_plus": True, "accepted_terms_at": "2026-10-24T09:00:00+00:00"}


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def app(fake):
    return create_app(sb=fake, testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/login"):
    client.get(path)
    with client.session_transaction() as s:
        return s["csrf"]


def post(client, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault("csrf", csrf(client))
    return client.post(url, data=data, **kw)


def login(client, email, pw=GOOD_PW):
    return post(client, "/login", {"email": email, "password": pw})


def make_user(fake, email="a@example.com", meta=None):
    fake.add_user(email, GOOD_PW, meta=CONSENT if meta is None else meta)
    return email


# ---------------- small helpers ----------------
def test_helpers():
    assert valid_email("a@b.co") and not valid_email("nope") and not valid_email("a b@c.d")
    assert password_problem("short") and password_problem("a" * 200) and password_problem("me@x.com", "me@x.com")
    assert password_problem("a long enough pass") is None
    assert safe_next("/trips", "/x") == "/trips"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "", None):
        assert safe_next(bad, "/x") == "/x"


# ---------------- sign-up ----------------
@pytest.mark.parametrize("changes, text", [
    ({"email": "not-an-email"}, "valid email"),
    ({"password": "short", "password2": "short"}, "at least 10"),
    ({"password2": "different password!"}, "don't match"),
    ({"age18": ""}, "18 and over"),
    ({"terms": ""}, "privacy notice"),
])
def test_signup_rejects_bad_input(client, fake, changes, text):
    form = {"email": "new@example.com", "password": GOOD_PW, "password2": GOOD_PW, "age18": "on", "terms": "on"}
    form.update(changes)
    r = post(client, "/signup", form)
    assert r.status_code == 400 and text in r.get_data(as_text=True).replace('&#39;', "'")
    assert fake.signup_calls == []


def test_signup_records_consent_and_asks_to_check_email(client, fake):
    r = post(client, "/signup", {"email": "New@Example.com", "password": GOOD_PW, "password2": GOOD_PW, "age18": "on", "terms": "on"})
    assert r.status_code == 200 and "Check your email" in r.get_data(as_text=True).replace('&#39;', "'")
    call = fake.signup_calls[0]
    assert call["email"] == "new@example.com"
    assert call["metadata"]["confirmed_18_plus"] is True and call["metadata"]["accepted_terms_at"]
    assert call["redirect_to"].endswith("/auth/callback")


def test_signup_does_not_reveal_existing_accounts(client, fake):
    make_user(fake, "taken@example.com")
    form = {"email": "taken@example.com", "password": GOOD_PW, "password2": GOOD_PW, "age18": "on", "terms": "on"}
    r1 = post(client, "/signup", form)
    form["email"] = "fresh@example.com"
    r2 = post(client, "/signup", form)
    assert r1.status_code == r2.status_code == 200
    assert "Check your email" in r1.get_data(as_text=True).replace('&#39;', "'")


# ---------------- login / logout ----------------
def test_login_wrong_password_is_generic(client, fake):
    make_user(fake)
    r = login(client, "a@example.com", "wrong password here")
    assert r.status_code == 401 and "don't match" in r.get_data(as_text=True).replace('&#39;', "'")
    r2 = login(client, "nobody@example.com")
    assert r2.status_code == 401 and "don't match" in r2.get_data(as_text=True).replace('&#39;', "'")


def test_unconfirmed_email_is_told_to_confirm(client, fake):
    fake.add_user("u@example.com", GOOD_PW, confirmed=False, meta=CONSENT)
    assert "confirm your email" in login(client, "u@example.com").get_data(as_text=True).replace('&#39;', "'").lower()


def test_login_creates_consent_profile_from_signup_data(client, fake):
    make_user(fake)
    r = login(client, "a@example.com")
    assert r.status_code == 302 and r.headers["Location"].endswith("/trips")
    assert len(fake.tables["profiles"]) == 1 and fake.tables["profiles"][0]["confirmed_18_plus"] is True


def test_user_without_consent_data_must_confirm_first(client, fake):
    make_user(fake, meta={})
    r = login(client, "a@example.com")
    assert r.headers["Location"].endswith("/consent")
    assert client.get("/consent").status_code == 200
    assert post(client, "/consent", {"age18": "on"}).status_code == 400
    r = post(client, "/consent", {"age18": "on", "terms": "on"})
    assert r.status_code == 302 and len(fake.tables["profiles"]) == 1


def test_login_blocks_open_redirects(client, fake):
    make_user(fake)
    r = post(client, "/login", {"email": "a@example.com", "password": GOOD_PW, "next": "//evil.com"})
    assert r.headers["Location"].endswith("/trips")


def test_login_goes_back_to_the_page_you_wanted(client, fake):
    make_user(fake)
    r = post(client, "/login", {"email": "a@example.com", "password": GOOD_PW, "next": "/account/password"})
    assert r.headers["Location"].endswith("/account/password")


def test_logout_clears_session_and_requires_csrf(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    assert client.post("/logout").status_code == 400               # no token: refused
    assert client.get("/trips").status_code == 200                 # still logged in
    post(client, "/logout", {"csrf": csrf(client, "/trips")})
    assert client.get("/trips").status_code == 302


# ---------------- protection of pages ----------------
def test_trips_requires_login(client):
    r = client.get("/trips")
    assert r.status_code == 302 and "/login?next=/trips" in r.headers["Location"]


def test_post_without_csrf_token_is_refused(client, fake):
    make_user(fake)
    assert client.post("/login", data={"email": "a@example.com", "password": GOOD_PW}).status_code == 400
    assert client.post("/login", data={"email": "a@example.com", "password": GOOD_PW, "csrf": "forged"}).status_code == 400


def test_security_headers_and_cookie_flags(client):
    r = client.get("/login")
    assert r.headers["Cache-Control"] == "private, no-store" and "noindex" in r.headers["X-Robots-Tag"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "HttpOnly" in r.headers.get("Set-Cookie", "")


# ---------------- trips and privacy between users ----------------
def test_trip_lifecycle(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    post(client, "/trips", {"name": "  Hostel   to home "})
    html = client.get("/trips").get_data(as_text=True).replace('&#39;', "'")
    assert "Hostel to home" in html
    tid = fake.tables["trips"][0]["id"]
    post(client, f"/trips/{tid}/archive", {"csrf": csrf(client, "/trips")})
    assert "Archived" in client.get("/trips").get_data(as_text=True).replace('&#39;', "'")
    post(client, f"/trips/{tid}/restore", {"csrf": csrf(client, "/trips")})
    assert fake.tables["trips"][0]["archived_at"] is None
    post(client, f"/trips/{tid}/delete", {"csrf": csrf(client, "/trips")})          # box not ticked: nothing happens
    assert len(fake.tables["trips"]) == 1
    post(client, f"/trips/{tid}/delete", {"csrf": csrf(client, "/trips"), "sure": "on"})
    assert fake.tables["trips"] == []


def test_trip_name_rules(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    post(client, "/trips", {"name": "   "})
    post(client, "/trips", {"name": "x" * 81})
    assert fake.tables["trips"] == []


def test_users_cannot_see_or_change_each_others_trips(client, app, fake):
    make_user(fake, "a@example.com")
    make_user(fake, "b@example.com")
    login(client, "a@example.com")
    post(client, "/trips", {"name": "Alice private move"})
    tid = fake.tables["trips"][0]["id"]
    other = app.test_client()
    login(other, "b@example.com")
    assert "Alice private move" not in other.get("/trips").get_data(as_text=True).replace('&#39;', "'")
    post(other, f"/trips/{tid}/archive", {"csrf": csrf(other, "/trips")})
    post(other, f"/trips/{tid}/delete", {"csrf": csrf(other, "/trips"), "sure": "on"})
    assert len(fake.tables["trips"]) == 1 and fake.tables["trips"][0]["archived_at"] is None


def test_app_only_ever_uses_the_users_own_token_never_the_secret_key(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    post(client, "/trips", {"name": "Trip"})
    client.get("/trips")
    assert fake.tokens_used and all(t.startswith("at-") for t in fake.tokens_used)
    assert SECRET_KEY_VALUE not in fake.tokens_used


def test_bad_trip_id_is_404_not_an_error(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    assert post(client, "/trips/not-a-uuid/archive", {"csrf": csrf(client, "/trips")}).status_code == 404


# ---------------- password reset and email links ----------------
def test_forgot_gives_the_same_answer_for_any_email(client, fake):
    make_user(fake, "real@example.com")
    r1 = post(client, "/forgot", {"email": "real@example.com"})
    r2 = post(client, "/forgot", {"email": "ghost@example.com"})
    assert r1.status_code == r2.status_code == 200
    assert "If" in r1.get_data(as_text=True).replace('&#39;', "'") and "If" in r2.get_data(as_text=True).replace('&#39;', "'")
    assert post(client, "/forgot", {"email": "not-an-email"}).status_code == 200
    assert "not-an-email" not in fake.recover_calls


def test_recovery_link_leads_to_choose_new_password(client, fake):
    make_user(fake)
    tokens = fake._issue(fake.users["a@example.com"]["id"], "a@example.com")
    r = post(client, "/auth/session", {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "type": "recovery"})
    assert r.headers["Location"].endswith("/account/password")
    assert post(client, "/account/password", {"password": "short", "password2": "short"}).status_code == 400
    r = post(client, "/account/password", {"password": "a brand new password", "password2": "a brand new password"})
    assert r.status_code == 302 and fake.passwords_set == ["a brand new password"]


def test_confirmation_link_logs_in(client, fake):
    make_user(fake)
    tokens = fake._issue(fake.users["a@example.com"]["id"], "a@example.com")
    r = post(client, "/auth/session", {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "type": "signup"})
    assert r.headers["Location"].endswith("/trips") and len(fake.tables["profiles"]) == 1


def test_expired_or_fake_links_are_handled_kindly(client, fake):
    r = post(client, "/auth/session", {"error_code": "otp_expired"}, follow_redirects=True)
    assert "expired" in r.get_data(as_text=True).replace('&#39;', "'")
    r = post(client, "/auth/session", {"access_token": "forged-token", "type": "recovery"}, follow_redirects=True)
    assert "isn't valid" in r.get_data(as_text=True).replace('&#39;', "'")
    assert client.get("/trips").status_code == 302                  # forged token did not log anyone in


def test_callback_page_hands_the_fragment_to_the_server(client):
    html = client.get("/auth/callback").get_data(as_text=True).replace('&#39;', "'")
    assert "location.hash" in html and 'action="/auth/session"' in html


# ---------------- sessions ----------------
def test_expired_access_token_is_refreshed(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    with client.session_transaction() as s:
        old = s["at"]
        s["exp"] = int(time.time()) - 5
    assert client.get("/trips").status_code == 200
    with client.session_transaction() as s:
        assert s["at"] != old


def test_failed_refresh_logs_the_user_out(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    fake.fail_refresh = True
    with client.session_transaction() as s:
        s["exp"] = int(time.time()) - 5
    assert client.get("/trips").status_code == 302


def test_public_pages_work(client):
    for path in ("/", "/privacy", "/signup", "/login", "/forgot"):
        assert client.get(path).status_code == 200
    assert "Draft" in client.get("/privacy").get_data(as_text=True).replace('&#39;', "'")
