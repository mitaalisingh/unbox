"""A very small Supabase client: Auth (sign-up, login, reset) and the database, over plain HTTPS.

We call Supabase's REST endpoints directly instead of using a big library, so every step is visible:
  * Auth      -> {SUPABASE_URL}/auth/v1/...
  * Database  -> {SUPABASE_URL}/rest/v1/...   (sent WITH the logged-in user's token, so the
                 database's row-level rules decide what that user may see)

Keys come from the environment (.env). They are never printed or logged.
"""
import json
import os

import httpx


class SupabaseError(Exception):
    """A problem talking to Supabase. The message is safe to show to a user."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _friendly(status, body):
    """Turn a Supabase error body into a short message that is safe to show people."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = {}
    text = (data.get("msg") or data.get("message") or data.get("error_description") or data.get("error") or "").lower()
    code = str(data.get("error_code") or data.get("code") or "").lower()
    if "invalid login" in text or code == "invalid_credentials":
        return "That email and password don't match."
    if "email not confirmed" in text or code == "email_not_confirmed":
        return "Please confirm your email first. Check your inbox for the link."
    if "already registered" in text or code in ("user_already_exists", "email_exists"):
        return "If that email can be used, we've sent it a message."          # do not reveal who has an account
    if "rate limit" in text or code in ("over_email_send_rate_limit", "over_request_rate_limit") or status == 429:
        return "Too many tries. Please wait a few minutes and try again."
    if "password" in text and ("weak" in text or "least" in text or "short" in text):
        return "That password is too weak. Use at least 10 characters."
    return "Something went wrong. Please try again."


class Supabase:
    def __init__(self, url=None, public_key=None, secret_key=None, client=None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.public_key = public_key or os.environ.get("SUPABASE_PUBLIC_KEY", "")
        self.secret_key = secret_key or os.environ.get("SUPABASE_SECRET_KEY", "")
        if not (self.url and self.public_key):
            raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLIC_KEY must be set (see .env.example).")
        self._http = client or httpx.Client(timeout=20)

    # ---- low level -------------------------------------------------------------------------
    def _request(self, method, path, *, token=None, json_body=None, params=None, key=None, prefer=None):
        headers = {"apikey": key or self.public_key, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if prefer:
            headers["Prefer"] = prefer
        try:
            r = self._http.request(method, self.url + path, headers=headers, json=json_body, params=params)
        except httpx.HTTPError:
            raise SupabaseError("We couldn't reach the server. Please try again.") from None
        if r.status_code >= 400:
            raise SupabaseError(_friendly(r.status_code, r.text), r.status_code)
        return r.json() if r.content else None

    # ---- Auth ------------------------------------------------------------------------------
    def sign_up(self, email, password, metadata=None, redirect_to=None):
        params = {"redirect_to": redirect_to} if redirect_to else None
        return self._request("POST", "/auth/v1/signup", json_body={"email": email, "password": password, "data": metadata or {}}, params=params)

    def sign_in(self, email, password):
        return self._request("POST", "/auth/v1/token", params={"grant_type": "password"}, json_body={"email": email, "password": password})

    def refresh(self, refresh_token):
        return self._request("POST", "/auth/v1/token", params={"grant_type": "refresh_token"}, json_body={"refresh_token": refresh_token})

    def recover(self, email, redirect_to=None):
        params = {"redirect_to": redirect_to} if redirect_to else None
        return self._request("POST", "/auth/v1/recover", json_body={"email": email}, params=params)

    def get_user(self, token):
        return self._request("GET", "/auth/v1/user", token=token)

    def set_password(self, token, new_password):
        return self._request("PUT", "/auth/v1/user", token=token, json_body={"password": new_password})

    def sign_out(self, token):
        try:
            self._request("POST", "/auth/v1/logout", token=token)
        except SupabaseError:
            pass                                   # the cookie is cleared either way

    # ---- Database, as the logged-in user (row-level rules apply) ---------------------------
    def select(self, table, token, params=None):
        return self._request("GET", f"/rest/v1/{table}", token=token, params=params or {"select": "*"})

    def insert(self, table, token, row):
        rows = self._request("POST", f"/rest/v1/{table}", token=token, json_body=row, prefer="return=representation")
        return rows[0] if rows else None

    def update(self, table, token, match, changes):
        return self._request("PATCH", f"/rest/v1/{table}", token=token, params=match, json_body=changes, prefer="return=representation")

    def delete(self, table, token, match):
        return self._request("DELETE", f"/rest/v1/{table}", token=token, params=match)
