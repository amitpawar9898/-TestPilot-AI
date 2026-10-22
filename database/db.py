"""SQLite persistence. Every statement is parameterized; one short-lived connection per call."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SCHEMA = Path(__file__).with_name("schema.sql")
ANY = "__any__"  # sentinel: do not filter by owner

RUN_COLUMNS = {
    "finished_at", "status", "verdict", "iterations", "isolation", "duration_ms",
    "stop_reason", "llm_provider", "llm_model", "objective", "requirements", "targets", "options_json",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA.read_text(encoding="utf-8"))
            cols = {r["name"] for r in c.execute("PRAGMA table_info(test_cases)")}
            for col in ("ctor", "setup"):  # databases created by an earlier version
                if col not in cols:
                    c.execute(f"ALTER TABLE test_cases ADD COLUMN {col} TEXT")
            for table in ("runs", "fix_memory"):
                if "user_id" not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")

    # ------------------------------------------------------------------ writes
    def get_or_create_project(self, name: str) -> int:
        name = (name or "Default project").strip()[:80] or "Default project"
        with self._conn() as c:
            row = c.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = c.execute("INSERT INTO projects (name, created_at) VALUES (?, ?)", (name, now_iso()))
            return cur.lastrowid

    def projects(self) -> list[str]:
        with self._conn() as c:
            return [r["name"] for r in c.execute("SELECT name FROM projects ORDER BY name")]

    def create_run(self, run_id: str, project: str, objective: str, requirements: str, targets: list[str],
                   options: dict, provider: str, model: str, user_id: int | None = None) -> None:
        pid = self.get_or_create_project(project)
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs (id, project_id, created_at, status, objective, requirements, targets,"
                " options_json, llm_provider, llm_model, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, pid, now_iso(), "running", objective, requirements, ", ".join(targets),
                 json.dumps(options), provider, model, user_id),
            )

    def update_run(self, run_id: str, **fields) -> None:
        cols = {k: v for k, v in fields.items() if k in RUN_COLUMNS}
        if not cols:
            return
        sets = ", ".join(f"{k} = ?" for k in cols)  # column names come from a fixed whitelist
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*cols.values(), run_id))

    def save_source(self, run_id: str, meta: dict, analysis: dict, content: str | None) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sources WHERE run_id = ?", (run_id,))
            c.execute(
                "INSERT INTO sources (run_id, filename, sha256, line_count, byte_count, function_count,"
                " class_count, retained, content, analysis_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, meta.get("filename"), meta.get("sha256"), meta.get("line_count"), meta.get("byte_count"),
                 meta.get("function_count"), meta.get("class_count"), 1 if content is not None else 0,
                 content, json.dumps(analysis, default=str)),
            )

    def add_event(self, run_id: str, rec: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_events (run_id, agent, iteration, status, task, input_summary, output_summary,"
                " tool_calls, errors, warnings, reason, started_at, ended_at, duration_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, rec["key"], rec.get("iteration", 1), rec["status"], rec.get("task", ""),
                 rec.get("input_summary", ""), rec.get("output_summary", ""),
                 json.dumps(rec.get("tool_calls", [])), json.dumps(rec.get("errors", [])),
                 json.dumps(rec.get("warnings", [])), rec.get("reason", ""), rec.get("started_at"),
                 rec.get("ended_at"), int(rec.get("duration_ms", 0))),
            )

    def replace_cases(self, run_id: str, cases: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM test_cases WHERE run_id = ?", (run_id,))
            c.executemany(
                "INSERT INTO test_cases (run_id, case_id, iteration, name, category, function, args, kwargs, ctor, setup,"
                " expect_json, rationale, source, oracle, enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, k["id"], k.get("iteration", 1), k.get("name"), k.get("category"), k.get("function"),
                  k.get("args"), k.get("kwargs"), k.get("ctor") or "()", json.dumps(k.get("setup") or []), json.dumps(k.get("expect", {})), k.get("rationale"),
                  k.get("source"), k.get("oracle"), 1 if k.get("enabled", True) else 0) for k in cases],
            )

    def replace_results(self, run_id: str, results: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM test_results WHERE run_id = ?", (run_id,))
            c.executemany(
                "INSERT INTO test_results (run_id, case_id, status, actual, detail, exception_type,"
                " exception_message, traceback, stdout, line, duration_ms, isolation, executed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r["case_id"], r["status"], r.get("actual", ""), r.get("detail", ""),
                  r.get("exception_type", ""), r.get("exception_message", ""), r.get("traceback", ""),
                  r.get("stdout", ""), r.get("line"), float(r.get("duration_ms", 0) or 0),
                  r.get("isolation", ""), r.get("executed_at", now_iso())) for r in results],
            )

    def replace_findings(self, run_id: str, findings: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM findings WHERE run_id = ?", (run_id,))
            c.executemany(
                "INSERT INTO findings (run_id, finding_id, category, title, description, severity, file, line,"
                " function, rule, evidence, expected, actual, suggested_fix, validation_status, source_tool,"
                " case_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, f["finding_id"], f["category"], f.get("title"), f.get("description"), f.get("severity"),
                  f.get("file"), f.get("line"), f.get("function"), f.get("rule"), f.get("evidence"),
                  f.get("expected"), f.get("actual"), f.get("suggested_fix"), f.get("validation_status"),
                  f.get("source_tool"), ",".join(f.get("case_ids", []))) for f in findings],
            )

    def save_report(self, run_id: str, report: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO reports (run_id, created_at, report_json) VALUES (?,?,?)"
                " ON CONFLICT(run_id) DO UPDATE SET created_at = excluded.created_at,"
                " report_json = excluded.report_json",
                (run_id, now_iso(), json.dumps(report, default=str)),
            )

    def save_improvement(self, run_id: str, payload: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO improvements (run_id, created_at, payload_json) VALUES (?,?,?)"
                      " ON CONFLICT(run_id) DO UPDATE SET created_at=excluded.created_at, payload_json=excluded.payload_json",
                      (run_id, now_iso(), json.dumps(payload, default=str)))

    def delete_run(self, run_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def purge_older_than(self, days: int, user_id=ANY) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as c:
            if user_id == ANY:
                return c.execute("DELETE FROM runs WHERE created_at < ?", (cutoff,)).rowcount
            return c.execute("DELETE FROM runs WHERE created_at < ? AND user_id IS ?", (cutoff, user_id)).rowcount

    def scrub_retained_sources(self, user_id=ANY) -> int:
        with self._conn() as c:
            if user_id == ANY:
                return c.execute("UPDATE sources SET content = NULL, retained = 0 WHERE content IS NOT NULL").rowcount
            return c.execute("UPDATE sources SET content = NULL, retained = 0 WHERE content IS NOT NULL AND run_id IN"
                             " (SELECT id FROM runs WHERE user_id IS ?)", (user_id,)).rowcount

    # ------------------------------------------------------------------- reads
    def _df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with self._conn() as c:
            return pd.read_sql_query(sql, c, params=params)

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params)]

    def get_run(self, run_id: str, user_id=ANY) -> dict | None:
        rows = self._rows(
            "SELECT r.*, p.name AS project, s.filename, s.sha256, s.line_count, s.function_count, s.retained"
            " FROM runs r JOIN projects p ON p.id = r.project_id LEFT JOIN sources s ON s.run_id = r.id"
            " WHERE r.id = ?", (run_id,))
        if rows and user_id != ANY and rows[0]["user_id"] != user_id:
            return None  # someone else's run
        return rows[0] if rows else None

    def list_runs(self, search: str = "", status: str | None = None, verdict: str | None = None,
                  since: str | None = None, limit: int = 500, user_id=None) -> pd.DataFrame:
        sql = ("SELECT r.id, p.name AS project, r.created_at, r.status, r.verdict, r.objective, r.iterations,"
               " r.isolation, r.duration_ms, s.filename,"
               " (SELECT COUNT(*) FROM test_cases t WHERE t.run_id = r.id) AS tests,"
               " (SELECT COUNT(*) FROM test_results x WHERE x.run_id = r.id AND x.status = 'passed') AS passed,"
               " (SELECT COUNT(*) FROM test_results x WHERE x.run_id = r.id AND x.status = 'failed') AS failed,"
               " (SELECT COUNT(*) FROM test_results x WHERE x.run_id = r.id AND x.status IN ('error','timeout')) AS errors,"
               " (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.id) AS findings"
               " FROM runs r JOIN projects p ON p.id = r.project_id LEFT JOIN sources s ON s.run_id = r.id WHERE r.user_id IS ?")
        params: list = [user_id]
        if search:
            sql += " AND (r.id LIKE ? OR p.name LIKE ? OR r.objective LIKE ? OR s.filename LIKE ?)"
            params += [f"%{search}%"] * 4
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        if verdict:
            sql += " AND r.verdict = ?"
            params.append(verdict)
        if since:
            sql += " AND r.created_at >= ?"
            params.append(since)
        sql += " ORDER BY r.created_at DESC LIMIT ?"
        params.append(limit)
        return self._df(sql, tuple(params))

    def get_cases(self, run_id: str) -> list[dict]:
        out = []
        for r in self._rows("SELECT * FROM test_cases WHERE run_id = ? ORDER BY id", (run_id,)):
            out.append({"id": r["case_id"], "iteration": r["iteration"], "name": r["name"], "category": r["category"],
                        "function": r["function"], "args": r["args"], "kwargs": r["kwargs"], "ctor": r["ctor"] or "()", "setup": json.loads(r["setup"] or "[]"),
                        "expect": json.loads(r["expect_json"] or "{}"), "rationale": r["rationale"],
                        "source": r["source"], "oracle": r["oracle"], "enabled": bool(r["enabled"])})
        return out

    def get_results(self, run_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM test_results WHERE run_id = ? ORDER BY id", (run_id,))
        for r in rows:
            r["case_id"] = r["case_id"]
        return rows

    def get_findings(self, run_id: str | None = None, user_id=None) -> pd.DataFrame:
        sql = ("SELECT f.*, r.created_at AS run_created FROM findings f JOIN runs r ON r.id = f.run_id")
        if run_id:
            return self._df(sql + " WHERE f.run_id = ? ORDER BY f.id", (run_id,))
        return self._df(sql + " WHERE r.user_id IS ? ORDER BY r.created_at DESC, f.id", (user_id,))

    def get_report(self, run_id: str) -> dict | None:
        rows = self._rows("SELECT report_json FROM reports WHERE run_id = ?", (run_id,))
        return json.loads(rows[0]["report_json"]) if rows else None

    def get_improvement(self, run_id: str) -> dict | None:
        rows = self._rows("SELECT payload_json FROM improvements WHERE run_id = ?", (run_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def get_analysis(self, run_id: str) -> dict:
        rows = self._rows("SELECT analysis_json FROM sources WHERE run_id = ?", (run_id,))
        return json.loads(rows[0]["analysis_json"]) if rows and rows[0]["analysis_json"] else {}

    def get_retained_source(self, run_id: str) -> str | None:
        rows = self._rows("SELECT content FROM sources WHERE run_id = ?", (run_id,))
        return rows[0]["content"] if rows else None

    def get_events(self, run_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM agent_events WHERE run_id = ? ORDER BY id", (run_id,))
        for r in rows:
            for k in ("tool_calls", "errors", "warnings"):
                r[k] = json.loads(r[k] or "[]")
        return rows

    def recent_events(self, limit: int = 12, user_id=None) -> pd.DataFrame:
        return self._df(
            "SELECT e.*, r.created_at AS run_created FROM agent_events e JOIN runs r ON r.id = e.run_id"
            " WHERE r.user_id IS ? AND e.status IN ('completed','failed','blocked','needs_review') ORDER BY e.id DESC LIMIT ?", (user_id, limit))

    def timeline(self, run_id: str) -> pd.DataFrame:
        return self._df(
            "SELECT agent, iteration, status, started_at, ended_at, duration_ms FROM agent_events"
            " WHERE run_id = ? AND ended_at IS NOT NULL ORDER BY id", (run_id,))

    def metrics(self, user_id=None) -> dict:
        with self._conn() as c:
            one = lambda sql: c.execute(sql, (user_id,)).fetchone()[0] or 0  # noqa: E731 - constant SQL, owner bound as a parameter
            counts = {r["status"]: r["n"] for r in c.execute(
                "SELECT x.status, COUNT(*) AS n FROM test_results x JOIN runs r ON r.id = x.run_id WHERE r.user_id IS ? GROUP BY x.status", (user_id,))}
            sev = {r["severity"]: r["n"] for r in c.execute(
                "SELECT f.severity, COUNT(*) AS n FROM findings f JOIN runs r ON r.id = f.run_id WHERE r.user_id IS ? GROUP BY f.severity", (user_id,))}
            return {
                "runs": one("SELECT COUNT(*) FROM runs WHERE user_id IS ?"),
                "cases": one("SELECT COUNT(*) FROM test_cases t JOIN runs r ON r.id = t.run_id WHERE r.user_id IS ?"),
                "passed": counts.get("passed", 0), "failed": counts.get("failed", 0),
                "errors": counts.get("error", 0) + counts.get("timeout", 0), "not_run": counts.get("not_run", 0),
                "findings": one("SELECT COUNT(*) FROM findings f JOIN runs r ON r.id = f.run_id WHERE r.user_id IS ?"),
                "outcomes": counts, "severity": sev,
            }

    def runs_trend(self, limit: int = 30, user_id=None) -> pd.DataFrame:
        return self._df(
            "SELECT r.id, r.created_at,"
            " SUM(CASE WHEN x.status = 'passed' THEN 1 ELSE 0 END) AS passed,"
            " SUM(CASE WHEN x.status IN ('passed','failed','error','timeout') THEN 1 ELSE 0 END) AS executed"
            " FROM runs r JOIN test_results x ON x.run_id = r.id WHERE r.user_id IS ? GROUP BY r.id ORDER BY r.created_at DESC LIMIT ?",
            (user_id, limit),
        ).sort_values("created_at")

    # ------------------------------------------------------------ fix memory
    def add_fix_memory(self, run_id: str, function: str, problem: str, before: str, after: str, provider: str = "", model: str = "", user_id=None) -> None:
        with self._conn() as c:
            dup = c.execute("SELECT id FROM fix_memory WHERE before_code = ? AND after_code = ? AND user_id IS ?", (before, after, user_id)).fetchone()
            if dup:
                return
            c.execute("INSERT INTO fix_memory (created_at, run_id, function_name, problem, before_code, after_code, provider, model, user_id)"
                      " VALUES (?,?,?,?,?,?,?,?,?)", (now_iso(), run_id, function, problem[:1500], before[:6000], after[:6000], provider, model, user_id))

    def list_fix_memory(self, include_rejected: bool = True, user_id=None) -> pd.DataFrame:
        sql = "SELECT * FROM fix_memory WHERE user_id IS ?" + ("" if include_rejected else " AND rating >= 0") + " ORDER BY id DESC"
        return self._df(sql, (user_id,))

    def rate_fix_memory(self, memory_id: int | None = None, rating: int = 0, run_id: str | None = None, user_id=None) -> int:
        with self._conn() as c:
            if run_id is not None:
                return c.execute("UPDATE fix_memory SET rating = ? WHERE run_id = ? AND user_id IS ?", (rating, run_id, user_id)).rowcount
            return c.execute("UPDATE fix_memory SET rating = ? WHERE id = ? AND user_id IS ?", (rating, memory_id, user_id)).rowcount

    def delete_fix_memory(self, memory_id: int | None = None, user_id=None) -> int:
        with self._conn() as c:
            if memory_id is None:
                return c.execute("DELETE FROM fix_memory WHERE user_id IS ?", (user_id,)).rowcount
            return c.execute("DELETE FROM fix_memory WHERE id = ? AND user_id IS ?", (memory_id, user_id)).rowcount

    def similar_fixes(self, query: str, k: int = 3, user_id=None) -> list[dict]:
        """Token-overlap (Jaccard) retrieval over non-rejected memories. Deterministic, local, no embeddings."""
        tok = lambda s: set(re.findall(r"[A-Za-z_]{3,}", (s or "").lower()))  # noqa: E731
        q = tok(query)
        if not q:
            return []
        scored = []
        for r in self._rows("SELECT * FROM fix_memory WHERE rating >= 0 AND user_id IS ? ORDER BY id DESC LIMIT 500", (user_id,)):
            d = tok(r["problem"]) | tok(r["before_code"])
            score = len(q & d) / max(1, len(q | d)) + (0.05 if r["rating"] > 0 else 0)
            if score > 0.08:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        top = [r for _, r in scored[:k]]
        if top:
            with self._conn() as c:
                c.executemany("UPDATE fix_memory SET times_used = times_used + 1 WHERE id = ?", [(r["id"],) for r in top])
        return top

    # ----------------------------------------------------------------- users
    def create_user(self, username: str, password_hash: str, role: str = "user") -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                             (username, password_hash, role, now_iso())).lastrowid

    def count_users(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user(self, username: str) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE username = ?", (username,))
        return rows[0] if rows else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None

    def list_users(self) -> pd.DataFrame:
        return self._df("SELECT u.id, u.username, u.role, u.created_at, u.last_login, u.locked_until,"
                        " (SELECT COUNT(*) FROM runs r WHERE r.user_id = u.id) AS runs FROM users u ORDER BY u.id")

    def record_failed_login(self, user_id: int, max_fails: int, lock_until: str) -> int:
        with self._conn() as c:
            c.execute("UPDATE users SET failed_count = failed_count + 1 WHERE id = ?", (user_id,))
            n = c.execute("SELECT failed_count FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            if n >= max_fails:
                c.execute("UPDATE users SET locked_until = ?, failed_count = 0 WHERE id = ?", (lock_until, user_id))
            return n

    def record_login(self, user_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET failed_count = 0, locked_until = NULL, last_login = ? WHERE id = ?", (now_iso(), user_id))

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET password_hash = ?, failed_count = 0, locked_until = NULL WHERE id = ?", (password_hash, user_id))

    def set_role(self, user_id: int, role: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    def count_admins(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]

    def delete_user(self, user_id: int) -> None:
        with self._conn() as c:  # runs, findings, tests and learned fixes of this user are removed by ON DELETE CASCADE
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def create_api_session(self, token_hash: str, user_id: int, expires_at: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO api_sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                      (token_hash, user_id, now_iso(), expires_at))

    def get_api_session_user(self, token_hash: str) -> dict | None:
        rows = self._rows("SELECT u.id, u.username, u.role FROM api_sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ? AND s.expires_at > ?", (token_hash, now_iso()))
        return rows[0] if rows else None

    def delete_api_session(self, token_hash: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM api_sessions WHERE token_hash = ?", (token_hash,))
