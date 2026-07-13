
# Getfly CRM Log Parser v1.2

Ứng dụng nội bộ giúp copy/paste lịch sử Getfly CRM và tự động tạo dữ liệu có cấu trúc.

## Điểm mới v1.2

- Tự nhận diện **Mã KH chính** và **Tên khách hàng chính** từ toàn bộ log.
- Điền Mã KH chính và Tên KH chính vào tất cả dòng, không chỉ dòng có mã.
- Cho phép đổi khách hàng chính khi log có nhiều thành viên gia đình dùng chung thẻ/cọc.
- Tạo bảng **Financial Events**:
  - Giá trị mua/gói
  - Khách thanh toán
  - Khách còn nợ công ty
  - Công ty phải trả/ghi có khách hàng
  - Công ty còn nợ thuốc/dịch vụ
  - Trừ cọc, thẻ và tài khoản
  - Số dư cọc, tài khoản chính, tài khoản tặng và voucher
  - Tiền tặng, giảm giá và tiền bù
- Tạo bảng **Service Inventory / HS**:
  - Mã hồ sơ bắt đầu bằng HS
  - Dịch vụ/gói còn lại
  - Số lượng và đơn vị
  - Trạng thái còn/chưa sử dụng/hết tồn
  - Ngày kích hoạt, hạn sử dụng và quà tặng
- Tạo bảng **Related Customers**:
  - Người sử dụng dịch vụ
  - Chủ nguồn tiền/thẻ
  - Chủ thẻ
  - Khách hàng được nhắc
- Xuất Excel gồm:
  - Tổng quan
  - CRM_Log
  - Financial_Events
  - Service_Inventory
  - Related_Customers
  - Customer_Candidates
  - Raw_Input

## Cài đặt

Máy cần Python 3.10 đến 3.14.

Lần đầu chạy:

```text
INSTALL_AND_RUN.bat
```

Các lần sau:

```text
START_APP.bat
```

Ứng dụng mở tại:

```text
http://localhost:8501
```

## Lưu dữ liệu

Database SQLite nằm tại:

```text
data/crm_logs.db
```

Nên sao lưu file này định kỳ.

## Lưu ý sử dụng số tiền

CRM là nội dung tự do nên một số câu có thể có nhiều số tiền và nhiều ý nghĩa.
Ứng dụng giữ lại **Dòng nguồn** và **Nội dung gốc** để đối chiếu. Các dòng có độ
tin cậy Trung bình hoặc Thấp cần được kiểm tra trước khi sử dụng cho báo cáo tài
chính chính thức.
