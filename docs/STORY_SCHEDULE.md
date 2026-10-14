# Studio: phong cách, tham chiếu và lịch tạo

1. Mở **Studio → Video người que → Mẫu từ HTML**. Có 12 mẫu từ tài liệu SRT. Các mẫu cũ và mẫu riêng vẫn dùng được; cấu hình đang chọn không tự đổi.
2. Nhập SRT/nội dung, bấm **AI phân tích và lên kịch bản**, duyệt hồ sơ nhân vật và các cảnh. Chọn giọng, nhạc và cách tạo chuyển động.
3. Ở nhân vật A/B, chọn ảnh đã có, tải file hoặc dán **URL ảnh công khai** rồi bấm **Tải từ URL**. Chỉ nhận PNG/JPEG/WebP, tối đa 10 MB / 25 triệu điểm ảnh. Link cần trả về ảnh trực tiếp; trang đăng nhập và trang chia sẻ không phải link ảnh. Ảnh được tải và lưu qua thư viện tham chiếu hiện có.
4. **Lưu kịch bản hiện tại** giữ nguồn, các cảnh và toàn bộ cấu hình. **Lưu và hẹn tạo video** lưu cùng ngày giờ trên máy. Để hẹn bản nháp đã lưu, chọn ngày giờ rồi bấm **Hẹn theo giờ đã chọn** ở bản đó.
5. **Hủy lịch · giữ bản nháp** dừng lịch chưa chạy. **Nạp kịch bản** mở bản đã lưu; sửa rồi lưu tạo một bản mới. Với video đang chạy/đã tạo, **Mở kịch bản / video** cho phép duyệt ảnh mẫu, theo dõi, dừng và tiếp tục bằng luồng Studio hiện có.

## Điều kiện hoạt động

- Backend FrameWeaver phải chạy; máy không tắt/ngủ. Với ảnh/hoạt hình AI, mở Google Flow và kết nối extension. Lịch không chạy trên Firebase.
- Kiểm tra lịch mỗi 30 giây. Nếu lỡ giờ, chạy khi mở lại và kết nối đủ. Xử lý tuần tự, chờ tác vụ media đang chạy kết thúc. Ảnh mẫu cần duyệt sẽ giữ hàng chờ đến khi được duyệt hoặc dừng.
- Dùng tín dụng Flow như tạo ngay. Lịch lỗi không tự tạo lại; mở video để tiếp tục phần thiếu hoặc nạp kịch bản để đặt lịch mới.
- Khởi động lại không tự gửi lại một lịch đã bắt đầu. Giữ video đã tạo và báo tác vụ bị ngắt để người dùng kiểm tra.
- Nếu video được đưa vào Thùng rác hoặc đã bị xóa, lịch ngừng chờ duyệt và giữ kịch bản để nạp/hẹn lại. Khôi phục video không tự chạy lại lịch cũ. Video chưa khởi tạo xong được đánh dấu lỗi và giữ trong dự án; bản nháp được mở khóa để hẹn lại hoặc xóa.
- Nhạc trong kịch bản đã lưu/chưa hoàn tất hiện ở **Nơi đang sử dụng** và được bảo vệ khỏi chuyển vào Thùng rác/xóa vĩnh viễn. Gỡ nhạc khỏi bản dựng hoặc xóa bản nháp/lịch đã lưu không dùng trước khi xóa nhạc. Khi lưu hoặc hẹn lại, hệ thống kiểm tra nhạc còn tồn tại và chưa ở Thùng rác.
- Bản nháp/lịch nằm trong `output/_shared/story_schedule/<project UUID>/<schedule UUID>.json`. Chỉ lưu dữ liệu và tham chiếu file, không sao chép lại video/nhạc. Tối đa 100 bản nháp/lịch chờ mỗi dự án; lịch sử hoàn tất không tính vào giới hạn này.

## Kiểm tra

`venv/bin/python -m pytest tests/unit/test_story_schedule.py tests/unit/test_media_library.py tests/unit/test_reference_images.py tests/unit/test_character_photo_upload.py`

Kiểm thử dùng thư mục tạm và bộ tạo giả lập: không tiêu tín dụng Flow.
