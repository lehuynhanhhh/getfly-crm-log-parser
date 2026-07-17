
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "crm_logs.db"

PUBLIC_ACTOR = "public_user"

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
    amount_vnd INTEGER,
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
CREATE TABLE IF NOT EXISTS save_batches (
    batch_id TEXT PRIMARY KEY,
    primary_customer_code TEXT,
    source_hash TEXT,
    source_log_count INTEGER DEFAULT 0,
    source_financial_count INTEGER DEFAULT 0,
    source_inventory_count INTEGER DEFAULT 0,
    source_mention_count INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    committed_at TEXT,
    status TEXT DEFAULT 'processing',
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _get_db_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def _is_pg() -> bool:
    return _get_db_url().startswith("postgresql://") or _get_db_url().startswith("postgres://")


def _is_production() -> bool:
    return bool(os.environ.get("STREAMLIT_RUNTIME_API_SERVER_URL", "")) or \
           bool(os.environ.get("STREAMLIT_SHARING", ""))


def validate_db_config() -> None:
    url = _get_db_url()
    if _is_production():
        if not url:
            msg = (
                "⚠️ **Production database chưa được cấu hình.**\n\n"
                "Chức năng lưu dữ liệu đang bị vô hiệu hóa để tránh mất dữ liệu.\n\n"
                "Vui lòng cấu hình `DATABASE_URL` trong Streamlit Secrets:\n"
                "  DATABASE_URL = \"postgresql://user:password@host:5432/dbname\"\n\n"
                "Sau khi cấu hình, hãy khởi động lại ứng dụng."
            )
            raise RuntimeError(msg)
        if not _is_pg():
            raise RuntimeError(
                "DATABASE_URL phải bắt đầu bằng `postgresql://` hoặc `postgres://`.\n"
                f"Giá trị hiện tại: {url[:15]}..."
            )


def _pg_conn_str() -> str:
    return _get_db_url()


def _connect(db_path: Path = DB_PATH):
    if _is_pg():
        import psycopg2
        conn = psycopg2.connect(_pg_conn_str())
        conn.autocommit = True
        return conn
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
    validate_db_config()
    with _connect(db_path) as conn:
        _init_schema(conn)
        _run_migrations(conn, db_path)


def _backup_db(db_path: Path, version: str) -> Path | None:
    if _is_pg() or not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"crm_logs_{timestamp}_{version}.db"
    shutil.copy2(str(db_path), str(backup_path))
    return backup_path


def _run_migrations(conn, db_path: Path) -> None:
    applied = {row[0] for row in _execute(
        conn, "SELECT version FROM schema_migrations"
    ).fetchall()} if _table_columns(conn, "schema_migrations") >= {"version"} else set()

    migrations = [
        ("V001__money_to_bigint", _migrate_v001_money_to_bigint),
    ]
    for version, fn in migrations:
        if version not in applied:
            _backup_db(db_path, version)
            fn(conn)
            _execute(conn, "INSERT INTO schema_migrations (version) VALUES (?)", (version,))


def _migrate_v001_money_to_bigint(conn) -> None:
    if _is_pg():
        _execute(conn,
            "ALTER TABLE financial_events ALTER COLUMN amount_vnd TYPE BIGINT USING amount_vnd::bigint")
    else:
        _execute(conn, "PRAGMA foreign_keys = OFF")
        _execute(conn, """
            CREATE TABLE financial_events_v2 (
                event_id TEXT PRIMARY KEY,
                log_key TEXT,
                customer_code TEXT,
                logged_at TEXT,
                event_type TEXT,
                amount_vnd INTEGER,
                payload_json TEXT NOT NULL,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _execute(conn, """
            INSERT INTO financial_events_v2
            SELECT event_id, log_key, customer_code, logged_at, event_type,
                   CAST(CAST(amount_vnd AS REAL) AS INTEGER), payload_json, imported_at
            FROM financial_events
        """)
        _execute(conn, "DROP TABLE financial_events")
        _execute(conn, "ALTER TABLE financial_events_v2 RENAME TO financial_events")
        _execute(conn, "CREATE INDEX IF NOT EXISTS idx_financial_customer ON financial_events(customer_code)")
        _execute(conn, "PRAGMA foreign_keys = ON")


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


@dataclass
class SaveResult:
    batch_id: str = ""
    customer_upserted: bool = False
    crm_logs_inserted: int = 0
    crm_logs_updated: int = 0
    financial_inserted: int = 0
    financial_updated: int = 0
    inventory_inserted: int = 0
    inventory_updated: int = 0
    mentions_inserted: int = 0
    mentions_updated: int = 0
    committed: bool = False
    error_message: str = ""


def save_parse_result(
    bundle: Mapping[str, Any],
    metadata: Mapping[str, str],
    created_by: str = PUBLIC_ACTOR,
    db_path: Path = DB_PATH,
) -> SaveResult:
    validate_db_config()
    init_db(db_path)
    result = SaveResult()

    logs = bundle.get("logs", pd.DataFrame())
    financial = bundle.get("financial_events", pd.DataFrame())
    inventory = bundle.get("service_inventory", pd.DataFrame())
    mentions = bundle.get("customer_mentions", pd.DataFrame())

    if not isinstance(logs, pd.DataFrame) or logs.empty:
        result.error_message = "No logs to save."
        return result

    customer_code = str(
        metadata.get("customer_code")
        or logs.iloc[0].get("Mã KH chính", "")
    ).strip().upper()
    customer_name = str(
        metadata.get("customer_name")
        or logs.iloc[0].get("Tên khách hàng chính", "")
    ).strip()

    if not customer_code:
        result.error_message = "Cần có Mã khách hàng chính trước khi lưu database."
        return result

    log_keys = logs["Log key"].astype(str).tolist()
    source_hash = hashlib.sha256(
        str(bundle.get("raw_text", "")).encode("utf-8")
    ).hexdigest()[:16]

    conn = _connect(db_path)
    try:
        if _is_pg():
            conn.autocommit = False
        else:
            _execute(conn, "BEGIN")

        batch_id = uuid.uuid4().hex
        _execute(conn, """
            INSERT INTO save_batches (batch_id, primary_customer_code, source_hash,
                source_log_count, source_financial_count, source_inventory_count,
                source_mention_count, created_by, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing')
        """, (batch_id, customer_code, source_hash,
              len(logs), len(financial), len(inventory), len(mentions), created_by))

        _execute(conn, """
            INSERT INTO customers (customer_code, customer_name, branch, getfly_url, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(customer_code) DO UPDATE SET
                customer_name = excluded.customer_name,
                branch = excluded.branch,
                getfly_url = excluded.getfly_url,
                updated_at = CURRENT_TIMESTAMP
        """, (customer_code, customer_name,
              str(metadata.get("branch", "")),
              str(metadata.get("getfly_url", ""))))
        result.customer_upserted = True

        for _, row in logs.iterrows():
            log_key = str(row.get("Log key", ""))
            logged_at = pd.to_datetime(row.get("Thời gian ghi nhận"), errors="coerce")
            existing = _execute(conn,
                "SELECT 1 FROM crm_logs_v2 WHERE log_key = ?", (log_key,)
            ).fetchone()
            _execute(conn, """
                INSERT INTO crm_logs_v2 (log_key, customer_code, customer_name, logged_at, payload_json, imported_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(log_key) DO UPDATE SET
                    customer_code = excluded.customer_code,
                    customer_name = excluded.customer_name,
                    logged_at = excluded.logged_at,
                    payload_json = excluded.payload_json,
                    imported_at = CURRENT_TIMESTAMP
            """, (log_key, customer_code, customer_name,
                  None if pd.isna(logged_at) else logged_at.isoformat(),
                  _row_payload(row)))
            if existing:
                result.crm_logs_updated += 1
            else:
                result.crm_logs_inserted += 1

        if isinstance(financial, pd.DataFrame) and not financial.empty:
            for _, row in financial.iterrows():
                logged_at = pd.to_datetime(row.get("Thời gian ghi nhận"), errors="coerce")
                amount_val = pd.to_numeric(row.get("Số tiền (VND)"), errors="coerce")
                amount_int = int(amount_val) if pd.notna(amount_val) else None
                event_id = str(row.get("Financial event ID", ""))
                existing = _execute(conn,
                    "SELECT 1 FROM financial_events WHERE event_id = ?", (event_id,)
                ).fetchone()
                _execute(conn, """
                    INSERT INTO financial_events (event_id, log_key, customer_code, logged_at,
                        event_type, amount_vnd, payload_json, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(event_id) DO UPDATE SET
                        log_key = excluded.log_key, customer_code = excluded.customer_code,
                        logged_at = excluded.logged_at, event_type = excluded.event_type,
                        amount_vnd = excluded.amount_vnd, payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                """, (event_id, str(row.get("Log key", "")), customer_code,
                      None if pd.isna(logged_at) else logged_at.isoformat(),
                      str(row.get("Loại sự kiện tài chính", "")),
                      amount_int, _row_payload(row)))
                if existing:
                    result.financial_updated += 1
                else:
                    result.financial_inserted += 1

        if isinstance(inventory, pd.DataFrame) and not inventory.empty:
            for _, row in inventory.iterrows():
                logged_at = pd.to_datetime(row.get("Thời gian ghi nhận"), errors="coerce")
                event_id = str(row.get("Inventory event ID", ""))
                existing = _execute(conn,
                    "SELECT 1 FROM service_inventory WHERE event_id = ?", (event_id,)
                ).fetchone()
                _execute(conn, """
                    INSERT INTO service_inventory (event_id, log_key, customer_code, logged_at,
                        profile_codes, service_name, payload_json, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(event_id) DO UPDATE SET
                        log_key = excluded.log_key, customer_code = excluded.customer_code,
                        logged_at = excluded.logged_at,
                        profile_codes = excluded.profile_codes,
                        service_name = excluded.service_name,
                        payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                """, (event_id, str(row.get("Log key", "")), customer_code,
                      None if pd.isna(logged_at) else logged_at.isoformat(),
                      str(row.get("Mã hồ sơ HS", "")),
                      str(row.get("Dịch vụ/Gói còn lại", "")),
                      _row_payload(row)))
                if existing:
                    result.inventory_updated += 1
                else:
                    result.inventory_inserted += 1

        if isinstance(mentions, pd.DataFrame) and not mentions.empty:
            for _, row in mentions.iterrows():
                mention_id = str(row.get("Mention ID", ""))
                existing = _execute(conn,
                    "SELECT 1 FROM customer_mentions WHERE mention_id = ?", (mention_id,)
                ).fetchone()
                _execute(conn, """
                    INSERT INTO customer_mentions (mention_id, log_key, customer_code,
                        mentioned_customer_code, role_in_log, payload_json, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(mention_id) DO UPDATE SET
                        log_key = excluded.log_key, customer_code = excluded.customer_code,
                        mentioned_customer_code = excluded.mentioned_customer_code,
                        role_in_log = excluded.role_in_log,
                        payload_json = excluded.payload_json,
                        imported_at = CURRENT_TIMESTAMP
                """, (mention_id, str(row.get("Log key", "")), customer_code,
                      str(row.get("Mã KH được nhắc", "")),
                      str(row.get("Vai trò trong log", "")),
                      _row_payload(row)))
                if existing:
                    result.mentions_updated += 1
                else:
                    result.mentions_inserted += 1

        _execute(conn,
            "UPDATE save_batches SET status = 'committed', committed_at = CURRENT_TIMESTAMP WHERE batch_id = ?",
            (batch_id,))
        result.batch_id = batch_id

        if _is_pg():
            conn.commit()
        else:
            _execute(conn, "COMMIT")
        result.committed = True

    except Exception as e:
        try:
            if _is_pg():
                conn.rollback()
            else:
                _execute(conn, "ROLLBACK")
            _execute(conn,
                "UPDATE save_batches SET status = 'failed', error_message = ? WHERE batch_id = ?",
                (str(e), batch_id))
        except Exception:
            pass
        result.committed = False
        result.error_message = str(e)
    finally:
        conn.close()

    return result


def load_customer_bundle(
    customer_code: str = "",
    limit: int = 10000,
    db_path: Path = DB_PATH,
) -> dict:
    validate_db_config()
    kwargs = {"limit": limit, "db_path": db_path}
    return {
        "customer": _load_customer(customer_code, db_path),
        "logs": load_logs(customer_code, **kwargs),
        "financial_events": load_financial_events(customer_code, **kwargs),
        "service_inventory": load_inventory(customer_code, **kwargs),
        "customer_mentions": load_mentions(customer_code, **kwargs),
    }


def _load_customer(customer_code: str, db_path: Path) -> dict | None:
    customer_code = customer_code.strip().upper()
    if not customer_code:
        return None
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _execute(conn,
            "SELECT customer_code, customer_name, branch, getfly_url, updated_at FROM customers WHERE UPPER(customer_code) = ?",
            (customer_code,)).fetchone()
    if not row:
        return None
    return dict(zip(["customer_code", "customer_name", "branch", "getfly_url", "updated_at"], row))


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
