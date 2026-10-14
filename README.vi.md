<p align="center"><img src="docs/images/frameweaver_banner.svg" width="880" alt="FrameWeaver" /></p>

<p align="center"><a href="README.md">English</a> · <a href="https://github.com/phucnguyentvp1/frameweaver/releases">Tải bản phát hành</a> · <a href="https://github.com/phucnguyentvp1/frameweaver/issues">Báo lỗi / góp ý</a></p>

# FrameWeaver

**Không gian làm video trên máy cá nhân: cảnh, giọng đọc, âm thanh và timeline.**

FrameWeaver kết hợp dashboard React, backend Python và Chrome extension để tổ
chức dự án, dùng lại ảnh tham chiếu, tạo cảnh qua phiên Google Flow của bạn,
chuẩn bị lời đọc và dựng video bằng FFmpeg.

Được phát triển và duy trì bởi [phucnguyentvp1](https://github.com/phucnguyentvp1),
FrameWeaver kết hợp tạo cảnh với bàn dựng nhiều lớp, thư viện media, phân tích
giọng nói, lời đọc, phụ đề và lịch sử bản xuất. Dự án hiện có bản preview để dùng thử.

## Từ ý tưởng đến video của bạn

**YouTube Shorts, Reels, truyện nhân quả, người que, nhân vật 2D — và nhiều hơn nữa.**

Chia câu chuyện thành cảnh, thêm giọng đọc và phụ đề, rồi hoàn thiện bằng timeline.
Dưới đây là các ảnh demo do người duy trì FrameWeaver cung cấp.

<table>
  <tr>
    <th width="33%">Truyện nhân quả</th>
    <th width="33%">Kể chuyện người que</th>
    <th width="33%">Truyện gia đình 2D</th>
  </tr>
  <tr>
    <td valign="top"><a href="docs/images/showcase/karma-story-short.png"><img src="docs/images/showcase/karma-story-short.png" width="260" alt="Video dọc minh họa truyện nhân quả, có tiêu đề và phụ đề tiếng Việt" /></a></td>
    <td valign="top"><a href="docs/images/showcase/stickman-family-short.png"><img src="docs/images/showcase/stickman-family-short.png" width="260" alt="Câu chuyện người que về gia đình, có nhân vật cha, mẹ, con và phụ đề" /></a></td>
    <td valign="top"><a href="docs/images/showcase/illustrated-family-short.png"><img src="docs/images/showcase/illustrated-family-short.png" width="260" alt="Truyện gia đình bằng minh họa 2D, có tiêu đề và phụ đề" /></a></td>
  </tr>
  <tr>
    <td>Minh họa giàu không khí, tiêu đề và giọng kể theo cảnh.</td>
    <td>Nhân vật tối giản cho chuyện đời thường và nội dung giải thích.</td>
    <td>Câu chuyện giàu cảm xúc, kết hợp hình ảnh, lời đọc và phụ đề.</td>
  </tr>
</table>

Xem ảnh lớn trong [bộ sưu tập demo và giao diện](docs/SHOWCASE.md).

## Chức năng

- Quản lý dự án, nhân vật, ảnh tham chiếu, prompt và hàng chờ tạo ảnh/video.
- Kết nối Google Flow qua Chrome extension và phiên đăng nhập của chính bạn.
- Chọn phong cách minh họa, người que và nhân vật 2D; lưu yêu thích, thêm ảnh mẫu và dùng lại mẫu sản xuất.
- Soạn lời đọc, sửa SRT, chọn giọng và dùng các bộ máy TTS cục bộ tùy chọn.
- Phân tích giọng nói và căn thời gian nội dung với các công cụ tùy chọn.
- Quản lý nhạc/hiệu ứng, trộn âm thanh, chèn chữ và chỉnh khung hình.
- Dựng timeline kéo thả nhiều lớp, sắp xếp/cắt/tách đoạn, chuyển cảnh, xuất thử và lưu lịch sử MP4.
- Quản lý thư viện media cục bộ, video nhập vào và âm thanh dùng lại.
- Dùng các công thức `/fk-*` với coding agent tương thích.
- Dashboard có tiếng Anh, Việt, Hindi, Indonesia, Trung, Hàn và Nhật.

## Bàn dựng và dashboard

### Timeline kéo thả nhiều lớp

<a href="docs/images/showcase/timeline-editor.png"><img src="docs/images/showcase/timeline-editor.png" width="1040" alt="Bàn dựng FrameWeaver với thư viện tư liệu, màn hình xem trước và các lớp hình ảnh, giọng đọc, phụ đề, nhạc nền" /></a>

Đưa hình ảnh, lời đọc, phụ đề, nhạc và lớp phủ vào cùng một bản dựng. Kéo tư liệu
từ thư viện xuống track, di chuyển đoạn, tách tại vạch phát, chỉnh khung hình và
xem thử trước khi xuất. Bàn dựng có tự giảm nhạc khi có lời kể, hoàn tác/làm lại,
tự lưu và lịch sử bản xuất.

### Thư viện phong cách trực quan

Từ người que nền sáng/tối, nhân vật 2D, minh họa điện ảnh, chibi và tranh vẽ tay
đến truyện dân gian Việt Nam, truyện nhân quả. Chọn mẫu, lưu yêu thích hoặc thêm
phong cách từ ảnh tham chiếu của bạn.

<details>
  <summary><strong>Mở ảnh thư viện phong cách</strong></summary>
  <p><a href="docs/images/showcase/style-library.png"><img src="docs/images/showcase/style-library.png" width="820" alt="Thư viện phong cách FrameWeaver với mẫu người que, 2D, chibi, tranh vẽ tay, truyện dân gian và truyện nhân quả" /></a></p>
</details>

<details>
  <summary><strong>Mở ảnh dashboard và hướng dẫn kết nối</strong></summary>
  <p><img src="docs/images/frameweaver_dashboard.jpg" width="920" alt="Dashboard FrameWeaver và hướng dẫn cài đặt, kết nối extension" /></p>
  <p>Extension mang tên <strong>FrameWeaver 0.3.5</strong>. Xem thêm <a href="docs/images/frameweaver_extension.jpg">ảnh giao diện extension</a>.</p>
</details>

## Cài đặt nhanh

Chuẩn bị **Python 3.12**, **Node.js 24**, Chrome, FFmpeg và ffprobe. Các lệnh dưới
đây dùng shell macOS/Linux. Windows có thể điều chỉnh qua WSL; bản preview chưa
được xác minh đầy đủ trên Windows.

```sh
git clone https://github.com/phucnguyentvp1/frameweaver.git
cd frameweaver
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Tạo một dự án trong Google Flow, lấy UUID từ URL và điền vào `FLOW_PROJECT_ID`
trong `.env`. Điền khóa của bạn cho dịch vụ tùy chọn nếu cần, rồi chạy backend:

```sh
set -a
source .env
set +a
python -m agent.main
```

Backend đọc biến môi trường của shell, **không tự nạp `.env`**. Giữ terminal này mở.

Trong Chrome, vào trình quản lý extension, bật Developer mode, chọn **Load
unpacked** và trỏ đến thư mục `extension/`. Mở [Google Flow](https://flow.google.com/),
đăng nhập và giữ tab đó mở. Nếu tải ZIP extension riêng, giải nén trước và chọn
thư mục chứa `manifest.json`.

Mở terminal thứ hai:

```sh
cd frameweaver/dashboard
npm ci
npm run dev
```

Truy cập **http://127.0.0.1:5173**. Backend dùng cổng **8100**, cầu nối extension
sử dụng **9222**. Cấu hình mặc định chỉ lắng nghe trên localhost.

```sh
curl -s http://127.0.0.1:8100/health
curl -s http://127.0.0.1:8100/api/flow/status
```

Trước khi tạo nội dung, cần thấy `extension_connected: true`. Trạng thái Flow
phải có transport `batch` và UUID dự án. `flow_key_present: false` là bình thường
với transport hiện tại.

Nếu bạn đang dùng một bản trong Downloads hoặc thư mục khác, bản đó tiếp tục đọc
code của thư mục cũ. Hãy chạy backend/dashboard và nạp extension từ đúng bản
FrameWeaver; chỉ bật một cầu nối cho cùng backend.

## Quy trình làm video

1. Tạo dự án cục bộ và liên kết với dự án Google Flow.
2. Khai báo nhân vật, địa điểm, đạo cụ; tạo đủ ảnh tham chiếu trước.
3. Viết cảnh theo hành động và gắn các nhân vật/tham chiếu liên quan.
4. Tạo ảnh cảnh, sau đó tạo clip qua hàng chờ.
5. Duyệt chất lượng clip trước khi dùng thao tác upscale được hỗ trợ.
6. Chuẩn bị giọng đọc, phụ đề, nhạc và chỉnh sửa timeline.
7. Xuất thử hoặc xuất MP4 hoàn chỉnh để tải xuống.

Gửi yêu cầu tạo hàng loạt qua `/api/requests/batch`; server tự điều tiết số tác
vụ đồng thời và thời gian nghỉ. Xem [skills/README.md](skills/README.md).

## Giới hạn của bản preview

- Veo video upscale, reference-to-video và nối cảnh bằng khung đầu/cuối được ghi
  nhận là chưa port sang transport mới. Xem [ghi chú transport](docs/CAPTURE.md).
- Chưa xác minh lại generation trực tiếp riêng cho bản đổi tên FrameWeaver.
- Ổ ngoài trên macOS có lỗi liên quan đến metadata `._*` và timestamp của cache.
  Khi dùng thử trên Mac, nên đặt dữ liệu làm việc và thư mục tạm trên APFS.
- TTS, phân tích giọng và phục hồi video cần cài thêm dependency/model tương ứng.
- Tài khoản, phiên đăng nhập, AI credits và subscription không nằm trong gói nguồn.
- Tên file xuất do ứng dụng đặt dùng `frameweaver-`; tên người dùng tự đặt và file
  tải lên giữ tên đã lưu. Gói chia sẻ không chứa database hoặc media cá nhân.

Các kiểm tra tập trung của backend, test khởi động MV3 và build dashboard đã đạt.
Bản preview chưa được tuyên bố vượt qua toàn bộ kiểm thử trên mọi nền tảng.

## Tài liệu và đóng góp

- [Hướng dẫn chia sẻ và môi trường](docs/SHARING.md)
- [Bộ sưu tập demo và giao diện](docs/SHOWCASE.md)
- [Tài liệu kỹ thuật tham khảo](docs/REFERENCE.md)
- [Bàn dựng timeline](docs/TIMELINE_EDITOR.md)
- [Phân tích giọng](docs/speech-analysis.md)
- [Lịch sử bản xuất](docs/EXPORT_HISTORY.md)
- [Hướng dẫn đóng góp](CONTRIBUTING.md)
- [Báo lỗi bảo mật riêng tư](SECURITY.md)

Ưu tiên tiếp theo: cài lần đầu dễ tái tạo, sửa tương thích ổ ngoài, kiểm tra thêm
nền tảng và làm demo trực tiếp bằng FrameWeaver. Có thể đề xuất qua Issues hoặc
pull request, kèm bước tái hiện và cách đã kiểm tra.

## Giấy phép và ghi nhận

FrameWeaver được phân phối theo giấy phép **MIT**. Một phần code được sử dụng và
điều chỉnh từ [Flow Kit](https://github.com/crisng95/flowkit); thông báo bản quyền
và giấy phép gốc cho phần code đó được giữ trong [LICENSE](LICENSE). Các tính năng
bổ sung và thay đổi của FrameWeaver được phát triển trong repo này. Giọng mẫu và tài nguyên
bên thứ ba có điều kiện riêng, gồm ghi chú CC BY-NC cho một số giọng mẫu kế thừa;
đọc [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) trước khi phân phối hoặc dùng
thương mại các tài nguyên đó.

Cảm ơn các tác giả và cộng tác viên Flow Kit cùng những dự án tích hợp. FrameWeaver
là dự án cộng đồng độc lập, không liên kết chính thức với Google, Anthropic hay OpenAI.
