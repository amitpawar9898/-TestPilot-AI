"""Local account authentication (no external service needed).

* Passwords are never stored: only a salted scrypt hash (PBKDF2-SHA256 as a fallback) is kept.
* Wrong usernames and wrong passwords give the same message, and an unknown username still costs one hash check.
* 5 wrong passwords lock the account for 15 minutes.
* The first account created becomes the admin.
Recovery if you lock yourself out:  python -m core.auth reset-password <username>
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import re
import sys
from datetime import datetime, timedelta, timezone

USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,32}")
MAX_FAILS = 5
LOCK_MINUTES = 15
SESSION_HOURS = 8
GENERIC_FAIL = "Invalid username or password, or the account is temporarily locked."
COMMON = {"password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop", "letmein123", "admin12345", "welcome123", "iloveyou123", "abc1234567"}
_SCRYPT = (2**14, 8, 1)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    try:
        n, r, p = _SCRYPT
        dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
        return f"scrypt${n}${r}${p}${b64(salt)}${b64(dk)}"
    except (AttributeError, ValueError):  # OpenSSL without scrypt
        iters = 600_000
        return f"pbkdf2${iters}${b64(salt)}${b64(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iters, 32))}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            _, n, r, p, salt, want = parts
            dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        elif parts[0] == "pbkdf2":
            _, iters, salt, want = parts
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters), 32)
        else:
            return False
        return hmac.compare_digest(dk, base64.b64decode(want))
    except Exception:  # noqa: BLE001 - malformed hash must never raise into the UI
        return False


_DUMMY = hash_password("dummy-password-for-timing")


def validate_username(name: str) -> str | None:
    return None if USERNAME_RE.fullmatch(name or "") else "Username must be 3-32 characters: letters, digits, dot, dash or underscore."


def validate_password(pw: str, username: str = "") -> str | None:
    if len(pw or "") < 10:
        return "Password must be at least 10 characters."
    if len(pw) > 128:
        return "Password must be at most 128 characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must contain at least one letter and one digit."
    if pw.lower() in COMMON or len(set(pw)) < 4:
        return "That password is too easy to guess."
    if username and username.lower() in pw.lower():
        return "Password must not contain your username."
    return None


def _public(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


def register(db, username: str, password: str, allow_signup: bool = True, by_admin: bool = False, role: str = "user"):
    """Returns (ok, message, user|None). The very first account is always the admin."""
    username = (username or "").strip()
    first = db.count_users() == 0
    if not (first or allow_signup or by_admin):
        return False, "Sign-up is disabled. Ask an administrator for an account.", None
    err = validate_username(username) or validate_password(password, username)
    if err:
        return False, err, None
    if db.get_user(username):
        return False, "That username is taken.", None
    try:
        uid = db.create_user(username, hash_password(password), "admin" if first else (role if by_admin else "user"))
    except Exception:  # noqa: BLE001 - e.g. race on the unique index
        return False, "That username is taken.", None
    return True, "Account created." + (" You are the administrator." if first else ""), _public(db.get_user_by_id(uid))


def authenticate(db, username: str, password: str):
    """Returns (ok, message, user|None)."""
    u = db.get_user((username or "").strip())
    if not u:
        verify_password(password or "", _DUMMY)  # keep timing similar for unknown users
        return False, GENERIC_FAIL, None
    if u["locked_until"] and datetime.fromisoformat(u["locked_until"]) > datetime.now(timezone.utc):
        return False, GENERIC_FAIL, None
    if verify_password(password or "", u["password_hash"]):
        db.record_login(u["id"])
        return True, "", _public(u)
    lock = (datetime.now(timezone.utc) + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    db.record_failed_login(u["id"], MAX_FAILS, lock)
    return False, GENERIC_FAIL, None


def change_password(db, user_id: int, old: str, new: str):
    u = db.get_user_by_id(user_id)
    if not u or not verify_password(old or "", u["password_hash"]):
        return False, "Current password is incorrect."
    err = validate_password(new, u["username"])
    if err:
        return False, err
    db.set_password_hash(user_id, hash_password(new))
    return True, "Password changed."


def session_expired(login_at: float, now: float) -> bool:
    return now - login_at > SESSION_HOURS * 3600


def _cli(argv: list[str]) -> int:
    from config.settings import DB_PATH
    from database.db import Database
    db = Database(DB_PATH)
    if len(argv) >= 2 and argv[0] == "reset-password":
        u = db.get_user(argv[1])
        if not u:
            print("No such user.")
            return 1
        pw = getpass.getpass("New password: ")
        err = validate_password(pw, u["username"])
        if err or pw != getpass.getpass("Repeat: "):
            print(err or "Passwords do not match.")
            return 1
        db.set_password_hash(u["id"], hash_password(pw))
        print("Password updated and any lock cleared.")
        return 0
    print("Usage: python -m core.auth reset-password <username>")
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
