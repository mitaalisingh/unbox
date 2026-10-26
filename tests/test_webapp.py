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
    assert r.status_code == 302 and "/welcome" in r.headers["Location"]               # the short introduction
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
    assert "/welcome" in r.headers["Location"] and len(fake.tables["profiles"]) == 1


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


def test_items_missing_search_numbers_get_them_when_you_search(fake):
    """Items added while the model wasn't ready (or by an older version) must still be found by meaning."""
    from tests.fake_embed import vec
    old_app = create_app(sb=fake, testing=True, embed_doc=lambda t: None, embed_query=lambda t: None)
    c1 = old_app.test_client()
    tid = logged_in_trip(c1, fake)
    add(c1, tid, {"name": "Travel mug", "tags": "steel"})
    assert not fake.tables["nodes"][0].get("embedding")
    new_app = create_app(sb=fake, testing=True, embed_doc=vec, embed_query=vec)
    c2 = new_app.test_client()
    login(c2, "a@example.com")
    html = c2.get(f"/trips/{tid}/search?q=something+to+drink+from").get_data(as_text=True)
    assert "Travel mug" in names(html)                       # found by meaning, though it had no numbers when it was added
    assert fake.tables["nodes"][0]["embedding"].startswith("[")


def test_catch_up_never_touches_other_peoples_items(client, app, fake):
    tid = stocked_trip(client, fake)
    for n in fake.tables["nodes"]:
        n.pop("embedding", None)
    other = app.test_client()
    make_user(fake, "b@example.com")
    login(other, "b@example.com")
    post(other, "/trips", {"name": "B trip"})
    tid_b = [t["id"] for t in fake.tables["trips"] if t["name"] == "B trip"][0]
    other.get(f"/trips/{tid_b}/search?q=mug")
    assert not any(n.get("embedding") for n in fake.tables["nodes"] if n["trip_id"] == tid)


# ================= editing =================
def edit(client, tid, nid, data=None, photo=None, **kw):
    form = {"csrf": csrf(client, f"/trips/{tid}/n/{nid}/edit")}
    form.update(data or {})
    if photo is not None:
        form["photo"] = (io.BytesIO(photo), "new.jpg")
    return client.post(f"/trips/{tid}/n/{nid}/edit", data=form, content_type="multipart/form-data", **kw)


def node_named(fake, name):
    return next(n for n in fake.tables["nodes"] if n["name"] == name)


def full(**changes):
    """A complete edit form, so a test only states what it wants to change."""
    form = {"name": "Travel mug", "tags": "steel", "description": "", "weight_kg": "", "parent_id": ""}
    form.update(changes)
    return form


def test_edit_changes_text_and_weight_and_can_clear_the_weight(client, fake):
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    edit(client, tid, mug["id"], full(name="Steel flask", tags="steel, thermos", description="from home", weight_kg="0.45", parent_id=mug["parent_id"]))
    n = node_named(fake, "Steel flask")
    assert (n["tags"], n["description"], n["weight_kg"]) == ("steel, thermos", "from home", 0.45)
    edit(client, tid, n["id"], full(name="Steel flask", weight_kg="", parent_id=n["parent_id"]))
    assert node_named(fake, "Steel flask")["weight_kg"] is None


def test_edit_form_shows_current_values(client, fake):
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    html = client.get(f"/trips/{tid}/n/{mug['id']}/edit").get_data(as_text=True)
    assert 'value="Travel mug"' in html and 'value="steel"' in html
    assert "Remove the photo" not in html                    # it has no photo, so no remove option


def test_editing_the_name_refreshes_search_by_meaning(client, fake):
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    assert "Travel mug" not in names(client.get(f"/trips/{tid}/search?q=bulb").get_data(as_text=True))
    edit(client, tid, mug["id"], full(name="Reading bulb", tags="", parent_id=mug["parent_id"]))
    assert "Reading bulb" in names(client.get(f"/trips/{tid}/search?q=lighting").get_data(as_text=True))     # its meaning changed
    assert "Reading bulb" not in names(client.get(f"/trips/{tid}/search?q=something+to+drink+from").get_data(as_text=True))


def test_editing_without_a_model_clears_stale_search_numbers(fake):
    app = create_app(sb=fake, testing=True, embed_doc=lambda t: None, embed_query=lambda t: None)
    c = app.test_client()
    tid = logged_in_trip(c, fake)
    add(c, tid, {"name": "Mug"})
    n = fake.tables["nodes"][0]
    n["embedding"] = "[1.0]"                                 # pretend an older version stored numbers
    edit(c, tid, n["id"], full(name="Lamp", tags="", parent_id=""))
    assert fake.tables["nodes"][0]["embedding"] is None      # cleared, so the catch-up recomputes them later


def test_replacing_a_photo_stores_a_clean_one_in_the_same_place(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps(size=(400, 300), orientation=1))
    n = fake.tables["nodes"][0]
    old = fake.storage[n["photo_path"]]
    edit(client, tid, n["id"], full(name="Lamp", tags="", parent_id=""), photo=photo_with_gps(size=(800, 500), orientation=1))
    new = fake.storage[n["photo_path"]]
    assert len(fake.storage) == 1 and new != old             # replaced, not duplicated
    assert Image.open(io.BytesIO(new)).size == (800, 500)
    assert dict(Image.open(io.BytesIO(new)).getexif()) == {} and b"SecretPhoneMaker" not in new


def test_a_bad_new_photo_changes_nothing(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps(size=(300, 200), orientation=1))
    n = dict(fake.tables["nodes"][0])
    before = fake.storage[n["photo_path"]]
    edit(client, tid, n["id"], full(name="Renamed", tags="x", parent_id=""), photo=b"not a photo")
    assert node_named(fake, "Lamp")["tags"] == "" and fake.storage[n["photo_path"]] == before


