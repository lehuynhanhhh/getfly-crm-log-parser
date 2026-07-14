
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


DB_PATH = Path("data/crm_logs.db")

TABLE_MAP = {
    "logs": "crm_logs_v2",
    "financial_events": "financial_events",
    "service_inventory": "service_inventory",
    "customer_mentions": "customer_mentions",
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_code TEXT PRIMARY KEY,
    customer_name TEXT,
    branch TEXT,
    getfly_url TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS crm_logs_v2 (
    log_key TEXT PRIMARY KEY,
    customer_code TEXT,
    customer_name TEXT,
    logged_at TEXT,
    payload_json TEXT NOT NULL,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS financial_events (
    event_id TEXT PRIMARY KEY,
    log_key TEXT,
    customer_code TEXT,
    logged_at TEXT,
    event_type TEXT,
    amount_vnd REAL,
    payload_json TEXT NOT NULL,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS service_inventory (
    event_id TEXT PRIMARY KEY,
    log_key TEXT,
    customer_code TEXT,
    logged_at TEXT,
    profile_codes TEXT,
    service_name TEXT,
    payload_json TEXT NOT NULL,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customer_mentions (
    mention_id TEXT PRIMARY KEY,
    log_key TEXT,
    customer_code TEXT,
    mentioned_customer_code TEXT,
    role_in_log TEXT,
    payload_json TEXT NOT NULL,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_crm_logs_v2_customer ON crm_logs_v2(customer_code);
CREATE INDEX IF NOT EXISTS idx_financial_customer ON financial_events(customer_code);
CREATE INDEX IF NOT EXISTS idx_inventory_customer ON service_inventory(customer_code);
CREATE INDEX IF NOT EXISTS idx_mentions_customer ON customer_mentions(customer_code);
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    display_name TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    failed_login_count INTEGER DEFAULT 0,
    locked_until TEXT,
    password_changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    ip_address TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    last_activity TEXT DEFAULT CURRENT_TIMESTAMP,
    is_revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_audit_log (
    id TEXT PRIMARY KEY,
    email TEXT,
    action TEXT,
    details TEXT,
    ip_address TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_email ON auth_sessions(email);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_audit_email ON auth_audit_log(email);
"""


def _is_pg() -> bool:
    return bool(os.environ.get("DATABASE_URL", ""))


def _pg_conn_str() -> str:
    return os.environ["DATABASE_URL"]


def _connect(db_path: Path = DB_PATH):
    if _is_pg():
        import psycopg2
        return psycopg2.connect(_pg_conn_str())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = __import__("sqlite3").connect(str(db_path))
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _param(count: int = 1) -> str:
    if _is_pg():
        return ",".join("%s" for _ in range(count))
    return ",".join("?" for _ in range(count))


def _execute(conn, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> Any:
    if _is_pg():
        sql = sql.replace("?", "%s")
    if params is None:
        return conn.execute(sql)
    return conn.execute(sql, params)


def _table_columns(conn, table_name: str) -> set[str]:
    if _is_pg():
        rows = _execute(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s",
            [table_name],
        ).fetchall()
        return {row[0] for row in rows}
    rows = _execute(conn, f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _init_schema(conn) -> None:
    for statement in _SCHEMA_SQL.strip().split(";"):
        stmt = statement.strip()
        if stmt:
            _execute(conn, stmt)


def init_db(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        _init_schema(conn)
        _migrate_users(conn)
        _seed_admin(conn)
    clean_expired_sessions(db_path)


def _migrate_users(conn) -> None:
    user_migrations = [
        ("role", "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'"),
        ("display_name", "ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''"),
        ("is_active", "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1"),
        ("failed_login_count", "ALTER TABLE users ADD COLUMN failed_login_count INTEGER DEFAULT 0"),
        ("locked_until", "ALTER TABLE users ADD COLUMN locked_until TEXT"),
        ("password_changed_at", "ALTER TABLE users ADD COLUMN password_changed_at TEXT DEFAULT CURRENT_TIMESTAMP"),
        ("updated_at", "ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP"),
    ]
    for col, sql in user_migrations:
        try:
            _execute(conn, sql)
        except Exception:
            pass


def seed_admin(db_path: Path = DB_PATH) -> None:
    init_db(db_path)


def _seed_admin(conn) -> None:
    import bcrypt
    exists = _execute(
        conn, "SELECT 1 FROM users WHERE email = ?", ("admin@driphydration.vn",)
    ).fetchone()
    if not exists:
        pw_hash = bcrypt.hashpw(b"Financeteam@123", bcrypt.gensalt()).decode()
        _execute(
            conn,
            "INSERT INTO users (email, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ("admin@driphydration.vn", pw_hash, "admin", "Admin"),
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    if pd.isna(value):
        return None
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _row_payload(row: pd.Series) -> str:
    return json.dumps(
        {str(column): _json_value(value) for column, value in row.items()},
        ensure_ascii=False,
    )


def _delete_children_for_logs(conn, log_keys: list[str]) -> None:
    if not log_keys:
        return
    placeholders = _param(len(log_keys))
    for table in ("financial_events", "service_inventory", "customer_mentions"):
        _execute(
            conn, f"DELETE FROM {table} WHERE log_key IN ({placeholders})", log_keys
        )


def save_bundle(
    bundle: Mapping[str, Any],
    metadata: Mapping[str, str],
    db_path: Path = DB_PATH,
) -> tuple[int, int]:
    init_db(db_path)

    logs = bundle.get("logs", pd.DataFrame())
    financial = bundle.get("financial_events", pd.DataFrame())
    inventory = bundle.get("service_inventory", pd.DataFrame())
    mentions = bundle.get("customer_mentions", pd.DataFrame())

    if not isinstance(logs, pd.DataFrame) or logs.empty:
        return 0, 0

    customer_code = str(
        metadata.get("customer_code")
        or logs.iloc[0].get("Mã KH chính", "")
    ).strip().upper()
    customer_name = str(
        metadata.get("customer_name")
        or logs.iloc[0].get("Tên khách hàng chính", "")
    ).strip()

    if not customer_code:
        raise ValueError("Cần có Mã khách hàng chính trước khi lưu database.")

    log_keys = logs["Log key"].astype(str).tolist()
    inserted = 0
    updated = 0

    with _connect(db_path) as conn:
        _execute(
            conn,
            """
            INSERT INTO customers (
                customer_code, customer_name, branch, getfly_url, updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(customer_code) DO UPDATE SET
                customer_name = excluded.customer_name,
                branch = excluded.branch,
                getfly_url = excluded.getfly_url,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                customer_code,
                customer_name,
                str(metadata.get("branch", "")),
                str(metadata.get("getfly_url", "")),
            ),
        )

        existing_keys = {
            row[0]
            for row in _execute(
                conn,
                f"SELECT log_key FROM crm_logs_v2 WHERE log_key IN ({_param(len(log_keys))})",
                log_keys,
            ).fetchall()
        } if log_keys else set()

        _delete_children_for_logs(conn, log_keys)

        for _, row in logs.iterrows():
            log_key = str(row.get("Log key", ""))
            logged_at = pd.to_datetime(
                row.get("Thời gian ghi nhận"), errors="coerce"
            )
            _execute(
                conn,
                """
                INSERT INTO crm_logs_v2 (
                    log_key, customer_code, customer_name, logged_at,
                    payload_json, imported_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(log_key) DO UPDATE SET
                    customer_code = excluded.customer_code,
                    customer_name = excluded.customer_name,
                    logged_at = excluded.logged_at,
                    payload_json = excluded.payload_json,
                    imported_at = CURRENT_TIMESTAMP
                """,
                (
                    log_key,
                    customer_code,
                    customer_name,
                    None if pd.isna(logged_at) else logged_at.isoformat(),
                    _row_payload(row),
                ),
            )
            if log_key in existing_keys:
                updated += 1
            else:
                inserted += 1

        if isinstance(financial, pd.DataFrame):
            for _, row in financial.iterrows():
                logged_at = pd.to_datetime(
                    row.get("Thời gian ghi nhận"), errors="coerce"
                )
                amount = pd.to_numeric(row.get("Số tiền (VND)"), errors="coerce")
                _execute(
                    conn,
                    """
                    INSERT INTO financial_events (
                        event_id, log_key, customer_code, logged_at,
                        event_type, amount_vnd, payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(event_id) DO UPDATE SET
                        log_key = excluded.log_key,
                        customer_code = excluded.customer_code,
                        logged_at = excluded.logged_at,
                        event_type = excluded.event_type,
                        amount_vnd = excluded.amount_vnd,
                        payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(row.get("Financial event ID", "")),
                        str(row.get("Log key", "")),
                        customer_code,
                        None if pd.isna(logged_at) else logged_at.isoformat(),
                        str(row.get("Loại sự kiện tài chính", "")),
                        None if pd.isna(amount) else float(amount),
                        _row_payload(row),
                    ),
                )

        if isinstance(inventory, pd.DataFrame):
            for _, row in inventory.iterrows():
                logged_at = pd.to_datetime(
                    row.get("Thời gian ghi nhận"), errors="coerce"
                )
                _execute(
                    conn,
                    """
                    INSERT INTO service_inventory (
                        event_id, log_key, customer_code, logged_at,
                        profile_codes, service_name, payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(event_id) DO UPDATE SET
                        log_key = excluded.log_key,
                        customer_code = excluded.customer_code,
                        logged_at = excluded.logged_at,
                        profile_codes = excluded.profile_codes,
                        service_name = excluded.service_name,
                        payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(row.get("Inventory event ID", "")),
                        str(row.get("Log key", "")),
                        customer_code,
                        None if pd.isna(logged_at) else logged_at.isoformat(),
                        str(row.get("Mã hồ sơ HS", "")),
                        str(row.get("Dịch vụ/Gói còn lại", "")),
                        _row_payload(row),
                    ),
                )

        if isinstance(mentions, pd.DataFrame):
            for _, row in mentions.iterrows():
                _execute(
                    conn,
                    """
                    INSERT INTO customer_mentions (
                        mention_id, log_key, customer_code,
                        mentioned_customer_code, role_in_log,
                        payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(mention_id) DO UPDATE SET
                        log_key = excluded.log_key,
                        customer_code = excluded.customer_code,
                        mentioned_customer_code = excluded.mentioned_customer_code,
                        role_in_log = excluded.role_in_log,
                        payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(row.get("Mention ID", "")),
                        str(row.get("Log key", "")),
                        customer_code,
                        str(row.get("Mã KH được nhắc", "")),
                        str(row.get("Vai trò trong log", "")),
                        _row_payload(row),
                    ),
                )

    return inserted, updated


def _load_json_rows(
    table_name: str,
    customer_code: str = "",
    limit: int = 5000,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    init_db(db_path)
    customer_code = customer_code.strip().upper()
    query = f"SELECT payload_json FROM {table_name}"
    params: list[Any] = []
    if customer_code:
        query += f" WHERE UPPER(customer_code) = {_param()}"
        params.append(customer_code)
    with _connect(db_path) as conn:
        table_columns = _table_columns(conn, table_name)
        if "logged_at" in table_columns:
            query += " ORDER BY logged_at DESC"
        query += f" LIMIT {_param()}"
        params.append(int(limit))
        rows = _execute(conn, query, params).fetchall()

    records = [json.loads(row[0]) for row in rows]
    frame = pd.DataFrame(records)
    for column in frame.columns:
        if "Thời gian" in column:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def load_logs(
    customer_code: str = "",
    limit: int = 5000,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    return _load_json_rows("crm_logs_v2", customer_code, limit, db_path)


def load_financial_events(
    customer_code: str = "",
    limit: int = 5000,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    return _load_json_rows("financial_events", customer_code, limit, db_path)


def load_inventory(
    customer_code: str = "",
    limit: int = 5000,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    return _load_json_rows("service_inventory", customer_code, limit, db_path)


def load_mentions(
    customer_code: str = "",
    limit: int = 5000,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    return _load_json_rows("customer_mentions", customer_code, limit, db_path)


def load_customer_url(customer_code: str, db_path: Path = DB_PATH) -> str:
    init_db(db_path)
    customer_code = customer_code.strip().upper()
    if not customer_code:
        return ""
    with _connect(db_path) as conn:
        row = _execute(
            conn,
            "SELECT getfly_url FROM customers WHERE UPPER(customer_code) = ?",
            (customer_code,),
        ).fetchone()
    return str(row[0]) if row else ""


def delete_logs(log_keys: list[str], db_path: Path = DB_PATH) -> int:
    if not log_keys:
        return 0
    init_db(db_path)
    with _connect(db_path) as conn:
        _delete_children_for_logs(conn, log_keys)
        _execute(
            conn,
            f"DELETE FROM crm_logs_v2 WHERE log_key IN ({_param(len(log_keys))})",
            log_keys,
        )
    return len(log_keys)


def delete_all_data(db_path: Path = DB_PATH) -> dict[str, int]:
    init_db(db_path)
    counts = {}
    with _connect(db_path) as conn:
        for table in ["customer_mentions", "service_inventory", "financial_events", "crm_logs_v2", "customers"]:
            counts[table] = _execute(conn, f"DELETE FROM {table}").rowcount
    return counts


def database_stats(db_path: Path = DB_PATH) -> tuple[int, int, int, int]:
    init_db(db_path)
    with _connect(db_path) as conn:
        customers = _execute(conn, "SELECT COUNT(*) FROM customers").fetchone()[0]
        logs = _execute(conn, "SELECT COUNT(*) FROM crm_logs_v2").fetchone()[0]
        financial = _execute(conn, "SELECT COUNT(*) FROM financial_events").fetchone()[0]
        inventory = _execute(conn, "SELECT COUNT(*) FROM service_inventory").fetchone()[0]
    return int(customers), int(logs), int(financial), int(inventory)


def register_user(
    email: str, password: str, role: str = "user", display_name: str = "",
    db_path: Path = DB_PATH,
) -> bool:
    import bcrypt
    init_db(db_path)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _connect(db_path) as conn:
        try:
            _execute(
                conn,
                "INSERT INTO users (email, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
                (email, password_hash, role, display_name),
            )
            return True
        except Exception:
            return False


def verify_user(email: str, password: str, db_path: Path = DB_PATH) -> tuple[bool, str]:
    import bcrypt
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _execute(
            conn,
            "SELECT password_hash, role, is_active, failed_login_count, locked_until FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None:
        return False, ""

    pw_hash, role, is_active, failed_count, locked_until = row

    if not is_active:
        return False, ""

    if locked_until:
        try:
            locked_dt = datetime.fromisoformat(locked_until)
            if datetime.utcnow() < locked_dt:
                return False, ""
        except (ValueError, TypeError):
            pass

    ok = bcrypt.checkpw(password.encode(), pw_hash.encode())
    if not ok:
        new_count = (failed_count or 0) + 1
        with _connect(db_path) as conn:
            if new_count >= 5:
                lock_until = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
                _execute(
                    conn,
                    "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE email = ?",
                    (new_count, lock_until, email),
                )
            else:
                _execute(
                    conn, "UPDATE users SET failed_login_count = ? WHERE email = ?",
                    (new_count, email),
                )
        return False, ""

    with _connect(db_path) as conn:
        _execute(
            conn,
            "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE email = ?",
            (email,),
        )
    return True, role


def _audit_log(
    email: str, action: str, details: str = "", db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    log_id = uuid.uuid4().hex
    with _connect(db_path) as conn:
        _execute(
            conn,
            "INSERT INTO auth_audit_log (id, email, action, details) VALUES (?, ?, ?, ?)",
            (log_id, email, action, details),
        )


def create_auth_session(
    email: str, role: str, max_age_days: int = 7, db_path: Path = DB_PATH,
) -> str:
    init_db(db_path)
    session_id = uuid.uuid4().hex
    expires_at = (datetime.utcnow() + timedelta(days=max_age_days)).isoformat()
    with _connect(db_path) as conn:
        _execute(
            conn,
            "INSERT INTO auth_sessions (session_id, email, role, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, email, role, expires_at),
        )
    _audit_log(email, "session_created", f"sid={session_id[:12]}...", db_path)
    return session_id


def validate_session(
    session_id: str, db_path: Path = DB_PATH,
) -> tuple[bool, str, str]:
    if not session_id:
        return False, "", ""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _execute(
            conn,
            "SELECT email, role, expires_at, is_revoked FROM auth_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return False, "", ""
    email, role, expires_at, is_revoked = row
    if is_revoked:
        return False, "", ""
    try:
        expires = datetime.fromisoformat(expires_at)
        if datetime.utcnow() > expires:
            return False, "", ""
    except (ValueError, TypeError):
        pass
    with _connect(db_path) as conn:
        _execute(
            conn,
            "UPDATE auth_sessions SET last_activity = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
    return True, email, role


def revoke_session(session_id: str, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        _execute(
            conn, "UPDATE auth_sessions SET is_revoked = 1 WHERE session_id = ?",
            (session_id,),
        )
        row = _execute(
            conn, "SELECT email FROM auth_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row:
        _audit_log(row[0], "session_revoked", f"sid={session_id[:12]}...", db_path)


def revoke_all_user_sessions(email: str, db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    with _connect(db_path) as conn:
        result = _execute(
            conn, "UPDATE auth_sessions SET is_revoked = 1 WHERE email = ? AND is_revoked = 0",
            (email,),
        )
        count = result.rowcount if hasattr(result, "rowcount") else 0
    if count:
        _audit_log(email, "all_sessions_revoked", f"{count} sessions", db_path)
    return count


def get_user_by_email(email: str, db_path: Path = DB_PATH) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _execute(
            conn,
            "SELECT email, role, display_name, is_active FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return {
        "email": row[0],
        "role": row[1],
        "display_name": row[2] or "",
        "is_active": bool(row[3]),
    }


def list_users(db_path: Path = DB_PATH) -> list[dict]:
    init_db(db_path)
    keys = ["email", "role", "display_name", "is_active", "failed_login_count", "locked_until"]
    with _connect(db_path) as conn:
        rows = _execute(
            conn,
            "SELECT email, role, display_name, is_active, failed_login_count, locked_until FROM users ORDER BY email",
        ).fetchall()
    return [dict(zip(keys, row)) for row in rows]


def update_user_password(email: str, new_password: str, db_path: Path = DB_PATH) -> bool:
    import bcrypt
    init_db(db_path)
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with _connect(db_path) as conn:
        try:
            _execute(
                conn,
                "UPDATE users SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP WHERE email = ?",
                (pw_hash, email),
            )
            _audit_log(email, "password_changed", db_path=db_path)
            return True
        except Exception:
            return False


def update_user_role(email: str, new_role: str, db_path: Path = DB_PATH) -> bool:
    init_db(db_path)
    with _connect(db_path) as conn:
        try:
            _execute(conn, "UPDATE users SET role = ? WHERE email = ?", (new_role, email))
            _audit_log(email, "role_changed", new_role, db_path)
            return True
        except Exception:
            return False


def clean_expired_sessions(db_path: Path = DB_PATH) -> int:
    try:
        with _connect(db_path) as conn:
            if _is_pg():
                sql = "DELETE FROM auth_sessions WHERE expires_at < NOW()"
            else:
                sql = "DELETE FROM auth_sessions WHERE expires_at < datetime('now')"
            result = _execute(conn, sql)
            return result.rowcount if hasattr(result, "rowcount") else 0
    except Exception:
        return 0
