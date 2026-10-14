# Video người que trong Studio

1. Mở dự án → **Studio → Video người que**.
2. Chọn **Người que** hoặc **Nhân vật 2D** có thân, tay chân đầy đủ; mỗi loại có nền trắng, nền đen hoặc phấn sáng. Chọn khung dọc/ngang.
3. Dán chủ đề, lời bình hoặc SRT. Để **Số cảnh → Tự động theo nội dung** → **AI lên kịch bản**; chọn **Tự chọn** nếu muốn nhập số cảnh.
4. Duyệt và sửa tên video, lời đọc, hành động minh họa từng cảnh.
5. Chọn cách chuyển động; với đối thoại bật **A và B**, chọn người nói, biểu cảm và cử chỉ ở mỗi cảnh.
6. Nghe demo và chọn giọng Việt (VieNeu) hoặc Anh (Kokoro); đối thoại chọn riêng giọng A và B.
7. Chọn nhạc đã có trong dự án hoặc tải MP3/WAV lên. Nhạc tự giảm khi có lời đọc.
8. Xem số ảnh và clip cần tạo → **Tạo video hoàn chỉnh** → **Mở timeline để chỉnh tiếp**.

## Luồng tự động từ SRT

Dán SRT → chọn phong cách và giọng → **Tạo video tự động**. AI đọc toàn bộ nội dung, giữ nguyên lời SRT và tự chia các ý thành cảnh. Có thể chọn **Bối cảnh có đồ vật, chiều sâu** trong phần phong cách nếu muốn thấy nhà cửa, đường phố và môi trường thay vì nền trắng/đen thuần.

- Giọng kể nằm ngoài hình mặc định, không tự trở thành một nhân vật. AI chọn tối đa sáu nhân vật chính A–F; chỉ gán người thực sự có mặt ở mỗi cảnh. Chế độ đối thoại vẫn dùng hai giọng A/B.
- Cảnh có thể là hành động, bối cảnh không người, cận đồ vật hoặc hình ảnh ẩn dụ. Bạn bè, người qua đường được mô tả như người phụ khi cần; không phải tạo ảnh chân dung cho từng người.
- AI đề xuất tối đa bốn tham chiếu bối cảnh/đạo cụ quan trọng dùng lại, tối đa hai ở mỗi cảnh. Đồ vật thông thường nằm trong mô tả cảnh. Phần **Bối cảnh và đạo cụ dùng lại** cho sửa mô tả hoặc chọn ảnh từ thư viện; để tự động nếu không cần chỉnh.
- Bật **Xem toàn bộ ảnh trước khi tạo chuyển động**: ứng dụng tạo tham chiếu rồi tất cả ảnh cảnh, dừng để duyệt một lượt. Bấm **Tạo lại ảnh cảnh…** chỉ tạo lại ảnh đó; **Duyệt tất cả & tạo video** mới bắt đầu lời đọc và chuyển động. Bỏ chọn để chạy đến video hoàn chỉnh.
- Chế độ **Hoạt hình AI** dùng ảnh của từng cảnh làm khung hình đầu, cùng ba nhịp hành động của chính cảnh đó. Số nhân vật, bối cảnh và đồ vật được giữ theo ảnh; kết quả AI vẫn cần kiểm tra trực quan. Các chế độ zoom/pan chỉ di chuyển camera trên ảnh.

Tất cả dùng chung thư viện tham chiếu, batch Google Flow, bộ giọng và timeline hiện có. Kịch bản cũ vẫn mở được; để nhận cách phân cảnh mới, phân tích lại SRT cho video mới.

## Ba cách chuyển động

| Cách | Chuyển động | Tạo tư liệu |
| --- | --- | --- |
| Ảnh chuyển động | Zoom nhẹ, pan ngang hoặc giữ tĩnh; camera di chuyển trên ảnh | Tham chiếu và ảnh cảnh qua Google Flow |
| Hoạt hình AI | Nhân vật hành động theo prompt 0–3s, 3–6s, 6–8s | Tham chiếu → ảnh cảnh → clip Flow 8 giây |
| Người que nói chuyện · thử nghiệm | Nhân vật vector, biểu cảm, cử chỉ và miệng theo năng lượng WAV; một người hoặc A/B luân phiên | Dựng trên máy, không gửi yêu cầu Google Flow |

Chế độ nói chuyện dùng nhân vật vector do FrameWeaver vẽ, không chuyển ảnh tham chiếu thành nhân vật có khẩu hình. Miệng khép khi im lặng; chưa căn âm vị hay cam kết đồng bộ khẩu hình tiếng Việt. Hành động chi tiết như chạy/kéo đồ vật cần chế độ hoạt hình AI. Chọn clip nói chuyện trên timeline → **Nhân vật nói chuyện** để đổi biểu cảm/cử chỉ; áp dụng qua bộ lưu và hoàn tác của editor.

**Nhân vật 2D** dùng hình người tròn, thân liền, tay chân có độ dày và biểu cảm đen trắng. Nền trắng dùng viền đen, nền đen dùng hình trắng, phấn sáng dùng viền phấn lam nhẹ. Áp dụng cả ba cách chuyển động. Đổi mẫu 2D sẽ chọn lại tham chiếu tự tạo; nếu chọn ảnh có sẵn, hình dáng trong ảnh đó vẫn quyết định nhân vật của Google Flow. Các mẫu người que và bản dựng đã lưu giữ nguyên.

## Cách dựng

