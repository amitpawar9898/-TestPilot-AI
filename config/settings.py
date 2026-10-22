"""Settings. Non-secret values live in data/settings.json; secrets come from server environment / .env only."""
from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import urlopen
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("TESTPILOT_DATA_DIR", str(BASE_DIR / "data")))
REPORTS_DIR = Path(os.getenv("TESTPILOT_REPORTS_DIR", str(BASE_DIR / "reports")))
DB_PATH = DATA_DIR / "testpilot.db"
SETTINGS_FILE = DATA_DIR / "settings.json"
APP_NAME = "TestPilot AI"
APP_VERSION = "1.0.0"

PROVIDERS: dict[str, dict] = {
    "none": {"label": "Deterministic engine (no LLM, free)", "env": None, "models": [], "base_url": "", "local": True},
    "anthropic": {
        "label": "Anthropic Claude",
        "env": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
        "base_url": "https://api.anthropic.com",
        "local": False,
    },
    "openai": {
        "label": "OpenAI API (GPT and Codex models)",
        "env": "OPENAI_API_KEY",
        "models": ["gpt-5.3-codex", "gpt-5.1-codex", "gpt-4.1", "gpt-4o-mini"],
        "base_url": "https://api.openai.com/v1",
        "local": False,
    },
    "ollama": {
        "label": "Ollama (local model)",
        "env": None,
        "models": ["llama3.1", "qwen2.5-coder", "mistral"],
        "base_url": "http://localhost:11434/v1",
        "local": True,
    },
    "openai_compatible": {
        "label": "OpenAI-compatible endpoint (Groq, OpenRouter, ...)",
        "env": "OPENAI_COMPATIBLE_API_KEY",
        "models": [],
        "base_url": "",
        "local": False,
    },
}

MODEL_HINTS = {
    "claude-sonnet-5": "Current Claude model for code review and fixes.",
    "claude-opus-5": "Higher-capability Claude model; may be slower or costlier.",
    "claude-haiku-4-5-20251001": "Fast Claude model for simpler fixes.",
    "gpt-5.3-codex": "OpenAI Codex API model; requires an OpenAI API key and API billing.",
    "gpt-5.1-codex": "OpenAI Codex model for code tasks.",
}

EXECUTION_MODES = {
    "docker": "Docker container (recommended, isolated)",
    "local_trusted": "Local process (trusted code only, NOT a sandbox)",
    "disabled": "Disabled (analysis and test generation only)",
}


@dataclass
class Settings:
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    llm_consent: bool = False  # explicit consent before source is sent to a remote provider
    execution_mode: str = "docker"
    docker_image: str = "python:3.12-slim"
    per_test_timeout: int = 5
    total_timeout: int = 60
    memory_mb: int = 256
    max_iterations: int = 2
    max_tests_per_function: int = 14
    retain_source: bool = False
    retention_days: int = 90
    require_login: bool = False  # when True every page needs a signed-in account
    allow_signup: bool = True
    learn_from_fixes: bool = False  # opt-in: keep verified fixes locally as examples / fine-tuning data
    use_fix_memory: bool = True
    report_include_tests: bool = True
    report_include_ai_notes: bool = True

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or SETTINGS_FILE
        s = cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for f in fields(cls):
            if f.name in raw and isinstance(raw[f.name], type(getattr(s, f.name))):
                setattr(s, f.name, raw[f.name])

        # Explicit deployment configuration takes precedence over persisted UI settings.
        provider = os.getenv("TESTPILOT_PROVIDER", "").strip().lower()
        if provider in PROVIDERS:
            s.provider = provider
        model = os.getenv("TESTPILOT_MODEL", "").strip()
        if model:
            s.model = model
        base_url = os.getenv("TESTPILOT_BASE_URL", "" ).strip()
        if base_url:
            s.base_url = base_url
        consent = os.getenv("TESTPILOT_LLM_CONSENT")
        if consent is not None:
            s.llm_consent = consent.strip() == "1"
        return s.clamp()

    def save(self, path: Path | None = None) -> None:
        path = Path(path or SETTINGS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self.clamp()), indent=2), encoding="utf-8")

    def clamp(self) -> "Settings":
        self.per_test_timeout = max(1, min(int(self.per_test_timeout), 60))
        self.total_timeout = max(5, min(int(self.total_timeout), 600))
        self.memory_mb = max(64, min(int(self.memory_mb), 4096))
        self.max_iterations = max(1, min(int(self.max_iterations), 5))
        self.max_tests_per_function = max(3, min(int(self.max_tests_per_function), 60))
        self.retention_days = max(1, min(int(self.retention_days), 3650))
        if self.provider not in PROVIDERS:
            self.provider = "none"
        if self.execution_mode not in EXECUTION_MODES:
            self.execution_mode = "docker"
        return self

    # ---- LLM helpers ---------------------------------------------------
    def effective_model(self) -> str:
        if self.model:
            return self.model
        models = PROVIDERS[self.provider]["models"]
        return models[0] if models else ""

    def effective_base_url(self) -> str:
        return (self.base_url or PROVIDERS[self.provider]["base_url"]).rstrip("/")

    def llm_status(self) -> tuple[bool, str]:
        """(usable, human-readable reason)."""
        if self.provider == "none":
            return False, "No LLM selected. The deterministic engine is used."
        meta = PROVIDERS[self.provider]
        if not meta["local"] and not self.llm_consent:
            return False, "Consent required: source code is sent to the provider. Enable it in Settings."
        if self.provider == "ollama":
            health_url = self.effective_base_url().removesuffix("/v1").rstrip("/") + "/api/tags"
            try:
                with urlopen(health_url, timeout=0.5):
                    pass
            except (URLError, TimeoutError, OSError):
                return False, "Local Ollama is unavailable. Start Ollama and pull the configured model."
        if meta["env"] and not get_secret(meta["env"]):
            return False, f"Missing {meta['env']}. Add it to the server's .env file."
        if not self.effective_base_url():
            return False, "Base URL is required for this provider."
        if not self.effective_model():
            return False, "Model name is required for this provider."
        return True, f"{meta['label']} / {self.effective_model()}"


def get_secret(name: str) -> str:
    """Read a server-side environment variable loaded from .env; never logged or persisted."""
    return os.environ.get(name, "").strip()


def api_key_for(provider: str) -> str:
    env = PROVIDERS.get(provider, {}).get("env")
    return get_secret(env) if env else ""


def redact(text: str) -> str:
    """Remove any configured API key from a string before it is shown or logged."""
    out = str(text)
    for meta in PROVIDERS.values():
        env = meta.get("env")
        key = get_secret(env) if env else ""
        if key and len(key) > 6:
            out = out.replace(key, "***")
    return out


def unsafe_local_allowed() -> bool:
    return os.environ.get("TESTPILOT_ALLOW_UNSAFE_LOCAL", "") == "1"


def login_required(s: "Settings") -> bool:
    return bool(s.require_login) or os.environ.get("TESTPILOT_REQUIRE_LOGIN", "") == "1"
