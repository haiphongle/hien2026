# Bàn dựng timeline — giai đoạn 1–6

Vào **Dự án → Hậu kỳ → Bàn dựng timeline**. Nút mở rộng bên cạnh Hoàn tác
cho phép dùng phần lớn màn hình; Escape thu gọn. Kéo đường phân cách để đổi
chiều rộng thư viện và bảng thuộc tính.
Đường phân cách ngang thay đổi phần diện tích dành cho timeline.

## Thao tác

- Thư viện gồm video đã lưu trong cùng dự án, các bản xuất của video đang chọn,
  video đã ghép giọng/nhạc nếu có, tất cả nhạc trong dự án và hiệu ứng âm thanh.
- **Tải nhạc** nhập MP3/WAV tối đa 100 MB vào kho nhạc dự án. **Tải hiệu ứng**
  nhập MP3/WAV/M4A/OGG/FLAC/AAC tối đa 16 MB / 30 giây vào kho của video đang
  chọn. Có tiến trình và hủy tải. File đã lưu xuất hiện sau khi nạp lại trang;
  chọn tab Nhạc/Hiệu ứng, nghe thử rồi kéo vào timeline hoặc bấm **+**.
- Kéo tư liệu xuống timeline. Nút **+** thêm vào cuối. Nếu video mới không vừa
  khoảng trống, nó được đặt sau các đoạn đang có.
- Hình và âm gốc được liên kết. Kéo thân đoạn để di chuyển; kéo qua một đoạn
  hình khác để đổi thứ tự và nối liền các đoạn. Các lớp khóa không bị di chuyển.
- Kéo mép trái/phải để cắt. Chọn đoạn, kéo vạch phát trên thước rồi bấm **Tách**.
- **Xóa** giữ khoảng trống. **Xóa & nối** bỏ khoảng thời gian đó trên mọi lớp,
  bao gồm cắt đoạn âm thanh đi qua khoảng bị xóa. Lớp khóa chặn thao tác này.
- Shift + bấm để chọn nhiều đoạn. Có thể nhân đôi, bỏ liên kết hoặc liên kết
  hình/tiếng của cùng tư liệu có cùng mốc. Âm lượng riêng nằm trong Thuộc tính.
- Nam châm bám mép và vạch phát. Giữ Shift trong lúc kéo để bỏ bám tạm thời.
- **Vừa khung** căn toàn bản dựng; thanh Thu phóng và thanh cuộn dùng để sửa kỹ.
- Sóng âm và dải ảnh lấy từ file thật, được tạo tự động cho video đang mở;
  **Vẽ sóng & ảnh** chuẩn bị tư liệu mới. Dữ liệu này được lưu đệm theo dấu vân
  tay file, không giải mã toàn bộ audio trong trình duyệt.
- Space phát/dừng, S tách, Delete xóa, Shift + Delete xóa và nối, ⌘D nhân đôi,
  ⌘Z/⌘⇧Z hoàn tác/làm lại, ⌘S lưu. Phím tắt không chạy khi đang nhập văn bản.

## Lưu và xuất

Bản dựng tự lưu sau khoảng 700 ms. Thao tác kéo chỉ tạo một bước hoàn tác sau
khi thả chuột; Escape/hủy con trỏ bỏ thao tác đang kéo. Nút **Lưu**, **Hoàn tác**,
**Làm lại** có nhãn trên thanh công cụ. Trạng thái **Đã lưu · bản N** xác nhận
backend đã lưu, không phải đã xuất thành MP4.

Lịch sử tối đa 50 bước được lưu trong trình duyệt này sau khi bản dựng được lưu;
bản lớn giữ ít bước hơn để giới hạn dung lượng. Khi mở lại trang, bản dựng cuối
được nạp từ backend và lịch sử chỉ được phục hồi nếu khớp bản đó. Bản đã đổi ở
tab khác sẽ không dùng lịch sử cũ. Xóa dữ liệu trình duyệt hoặc đổi trình duyệt
làm mất lịch sử hoàn tác; bản dựng trên máy vẫn còn. Trình duyệt chặn lưu lịch
sử sẽ có thông báo, không cản lưu bản dựng.

**Xuất thử 12s** dựng tối đa 12 giây đầu. **Xuất bản dựng** dựng toàn bộ bản đã
lưu với tiến trình và nút Dừng; khi xong có xem trước và tải MP4. Job dùng một
bản chụp dữ liệu: sửa tiếp trong lúc xuất không làm thay đổi video đang dựng.
Bản xuất đầy đủ được thêm vào lịch sử các phiên bản và thư viện. Video nguồn,
scene, lời thoại và phụ đề gốc không bị sửa.

```
output/_shared/timeline/<video UUID>/document.json   # bản dựng + revision
output/_shared/timeline/<video UUID>/assets/          # chỉ mục tư liệu
output/_shared/timeline/<video UUID>/visuals/         # sóng âm / dải ảnh
output/_shared/timeline/<video UUID>/proxies/         # bản xem nhẹ
output/_shared/timeline/<video UUID>/previews/        # bản thử
output/_shared/editing/<video UUID>/exports/         # MP4 hoàn chỉnh
```

