"""Schema definitions — DDL strings và version management cho SQLite persistence layer."""

# Schema version hiện tại. Tăng khi có thay đổi DDL.
CURRENT_VERSION = 1

# --- DDL: Schema version tracking ---

DDL_SCHEMA_VERSION = """\
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);
"""

# --- DDL: Outlook combo state ---

DDL_OUTLOOK_COMBOS = """\
CREATE TABLE IF NOT EXISTS outlook_combos (
    email TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    client_id TEXT NOT NULL,
    used_for_signup INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_failed_at TEXT,
    used_at TEXT,
    last_refresh_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# --- DDL: Jobs (web UI) ---

DDL_JOBS = """\
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    combo TEXT NOT NULL,
    mail_mode TEXT NOT NULL DEFAULT 'outlook'
        CHECK(mail_mode IN ('outlook', 'worker', 'gmail_advanced')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued', 'running', 'success', 'error', 'cancelled')),
    error TEXT,
    password TEXT,
    secret TEXT,
    first_code TEXT,
    user_id TEXT,
    session_path TEXT,
    payment_link TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    job_type TEXT NOT NULL DEFAULT 'signup'
);
"""

DDL_JOBS_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_email ON jobs(email);
"""

# --- DDL: Job logs ---

DDL_JOB_LOGS = """\
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    line TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""

DDL_JOB_LOGS_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);
"""

# --- DDL: Session results ---

DDL_SESSION_RESULTS = """\
CREATE TABLE IF NOT EXISTS session_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    password TEXT,
    name TEXT,
    age INTEGER,
    user_id TEXT,
    account_id TEXT,
    session_token TEXT,
    access_token TEXT,
    cookies TEXT,
    two_factor TEXT,
    phase1_seconds REAL,
    phase2_seconds REAL,
    otp_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_SESSION_RESULTS_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_session_results_email ON session_results(email);
"""

# --- Ordered list tất cả DDL statements cho migration ---

ALL_DDL: list[str] = [
    DDL_SCHEMA_VERSION,
    DDL_OUTLOOK_COMBOS,
    DDL_JOBS,
    DDL_JOBS_INDEXES,
    DDL_JOB_LOGS,
    DDL_JOB_LOGS_INDEXES,
    DDL_SESSION_RESULTS,
    DDL_SESSION_RESULTS_INDEXES,
]
"""Danh sách DDL theo thứ tự thực thi. Engine sẽ chạy lần lượt trong 1 transaction."""
