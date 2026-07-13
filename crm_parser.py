
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Mapping

import pandas as pd


DEFAULT_STATUS_MAPPING = {
    "KNM": "Khách hàng mới",
    "HT": "Hoàn thành",
    "TB": "Cần xác nhận",
    "PTT": "Cần xác nhận",
    "PTTG": "Cần xác nhận",
    "NLC": "Cần xác nhận",
    "NL": "Cần xác nhận",
    "KGD": "Không gọi được",
}

ROLE_PREFIXES = (
    "QHKH",
    "Lễ tân",
    "CSKH",
    "ĐTGB",
    "GSCG",
    "MKT",
    "Sale",
    "Sales",
    "Tư vấn",
    "Bác sĩ",
    "BS",
    "KTV",
    "Y tá",
)

ABS_HEADER_RE = re.compile(
    r"^(?P<author>.+?)(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<time>\d{1,2}:\d{2})(?P<edited>\s*\(Đã chỉnh sửa\))?\s*$",
    re.IGNORECASE,
)

REL_HEADER_RE = re.compile(
    r"^(?P<author>.+?)(?P<relative>\d+\s+ngày trước|hôm qua|hôm nay)"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}))?"
    r"(?P<edited>\s*\(Đã chỉnh sửa\))?\s*$",
    re.IGNORECASE,
)

CUSTOMER_CODE_RE = re.compile(r"\bKH\d{5,9}\b", re.IGNORECASE)
PROFILE_CODE_RE = re.compile(r"\bHS\d{6,12}\b", re.IGNORECASE)
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)")
EXPIRY_RE = re.compile(
    r"(?:HẾT HẠN|HẠN)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.IGNORECASE,
)
ACTIVATION_RE = re.compile(
    r"(?:KÍCH|KICH)\s+NGÀY\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.IGNORECASE,
)

# Money formats commonly used in Vietnamese CRM notes:
# 390tr, 880tr600, 2tr1, 8tr400, 1ty2, 600k, 31,512,938, 220.923.000
AMOUNT_PATTERNS = [
    (re.compile(r"(?<![\w])(?P<bil>\d+(?:[.,]\d+)?)\s*(?:tỷ|tỉ|ty)(?P<tail>\d{1,3})?", re.I), 1_000_000_000, "bil"),
    (re.compile(r"(?<![\w])(?P<mil>\d+(?:[.,]\d+)?)\s*(?:tr(?![A-Za-zÀ-ỹ])|triệu)(?P<tail>\d{1,3})?", re.I), 1_000_000, "mil"),
    (re.compile(r"(?<![\w])(?P<thou>\d+(?:[.,]\d+)?)\s*(?:k|nghìn)(?![\w])", re.I), 1_000, "thou"),
    (re.compile(r"(?<![\w])(?P<sep>\d{1,3}(?:[.,]\d{3}){1,4})(?![\w%])"), 1, "sep"),
]

STOP_NAME_WORDS = {
    "SD", "THE", "TRU", "COC", "TOA", "THUOC", "HT", "NL", "HOI", "THAM",
    "KHACH", "CON", "HET", "TK", "TAI", "KHOAN", "GOI", "DICH", "VU", "MUA",
    "KICH", "CHINH", "TANG", "SAU", "TRUOC", "PHI", "NGAY", "LOAI",
    "DRIPCARE", "DRIP", "THANH", "TOAN", "CUA", "CHO", "HO TRO", "NOTE",
}
HONORIFICS = {"CHI", "CO", "ONG", "BA", "ANH", "EM", "BE"}

LOG_COLUMNS = [
    "STT",
    "Mã KH chính",
    "Tên khách hàng chính",
    "Nguồn KH chính",
    "Thời gian ghi nhận",
    "Nguồn thời gian",
    "Ngày",
    "Giờ",
    "Người ghi (nguyên bản)",
    "Vai trò/Bộ phận",
    "Người phụ trách",
    "Đã chỉnh sửa",
    "Mã trạng thái",
    "Trạng thái chuẩn",
    "Nhóm nội dung",
    "Cơ sở",
    "Ngày dịch vụ",
    "Mã KH được nhắc",
    "Tên KH được nhắc",
    "Mã KH sử dụng dịch vụ",
    "Tên KH sử dụng dịch vụ",
    "Mã KH nguồn tiền/thẻ",
    "Tên KH nguồn tiền/thẻ",
    "Mã hồ sơ HS",
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
    "Tình trạng thanh toán",
    "Trạng thái nghiệp vụ",
    "Nội dung gốc",
    "Độ tin cậy",
    "Log key",
]

FINANCIAL_COLUMNS = [
    "Financial event ID",
    "Log key",
    "Mã KH chính",
    "Tên khách hàng chính",
    "Thời gian ghi nhận",
    "Loại sự kiện tài chính",
    "Góc nhìn công nợ",
    "Số tiền (VND)",
    "Tài khoản/Nguồn tiền",
    "Tình trạng thanh toán",
    "Mã KH sử dụng dịch vụ",
    "Tên KH sử dụng dịch vụ",
    "Mã KH nguồn tiền/thẻ",
    "Tên KH nguồn tiền/thẻ",
    "Hạn sử dụng",
    "Tỷ lệ giảm/tặng",
    "Dòng nguồn",
    "Độ tin cậy",
]

INVENTORY_COLUMNS = [
    "Inventory event ID",
    "Log key",
    "Mã KH chính",
    "Tên khách hàng chính",
    "Thời gian ghi nhận",
    "Mã KH sở hữu",
    "Tên KH sở hữu",
    "Mã KH sử dụng dịch vụ",
    "Tên KH sử dụng dịch vụ",
    "Trạng thái tồn",
    "Dịch vụ/Gói còn lại",
    "Số lượng chính",
    "Đơn vị chính",
    "Chi tiết số lượng",
    "Là quà tặng",
    "Ngày kích hoạt",
    "Hạn sử dụng",
    "Mã hồ sơ HS",
    "Dòng nguồn",
    "Độ tin cậy",
]

CUSTOMER_MENTION_COLUMNS = [
    "Mention ID",
    "Log key",
    "Mã KH chính",
    "Tên khách hàng chính",
    "Thời gian ghi nhận",
    "Mã KH được nhắc",
    "Tên KH được nhắc",
    "Vai trò trong log",
    "Dòng nguồn",
    "Độ tin cậy",
]

CANDIDATE_COLUMNS = [
    "Mã KH",
    "Tên khách hàng",
    "Điểm nhận diện",
    "Số lần xuất hiện",
    "Bằng chứng mẫu",
]


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    return (
        "".join(c for c in normalized if unicodedata.category(c) != "Mn")
        .replace("đ", "d")
        .replace("Đ", "D")
    )


