
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any


# ── Test mode ──
if "--test" in sys.argv:
    import os
    import numpy as np
    import pandas as pd

    def _infer_filter_type(col, series):
        raw = series.dropna()
        if raw.empty:
            return "id"
        if pd.api.types.is_bool_dtype(raw.dtype):
            return "sel"
        if pd.api.types.is_datetime64_any_dtype(raw.dtype):
            return "date"
        if pd.api.types.is_numeric_dtype(raw.dtype) and raw.nunique() >= 2:
            return "num"
        return "sel"

    def _clean_vnd_series(series):
        cleaned = series.astype(str)
        cleaned = cleaned.str.replace(r"(?i)\bVND\b", "", regex=True)
        cleaned = cleaned.str.replace(r"\.(?=\d{3}(?:\D|$))", "", regex=True)
        cleaned = cleaned.str.replace(r",(?=\d{3}(?:\D|$))", "", regex=True)
        cleaned = cleaned.str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True)
        return pd.to_numeric(cleaned, errors="coerce")

    errors = 0
    def check(cond, msg):
        global errors
        if not cond:
            print(f"  FAIL: {msg}", flush=True)
            errors += 1
        else:
            print(f"  OK:   {msg}", flush=True)

    check(_infer_filter_type("", pd.Series(["KH440297", "KH123456"])) == "sel", "KH440297 -> sel")
    check(_infer_filter_type("", pd.Series(["HS250857315", "HS98765432"])) == "sel", "HS250857315 -> sel")
    check(_infer_filter_type("", pd.Series(["16:03", "09:30"])) == "sel", "16:03 (Gio) -> sel")
    check(_infer_filter_type("", pd.Series(["11/07/2026", "12/07/2026"])) == "sel", "11/07/2026 (Ngay) -> sel")
    check(_infer_filter_type("", pd.to_datetime(pd.Series(["2026-07-11", "2026-07-12"]))) == "date", "datetime64 -> date")
    check(_infer_filter_type("", pd.Series([1, 2, 3])) == "num", "int [1,2,3] -> num")
    check(_infer_filter_type("", pd.Series([True, False])) == "sel", "bool -> sel")
    check(_infer_filter_type("", pd.Series([1, 1, 1])) == "sel", "single-value [1,1,1] -> sel (no slider)")
    check(_infer_filter_type("", pd.Series([], dtype=float)) == "id", "empty series -> id")

    def _clean(s):
        return _clean_vnd_series(s).tolist()

    check(_clean(pd.Series(["1.500.000", "2.000.000"])) == [1500000, 2000000], "1.500.000 -> 1500000")
    check(_clean(pd.Series(["1.500.000 VND", "2.000.000 VND"])) == [1500000, 2000000], "1.500.000 VND -> 1500000")
    check(abs(_clean(pd.Series(["1.500.000,50"]))[0] - 1500000.5) < 0.01, "1.500.000,50 -> 1500000.5")
    check(_clean(pd.Series(["1,500,000.00"])) == [1500000], "1,500,000.00 -> 1500000")
    check(all(pd.isna(_clean(pd.Series(["abc"])))), "abc -> NaN")

    total = 14

    # ── Persistence tests ──
    import gc
    import tempfile
    from pathlib import Path
    from database import (
        DB_PATH,
        _connect,
        _execute,
        init_db,
        load_customer_bundle,
        save_parse_result,
    )

    def _assert_persist(cond, msg):
        global errors
        if not cond:
            print(f"  FAIL: {msg}", flush=True)
            errors += 1
        else:
            print(f"  OK:   {msg}", flush=True)

    # Use a temp DB so we don't pollute the real one
    tmp = Path(tempfile.mktemp(suffix=".db"))
    old_db_path = DB_PATH
    # Monkey-patch DB_PATH for test scope
    import database as _db_mod
    _db_mod.DB_PATH = tmp

    init_db()

    # 1. Basic persistence
    bundle1 = {
        "logs": pd.DataFrame({
            "Log key": ["k1", "k2"],
            "Mã KH chính": ["KH001", "KH001"],
            "Thời gian ghi nhận": ["2026-07-14 10:00", "2026-07-14 10:01"],
            "Tên khách hàng chính": ["Test A", "Test A"],
            "STT": [1, 2],
        }),
        "financial_events": pd.DataFrame({
            "Financial event ID": ["f1"],
            "Log key": ["k1"],
            "Loại sự kiện tài chính": ["TT"],
            "Số tiền (VND)": [1500000],
            "Thời gian ghi nhận": ["2026-07-14 10:00"],
        }),
        "service_inventory": pd.DataFrame(),
        "customer_mentions": pd.DataFrame(),
    }
    meta1 = {"customer_code": "KH001", "customer_name": "Test A"}
    r1 = save_parse_result(bundle1, meta1, db_path=tmp)
    _assert_persist(r1.committed, "P1: save committed (fresh)")

    # 2. F5 / restart simulation (new connection)
    loaded = load_customer_bundle("KH001", db_path=tmp)
    _assert_persist(len(loaded["logs"]) == 2, "P2: 2 logs survive after restart sim")
    _assert_persist(len(loaded["financial_events"]) == 1, "P3: 1 financial event survives")

    # 3. Idempotency (re-save same data)
    r2 = save_parse_result(bundle1, meta1, db_path=tmp)
    _assert_persist(r2.committed, "P4: idempotent save committed")
    loaded2 = load_customer_bundle("KH001", db_path=tmp)
    _assert_persist(len(loaded2["logs"]) == 2, "P5: still 2 logs after idempotent save")

    # 4. Partial update (new log added)
    bundle2 = {
        "logs": pd.DataFrame({
            "Log key": ["k1", "k2", "k3"],
            "Mã KH chính": ["KH001", "KH001", "KH001"],
            "Thời gian ghi nhận": ["2026-07-14 10:00", "2026-07-14 10:01", "2026-07-14 10:02"],
            "Tên khách hàng chính": ["Test A", "Test A", "Test A"],
            "STT": [1, 2, 3],
        }),
        "financial_events": pd.DataFrame({
            "Financial event ID": ["f1", "f2"],
            "Log key": ["k1", "k3"],
            "Loại sự kiện tài chính": ["TT", "CK"],
            "Số tiền (VND)": [1500000, 2000000],
            "Thời gian ghi nhận": ["2026-07-14 10:00", "2026-07-14 10:02"],
        }),
        "service_inventory": pd.DataFrame(),
        "customer_mentions": pd.DataFrame(),
    }
    r3 = save_parse_result(bundle2, meta1, db_path=tmp)
    _assert_persist(r3.committed, "P6: partial update committed")
    loaded3 = load_customer_bundle("KH001", db_path=tmp)
    _assert_persist(len(loaded3["logs"]) == 3, "P7: 3 logs after adding new log")
    _assert_persist(len(loaded3["financial_events"]) == 2, "P8: 2 financial events after adding new")

    # 5. Money is INTEGER (BIGINT)
    fin = loaded3["financial_events"]
    money_vals = fin["Số tiền (VND)"].tolist()
    _assert_persist(all(isinstance(v, (int, np.integer)) for v in money_vals), "P9: money column is int type")
    _assert_persist(set(money_vals) == {1500000, 2000000}, "P10: money values match originals")

    # 6. Identifier column is TEXT
    _assert_persist(isinstance(loaded3["logs"]["Log key"].iloc[0], str), "P11: Log key is TEXT")

    # 7. Empty data (no logs to save)
    empty_bundle = {
        "logs": pd.DataFrame(),
        "financial_events": pd.DataFrame(),
        "service_inventory": pd.DataFrame(),
        "customer_mentions": pd.DataFrame(),
    }
    r4 = save_parse_result(empty_bundle, meta1, db_path=tmp)
    _assert_persist(not r4.committed and r4.error_message == "No logs to save.", "P12: empty bundle rejected")

    # 8. Schema migration record
    conn = _connect(tmp)
    mig_rows = _execute(conn, "SELECT version FROM schema_migrations").fetchall()
    conn.close()
    _assert_persist(len(mig_rows) >= 1, "P13: schema_migrations has records")
    _assert_persist(any("V001" in r[0] for r in mig_rows), "P14: V001 migration recorded")

    # Cleanup
    import gc
    try:
        conn.close()
    except NameError:
        pass
    gc.collect()
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    _db_mod.DB_PATH = old_db_path

    total += 13
    passed = total - errors
    print(f"\n{'-' * 40}\nPassed: {passed}/{total}", flush=True)
    os._exit(0 if errors == 0 else 1)