Tư liệu đã bị xóa/thay đổi được đánh dấu lỗi, không âm thầm thay bằng file khác.
Hai tab sửa cùng video dùng revision để phát hiện xung đột. Nạp lại bản đã lưu
sẽ bỏ các thay đổi chưa lưu của tab hiện tại.

## Âm thanh và khoảng lặng

- Khoảng nghỉ màu vàng được tính từ sóng âm thật. **Khoảng lặng** cho chọn
  ngưỡng dB và độ dài nghỉ tối thiểu; mặc định −40 dB / 0.35 giây. Chọn đoạn
  âm thanh trước để phân tích đoạn đó. Nút khoảng nghỉ có đệm 80 ms hai đầu.
- **Vùng chọn**, hoặc Shift + kéo trên thước: nghe/xem vùng, chỉ giữ vùng,
  cắt vùng và nối mọi lớp, hoặc xuất riêng vùng. Không tự cắt tất cả khoảng
  lặng: nhạc nhỏ cũng có thể được đánh dấu là nghỉ.
- Kéo đường âm lượng, chấm fade vào/ra. Bấm đúp đường để thêm điểm âm lượng,
  kéo điểm lên/xuống; bấm đúp điểm để xóa. Thuộc tính có thanh fade riêng.
- Nút tắt lớp và **S** nghe riêng áp dụng cho cả xem trước và bản xuất. Chọn
  vai trò lớp **Nhạc / Giọng / SFX / Âm gốc**. Ducking giảm các lớp Nhạc khi có
  đoạn Giọng đang nghe được, có thời gian vào/ra 0.2/0.35 giây, mặc định còn 25%.
- Đỉnh sóng gần quá lớn tô đỏ; đồng hồ dB đo bản trộn đang phát. **Phân tích
  phổ tần** hiển thị FFT thật của bản trộn; không phải hiệu ứng minh họa.
  Bản xuất có limiter. Bộ giới hạn của trình duyệt và FFmpeg không hoàn toàn
  giống nhau, nên nghe MP4 xuất thử trước khi chốt bản âm thanh.

## Phụ đề, hình ảnh và kết quả AI

- **+ Phụ đề** tạo khối chữ kéo/cắt được. Bấm chữ trên video hoặc chọn khối,
  chỉnh nội dung, font, cỡ, màu, nền, vị trí và căn chữ. Kéo nút Chọn chữ để
  đặt vị trí. Xem trước và xuất dùng cùng bộ vẽ PNG, hỗ trợ dấu tiếng Việt.
- **+ Hiệu ứng hình** thêm khối làm mờ/phủ màu/delogo/logo hoặc chỉnh màu có
  thời gian riêng. Kéo vùng trực tiếp trên video, chỉnh kích thước ở thuộc tính.
  Delogo và blur ở trình duyệt là xem gần đúng; kiểm tra MP4 xuất thử để xem
  kết quả FFmpeg. Ảnh/logo tải qua nút này được lưu cùng thư viện ảnh hậu kỳ.
- **Công cụ AI** mở các công cụ nhận dạng, dịch và tạo giọng hiện có. Lưu kịch
  bản/tạo giọng xong, trở lại bàn dựng và **Nạp phụ đề / giọng / hiệu ứng AI**.
  Các mốc nguồn được ánh xạ qua điểm cắt và tốc độ của từng đoạn hình.
- Câu có giọng chưa hợp lệ hoặc dài hơn khung được báo để sửa trước khi nạp.
  Nạp lại cùng kết quả không nhân đôi; bản dịch/giọng mới cập nhật khối đã nạp.
  Mẫu chữ chỉnh trên timeline được giữ khi thay nội dung bản dịch.
- Phụ đề/giọng/hiệu ứng nạp từ AI được gắn với đoạn hình. Kéo, đổi thứ tự, cắt,
  nhân đôi hoặc đổi tốc độ đưa các lớp liên quan đi cùng. Có thể Tách khỏi đoạn
  hình để chỉnh độc lập. Câu quá dài có dấu **!** trên timeline.
- Tắt lớp Phụ đề để xuất chỉ có giọng; tắt lớp Giọng để chỉ có chữ.

## Bản xem nhẹ và chuyển sang hậu kỳ

**Tạo bản xem nhẹ** lưu proxy H.264 tối đa cạnh 720 px theo dấu vân tay nguồn;
video đang chọn từ ba phút trở lên được chuẩn bị tự động sau sóng âm. Proxy chỉ
dùng xem hình, giữ thời gian; bản xuất dùng file nguồn. Sóng âm được tính một
lần và chỉ vẽ các pixel đang nhìn thấy. Audio được trộn từng nhóm 12 nguồn,
stem chỉ dài bằng khoảng có nội dung, giảm file PCM tạm cho kịch bản nhiều câu.

**Lịch sử bản xuất** lưu bản đầy đủ và bản vùng chọn, có xem/tải từng phiên bản.
Tiến trình và nút Dừng dùng chung với xuất thử và tạo proxy. Bản đang xuất dùng
snapshot đã lưu, vẫn có thể sửa tiếp bản nháp.

