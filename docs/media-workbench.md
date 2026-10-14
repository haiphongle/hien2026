# Nhập video, phụ đề và lồng tiếng

Trong **Dự án → Hậu kỳ → Lời đọc & phụ đề → Hội thoại nhân vật**:

1. Bấm **Nhập video từ máy**. Hỗ trợ MP4, MOV, MKV, WebM, M4V; tối đa 5 GB và 60 phút.
2. Chờ chuẩn hoá xong. Video nhập là một video riêng trong dự án, không gửi lên Google Flow.
3. Chọn ngôn ngữ và **Nhận dạng bằng Whisper**. Lời nhận dạng lưu thành bản nháp.
4. **Đưa vào trình sửa bên dưới**, sửa lời, mốc thời gian, người nói; **Lưu kịch bản & giọng**.
5. Nếu cần dịch: chọn ngôn ngữ đích, nhập ngữ cảnh / tên riêng; bấm **Dịch bản đã lưu**. Từng nhóm dịch đúng được lưu ngay. Nếu bị lỗi, dừng hoặc khởi động lại máy chủ, bấm **Tiếp tục dịch** để dịch phần còn thiếu với cùng thiết lập. Xem bảng gốc–dịch khi đủ tất cả câu, đưa bản dịch vào trình sửa rồi lưu.
6. Xuất một trong hai cách:
   - **Xuất video chỉ phụ đề**: giữ tiếng gốc, thêm phụ đề đã lưu; không yêu cầu TTS.
   - Chọn giọng cho từng người, nghe mẫu, tạo các câu rồi **Ghép hội thoại & phụ đề**. Âm thanh gốc 0% sẽ tắt toàn bộ âm thanh gốc, gồm nhạc và hiệu ứng. Chưa có tách lời khỏi nhạc nền.
7. **Xem & tải video** để mở MP4. Có thể tải phụ đề SRT riêng trong trình sửa.

Nhạc nền đã ghép trong mục Nhạc nền được giữ ở lần xuất tiếp theo. Mỗi lần xuất bắt đầu từ bản nguồn sạch để tránh chồng lời hoặc phụ đề cũ.

## Sửa câu quá dài

- **Căn thời lượng câu này** hoặc **Tự căn thời lượng** dùng thời lượng giọng đã tạo để kéo dài khung. Mặc định giữ mốc bắt đầu các câu; có thể bật **Cho phép dời các câu phía sau** để tránh chồng lấn. Xem bảng trước/sau trước khi áp dụng. Nếu vượt cuối video, thao tác bị chặn; video nguồn không tự kéo dài. Dời mốc có thể làm lời lệch hình, đặc biệt với phim/lời hát.
- **Rút gọn lời** mở bản gốc và ô sửa với mục tiêu số ký tự dựa trên thời lượng thực tế, khung và tốc độ đọc phụ đề. Bạn tự sửa để giữ đúng ý; đây không phải rút gọn tự động bằng AI. Ước lượng cần được kiểm tra lại sau khi tạo giọng.
- **Tách câu** gợi ý điểm ngắt theo dấu câu/từ, cho sửa hai phần, giữ người nói/giọng/tốc độ và chia khung theo độ dài văn bản. Tách không làm tăng tổng thời gian. Hai câu mới cần tạo lại giọng; câu khác vẫn dùng âm thanh đã lưu.
- **Áp dụng vào bản nháp** chưa lưu lên server. Có **Hoàn tác lần sửa** trước khi lưu hoặc sửa tiếp. **Lưu & tạo** lưu toàn bộ bản nháp và tạo riêng các câu vừa sửa. Nếu chỉ đổi mốc, lưu kịch bản để tái sử dụng audio hiện có.

## Phạm vi hiện tại

