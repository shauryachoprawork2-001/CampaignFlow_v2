"""Small shared helpers: time, errors, request parsing and validation."""
import re
from datetime import date, datetime, timezone

from flask import request

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- time
def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> str:
    return iso(utcnow_dt())


def today_utc() -> date:
    return utcnow_dt().date()


# ---------------------------------------------------------------- errors
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------- parsing
def parse_json() -> dict:
    """Require a JSON object body. Requiring application/json also acts as a
    CSRF guard: cross-site HTML forms cannot send that content type."""
    if not request.is_json:
        raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object")
    return data


def _bad(field: str, msg: str):
    raise ApiError(422, "validation_error", f"{field}: {msg}", {field: msg})


def v_str(d, key, *, required=True, min_len=1, max_len=200, default=None):
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            _bad(key, "is required")
        return default
    if not isinstance(v, str):
        _bad(key, "must be a string")
    v = v.strip()
    if len(v) < min_len:
        _bad(key, f"must be at least {min_len} characters")
    if len(v) > max_len:
        _bad(key, f"must be at most {max_len} characters")
    return v


def v_email(d, key, *, required=True):
    v = v_str(d, key, required=required, max_len=254)
    if v is None:
        return None
    if not EMAIL_RE.match(v):
        _bad(key, "must be a valid email address")
    return v.lower()


def v_int(d, key, *, required=True, min=None, max=None, default=None):
    v = d.get(key)
    if v is None:
        if required:
            _bad(key, "is required")
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        _bad(key, "must be an integer")
    if min is not None and v < min:
        _bad(key, f"must be >= {min}")
    if max is not None and v > max:
        _bad(key, f"must be <= {max}")
    return v


def v_bool(d, key, *, default=False):
    v = d.get(key, default)
    if not isinstance(v, bool):
        _bad(key, "must be true or false")
    return v


def v_enum(d, key, choices, *, required=True, default=None):
    v = d.get(key)
    if v is None:
        if required:
            _bad(key, "is required")
        return default
    if v not in choices:
        _bad(key, "must be one of: " + ", ".join(sorted(choices)))
    return v


def v_date(d, key, *, required=True, default=None):
    v = d.get(key)
    if v is None or v == "":
        if required:
            _bad(key, "is required")
        return default
    if not isinstance(v, str) or not DATE_RE.match(v):
        _bad(key, "must be a date formatted YYYY-MM-DD")
    try:
        date.fromisoformat(v)
    except ValueError:
        _bad(key, "is not a valid calendar date")
    return v


def page_args(default_per_page=25):
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = int(request.args.get("per_page", default_per_page))
    except ValueError:
        raise ApiError(422, "validation_error", "page and per_page must be integers")
    per_page = max(1, min(per_page, 100))
    return page, per_page, (page - 1) * per_page


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def rows_to_dicts(rows):
    return [dict(r) for r in rows]