**Dùng bản dựng này cho hậu kỳ** tạo một video nhập mới trong cùng dự án từ
bản xuất toàn bộ, rồi chọn video đó trong Công cụ AI & hậu kỳ. Không cần tải
xuống/nhập lại; bấm lại cùng phiên bản dùng lại video đã tạo. Các công cụ AI
trên video mới dùng đúng bản dựng đã cắt. Đối với video gốc, công cụ AI vẫn
nhận dạng/dịch theo nguồn gốc; xuất bản dựng trước nếu muốn phân tích bản cắt.
Giọng/chữ đã burn vào MP4 không còn là lớp riêng trên video nhập mới.

## Phạm vi hiện tại

### Chữ & tiêu đề

- Mở **Chữ & tiêu đề** ở thanh trên hoặc thư viện bên trái. Chọn **Thêm chữ tự do**
  hay một trong bốn mẫu: **Tên phim**, **Tác giả**, **Giới thiệu**, **Quảng cáo**.
- Đoạn chữ được thêm tại vạch phát trên lớp riêng `text`. Nhập nội dung ở bảng
  bên phải; Enter xuống dòng. Có 4 font kèm hình xem thử, màu, viền, nền và căn chữ.
- Kéo trực tiếp chữ trên video để đặt vị trí; kéo góc dưới bên phải để đổi cỡ.
  Trong lúc kéo chỉ biến đổi hình xem trước, thả chuột mới lưu một thao tác hoàn tác.
- Kéo đoạn trên timeline để đổi lúc xuất hiện, kéo hai mép để đổi thời lượng.
  Nếu nằm trọn trong đoạn hình khi thêm, chữ đi theo đoạn hình đó; có thể chọn
  **Tách khỏi đoạn hình**. Có thể thêm nhiều đoạn chữ cùng lúc, tắt lớp hoặc xóa hết.
- Tiêu đề được lưu cùng bản dựng, độc lập với phụ đề AI. Bản xem trước và bản
  xuất dùng chung bộ vẽ font tiếng Việt. Hiện là chữ tĩnh; chưa có hoạt ảnh chữ.
- Lịch sử giữ tối đa 50 thao tác trong phiên. Sau khi mở lại, số thao tác còn
  giữ được phụ thuộc giới hạn 750.000 ký tự lưu trong trình duyệt; ưu tiên thao tác gần nhất.

Kiểm tra mới: `test_timeline_titles.py` đối chiếu ảnh chữ với MP4 thật, thời gian,
tắt lớp, font và đồng bộ phụ đề không thay tiêu đề. `textTitles.test.ts` kiểm tra
di chuyển, đổi cỡ, chia đoạn, lớp độc lập, khóa, lịch sử và mẫu cho khung dọc.

Một lớp hình ảnh, các lớp âm thanh, phụ đề, chữ và hiệu ứng; tối đa 12 lớp / 5000 đoạn /
2000 đoạn tiếng / 32 hiệu ứng hình / 60 phút.
Hình không chồng lên nhau; khoảng trống là hình đen. Tư liệu khác tỷ lệ được
đặt vừa trong khung, có viền đen như phần xem trước. Khung đầu tiên lấy từ video
nguồn, tối đa 8.3 triệu pixel. Mốc cắt được căn theo fps chung của bản dựng.

Mốc đoạn và lớp được lưu chung, có liên kết theo đoạn hình. Một lớp hình vẫn
không hỗ trợ video chồng hình hoặc chuyển cảnh hòa trộn. Chỉnh tỷ lệ/khung đầu
ra nâng cao vẫn ở Công cụ AI & hậu kỳ. Đã kiểm tra bản xuất thật trên fixture
ngắn; chưa coi đây là xác nhận chạy ổn toàn bộ phim 60 phút trên mọi cấu hình.

Thiết kế học từ OpenCut Classic: bản dựng độc lập với giao diện, thao tác kéo
có trạng thái xem thử riêng, lệnh có hoàn tác và sóng âm dùng chung giữa các
đoạn tách. Không cài hoặc chép nguyên ứng dụng Next.js/Rust của repo đó.

## Kiểm tra

Backend: `tests/unit/test_timeline.py` kiểm tra MP4 thật sau đổi thứ tự, khoảng
trống, mute/gain/SFX, tỷ lệ khác nhau, cache sóng âm, nguồn bị thay, cô lập dự án,
snapshot xuất và hủy tiến trình. Frontend: `dashboard/tests/timeline.test.ts`
kiểm tra split/trim/link/ripple/snap/reorder và khóa lớp.

`test_timeline_layers.py`: fade, tốc độ, solo, ducking, vùng chọn, caption PNG,
hiệu ứng theo thời gian, logo/blur, nhiều stem, proxy, ánh xạ phụ đề và handoff
giữ nguồn/idempotency. `timelineLayers.test.ts`: các lớp theo đoạn hình sau
split/trim/reorder/ripple/speed/nhân đôi; ánh xạ khoảng lặng và gain.
