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

    # ---- Storage (photos), as the logged-in user: the bucket's own rules limit them to their own trip folders ----
    def upload(self, bucket, path, token, data, content_type="image/jpeg"):
        headers = {"apikey": self.public_key, "Authorization": f"Bearer {token}", "Content-Type": content_type, "x-upsert": "true"}
        try:
            r = self._http.post(f"{self.url}/storage/v1/object/{bucket}/{path}", headers=headers, content=data)
        except httpx.HTTPError:
            raise SupabaseError("We couldn't reach the server. Please try again.") from None
        if r.status_code >= 400:
            raise SupabaseError("The photo couldn't be saved. Please try again.", r.status_code)

    def sign_many(self, bucket, paths, token, expires=3600):
        """Short-lived links (default one hour) for private photos. Returns {path: full URL}."""
        paths = [p for p in paths if p]
        if not paths:
            return {}
        rows = self._request("POST", f"/storage/v1/object/sign/{bucket}", token=token, json_body={"expiresIn": expires, "paths": paths})
        out = {}
        for row in rows or []:
            link = row.get("signedURL")
            if link:
                out[row["path"]] = link if link.startswith("http") else self.url + (link if link.startswith("/storage/v1") else "/storage/v1" + link)
        return out

    def remove(self, bucket, paths, token):
        if paths:
            self._request("DELETE", f"/storage/v1/object/{bucket}", token=token, json_body={"prefixes": list(paths)})

    def list_folder(self, bucket, folder, token):
        """Every file path directly inside one folder (a trip's folder)."""
        names, offset = [], 0
        while True:
            rows = self._request("POST", f"/storage/v1/object/list/{bucket}", token=token,
                                 json_body={"prefix": folder, "limit": 100, "offset": offset}) or []
            names += [f"{folder}/{r['name']}" for r in rows if r.get("name")]
            if len(rows) < 100:
                return names
            offset += 100

    def rpc(self, fn, token, args):
        """Call a database function as the logged-in user (the row-level rules still apply)."""
        return self._request("POST", f"/rest/v1/rpc/{fn}", token=token, json_body=args)

    # ---- Server-only calls with the SECRET key. Used for exactly one job: showing a trip to a viewer who has no
    # account but knows the trip's viewer password. The caller checks the password first and asks only for that trip.
    def _admin(self, method, path, *, json_body=None, params=None, prefer=None):
        if not self.secret_key:
            raise SupabaseError("Viewer links aren't set up on this server.")
        headers = {"apikey": self.secret_key, "Content-Type": "application/json"}
        if self.secret_key.startswith("eyJ"):                 # older keys are JWTs and must also be sent as a bearer token
            headers["Authorization"] = f"Bearer {self.secret_key}"
        if prefer:
            headers["Prefer"] = prefer
        try:
            r = self._http.request(method, self.url + path, headers=headers, json=json_body, params=params)
        except httpx.HTTPError:
            raise SupabaseError("We couldn't reach the server. Please try again.") from None
        if r.status_code >= 400:
            raise SupabaseError(_friendly(r.status_code, r.text), r.status_code)
        return r.json() if r.content else None

    def admin_select(self, table, params):
        return self._admin("GET", f"/rest/v1/{table}", params=params)

    def admin_insert(self, table, row):
        return self._admin("POST", f"/rest/v1/{table}", json_body=row)

    def admin_sign_many(self, bucket, paths, expires=3600):
        paths = [p for p in paths if p]
        if not paths:
            return {}
        rows = self._admin("POST", f"/storage/v1/object/sign/{bucket}", json_body={"expiresIn": expires, "paths": paths})
        out = {}
        for row in rows or []:
            link = row.get("signedURL")
            if link:
                out[row["path"]] = link if link.startswith("http") else self.url + (link if link.startswith("/storage/v1") else "/storage/v1" + link)
        return out
