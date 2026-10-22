from __future__ import annotations

import hashlib
import os

from fastapi import Depends, HTTPException, Request

from config.settings import Settings, login_required
from database.db import Database

COOKIE_NAME = "testpilot_session"


def get_db(request: Request) -> Database:
    return request.app.state.db


def _token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME, "")


def get_current_user(request: Request, db: Database = Depends(get_db)) -> dict | None:
    token = _token(request)
    if token:
        user = db.get_api_session_user(hashlib.sha256(token.encode()).hexdigest())
        if user:
            return user
    if login_required(Settings.load()):
        raise HTTPException(status_code=401, detail="Sign in is required.", headers={"WWW-Authenticate": "Bearer"})
    return None


def require_user(user: dict | None = Depends(get_current_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def session_hash(request: Request) -> str | None:
    token = _token(request)
    return hashlib.sha256(token.encode()).hexdigest() if token else None
