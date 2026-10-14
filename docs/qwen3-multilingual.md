# Qwen3-TTS trong FrameWeaver

FrameWeaver dùng model `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` và source trong `Qwen3-TTS-main/`. Model có 9 giọng mẫu, đọc tiếng Trung, Anh, Nhật, Hàn, Đức, Pháp, Nga, Bồ Đào Nha, Tây Ban Nha và Ý. Tiếng Việt vẫn dùng VieNeu; Qwen3 không công bố hỗ trợ tiếng Việt. Bản 0.6B chọn chất giọng nhưng chưa hỗ trợ lệnh mô tả cảm xúc. Không hiển thị ô điều khiển sắc thái khi model này được chọn.

Tại **Hậu kỳ → Lời đọc & phụ đề theo thời gian**, chọn ngôn ngữ, mở **Giọng đa ngôn ngữ · Qwen3**, bấm **Chuẩn bị demo**, nghe thử rồi gán giọng cho người nói. Mỗi lần chỉ chuẩn bị 9 demo của ngôn ngữ đang chọn. Để làm lời thuyết minh theo cảnh, chọn **Qwen3** trong phần cài đặt giọng, chọn ngôn ngữ và giọng, sau đó tạo lời đọc như các bộ máy khác. Văn bản phải được dịch sang ngôn ngữ đó trước khi tổng hợp giọng.

Môi trường Qwen3 chạy ở `.flowkit/qwen3-venv`, model và cache ở `.flowkit/qwen3-cache`. Có thể đặt biến `QWEN3_PYTHON` và `QWEN3_CACHE_DIR` nếu muốn dùng ổ khác. Qwen3 được chạy trong tiến trình riêng và tự giải phóng bộ nhớ khi nhàn rỗi; API chính không nhập Torch. Lần đầu tạo demo sẽ tải model khoảng 2,5 GB. Nếu cache đặt trên ổ ngoài, cần gắn ổ trước khi mở FrameWeaver; trang sẽ báo môi trường chưa sẵn sàng khi ổ vắng mặt.

Môi trường cài từ source cần Python 3.12, `torch` và `torchaudio` cùng phiên bản, `transformers==4.57.3`, `accelerate==1.12.0`, `librosa`, `soundfile`, `sox`, `onnxruntime`, `einops`. Danh sách nằm trong `requirements-qwen3.txt`. Trên Mac không cài FlashAttention CUDA; worker dùng attention thường, MPS với BF16 để giảm bộ nhớ và tránh lỗi NaN của FP16. CPU dùng FP32. Worker Qwen giải phóng model sau hai phút không tạo giọng; các bộ máy khác giữ thời gian nhàn rỗi đang có. Khi model đã tải, worker nạp snapshot cục bộ để tokenizer không gọi mạng.

Nếu model đặt trên KING-500GB, backend chạy nền cần quyền **Ổ đĩa tháo rời** cho Python trong macOS. Khi hệ thống hỏi, người dùng quyết định cấp quyền. Kiểm tra môi trường có giới hạn thời gian; thiếu quyền hoặc ổ chưa gắn sẽ hiện trạng thái chưa sẵn sàng. Không cần cấp quyền toàn bộ ổ đĩa để dùng Qwen.

Đã thử trên máy Apple M1, RAM 8 GB: Ryan tiếng Anh tạo được WAV mono 24 kHz dài 3,44 giây; Whisper nhận dạng lại đúng câu demo. Lần tổng hợp đó mất khoảng 74 giây, nên với video nhiều câu Kokoro vẫn là lựa chọn tiếng Anh nhanh hơn. Model 1.7B và điều khiển cảm xúc chưa được triển khai ở bước này.

Mỗi câu Qwen có giới hạn xử lý từ 5 đến 30 phút tùy độ dài văn bản, tính riêng sau khi nạp model. Nếu vượt giới hạn, tiến trình được dừng và câu đó báo lỗi; không lưu tiếng bị cắt cụt. Khi chuẩn bị demo, thư viện tiếp tục các giọng còn lại và giữ những mẫu đã thành công. Bấm chuẩn bị lại chỉ tạo các mẫu còn thiếu.