def normalize_lines(raw_text: str) -> list[str]:
    text = raw_text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for old, new in {
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "\xa0": " ",
    }.items():
        text = text.replace(old, new)

    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line or line.casefold() == "thích".casefold():
            continue
        lines.append(line)
    return lines


def _parse_header(line: str, reference_date: date) -> dict[str, Any] | None:
    absolute = ABS_HEADER_RE.match(line)
    if absolute:
        dt = datetime.strptime(
            f"{absolute.group('date')} {absolute.group('time')}",
            "%d/%m/%Y %H:%M",
        )
        return {
            "author": absolute.group("author").strip(),
            "log_datetime": dt,
            "edited": bool(absolute.group("edited")),
            "time_source": "Tuyệt đối",
        }

    relative = REL_HEADER_RE.match(line)
    if relative:
        relative_text = strip_accents(relative.group("relative")).lower()
        if relative_text == "hom nay":
            days_ago = 0
        elif relative_text == "hom qua":
            days_ago = 1
        else:
            day_match = re.search(r"\d+", relative_text)
            days_ago = int(day_match.group()) if day_match else 0

        time_text = relative.group("time") or "00:00"
        inferred_date = reference_date - timedelta(days=days_ago)
        dt = datetime.strptime(
            f"{inferred_date:%d/%m/%Y} {time_text}",
            "%d/%m/%Y %H:%M",
        )
        return {
            "author": relative.group("author").strip(),
            "log_datetime": dt,
            "edited": bool(relative.group("edited")),
            "time_source": "Suy luận từ thời gian tương đối",
        }

    return None


