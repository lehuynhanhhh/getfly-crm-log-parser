
from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping

import pandas as pd


MONEY_COLUMNS = {
    "Giá trị mua (VND)",
    "Đã thanh toán (VND)",
    "KH còn nợ công ty (VND)",
    "Công ty phải trả/ghi có KH (VND)",
    "Số tiền trừ cọc/thẻ (VND)",
    "Số dư cọc (VND)",
    "Số dư TK chính (VND)",
    "Số dư TK tặng (VND)",
    "Số dư voucher (VND)",
    "Tiền tặng/khuyến mãi (VND)",
    "Tiền bù thêm (VND)",
    "Số tiền (VND)",
}


def _as_bundle(data: Any) -> dict[str, pd.DataFrame]:
    if isinstance(data, pd.DataFrame):
        return {
            "logs": data.copy(),
            "financial_events": pd.DataFrame(),
            "service_inventory": pd.DataFrame(),
            "customer_mentions": pd.DataFrame(),
            "customer_candidates": pd.DataFrame(),
        }
    if isinstance(data, Mapping):
        return {
            "logs": data.get("logs", pd.DataFrame()).copy(),
            "financial_events": data.get("financial_events", pd.DataFrame()).copy(),
            "service_inventory": data.get("service_inventory", pd.DataFrame()).copy(),
            "customer_mentions": data.get("customer_mentions", pd.DataFrame()).copy(),
            "customer_candidates": data.get("customer_candidates", pd.DataFrame()).copy(),
        }
    raise TypeError("Dữ liệu xuất Excel phải là DataFrame hoặc CRM bundle.")


def _prepare_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    output = data.copy()
    for column in output.columns:
        if "Thời gian" in column:
            output[column] = pd.to_datetime(output[column], errors="coerce")
        elif column in {"Ngày"}:
            output[column] = pd.to_datetime(output[column], errors="coerce")
    return output


def _latest_balance(financial: pd.DataFrame, event_type: str) -> int | None:
    if financial.empty:
        return None
    subset = financial[financial["Loại sự kiện tài chính"] == event_type].copy()
    if subset.empty:
        return None
    subset["Thời gian ghi nhận"] = pd.to_datetime(
        subset["Thời gian ghi nhận"], errors="coerce"
    )
    subset["Số tiền (VND)"] = pd.to_numeric(
        subset["Số tiền (VND)"], errors="coerce"
    )
    subset = subset.dropna(subset=["Số tiền (VND)"])
    if subset.empty:
        return None
    latest_time = subset["Thời gian ghi nhận"].max()
    latest = subset[subset["Thời gian ghi nhận"] == latest_time]
    if event_type in {"Số dư tài khoản tặng", "Số dư voucher"}:
        return int(latest["Số tiền (VND)"].sum())
    return int(latest.iloc[-1]["Số tiền (VND)"])


