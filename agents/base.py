from __future__ import annotations

import time

from core.state import AgentRecord, ToolCall


class AgentFailure(Exception):
    """An expected, explainable failure (shown to the user as the agent's reason)."""


class AgentContext:
    def __init__(self, state, rec: AgentRecord, settings, db, llm, notify=None):
        self.state, self.rec, self.settings, self.db, self.llm = state, rec, settings, db, llm
        self.notify = notify or (lambda: None)

    def tool(self, name: str, summary: str, fn, *args, describe=None, **kwargs):
        """Run a tool, record it (with timing and result) on the agent's visible tool-call log."""
        t0 = time.perf_counter()
        try:
            out = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            self.rec.tool_calls.append(ToolCall(name, summary, f"{type(e).__name__}: {str(e)[:160]}", False, int((time.perf_counter() - t0) * 1000)).__dict__)
            self.notify()
            raise
        res = describe(out) if describe else "ok"
        self.rec.tool_calls.append(ToolCall(name, summary, str(res)[:200], True, int((time.perf_counter() - t0) * 1000)).__dict__)
        self.notify()
        return out

    def warn(self, msg: str) -> None:
        self.rec.warnings.append(msg)


class BaseAgent:
    key = ""
    name = ""

    def run(self, ctx: AgentContext) -> None:
        raise NotImplementedError
