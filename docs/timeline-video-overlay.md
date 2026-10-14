# Chèn video người kể chuyện trên timeline

Trong **Hậu kỳ → Bàn dựng → Thư viện tư liệu**:

1. Mở **Tải video / nhạc / hiệu ứng → Tải video chèn**. Chọn MP4, MOV, MKV, WebM hoặc M4V, tối đa 5 GB / 60 phút. Video được chuẩn hóa ở nền, có tiến trình và hủy. Đây là video nguồn mới trong cùng dự án; bản dựng đang mở vẫn giữ nguyên.
2. Đặt vạch phát vào thời điểm muốn bắt đầu, tìm video trong thư viện rồi bấm **Chèn khung tại vạch phát**. Có thể dùng ngay video đã có trong dự án. Nút **+** vẫn nối video vào lớp hình chính ở cuối.
3. Kéo khung trong xem trước để di chuyển, kéo bốn góc để đổi cỡ. Trong **Đoạn đã chọn**, chọn góc có sẵn hoặc **Hai video cạnh nhau**, lấp đầy/giữ trọn hình, lật ngang và viền.
4. Video chèn có lớp hình và lớp tiếng liên kết. Kéo/cắt/tách/đổi tốc độ một đoạn sẽ cập nhật cả hai. Kéo video khác từ thư viện vào lớp **Video chèn** để đặt thêm đoạn.
5. Chọn hình chính hoặc hình chèn để chỉnh âm lượng 0–200%, **Tắt tiếng đoạn này**, hoặc nghe riêng lớp tiếng. **Ẩn hình, giữ tiếng** chỉ ẩn hình chèn. Nút hình con mắt trên track ẩn cả lớp hình; nút loa tắt cả lớp tiếng. Các nút nghe riêng (S) có thể ảnh hưởng những lớp tiếng khác.
6. Tự lưu và hoàn tác/làm lại dùng cùng bản dựng hiện tại. Xuất thử, xuất toàn bộ hoặc vùng chọn dùng cùng thiết lập hình/tiếng; file nguồn không đổi.

## Phạm vi bản đầu

- Một lớp video chèn, tối đa 32 đoạn nối tiếp. Chưa đặt nhiều khung chồng nhau cùng lúc.
- Khung chữ nhật; kéo góc giữ tỷ lệ khung. Chưa có khung tròn, bo góc, animation hoặc bám khuôn mặt.
- Đổi tốc độ video chèn không đẩy các đoạn của phim chính. Nếu không đủ chỗ trống sẽ báo để người dùng chỉnh thời gian.
- Ảnh/hiệu ứng vùng và chữ nằm trên video chèn. Hiệu chỉnh màu chung áp dụng cho hình chính.
- Preview dùng đồng hồ phát chung: chờ hình/tiếng tải đủ rồi phát, tối đa một decoder video chèn hiện tại và một đoạn sắp tới. Dùng **Tạo bản xem nhẹ** nếu file nguồn nặng.
- Học cách phân lớp, thời gian và âm lượng từ tài liệu MoviePy; bộ xuất vẫn dùng FFmpeg hiện có, không cài MoviePy thêm.

## Kiểm tra

Các bài kiểm tra tập trung tại `dashboard/tests/timelineVideoOverlay.test.ts`, `timelinePlayback.test.ts`, `tests/unit/test_timeline_overlay.py`: AV sau kéo/cắt/tốc độ, giới hạn khung, khóa lớp, history sau mở lại, buffering, mute, và xuất FFmpeg thực tế (thời gian, vị trí, viền, lật, chia đôi, âm lượng, ẩn hình và vùng chọn).