- Đo WAV thật rồi căn độ dài hình theo lời. Nếu bật duyệt toàn bộ ảnh, tạo WAV sau khi duyệt. Không ép mọi cảnh vào 8 giây.
- Chế độ AI cắt clip 8 giây theo lời; lời dài hơn clip thì giữ khung hình cuối. Muốn chuyển động liên tục, chia lời thành cảnh ngắn hơn.
- SRT tự động được AI đọc toàn bộ và gộp theo ý nghĩa; máy chủ lấy lời từ SRT gốc rồi tách thành tối đa 200 cảnh. Với hoạt hình AI, mỗi cảnh giữ tối đa 28 từ để phù hợp clip ngắn. Không phải tự chia SRT ở mốc 12 cảnh; bản nháp, bảng prompt và luồng tạo video cùng hỗ trợ số cảnh mới. Vẫn giới hạn đầu vào 200 câu SRT / 12.000 ký tự. Giữ nguyên lời và thứ tự, bỏ dấu trích dẫn `[cite: 2]`. Chế độ tự chọn đặt số nhóm nội dung trước khi tách lời dài. Mốc SRT cũ được thay bằng thời lượng tiếng đọc.
- Phụ đề được chia theo độ dài cụm từ, chưa căn chính xác từng từ bằng nhận dạng âm thanh. Có thể kéo mốc trên timeline.
- Tự tạo một ảnh nhân vật tham chiếu theo phong cách, hoặc dùng nhân vật đã có. Tạo xong tham chiếu mới tạo ảnh cảnh.
- Tất cả yêu cầu tạo ảnh đi qua hàng đợi batch hiện có; không có hàng đợi Google Flow riêng.
- Bộ tạo giọng, nhạc, bộ trộn và xuất video đều dùng dịch vụ hiện có. Không cài thêm các repo tham khảo.
- Lời, hình và tiếng liên kết thành nhóm trên timeline. Không dùng miệng hoặc chớp mắt để chèn thêm thời gian.

## Kiểm tra và dùng lại

- **Lưu mẫu** giữ phong cách, khung hình, tham chiếu, giọng A/B, tốc độ và nhạc; chọn mẫu trong cùng dự án cho lần sau. Nội dung kịch bản không bị thay thế.
- **Cập nhật video đã tạo** chỉ tạo lại tư liệu bị thay đổi, giữ các bản xuất cũ. Cần giữ nguyên số cảnh; timeline đã chỉnh thủ công thì tạo video mới để tránh mất bản dựng.
- **Kiểm tra chất lượng** hiện thời lượng lời/hình và cảnh báo chữ đọc nhanh. Chế độ AI dùng bộ review light hiện có để kiểm tra nhân vật; lỗi review được hiển thị, không âm thầm tạo lại clip và tốn tín dụng.
- **Whisper nghe lại** là tùy chọn kiểm tra thiếu từ, dùng model đã có và chỉ nhận dạng WAV thay đổi. Không tự sửa nội dung hoặc hứa căn từng từ.
- Hủy/tiếp tục dùng hệ thống tác vụ có sẵn. Bản xuất xem/tải trong lịch sử xuất của Hậu kỳ.

## Lưu và tiếp tục

Bản nháp Studio lưu trong trình duyệt. Kịch bản đã bắt đầu, ảnh tải về, WAV, clip ảnh và timeline lưu trong thư mục `output` của FrameWeaver:

- `output/_shared/media_workbench/<video-id>/stickman/`: kịch bản, cache Google, báo cáo; `renders/` và `voices/` lưu tư liệu theo chữ ký bất biến để bản cũ không mất nguồn.
- `output/_shared/stickman_templates/<project-id>/`: các mẫu sản xuất.
- `output/<tên-dự-án>/tts/`: WAV giọng đọc và chữ ký cache.
- `output/_shared/timeline/<video-id>/`: các lớp và tư liệu bản dựng.
- Bản xuất nằm trong thư mục exports của hệ thống hậu kỳ, giữ nguyên video nguồn.

Lỗi hoặc dừng: bấm **Tiếp tục phần còn thiếu**. Tác vụ dùng kịch bản đã lưu lúc bắt đầu, tái sử dụng giọng và ảnh hợp lệ. Nếu đã dựng xong timeline, tiếp tục xuất timeline đang lưu. Nếu sửa timeline thủ công khi đang cập nhật cảnh, tác vụ từ chối ghi đè. Các yêu cầu Google Flow đã gửi có thể vẫn hoàn thành sau khi dừng việc dựng local.

## Nguồn tham khảo và giấy phép

Kiểm tra các bản tải về trong workspace:

| Nguồn | Giấy phép thấy trong bản tải | Cách sử dụng |
| --- | --- | --- |
| stickman-video-director-main | MIT | Học cách phân cảnh và hướng dẫn chuyển động |
| Stickman-Studio-main | Không thấy LICENSE | Tham khảo luồng giao diện; không sao chép code/tư liệu |
| synctoon-main | GPL-3.0 | Học cách phân vai, biểu cảm và đồng bộ; không sao chép code/tư liệu, không thêm phụ thuộc |

Bộ vẽ vector của FrameWeaver được viết riêng bằng Pillow và FFmpeg đã có. Trước khi đóng gói/phân phối, vẫn phải kiểm tra riêng giấy phép model TTS, FFmpeg build, font, nhạc và tư liệu người dùng chọn; giấy phép tham khảo không thay thế các giấy phép đó.
