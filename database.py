
from __future__ import annotations

import json
import sqlite3
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


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
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

            CREATE INDEX IF NOT EXISTS idx_crm_logs_v2_customer
            ON crm_logs_v2(customer_code);

            CREATE INDEX IF NOT EXISTS idx_financial_customer
            ON financial_events(customer_code);

            CREATE INDEX IF NOT EXISTS idx_inventory_customer
            ON service_inventory(customer_code);

            CREATE INDEX IF NOT EXISTS idx_mentions_customer
            ON customer_mentions(customer_code);
            """
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


def _delete_children_for_logs(conn: sqlite3.Connection, log_keys: list[str]) -> None:
    if not log_keys:
        return
    placeholders = ",".join("?" for _ in log_keys)
    conn.execute(
        f"DELETE FROM financial_events WHERE log_key IN ({placeholders})",
        log_keys,
    )
    conn.execute(
        f"DELETE FROM service_inventory WHERE log_key IN ({placeholders})",
        log_keys,
    )
    conn.execute(
        f"DELETE FROM customer_mentions WHERE log_key IN ({placeholders})",
        log_keys,
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
        conn.execute(
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
            for row in conn.execute(
                f"SELECT log_key FROM crm_logs_v2 WHERE log_key IN ({','.join('?' for _ in log_keys)})",
                log_keys,
            ).fetchall()
        } if log_keys else set()

        _delete_children_for_logs(conn, log_keys)

        for _, row in logs.iterrows():
            log_key = str(row.get("Log key", ""))
            logged_at = pd.to_datetime(
                row.get("Thời gian ghi nhận"), errors="coerce"
            )
            conn.execute(
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
                conn.execute(
                    """
                    INSERT OR REPLACE INTO financial_events (
                        event_id, log_key, customer_code, logged_at,
                        event_type, amount_vnd, payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                conn.execute(
                    """
                    INSERT OR REPLACE INTO service_inventory (
                        event_id, log_key, customer_code, logged_at,
                        profile_codes, service_name, payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                conn.execute(
                    """
                    INSERT OR REPLACE INTO customer_mentions (
                        mention_id, log_key, customer_code,
                        mentioned_customer_code, role_in_log,
                        payload_json, imported_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
        query += " WHERE UPPER(customer_code) = ?"
        params.append(customer_code)
    with _connect(db_path) as conn:
        table_columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "logged_at" in table_columns:
            query += " ORDER BY logged_at DESC"
        query += " LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(query, params).fetchall()

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


def database_stats(db_path: Path = DB_PATH) -> tuple[int, int, int, int]:
    init_db(db_path)
    with _connect(db_path) as conn:
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        logs = conn.execute("SELECT COUNT(*) FROM crm_logs_v2").fetchone()[0]
        financial = conn.execute("SELECT COUNT(*) FROM financial_events").fetchone()[0]
        inventory = conn.execute("SELECT COUNT(*) FROM service_inventory").fetchone()[0]
    return int(customers), int(logs), int(financial), int(inventory)