- Whisper chạy tại máy, dùng Metal trên Mac hỗ trợ. Đã cài Whisper Small đa ngôn ngữ (~488 MB). Base và Large v3 Turbo Q5 chỉ chọn được nếu có model tương ứng.
- Phụ đề có tối đa 5.000 câu; trình sửa hiển thị 30 câu mỗi trang. Cảnh báo câu dài / nhiều ký tự mỗi giây chỉ là gợi ý đọc, cần người dùng xem lại.
- Dịch bằng CLI Codex hoặc Claude Code đã cài và đăng nhập. Gửi văn bản phụ đề, ngữ cảnh và từ điển tới nhà cung cấp đã chọn, sử dụng hạn mức tài khoản đó. Video và audio không gửi đi trong bước dịch.
- Bản dịch giữ ID, mốc thời gian, tên người nói và lựa chọn giọng. AI đảo thứ tự thì sắp lại bằng UUID chính xác, khi tất cả ID đầy đủ và không trùng. Không đoán ID theo vị trí/nội dung. Kết quả thiếu câu, sai ID, trùng ID hoặc lời trống được thử lại một lần, rồi chia nhóm nhỏ hơn; tối đa 12 lượt gọi AI cho mỗi nhóm ban đầu 20 câu. Lỗi tài khoản/CLI/kết nối không tự thử lại. Không tự ghi đè kịch bản đã lưu.
- Phần dịch đã lưu được dùng lại khi toàn bộ kịch bản, ngôn ngữ đích, nhà cung cấp/model/effort, ngữ cảnh và từ điển khớp. Thay đổi các mục đó bắt đầu bản dịch mới. **Dịch lại từ đầu** bỏ qua phần đã lưu, có thể dùng thêm hạn mức AI. Bản dịch hoàn chỉnh trước đó được giữ tới khi bản mới hoàn tất.
- VieNeu hiện dùng cho tiếng Việt. Dịch ra ngôn ngữ khác có thể xuất SRT / video phụ đề; chưa bổ sung TTS đa ngôn ngữ hay tự nhận diện tên người nói.
- Phụ đề xuất dùng trình dựng Pillow + FFmpeg hiện có. Chưa có bộ tuỳ chỉnh font, vị trí, karaoke, timeline waveform hoặc tách stem.
- Không nhúng ứng dụng Subtitle Edit .NET. Luồng biên tập, đối chiếu và kiểm tra phụ đề được triển khai trong giao diện FrameWeaver.

## Lưu trữ

Các đường dẫn sau nằm trong thư mục dự án FrameWeaver:

- `.flowkit/models/whisper/ggml-small.bin`: model, dùng chung cho các video.
- `output/_shared/media_workbench/<video-id>/source.mp4`: bản nguồn đã chuẩn hoá, không bị ghi đè khi ghép.
- Cùng thư mục: `source.json`, `job.json`, `transcript.json`, `translation.json`. `translation-progress.json` lưu ID và lời dịch đã kiểm tra để tiếp tục sau khi dừng/lỗi; chưa phải bản dịch hoàn chỉnh để áp dụng.
- `output/_shared/speech/videos/<video-id>/script.json`: kịch bản và giọng đã lưu; `audio/`: lời đọc được tái sử dụng khi nội dung, giọng, tốc độ không đổi.
- `output/quick_videos/<video-id>.mp4`: bản xuất hoàn chỉnh gần nhất.

File upload tạm được xoá sau chuẩn hoá hoặc huỷ tác vụ. ASR và dịch có tiến trình / nút dừng, trạng thái lưu trên máy; tác vụ bị ngắt bởi khởi động lại được đánh dấu lỗi để không quay mãi. Bản nháp đang sửa được khôi phục từ trình duyệt nếu phiên bản kịch bản và timeline vẫn khớp.

## Kiểm chứng

Chạy `venv/bin/python -m pytest tests/unit/test_subtitle_translation.py tests/unit/test_media_workbench.py tests/unit/test_speech.py tests/unit/test_audio_mix.py tests/unit/test_audio_mix_jobs.py -q`.

Trong `dashboard/`: `npm run lint` và `npm run build`.
Kiểm tra căn mốc, chồng lấn, vượt cuối video, tách câu và tái sử dụng audio: `npm run test:speech-repair` (Node 24).

Whisper Small tải từ kho model `ggerganov/whisper.cpp`, SHA256:
`1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
