
from __future__ import annotations

import json
import os
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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
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
    seed_admin(db_path)


def _migrate_users(conn) -> None:
    cols = _table_columns(conn, "users")
    if "role" not in cols:
        _execute(conn, "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")


def seed_admin(db_path: Path = DB_PATH) -> None:
    import bcrypt
    init_db(db_path)
    with _connect(db_path) as conn:
        exists = _execute(
            conn, "SELECT 1 FROM users WHERE email = ?", ("admin@driphydration.vn",)
        ).fetchone()
        if not exists:
            pw_hash = bcrypt.hashpw(b"Financeteam@123", bcrypt.gensalt()).decode()
            _execute(
                conn,
                "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                ("admin@driphydration.vn", pw_hash, "admin"),
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
    email: str, password: str, role: str = "user", db_path: Path = DB_PATH,
) -> bool:
    import bcrypt
    init_db(db_path)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _connect(db_path) as conn:
        try:
            _execute(
                conn,
                "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                (email, password_hash, role),
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
            "SELECT password_hash, role FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if row is None:
        return False, ""
    ok = bcrypt.checkpw(password.encode(), row[0].encode())
    return ok, (row[1] if ok else "")
