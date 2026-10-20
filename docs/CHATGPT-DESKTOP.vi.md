# Kết nối ChatGPT · desktop macOS beta 3

[English](CHATGPT-DESKTOP.md) · [Tải bộ cài](releases/v1.3.1-beta.3-macos.md)

Dùng cho cả Apple Silicon và Intel, macOS 13+. Hướng dẫn dành cho ứng dụng beta 3
đã đóng gói; source preview cũ trên repository chưa có trình cài này.

## Cách dùng

1. Vào **Cài đặt → ChatGPT**. Nếu báo thiếu thành phần, bấm **Cài thành phần ChatGPT**.
2. Đợi tải, kiểm tra checksum, giải nén và kiểm tra phiên bản. Có tiến trình,
   hủy/thử lại, nguồn tải chính xác và thư mục lưu. Không cần Node.js, Terminal hay quyền admin.
3. Bấm **Đăng nhập bằng ChatGPT**, hoàn tất đăng nhập OpenAI trong trình duyệt.
   Nếu trình duyệt không tự mở, dùng liên kết đăng nhập được hiển thị.
4. Quay lại FrameWeaver và kiểm tra tài khoản. Chọn Codex cho tác vụ AI cần dùng;
   đăng nhập không tự đổi cấu hình các tác vụ đã lưu.
5. Có thể bấm **Kiểm tra AI**: gửi một yêu cầu văn bản nhỏ qua tài khoản và dùng
   hạn mức tương ứng. Nút này chưa kiểm tra khả năng đọc ảnh.

Ứng dụng kết nối qua Codex CLI chính thức; không đọc cookie tab ChatGPT web.
Chỉ đăng nhập ChatGPT web chưa đủ. Quyền truy cập và hạn mức phụ thuộc OpenAI.
CLI đã cài hoặc CLI của ứng dụng ChatGPT được ưu tiên, không bị ghi đè. Đăng nhập
sử dụng kho tài khoản Codex hiện có trên máy.

## Nguồn tải và nơi lưu

Ghim **Codex CLI 0.163.0**, tải từ
[bản phát hành chính thức của OpenAI](https://github.com/openai/codex/releases/tag/rust-v0.163.0).
Kiểm tra dung lượng và SHA256 theo kiến trúc trước khi áp dụng. Tải lỗi/hủy không
để lại file thực thi dở dang.

```text
~/Library/Application Support/FrameWeaver/.flowkit/tools/codex/0.163.0/<platform>/codex
```

`<platform>` là `aarch64-apple-darwin` với M1 trở lên hoặc `x86_64-apple-darwin`
với Intel. Gói tải khoảng 100–109 MB; cần khoảng 700 MB trống lúc cài. Chỉ tải khi
bấm cài, không nhét sẵn trong DMG. Không có tài khoản/khóa của người build.

## Tải lại và xử lý sự cố

- **Tải lại giao diện** ở thanh trên tương tự F5; làm mới trang hiện tại, không
  khởi động lại backend. Cảnh báo lưu/rời trang của trình sửa vẫn được giữ.
- Trạng thái extension ở thanh bên dùng chung với Cài đặt/Hướng dẫn; **Kiểm tra
  kết nối** cập nhật ngay, tự kiểm tra tiếp khi mất/kết nối lại.
- Extension xanh chỉ xác nhận cầu nối. Đọc chẩn đoán để biết tab Flow, tài khoản,
  model sẵn sàng hay chưa. Kiểm tra kết nối không tạo media và không tốn credit tạo ảnh/video.
- Tải lỗi: xem nguồn tải, mạng, dung lượng và quyền ghi rồi thử lại; không cần npm.
- Đăng nhập xong nhưng AI lỗi: kiểm tra hạn mức/gói và tài khoản. Không đăng token,
  cookie, liên kết đăng nhập hoặc mật khẩu lên issue.

Trình cài và các route lõi đã được kiểm tra bằng runtime đóng gói hai kiến trúc.
Intel kiểm thử qua Rosetta, chưa thử trên máy Intel thật hoặc Mac sạch thứ hai.
