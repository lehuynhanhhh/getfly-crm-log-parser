
# Data Dictionary – Getfly CRM Log Parser v1.2

## CRM_Log

- **Mã KH chính / Tên khách hàng chính**: khách hàng sở hữu CRM đang được paste.
  Hai trường này được điền vào toàn bộ dòng.
- **Mã KH được nhắc / Tên KH được nhắc**: tất cả khách hàng xuất hiện trong log.
- **Mã KH sử dụng dịch vụ**: người thực tế sử dụng dịch vụ trong log.
- **Mã KH nguồn tiền/thẻ**: chủ cọc, thẻ hoặc tài khoản bị trừ.
- **Mã hồ sơ HS**: toàn bộ mã bắt đầu bằng `HS`.
- **Giá trị mua (VND)**: giá trị gói/dịch vụ mua hoặc kích hoạt.
- **Đã thanh toán (VND)**: số tiền khách đã thanh toán.
- **KH còn nợ công ty (VND)**: khoản phải thu được ghi rõ trong log.
- **Công ty phải trả/ghi có KH (VND)**: số tiền hoàn, back hoặc ghi có lại.
- **Số tiền trừ cọc/thẻ (VND)**: khoản sử dụng từ cọc/thẻ/tài khoản.
- **Số dư cọc / TK chính / TK tặng / voucher**: số dư tại thời điểm log.
- **Tiền tặng/khuyến mãi**: số tiền tặng vào tài khoản/voucher.
- **Tiền bù thêm**: khoản khách bù thêm hoặc đối trừ bổ sung.

## Financial_Events

Một log có thể tạo nhiều dòng. Ví dụ một câu có thể đồng thời có:
- Giá trị mua 390 triệu
- Đã thanh toán 280 triệu
- Còn nợ 110 triệu
- Được tặng tài khoản 15 triệu

## Service_Inventory

- Dịch vụ/gói còn lại
- Số lượng chính và đơn vị
- Chi tiết số lượng khi một dòng có nhiều loại
- Trạng thái còn/chưa sử dụng/hết tồn
- Ngày kích hoạt
- Hạn sử dụng
- Mã hồ sơ HS
- Có phải quà tặng hay không

## Related_Customers

Vai trò:
- Khách hàng chính
- Người sử dụng dịch vụ
- Chủ nguồn tiền/thẻ
- Chủ thẻ
- Khách hàng được nhắc

## Cảnh báo

Các trường được nhận diện từ nội dung tự do. Không coi số liệu tự động là xác nhận
kế toán cuối cùng nếu chưa đối chiếu dòng nguồn.
