PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',      -- 'admin' | 'user'
    created_at TEXT NOT NULL,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login TEXT
);

-- Revocable API sessions; only a SHA-256 digest of the random token is stored.
CREATE TABLE IF NOT EXISTS api_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    verdict TEXT DEFAULT '',
    objective TEXT DEFAULT '',
    requirements TEXT DEFAULT '',
    targets TEXT DEFAULT '',
    options_json TEXT DEFAULT '{}',
    iterations INTEGER DEFAULT 1,
    llm_provider TEXT DEFAULT 'none',
    llm_model TEXT DEFAULT '',
    isolation TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    stop_reason TEXT DEFAULT '',
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE   -- NULL = anonymous (no login)
);

-- Source metadata. The code itself is stored only when retention is enabled in Settings.
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    filename TEXT,
    sha256 TEXT,
    line_count INTEGER,
    byte_count INTEGER,
    function_count INTEGER,
    class_count INTEGER,
    retained INTEGER NOT NULL DEFAULT 0,
    content TEXT,
    analysis_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    task TEXT, input_summary TEXT, output_summary TEXT,
    tool_calls TEXT, errors TEXT, warnings TEXT, reason TEXT,
    started_at TEXT, ended_at TEXT, duration_ms INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    iteration INTEGER DEFAULT 1,
    name TEXT, category TEXT, function TEXT,
    args TEXT, kwargs TEXT, ctor TEXT, setup TEXT, expect_json TEXT,
    rationale TEXT, source TEXT, oracle TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (run_id, case_id)
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    actual TEXT, detail TEXT,
    exception_type TEXT, exception_message TEXT, traceback TEXT, stdout TEXT,
    line INTEGER, duration_ms REAL DEFAULT 0, isolation TEXT, executed_at TEXT,
    UNIQUE (run_id, case_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT, description TEXT, severity TEXT,
    file TEXT, line INTEGER, function TEXT, rule TEXT,
    evidence TEXT, expected TEXT, actual TEXT, suggested_fix TEXT,
    validation_status TEXT, source_tool TEXT, case_ids TEXT,
    UNIQUE (run_id, finding_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    report_json TEXT NOT NULL
);

-- Improvement artifacts are separate from the optional original-source retention setting.
CREATE TABLE IF NOT EXISTS improvements (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_events_run ON agent_events(run_id);
CREATE INDEX IF NOT EXISTS idx_results_run ON test_results(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);

-- Verified fixes the user opted to keep locally ("fix memory"): few-shot examples and fine-tuning data.
CREATE TABLE IF NOT EXISTS fix_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    run_id TEXT,
    function_name TEXT,
    problem TEXT,
    before_code TEXT NOT NULL,
    after_code TEXT NOT NULL,
    provider TEXT, model TEXT,
    rating INTEGER NOT NULL DEFAULT 0,      -- 1 approved, 0 unrated, -1 rejected
    times_used INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
);