def test_removing_a_photo_deletes_the_file(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    n = fake.tables["nodes"][0]
    edit(client, tid, n["id"], full(name="Lamp", tags="", parent_id="", remove_photo="on"))
    assert fake.tables["nodes"][0]["photo_path"] is None and fake.storage == {}


def test_if_the_photo_cannot_be_removed_nothing_is_changed(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Lamp"}, photo=photo_with_gps())
    n = fake.tables["nodes"][0]
    fake.fail_remove = True
    edit(client, tid, n["id"], full(name="New name", tags="", parent_id="", remove_photo="on"))
    assert fake.tables["nodes"][0]["name"] == "Lamp" and fake.tables["nodes"][0]["photo_path"] and len(fake.storage) == 1


def test_move_an_item_into_a_box_and_back_to_the_top(client, fake):
    tid = stocked_trip(client, fake)
    kitchen, lamp = node_named(fake, "Kitchen"), node_named(fake, "Desk lamp")
    edit(client, tid, lamp["id"], full(name="Desk lamp", tags="reading light", parent_id=kitchen["id"]))
    assert node_named(fake, "Desk lamp")["parent_id"] == kitchen["id"]
    assert "Desk lamp" in names(client.get(f"/trips/{tid}/n/{kitchen['id']}").get_data(as_text=True))
    edit(client, tid, lamp["id"], full(name="Desk lamp", tags="reading light", parent_id=""))
    assert node_named(fake, "Desk lamp")["parent_id"] is None


def test_a_box_cannot_be_moved_into_itself_or_its_own_contents(client, fake):
    tid = logged_in_trip(client, fake)
    add(client, tid, {"name": "Big", "kind": "box"})
    big = fake.tables["nodes"][0]["id"]
    add(client, tid, {"name": "Small", "kind": "box"}, parent=big)
    small = fake.tables["nodes"][1]["id"]
    edit(client, tid, big, full(name="Big", tags="", parent_id=big))
    edit(client, tid, big, full(name="Big", tags="", parent_id=small))
    assert node_named(fake, "Big")["parent_id"] is None
    html = client.get(f"/trips/{tid}/n/{big}/edit").get_data(as_text=True)
    assert "Small</option>" not in html and f'value="{big}"' not in html          # not even offered as a choice


def test_items_cannot_be_moved_into_an_item_or_a_made_up_place(client, fake):
    tid = stocked_trip(client, fake)
    lamp, cable = node_named(fake, "Desk lamp"), node_named(fake, "HDMI cable")
    edit(client, tid, lamp["id"], full(name="Desk lamp", tags="reading light", parent_id=cable["id"]))
    edit(client, tid, lamp["id"], full(name="Desk lamp", tags="reading light", parent_id="00000000-0000-0000-0000-000000000000"))
    assert node_named(fake, "Desk lamp")["parent_id"] is None


def test_another_user_cannot_open_or_change_my_items(client, app, fake):
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    other = app.test_client()
    make_user(fake, "b@example.com")
    login(other, "b@example.com")
    assert other.get(f"/trips/{tid}/n/{mug['id']}/edit").status_code == 404
    assert edit(other, tid, mug["id"], full(name="Hacked")).status_code == 404
    assert node_named(fake, "Travel mug")["name"] == "Travel mug"


@pytest.mark.parametrize("data", [{"name": ""}, {"name": "x" * 81}, {"tags": "t" * 201}, {"description": "d" * 301},
                                  {"weight_kg": "abc"}, {"weight_kg": "-1"}, {"weight_kg": "1001"}])
def test_edit_validation(client, fake, data):
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    edit(client, tid, mug["id"], full(parent_id=mug["parent_id"], **data))
    assert node_named(fake, "Travel mug")["tags"] == "steel"


def test_edit_needs_login_and_a_form_token(client, fake):
    assert client.get("/trips/00000000-0000-0000-0000-000000000000/n/00000000-0000-0000-0000-000000000001/edit").status_code == 302
    tid = stocked_trip(client, fake)
    mug = node_named(fake, "Travel mug")
    assert client.post(f"/trips/{tid}/n/{mug['id']}/edit", data={"name": "x"}).status_code == 400


def test_rename_a_trip(client, fake):
    tid = logged_in_trip(client, fake, name="Old name")
    post(client, f"/trips/{tid}/rename", {"name": "  New   name "})
    assert fake.tables["trips"][0]["name"] == "New name"
    post(client, f"/trips/{tid}/rename", {"name": ""})
    post(client, f"/trips/{tid}/rename", {"name": "x" * 81})
    assert fake.tables["trips"][0]["name"] == "New name"


def test_only_the_owner_can_rename_a_trip(client, app, fake):
    tid = logged_in_trip(client, fake, name="Mine")
    other = app.test_client()
    make_user(fake, "b@example.com")
    login(other, "b@example.com")
    assert post(other, f"/trips/{tid}/rename", {"name": "Stolen"}).status_code == 404
    assert fake.tables["trips"][0]["name"] == "Mine"


# ================= batch capture: photos named by the AI =================
def capture_app(fake, guesses=None):
    from tests.fake_embed import vec
    table = guesses or {}
    calls = []

    def suggest(image):
        calls.append(len(image))
        return table.get(len(calls), [("water bottle", 0.8), ("flask", 0.1), ("mug", 0.05)])
    return create_app(sb=fake, testing=True, embed_doc=vec, embed_query=vec, suggest=suggest), calls


def capture(client, tid, photos, parent=""):
    data = {"parent_id": parent, "csrf": csrf(client), "photos": [(io.BytesIO(p), f"p{i}.jpg") for i, p in enumerate(photos)]}
    return client.post(f"/trips/{tid}/capture", data=data, content_type="multipart/form-data")


def test_capture_names_every_photo_and_shows_the_review_screen(fake):
    app, calls = capture_app(fake, {2: [("mystery thing", 0.05), ("lamp", 0.04), ("mug", 0.03)]})
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    r = capture(client, tid, [photo_with_gps(), photo_with_gps(), photo_with_gps()])
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "3 items saved" in page and "water bottle" in page and "flask" in page
    nodes = fake.tables["nodes"]
    assert [n["name"] for n in nodes] == ["water bottle", "Unidentified item", "water bottle"]
    assert all(n["photo_path"] in fake.storage for n in nodes) and [n["position"] for n in nodes] == [1, 2, 3]
    assert page.count("Not sure about this one") == 1              # only the shaky guess is flagged
    assert b"Exif" not in fake.storage[nodes[0]["photo_path"]]       # GPS and metadata removed here too


def test_capture_skips_bad_files_and_still_saves_the_rest(fake):
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    r = capture(client, tid, [photo_with_gps(), b"<html>not a photo</html>"])
    assert "1 photo couldn" in r.get_data(as_text=True) and len(fake.tables["nodes"]) == 1


def test_capture_survives_an_ai_failure(fake):
    from tests.fake_embed import vec

    def broken(image):
        raise RuntimeError("model missing")
    app = create_app(sb=fake, testing=True, embed_doc=vec, embed_query=vec, suggest=broken)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    capture(client, tid, [photo_with_gps()])
    assert [n["name"] for n in fake.tables["nodes"]] == ["Unidentified item"]


def test_capture_limits_and_rules(fake):
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    r = capture(client, tid, [])
    assert r.status_code == 302
    assert "Choose at least one photo" in client.get(f"/trips/{tid}").get_data(as_text=True)
    r = capture(client, tid, [photo_with_gps()] * 21)
    assert r.status_code == 302 and not fake.tables["nodes"]
    assert "Up to 20 photos" in client.get(f"/trips/{tid}").get_data(as_text=True)


def test_review_saves_only_the_corrected_names(fake):
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    capture(client, tid, [photo_with_gps(), photo_with_gps()])
    a, b = fake.tables["nodes"]
    r = client.post(f"/trips/{tid}/review", data={"csrf": csrf(client), "id": [a["id"], b["id"]], f"old_{a['id']}": "water bottle",
                    f"name_{a['id']}": "Steel flask", f"old_{b['id']}": "water bottle", f"name_{b['id']}": "water bottle", "back": f"/trips/{tid}"})
    assert r.status_code == 302
    assert [n["name"] for n in fake.tables["nodes"]] == ["Steel flask", "water bottle"]
    assert fake.tables["nodes"][0]["embedding"]                    # search numbers refreshed with the new name


def test_nobody_else_can_capture_into_or_review_my_trip(fake):
    app, _ = capture_app(fake)
    mine, other = app.test_client(), app.test_client()
    tid = logged_in_trip(mine, fake)
    logged_in_trip(other, fake, email="b@example.com", name="Theirs")
    assert capture(other, tid, [photo_with_gps()]).status_code == 404
    assert not fake.tables["nodes"]


# ================= invite codes =================
FORM = {"email": "new@example.com", "password": GOOD_PW, "password2": GOOD_PW, "age18": "on", "terms": "on"}


def test_signup_is_open_when_no_invite_codes_are_set(client, fake, monkeypatch):
    monkeypatch.delenv("INVITE_CODES", raising=False)
    assert "Invite code" not in client.get("/signup").get_data(as_text=True)
    assert post(client, "/signup", FORM).status_code == 200 and fake.signup_calls


def test_signup_needs_a_valid_invite_code_when_set(client, fake, monkeypatch):
    monkeypatch.setenv("INVITE_CODES", "friends2026, Hostel-Batch")
    assert "Invite code" in client.get("/signup").get_data(as_text=True)
    for bad in ("", "nope", "friends"):
        r = post(client, "/signup", {**FORM, "invite": bad})
        assert r.status_code == 400 and "invite code isn&#39;t valid" in r.get_data(as_text=True)
    assert fake.signup_calls == []                                  # no email is sent for a wrong code
    assert post(client, "/signup", {**FORM, "invite": "  HOSTEL-batch "}).status_code == 200   # case and spaces forgiven
    assert len(fake.signup_calls) == 1


def test_too_many_wrong_codes_locks_guessing_for_a_while(client, fake, monkeypatch):
    monkeypatch.setenv("INVITE_CODES", "friends2026")
    for i in range(10):
        post(client, "/signup", {**FORM, "invite": f"guess{i}"})
    assert post(client, "/signup", {**FORM, "invite": "friends2026"}).status_code == 400   # even the right one waits
    assert fake.signup_calls == []


# ================= capture one photo at a time (what the page's script calls) =================
def test_capture_one_saves_a_named_item_and_returns_json(fake):
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    r = client.post(f"/trips/{tid}/capture-one", data={"csrf": csrf(client), "parent_id": "", "photo": (io.BytesIO(photo_with_gps()), "p.jpg")},
                    content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["name"] == "water bottle" and j["guesses"][:2] == ["water bottle", "flask"] and j["check"] is False
    assert fake.tables["nodes"][0]["id"] == j["id"] and fake.tables["nodes"][0]["photo_path"] in fake.storage


def test_capture_one_refuses_bad_photos_and_strangers(fake):
    app, _ = capture_app(fake)
    mine, other = app.test_client(), app.test_client()
    tid = logged_in_trip(mine, fake)
    logged_in_trip(other, fake, email="b@example.com", name="Theirs")
    bad = mine.post(f"/trips/{tid}/capture-one", data={"csrf": csrf(mine), "photo": (io.BytesIO(b"nope"), "p.jpg")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and not fake.tables["nodes"]
    theirs = other.post(f"/trips/{tid}/capture-one", data={"csrf": csrf(other), "photo": (io.BytesIO(photo_with_gps()), "p.jpg")}, content_type="multipart/form-data")
    assert theirs.status_code == 404 and not fake.tables["nodes"]


def test_review_show_lists_the_saved_items_with_their_suggestions(fake):
    import json
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    j = client.post(f"/trips/{tid}/capture-one", data={"csrf": csrf(client), "photo": (io.BytesIO(photo_with_gps()), "p.jpg")}, content_type="multipart/form-data").get_json()
    results = json.dumps([j, {"id": "00000000-0000-0000-0000-000000000000", "guesses": ["ghost"]}])      # unknown ids are ignored
    page = client.post(f"/trips/{tid}/review-show", data={"csrf": csrf(client), "results": results, "back": f"/trips/{tid}"}).get_data(as_text=True)
    assert "1 item saved" in page and "water bottle" in page and "ghost" not in page
    assert client.post(f"/trips/{tid}/review-show", data={"csrf": csrf(client), "results": "not json"}).status_code == 400


# ================= viewers: no account, viewer password, QR labels =================
def shared_trip(fake, app, password="welcome", email="a@example.com", name="Move"):
    """An owner with one trip, a big box holding a lamp (with a photo), and a viewer password set."""
    owner = app.test_client()
    tid = logged_in_trip(owner, fake, email=email, name=name)
    add(owner, tid, {"name": "Big box", "kind": "box"})
    big = [n for n in fake.tables["nodes"] if n["trip_id"] == tid][-1]["id"]
    add(owner, tid, {"name": "Secret lamp"}, photo=photo_with_gps(), parent=big)
    if password:
        post(owner, f"/trips/{tid}/viewer-password", {"password": password})
    return owner, tid, big


def unlock(viewer, tid, password, nid=""):
    return post(viewer, f"/v/{tid}/unlock", {"password": password, "nid": nid})


def test_a_scan_without_the_password_shows_nothing(fake, app):
    owner, tid, big = shared_trip(fake, app)
    viewer = app.test_client()
    r = viewer.get(f"/v/{tid}/{big}")
    page = r.get_data(as_text=True)
    assert r.status_code == 401 and "Enter the password" in page
    assert "Move" not in page and "Big box" not in page and "Secret lamp" not in page


def test_unknown_trip_and_unshared_trip_look_exactly_alike(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)                 # exists but sharing is off
    viewer = app.test_client()
    a = viewer.get(f"/v/{tid}")
    b = viewer.get("/v/00000000-0000-0000-0000-000000000000")
    assert a.status_code == b.status_code == 401
    assert unlock(viewer, tid, "anything").status_code == 401                # no password set: nothing unlocks it
    assert viewer.get(f"/v/{tid}").status_code == 401


def test_wrong_password_is_refused_and_recorded_right_one_shows_the_box(fake, app):
    owner, tid, big = shared_trip(fake, app)
    viewer = app.test_client()
    r = unlock(viewer, tid, "wrong", big)
    assert r.status_code == 401 and "Secret lamp" not in r.get_data(as_text=True) and len(fake.tables["viewer_attempts"]) == 1
    r = unlock(viewer, tid, "welcome", big)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/v/{tid}/{big}")
    page = viewer.get(f"/v/{tid}/{big}").get_data(as_text=True)
    assert "Secret lamp" in page and "1 item" in page
    assert "https://files.example/" in page and "token=viewer" in page       # the photo, through a short-lived link
    assert "Edit" not in page and "Delete" not in page                       # read-only


def test_the_password_is_stored_hashed_and_never_sent_out(fake, app):
    owner, tid, big = shared_trip(fake, app)
    stored = fake.tables["trips"][0]["viewer_pw_hash"]
    assert stored and "welcome" not in stored
    viewer = app.test_client()
    unlock(viewer, tid, "welcome")
    for url in (f"/v/{tid}", f"/v/{tid}/{big}", f"/trips/{tid}"):
        body = (viewer if url.startswith("/v/") else owner).get(url).get_data(as_text=True)
        assert stored not in body and SECRET_KEY_VALUE not in body


def test_a_pass_is_only_good_for_its_own_trip(fake, app):
    owner, tid, big = shared_trip(fake, app)
    owner2, tid2, big2 = shared_trip(fake, app, password="other-pass", email="b@example.com", name="Theirs")
    viewer = app.test_client()
    unlock(viewer, tid, "welcome")
    assert viewer.get(f"/v/{tid2}").status_code == 401                       # trip 1's password does not open trip 2
    assert unlock(viewer, tid2, "welcome").status_code == 401
    assert viewer.get(f"/v/{tid}/{big2}").status_code == 404                 # a box from another trip is not reachable via this trip
    assert unlock(viewer, tid2, "other-pass").status_code == 302


def test_changing_or_removing_the_password_ends_existing_passes(fake, app):
    owner, tid, big = shared_trip(fake, app)
    viewer = app.test_client()
    unlock(viewer, tid, "welcome")
    assert viewer.get(f"/v/{tid}").status_code == 200
    time.sleep(0.01)
    post(owner, f"/trips/{tid}/viewer-password", {"password": "brand-new"})
    assert viewer.get(f"/v/{tid}").status_code == 401                        # the old pass stopped working
    assert unlock(viewer, tid, "welcome").status_code == 401 and unlock(viewer, tid, "brand-new").status_code == 302
    post(owner, f"/trips/{tid}/viewer-password", {"remove": "1"})
    assert viewer.get(f"/v/{tid}").status_code == 401 and fake.tables["trips"][0]["viewer_pw_hash"] is None


def test_guessing_the_password_is_slowed_down(fake, app):
    owner, tid, big = shared_trip(fake, app)
    viewer = app.test_client()
    for i in range(8):
        assert unlock(viewer, tid, f"guess{i}").status_code == 401
    r = unlock(viewer, tid, "welcome")                                      # even the right one has to wait
    assert r.status_code == 429 and viewer.get(f"/v/{tid}").status_code == 401


def test_viewer_only_ever_asks_the_database_for_one_trip(fake, app):
    owner, tid, big = shared_trip(fake, app)
    viewer = app.test_client()
    unlock(viewer, tid, "welcome")
    viewer.get(f"/v/{tid}/{big}")
    reads = [c for c in fake.admin_calls if c[0] == "select" and c[1] in ("trips", "nodes")]
    assert reads and all(c[2].get("id") == f"eq.{tid}" or c[2].get("trip_id") == f"eq.{tid}" for c in reads)
    assert "embedding" not in " ".join(c[2].get("select", "") for c in reads)  # the search numbers are not needed or sent


def test_only_the_owner_can_set_the_viewer_password_and_it_has_a_minimum_length(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert post(stranger, f"/trips/{tid}/viewer-password", {"password": "hacked-in"}).status_code == 404
    assert fake.tables["trips"][0].get("viewer_pw_hash") is None
    post(owner, f"/trips/{tid}/viewer-password", {"password": "abc"})
    assert fake.tables["trips"][0].get("viewer_pw_hash") is None
    anon = app.test_client()                                               # not logged in: sent to the login page, nothing saved
    assert post(anon, f"/trips/{tid}/viewer-password", {"password": "welcomes"}).status_code == 302
    assert fake.tables["trips"][0].get("viewer_pw_hash") is None


def test_qr_labels_link_to_the_viewer_page_and_contain_no_password(fake, app):
    owner, tid, big = shared_trip(fake, app)
    page = owner.get(f"/trips/{tid}/labels").get_data(as_text=True)
    assert "<svg" in page and "Big box" in page and "1 item" in page and "welcome" not in page
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert stranger.get(f"/trips/{tid}/labels").status_code == 404
    assert app.test_client().get(f"/trips/{tid}/labels").status_code == 302   # needs login


# ================= "Add your items": choose where, sub storage, type-in, and moving an item =================
def test_add_flow_pages_need_login_and_ownership(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    anon = app.test_client()
    for path in (f"/trips/{tid}/add", f"/trips/{tid}/add/storage", f"/trips/{tid}/add/items"):
        assert anon.get(path).status_code == 302
        assert stranger.get(path).status_code == 404
        assert owner.get(path).status_code == 200
    assert owner.get(f"/trips/{tid}/add/items?box={big}").status_code == 200
    item = next(n["id"] for n in fake.tables["nodes"] if not n["is_box"])
    assert owner.get(f"/trips/{tid}/add/items?box={item}").status_code == 404      # an item is not a place to put things


def test_creating_a_sub_storage_returns_to_the_trip_so_items_can_be_dragged_in(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    r = post(owner, f"/trips/{tid}/add/storage", {"name": "  Kitchen   box "})
    box = fake.tables["nodes"][0]
    assert box["name"] == "Kitchen box" and box["is_box"] and box["parent_id"] is None
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/trips/{tid}")           # back to the trip, no detour
    assert "Kitchen box" in owner.get(r.headers["Location"]).get_data(as_text=True)
    r = post(owner, f"/trips/{tid}/add/storage", {"name": "Blue bag", "next": "items"})        # or straight on to adding items
    assert r.headers["Location"].endswith(f"/trips/{tid}/add/items?box={fake.tables['nodes'][1]['id']}")
    assert post(owner, f"/trips/{tid}/add/storage", {"name": "  "}).status_code == 200 and len(fake.tables["nodes"]) == 2
    r = post(owner, f"/trips/{tid}/add/storage", {"name": "Shoe bag", "box": box["id"]})       # a storage inside a storage
    assert fake.tables["nodes"][2]["parent_id"] == box["id"] and r.headers["Location"].endswith(f"/n/{box['id']}")


def test_trip_settings_shows_one_section_at_a_time(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    def main_of(html):
        return html.split("<main>")[1].split("</main>")[0]                        # the page itself, not the shared loading-screen tips
    share = main_of(owner.get(f"/trips/{tid}/settings?section=share").get_data(as_text=True))
    assert "Share by QR" in share and "Print QR labels" in share and "Rename this trip" not in share
    rename = main_of(owner.get(f"/trips/{tid}/settings?section=rename").get_data(as_text=True))
    assert "Rename this trip" in rename and "Share by QR" not in rename
    trip_page = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Share by QR" not in trip_page.split("<main>")[1].split("menu")[0] and "Rename this trip" not in trip_page   # tucked away in the menu
    assert "Rename trip" in trip_page and f"/trips/{tid}/settings" in trip_page
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert stranger.get(f"/trips/{tid}/settings").status_code == 404


def test_typing_items_in_returns_to_the_add_screen_for_the_next_one(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    r = post(owner, f"/trips/{tid}/add", {"name": "Spoon", "kind": "item", "parent_id": big, "again": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/trips/{tid}/add/items?box={big}")
    assert fake.tables["nodes"][-1]["name"] == "Spoon" and fake.tables["nodes"][-1]["parent_id"] == big
    r = post(owner, f"/trips/{tid}/add", {"name": "Fork", "kind": "item", "again": "1"})
    assert r.headers["Location"].endswith(f"/trips/{tid}/add/items")                    # loose item: back to the loose-items screen


def test_moving_an_item_into_a_sub_storage(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Kitchen box", "kind": "box"})
    add(owner, tid, {"name": "Mug"})
    box = next(n for n in fake.tables["nodes"] if n["is_box"])
    mug = next(n for n in fake.tables["nodes"] if n["name"] == "Mug")
    r = post(owner, f"/trips/{tid}/n/{mug['id']}/move", {"parent_id": box["id"]})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "item": "Mug", "box": "Kitchen box"}
    assert mug["parent_id"] == box["id"] and mug["position"] == 1
    assert "Mug" in owner.get(f"/trips/{tid}/n/{box['id']}").get_data(as_text=True)


def test_moves_that_make_no_sense_or_cross_trips_are_refused(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Box A", "kind": "box"}); add(owner, tid, {"name": "Box B", "kind": "box"}); add(owner, tid, {"name": "Mug"})
    a, b = [n for n in fake.tables["nodes"] if n["is_box"]]
    mug = next(n for n in fake.tables["nodes"] if n["name"] == "Mug")
    assert post(owner, f"/trips/{tid}/n/{a['id']}/move", {"parent_id": b["id"]}).status_code == 400       # boxes aren't moved this way
    assert post(owner, f"/trips/{tid}/n/{mug['id']}/move", {"parent_id": mug["id"]}).status_code == 400   # an item isn't a place
    assert post(owner, f"/trips/{tid}/n/{mug['id']}/move", {"parent_id": "not-an-id"}).status_code == 404
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert post(stranger, f"/trips/{tid}/n/{mug['id']}/move", {"parent_id": a["id"]}).status_code == 404
    assert mug["parent_id"] is None


# ================= lock a trip (view only, even for the owner) and the "all items" view =================
def lock(owner, tid, on=True):
    return post(owner, f"/trips/{tid}/lock", {"lock": "1" if on else "0"})


def test_locking_hides_every_editing_control_and_unlocking_brings_them_back(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    page = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Add your items" in page and "Lock trip" in page and "Rename trip" in page
    lock(owner, tid)
    assert fake.tables["trips"][0]["locked_at"]
    page = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Locked" in page and "Unlock trip" in page and "Big box" in page
    assert "Add your items" not in page and "Rename trip" not in page and "Lock trip" not in page
    box_page = owner.get(f"/trips/{tid}/n/{big}").get_data(as_text=True)
    assert "Secret lamp" in box_page and "Edit this box" not in box_page and "Add items" not in box_page and "Delete this box" not in box_page
    lamp = next(n["id"] for n in fake.tables["nodes"] if n["name"] == "Secret lamp")
    item_page = owner.get(f"/trips/{tid}/n/{lamp}").get_data(as_text=True)
    assert "Secret lamp" in item_page and "Delete this item" not in item_page
    lock(owner, tid, on=False)
    assert not fake.tables["trips"][0]["locked_at"]
    assert "Add your items" in owner.get(f"/trips/{tid}").get_data(as_text=True)


def test_a_locked_trip_refuses_every_change_even_if_someone_sends_it_directly(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    lamp = next(n for n in fake.tables["nodes"] if n["name"] == "Secret lamp")
    before = [dict(n) for n in fake.tables["nodes"]]
    lock(owner, tid)
    photo = (io.BytesIO(photo_with_gps()), "p.jpg")
    assert post(owner, f"/trips/{tid}/add", {"name": "Sneaky", "kind": "item"}).status_code == 302
    assert post(owner, f"/trips/{tid}/add/storage", {"name": "Sneaky box"}).status_code == 302
    assert owner.get(f"/trips/{tid}/add").status_code == 302 and owner.get(f"/trips/{tid}/add/items").status_code == 302
    assert owner.get(f"/trips/{tid}/n/{lamp['id']}/edit").status_code == 302
    assert post(owner, f"/trips/{tid}/n/{lamp['id']}/edit", {"name": "Renamed"}).status_code == 302
    assert post(owner, f"/trips/{tid}/n/{lamp['id']}/delete").status_code == 302
    assert post(owner, f"/trips/{tid}/rename", {"name": "New name"}).status_code == 302
    assert post(owner, f"/trips/{tid}/n/{lamp['id']}/move", {"parent_id": big}).status_code == 409
    r = owner.post(f"/trips/{tid}/capture-one", data={"csrf": csrf(owner), "photo": photo}, content_type="multipart/form-data")
    assert r.status_code == 409
    r = owner.post(f"/trips/{tid}/capture", data={"csrf": csrf(owner), "parent_id": "", "photos": [(io.BytesIO(photo_with_gps()), "p.jpg")]}, content_type="multipart/form-data")
    assert r.status_code == 302
    assert fake.tables["nodes"] == before and fake.tables["trips"][0]["name"] == "Move"
    assert fake.uploads.count(lamp["photo_path"]) == 1                                  # no new photos were stored


def test_only_the_owner_can_lock_a_trip(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert post(stranger, f"/trips/{tid}/lock", {"lock": "1"}).status_code == 404
    assert not fake.tables["trips"][0].get("locked_at")


def test_all_items_view_lists_everything_wherever_it_is_kept(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)                  # box "Big box" holds "Secret lamp"
    add(owner, tid, {"name": "Loose spoon"})
    by_storage = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Big box" in by_storage and "Loose spoon" in by_storage and "Secret lamp" not in by_storage
    everything = owner.get(f"/trips/{tid}?show=all").get_data(as_text=True)
    assert "Secret lamp" in everything and "Loose spoon" in everything
    assert "data-drop-box" not in everything                                           # no storage cards in this view
    assert "data-drop-box" in by_storage


# ================= weight and note: on the review screen, and quick edit from the swipe view =================
def test_review_saves_weights_and_ignores_bad_ones(fake, app):
    app, _ = capture_app(fake)
    client = app.test_client()
    tid = logged_in_trip(client, fake)
    capture(client, tid, [photo_with_gps(), photo_with_gps(), photo_with_gps()])
    a, b, c = fake.tables["nodes"]
    form = {"csrf": csrf(client), "id": [a["id"], b["id"], c["id"]], "back": f"/trips/{tid}"}
    for n, w in ((a, "1,5"), (b, "heavy"), (c, "")):
        form[f"old_{n['id']}"] = n["name"]; form[f"name_{n['id']}"] = n["name"]; form[f"weight_{n['id']}"] = w
    r = client.post(f"/trips/{tid}/review", data=form)
    assert r.status_code == 302
    assert a["weight_kg"] == 1.5 and b.get("weight_kg") is None and c.get("weight_kg") is None
    assert "weren&#39;t numbers" in client.get(f"/trips/{tid}").get_data(as_text=True)


def test_swipe_view_shows_the_details_a_user_entered(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Tape dispenser", "weight_kg": "0.35", "tags": "office", "description": "Black, Kangaro"})
    page = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "Details" in page and "0.35 kg" in page and "office" in page and "Black, Kangaro" in page
    assert "Weight &amp; note" in page                                   # the owner can edit it right there


def test_quick_edit_changes_only_weight_and_note(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Mug", "tags": "cup"})
    mug = fake.tables["nodes"][0]
    r = post(owner, f"/trips/{tid}/n/{mug['id']}/quick", {"weight_kg": "0,4", "description": "  chipped   handle "})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "weight_kg": 0.4, "description": "chipped handle"}
    assert mug["name"] == "Mug" and mug["tags"] == "cup" and mug["weight_kg"] == 0.4 and mug["description"] == "chipped handle"
    r = post(owner, f"/trips/{tid}/n/{mug['id']}/quick", {"weight_kg": "", "description": ""})              # clearing both is allowed
    assert r.get_json()["weight_kg"] is None and mug["weight_kg"] is None
    assert post(owner, f"/trips/{tid}/n/{mug['id']}/quick", {"weight_kg": "abc"}).status_code == 400
    assert post(owner, f"/trips/{tid}/n/{mug['id']}/quick", {"description": "x" * 301}).status_code == 400
    assert mug["weight_kg"] is None


def test_quick_edit_is_owner_only_and_blocked_when_locked(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Mug"}); add(owner, tid, {"name": "Big", "kind": "box"})
    mug, big = fake.tables["nodes"]
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert post(stranger, f"/trips/{tid}/n/{mug['id']}/quick", {"weight_kg": "9"}).status_code == 404
    assert post(owner, f"/trips/{tid}/n/{big['id']}/quick", {"weight_kg": "9"}).status_code == 404          # boxes aren't edited this way
    lock(owner, tid)
    assert post(owner, f"/trips/{tid}/n/{mug['id']}/quick", {"weight_kg": "9"}).status_code == 409
    assert mug.get("weight_kg") is None
    assert "Weight &amp; note" not in owner.get(f"/trips/{tid}").get_data(as_text=True)



# ================= install as an app, the introduction, and the Details card =================
def test_app_install_files_are_served(client):
    import json
    m = client.get("/manifest.webmanifest")
    data = json.loads(m.get_data(as_text=True))
    assert m.mimetype == "application/manifest+json" and data["display"] == "standalone" and data["start_url"] == "/trips"
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.mimetype and "caches" not in sw.get_data(as_text=True)    # stores nothing
    assert b'rel="manifest"' in client.get("/login").data and b"apple-touch-icon" in client.get("/login").data


def test_the_introduction_needs_login_and_is_always_reachable_from_the_profile(client, fake):
    assert client.get("/welcome").status_code == 302
    make_user(fake)
    login(client, "a@example.com")
    page = client.get("/welcome").get_data(as_text=True)
    assert "simplified" in page and "in its place" in page and "see what" in page and "Lock it" in page and "Get started" in page
    assert all(client.get(f"/static/intro/art{i}.svg").status_code == 200 for i in (1, 2, 3, 4))
    assert "/welcome" in client.get("/account").get_data(as_text=True)


def test_details_card_is_hidden_until_something_is_entered(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    add(owner, tid, {"name": "Plain mug"})
    add(owner, tid, {"name": "Heavy pan", "weight_kg": "2.5"})
    page = owner.get(f"/trips/{tid}").get_data(as_text=True)
    cards = page.split('class="slide"')[1:]
    plain = next(c for c in cards if "Plain mug" in c)
    heavy = next(c for c in cards if "Heavy pan" in c)
    assert 'class="details" hidden' in plain and 'class="details" >' not in plain
    assert 'class="details" hidden' not in heavy and "2.5 kg" in heavy
    assert "Weight &amp; note" in plain                       # the owner can still add them


# ================= trip codes: a copy of a trip goes into a friend's account =================
import re as _re


def make_code(owner, tid):
    page = post(owner, f"/trips/{tid}/share-code", {}).headers["Location"]
    html = owner.get(page).get_data(as_text=True)
    return _re.search(r'id="the-code">([A-Z0-9-]+)<', html).group(1)


def friend_client(app, fake, email="friend@example.com"):
    friend = app.test_client()
    make_user(fake, email)
    login(friend, email)
    return friend


def test_the_trip_code_is_shown_once_and_only_a_scrambled_copy_is_stored(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    code = make_code(owner, tid)
    assert _re.fullmatch(r"[A-Z2-9]{5}-[A-Z2-9]{5}", code)
    trip = fake.tables["trips"][0]
    assert trip["share_code_hash"] and code.replace("-", "") not in str(trip.values()) and trip["share_code_expires_at"]
    again = owner.get(f"/trips/{tid}/settings?section=copy").get_data(as_text=True)
    assert code not in again and "can't be shown again" in again                      # never shown a second time
    assert code not in owner.get(f"/trips/{tid}").get_data(as_text=True)


def test_entering_a_trip_code_copies_the_whole_trip_into_the_friends_account(fake, app):
    owner, tid, big = shared_trip(fake, app, password="welcome")
    lamp = next(n for n in fake.tables["nodes"] if n["name"] == "Secret lamp")
    lamp["weight_kg"], lamp["description"] = 1.2, "Handle with care"
    add(owner, tid, {"name": "Loose spoon"})
    lock(owner, tid)
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    r = post(friend, "/trips/import", {"code": code.lower()})                               # case does not matter
    assert r.status_code == 302
    mine = [t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"]]
    assert len(mine) == 1 and mine[0]["name"] == "Move" and mine[0]["id"] != tid and r.headers["Location"].endswith(mine[0]["id"])
    assert not mine[0].get("viewer_pw_hash") and not mine[0].get("locked_at") and not mine[0].get("share_code_hash")   # the lock, password and code stay behind
    copies = [n for n in fake.tables["nodes"] if n["trip_id"] == mine[0]["id"]]
    assert sorted(n["name"] for n in copies) == ["Big box", "Loose spoon", "Secret lamp"]
    box = next(n for n in copies if n["name"] == "Big box"); lamp2 = next(n for n in copies if n["name"] == "Secret lamp")
    assert lamp2["parent_id"] == box["id"] and box["id"] != big and lamp2["weight_kg"] == 1.2 and lamp2["description"] == "Handle with care"
    assert lamp2["photo_path"] and lamp2["photo_path"].startswith(mine[0]["id"] + "/") and lamp2["photo_path"] != lamp["photo_path"]
    assert fake.storage[lamp2["photo_path"]] == fake.storage[lamp["photo_path"]]            # the photo came along
    page = friend.get(f"/trips/{mine[0]['id']}").get_data(as_text=True)
    assert "Big box" in page and "Add your items" in page                                    # and it can be edited, unlike a locked original


def test_the_copy_is_independent_of_the_original(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    post(friend, "/trips/import", {"code": code})
    mine = next(t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"])
    copy_lamp = next(n for n in fake.tables["nodes"] if n["trip_id"] == mine["id"] and n["name"] == "Secret lamp")
    post(friend, f"/trips/{mine['id']}/n/{copy_lamp['id']}/delete")
    assert any(n["trip_id"] == tid and n["name"] == "Secret lamp" for n in fake.tables["nodes"])    # the original is untouched
    assert tid not in friend.get("/trips").get_data(as_text=True)                                      # and the friend never gets the original


def test_wrong_expired_and_switched_off_codes_are_refused(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    assert "isn&#39;t right" in post(friend, "/trips/import", {"code": "AAAAA-BBBBB"}, follow_redirects=True).get_data(as_text=True)
    assert post(friend, "/trips/import", {"code": ""}, follow_redirects=True).status_code == 200
    fake.tables["trips"][0]["share_code_expires_at"] = "2000-01-01T00:00:00+00:00"           # expired
    assert "isn&#39;t right" in post(friend, "/trips/import", {"code": code}, follow_redirects=True).get_data(as_text=True)
    fake.tables["trips"][0]["share_code_expires_at"] = "2999-01-01T00:00:00+00:00"
    post(owner, f"/trips/{tid}/share-code", {"off": "1"})
    assert fake.tables["trips"][0]["share_code_hash"] is None
    assert "isn&#39;t right" in post(friend, "/trips/import", {"code": code}, follow_redirects=True).get_data(as_text=True)
    assert len([t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"]]) == 0


def test_guessing_trip_codes_is_slowed_down(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    for i in range(10):
        post(friend, "/trips/import", {"code": f"GUESS-{i:04d}"})
    page = post(friend, "/trips/import", {"code": code}, follow_redirects=True).get_data(as_text=True)
    assert "Too many wrong codes" in page
    assert not [t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"]]


def test_only_the_owner_can_make_or_switch_off_a_trip_code(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    stranger = friend_client(app, fake, "stranger@example.com")
    assert post(stranger, f"/trips/{tid}/share-code", {}).status_code == 404
    assert not fake.tables["trips"][0].get("share_code_hash")
    assert app.test_client().post(f"/trips/{tid}/share-code", data={}).status_code in (302, 400)


def test_a_photo_that_cannot_be_copied_does_not_stop_the_copy(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    lamp = next(n for n in fake.tables["nodes"] if n["name"] == "Secret lamp")
    fake.fail_copy_for = {lamp["photo_path"]}
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    post(friend, "/trips/import", {"code": code})
    mine = next(t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"])
    copy_lamp = next(n for n in fake.tables["nodes"] if n["trip_id"] == mine["id"] and n["name"] == "Secret lamp")
    assert copy_lamp["photo_path"] is None


def test_a_failed_copy_leaves_nothing_behind(fake, app, monkeypatch):
    owner, tid, big = shared_trip(fake, app, password=None)
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    from sbclient import SupabaseError
    def boom(table, token, rows):
        raise SupabaseError("nope", 500)
    monkeypatch.setattr(fake, "insert_many", boom)
    page = post(friend, "/trips/import", {"code": code}, follow_redirects=True).get_data(as_text=True)
    assert "Nothing was added" in page
    assert not [t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"]]
    assert not [p for p in fake.storage if not p.startswith(tid + "/")]                       # copied photos were removed again


# ================= delete my account =================
def test_deleting_an_account_removes_everything_and_only_that_persons_data(fake, app):
    owner, tid, big = shared_trip(fake, app, password="welcome", email="gone@example.com", name="Mine")
    other, tid2, big2 = shared_trip(fake, app, password=None, email="stays@example.com", name="Theirs")
    page = owner.get("/account/delete").get_data(as_text=True)
    assert "gone@example.com" in page and "can't be undone" in page
    r = post(owner, "/account/delete", {"password": GOOD_PW, "sure": "on"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert "gone@example.com" not in fake.users and ("delete_user", ) == fake.admin_calls[-1][:1]
    assert not [t for t in fake.tables["trips"] if t["name"] == "Mine"] and not [n for n in fake.tables["nodes"] if n["trip_id"] == tid]
    assert not [p for p in fake.storage if p.startswith(tid + "/")]                        # photos gone
    assert [p for p in fake.storage if p.startswith(tid2 + "/")]                           # the other person's photos untouched
    assert [t for t in fake.tables["trips"] if t["name"] == "Theirs"] and "stays@example.com" in fake.users
    assert owner.get("/trips").status_code == 302                                           # logged out
    assert login(app.test_client(), "gone@example.com").status_code == 401                  # can't log in any more
    assert viewer_cannot_open(app, tid, big)


def viewer_cannot_open(app, tid, big):
    return app.test_client().get(f"/v/{tid}/{big}").status_code == 401


def test_deleting_needs_the_password_and_the_tick_box(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None, email="gone@example.com", name="Mine")
    assert post(owner, "/account/delete", {"password": GOOD_PW}).status_code == 400            # no tick
    assert post(owner, "/account/delete", {"password": "wrong password!", "sure": "on"}).status_code == 400
    assert "gone@example.com" in fake.users and [t for t in fake.tables["trips"] if t["name"] == "Mine"]
    anon = app.test_client()
    assert anon.get("/account/delete").status_code == 302 and post(anon, "/account/delete", {"password": GOOD_PW, "sure": "on"}).status_code == 302
    assert "gone@example.com" in fake.users


def test_if_photos_cannot_be_removed_the_account_is_kept(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None, email="gone@example.com", name="Mine")
    fake.fail_remove = True
    r = post(owner, "/account/delete", {"password": GOOD_PW, "sure": "on"})
    assert r.status_code == 500 and "account was kept" in r.get_data(as_text=True)
    assert "gone@example.com" in fake.users and not any(c[0] == "delete_user" for c in fake.admin_calls)


def test_a_friends_copy_survives_the_original_owner_deleting_their_account(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None, email="gone@example.com", name="Mine")
    code = make_code(owner, tid)
    friend = friend_client(app, fake)
    post(friend, "/trips/import", {"code": code})
    post(owner, "/account/delete", {"password": GOOD_PW, "sure": "on"})
    mine = [t for t in fake.tables["trips"] if t["owner_id"] == fake.users["friend@example.com"]["id"]]
    assert len(mine) == 1
    copy_lamp = next(n for n in fake.tables["nodes"] if n["trip_id"] == mine[0]["id"] and n["name"] == "Secret lamp")
    assert copy_lamp["photo_path"] in fake.storage                                          # the copy has its own photo file


def test_label_qr_scales_as_a_whole_picture_and_is_never_cropped(fake, app):
    """Regression: the QR was drawn at a fixed size with no viewBox, so the page's CSS cropped it and no phone could scan it."""
    owner, tid, big = shared_trip(fake, app, password=None)
    page = owner.get(f"/trips/{tid}/labels").get_data(as_text=True)
    svg = page[page.index("<svg"):page.index("</svg>")]
    assert "viewBox=" in svg and ' width=' not in svg.split(">")[0] and ' height=' not in svg.split(">")[0]


def test_visitors_see_the_instructions_first_and_then_the_login(client, fake):
    page = client.get("/").get_data(as_text=True)
    assert "simplified" in page and "in its place" in page and "Don&#39;t show this again" not in page   # the button text is filled in by the page script
    assert "unbox-no-intro" in page and 'href="/login"' in page                  # Skip / Log in lead to the login; a device can opt out of the cards
    login_page = client.get("/login").get_data(as_text=True)
    assert "Hello!" in login_page and "Good to see you" in login_page and 'placeholder="E-mail address"' in login_page
    assert "stage-photo" in login_page and "glasscard" in login_page
    make_user(fake)
    r = login(client, "a@example.com")                                          # the first login of a new account: the cards once
    assert "/welcome" in r.headers["Location"]
    client.post("/logout", data={"csrf": csrf(client, "/welcome")})
    assert login(client, "a@example.com").headers["Location"].endswith("/trips")    # after that, straight to the trips
    assert client.get("/").status_code == 302                                    # logged in: "/" is your trips
    assert "simplified" in client.get("/welcome").get_data(as_text=True)         # Profile -> Instructions


def test_the_first_login_carries_on_to_the_page_the_person_wanted(client, fake):
    make_user(fake)
    r = post(client, "/login?next=/trips/abc", {"email": "a@example.com", "password": GOOD_PW, "next": "/trips/abc"})
    assert "/welcome" in r.headers["Location"] and "/trips/abc" in r.headers["Location"]
    assert "/trips/abc" in client.get("/welcome?next=/trips/abc").get_data(as_text=True)
    assert "evil.com" not in client.get("/welcome?next=https://evil.com").get_data(as_text=True)    # no open redirect
    anon = client.application.test_client()
    assert anon.get("/welcome").status_code == 302                               # the logged-in copy needs a login




# ================= share by QR: password is optional, and there is a QR for the whole trip =================
def test_a_trip_can_be_shared_by_qr_with_no_password(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    viewer = app.test_client()
    assert viewer.get(f"/v/{tid}/{big}").status_code == 401                          # not shared yet: nothing
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "open"})
    trip = fake.tables["trips"][0]
    assert trip["viewer_pw_set_at"] and trip.get("viewer_pw_hash") is None
    page = viewer.get(f"/v/{tid}/{big}")
    assert page.status_code == 200 and "Secret lamp" in page.get_data(as_text=True)    # no password asked
    assert "Edit" not in page.get_data(as_text=True)
    assert viewer.get(f"/v/{tid}").status_code == 200                                  # the whole-trip page too
    other, tid2, big2 = shared_trip(fake, app, password=None, email="b@example.com", name="Theirs")
    assert viewer.get(f"/v/{tid2}/{big2}").status_code == 401                          # another trip is still closed


def test_the_owner_can_switch_between_no_password_a_password_and_off(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    viewer = app.test_client()
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "open"})
    assert viewer.get(f"/v/{tid}").status_code == 200
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "password", "password": "welcome"})     # now ask for one
    assert fake.tables["trips"][0]["viewer_pw_hash"] and viewer.get(f"/v/{tid}").status_code == 401
    assert unlock(viewer, tid, "welcome").status_code == 302 and viewer.get(f"/v/{tid}").status_code == 200
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "open"})                                # and back to no password
    assert fake.tables["trips"][0].get("viewer_pw_hash") is None and app.test_client().get(f"/v/{tid}").status_code == 200
    post(owner, f"/trips/{tid}/viewer-password", {"remove": "1"})
    assert app.test_client().get(f"/v/{tid}").status_code == 401 and viewer.get(f"/v/{tid}").status_code == 401


def test_choosing_a_password_needs_one_and_nothing_else_changes(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    for form in ({"mode": "password", "password": "abc"}, {"mode": "password"}, {}):
        post(owner, f"/trips/{tid}/viewer-password", form)
        assert not fake.tables["trips"][0].get("viewer_pw_set_at")                   # still not shared


def test_the_settings_page_says_whether_sharing_is_on_and_how(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    url = f"/trips/{tid}/settings?section=share"
    assert "Off" in owner.get(url).get_data(as_text=True)
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "open"})
    page = owner.get(url).get_data(as_text=True)
    assert "On · no password" in page and "Stop sharing" in page
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "password", "password": "welcome"})
    page = owner.get(url).get_data(as_text=True)
    assert "On · asks for a password" in page and "welcome" not in page and fake.tables["trips"][0]["viewer_pw_hash"] not in page


def test_labels_include_a_qr_for_the_whole_trip_and_say_how_it_is_shared(fake, app):
    owner, tid, big = shared_trip(fake, app, password=None)
    page = owner.get(f"/trips/{tid}/labels").get_data(as_text=True)
    assert page.count("<svg") >= 2 and "Whole trip" in page and "Sharing is off" in page
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "open"})
    assert "No password is set" in owner.get(f"/trips/{tid}/labels").get_data(as_text=True)
    post(owner, f"/trips/{tid}/viewer-password", {"mode": "password", "password": "welcome"})
    assert "asks for your password" in owner.get(f"/trips/{tid}/labels").get_data(as_text=True)


def test_the_item_views_markup_is_well_formed(fake, app):
    """Regression: a stray closing tag once ended the swipe track early, so the grid and swipe views ran together on phones."""
    from html.parser import HTMLParser

    class Tree(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.problems, self.parent_of_slide, self.slides = [], [], set(), 0
        def handle_starttag(self, tag, attrs):
            cls = (dict(attrs).get("class") or "").split()
            if tag in ("img", "input", "br", "meta", "link"):
                return
            if "slide" in cls and tag == "div":
                self.slides += 1
                self.parent_of_slide.add(self.stack[-1][1] if self.stack else None)
            self.stack.append((tag, " ".join(cls)))
        def handle_endtag(self, tag):
            if not self.stack or self.stack[-1][0] != tag:
                self.problems.append(f"unexpected </{tag}> after {self.stack[-1] if self.stack else None}")
                return
            self.stack.pop()

    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    for name in ("Mug", "Lamp", "Spoon"):
        add(owner, tid, {"name": name}, photo=photo_with_gps())
    html = owner.get(f"/trips/{tid}").get_data(as_text=True)
    tree = Tree()
    tree.feed(html)
    assert tree.problems == [] and tree.stack == []                       # every tag opened was closed in the right place
    assert tree.slides == 3 and tree.parent_of_slide == {"track"}         # every slide sits directly inside the swipe track
    assert html.count('class="swipe"') == 1 and html.count('class="grid"') == 1



def test_doodles_loading_screen_and_the_removed_open_label(fake, app, client):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    trips = owner.get("/trips").get_data(as_text=True)
    assert "· open" not in trips and "Created" in trips                             # the stray "open" is gone
    empty = owner.get(f"/trips/{tid}").get_data(as_text=True)
    assert "doodles/box.svg" in empty and 'id="busy"' in empty                    # a doodle in the empty state, loading screen present
    for name in ("box", "camera", "lock"):
        r = client.get(f"/static/doodles/{name}.svg")
        assert r.status_code == 200 and b"prefers-reduced-motion" in r.data         # animated, but still for people who turn motion off
    assert "simplified" in client.get("/").get_data(as_text=True)                   # the home page for visitors is the instructions
    assert 'class="glass addbox slow"' in owner.get("/account/delete").get_data(as_text=True)


def test_a_rejected_login_token_sends_the_person_to_log_in_instead_of_an_error_page(client, fake):
    make_user(fake)
    login(client, "a@example.com")
    fake.tokens.clear()                                                   # the database no longer accepts this person's token
    fake.refresh_tokens.clear()
    r = client.get("/trips")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/trips").status_code == 302                         # and they are logged out


# ================= trip pictures, the greeting, and names =================
def test_every_trip_gets_its_own_picture_and_keeps_it(fake, app, client):
    owner = app.test_client()
    make_user(fake)
    login(owner, "a@example.com")
    for i in range(12):
        post(owner, "/trips", {"name": f"Trip {i}"})
        fake.tables["trips"][-1]["created_at"] = f"2026-10-24T10:00:{i:02d}+00:00"       # one after another
    page = owner.get("/trips").get_data(as_text=True)
    used = _re.findall(r"static/trips/(trip-\d\d\.svg)", page)
    assert len(used) == 12 and len(set(used)) == 12                                  # no two trips share a picture
    scenes = [(int(u[5:7]) - 1) % 5 for u in used]                                    # the fake lists oldest first
    assert all(a != b for a, b in zip(scenes, scenes[1:]))                           # neighbours are never the same kind of scene
    assert used == _re.findall(r"static/trips/(trip-\d\d\.svg)", owner.get("/trips").get_data(as_text=True))     # and they are stable
    for i in range(1, 26):
        assert client.get(f"/static/trips/trip-{i:02d}.svg").status_code == 200


def test_more_trips_than_pictures_still_all_get_one(fake, app):
    owner = app.test_client()
    make_user(fake)
    login(owner, "a@example.com")
    for i in range(27):
        post(owner, "/trips", {"name": f"T{i}"})
    page = owner.get("/trips").get_data(as_text=True)
    assert len(_re.findall(r"static/trips/trip-\d\d\.svg", page)) == 27


def test_the_greeting_uses_the_name_and_shows_once_per_login(client, fake):
    make_user(fake, "mitaali.singh255@example.com")
    login(client, "mitaali.singh255@example.com")
    first = client.get("/trips").get_data(as_text=True)
    assert 'id="hello"' in first and "<em>Mitaali</em>" in first                    # no name given: the first word of the email
    assert 'id="hello"' not in client.get("/trips").get_data(as_text=True)          # not again until the next login
    client.post("/logout", data={"csrf": csrf(client, "/trips")})
    login(client, "mitaali.singh255@example.com")
    assert 'id="hello"' in client.get("/trips").get_data(as_text=True)


def test_a_name_given_at_signup_or_in_the_profile_is_used(client, fake):
    r = post(client, "/signup", {"email": "x9@example.com", "password": GOOD_PW, "password2": GOOD_PW, "age18": "on", "terms": "on", "name": "  Rahul   Sharma "})
    assert fake.signup_calls[-1]["metadata"]["display_name"] == "Rahul Sharma"
    fake.users["x9@example.com"]["confirmed"] = True
    login(client, "x9@example.com")
    assert "<em>Rahul</em>" in client.get("/trips").get_data(as_text=True)
    assert 'value="Rahul Sharma"' in client.get("/account").get_data(as_text=True)
    post(client, "/account/name", {"name": "Raj"})
    assert fake.users["x9@example.com"]["meta"]["display_name"] == "Raj"
    client.post("/logout", data={"csrf": csrf(client, "/trips")})
    login(client, "x9@example.com")
    assert "<em>Raj</em>" in client.get("/trips").get_data(as_text=True)
    assert post(app_anon(client), "/account/name", {"name": "x"}).status_code == 302


def app_anon(client):
    return client.application.test_client()


# ================= choosing or uploading a trip's picture =================
def post_cover(client, tid, **fields):
    data = {"csrf": csrf(client), **fields}
    return client.post(f"/trips/{tid}/cover", data=data, content_type="multipart/form-data")


def test_a_trip_can_use_a_chosen_illustration_and_others_avoid_it(fake, app):
    owner = app.test_client()
    make_user(fake)
    login(owner, "a@example.com")
    for name in ("One", "Two", "Three"):
        post(owner, "/trips", {"name": name})
    one = fake.tables["trips"][0]
    r = post_cover(owner, one["id"], art="7")
    assert r.status_code == 302 and one["cover"] == "art:7"
    page = owner.get("/trips").get_data(as_text=True)
    used = _re.findall(r"static/trips/(trip-\d\d\.svg)", page)
    assert used.count("trip-07.svg") == 1 and len(set(used)) == 3                    # the others pick something else
    assert 'class="on"' in owner.get(f"/trips/{one['id']}/settings?section=cover").get_data(as_text=True)
    assert owner.get(f"/trips/{one['id']}/settings?section=cover").get_data(as_text=True).count('name="art"') == 30


def test_a_trip_can_use_the_owners_own_photo_and_it_is_cleaned_and_private(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    r = owner.post(f"/trips/{tid}/cover", data={"csrf": csrf(owner), "photo": (io.BytesIO(photo_with_gps()), "mine.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 302 and fake.tables["trips"][0]["cover"] == "photo"
    assert b"Exif" not in fake.storage[f"{tid}/cover.jpg"] and b"SecretPhoneMaker" not in fake.storage[f"{tid}/cover.jpg"]
    assert f"https://files.example/{tid}/cover.jpg" in owner.get("/trips").get_data(as_text=True)     # shown through a short-lived link
    post_cover(owner, tid, art="3")                                                  # choosing an illustration drops the uploaded file
    assert f"{tid}/cover.jpg" not in fake.storage and fake.tables["trips"][0]["cover"] == "art:3"
    owner.post(f"/trips/{tid}/cover", data={"csrf": csrf(owner), "photo": (io.BytesIO(photo_with_gps()), "mine.jpg")}, content_type="multipart/form-data")
    post_cover(owner, tid, auto="1")
    assert fake.tables["trips"][0]["cover"] is None and f"{tid}/cover.jpg" not in fake.storage


def test_a_bad_picture_is_refused_and_nothing_changes(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    r = owner.post(f"/trips/{tid}/cover", data={"csrf": csrf(owner), "photo": (io.BytesIO(b"<html>nope</html>"), "x.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 302 and not fake.tables["trips"][0].get("cover") and f"{tid}/cover.jpg" not in fake.storage
    post_cover(owner, tid, art="99")
    post_cover(owner, tid)
    assert not fake.tables["trips"][0].get("cover")


def test_only_the_owner_can_change_a_picture_and_a_locked_trip_keeps_its_own(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    stranger = app.test_client()
    logged_in_trip(stranger, fake, email="b@example.com", name="Theirs")
    assert post_cover(stranger, tid, art="2").status_code == 404 and not fake.tables["trips"][0].get("cover")
    lock(owner, tid)
    assert post_cover(owner, tid, art="2").status_code == 302 and not fake.tables["trips"][0].get("cover")


def test_deleting_a_trip_also_deletes_its_own_picture_file(fake, app):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    owner.post(f"/trips/{tid}/cover", data={"csrf": csrf(owner), "photo": (io.BytesIO(photo_with_gps()), "mine.jpg")}, content_type="multipart/form-data")
    assert f"{tid}/cover.jpg" in fake.storage
    post(owner, f"/trips/{tid}/delete", {"sure": "on"})
    assert f"{tid}/cover.jpg" not in fake.storage


def test_trips_still_show_if_the_database_has_no_cover_column_yet(fake, app, monkeypatch):
    owner = app.test_client()
    tid = logged_in_trip(owner, fake)
    real = fake.select
    from sbclient import SupabaseError
    def select(table, token, params=None):
        if table == "trips" and "cover" in (params or {}).get("select", ""):
            raise SupabaseError("column does not exist", 400)
        return real(table, token, params)
    monkeypatch.setattr(fake, "select", select)
    assert "static/trips/trip-" in owner.get("/trips").get_data(as_text=True)
    assert owner.get(f"/trips/{tid}").status_code == 200
