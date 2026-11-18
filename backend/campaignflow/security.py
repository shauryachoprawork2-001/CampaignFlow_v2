"""Password hashing, sessions, role-based access control, login throttling."""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

from flask import current_app, g, request

from .db import get_db
from .util import ApiError, iso, utcnow, utcnow_dt

# ------------------------------------------------------------------ RBAC
ROLES = ("admin", "manager", "analyst", "client_viewer")

PERMISSIONS = {
    "admin": {
        "users.manage", "clients.write", "campaigns.write", "budgets.write",
        "metrics.record", "reports.generate", "audit.read", "tasks.read", "tasks.run",
        "data.read_all",
    },
    "manager": {
        "clients.write", "campaigns.write", "budgets.write", "metrics.record",
        "reports.generate", "tasks.read", "tasks.run", "data.read_all",
    },
    "analyst": {"metrics.record", "reports.generate", "data.read_all"},
    # client_viewer: read-only, and only their own client's data
    "client_viewer": set(),
}

# ------------------------------------------------------------------ passwords
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown emails

# ------------------------------------------------------------------ sessions
COOKIE = "cf_session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    days = current_app.config["SESSION_DAYS"]
    now = utcnow_dt()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (_hash_token(token), user_id, iso(now), iso(now + timedelta(days=days))),
    )
    # opportunistic cleanup keeps the table small
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
    return token


def destroy_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def _token_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE)


def authenticate() -> dict:
    token = _token_from_request()
    if not token:
        raise ApiError(401, "unauthenticated", "Authentication required")
    return load_user(get_db(), token)


def load_user(conn, token: str) -> dict:
    row = conn.execute(
        """SELECT u.id, u.agency_id, u.client_id, u.email, u.name, u.role,
                  a.name AS agency_name, a.currency
             FROM sessions s
             JOIN users u    ON u.id = s.user_id
             JOIN agencies a ON a.id = u.agency_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1""",
        (_hash_token(token), utcnow()),
    ).fetchone()
    if row is None:
        raise ApiError(401, "unauthenticated", "Session is invalid or has expired")
    user = dict(row)
    user["perms"] = PERMISSIONS[user["role"]]
    return user


def require(*perms):
    """Route decorator: authenticate, then check the role grants every permission."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = authenticate()
            missing = [p for p in perms if p not in user["perms"]]
            if missing:
                raise ApiError(403, "forbidden", "Your role does not allow this action",
                               {"required": list(perms), "role": user["role"]})
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco


def client_scope(user: dict):
    """client_viewers are confined to one client; staff see the whole agency."""
    return user["client_id"] if user["role"] == "client_viewer" else None


def public_user(user: dict) -> dict:
    return {
        "id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"],
        "agency_id": user["agency_id"], "agency_name": user["agency_name"],
        "client_id": user["client_id"], "currency": user["currency"],
        "permissions": sorted(user["perms"]),
    }


# ------------------------------------------------------------------ login throttle
class LoginThrottle:
    """In-memory sliding window: N failures per window per (email, ip)."""

    def __init__(self, limit=10, window=900):
        self.limit, self.window = limit, window
        self._hits = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return hits

    def check(self, key):
        with self._lock:
            if len(self._prune(key, time.time())) >= self.limit:
                raise ApiError(429, "too_many_attempts",
                               "Too many failed sign-in attempts. Try again later.")

    def fail(self, key):
        with self._lock:
            self._prune(key, time.time()).append(time.time())

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


throttle = LoginThrottle()
