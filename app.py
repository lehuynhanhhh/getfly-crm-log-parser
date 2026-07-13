
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from crm_parser import DEFAULT_STATUS_MAPPING, parse_crm_bundle
from database import (
    database_stats,
    init_db,
    load_financial_events,
    load_inventory,
    load_logs,
    load_mentions,
    save_bundle,
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


st.title("Getfly CRM Log Parser")
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
    st.metric("Khách hàng đã lưu", customer_count)
    st.metric("Log đã lưu", log_count)
    st.metric("Sự kiện tài chính", financial_count)
    st.metric("Dòng tồn dịch vụ", inventory_count)


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
            edited_logs = st.data_editor(
                logs,
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
            edited_financial = st.data_editor(
                financial,
                hide_index=True,
                use_container_width=True,
                height=560,
                num_rows="dynamic",
                disabled=["Financial event ID", "Log key"],
                column_config={
                    "Số tiền (VND)": st.column_config.NumberColumn(format="%d"),
                },
                key="financial_editor_v12",
            )
            bundle["financial_events"] = edited_financial

        with result_tabs[2]:
            edited_inventory = st.data_editor(
                inventory,
                hide_index=True,
                use_container_width=True,
                height=560,
                num_rows="dynamic",
                disabled=["Inventory event ID", "Log key"],
                key="inventory_editor_v12",
            )
            bundle["service_inventory"] = edited_inventory

        with result_tabs[3]:
            edited_mentions = st.data_editor(
                mentions,
                hide_index=True,
                use_container_width=True,
                height=520,
                num_rows="dynamic",
                disabled=["Mention ID", "Log key"],
                key="mentions_editor_v12",
            )
            bundle["customer_mentions"] = edited_mentions

        with result_tabs[4]:
            st.dataframe(
                candidates,
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
            if st.button(
                "Lưu vào database",
                use_container_width=True,
                type="primary",
            ):
                try:
                    inserted, updated = save_bundle(bundle, metadata)
                    st.success(
                        f"Đã thêm {inserted} log mới và cập nhật {updated} log đã tồn tại."
                    )
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

    history_tabs = st.tabs(
        ["CRM Log", "Financial Events", "Service Inventory / HS", "Related Customers"]
    )

    history_frames = [
        load_logs(search_code),
        load_financial_events(search_code),
        load_inventory(search_code),
        load_mentions(search_code),
    ]
    for history_tab, frame in zip(history_tabs, history_frames):
        with history_tab:
            if frame.empty:
                st.info("Chưa có dữ liệu phù hợp.")
            else:
                st.dataframe(
                    frame,
                    hide_index=True,
                    use_container_width=True,
                    height=540,
                )


with tab_guide:
    st.subheader("Các bảng dữ liệu")
    st.markdown(
        """
**CRM Log** — một dòng cho mỗi hoạt động CRM. Mã và tên khách hàng chính được
điền trên toàn bộ dòng.

**Financial Events** — một dòng cho mỗi sự kiện tiền:

- Giá trị mua/gói
- Khách thanh toán
- Khách còn nợ công ty
- Công ty phải trả hoặc ghi có khách hàng
- Công ty còn nợ thuốc/dịch vụ
- Tiền trừ cọc, thẻ hoặc tài khoản
- Số dư cọc, tài khoản chính, tài khoản tặng và voucher
- Tiền tặng, giảm giá và tiền bù thêm

**Service Inventory / HS** — tồn dịch vụ, số lượng, đơn vị, trạng thái,
ngày kích hoạt, hạn sử dụng và toàn bộ mã hồ sơ bắt đầu bằng `HS`.

**Related Customers** — phân biệt khách hàng chính, người sử dụng dịch vụ,
chủ thẻ/chủ nguồn tiền và những khách hàng khác được nhắc trong log.

### Nguyên tắc kiểm tra

Các số tiền được nhận diện từ ghi chú tự do. Luôn kiểm tra các dòng có độ
tin cậy Trung bình hoặc Thấp trước khi sử dụng cho báo cáo tài chính chính thức.
        """
    )