import pandas as pd
import streamlit as st

from crm_parser import DEFAULT_STATUS_MAPPING, parse_crm_bundle
from database import (
    PUBLIC_ACTOR,
    database_stats,
    delete_all_data,
    delete_logs,
    init_db,
    load_customer_bundle,
    load_customer_url,
    load_financial_events,
    load_inventory,
    load_logs,
    load_mentions,
    save_parse_result,
    SaveResult,
    validate_db_config,
    _get_db_url,
    _is_production,
    _is_pg,
)
from excel_export import build_excel_bytes


st.set_page_config(
    page_title="Getfly CRM Log",
    page_icon="📋",
    layout="wide",
)

PRIMARY = "#67C8C9"

st.markdown(
    f"""
    <style>
        .block-container {{
            padding-top: 1.3rem;
            padding-bottom: 2rem;
        }}
        div[data-testid="stMetric"] {{
            border: 1px solid #DDE4E7;
            border-radius: 12px;
            padding: 12px;
            background: #FFFFFF;
            color: #1a1a1a;
        }}
        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{
            color: #555555;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
            color: #1a1a1a;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
            color: #333333;
        }}
        .app-note {{
            padding: 12px 14px;
            background: #EAF8F8;
            border-left: 5px solid {PRIMARY};
            border-radius: 8px;
            color: #1a1a1a;
        }}
        .stSidebar .stMetric label,
        .stSidebar .stMetric div {{
            color: #1a1a1a !important;
        }}
        .stSidebar .stMetric {{
            color: #1a1a1a !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

init_db()

if _is_production() and not _get_db_url():
    st.sidebar.error(
        "⚠️ **Chế độ production nhưng thiếu DATABASE_URL**\n\n"
        "Cấu hình trong Streamlit Secrets:\n"
        "`DATABASE_URL = \"postgresql://...\"`\n\n"
        "Dữ liệu sẽ KHÔNG được lưu khi thiếu cấu hình này.",
        icon="🚨",
    )

st.sidebar.info(
    "Ứng dụng đang ở chế độ truy cập công khai. "
    "Dữ liệu lưu trong hệ thống được dùng chung cho tất cả người truy cập."
)

st.session_state.setdefault("crm_bundle", None)
st.session_state.setdefault("raw_text", "")
st.session_state.setdefault("status_mapping", DEFAULT_STATUS_MAPPING.copy())
st.session_state.setdefault("default_branch", "")
st.session_state.setdefault("getfly_url", "")


def vnd(value: object) -> str:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return "—"
    return f"{float(number):,.0f} ₫".replace(",", ".")


def _show_db_diagnostics() -> None:
    import os
    from pathlib import Path
    from database import DB_PATH, _connect, _execute, _get_db_url, _is_production, _is_pg

    env = "🏭 Production" if _is_production() else "💻 Local"
    backend = "🐘 PostgreSQL" if _is_pg() else "🗄️ SQLite"
    url = _get_db_url()
    masked_url = url[:url.find("://") + 3] + "*****" if url else "—"
    db_path = DB_PATH

    st.markdown(f"**Environment:** {env}")
    st.markdown(f"**Backend:** {backend}")
    st.markdown(f"**Database URL:** `{masked_url}`")

    if _is_pg():
        return

    st.markdown(f"**File:** `{db_path}`")
    st.markdown(f"**Exists:** {db_path.exists()}")
    if db_path.exists():
        st.markdown(f"**Size:** {db_path.stat().st_size / 1024:.1f} KB")
    backup_dir = db_path.parent / "backups"
    if backup_dir.exists():
        backups = sorted(backup_dir.glob("*.db"))
        st.markdown(f"**Backups:** {len(backups)} ({backups[-1].name if backups else '—'})")

    st.divider()
    try:
        conn = _connect(db_path)
        rows = _execute(conn, "SELECT version FROM schema_migrations ORDER BY version").fetchall()
        st.markdown(f"**Migrations:** {', '.join(r[0] for r in rows) if rows else 'None'}")
        conn.close()
    except Exception:
        st.markdown("**Migrations:** Error reading")

    st.divider()
    c, l, f, i = database_stats()
    st.markdown(f"**Customers:** {c}")
    st.markdown(f"**CRM Logs:** {l}")
    st.markdown(f"**Financial Events:** {f}")
    st.markdown(f"**Service Inventory:** {i}")


def latest_balance(financial: pd.DataFrame, event_type: str) -> float | None:
    if financial.empty:
        return None
    data = financial[financial["Loại sự kiện tài chính"] == event_type].copy()
    if data.empty:
        return None
    data["Thời gian ghi nhận"] = pd.to_datetime(
        data["Thời gian ghi nhận"], errors="coerce"
    )
    data["Số tiền (VND)"] = pd.to_numeric(
        data["Số tiền (VND)"], errors="coerce"
    )
    data = data.dropna(subset=["Số tiền (VND)"])
    if data.empty:
        return None
    latest_time = data["Thời gian ghi nhận"].max()
    latest = data[data["Thời gian ghi nhận"] == latest_time]
    if event_type in {"Số dư tài khoản tặng", "Số dư voucher"}:
        return float(latest["Số tiền (VND)"].sum())
    return float(latest.iloc[-1]["Số tiền (VND)"])


# ── Column filter type constants ──
# skip  — hidden
# id    — no widget
# date  — st.date_input range
# curr  — st.slider 0→max
# num   — st.slider min→max
# sel   — st.selectbox exact match
# text  — st.text_input contains search

_COLUMN_CONFIG: dict[str, str] = {
    # skip
    "Chọn": "skip",
    "Getfly URL": "skip",
    # id (technical)
    "STT": "id",
    "Log key": "id",
    "Financial event ID": "id",
    "Inventory event ID": "id",
    "Mention ID": "id",
    # date
    "Thời gian ghi nhận": "date",
    "Ngày": "date",
    # text (date-like strings that must NOT be parsed as numeric)
    "Ngày dịch vụ": "text",
    "Ngày kích hoạt": "text",
    "Hạn sử dụng": "text",
    # time (also string, not numeric)
    "Giờ": "sel",
    # select (identifiers / short codes)
    "Mã KH chính": "sel",
    "Mã trạng thái": "sel",
    "Mã hồ sơ HS": "sel",
    "Mã KH": "sel",
    # select (business text with few distinct values)
    "Tên khách hàng chính": "sel",
    "Nguồn KH chính": "sel",
    "Nguồn thời gian": "sel",
    "Người ghi (nguyên bản)": "sel",
    "Vai trò/Bộ phận": "sel",
    "Người phụ trách": "sel",
    "Đã chỉnh sửa": "sel",
    "Trạng thái chuẩn": "sel",
    "Nhóm nội dung": "sel",
    "Cơ sở": "sel",
    "Tên KH được nhắc": "sel",
    "Tên KH sử dụng dịch vụ": "sel",
    "Tên KH nguồn tiền/thẻ": "sel",
    "Tên KH sở hữu": "sel",
    "Tình trạng thanh toán": "sel",
    "Trạng thái nghiệp vụ": "sel",
    "Độ tin cậy": "sel",
    "Loại sự kiện tài chính": "sel",
    "Góc nhìn công nợ": "sel",
    "Tài khoản/Nguồn tiền": "sel",
    "Trạng thái tồn": "sel",
    "Đơn vị chính": "sel",
    "Là quà tặng": "sel",
    "Vai trò trong log": "sel",
    "Mã KH sở hữu": "sel",
    # text (long content → substring search)
    "Mã KH được nhắc": "text",
    "Mã KH sử dụng dịch vụ": "text",
    "Mã KH nguồn tiền/thẻ": "text",
    "Nội dung gốc": "text",
    "Dòng nguồn": "text",
    "Dịch vụ/Gói còn lại": "text",
    "Chi tiết số lượng": "text",
    "Bằng chứng mẫu": "text",
    # currency (slider 0→max)
    "Giá trị mua (VND)": "curr",
    "Đã thanh toán (VND)": "curr",
    "KH còn nợ công ty (VND)": "curr",
    "Công ty phải trả/ghi có KH (VND)": "curr",
    "Số tiền trừ cọc/thẻ (VND)": "curr",
    "Số dư cọc (VND)": "curr",
    "Số dư TK chính (VND)": "curr",
    "Số dư TK tặng (VND)": "curr",
    "Số dư voucher (VND)": "curr",
    "Tiền tặng/khuyến mãi (VND)": "curr",
    "Tiền bù thêm (VND)": "curr",
    "Số tiền (VND)": "curr",
    # numeric (slider min→max)
    "Số lượng chính": "num",
    "Điểm nhận diện": "num",
    "Số lần xuất hiện": "num",
    "Tỷ lệ giảm/tặng": "num",
}


def _infer_filter_type(col: str, series: pd.Series) -> str:
    raw = series.dropna()
    if raw.empty:
        return "id"
    if pd.api.types.is_bool_dtype(raw.dtype):
        return "sel"
    if pd.api.types.is_datetime64_any_dtype(raw.dtype):
        return "date"
    if pd.api.types.is_numeric_dtype(raw.dtype) and raw.nunique() >= 2:
        return "num"
    return "sel"


def _clean_vnd_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str)
    cleaned = cleaned.str.replace(r"(?i)\bVND\b|₫", "", regex=True)
    cleaned = cleaned.str.replace(r"\.(?=\d{3}(?:\D|$))", "", regex=True)
    cleaned = cleaned.str.replace(r",(?=\d{3}(?:\D|$))", "", regex=True)
    cleaned = cleaned.str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _column_filters(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    if df.empty:
        return df
    clear_key = st.session_state.get(f"flt_clear_{key_prefix}", 0)
    filters: dict[str, Any] = {}
    with st.expander("🔍 Lọc theo cột", expanded=False):
        visible = [c for c in df.columns if _COLUMN_CONFIG.get(c, _infer_filter_type(c, df[c])) not in ("skip", "id")]
        ncols = st.columns(min(4, len(visible)) or 1)
        visible_idx = 0
        for i, col in enumerate(df.columns):
            ftype = _COLUMN_CONFIG.get(col) or _infer_filter_type(col, df[col])
            if ftype in ("skip", "id"):
                continue
            with ncols[visible_idx % len(ncols)]:
                visible_idx += 1
                raw = df[col].dropna()
                if raw.empty:
                    continue
                if ftype == "date":
                    if pd.api.types.is_datetime64_any_dtype(raw.dtype):
                        min_d = raw.min().date()
                        max_d = raw.max().date()
                    else:
                        continue
                    if min_d == max_d:
                        st.text(f"📅 {col}: {min_d}")
                        continue
                    sel = st.date_input(
                        col, (min_d, max_d), min_d, max_d,
                        key=f"flt_{key_prefix}_{i}_{clear_key}",
                    )
                    if isinstance(sel, (list, tuple)) and len(sel) == 2:
                        if sel[0] != min_d or sel[1] != max_d:
                            filters[col] = (sel[0], sel[1])
                elif ftype in ("curr", "num"):
                    if pd.api.types.is_numeric_dtype(raw.dtype):
                        vmin, vmax = float(raw.min()), float(raw.max())
                    else:
                        coerced = _clean_vnd_series(raw).dropna()
                        if coerced.empty:
                            continue
                        vmin, vmax = float(coerced.min()), float(coerced.max())
                    if vmin == vmax:
                        st.text(f"{col}: {vmax:,.0f}")
                        continue
                    lo = 0.0 if ftype == "curr" else vmin
                    filters[col] = st.slider(
                        col, lo, vmax, (lo, vmax),
                        key=f"flt_{key_prefix}_{i}_{clear_key}",
                    )
                elif ftype == "text":
                    val = st.text_input(col, placeholder="Tìm kiếm…", key=f"flt_{key_prefix}_{i}_{clear_key}")
                    if val:
                        filters[col] = val
                else:
                    options = sorted(raw.unique().tolist())
                    sel = st.selectbox(
                        col, ["", *options],
                        format_func=lambda x: "Tất cả" if x == "" else str(x),
                        key=f"flt_{key_prefix}_{i}_{clear_key}",
                    )
                    if sel:
                        filters[col] = sel
        if st.button("🗑️ Xoá bộ lọc", key=f"flt_btn_{key_prefix}_{clear_key}"):
            st.session_state[f"flt_clear_{key_prefix}"] = clear_key + 1
            st.rerun()
    for col, fval in filters.items():
        if fval is None:
            continue
        ftype = _COLUMN_CONFIG.get(col) or _infer_filter_type(col, df[col])
        if ftype == "date":
            start, end = fval
            df = df[(df[col].dt.date >= start) & (df[col].dt.date <= end)]
        elif ftype == "curr":
            lo, hi = fval
            if pd.api.types.is_numeric_dtype(df[col].dtype):
                df = df[df[col].between(lo, hi, inclusive="both")]
            else:
                mask = _clean_vnd_series(df[col]).between(lo, hi, inclusive="both")
                df = df[mask.reindex(df.index, fill_value=False)]
        elif ftype == "num":
            lo, hi = fval
            if pd.api.types.is_numeric_dtype(df[col].dtype):
                df = df[df[col].between(lo, hi, inclusive="both")]
            else:
                coerced = pd.to_numeric(df[col].astype(str), errors="coerce")
                mask = coerced.between(lo, hi, inclusive="both")
                df = df[mask.reindex(df.index, fill_value=False)]
        elif ftype == "text":
            df = df[df[col].astype(str).str.contains(str(fval), case=False, na=False)]
        else:
            df = df[df[col].astype(str) == str(fval)]
    return df.reset_index(drop=True)


st.title("Getfly CRM Log")
st.caption(
    "Paste CRM → nhận diện khách hàng chính → tách log, tiền, công nợ, hồ sơ HS và tồn dịch vụ."
)

with st.sidebar:
    st.header("Thông tin đầu vào")
    manual_customer_code = st.text_input(
        "Mã KH chính (không bắt buộc)",
        placeholder="Để trống để hệ thống tự tìm",
    ).strip().upper()
    manual_customer_name = st.text_input(
        "Tên KH chính (không bắt buộc)",
        placeholder="Để trống để hệ thống tự tìm",
    ).strip()
    branch = st.selectbox(
        "Cơ sở mặc định",
        ["", "D1/HCM", "D5/HCM", "D2/HN", "Khác"],
        index=0,
    )
    getfly_url = st.text_input(
        "Getfly URL",
        value=st.session_state.get("getfly_url", ""),
        placeholder="https://drip.getflycrm.com/#/crm/view_account/...",
    ).strip()

    st.divider()
    st.subheader("Từ điển trạng thái")
    mapping_df = pd.DataFrame(
        [
            {"Mã": key, "Nội dung chuẩn": value}
            for key, value in st.session_state.status_mapping.items()
        ]
    )
    edited_mapping = st.data_editor(
        mapping_df,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key="status_mapping_editor_v12",
    )
    st.session_state.status_mapping = {
        str(row["Mã"]).strip().upper(): str(row["Nội dung chuẩn"]).strip()
        for _, row in edited_mapping.iterrows()
        if str(row.get("Mã", "")).strip()
    }

    st.divider()
    customer_count, log_count, financial_count, inventory_count = database_stats()
    st.metric("Khách hàng đã lưu", f"{customer_count:,}")
    st.metric("Log đã lưu", f"{log_count:,}")
    st.metric("Sự kiện tài chính", f"{financial_count:,}")
    st.metric("Dòng tồn dịch vụ", f"{inventory_count:,}")

    with st.expander("🛠️ Database Diagnostics", expanded=False):
        _show_db_diagnostics()


tab_parse, tab_history, tab_guide = st.tabs(
    ["1. Paste & xử lý", "2. Dữ liệu đã lưu", "3. Hướng dẫn"]
)

with tab_parse:
    with st.form("parse_form_v12", clear_on_submit=False):
        raw_text = st.text_area(
            "Paste toàn bộ CRM tại đây",
            value=st.session_state.raw_text,
            height=360,
            placeholder=(
                "Paste lịch sử CRM của một khách hàng.\n"
                "Mỗi log thường bắt đầu bằng: Tên người ghi + dd/mm/yyyy hh:mm"
            ),
        )
        parse_clicked = st.form_submit_button(
            "Phân tích dữ liệu",
            type="primary",
            use_container_width=True,
        )

    if parse_clicked:
        st.session_state.raw_text = raw_text
        st.session_state.default_branch = "" if branch == "Khác" else branch
        st.session_state.getfly_url = getfly_url

        bundle = parse_crm_bundle(
            raw_text=raw_text,
            customer_code=manual_customer_code,
            customer_name=manual_customer_name,
            default_branch=st.session_state.default_branch,
            status_mapping=st.session_state.status_mapping,
        )
        st.session_state.crm_bundle = bundle

        if bundle["logs"].empty:
            st.error(
                "Không nhận diện được log. Hãy kiểm tra cấu trúc dòng đầu của từng log."
            )
        else:
            st.success(
                f"Đã nhận diện {len(bundle['logs']):,} log cho "
                f"{bundle['primary_customer_code']} – {bundle['primary_customer_name']}."
            )

    bundle = st.session_state.crm_bundle

    if bundle is not None and not bundle["logs"].empty:
        candidates = bundle["customer_candidates"]
        primary_code = str(bundle["primary_customer_code"])
        primary_name = str(bundle["primary_customer_name"])

        st.subheader("Khách hàng chính")
        c1, c2, c3 = st.columns([1.2, 2, 1.3])
        c1.metric("Mã KH chính", primary_code or "Chưa xác định")
        c2.metric("Tên khách hàng chính", primary_name or "Chưa xác định")
        c3.metric("Nguồn nhận diện", str(bundle["primary_customer_source"]))

        if not candidates.empty:
            options = [
                f"{row['Mã KH']} | {row['Tên khách hàng']}"
                for _, row in candidates.iterrows()
            ]
            current_label = f"{primary_code} | {primary_name}"
            default_index = options.index(current_label) if current_label in options else 0
            selected_candidate = st.selectbox(
                "Đổi khách hàng chính khi log có nhiều người sử dụng chung thẻ/cọc",
                options=options,
                index=default_index,
            )
            if st.button("Áp dụng khách hàng chính đã chọn"):
                selected_code, selected_name = [
                    value.strip() for value in selected_candidate.split("|", 1)
                ]
                st.session_state.crm_bundle = parse_crm_bundle(
                    raw_text=st.session_state.raw_text,
                    customer_code=selected_code,
                    customer_name=selected_name,
                    default_branch=st.session_state.default_branch,
                    status_mapping=st.session_state.status_mapping,
                )
                st.rerun()

        logs = bundle["logs"]
        financial = bundle["financial_events"]
        inventory = bundle["service_inventory"]
        mentions = bundle["customer_mentions"]

        st.markdown(
            '<div class="app-note">'
            "<b>Đã áp dụng:</b> Mã KH chính và Tên khách hàng chính được điền vào "
            "toàn bộ log, sự kiện tài chính, tồn dịch vụ và bảng khách hàng liên quan."
            "</div>",
            unsafe_allow_html=True,
        )

        st.subheader("Tóm tắt")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Tổng log", f"{len(logs):,}")
        m2.metric("Sự kiện tiền", f"{len(financial):,}")
        m3.metric("Tồn dịch vụ/HS", f"{len(inventory):,}")
        m4.metric("Số dư cọc gần nhất", vnd(latest_balance(financial, "Số dư cọc")))
        m5.metric("TK chính gần nhất", vnd(latest_balance(financial, "Số dư tài khoản chính")))
        m6.metric("TK tặng gần nhất", vnd(latest_balance(financial, "Số dư tài khoản tặng")))

        result_tabs = st.tabs(
            [
                "CRM Log",
                "Financial Events",
                "Service Inventory / HS",
                "Related Customers",
                "Customer Candidates",
            ]
        )

        with result_tabs[0]:
            display_logs = logs.copy()
            gurl = st.session_state.getfly_url
            if gurl:
                display_logs["Getfly URL"] = gurl
            display_logs = _column_filters(display_logs, "p_logs")
            edited_logs = st.data_editor(
                display_logs,
                hide_index=True,
                use_container_width=True,
                height=560,
                num_rows="dynamic",
                disabled=["Log key"],
                key="logs_editor_v12",
            )
            bundle["logs"] = edited_logs

        with result_tabs[1]:
            st.caption(
                "Một log có thể tạo nhiều dòng tài chính: mua gói, thanh toán, nợ, "
                "hoàn/back, số dư cọc, số dư tài khoản và tiền trừ."
            )
            display_fin = _column_filters(financial, "p_fin")
            edited_financial = st.data_editor(
                display_fin,
                hide_index=True,
                use_container_width=True,
                height=560,
                num_rows="dynamic",
                disabled=["Financial event ID", "Log key"],
                column_config={
                    "Số tiền (VND)": st.column_config.NumberColumn(format="%,.0f"),
                },
                key="financial_editor_v12",
            )
            bundle["financial_events"] = edited_financial

        with result_tabs[2]:
            display_inv = _column_filters(inventory, "p_inv")
            edited_inventory = st.data_editor(
                display_inv,
                hide_index=True,
                use_container_width=True,
                height=560,
                num_rows="dynamic",
                disabled=["Inventory event ID", "Log key"],
                key="inventory_editor_v12",
            )
            bundle["service_inventory"] = edited_inventory

        with result_tabs[3]:
            display_ment = _column_filters(mentions, "p_ment")
            edited_mentions = st.data_editor(
                display_ment,
                hide_index=True,
                use_container_width=True,
                height=520,
                num_rows="dynamic",
                disabled=["Mention ID", "Log key"],
                key="mentions_editor_v12",
            )
            bundle["customer_mentions"] = edited_mentions

        with result_tabs[4]:
            display_cand = _column_filters(candidates, "p_cand")
            st.dataframe(
                display_cand,
                hide_index=True,
                use_container_width=True,
                height=400,
            )

        metadata = {
            "customer_code": str(bundle["primary_customer_code"]),
            "customer_name": str(bundle["primary_customer_name"]),
            "branch": st.session_state.default_branch,
            "getfly_url": st.session_state.getfly_url,
        }

        a1, a2, a3 = st.columns(3)
        with a1:
            excel_bytes = build_excel_bytes(
                bundle,
                metadata,
                st.session_state.raw_text,
            )
            st.download_button(
                "Xuất Excel đầy đủ",
                data=excel_bytes,
                file_name=(
                    f"Getfly_CRM_{metadata['customer_code'] or 'CRM'}_"
                    f"{datetime.now():%Y%m%d_%H%M}.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
            )

        with a2:
            disabled_save = _is_production() and not _get_db_url()
            save_btn = st.button(
                "Lưu vào database",
                use_container_width=True,
                type="primary",
                disabled=disabled_save,
            )
            if disabled_save:
                st.caption("🔒 Cần cấu hình DATABASE_URL")
            if save_btn:
                try:
                    save_result = save_parse_result(bundle, metadata, created_by=PUBLIC_ACTOR)
                    if save_result.committed:
                        # load-after-save validation
                        persisted = load_customer_bundle(metadata["customer_code"])
                        expected_logs = len(bundle["logs"])
                        actual_logs = len(persisted["logs"])
                        msg = (
                            f"✅ Đã lưu: {save_result.crm_logs_inserted} log mới, "
                            f"{save_result.crm_logs_updated} log cập nhật, "
                            f"{save_result.financial_inserted} sự kiện tài chính."
                        )
                        if actual_logs < expected_logs:
                            msg += (
                                f"\n⚠️ Chỉ đọc lại được {actual_logs}/{expected_logs} log từ database. "
                                f"Một số dữ liệu có thể chưa được lưu."
                            )
                        st.success(msg)
                        st.session_state["last_saved_customer"] = metadata["customer_code"]
                        st.session_state["last_save_batch"] = save_result.batch_id
                    else:
                        st.error(f"❌ Lưu thất bại: {save_result.error_message}")
                except ValueError as exc:
                    st.error(str(exc))

        with a3:
            if st.button("Xóa kết quả hiện tại", use_container_width=True):
                st.session_state.crm_bundle = None
                st.session_state.raw_text = ""
                st.rerun()


with tab_history:
    st.subheader("Tra cứu database")
    search_code = st.text_input(
        "Mã KH chính",
        placeholder="Để trống để xem dữ liệu gần nhất",
        key="history_customer_code_v12",
    ).strip().upper()

    refresh_key = st.session_state.get("history_refresh", 0)

    history_tabs = st.tabs(
        ["CRM Log", "Financial Events", "Service Inventory / HS", "Related Customers"]
    )

    tab_keys = ["crm_logs_v2", "financial_events", "service_inventory", "customer_mentions"]
    history_frames = [
        load_logs(search_code),
        load_financial_events(search_code),
        load_inventory(search_code),
        load_mentions(search_code),
    ]

    st.markdown("---")
    col_delete_sel, _ = st.columns([1, 3])
    with col_delete_sel:
        if st.button("Xoá dòng đã chọn", type="secondary", use_container_width=True):
            selected_by_tab = st.session_state.get("history_checked_rows", {})
            all_keys = []
            for tab_idx, frame in enumerate(history_frames):
                tab_selected = selected_by_tab.get(tab_idx, set())
                if tab_selected and not frame.empty:
                    id_col = ["Log key", "Log key", "Log key", "Log key"][tab_idx]
                    if id_col in frame.columns:
                        for row_idx in tab_selected:
                            if row_idx < len(frame):
                                all_keys.append(str(frame.iloc[row_idx].get(id_col, "")))
            deleted = delete_logs([k for k in all_keys if k])
            if deleted:
                st.success(f"Đã xoá {deleted} log và dữ liệu liên quan.")
                st.session_state["history_refresh"] = refresh_key + 1
                st.rerun()
            else:
                st.warning("Không có dòng nào được chọn hoặc log key không hợp lệ.")

    # Load Getfly URL if searching a specific customer
    customer_url = load_customer_url(search_code) if search_code else ""

    for tab_idx, (history_tab, frame) in enumerate(zip(history_tabs, history_frames)):
        with history_tab:
            if frame.empty:
                st.info("Chưa có dữ liệu phù hợp.")
            else:
                frame = frame.reset_index(drop=True)
                # Add Getfly URL column to CRM Log
                if tab_idx == 0 and customer_url:
                    frame["Getfly URL"] = customer_url
                id_col = ["Log key", "Log key", "Log key", "Log key"][tab_idx]
                frame_with_check = frame.copy()
                frame_with_check.insert(0, "Chọn", False)
                disabled_cols = [c for c in frame_with_check.columns if c != "Chọn"]
                # Column-specific config
                col_config = {
                    "Chọn": st.column_config.CheckboxColumn("Chọn", default=False),
                }
                if tab_idx == 1 and "Số tiền (VND)" in frame_with_check.columns:
                    col_config["Số tiền (VND)"] = st.column_config.NumberColumn(format="%,.0f")
                # Apply column filters
                filtered = _column_filters(frame_with_check, f"h_{tab_idx}")
                edited = st.data_editor(
                    filtered,
                    column_config=col_config,
                    hide_index=True,
                    use_container_width=True,
                    height=540,
                    disabled=disabled_cols,
                    key=f"history_select_{tab_idx}_{refresh_key}",
                )
                checked = set(edited[edited["Chọn"] == True].index)
                st.session_state[f"history_checked_{tab_idx}"] = checked

    # Merge checked rows from all tabs into a dict {tab_idx: set}
    checked_by_tab = {}
    for i in range(4):
        tab_set = st.session_state.get(f"history_checked_{i}", set())
        if tab_set:
            checked_by_tab[i] = tab_set
    st.session_state["history_checked_rows"] = checked_by_tab


with tab_guide:
    st.subheader("Tổng quan ứng dụng")
    st.markdown(
        """
Ứng dụng **Getfly CRM Log** giúp tự động hoá việc trích xuất và phân tích dữ liệu
từ lịch sử CRM của khách hàng trên Getfly. Thay vì đọc thủ công từng dòng ghi chú,
bạn chỉ cần paste toàn bộ lịch sử CRM vào ô nhập liệu — hệ thống sẽ tự động nhận
diện khách hàng chính, tách log, truy xuất số tiền, công nợ, tồn dịch vụ và hồ sơ.

---

### 1. Paste & xử lý

**Đầu vào**
- **Mã KH chính** (không bắt buộc): Nhập thủ công nếu bạn biết trước mã khách hàng.
  Để trống nếu muốn hệ thống tự tìm từ dữ liệu CRM.
- **Tên KH chính** (không bắt buộc): Tương tự, nhập nếu có sẵn.
- **Cơ sở mặc định**: Chọn cơ sở (D1/HCM, D5/HCM, D2/HN hoặc Khác) để gán cho
  toàn bộ dữ liệu.
- **Getfly URL**: Đường dẫn đến trang CRM trên Getfly để tiện tra cứu sau này.

**Paste dữ liệu**: Sao chép toàn bộ lịch sử CRM từ Getfly (thường bắt đầu mỗi log
bằng tên người ghi + ngày giờ) và paste vào ô văn bản, sau đó bấm **"Phân tích dữ liệu"**.

**Kết quả**
- Hệ thống nhận diện **khách hàng chính** dựa trên tần suất xuất hiện trong log.
- Toàn bộ log được gán mã và tên khách hàng chính.
- Các bảng dữ liệu chi tiết được sinh ra: CRM Log, Financial Events,
  Service Inventory / HS, Related Customers.

**Nếu có nhiều khách hàng trong cùng một chuỗi log** (ví dụ: chủ thẻ và người dùng),
bạn có thể chọn lại khách hàng chính từ danh sách gợi ý và bấm **"Áp dụng"** để
phân tích lại.

---

### 2. Dữ liệu đã lưu

Tab này cho phép tra cứu dữ liệu đã được lưu vào database SQLite.

**Tra cứu**: Nhập mã khách hàng vào ô tìm kiếm để lọc dữ liệu. Để trống để xem
dữ liệu gần nhất.

**Các bảng dữ liệu**

| Bảng | Mô tả | Cột chính |
|------|-------|-----------|
| **CRM Log** | Mỗi dòng là một hoạt động CRM (gọi điện, chăm sóc, ghi chú, thanh toán...) | Log key, Thời gian ghi nhận, Mã KH chính, Tên khách hàng chính, Trạng thái, Nội dung CRM |
| **Financial Events** | Mỗi dòng là một sự kiện tài chính được trích xuất | Financial event ID, Loại sự kiện tài chính, Số tiền (VND), Độ tin cậy, Log key |
| **Service Inventory / HS** | Tồn dịch vụ, gói còn lại và mã hồ sơ HS | Inventory event ID, Dịch vụ/Gói còn lại, Số lượng, Đơn vị, Mã hồ sơ HS, Ngày kích hoạt, Hạn sử dụng |
| **Related Customers** | Các khách hàng liên quan được nhắc đến trong log | Mention ID, Mã KH được nhắc, Vai trò trong log, Log key |

**Các loại sự kiện tài chính được hỗ trợ**

1. **Giá trị mua/gói** — Số tiền của gói dịch vụ hoặc sản phẩm được mua.
2. **Khách thanh toán** — Số tiền khách hàng đã thanh toán.
3. **Khách còn nợ công ty** — Số dư nợ phải thu từ khách hàng.
4. **Công ty phải trả / ghi có khách hàng** — Tiền hoàn lại hoặc bồi hoàn cho khách.
5. **Công ty còn nợ thuốc/dịch vụ** — Giá trị dịch vụ chưa hoàn thành.
6. **Tiền trừ cọc, thẻ hoặc tài khoản** — Các khoản khấu trừ từ nguồn tiền có sẵn.
7. **Số dư cọc** — Số tiền cọc còn lại của khách hàng.
8. **Số dư tài khoản chính** — Số dư trong tài khoản chính.
9. **Số dư tài khoản tặng** — Số dư trong tài khoản khuyến mãi / tặng.
10. **Số dư voucher** — Giá trị voucher còn lại.
11. **Tiền tặng** — Tiền được cấp thêm từ chương trình khuyến mãi.
12. **Giảm giá** — Chiết khấu hoặc giảm giá trực tiếp.
13. **Tiền bù thêm** — Khoản bù chênh lệch từ công ty.

**Quản lý dữ liệu**
- **Xoá dòng đã chọn**: Đánh dấu các dòng trong bảng, sau đó bấm nút để xoá.
- **Xoá toàn bộ database**: Xoá sạch tất cả dữ liệu trong database (cần xác nhận).

---

### 3. Service Inventory / HS — Chi tiết

Bảng này ghi nhận các dịch vụ / gói còn tồn của khách hàng. Mỗi dòng bao gồm:

- **Dịch vụ/Gói còn lại**: Tên gói dịch vụ hoặc sản phẩm.
- **Số lượng**: Số lượng còn lại (có thể là số lần, liệu trình, buổi...).
- **Đơn vị**: Đơn vị tính (buổi, liệu trình, tháng, chai, lọ...).
- **Trạng thái**: Trạng thái hiện tại (còn hiệu lực, đã hết, đã huỷ...).
- **Ngày kích hoạt**: Ngày gói dịch vụ bắt đầu có hiệu lực.
- **Hạn sử dụng**: Ngày hết hạn của gói dịch vụ.
- **Mã hồ sơ HS**: Danh sách mã hồ sơ liên quan (bắt đầu bằng `HS`), cách nhau bởi dấu phẩy.

---

### 4. Related Customers — Vai trò trong log

Hệ thống phân loại các khách hàng được nhắc đến trong log theo các vai trò sau:

| Vai trò | Mô tả |
|---------|-------|
| **Khách hàng chính** | Khách hàng được xác định là chủ thể chính của toàn bộ dữ liệu |
| **Người sử dụng dịch vụ** | Người trực tiếp sử dụng dịch vụ / sản phẩm (có thể khác người mua) |
| **Chủ thẻ / chủ nguồn tiền** | Người sở hữu thẻ thành viên hoặc nguồn thanh toán |
| **Khác** | Các khách hàng khác được nhắc đến trong ghi chú |

---

### 5. Nguyên tắc kiểm tra và độ tin cậy

Các số tiền được nhận diện từ **văn bản ghi chú tự do**, không phải từ trường
dữ liệu có cấu trúc. Do đó, độ chính xác phụ thuộc vào:

1. **Tính nhất quán của dữ liệu đầu vào**: Nếu CRM được ghi theo một format nhất
   định, tỷ lệ nhận diện đúng sẽ cao.
2. **Độ tin cậy (Confidence)**:
   - **Cao**: Số tiền được tìm thấy ngay sau từ khoá rõ ràng (Tổng tiền, Thanh toán...).
   - **Trung bình**: Số tiền được suy luận từ ngữ cảnh nhưng không có từ khoá trực tiếp.
   - **Thấp**: Số tiền được ước lượng từ các dữ liệu xung quanh, cần kiểm tra thủ công.

**Khuyến nghị**: Luôn kiểm tra các dòng có độ tin cậy **Trung bình** hoặc **Thấp**
trước khi sử dụng cho báo cáo tài chính chính thức.

---

### 6. Xuất dữ liệu

Sau khi phân tích, bạn có thể **xuất Excel** đầy đủ các bảng dữ liệu — mỗi bảng
là một sheet riêng trong cùng một file. File Excel bao gồm:

- Sheet **Hướng dẫn** — thông tin metadata và chú thích.
- Sheet **CRM Log** — toàn bộ log đã được xử lý.
- Sheet **Financial Events** — tất cả sự kiện tài chính.
- Sheet **Service Inventory** — tồn dịch vụ / hồ sơ.
- Sheet **Related Customers** — khách hàng liên quan.
- Sheet **Raw CRM** — dữ liệu gốc paste vào.

---
        """
    )
