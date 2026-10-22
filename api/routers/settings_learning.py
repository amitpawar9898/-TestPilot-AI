from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_user, get_db, require_admin
from api.schemas import (Availability, ConnectionTestRequest, ConnectionTestResponse, CountResponse, LearningList,
                         FilePayload, LearningRatingRequest, MessageResponse, ModelList, PurgeRequest, SettingsResponse,
                         SettingsUpdateRequest)
from config.settings import EXECUTION_MODES, MODEL_HINTS, PROVIDERS, Settings, get_secret
from database.db import Database
from tools.llm_client import LLMClient, detect_ollama
from tools.test_runner import sandbox_status
from tools.training_export import to_chat_jsonl, to_prompt_jsonl

router = APIRouter(prefix="/api", tags=["settings", "learning"])


def _owner(user: dict | None) -> int | None:
    return int(user["id"]) if user else None


def _admin_or_anonymous(user: dict | None):
    if user and user["role"] != "admin":
        raise HTTPException(403, "Administrator access required.")
    return user


@router.get("/learning", response_model=LearningList)
def list_learning(user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    return {"items": db.list_fix_memory(user_id=_owner(user)).to_dict("records")}


@router.patch("/learning/{fix_id}", response_model=MessageResponse)
def rate_learning(fix_id: int, body: LearningRatingRequest, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    count = db.rate_fix_memory(memory_id=fix_id, rating=body.rating, user_id=_owner(user))
    if not count:
        raise HTTPException(404, "Learned fix not found.")
    return {"message": "Learning example rating saved."}


@router.patch("/learning/run/{run_id}", response_model=MessageResponse)
def rate_run_learning(run_id: str, body: LearningRatingRequest, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    count = db.rate_fix_memory(run_id=run_id, rating=body.rating, user_id=_owner(user))
    if not count:
        raise HTTPException(404, "No learned fix belongs to this run and account.")
    return {"message": "Learning example rating saved."}


@router.delete("/learning/{fix_id}", response_model=MessageResponse)
def delete_learning(fix_id: int, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    if not db.delete_fix_memory(fix_id, user_id=_owner(user)):
        raise HTTPException(404, "Learned fix not found.")
    return {"message": "Learned fix deleted."}


@router.delete("/learning", response_model=CountResponse)
def delete_all_learning(user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    return {"deleted": db.delete_fix_memory(user_id=_owner(user))}


@router.get("/learning/export", response_model=FilePayload)
def export_learning(format: str = "chat", approved_only: bool = False,
                    user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    if format not in ("chat", "prompt"):
        raise HTTPException(400, "Format must be chat or prompt.")
    df = db.list_fix_memory(user_id=_owner(user))
    from api.routers.runs import _file
    if format == "chat":
        return _file("testpilot_finetune_chat.jsonl", "application/jsonl", to_chat_jsonl(df, approved_only))
    return _file("testpilot_finetune_prompt.jsonl", "application/jsonl", to_prompt_jsonl(df, approved_only))


@router.get("/settings", response_model=SettingsResponse)
def read_settings(user: dict | None = Depends(get_current_user)):
    _admin_or_anonymous(user)
    s = Settings.load()
    ready, reason = s.llm_status()
    return {"settings": asdict(s), "llm_ready": ready, "llm_reason": reason}


@router.patch("/settings", response_model=SettingsResponse)
def update_settings(body: SettingsUpdateRequest, user: dict | None = Depends(get_current_user)):
    _admin_or_anonymous(user)
    s = Settings.load()
    values = body.model_dump(exclude_unset=True)
    if "provider" in values and values["provider"] not in PROVIDERS:
        raise HTTPException(400, "Unknown provider.")
    if "execution_mode" in values and values["execution_mode"] not in EXECUTION_MODES:
        raise HTTPException(400, "Unknown execution mode.")
    for key, val in values.items():
        setattr(s, key, val)
    s.clamp().save()
    ready, reason = s.llm_status()
    return {"settings": asdict(s), "llm_ready": ready, "llm_reason": reason}


@router.get("/settings/models", response_model=ModelList)
def models(user: dict | None = Depends(get_current_user)):
    _admin_or_anonymous(user)
    up, local_models = detect_ollama()
    providers = [{"id": "none", "label": PROVIDERS["none"]["label"]}]
    if up:
        providers.append({"id": "ollama", "label": PROVIDERS["ollama"]["label"]})
    for name, meta in PROVIDERS.items():
        if meta.get("env") and get_secret(meta["env"]):
            providers.append({"id": name, "label": meta["label"]})
    model_list = [{"provider": "ollama", "name": m, "hint": MODEL_HINTS.get(m, "Local model.")} for m in local_models]
    for name, meta in PROVIDERS.items():
        if any(p["id"] == name for p in providers) and name != "ollama":
            model_list.extend({"provider": name, "name": m, "hint": MODEL_HINTS.get(m, "")}
                              for m in meta["models"])
    return {"providers": providers, "models": model_list, "hints": MODEL_HINTS}


@router.get("/settings/availability", response_model=Availability)
def availability(user: dict | None = Depends(get_current_user)):
    _admin_or_anonymous(user)
    s = Settings.load()
    ollama_up, ollama_models = detect_ollama()
    return {"docker": sandbox_status(s), "ollama": {"available": ollama_up, "models": ollama_models}}


@router.post("/settings/test-connection", response_model=ConnectionTestResponse)
def test_connection(body: ConnectionTestRequest, user: dict | None = Depends(get_current_user)):
    _admin_or_anonymous(user)
    s = Settings.load()
    if body.provider:
        if body.provider not in PROVIDERS:
            raise HTTPException(400, "Unknown provider.")
        s.provider = body.provider
    if body.model is not None:
        s.model = body.model
    usable, why = s.llm_status()
    client = LLMClient.from_settings(s) if usable else None
    if client is None:
        return {"ok": False, "message": why}
    good, message = client.test_connection()
    return {"ok": good, "message": message}


@router.post("/settings/retention/purge", response_model=CountResponse)
def purge_old(body: PurgeRequest, user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _admin_or_anonymous(user)
    return {"deleted": db.purge_older_than(body.days, user_id=_owner(user))}


@router.post("/settings/retention/scrub-sources", response_model=CountResponse)
def scrub_sources(user: dict | None = Depends(get_current_user), db: Database = Depends(get_db)):
    _admin_or_anonymous(user)
    return {"scrubbed": db.scrub_retained_sources(user_id=_owner(user))}
