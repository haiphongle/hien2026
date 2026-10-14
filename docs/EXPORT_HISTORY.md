# Quản lý bản xuất

Trong Hậu kỳ → Lịch sử bản xuất, chọn **Xóa bản này** hoặc đánh dấu nhiều bản rồi **Xóa đã chọn**. Hộp xác nhận hiện tên, dung lượng MP4 thực tế và timeline đang dùng các bản này.

- Xóa vĩnh viễn MP4, mục lịch sử, asset đăng ký và cache sóng/ảnh/proxy của bản xuất đó. Giữ video nguồn, lời đọc, nhạc, phụ đề và quyết định dựng.
- Chặn cả nhóm nếu có một bản đang dùng trong timeline đã lưu, kể cả bản dựng trong thùng rác. Gỡ đoạn đó và lưu trước khi thử lại. Lịch sử hoàn tác trên trình duyệt không giữ bản xuất khỏi bị xóa.
- Chặn khi dự án đang xử lý tư liệu hoặc có tác vụ ghép/xuất. Kiểm tra lại tại lúc xác nhận để bảo vệ snapshot của tác vụ đang chạy.
- Bản phẳng đã tạo cho AI có file nguồn riêng, không phụ thuộc MP4 gốc trong lịch sử.
- Mỗi yêu cầu tối đa 100 bản. Xác nhận không tự xóa; chỉ nút **Xóa vĩnh viễn** mới gửi yêu cầu xóa.
- Nếu lỗi I/O hoặc máy chủ dừng giữa chừng, `deleting-exports.json` ghi các UUID đã xác nhận. Lần nạp lịch sử tiếp theo hoàn tất xóa trước khi trả danh sách; không tăng revision thiết lập hay sửa timeline.

API: `POST /api/videos/{vid}/editing/exports/check-delete` và `DELETE /api/videos/{vid}/editing/exports`, cùng body `{"export_ids": ["UUID"]}`. API DELETE luôn kiểm tra lại nơi sử dụng, không tin kết quả kiểm tra trước đó.

Kiểm thử: `venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_editing_exports.py`.
