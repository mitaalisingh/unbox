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
    from tests.fake_embed import vec
    return create_app(sb=fake, testing=True, embed_doc=vec, embed_query=vec)


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


# ================= boxes, items and photos =================
from tests.test_images import photo_with_gps   # noqa: E402
from PIL import Image                          # noqa: E402
import io                                      # noqa: E402


def logged_in_trip(client, fake, email="a@example.com", name="Move"):
    make_user(fake, email)
    login(client, email)
    post(client, "/trips", {"name": name})
    return [t for t in fake.tables["trips"] if t["name"] == name][-1]["id"]


def add(client, tid, data, photo=None, parent=""):
    form = {"csrf": csrf(client, f"/trips/{tid}"), "parent_id": parent, "kind": "item"}
    form.update(data)
    if photo is not None:
        form["photo"] = (io.BytesIO(photo), "pic.jpg")
    return client.post(f"/trips/{tid}/add", data=form, content_type="multipart/form-data")


def test_add_box_then_item_inside_and_totals_roll_up(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Kitchen", "kind": "box"})
    box = fake.tables["nodes"][0]
    add(client, tid, {"name": "Mug", "weight_kg": "0.4"}, parent=box["id"])
    add(client, tid, {"name": "Pan", "weight_kg": "1,1"}, parent=box["id"])         # a comma decimal is accepted
    page = client.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Kitchen" in page and "2 items" in page and "1.5 kg known" in page
    box_page = client.get(f"/trips/{tid}/n/{box['id']}").get_data(as_text=True)
    assert "Mug" in box_page and "Pan" in box_page and "2 items" in box_page


def test_nesting_boxes_in_boxes(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Big", "kind": "box"})
    big = fake.tables["nodes"][0]["id"]
    add(client, tid, {"name": "Small", "kind": "box"}, parent=big)
    small = fake.tables["nodes"][1]["id"]
    add(client, tid, {"name": "Spoon"}, parent=small)
    page = client.get(f"/trips/{tid}/n/{small}").get_data(as_text=True)
    assert "Big" in page and "Spoon" in page                                           # breadcrumb shows the parent box
    assert "1 item" in client.get(f"/trips/{tid}/n/{big}").get_data(as_text=True)      # counts include nested boxes


def test_photo_is_cleaned_stored_privately_and_shown_by_short_lived_link(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    node = fake.tables["nodes"][0]
    assert node["photo_path"] == f"{tid}/{node['id']}.jpg"
    stored = fake.storage[node["photo_path"]]
    img = Image.open(io.BytesIO(stored))
    assert dict(img.getexif()) == {} and b"SecretPhoneMaker" not in stored and b"Exif" not in stored    # location is gone
    assert max(img.size) <= 1400
    page = client.get(f"/trips/{tid}").get_data(as_text=True)
    assert f"https://files.example/{node['photo_path']}?token=short-lived" in page


def test_bad_uploads_are_refused_and_nothing_is_saved(client, fake):
    tid = logged_in_trip(client, fake)
    r = add(client, tid, {"name": "Fake"}, photo=b"<html>not a photo</html>")
    assert r.status_code == 302
    assert fake.tables["nodes"] == [] and fake.storage == {}


@pytest.mark.parametrize("data", [{"name": ""}, {"name": "x" * 81}, {"name": "ok", "weight_kg": "abc"}, {"name": "ok", "weight_kg": "-3"},
                                  {"name": "ok", "weight_kg": "5000"}, {"name": "ok", "tags": "t" * 201}, {"name": "ok", "description": "d" * 301}])
def test_item_field_validation(client, fake, data):
    tid = logged_in_trip(client, fake)
    add(client, tid, data)
    assert fake.tables["nodes"] == []


def test_items_cannot_go_inside_an_item_or_a_foreign_node(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Mug"})
    mug = fake.tables["nodes"][0]["id"]
    add(client, tid, {"name": "Inside a mug?"}, parent=mug)
    assert len(fake.tables["nodes"]) == 1


def test_failed_save_removes_the_uploaded_photo(client, fake, monkeypatch):
    tid = logged_in_trip(client, fake)
    def boom(*a, **k):
        from sbclient import SupabaseError
        raise SupabaseError("db down", 500)
    monkeypatch.setattr(fake, "insert", boom)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    assert fake.storage == {} and len(fake.uploads) == 1 and len(fake.removed) == 1


def test_another_user_cannot_view_add_to_or_delete_in_my_trip(client, app, fake):
    tid = logged_in_trip(client, fake, "a@example.com")
    add(client, tid, {"name": "Secret box", "kind": "box"}, photo=photo_with_gps())
    nid = fake.tables["nodes"][0]["id"]
    other = app.test_client()
    make_user(fake, "b@example.com")
    login(other, "b@example.com")
    assert other.get(f"/trips/{tid}").status_code == 404
    assert other.get(f"/trips/{tid}/n/{nid}").status_code == 404
    assert add(other, tid, {"name": "Intruder"}).status_code == 404
    other_csrf = csrf(other, "/trips")
    assert other.post(f"/trips/{tid}/n/{nid}/delete", data={"csrf": other_csrf}).status_code == 404
    assert other.post(f"/trips/{tid}/delete", data={"csrf": other_csrf, "sure": "on"}).status_code == 404
    assert len(fake.tables["nodes"]) == 1 and len(fake.storage) == 1 and len(fake.tables["trips"]) == 1


def test_deleting_a_box_deletes_everything_inside_and_all_their_photos(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Big", "kind": "box"}, photo=photo_with_gps())
    big = fake.tables["nodes"][0]["id"]
    add(client, tid, {"name": "Small", "kind": "box"}, photo=photo_with_gps(), parent=big)
    small = fake.tables["nodes"][1]["id"]
    add(client, tid, {"name": "Spoon"}, photo=photo_with_gps(), parent=small)
    add(client, tid, {"name": "Keeper"}, photo=photo_with_gps())                        # outside the box: must survive
    assert len(fake.storage) == 4
    r = post(client, f"/trips/{tid}/n/{big}/delete", {"csrf": csrf(client, f"/trips/{tid}")})
    assert r.status_code == 302
    assert [n["name"] for n in fake.tables["nodes"]] == ["Keeper"]
    assert len(fake.storage) == 1 and list(fake.storage)[0].endswith(f"{fake.tables['nodes'][0]['id']}.jpg")


def test_if_file_deletion_fails_nothing_is_deleted(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    nid = fake.tables["nodes"][0]["id"]
    fake.fail_remove = True
    post(client, f"/trips/{tid}/n/{nid}/delete", {"csrf": csrf(client, f"/trips/{tid}")})
    assert len(fake.tables["nodes"]) == 1 and len(fake.storage) == 1               # no half-deleted state


def test_deleting_a_trip_deletes_all_its_photos(client, fake):
    tid = logged_in_trip(client, fake, name="First")
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    add(client, tid, {"name": "Mug"}, photo=photo_with_gps())
    post(client, "/trips", {"name": "Second"})
    tid2 = [t["id"] for t in fake.tables["trips"] if t["name"] == "Second"][0]
    add(client, tid2, {"name": "Other"}, photo=photo_with_gps())
    assert len(fake.storage) == 3
    post(client, f"/trips/{tid}/delete", {"csrf": csrf(client, "/trips"), "sure": "on"})
    assert [t["name"] for t in fake.tables["trips"]] == ["Second"]
    assert len(fake.storage) == 1 and all(p.startswith(tid2) for p in fake.storage)


def test_failed_photo_cleanup_keeps_the_trip(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    fake.fail_remove = True
    post(client, f"/trips/{tid}/delete", {"csrf": csrf(client, "/trips"), "sure": "on"})
    assert len(fake.tables["trips"]) == 1 and len(fake.storage) == 1


def test_trip_pages_need_login(client):
    for path in ("/trips/00000000-0000-0000-0000-000000000000", "/trips/00000000-0000-0000-0000-000000000000/n/00000000-0000-0000-0000-000000000001"):
        assert client.get(path).status_code == 302
    assert client.post("/trips/00000000-0000-0000-0000-000000000000/add", data={}).status_code == 400   # no CSRF token


# ================= search inside a trip =================
def names(html):
    import re as _re
    return _re.findall(r"<strong>(.*?)</strong>", html)


def stocked_trip(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Kitchen", "kind": "box"})
    kitchen = fake.tables["nodes"][0]["id"]
    add(client, tid, {"name": "Travel mug", "tags": "steel"}, parent=kitchen)
    add(client, tid, {"name": "Desk lamp", "tags": "reading light"})
    add(client, tid, {"name": "HDMI cable"})
    add(client, tid, {"name": "Blue blanket"})
    return tid


def test_new_items_get_search_numbers_saved(client, fake):
    stocked_trip(client, fake)
    assert all(n.get("embedding", "").startswith("[") for n in fake.tables["nodes"])


def test_search_finds_by_meaning_not_just_exact_words(client, fake):
    tid = stocked_trip(client, fake)
    html = client.get(f"/trips/{tid}/search?q=something+to+drink+from").get_data(as_text=True)
    assert "Travel mug" in names(html)                      # "drink" and "mug" are different words with the same meaning
    assert "Desk lamp" not in names(html) and "HDMI cable" not in names(html)


def test_search_by_exact_word_and_typo(client, fake):
    tid = stocked_trip(client, fake)
    assert "Desk lamp" in names(client.get(f"/trips/{tid}/search?q=lamp").get_data(as_text=True))
    assert "Blue blanket" in names(client.get(f"/trips/{tid}/search?q=blankt").get_data(as_text=True))     # typo


def test_search_shows_where_the_item_is(client, fake):
    tid = stocked_trip(client, fake)
    html = client.get(f"/trips/{tid}/search?q=mug").get_data(as_text=True)
    assert "Kitchen" in html                                 # its box, so you know where to look


def test_search_with_nothing_found_or_nothing_typed(client, fake):
    tid = stocked_trip(client, fake)
    assert "Nothing found" in client.get(f"/trips/{tid}/search?q=zebra").get_data(as_text=True)
    assert "doesn't have to be the exact word" in client.get(f"/trips/{tid}/search").get_data(as_text=True)
    assert client.get(f"/trips/{tid}/search?q=" + "x" * 500).status_code == 200


def test_search_only_covers_my_own_trip_and_other_users_get_nothing(client, app, fake):
    tid = stocked_trip(client, fake)
    other = app.test_client()
    make_user(fake, "b@example.com")
    login(other, "b@example.com")
    assert other.get(f"/trips/{tid}/search?q=mug").status_code == 404
    post(other, "/trips", {"name": "Bs trip"})
    tid_b = [t["id"] for t in fake.tables["trips"] if t["name"] == "Bs trip"][0]
    html = other.get(f"/trips/{tid_b}/search?q=mug").get_data(as_text=True)
    assert "Travel mug" not in html                          # nothing of A's leaks into B's search


def test_search_needs_login(client):
    assert client.get("/trips/00000000-0000-0000-0000-000000000000/search?q=mug").status_code == 302


def test_search_still_works_by_words_when_the_model_is_missing(fake):
    app = create_app(sb=fake, testing=True, embed_doc=lambda t: None, embed_query=lambda t: None)
    c = app.test_client()
    tid = stocked_trip(c, fake)
    assert not any(n.get("embedding") for n in fake.tables["nodes"])
    assert "Desk lamp" in names(c.get(f"/trips/{tid}/search?q=lamp").get_data(as_text=True))


def test_a_crashing_model_never_blocks_adding_items(fake):
    def broken(_):
        raise RuntimeError("model exploded")
    app = create_app(sb=fake, testing=True, embed_doc=broken, embed_query=broken)
    c = app.test_client()
    tid = logged_in_trip(c, fake)
    add(c, tid, {"name": "Mug"})
    assert len(fake.tables["nodes"]) == 1
    assert "Mug" in names(c.get(f"/trips/{tid}/search?q=mug").get_data(as_text=True))