def split_crm_logs(
    raw_text: str,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    reference_date = reference_date or datetime.now().date()
    logs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in normalize_lines(raw_text):
        header = _parse_header(line, reference_date)
        if header:
            if current:
                logs.append(current)
            current = {**header, "content_lines": []}
        elif current:
            current["content_lines"].append(line)

    if current:
        logs.append(current)
    return logs


def _split_author(author: str) -> tuple[str, str]:
    author = author.strip()
    for prefix in ROLE_PREFIXES:
        if author.casefold().startswith(prefix.casefold() + " "):
            return prefix, author[len(prefix):].strip()
    if author.casefold().startswith("lễ tân"):
        return "Lễ tân", author
    return "", author


def _normalize_name_candidate(name: str) -> str:
    text = re.sub(r"[^A-Za-zÀ-ỹĐđ\s.]", " ", name or "")
    tokens = [token.strip(". ") for token in re.split(r"\s+", text) if token.strip(". ")]

    while tokens and strip_accents(tokens[0]).upper() in HONORIFICS:
        tokens = tokens[1:]

    output: list[str] = []
    for token in tokens:
        key = strip_accents(token).upper()
        if output and key in STOP_NAME_WORDS:
            break
        output.append(token)

    while output and strip_accents(output[0]).upper() in STOP_NAME_WORDS:
        output = output[1:]

    if not 2 <= len(output) <= 6:
        return ""
    if any(strip_accents(token).upper() in STOP_NAME_WORDS for token in output):
        return ""
    if any(len(token) < 2 for token in output):
        return ""

    return " ".join(output).upper()


def extract_customer_pairs_from_line(line: str) -> list[tuple[str, str]]:
    compact = re.sub(r"\s+", " ", (line or "").replace("\u200b", "")).strip()
    results: list[tuple[str, str]] = []

    for code_match in CUSTOMER_CODE_RE.finditer(compact):
        code = code_match.group().upper()
        before = compact[: code_match.start()].strip(" :-")
        after = compact[code_match.end() :].strip(" :-")

        before_tokens = before.split()
        for size in range(min(6, len(before_tokens)), 1, -1):
            candidate = _normalize_name_candidate(" ".join(before_tokens[-size:]))
            if candidate:
                results.append((code, candidate))
                break

        after_tokens = after.split()
        for size in range(min(6, len(after_tokens)), 1, -1):
            candidate = _normalize_name_candidate(" ".join(after_tokens[:size]))
            if candidate:
                results.append((code, candidate))
                break

    unique: list[tuple[str, str]] = []
    for item in results:
        if item not in unique:
            unique.append(item)
    return unique


def detect_customer_candidates(
    raw_text: str,
) -> pd.DataFrame:
    scores: defaultdict[str, float] = defaultdict(float)
    names: defaultdict[str, Counter] = defaultdict(Counter)
    evidence: defaultdict[str, list[str]] = defaultdict(list)

    for line in normalize_lines(raw_text):
        normalized = strip_accents(line).upper()
        pairs = extract_customer_pairs_from_line(line)
        for code, name in pairs:
            names[code][name] += 1
            score = 1.0
            if "MA KH" in normalized:
                score += 8
            if "CHU THE" in normalized:
                score += 10
            if re.search(r"\bSD(?: THE)?\b", normalized):
                score += 1.5
            if any(
                keyword in normalized
                for keyword in ("TRU COC", "TRU THE", "TRU TK", "COC CON", "TK CON", "THE TK")
            ):
                score += 3
            if ".PDF" in normalized or ".JPG" in normalized or ".PNG" in normalized:
                score += 2
            scores[code] += score
            if len(evidence[code]) < 5:
                evidence[code].append(line)

    rows: list[dict[str, Any]] = []
    for code, base_score in scores.items():
        best_name, count = names[code].most_common(1)[0]
        rows.append(
            {
                "Mã KH": code,
                "Tên khách hàng": best_name,
                "Điểm nhận diện": round(base_score + count * 0.5, 2),
                "Số lần xuất hiện": int(count),
                "Bằng chứng mẫu": "\n".join(evidence[code]),
            }
        )

    if not rows:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    return (
        pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
        .sort_values(["Điểm nhận diện", "Số lần xuất hiện"], ascending=False)
        .reset_index(drop=True)
    )


def _tail_fraction(tail: str | None, scale: int) -> int:
    if not tail:
        return 0
    return int(tail) * (scale // (10 ** len(tail)))


def extract_amounts(text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    for pattern, scale, group_name in AMOUNT_PATTERNS:
        for match in pattern.finditer(text or ""):
            if any(not (match.end() <= start or match.start() >= end) for start, end in occupied):
                continue

            if group_name == "sep":
                value = int(re.sub(r"\D", "", match.group("sep")))
            else:
                number_text = match.group(group_name)
                if group_name == "thou" and re.fullmatch(r"\d{1,3}[.,]\d{3}", number_text):
                    number = int(re.sub(r"\D", "", number_text))
                else:
                    number = float(number_text.replace(",", "."))
                value = int(round(number * scale))
                if group_name in {"bil", "mil"}:
                    value += _tail_fraction(match.groupdict().get("tail"), scale)

            matches.append(
                {
                    "raw": match.group(0),
                    "value": value,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
            occupied.append((match.start(), match.end()))

    return sorted(matches, key=lambda item: item["start"])


def _nearest_amount(
    line: str,
    keyword_match: re.Match[str] | None = None,
    prefer_after: bool = True,
) -> dict[str, Any] | None:
    amounts = extract_amounts(line)
    if not amounts:
        return None
    if keyword_match is None:
        return amounts[0]

    after = [item for item in amounts if item["start"] >= keyword_match.end()]
    before = [item for item in amounts if item["end"] <= keyword_match.start()]

    if prefer_after and after:
        return min(after, key=lambda item: item["start"] - keyword_match.end())
    if before:
        return max(before, key=lambda item: item["value"])
    if after:
        return min(after, key=lambda item: item["start"] - keyword_match.end())
    return amounts[0]


def _parse_date_text(text: str) -> str:
    match = DATE_RE.search(text or "")
    if not match:
        return ""
    day, month, year = match.groups()
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    try:
        return datetime(year_int, int(month), int(day)).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _extract_expiry(line: str) -> str:
    match = EXPIRY_RE.search(line or "")
    return _parse_date_text(match.group(1)) if match else ""


def _extract_activation(line: str) -> str:
    match = ACTIVATION_RE.search(line or "")
    return _parse_date_text(match.group(1)) if match else ""


def _extract_visit_date(content: str, log_datetime: datetime) -> str:
    for day, month, year in DATE_RE.findall(content or ""):
        year_int = int(year)
        if year_int < 100:
            year_int += 2000
        try:
            candidate = datetime(year_int, int(month), int(day))
        except ValueError:
            continue
        if abs((candidate.date() - log_datetime.date()).days) <= 60:
            return candidate.strftime("%d/%m/%Y")
    return ""


def _classify_content(content: str) -> str:
    normalized = strip_accents(content).casefold()

    service_words = (
        "truyen", "massa", "massage", "tiem", "dich vu", "het ton",
        "tru the", "tru tk", "tru coc", "y ta", "ktv", "bac si", "bs ",
        "con ", "hs",
    )
    schedule_words = (
        "lich", "chi ban", " qua", "nhac", "cham tiep", "13h", "14h",
        "t4 ", "t5 ", "hen",
    )
    financial_words = (
        "thanh toan", "con no", "no lai", "coc con", "tk con", "tai khoan",
        "hoan lai", "back lai", "mua goi", "dang ky",
    )

    if any(word in normalized for word in financial_words):
        return "Tài chính / công nợ"
    if any(word in normalized for word in service_words):
        return "Sử dụng dịch vụ / tồn"
    if any(word in normalized for word in schedule_words):
        return "Lịch hẹn / chăm sóc"
    if len(content.strip().split()) <= 3:
        return "Mã trạng thái / viết tắt"
    if CUSTOMER_CODE_RE.search(content):
        return "Thông tin khách hàng"
    return "Khác"


def _extract_branch(author: str, content: str, default_branch: str) -> str:
    normalized = strip_accents(f"{author}\n{content}").casefold()
    patterns = (
        ("D1/HCM", ("d1/hcm", "- d1", " d1 ")),
        ("D5/HCM", ("d5/hcm", "- d5", " d5 ")),
        ("D2/HN", ("d2/hn", "- d2", " d2 ")),
    )
    for branch, candidates in patterns:
        if any(candidate in normalized for candidate in candidates):
            return branch
    return (default_branch or "").strip()


def _extract_status_code(content: str) -> str:
    compact = re.sub(r"\s+", " ", content or "").strip()
    if re.fullmatch(r"[A-Za-zÀ-ỹ.]{1,12}(?:\s+khách)?", compact, re.I):
        return compact.split()[0].upper().replace(".", "")
    return ""


def _extract_business_status(content: str) -> str:
    normalized = strip_accents(content).upper()
    statuses: list[str] = []
    status_map = {
        "HET TON": "Hết tồn",
        "CHO XU LY": "Chờ xử lý",
        "THANH TOAN DU": "Thanh toán đủ",
        "NO LAI": "Còn nợ",
        "NO DON": "Nợ đơn",
        "NO LAI THUOC": "Công ty còn nợ thuốc/dịch vụ",
        "NO THUOC": "Công ty còn nợ thuốc/dịch vụ",
        "CHUA SD": "Chưa sử dụng",
        "CHUA SU DUNG": "Chưa sử dụng",
        "HUY": "Hủy",
        "DA HEN": "Đã hẹn",
    }
    for key, label in status_map.items():
        if key in normalized and label not in statuses:
            statuses.append(label)
    return "; ".join(statuses)


def _make_log_key(
    customer_code: str,
    log_datetime: datetime,
    author: str,
    content: str,
) -> str:
    source = "|".join(
        [
            (customer_code or "").strip().upper(),
            log_datetime.isoformat(),
            author.strip().casefold(),
            content.strip().casefold(),
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _make_event_id(prefix: str, log_key: str, line: str, event_type: str, amount: Any) -> str:
    source = f"{prefix}|{log_key}|{line.casefold()}|{event_type}|{amount}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _resolve_pair_names(
    pairs: list[tuple[str, str]],
    global_name_map: Mapping[str, str],
) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for code, name in pairs:
        resolved = name or global_name_map.get(code, "")
        item = (code, resolved)
        if item not in output:
            output.append(item)
    return output


def _line_customer_context(
    line: str,
    global_name_map: Mapping[str, str],
) -> list[tuple[str, str]]:
    pairs = extract_customer_pairs_from_line(line)
    codes = [match.group().upper() for match in CUSTOMER_CODE_RE.finditer(line)]
    paired_codes = {code for code, _ in pairs}
    for code in codes:
        if code not in paired_codes:
            pairs.append((code, global_name_map.get(code, "")))
    return _resolve_pair_names(pairs, global_name_map)


def _financial_account_type(normalized_line: str) -> str:
    if "TRU COC" in normalized_line or "COC CON" in normalized_line:
        return "Cọc"
    if "TK TANG" in normalized_line or "THE TANG" in normalized_line:
        return "Tài khoản tặng"
    if "TAI KHOAN THUOC" in normalized_line:
        return "Tài khoản thuốc"
    if "VOUCHER" in normalized_line or re.search(r"\bVC\b", normalized_line):
        return "Voucher"
    if "TIEN MAT" in normalized_line:
        return "Tiền mặt"
    if re.search(r"\bCK\b", normalized_line) or "CHUYEN KHOAN" in normalized_line:
        return "Chuyển khoản"
    if "TK" in normalized_line or "THE" in normalized_line:
        return "Tài khoản chính"
    return ""


def _event_customer_roles(
    line: str,
    current_service_user: tuple[str, str] | None,
    global_name_map: Mapping[str, str],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    service_user = current_service_user
    pairs = _line_customer_context(line, global_name_map)
    normalized = strip_accents(line).upper()

    if pairs and re.search(r"\bSD(?: THE)?\b", normalized):
        service_user = pairs[0]

    funding_owner: tuple[str, str] | None = None
    if pairs and any(keyword in normalized for keyword in ("TRU COC", "TRU THE", "TRU TK", "TRU SAN", "CAN TRU")):
        funding_owner = pairs[-1]

    return service_user, funding_owner


def _payment_status_from_line(normalized_line: str) -> str:
    if "THANH TOAN DU" in normalized_line or "DA THANH TOAN DU" in normalized_line:
        return "Đã thanh toán đủ"
    if "NO LAI" in normalized_line or "CON NO" in normalized_line:
        return "Thanh toán một phần / còn nợ"
    if "THANH TOAN TRUOC" in normalized_line:
        return "Thanh toán trước"
    if "CHO" in normalized_line and ("BACK" in normalized_line or "XU LY" in normalized_line):
        return "Chờ xử lý"
    return ""


def _extract_financial_events_for_log(
    log: Mapping[str, Any],
    log_key: str,
    primary_code: str,
    primary_name: str,
    global_name_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_service_user: tuple[str, str] | None = None

    def add_event(
        line: str,
        event_type: str,
        perspective: str,
        amount_item: dict[str, Any] | None,
        account_type: str = "",
        payment_status: str = "",
        service_user: tuple[str, str] | None = None,
        funding_owner: tuple[str, str] | None = None,
        expiry: str = "",
        rate: str = "",
        confidence: str = "Cao",
    ) -> None:
        amount_value = amount_item["value"] if amount_item else None
        event_id = _make_event_id("FIN", log_key, line, event_type, amount_value)
        row = {
            "Financial event ID": event_id,
            "Log key": log_key,
            "Mã KH chính": primary_code,
            "Tên khách hàng chính": primary_name,
            "Thời gian ghi nhận": log["log_datetime"],
            "Loại sự kiện tài chính": event_type,
            "Góc nhìn công nợ": perspective,
            "Số tiền (VND)": amount_value,
            "Tài khoản/Nguồn tiền": account_type,
            "Tình trạng thanh toán": payment_status,
            "Mã KH sử dụng dịch vụ": service_user[0] if service_user else "",
            "Tên KH sử dụng dịch vụ": service_user[1] if service_user else "",
            "Mã KH nguồn tiền/thẻ": funding_owner[0] if funding_owner else "",
            "Tên KH nguồn tiền/thẻ": funding_owner[1] if funding_owner else "",
            "Hạn sử dụng": expiry,
            "Tỷ lệ giảm/tặng": rate,
            "Dòng nguồn": line,
            "Độ tin cậy": confidence,
        }
        signature = (
            row["Loại sự kiện tài chính"],
            row["Số tiền (VND)"],
            row["Tài khoản/Nguồn tiền"],
            row["Dòng nguồn"],
        )
        if not any(
            (
                existing["Loại sự kiện tài chính"],
                existing["Số tiền (VND)"],
                existing["Tài khoản/Nguồn tiền"],
                existing["Dòng nguồn"],
            )
            == signature
            for existing in events
        ):
            events.append(row)

    for line in log["content_lines"]:
        normalized = strip_accents(line).upper()
        line_pairs = _line_customer_context(line, global_name_map)
        if line_pairs and re.search(r"\bSD(?: THE)?\b", normalized):
            current_service_user = line_pairs[0]

        service_user, funding_owner = _event_customer_roles(
            line,
            current_service_user,
            global_name_map,
        )
        account_type = _financial_account_type(normalized)
        payment_status = _payment_status_from_line(normalized)
        expiry = _extract_expiry(line)
        rate_match = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", line)
        rate = rate_match.group(0) if rate_match else ""

        # Balance snapshots
        if "COC CON" in normalized:
            keyword = re.search(r"COC\s+CON", normalized)
            add_event(
                line, "Số dư cọc", "Số dư nghĩa vụ với khách hàng",
                _nearest_amount(line, keyword), "Cọc", payment_status,
                service_user, funding_owner, expiry, rate,
            )
            continue

        if (
            re.match(r"^\s*(?:TK|THE\s+TK|THE\s+TAI\s+KHOAN|THE\s+TON|TONG\s+TK\s+TANG)\b", normalized)
            and "CON" in normalized
            and not re.search(r"\b(?:B|L|CC|MG|THANG)\b", normalized)
        ):
            keyword = re.search(r"\bCON\b", normalized)
            if "TANG" in normalized:
                event_type = "Số dư tài khoản tặng"
                perspective = "Số dư quyền lợi khách hàng"
                account = "Tài khoản tặng"
            elif "VOUCHER" in normalized or re.search(r"\bVC\b", normalized):
                event_type = "Số dư voucher"
                perspective = "Số dư quyền lợi khách hàng"
                account = "Voucher"
            else:
                event_type = "Số dư tài khoản chính"
                perspective = "Số dư quyền lợi khách hàng"
                account = "Tài khoản chính"
                account_name_match = re.search(r"TK\s+([A-Z0-9 ._-]+?)\s+CON", normalized)
                if account_name_match:
                    account_name = account_name_match.group(1).strip()
                    if account_name and account_name not in {"CHINH"}:
                        account = f"Tài khoản {account_name.title()}"
            add_event(
                line, event_type, perspective,
                _nearest_amount(line, keyword), account, payment_status,
                service_user, funding_owner, expiry, rate,
            )
            continue

        if "CON TONG VC" in normalized or ("VOUCHER" in normalized and "CON" in normalized):
            keyword = re.search(r"(?:CON\s+TONG\s+VC|VOUCHER.*?CON)", normalized)
            add_event(
                line, "Số dư voucher", "Số dư quyền lợi khách hàng",
                _nearest_amount(line, keyword), "Voucher", payment_status,
                service_user, funding_owner, expiry, rate,
            )

        # Purchase / order value
        purchase_match = re.search(r"\b(MUA|DANG KY|DK|KICH)\b", normalized)
        if purchase_match:
            amounts_after_purchase = [
                item for item in extract_amounts(line)
                if item["start"] >= purchase_match.end()
            ]
            amount_item = None
            if amounts_after_purchase:
                search_tail = normalized[purchase_match.end():]
                target_rel = (
                    re.search(r"\bTONG\b", search_tail)
                    or re.search(r"(?<!<)=(?!>)", search_tail)
                    or re.search(r"\bGIA\b", search_tail)
                    or re.search(r"\bCON\b", search_tail)
                )
                if target_rel:
                    target_position = purchase_match.end() + target_rel.end()
                    after_target = [
                        item for item in amounts_after_purchase
                        if item["start"] >= target_position
                    ]
                    amount_item = after_target[0] if after_target else amounts_after_purchase[0]
                else:
                    amount_item = amounts_after_purchase[0]
            if amount_item:
                add_event(
                    line, "Giá trị mua/gói", "Doanh số/giao dịch mua",
                    amount_item, account_type, payment_status,
                    service_user, funding_owner, expiry, rate,
                )

        # Payment receipts
        if "CHINH SACH THANH TOAN" not in normalized:
            for pattern in (
                r"TONG\s+THANH\s+TOAN",
                r"TT\s+TONG",
                r"THANH\s+TOAN\s+TRUOC",
                r"THANH\s+TOAN\s+THEM",
                r"THANH\s+TOAN",
                r"\bCK\b",
                r"CHUYEN\s+KHOAN",
            ):
                match = re.search(pattern, normalized)
                if match:
                    amount_item = _nearest_amount(line, match)
                    if amount_item:
                        add_event(
                            line, "Khách thanh toán", "Tiền thu từ khách hàng",
                            amount_item, account_type, payment_status,
                            service_user, funding_owner, expiry, rate,
                        )
                    break

        # Customer receivable
        service_owed_match = re.search(r"(NO\s+LAI\s+THUOC|NO\s+THUOC|NO\s+DICH\s+VU)", normalized)
        if service_owed_match:
            add_event(
                line, "Công ty còn nợ dịch vụ/thuốc", "Nghĩa vụ cung cấp hàng hóa/dịch vụ",
                None, account_type, "Chưa hoàn tất nghĩa vụ",
                service_user, funding_owner, expiry, rate,
                confidence="Trung bình",
            )
        else:
            debt_match = re.search(r"(NO\s+LAI|CON\s+NO|KHACH\s+NO)", normalized)
            if debt_match:
                after_amounts = [
                    item for item in extract_amounts(line)
                    if item["start"] >= debt_match.end()
                ]
                debt_amount = after_amounts[0] if after_amounts else None
                add_event(
                    line, "Khách còn nợ công ty", "Phải thu khách hàng",
                    debt_amount, account_type,
                    payment_status or "Thanh toán một phần / còn nợ",
                    service_user, funding_owner, expiry, rate,
                    confidence="Cao" if debt_amount else "Trung bình",
                )
            elif "TRA NO DON" in normalized:
                add_event(
                    line, "Trả nợ đơn/đối trừ", "Công nợ đã được xử lý",
                    None, account_type, "Đã xử lý/đối trừ",
                    service_user, funding_owner, expiry, rate,
                    confidence="Trung bình",
                )
            elif "NO DON" in normalized:
                add_event(
                    line, "Nợ đơn/chờ xử lý", "Công nợ chưa xác định số tiền",
                    None, account_type, "Chờ xử lý",
                    service_user, funding_owner, expiry, rate,
                    confidence="Trung bình",
                )

        # Company refund / credit to customer
        refund_match = re.search(
            r"(HOAN\s+LAI|BACK\s+LAI|HOAN\s+TIEN|TRA\s+LAI\s+KHACH|CONG\s+TY.*?NO\s+KHACH)",
            normalized,
        )
        if refund_match:
            add_event(
                line, "Công ty phải trả/ghi có khách hàng",
                "Phải trả hoặc ghi có cho khách hàng",
                _nearest_amount(line, refund_match), account_type or "Cọc",
                payment_status, service_user, funding_owner, expiry, rate,
                confidence="Cao" if _nearest_amount(line, refund_match) else "Trung bình",
            )

        # Deductions from deposit/card/account. There can be multiple in one line.
        deduction_patterns = [
            (r"TRU\s+(?:THE\s+)?TAI\s+KHOAN\s+THUOC", "Tài khoản thuốc"),
            (r"TRU\s+(?:THE\s+)?TK\s+TANG", "Tài khoản tặng"),
            (r"TRU\s+(?:THE\s+)?TK\s+CHINH", "Tài khoản chính"),
            (r"TRU\s+COC", "Cọc"),
            (r"TRU\s+THE", "Thẻ/Tài khoản"),
            (r"TRU\s+TK", "Tài khoản"),
            (r"CAN\s+TRU", "Cấn trừ"),
        ]
        used_deduction_spans: list[tuple[int, int]] = []
        for pattern, source in deduction_patterns:
            for match in re.finditer(pattern, normalized):
                if any(not (match.end() <= start or match.start() >= end) for start, end in used_deduction_spans):
                    continue
                amount_item = _nearest_amount(line, match, prefer_after=True)
                if amount_item:
                    add_event(
                        line, "Trừ cọc/thẻ/tài khoản",
                        "Giảm số dư quyền lợi khách hàng",
                        amount_item, source, payment_status,
                        service_user, funding_owner, expiry, rate,
                    )
                    used_deduction_spans.append((match.start(), match.end()))

        # Top-up / additional amount
        topup_match = re.search(r"\bBU\b", normalized)
        if topup_match:
            amount_item = _nearest_amount(line, topup_match)
            if amount_item:
                add_event(
                    line, "Tiền bù thêm", "Khoản bổ sung/đối trừ",
                    amount_item, account_type, payment_status,
                    service_user, funding_owner, expiry, rate,
                )

        # Bonus / promotion amount
        gift_match = re.search(r"\bTẶNG\b", line, re.IGNORECASE)
        if not gift_match:
            unaccented_gift = re.search(r"\bTANG\b", normalized)
            if unaccented_gift and "TANG CUONG" not in normalized:
                gift_match = unaccented_gift
        excluded_bonus_phrases = (
            "TK TANG CON", "THE TANG CON", "TRU TK TANG", "TRU THE TANG",
        )
        gift_tail = normalized[gift_match.end():] if gift_match else ""
        monetary_gift_context = any(
            phrase in normalized
            for phrase in ("TANG TK", "TANG TAI KHOAN", "TANG VOUCHER", "TANG VC", "TUONG DUONG")
        ) or bool(re.search(r"TANG\s+\d+(?:[.,]\d+)?\s*%", normalized))
        if gift_match and re.search(r"\b(?:TK|THE\s+TK|TAI\s+KHOAN|VOUCHER|VC)\b", gift_tail):
            monetary_gift_context = True
        if gift_match and monetary_gift_context and not any(
            phrase in normalized for phrase in excluded_bonus_phrases
        ):
            after_amounts = [
                item for item in extract_amounts(line)
                if item["start"] >= gift_match.end()
            ]
            amount_item = None
            total_match = re.search(r"(?:\bTONG\b|=)", gift_tail)
            if total_match and after_amounts:
                target_position = gift_match.end() + total_match.end()
                after_total = [item for item in after_amounts if item["start"] >= target_position]
                amount_item = after_total[0] if after_total else after_amounts[-1]
            elif after_amounts:
                amount_item = after_amounts[0]
            if amount_item:
                add_event(
                    line, "Tiền tặng/khuyến mãi", "Quyền lợi công ty cấp cho khách",
                    amount_item, "Tài khoản tặng/Voucher", payment_status,
                    service_user, funding_owner, expiry, rate,
                )

        # Explicit discount amount, only when text says "tương đương".
        discount_match = re.search(r"GIAM.*?TUONG\s+DUONG", normalized)
        if discount_match:
            amount_item = _nearest_amount(line, discount_match)
            if amount_item:
                add_event(
                    line, "Giá trị giảm giá", "Giảm giá cho khách hàng",
                    amount_item, account_type, payment_status,
                    service_user, funding_owner, expiry, rate,
                )

        # Generic service amount / fee when no more specific purchase is present.
        if (
            not purchase_match
            and "CHINH SACH THANH TOAN" not in normalized
            and any(keyword in normalized for keyword in ("TOA THUOC", "PHI TAI NHA", "GIA ", "TONG "))
        ):
            amounts = extract_amounts(line)
            if amounts and not any(
                event["Dòng nguồn"] == line
                and event["Loại sự kiện tài chính"]
                in {"Giá trị mua/gói", "Khách thanh toán", "Trừ cọc/thẻ/tài khoản"}
                for event in events
            ):
                add_event(
                    line, "Chi phí dịch vụ/đơn hàng", "Giá trị sử dụng dịch vụ",
                    amounts[0], account_type, payment_status,
                    service_user, funding_owner, expiry, rate,
                    confidence="Trung bình",
                )

    return events


def _inventory_status(normalized: str) -> str:
    if "CHUA SD" in normalized or "CHUA SU DUNG" in normalized:
        return "Chưa sử dụng"
    if "HET TON" in normalized:
        return "Hết tồn"
    if "CON SAN" in normalized:
        return "Còn sẵn"
    if "CON" in normalized:
        return "Còn"
    if re.search(r"\bCO\b", normalized):
        return "Có"
    return ""


def _extract_quantity_details(line: str) -> tuple[Any, str, str]:
    pattern = re.compile(
        r"(?<![\w])(\d+(?:[.,]\d+)?)\s*(THÁNG|THANG|BUỔI|BUOI|THẺ|THE|MŨI|MUI|B|L|CC|MG)(?![\w])",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(line))
    detail = "; ".join(match.group(0) for match in matches)
    if not matches:
        return None, "", ""

    first = matches[0]
    raw_number_text = first.group(1)
    try:
        if re.fullmatch(r"\d{1,3}[.,]\d{3}", raw_number_text):
            quantity = int(re.sub(r"\D", "", raw_number_text))
        else:
            quantity = float(raw_number_text.replace(",", "."))
            if quantity.is_integer():
                quantity = int(quantity)
    except ValueError:
        quantity = None

    unit_map = {
        "THANG": "Tháng",
        "BUOI": "Buổi",
        "THE": "Thẻ",
        "MUI": "Mũi",
        "B": "Buổi",
        "L": "Lần",
        "CC": "cc",
        "MG": "mg",
    }
    unit = unit_map.get(strip_accents(first.group(2)).upper(), first.group(2))
    return quantity, unit, detail


def _clean_inventory_service(line: str) -> str:
    text = PROFILE_CODE_RE.sub("", line)
    text = re.sub(r"\([^)]*(?:kích ngày|KÍCH NGÀY|hết hạn|HẾT HẠN)[^)]*\)", "", text)
    text = re.sub(r"^[*+\-\s]+", "", text)
    text = re.sub(r"\b(?:CÒN SẴN|CÒN|CÓ)\b\s*:?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"=>\s*(?:CHƯA SD|CHƯA SỬ DỤNG)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -:")
    return text


def _extract_inventory_for_log(
    log: Mapping[str, Any],
    log_key: str,
    primary_code: str,
    primary_name: str,
    global_name_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_service_user: tuple[str, str] | None = None

    for line in log["content_lines"]:
        normalized = strip_accents(line).upper()
        pairs = _line_customer_context(line, global_name_map)
        if pairs and re.search(r"\bSD(?: THE)?\b", normalized):
            current_service_user = pairs[0]

        # Skip financial balances.
        if (
            "COC CON" in normalized
            or (("TK" in normalized or "THE TK" in normalized) and "CON" in normalized)
            or "CON TONG VC" in normalized
        ):
            continue

        profile_codes = [match.group().upper() for match in PROFILE_CODE_RE.finditer(line)]
        has_inventory_language = (
            any(keyword in normalized for keyword in ("CON ", "CON:", "CON SAN", "CHUA SD", "CHUA SU DUNG", "CO "))
            and (
                bool(profile_codes)
                or bool(re.search(r"\d+\s*(?:B|L|CC|MG|THANG|THE|MUI)\b", normalized))
            )
        )
        if not has_inventory_language:
            continue

        quantity, unit, quantity_detail = _extract_quantity_details(line)
        status = _inventory_status(normalized)
        service = _clean_inventory_service(line)
        owner = (primary_code, primary_name)
        event_id = _make_event_id("INV", log_key, line, status, quantity_detail)

        rows.append(
            {
                "Inventory event ID": event_id,
                "Log key": log_key,
                "Mã KH chính": primary_code,
                "Tên khách hàng chính": primary_name,
                "Thời gian ghi nhận": log["log_datetime"],
                "Mã KH sở hữu": owner[0],
                "Tên KH sở hữu": owner[1],
                "Mã KH sử dụng dịch vụ": current_service_user[0] if current_service_user else "",
                "Tên KH sử dụng dịch vụ": current_service_user[1] if current_service_user else "",
                "Trạng thái tồn": status,
                "Dịch vụ/Gói còn lại": service,
                "Số lượng chính": quantity,
                "Đơn vị chính": unit,
                "Chi tiết số lượng": quantity_detail,
                "Là quà tặng": "Có" if (
                    re.search(r"\bTẶNG\b", line, re.IGNORECASE)
                    or ("TANG" in normalized and "TANG CUONG" not in normalized)
                ) else "Không",
                "Ngày kích hoạt": _extract_activation(line),
                "Hạn sử dụng": _extract_expiry(line),
                "Mã hồ sơ HS": "; ".join(dict.fromkeys(profile_codes)),
                "Dòng nguồn": line,
                "Độ tin cậy": "Cao" if profile_codes or quantity_detail else "Trung bình",
            }
        )

    return rows


def _extract_customer_mentions_for_log(
    log: Mapping[str, Any],
    log_key: str,
    primary_code: str,
    primary_name: str,
    global_name_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for line in log["content_lines"]:
        normalized = strip_accents(line).upper()
        pairs = _line_customer_context(line, global_name_map)
        for code, name in pairs:
            roles: list[str] = []
            if "CHU THE" in normalized:
                roles.append("Chủ thẻ")
            if re.search(r"\bSD(?: THE)?\b", normalized):
                roles.append("Người sử dụng dịch vụ")
            if any(keyword in normalized for keyword in ("TRU COC", "TRU THE", "TRU TK", "TRU SAN", "CAN TRU")):
                if code == pairs[-1][0]:
                    roles.append("Chủ nguồn tiền/thẻ")
            if code == primary_code:
                roles.append("Khách hàng chính")
            if not roles:
                roles.append("Khách hàng được nhắc")

            for role in dict.fromkeys(roles):
                mention_id = _make_event_id("CUS", log_key, line, role, code)
                row = {
                    "Mention ID": mention_id,
                    "Log key": log_key,
                    "Mã KH chính": primary_code,
                    "Tên khách hàng chính": primary_name,
                    "Thời gian ghi nhận": log["log_datetime"],
                    "Mã KH được nhắc": code,
                    "Tên KH được nhắc": name,
                    "Vai trò trong log": role,
                    "Dòng nguồn": line,
                    "Độ tin cậy": "Cao" if name else "Trung bình",
                }
                signature = (code, role, line)
                if not any(
                    (existing["Mã KH được nhắc"], existing["Vai trò trong log"], existing["Dòng nguồn"])
                    == signature
                    for existing in rows
                ):
                    rows.append(row)

    return rows


def _aggregate_financial_to_log(events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "Giá trị mua (VND)": None,
            "Đã thanh toán (VND)": None,
            "KH còn nợ công ty (VND)": None,
            "Công ty phải trả/ghi có KH (VND)": None,
            "Số tiền trừ cọc/thẻ (VND)": None,
            "Số dư cọc (VND)": None,
            "Số dư TK chính (VND)": None,
            "Số dư TK tặng (VND)": None,
            "Số dư voucher (VND)": None,
            "Tiền tặng/khuyến mãi (VND)": None,
            "Tiền bù thêm (VND)": None,
            "Tình trạng thanh toán": "",
        }

    def event_sum(event_type: str) -> Any:
        selected = events.loc[
            events["Loại sự kiện tài chính"] == event_type,
            ["Số tiền (VND)", "Dòng nguồn"],
        ].copy()
        selected["Số tiền (VND)"] = pd.to_numeric(
            selected["Số tiền (VND)"], errors="coerce"
        )
        selected = selected.dropna(subset=["Số tiền (VND)"])
        if selected.empty:
            return None

        if event_type == "Khách thanh toán":
            total_mask = selected["Dòng nguồn"].astype(str).map(
                lambda value: "TONG" in strip_accents(value).upper()
            )
            totals = selected.loc[total_mask, "Số tiền (VND)"]
            if not totals.empty:
                return int(totals.max())

        deduplicated = selected.drop_duplicates(
            subset=["Số tiền (VND)", "Dòng nguồn"]
        )
        return int(deduplicated["Số tiền (VND)"].sum())

    def balance_value(event_type: str) -> Any:
        subset = events.loc[events["Loại sự kiện tài chính"] == event_type, "Số tiền (VND)"]
        subset = pd.to_numeric(subset, errors="coerce").dropna()
        if subset.empty:
            return None
        # Multiple gift/voucher accounts can coexist in one log, so sum them.
        if event_type in {"Số dư tài khoản tặng", "Số dư voucher"}:
            return int(subset.sum())
        return int(subset.iloc[-1])

    statuses = [
        value
        for value in events["Tình trạng thanh toán"].astype(str).tolist()
        if value and value != "nan"
    ]

    return {
        "Giá trị mua (VND)": event_sum("Giá trị mua/gói"),
        "Đã thanh toán (VND)": event_sum("Khách thanh toán"),
        "KH còn nợ công ty (VND)": event_sum("Khách còn nợ công ty"),
        "Công ty phải trả/ghi có KH (VND)": event_sum("Công ty phải trả/ghi có khách hàng"),
        "Số tiền trừ cọc/thẻ (VND)": event_sum("Trừ cọc/thẻ/tài khoản"),
        "Số dư cọc (VND)": balance_value("Số dư cọc"),
        "Số dư TK chính (VND)": balance_value("Số dư tài khoản chính"),
        "Số dư TK tặng (VND)": balance_value("Số dư tài khoản tặng"),
        "Số dư voucher (VND)": balance_value("Số dư voucher"),
        "Tiền tặng/khuyến mãi (VND)": event_sum("Tiền tặng/khuyến mãi"),
        "Tiền bù thêm (VND)": event_sum("Tiền bù thêm"),
        "Tình trạng thanh toán": "; ".join(dict.fromkeys(statuses)),
    }


def parse_crm_bundle(
    raw_text: str,
    customer_code: str = "",
    customer_name: str = "",
    default_branch: str = "",
    status_mapping: Mapping[str, str] | None = None,
    reference_date: date | None = None,
) -> dict[str, pd.DataFrame | str]:
    logs = split_crm_logs(raw_text, reference_date=reference_date)
    candidates = detect_customer_candidates(raw_text)

    input_code = re.sub(r"\s+", "", (customer_code or "").upper())
    input_name = (customer_name or "").strip().upper()
    global_name_map = {
        str(row["Mã KH"]): str(row["Tên khách hàng"])
        for _, row in candidates.iterrows()
    }

    if input_code:
        primary_code = input_code
        primary_name = input_name or global_name_map.get(primary_code, "")
        primary_source = "Người dùng nhập"
    elif not candidates.empty:
        primary_code = str(candidates.iloc[0]["Mã KH"])
        primary_name = input_name or str(candidates.iloc[0]["Tên khách hàng"])
        primary_source = "Tự nhận diện từ log"
    else:
        primary_code = ""
        primary_name = input_name
        primary_source = "Chưa xác định"

    mapping = {
        str(key).upper().strip(): str(value).strip()
        for key, value in (status_mapping or DEFAULT_STATUS_MAPPING).items()
        if str(key).strip()
    }

    log_rows: list[dict[str, Any]] = []
    all_financial: list[dict[str, Any]] = []
    all_inventory: list[dict[str, Any]] = []
    all_mentions: list[dict[str, Any]] = []

    for index, log in enumerate(logs, start=1):
        content = "\n".join(log["content_lines"]).strip()
        log_key = _make_log_key(primary_code, log["log_datetime"], log["author"], content)
        role, staff = _split_author(log["author"])
        status_code = _extract_status_code(content)
        standardized_status = mapping.get(
            status_code,
            "Cần xác nhận" if status_code else "",
        )

        mentions = _extract_customer_mentions_for_log(
            log, log_key, primary_code, primary_name, global_name_map
        )
        financial = _extract_financial_events_for_log(
            log, log_key, primary_code, primary_name, global_name_map
        )
        inventory = _extract_inventory_for_log(
            log, log_key, primary_code, primary_name, global_name_map
        )

        mention_df = pd.DataFrame(mentions, columns=CUSTOMER_MENTION_COLUMNS)
        financial_df = pd.DataFrame(financial, columns=FINANCIAL_COLUMNS)

        mentioned_codes = (
            "; ".join(dict.fromkeys(mention_df["Mã KH được nhắc"].astype(str)))
            if not mention_df.empty else ""
        )
        mentioned_names = (
            "; ".join(
                name for name in dict.fromkeys(mention_df["Tên KH được nhắc"].astype(str))
                if name and name != "nan"
            )
            if not mention_df.empty else ""
        )
        service_mentions = (
            mention_df[mention_df["Vai trò trong log"] == "Người sử dụng dịch vụ"]
            if not mention_df.empty else pd.DataFrame()
        )
        funding_mentions = (
            mention_df[mention_df["Vai trò trong log"] == "Chủ nguồn tiền/thẻ"]
            if not mention_df.empty else pd.DataFrame()
        )

        service_codes = (
            "; ".join(dict.fromkeys(service_mentions["Mã KH được nhắc"].astype(str)))
            if not service_mentions.empty else ""
        )
        service_names = (
            "; ".join(
                name for name in dict.fromkeys(service_mentions["Tên KH được nhắc"].astype(str))
                if name and name != "nan"
            )
            if not service_mentions.empty else ""
        )
        funding_codes = (
            "; ".join(dict.fromkeys(funding_mentions["Mã KH được nhắc"].astype(str)))
            if not funding_mentions.empty else ""
        )
        funding_names = (
            "; ".join(
                name for name in dict.fromkeys(funding_mentions["Tên KH được nhắc"].astype(str))
                if name and name != "nan"
            )
            if not funding_mentions.empty else ""
        )

        profile_codes = "; ".join(
            dict.fromkeys(match.group().upper() for match in PROFILE_CODE_RE.finditer(content))
        )
        finance_summary = _aggregate_financial_to_log(financial_df)

        confidence = "Cao"
        if log["time_source"] != "Tuyệt đối" or not primary_code:
            confidence = "Trung bình"
        if not content:
            confidence = "Thấp"

        row = {
            "STT": index,
            "Mã KH chính": primary_code,
            "Tên khách hàng chính": primary_name,
            "Nguồn KH chính": primary_source,
            "Thời gian ghi nhận": log["log_datetime"],
            "Nguồn thời gian": log["time_source"],
            "Ngày": log["log_datetime"].date(),
            "Giờ": log["log_datetime"].strftime("%H:%M"),
            "Người ghi (nguyên bản)": log["author"],
            "Vai trò/Bộ phận": role,
            "Người phụ trách": staff,
            "Đã chỉnh sửa": "Có" if log["edited"] else "Không",
            "Mã trạng thái": status_code,
            "Trạng thái chuẩn": standardized_status,
            "Nhóm nội dung": _classify_content(content),
            "Cơ sở": _extract_branch(log["author"], content, default_branch),
            "Ngày dịch vụ": _extract_visit_date(content, log["log_datetime"]),
            "Mã KH được nhắc": mentioned_codes,
            "Tên KH được nhắc": mentioned_names,
            "Mã KH sử dụng dịch vụ": service_codes,
            "Tên KH sử dụng dịch vụ": service_names,
            "Mã KH nguồn tiền/thẻ": funding_codes,
            "Tên KH nguồn tiền/thẻ": funding_names,
            "Mã hồ sơ HS": profile_codes,
            **finance_summary,
            "Trạng thái nghiệp vụ": _extract_business_status(content),
            "Nội dung gốc": content,
            "Độ tin cậy": confidence,
            "Log key": log_key,
        }
        log_rows.append(row)
        all_financial.extend(financial)
        all_inventory.extend(inventory)
        all_mentions.extend(mentions)

    logs_df = pd.DataFrame(log_rows, columns=LOG_COLUMNS)
    financial_df = pd.DataFrame(all_financial, columns=FINANCIAL_COLUMNS)
    inventory_df = pd.DataFrame(all_inventory, columns=INVENTORY_COLUMNS)
    mentions_df = pd.DataFrame(all_mentions, columns=CUSTOMER_MENTION_COLUMNS)

    return {
        "logs": logs_df,
        "financial_events": financial_df,
        "service_inventory": inventory_df,
        "customer_mentions": mentions_df,
        "customer_candidates": candidates,
        "primary_customer_code": primary_code,
        "primary_customer_name": primary_name,
        "primary_customer_source": primary_source,
    }


def parse_crm_logs(
    raw_text: str,
    customer_code: str = "",
    customer_name: str = "",
    default_branch: str = "",
    status_mapping: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Backward-compatible wrapper returning only the one-row-per-log table."""
    bundle = parse_crm_bundle(
        raw_text=raw_text,
        customer_code=customer_code,
        customer_name=customer_name,
        default_branch=default_branch,
        status_mapping=status_mapping,
    )
    return bundle["logs"]  # type: ignore[return-value]
