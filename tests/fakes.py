"""An in-memory stand-in for Supabase, used only by the tests.

It copies the one behaviour that matters for privacy: rows are filtered by the token the app sends, the way the
real database's row-level rules filter by the logged-in user. It also records which tokens were used, so tests can
prove the app never sends the powerful secret key on behalf of a visitor.
"""
import uuid

from sbclient import SupabaseError

SECRET_KEY_VALUE = "FAKE-SERVER-KEY-THE-APP-MUST-NEVER-SEND"


class FakeSupabase:
    def __init__(self):
        self.users = {}                    # email -> {id, password, confirmed, meta}
        self.tokens = {}                   # access token -> user id
        self.refresh_tokens = {}           # refresh token -> user id
        self.tables = {"profiles": [], "trips": []}
        self.tokens_used = []              # every token the app sent to the database
        self.recover_calls, self.signup_calls = [], []
        self.fail_refresh = False
        self.passwords_set = []

    # ---- helpers for the tests ----
    def add_user(self, email, password="correct horse battery", confirmed=True, meta=None):
        uid = str(uuid.uuid4())
        self.users[email] = {"id": uid, "password": password, "confirmed": confirmed, "meta": meta if meta is not None else {}}
        return uid

    def _issue(self, uid, email):
        at, rt = f"at-{uuid.uuid4()}", f"rt-{uuid.uuid4()}"
        self.tokens[at], self.refresh_tokens[rt] = uid, rt and uid
        return {"access_token": at, "refresh_token": rt, "expires_in": 3600, "user": {"id": uid, "email": email}}

    def _uid(self, token):
        self.tokens_used.append(token)
        if token not in self.tokens:
            raise SupabaseError("Something went wrong. Please try again.", 401)
        return self.tokens[token]

    # ---- Auth ----
    def sign_up(self, email, password, metadata=None, redirect_to=None):
        self.signup_calls.append({"email": email, "metadata": metadata, "redirect_to": redirect_to})
        if email not in self.users:
            self.add_user(email, password, confirmed=False, meta=metadata or {})
        return {}

    def sign_in(self, email, password):
        u = self.users.get(email)
        if not u or u["password"] != password:
            raise SupabaseError("That email and password don't match.", 400)
        if not u["confirmed"]:
            raise SupabaseError("Please confirm your email first. Check your inbox for the link.", 400)
        return self._issue(u["id"], email)

    def refresh(self, refresh_token):
        if self.fail_refresh or refresh_token not in self.refresh_tokens:
            raise SupabaseError("Something went wrong. Please try again.", 400)
        uid = self.refresh_tokens[refresh_token]
        email = next(e for e, u in self.users.items() if u["id"] == uid)
        return self._issue(uid, email)

    def recover(self, email, redirect_to=None):
        self.recover_calls.append(email)

    def get_user(self, token):
        uid = self._uid(token)
        email, u = next((e, u) for e, u in self.users.items() if u["id"] == uid)
        return {"id": uid, "email": email, "user_metadata": u["meta"]}

    def set_password(self, token, new_password):
        uid = self._uid(token)
        self.passwords_set.append(new_password)
        next(u for u in self.users.values() if u["id"] == uid)["password"] = new_password

    def sign_out(self, token):
        self.tokens.pop(token, None)

    # ---- Database with "row-level rules": a row is visible only to its owner ----
    def _owner_of(self, table, row):
        return row.get("owner_id") if table == "trips" else row.get("user_id")

    def _visible(self, table, uid):
        return [r for r in self.tables[table] if self._owner_of(table, r) == uid]

    @staticmethod
    def _matches(row, params):
        for k, v in (params or {}).items():
            if k in ("select", "order"):
                continue
            if v.startswith("eq.") and str(row.get(k)) != v[3:]:
                return False
        return True

    def select(self, table, token, params=None):
        uid = self._uid(token)
        return [dict(r) for r in self._visible(table, uid) if self._matches(r, params)]

    def insert(self, table, token, row):
        uid = self._uid(token)
        if self._owner_of(table, row) != uid:
            raise SupabaseError("Something went wrong. Please try again.", 403)       # the "with check" rule
        row = dict(row)
        if table == "trips":
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", "2026-10-24T10:00:00+00:00")
            row.setdefault("archived_at", None)
        self.tables[table].append(row)
        return dict(row)

    def update(self, table, token, match, changes):
        uid = self._uid(token)
        done = []
        for r in self._visible(table, uid):
            if self._matches(r, match):
                r.update(changes)
                done.append(dict(r))
        return done

    def delete(self, table, token, match):
        uid = self._uid(token)
        mine = [r for r in self._visible(table, uid) if self._matches(r, match)]
        self.tables[table] = [r for r in self.tables[table] if r not in mine]
