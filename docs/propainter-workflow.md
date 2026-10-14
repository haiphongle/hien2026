# Xóa logo/vật thể bằng AI (ProPainter)

## Dùng ngay trên timeline

Trong Hậu kỳ → Công cụ AI → Xóa vật thể bằng AI:

1. Chọn cảnh để khoanh logo. Mặc định dùng cảnh đang chọn hoặc ở con trỏ phát.
2. Chọn phạm vi: cảnh hiện tại, nhiều cảnh đã chọn hoặc tất cả cảnh. Vùng khoanh dùng cùng tọa độ tương đối trên mỗi nguồn; cần kiểm tra logo ở cùng vị trí.
3. Kiểm tra đầu/giữa/cuối rồi kéo chuột khoanh sát logo. Vùng cố định, tối đa 35% khung.
4. Bấm Xóa logo. Mỗi cảnh nhận từ 1–30 giây nguồn, phù hợp các clip 8 giây. Nhiều cảnh chạy lần lượt; có tiến trình và Dừng.
5. So sánh Trước/Sau. Bấm **Thay cảnh này trên timeline** hoặc **Áp dụng các cảnh đã xử lý**.

Áp dụng thay tư liệu của đoạn hình và tiếng gốc liên kết tại vị trí hiện tại. Giữ ID đoạn, thời điểm, thời lượng, tốc độ, âm lượng, chuyển cảnh và các lớp giọng/nhạc/phụ đề. Không thêm đoạn vào cuối. Có Hoàn tác trên timeline và Khôi phục cảnh gốc trong danh sách kết quả.

Nếu đoạn đã được cắt lại, đổi tốc độ hoặc đổi nguồn sau khi xử lý, ứng dụng từ chối áp dụng để tránh lệch đồng bộ. Có thể di chuyển cảnh sang vị trí khác trước khi áp dụng.

## Xử lý và lưu trữ

- AI chỉ xử lý vùng có thêm nền xung quanh ở cạnh dài 192/320 px; ghép vùng sửa trở lại khung gốc, giữ kích thước/FPS/âm thanh. Đây không phải độ phân giải của cả video đầu ra.
- Mỗi cửa sổ tối đa 60 khung hình. Có vùng chồng để hòa kết quả tại ranh giới; không trộn xuyên qua các cảnh riêng đã chọn.
- Các phần thành công lưu tại `.flowkit/propainter/parts/<hash>/`. Dừng/lỗi rồi chạy lại cùng nguồn, mốc cắt, vùng và thiết lập sẽ dùng lại chúng. Hoàn tất thì dọn phần trung gian.
- Kết quả và metadata lưu ở `output/_shared/editing/<video_id>/propainter/`. Bản đối chiếu nhỏ tối đa 640 px. Nguồn gốc luôn giữ nguyên.
- Kết quả hoàn tất dùng lại theo dấu nhận dạng nguồn và thiết lập. Mỗi lần áp dụng là một bước hoàn tác; thử lưu lại sau lỗi mạng không thêm đoạn hoặc tạo bước hoàn tác trùng.
- Nguồn ProPainter dùng `ProPainter-main/` nếu có; dự phòng `.flowkit/propainter/source/`. Kiểm tra sẵn sàng bao gồm entry point thật, Python, model và benchmark, không chỉ trạng thái thử nghiệm cũ.
- Worker chạy riêng, có khóa dùng bộ nhớ. Tốc độ và chất lượng phụ thuộc vùng/nội dung/máy; chưa tự bám logo chuyển động.
- Mặc định dùng vùng AI 192 px để nhẹ hơn trên Mac; 320 px tùy chọn khi cần chi tiết. Worker dọn cache MPS tại các điểm dọn cache GPU của upstream.

## API

- `GET /api/videos/{id}/editing/propainter?asset_id=<timeline_asset>`: kiểm tra môi trường, nguồn đã chọn và lịch sử. Không truyền asset thì dùng video nguồn mặc định.
- `POST .../propainter/clips`: `revision`, `clip_ids`, `region`, `dilation`, `max_side`. Chỉ đọc bản dựng đã lưu và tư liệu thuộc dự án. Kết quả chứa `trial_ids`; metadata từng kết quả có ánh xạ về cảnh gốc.
- `GET .../propainter/job`, `POST .../propainter/cancel?job_id=...`: chung workbench hiện có; hỗ trợ cả xử lý cảnh và đoạn thử.
- `GET .../propainter/results/{id}` và `.../{id}/before`: xem/tải và so sánh.
- API đoạn thử `POST .../propainter` vẫn hỗ trợ 1–3 giây/60 frame ở độ phân giải thấp để tương thích công cụ cũ. Đoạn thử cũ không được tự thay lên timeline.

ProPainter — Shangchen Zhou và cộng sự, S-Lab, NTU. Bản nguồn dự phòng: `sczhou/ProPainter` commit `e870e79321c31b733e2031af5aa2fb1fe3ac7eec`. Giữ LICENSE của upstream (NTU S-Lab 1.0, phi thương mại) trong thư mục nguồn; cần rà soát trước khi phân phối thương mại.

Kiểm thử: `venv/bin/python -m pytest tests/unit/test_propainter_restore.py tests/unit/test_editing_effects_api.py`; `node --test dashboard/tests/propainter.test.ts`. Kiểm thử mô hình thật tùy chọn: đặt `FLOWKIT_TEST_PROPAINTER=1` và chạy riêng `test_real_model_eight_second_logo_sample`.