Có thể bấm **Tạo demo** ngay trên một giọng chưa có mẫu, thay vì chờ cả thư viện. Kết quả được lưu theo giọng và ngôn ngữ; bấm **Nghe thử** để phát ngay những lần sau. Nếu Qwen sinh tiếng dài bất thường, worker tự thử lại một lần với mức lấy mẫu thấp hơn, trong cùng giới hạn thời gian. Giới hạn token giảm việc sinh tiếng kéo dài; tiếng vượt ngưỡng vẫn bị từ chối, không cắt cụt để coi là thành công. Khi cả hai lần thất bại, thông báo ghi độ dài và giới hạn cụ thể.

Giọng gốc thường ổn hơn khi đọc đúng ngôn ngữ: Ryan/Aiden cho tiếng Anh, Ono Anna cho tiếng Nhật, Sohee cho tiếng Hàn. Thử nghiệm Ono Anna tiếng Anh từng sinh tiếng kéo dài và lệch câu. Worker từ chối tiếng dài bất thường hoặc không có tiếng rõ, không cắt để biến nó thành kết quả thành công; demo cũ quá dài hoặc quá ngắn chưa được đối chiếu cũng không được tính là sẵn sàng.

Âm thanh mới được kiểm tra tín hiệu bằng bộ phân tích đã có. Khi tiếng quá ngắn so với câu hoặc gần như chỉ có khoảng im lặng, FrameWeaver dùng Whisper đã tải để đối chiếu lời đọc trên CPU. Kết quả cần kiểm tra, thiếu Whisper hoặc lỗi nhận dạng được ghi rõ cho câu đó; không tự đưa vào timeline như câu hợp lệ. Đây là cảnh báo từ nhận dạng, có thể sai với giọng hoặc cách phát âm khác lạ. Các câu có độ dài bình thường không chạy ASR tự động; muốn kiểm tra toàn bộ nội dung, dùng công cụ đối chiếu lời đọc. Kết quả kiểm tra được lưu cùng WAV, đọc danh sách và nghe lại demo không nạp model. Thay file, lời đọc, ngôn ngữ hoặc tốc độ sẽ làm kết quả kiểm tra cũ hết hiệu lực.

Tạo giọng và **Tự xử lý lồng tiếng** tiếp tục các câu còn lại khi một câu bị timeout hoặc tiến trình model dừng. Chạy lại dùng các câu đã thành công và chỉ tạo lại câu thiếu/lỗi. Các câu được gom theo bộ máy để giảm nạp model, vẫn giữ thứ tự và ID trong kịch bản. Sau vòng rút gọn AI cuối (tối đa hai vòng), hệ thống căn lại khoảng trống/tốc độ và kiểm tra WAV mới trước khi báo hoàn tất.

AI rút gọn xử lý tối đa 6 câu mỗi nhóm và lưu từng nhóm đã xong. Nhóm bị timeout được chia đôi ngay; không gửi lại nhóm lớn đó. Nếu một câu riêng vẫn timeout, tác vụ dừng với số câu cụ thể và nút **Tiếp tục tự xử lý** dùng lại phần đã lưu. Lỗi đăng nhập/tài khoản/CLI dừng ngay, không tự lặp yêu cầu.

Khi **Tự xử lý lồng tiếng** chỉ tăng tốc nhẹ cho một câu Qwen3/VieNeu, FrameWeaver chỉnh file WAV đã có bằng FFmpeg, giữ cao độ và bản giọng trước đó để hoàn tác. Không gọi lại model cho bước này. Kết quả đối chiếu Qwen hợp lệ được giữ cùng file đã chỉnh; WAV lỗi hoặc kiểm tra không khớp không được tái sử dụng. Đổi lời hoặc giọng vẫn cần tổng hợp lại; Kokoro giữ cách sinh giọng với tốc độ riêng trong model. Sau AI rút gọn, trạng thái ghi rõ đang tạo lại lời đã rút gọn. Với Qwen chạy trên M1, những câu đổi lời vẫn có thể mất hàng chục giây mỗi câu.

Đã kiểm tra thêm Ono Anna tiếng Nhật: mẫu tốc độ 1× dài 8,08 giây, tạo trong khoảng 78 giây; Whisper nhận dạng lại đúng câu demo và giao diện phát được file đã lưu. Tốc độ 1,2× cũng xuất WAV hợp lệ. Các ngôn ngữ còn lại chưa được kiểm tra chất lượng đọc thực tế trong lần tích hợp này.
