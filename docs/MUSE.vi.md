# Muse AI · desktop beta 3

[English](MUSE.md) · [Tải bản macOS](https://github.com/phucnguyentvp1/frameweaver/releases/tag/v1.3.1-beta.3)

Cả **Apple Silicon và Intel** beta 3 đều tích hợp Muse để lên kịch bản, tạo ảnh
tham chiếu/ảnh cảnh và tạo video từ ảnh đầu. Hướng dẫn này dành cho **ứng dụng
desktop đã đóng gói**, tách biệt với source preview cũ trên repository.

Muse kết nối qua [gateway cộng đồng muse2api](https://github.com/crisng95/muse2api).
Gateway, Chrome và tài khoản Muse được cài/kết nối riêng. Chỉ đăng nhập muse.ai
trong trình duyệt chưa tự kết nối FrameWeaver; URL website không phải URL gateway.

## 1. Khởi động gateway trên máy

Cài Chrome và đặt FrameWeaver trong `/Applications`. Ứng dụng có launcher tùy
chọn để tải gateway vào một môi trường Python riêng. Mở Terminal và chạy:

```sh
env -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  FRAMEWEAVER_DATA_DIR="$HOME/Library/Application Support/FrameWeaver" \
  "/Applications/FrameWeaver.app/Contents/Resources/runtime/python/bin/python3" \
  "/Applications/FrameWeaver.app/Contents/Resources/runtime/app/scripts/muse_gateway.py" \
  --install --background
```

Lệnh dùng cho **cả hai bản Mac**, cần mạng ở lần cài đầu. Gateway được ghim tại
revision [`efc06dd9741efe3b56a4bb02bb4761c0003eae30`](https://github.com/crisng95/muse2api/tree/efc06dd9741efe3b56a4bb02bb4761c0003eae30).
Sau khi khởi động lại máy, chạy lại lệnh **bỏ `--install`**. Gateway chưa tự khởi
động cùng máy. Nếu cổng 18610 đã có gateway chạy thì không mở thêm bản thứ hai.
Nếu không tìm thấy Chrome, thêm `--chrome` và đường dẫn file thực thi Chrome.

Launcher dùng hồ sơ trình duyệt riêng, tự lưu khóa gateway vào cấu hình riêng
tư của FrameWeaver, không tự đọc cookie Chrome của bạn. Môi trường, dữ liệu và
`gateway.log` được lưu tại:

```text
~/Library/Application Support/FrameWeaver/.flowkit/muse-gateway/
```

Nếu đã có gateway riêng, dùng hướng dẫn của gateway đó rồi nhập URL/khóa API
vào FrameWeaver. Gateway nội bộ chỉ nên lắng nghe localhost; gateway ngoài máy
phải dùng HTTPS.

## 2. Kết nối tài khoản trong FrameWeaver

1. Vào **Cài đặt → Muse**, bật kết nối. Với launcher ở trên, URL là
   **`http://127.0.0.1:18610`**. Lưu cấu hình; khóa đã được launcher lưu sẵn.
   Nếu dùng gateway khác, nhập khóa API của gateway đó.
2. Mở **Kết nối tài khoản Muse trên máy**. Trong Chrome đã đăng nhập muse.ai,
   mở **DevTools → Application → Cookies → https://muse.ai**. Sao chép cột Value
   của đúng bốn tên `hatch_sess`, `hatch_gw`, `hatch_vml`,
   `hatch_native_auth_device` vào các ô tương ứng trong FrameWeaver.
3. Xác nhận cho phép lưu phiên nội bộ, bấm **Lưu phiên và kiểm tra**. Form này
   chỉ hỗ trợ gateway trên máy tại cổng 18610. Phiên được giữ cho lần dùng sau;
   nhập lại khi Muse hết hạn phiên.
4. Bấm **Lưu và kiểm tra kết nối**. Gateway phải dùng driver **browser** và có
   tài khoản khả dụng. Driver **mock** bị chặn để không dùng kết quả giả.

Không lấy cookie Google/Gmail hoặc nhập mật khẩu Gmail. Không gửi giá trị cookie,
khóa API lên chat, ảnh chụp, issue hay GitHub. Dashboard gateway tại
`http://127.0.0.1:18610/dashboard` yêu cầu **khóa quản trị**, không phải mật khẩu
Muse. Launcher đi kèm dùng cùng một giá trị riêng tư cho khóa quản trị và API;
gateway khác có thể đặt hai khóa khác nhau. FrameWeaver kết nối bằng khóa API.
Nếu gateway tự quản dùng khóa quản trị riêng, hãy nhập tài khoản trực tiếp bằng
công cụ quản trị của gateway đó thay vì form tiện ích trong FrameWeaver.

## 3. Chọn Muse cho từng công đoạn

Trong **Studio → Video người que**, chọn riêng nhà cung cấp cho nội dung, ảnh
và video. Có thể dùng Muse lên kịch bản rồi Google Flow tạo media, hoặc dùng
Muse cho cả ba công đoạn được hỗ trợ. Giữ gateway chạy khi dùng Muse.

| Công đoạn | Hỗ trợ trong beta 3 |
| --- | --- |
| Kịch bản | Lên cảnh từ SRT/nội dung; duyệt trước khi sản xuất |
| Ảnh | Ảnh nhân vật/tham chiếu và ảnh cảnh; tối đa bốn ảnh tham chiếu mỗi yêu cầu |
| Video | Tạo chuyển động từ ảnh đầu; yêu cầu tích hợp dùng clip tám giây |
| Giọng đọc | Dùng các nhà cung cấp TTS hiện có, không tạo qua Muse |
| Ảnh cuối / upscale | Dùng luồng nhà cung cấp có hỗ trợ; tích hợp Muse này chưa hỗ trợ |

Luồng: **SRT/nội dung → duyệt kịch bản → ảnh tham chiếu → ảnh cảnh → duyệt ảnh
→ chuyển động → hậu kỳ → xuất video**. Chế độ ảnh + hiệu ứng dựng chuyển động
bằng FFmpeg trên máy. Chọn Muse tạo ảnh không bắt buộc tạo clip AI; khi chọn
tạo clip AI thì vẫn dùng hạn mức của nhà cung cấp.

Google Flow vẫn cần extension FrameWeaver và tab Flow đã đăng nhập. Muse dùng
gateway riêng. **Tạo dự án mới trong beta này vẫn cần kết nối Flow**, kể cả khi
các công đoạn tiếp theo dùng Muse.

`muse-chat`, `muse-image`, `muse-video` là tên model của gateway. Các tên khác
trong catalog có thể chỉ là alias, không chứng minh model thương hiệu nào thực
sự chạy. FrameWeaver chưa xác nhận được số token, hạn mức ảnh/video, quyền truy
cập hay chi phí tài khoản Muse; kiểm tra với nhà cung cấp.

## Lỗi thường gặp và phạm vi bản beta

- **Gateway chưa sẵn sàng / 503:** kiểm tra gateway đang chạy ở cổng 18610,
  driver `browser`, tài khoản khả dụng và file `gateway.log`.
- **401/403:** kiểm tra khóa gateway và phiên Muse. Đăng nhập lại website chưa
  tự cập nhật phiên mà gateway đang giữ.
- **Failed/timeout:** kết quả hoàn tất được giữ. Tiếp tục phần thiếu thay vì gửi
  lại toàn bộ; lỗi tạo media ở dịch vụ vẫn cần xem trong Muse/gateway.
- **Hủy:** dừng chờ/hàng đợi FrameWeaver. Gateway chưa có API hủy media đã gửi;
  tác vụ phía Muse có thể vẫn chạy và tính hạn mức.

Báo cáo kiểm thử desktop gồm khởi động cục bộ, kiểm tra kết nối, dựng và xuất.
Không xác nhận tốc độ, chất lượng, hạn mức Muse thật hoặc cài gateway thành công
trên mọi máy. Bản Intel kiểm thử qua Rosetta, chưa thử trên máy Intel thật.
Gateway là dự án cộng đồng riêng, không phải SDK chính thức của Muse. Sử dụng
theo điều khoản nhà cung cấp.

Dữ liệu ứng dụng nằm trong `~/Library/Application Support/FrameWeaver`. Bộ cài
không chứa tài khoản, cookie, khóa hoặc database của người build. Không chia sẻ
thư mục `.flowkit` hay media cá nhân cùng source.