def build_excel_bytes(
    data: Any,
    metadata: Mapping[str, str],
    raw_text: str,
) -> bytes:
    """Tạo Excel nhiều bảng: log, tài chính, tồn dịch vụ, KH liên quan và raw input."""
    bundle = _as_bundle(data)
    logs = _prepare_dataframe(bundle["logs"])
    financial = _prepare_dataframe(bundle["financial_events"])
    inventory = _prepare_dataframe(bundle["service_inventory"])
    mentions = _prepare_dataframe(bundle["customer_mentions"])
    candidates = bundle["customer_candidates"].copy()

    output = BytesIO()
    with pd.ExcelWriter(
        output,
        engine="xlsxwriter",
        datetime_format="dd/mm/yyyy hh:mm",
        date_format="dd/mm/yyyy",
    ) as writer:
        workbook = writer.book

        primary = "#2E8E90"
        accent = "#67C8C9"
        light = "#EAF8F8"
        border = "#D1D5DB"
        white = "#FFFFFF"
        dark = "#1F2937"
        warning = "#FEF3C7"

        title_fmt = workbook.add_format(
            {
                "bold": True,
                "font_size": 18,
                "font_color": white,
                "bg_color": primary,
                "align": "center",
                "valign": "vcenter",
            }
        )
        section_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": dark,
                "bg_color": accent,
                "border": 1,
                "border_color": border,
                "align": "center",
                "valign": "vcenter",
            }
        )
        label_fmt = workbook.add_format(
            {"bold": True, "border": 1, "border_color": border}
        )
        value_fmt = workbook.add_format(
            {"border": 1, "border_color": border}
        )
        money_value_fmt = workbook.add_format(
            {"border": 1, "border_color": border, "num_format": "#,##0"}
        )
        date_value_fmt = workbook.add_format(
            {
                "border": 1,
                "border_color": border,
                "num_format": "dd/mm/yyyy hh:mm",
            }
        )
        note_fmt = workbook.add_format(
            {
                "italic": True,
                "bg_color": light,
                "font_color": dark,
                "text_wrap": True,
                "valign": "top",
            }
        )

        # Summary
        summary_ws = workbook.add_worksheet("Tổng quan")
        writer.sheets["Tổng quan"] = summary_ws
        summary_ws.merge_range("A1:H2", "GETFLY CRM – PHÂN TÍCH LOG KHÁCH HÀNG", title_fmt)
        summary_ws.set_row(0, 25)
        summary_ws.set_row(1, 25)

        primary_code = metadata.get("customer_code", "")
        primary_name = metadata.get("customer_name", "")
        if not logs.empty:
            primary_code = primary_code or str(logs.iloc[0].get("Mã KH chính", ""))
            primary_name = primary_name or str(logs.iloc[0].get("Tên khách hàng chính", ""))

        summary_rows = [
            ("Mã khách hàng chính", primary_code),
            ("Tên khách hàng chính", primary_name),
            ("Nguồn nhận diện", str(logs.iloc[0].get("Nguồn KH chính", "")) if not logs.empty else ""),
            ("Cơ sở mặc định", metadata.get("branch", "")),
            ("Getfly URL", metadata.get("getfly_url", "")),
            ("Tổng số log", len(logs)),
            ("Sự kiện tài chính", len(financial)),
            ("Dòng tồn dịch vụ/hồ sơ", len(inventory)),
            ("Khách hàng liên quan", mentions["Mã KH được nhắc"].nunique() if not mentions.empty else 0),
            ("Từ ngày", logs["Thời gian ghi nhận"].min() if not logs.empty else ""),
            ("Đến ngày", logs["Thời gian ghi nhận"].max() if not logs.empty else ""),
        ]

        summary_ws.write("A4", "Thông tin", section_fmt)
        summary_ws.write("B4", "Giá trị", section_fmt)
        for row_num, (label, value) in enumerate(summary_rows, start=4):
            summary_ws.write(row_num, 0, label, label_fmt)
            if isinstance(value, pd.Timestamp):
                summary_ws.write_datetime(row_num, 1, value.to_pydatetime(), date_value_fmt)
            else:
                summary_ws.write(row_num, 1, value, value_fmt)

        financial_summary = [
            ("Số dư cọc gần nhất", _latest_balance(financial, "Số dư cọc")),
            ("Số dư TK chính gần nhất", _latest_balance(financial, "Số dư tài khoản chính")),
            ("Tổng TK tặng tại lần ghi nhận gần nhất", _latest_balance(financial, "Số dư tài khoản tặng")),
            ("Tổng voucher tại lần ghi nhận gần nhất", _latest_balance(financial, "Số dư voucher")),
        ]
        summary_ws.write("D4", "Chỉ tiêu tài chính", section_fmt)
        summary_ws.write("E4", "Giá trị (VND)", section_fmt)
        for row_num, (label, value) in enumerate(financial_summary, start=4):
            summary_ws.write(row_num, 3, label, label_fmt)
            if value is None:
                summary_ws.write(row_num, 4, "", money_value_fmt)
            else:
                summary_ws.write_number(row_num, 4, int(value), money_value_fmt)

        event_counts = (
            financial["Loại sự kiện tài chính"].value_counts()
            if not financial.empty else pd.Series(dtype=int)
        )
        summary_ws.write("G4", "Loại sự kiện", section_fmt)
        summary_ws.write("H4", "Số dòng", section_fmt)
        for row_num, (event_type, count) in enumerate(event_counts.head(12).items(), start=4):
            summary_ws.write(row_num, 6, event_type, value_fmt)
            summary_ws.write_number(row_num, 7, int(count), value_fmt)

        summary_ws.merge_range(
            "A18:H20",
            "Lưu ý: Các trường số tiền được nhận diện từ nội dung tự do trong CRM. "
            "Số dư gần nhất là ảnh chụp tại lần log gần nhất có ghi nhận, không thay thế xác nhận kế toán. "
            "Các dòng có độ tin cậy Trung bình/Thấp cần được kiểm tra trước khi sử dụng.",
            note_fmt,
        )
        summary_ws.set_column("A:A", 29)
        summary_ws.set_column("B:B", 32)
        summary_ws.set_column("C:C", 3)
        summary_ws.set_column("D:D", 38)
        summary_ws.set_column("E:E", 19)
        summary_ws.set_column("F:F", 3)
        summary_ws.set_column("G:G", 38)
        summary_ws.set_column("H:H", 12)
        summary_ws.freeze_panes(2, 0)

        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": white,
                "bg_color": primary,
                "border": 1,
                "border_color": border,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        body_fmt = workbook.add_format(
            {"border": 1, "border_color": border, "valign": "top"}
        )
        wrap_fmt = workbook.add_format(
            {
                "border": 1,
                "border_color": border,
                "valign": "top",
                "text_wrap": True,
            }
        )
        dt_fmt = workbook.add_format(
            {
                "num_format": "dd/mm/yyyy hh:mm",
                "border": 1,
                "border_color": border,
                "valign": "top",
            }
        )
        d_fmt = workbook.add_format(
            {
                "num_format": "dd/mm/yyyy",
                "border": 1,
                "border_color": border,
                "valign": "top",
            }
        )
        money_fmt = workbook.add_format(
            {
                "num_format": "#,##0",
                "border": 1,
                "border_color": border,
                "valign": "top",
            }
        )

        def write_table(sheet_name: str, dataframe: pd.DataFrame, freeze_cols: int = 2) -> None:
            frame = dataframe.copy()
            if frame.empty and len(frame.columns) == 0:
                frame = pd.DataFrame({"Thông báo": ["Không có dữ liệu"]})
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, freeze_cols)
            worksheet.autofilter(
                0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0)
            )
            worksheet.set_row(0, 34)

            for col_num, col_name in enumerate(frame.columns):
                worksheet.write(0, col_num, col_name, header_fmt)
                if len(frame):
                    lengths = frame[col_name].astype(str).str.len()
                    q90 = lengths.quantile(0.9)
                    width = int(q90 + 2) if pd.notna(q90) else len(str(col_name)) + 2
                else:
                    width = len(str(col_name)) + 2
                width = min(max(width, len(str(col_name)) + 2, 10), 42)

                if col_name in {"Nội dung gốc", "Dòng nguồn", "Bằng chứng mẫu"}:
                    width = 58
                elif col_name in {"Log key", "Financial event ID", "Inventory event ID", "Mention ID"}:
                    width = 27
                elif col_name in MONEY_COLUMNS:
                    width = 20
                worksheet.set_column(col_num, col_num, width)

            for row_num in range(1, len(frame) + 1):
                worksheet.set_row(row_num, 32)
                for col_num, col_name in enumerate(frame.columns):
                    value = frame.iloc[row_num - 1, col_num]
                    if pd.isna(value):
                        value = ""
                    if col_name in MONEY_COLUMNS and value != "":
                        try:
                            worksheet.write_number(row_num, col_num, float(value), money_fmt)
                        except (TypeError, ValueError):
                            worksheet.write(row_num, col_num, value, body_fmt)
                    elif "Thời gian" in col_name and isinstance(value, pd.Timestamp):
                        worksheet.write_datetime(row_num, col_num, value.to_pydatetime(), dt_fmt)
                    elif col_name == "Ngày" and isinstance(value, pd.Timestamp):
                        worksheet.write_datetime(row_num, col_num, value.to_pydatetime(), d_fmt)
                    elif col_name in {"Nội dung gốc", "Dòng nguồn", "Bằng chứng mẫu"}:
                        worksheet.write(row_num, col_num, str(value), wrap_fmt)
                    else:
                        worksheet.write(row_num, col_num, value, body_fmt)

            if "Độ tin cậy" in frame.columns and len(frame):
                col = frame.columns.get_loc("Độ tin cậy")
                worksheet.conditional_format(
                    1, col, len(frame), col,
                    {
                        "type": "text",
                        "criteria": "containing",
                        "value": "Trung bình",
                        "format": workbook.add_format(
                            {"bg_color": warning, "font_color": "#92400E"}
                        ),
                    },
                )
                worksheet.conditional_format(
                    1, col, len(frame), col,
                    {
                        "type": "text",
                        "criteria": "containing",
                        "value": "Thấp",
                        "format": workbook.add_format(
                            {"bg_color": "#FECACA", "font_color": "#991B1B"}
                        ),
                    },
                )

        write_table("CRM_Log", logs, freeze_cols=3)
        write_table("Financial_Events", financial, freeze_cols=4)
        write_table("Service_Inventory", inventory, freeze_cols=4)
        write_table("Related_Customers", mentions, freeze_cols=4)
        write_table("Customer_Candidates", candidates, freeze_cols=2)

        raw_ws = workbook.add_worksheet("Raw_Input")
        writer.sheets["Raw_Input"] = raw_ws
        raw_ws.write("A1", "Nội dung CRM nguyên bản", section_fmt)
        raw_ws.write("A2", raw_text or "", wrap_fmt)
        raw_ws.set_column("A:A", 120)
        raw_ws.set_row(1, 520)
        raw_ws.freeze_panes(1, 0)

    output.seek(0)
    return output.getvalue()
