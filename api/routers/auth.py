from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.deps import COOKIE_NAME, get_current_user, get_db, require_admin, require_user, session_hash
from api.schemas import (AccessSettings, AdminCreateUserRequest, AuthResponse, ConfirmRequest, CountResponse,
                         LoginRequest, MessageResponse, PasswordChangeRequest, RegisterRequest, RoleUpdateRequest, User)
from config.settings import Settings, login_required
from core import auth
from core.auth import SESSION_HOURS
from database.db import Database

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue(response: Response, db: Database, user: dict) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    db.create_api_session(hashlib.sha256(token.encode()).hexdigest(), int(user["id"]), expires.isoformat(timespec="seconds"))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax",
                        secure=os_https(), path="/")


def os_https() -> bool:
    # Local development uses HTTP; deployments can opt into secure cookies.
    import os
    return os.environ.get("TESTPILOT_COOKIE_SECURE", "") == "1"


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(body: RegisterRequest, response: Response, db: Database = Depends(get_db)):
    cfg = Settings.load()
    ok, message, user = auth.register(db, body.username, body.password, allow_signup=cfg.allow_signup)
    if not ok or user is None:
        raise HTTPException(status_code=409 if "taken" in message else 400, detail=message)
    _issue(response, db, user)
    return {"user": user, "authenticated": True}


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, response: Response, db: Database = Depends(get_db)):
    ok, message, user = auth.authenticate(db, body.username, body.password)
    if not ok or user is None:
        raise HTTPException(status_code=401, detail=message, headers={"WWW-Authenticate": "Bearer"})
    _issue(response, db, user)
    return {"user": user, "authenticated": True}


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, db: Database = Depends(get_db)):
    token_digest = session_hash(request)
    if token_digest:
        db.delete_api_session(token_digest)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"message": "Signed out."}


@router.get("/me", response_model=User | None)
def me(user: dict | None = Depends(get_current_user)):
    return user


@router.post("/password", response_model=MessageResponse)
def change_password(body: PasswordChangeRequest, user: dict = Depends(require_user), db: Database = Depends(get_db)):
    ok, message = auth.change_password(db, user["id"], body.current_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message}


@router.get("/admin/users", response_model=list[User])
def users(_: dict = Depends(require_admin), db: Database = Depends(get_db)):
    return [{"id": int(row["id"]), "username": row["username"], "role": row["role"]}
            for row in db.list_users().to_dict("records")]


@router.post("/admin/users", response_model=User, status_code=201)
def create_user(body: AdminCreateUserRequest, _: dict = Depends(require_admin), db: Database = Depends(get_db)):
    ok, message, user = auth.register(db, body.username, body.password, by_admin=True, role=body.role)
    if not ok or user is None:
        raise HTTPException(status_code=409 if "taken" in message else 400, detail=message)
    return user


@router.patch("/admin/users/{user_id}", response_model=User)
def set_role(user_id: int, body: RoleUpdateRequest, actor: dict = Depends(require_admin), db: Database = Depends(get_db)):
    current = db.get_user_by_id(user_id)
    if not current:
        raise HTTPException(404, "User not found.")
    if user_id == actor["id"]:
        raise HTTPException(400, "Use another administrator account to change your role.")
    if current["role"] == "admin" and body.role == "user" and db.count_admins() <= 1:
        raise HTTPException(409, "Create another administrator before demoting the last admin account.")
    db.set_role(user_id, body.role)
    return {"id": user_id, "username": current["username"], "role": body.role}


@router.delete("/admin/users/{user_id}", response_model=MessageResponse)
def delete_user(user_id: int, actor: dict = Depends(require_admin), db: Database = Depends(get_db)):
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if target["role"] == "admin" and db.count_admins() <= 1 and db.count_users() > 1:
        raise HTTPException(409, "Create another administrator before deleting the last admin account.")
    if actor["id"] == user_id and db.count_users() <= 1:
        raise HTTPException(409, "Create another account before deleting the last account.")
    db.delete_user(user_id)
    return {"message": "User and owned data deleted."}


@router.get("/admin/settings", response_model=AccessSettings)
def access_settings(_: dict = Depends(require_admin)):
    s = Settings.load()
    return {"require_login": s.require_login, "allow_signup": s.allow_signup}


@router.patch("/admin/settings", response_model=AccessSettings)
def update_access(body: AccessSettings, _: dict = Depends(require_admin)):
    s = Settings.load()
    s.require_login, s.allow_signup = body.require_login, body.allow_signup
    s.save()
    return body


@router.post("/account/delete", response_model=MessageResponse)
def delete_account(body: ConfirmRequest, response: Response, request: Request, user: dict = Depends(require_user), db: Database = Depends(get_db)):
    if not body.confirm:
        raise HTTPException(400, "Explicit confirmation is required.")
    if user["role"] == "admin" and db.count_admins() <= 1 and db.count_users() > 1:
        raise HTTPException(409, "You are the only administrator. Make another user an admin first.")
    token_digest = session_hash(request)
    if token_digest:
        db.delete_api_session(token_digest)
    db.delete_user(user["id"])
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"message": "Account and owned data deleted."}


@router.delete("/account/runs", response_model=CountResponse)
def delete_my_runs(user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    deleted = db.purge_older_than(0, user_id=user["id"] if user else None)
    return {"deleted": deleted}
