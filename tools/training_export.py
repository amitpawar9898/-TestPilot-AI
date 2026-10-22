"""Turn verified fixes into fine-tuning data. Only fixes that were re-tested (verified) are ever stored in fix memory."""
from __future__ import annotations

import json

import pandas as pd

SYSTEM = "You fix bugs in Python functions. Reply with only the complete corrected function."


def _user(row) -> str:
    return f"Problem evidence:\n{row['problem'] or '(none)'}\n\nFunction:\n{row['before_code']}"


def to_chat_jsonl(df: pd.DataFrame, approved_only: bool = False) -> str:
    """OpenAI-style chat fine-tuning format (also accepted by many open-source trainers)."""
    rows = df[df["rating"] > 0] if approved_only else df[df["rating"] >= 0]
    return "\n".join(json.dumps({"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _user(r)},
                                              {"role": "assistant", "content": r["after_code"]}]}) for _, r in rows.iterrows())


def to_prompt_jsonl(df: pd.DataFrame, approved_only: bool = False) -> str:
    rows = df[df["rating"] > 0] if approved_only else df[df["rating"] >= 0]
    return "\n".join(json.dumps({"prompt": SYSTEM + "\n\n" + _user(r), "completion": r["after_code"]}) for _, r in rows.iterrows())
