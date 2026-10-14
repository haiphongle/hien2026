# Kiểm tra âm thanh và người nói

Trong **Dự án → Hậu kỳ → Hội thoại & phụ đề**, mục **Kiểm tra chất lượng & người nói** có ba công cụ:

- **Kiểm tra lời đọc / video đã ghép**: quét file để tìm im lặng, clipping, tín hiệu lỗi và âm quá nhỏ/lớn. Bấm **Nghe đoạn này** để phát phần âm thanh được cảnh báo; phần nghe thử tự dừng ở cuối đoạn. Cảnh báo mang tính tham khảo; khoảng lặng có thể có chủ ý.
- **Nghe lại & đối chiếu**: dùng Whisper trên máy để nghe các câu tiếng Việt đã tạo. So sánh với bản kịch bản đã lưu, giữ nguyên tên có dấu, bỏ qua hoa/thường và dấu câu. Whisper có thể nhận sai; kết quả không đánh giá độ tự nhiên hoặc giống giọng.
- **Phân nhóm người nói**: phân tích âm thanh video gốc bằng sherpa-onnx trên CPU. Chọn tự động hoặc số người nói đã biết. Đặt tên cho từng nhóm, chọn giọng, sửa các câu không chắc chắn rồi đưa vào bản nháp. Cuối cùng bấm **Lưu kịch bản & giọng**. Các câu nhiều giọng/ít lời nói không được áp dụng mặc định. Không tự nhận biết tên hoặc danh tính.

Mỗi công cụ có tiến trình và Dừng. Hủy lúc chuẩn bị hoặc ghép nguồn cũng dừng tiến trình FFmpeg trước khi xóa tệp tạm và trả khóa xuất video. Kết quả thành công được lưu ở `output/_shared/media_workbench/<video-id>/analysis-*.json`. Chạy lại thất bại hoặc bị dừng giữ báo cáo thành công trước đó. Tệp tạm bị xóa sau công việc. Phân nhóm giữ một bản âm thanh gốc nén AAC trong cùng thư mục để nút **Nghe đoạn gốc** luôn phát đúng nguồn đã phân tích, kể cả sau khi lồng tiếng, và dừng ở cuối câu. Mỗi video chỉ giữ bản nghe lại mới nhất (khoảng 22 MB/giờ). Báo cáo được đánh dấu cũ khi kịch bản, mốc hoặc audio nguồn thay đổi. Kết quả kiểm tra không ngăn xuất video.

Đổi tên người nói trong các thẻ giọng để đổi đồng loạt. Dùng cùng tên để gộp; giọng tùy chỉnh từng câu được giữ và có cảnh báo khi một người đang dùng nhiều giọng. Có thể chuyển từng câu sang người khác trong trình sửa; thao tác phân nhóm không tự tách hoặc cắt nội dung câu.

## Cài trên máy mới

```sh
venv/bin/python -m pip install -r requirements-speech-analysis.txt
mkdir -p .flowkit/models/diarization
curl -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2 -o .flowkit/models/diarization/segmentation.tar.bz2
tar -xjf .flowkit/models/diarization/segmentation.tar.bz2 -C .flowkit/models/diarization
curl -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx -o .flowkit/models/diarization/embedding.onnx
```

Model phân đoạn khoảng 6 MB, model embedding khoảng 40 MB, gói native trên macOS ARM khoảng 12 MB. Giữ LICENSE/README đi kèm model. Xem [tài liệu model chính thức](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html) và [3D-Speaker](https://github.com/modelscope/3D-Speaker) về nguồn model. FrameWeaver triển khai luồng riêng; không phụ thuộc ứng dụng VoiceStudio.

Whisper dùng binary và model đã có trong `.flowkit/models/whisper`. Mỗi file lời đọc được nhận dạng độc lập; model nạp một lần cho mỗi nhóm tối đa 32 file. Kết quả gắn với ID câu bằng file nguồn, tránh nhầm giữa các câu do sai mốc thời gian nhận dạng. Phân tích audio và nhận diện chạy trong tiến trình con có thể hủy, chia sẻ hàng chờ với TTS/ASR để hạn chế tải máy. Chưa cài model thì chỉ chức năng tương ứng bị vô hiệu hóa.

## Kiểm thử

```sh
venv/bin/python -m pytest tests/unit/test_speech_analysis.py tests/unit/test_speech.py tests/unit/test_media_workbench.py -q
node --test dashboard/tests/speechSpeakers.test.ts dashboard/tests/speechRepair.test.ts dashboard/tests/speechPlayback.test.ts
FLOWKIT_TEST_SPEECH_MODELS=1 venv/bin/python -m pytest tests/integration/test_speech_analysis_local.py -q -s
```

Kiểm thử model thật dùng các bản nghe thử Thiện Minh và Mai Anh đã lưu; không tạo audio mới hay gửi dữ liệu ra dịch vụ bên ngoài. Đây là thử nghiệm hội thoại tổng hợp hai giọng, chưa đại diện cho phim có nhạc lớn, nhiều người nói chồng hoặc giọng gần giống nhau. Những trường hợp đó cần người dùng nghe và sửa nhãn.
