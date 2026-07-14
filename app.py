
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from crm_parser import DEFAULT_STATUS_MAPPING, parse_crm_bundle
from database import (
    database_stats,
    delete_all_data,
    delete_logs,
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
                    "Số tiền (VND)": st.column_config.NumberColumn(format="%,.0f"),
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
    col_delete_sel, col_delete_all, _ = st.columns([1, 1, 2])
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
    with col_delete_all:
        if st.button("Xoá toàn bộ database", type="secondary", use_container_width=True):
            st.session_state["confirm_delete_all"] = True

    if st.session_state.get("confirm_delete_all"):
        st.warning("Bạn có chắc chắn muốn xoá TOÀN BỘ dữ liệu? Hành động này không thể hoàn tác.")
        c_confirm, c_cancel = st.columns([1, 1])
        with c_confirm:
            if st.button("Xác nhận xoá tất cả", type="primary", use_container_width=True):
                counts = delete_all_data()
                total = sum(counts.values())
                st.success(f"Đã xoá {total} dòng khỏi database.")
                st.session_state["confirm_delete_all"] = False
                st.session_state["history_refresh"] = refresh_key + 1
                st.rerun()
        with c_cancel:
            if st.button("Huỷ", use_container_width=True):
                st.session_state["confirm_delete_all"] = False
                st.rerun()

    for tab_idx, (history_tab, frame) in enumerate(zip(history_tabs, history_frames)):
        with history_tab:
            if frame.empty:
                st.info("Chưa có dữ liệu phù hợp.")
            else:
                frame = frame.reset_index(drop=True)
                id_col = ["Log key", "Log key", "Log key", "Log key"][tab_idx]
                frame_with_check = frame.copy()
                frame_with_check.insert(0, "Chọn", False)
                disabled_cols = [c for c in frame_with_check.columns if c != "Chọn"]
                selection_key = f"history_select_{tab_idx}_{refresh_key}"
                edited = st.data_editor(
                    frame_with_check,
                    column_config={
                        "Chọn": st.column_config.CheckboxColumn("Chọn", default=False),
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=540,
                    disabled=disabled_cols,
                    key=selection_key,
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
