# Studio style preview assets

## Thay ảnh minh họa của 12 mẫu HTML

Vào **Studio → Video người que → Mẫu từ HTML → Ảnh & prompt mẫu** trên thẻ cần sửa.

1. Bấm **Sao chép prompt mẫu**, tạo ảnh bên ngoài và duyệt nét vẽ, tạo hình, nền.
2. Bấm **Tải ảnh từ máy** hoặc dán URL ảnh trực tiếp rồi **Lưu ảnh từ URL**.
3. Ảnh được lưu tại `output/_shared/story_style_previews/<preset-id>/preview.webp`, dùng chung giữa các dự án trên máy. Mỗi mẫu chỉ giữ ảnh hiện tại; không tạo nhiều bản sau mỗi lần thay. Metadata ở `preview.json`.
4. **Bỏ ảnh thay thế** quay về hình minh họa mặc định.

Ảnh minh họa không được đưa vào AI và không thay đổi prompt, nhân vật, cache hoặc video đã có. Prompt mẫu lấy trực tiếp từ `agent/story_presets.py`; khung 3:2 dành riêng cho thẻ, không đổi khung video.

Nếu muốn AI học nét vẽ từ ảnh, bấm **Tạo mẫu tham chiếu từ ảnh này → Phân tích ảnh này**. Luồng Thêm mẫu hiện có phân tích, lưu bản nháp, cho chỉnh mô tả, tạo nhân vật/cảnh thử và yêu cầu duyệt trước khi lưu vào **Mẫu của tôi**. Đây là một mẫu riêng; không tự áp dụng cho video đang mở.

Tải ảnh minh họa không cần AI hoặc extension. Phân tích/tạo mẫu tham chiếu cần các kết nối hiện có. PNG/JPEG/WebP tĩnh tối đa 10 MB, 16 megapixel; ảnh lưu tối đa 1280 px cạnh dài, giữ tỷ lệ, không cắt nhân vật. URL dùng lại bộ nhập ảnh công khai có kiểm tra địa chỉ/chuyển hướng và giới hạn dung lượng.

## Bộ ảnh minh họa trước đây

Generated with the built-in imagegen tool on 2026-10-08. These are illustrative style previews, not generated scene outputs. Optimized to 640×426 WebP for the style picker; originals remain in Codex generated_images.

New 2D studio/story profiles borrow the minimalist studio and cinematic environment ideas from the local MIT-licensed stickman-video-director style catalog. The character proportions, clothed designs and preview artwork are new. Existing 3D/anime/hand-painted material profiles are reused.

## Prompt set

### cartoon_light

Asset: dashboard/public/story-styles/cartoon_light.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Expressive black and white 2D hand-drawn cartoon. A kind round-headed full-bodied person walking with relaxed curved arms, bent knees and one hand presenting a tiny green-free monochrome sapling in a pot. Pure white background, bold flowing black contour, white-filled naturally connected body, no visible joints, simple expressive face. All monochrome black and white, organic drawing not cut-out puppet. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### cartoon_dark

Asset: dashboard/public/story-styles/cartoon_dark.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Expressive black and white 2D hand-drawn cartoon. A kind round-headed full-bodied person kneeling to admire a small moonlit plant. Deep pure black background, white-filled smoothly connected body and limbs, clean flowing black internal contours and bright white outer silhouette, face with emotion, natural supple posture. Strict black and white. Organic drawing not cut-out puppet. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### cartoon_chalk

Asset: dashboard/public/story-styles/cartoon_chalk.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Luminous chalk hand-drawn full-bodied 2D cartoon. A kind round-headed person gently setting down a burden bag beside a small tree, charcoal blackboard background, creamy white chalk-filled smoothly connected body and limbs, glowing cyan edges, natural flexible posture, friendly expressive face, poetic calm scene with hand-drawn chalk landscape. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### cartoon_studio

Asset: dashboard/public/story-styles/cartoon_studio.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Modern colorful 2D cel-animation character with soft coherent anatomy and fully dressed body, red plain knit beanie without pompom, mustard yellow t-shirt, navy trousers, white sneakers. Friendly young adult man in relaxed presenting pose with curved arms and weight on one leg. White high-key studio, very subtle gray floor perspective, only two small cyan glass geometric panels. Smooth clean line art, naturally connected shoulders elbows knees, no visible hinges, no stick limbs. Flat 2D colorful editorial animation. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### cartoon_story

Asset: dashboard/public/story-styles/cartoon_story.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Cinematic colorful 2D cel-animation character, red plain knit beanie, mustard yellow t-shirt, navy trousers, white sneakers, soft coherent fully clothed anatomy. Friendly young adult man sitting casually on a park bench and looking toward warm orange sunset, one arm relaxed on bench, naturally bent limbs, gentle expressive face. Lush peaceful garden, rich narrative depth and warm soft film lighting; character and environment both hand-drawn 2D with organic connected contours, no stick limbs or puppet hinges. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### 3d_pixar

Asset: dashboard/public/story-styles/3d_pixar.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Polished 3D family-animation style original fictional friendly young adult man wearing a sage green jacket, cream t-shirt, blue pants and sneakers. Full soft rounded cartoon anatomy, expressive kind eyes, slightly stylized proportions, natural relaxed walking pose carrying a book in a leafy sunny park. Rendered three-dimensional rounded forms, soft global illumination and depth of field, warm cinematic colors, no flat line art. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### anime

Asset: dashboard/public/story-styles/anime.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. High-quality original Japanese anime illustration, full-bodied fully clothed young adult man in cream shirt and light blue jacket, dark trousers, standing in a contemplative three-quarter natural pose beside a riverside cherry tree at sunset. Clean cel-shaded linework, coherent anatomy and supple hands, expressive anime eyes, beautifully detailed pastel sky and flowers, evocative quiet story mood. Not photorealistic, not 3D. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.

### ghibli

Asset: dashboard/public/story-styles/ghibli.webp

Use case: illustration-story. Asset type: landscape style preview for a video creator's style picker. Warm hand-painted storybook 2D watercolor animation scene. Original friendly fully clothed young adult character in a moss green cardigan, cream shirt and brown trousers, gently watering a small flowering plant beside a quaint garden cottage. Rounded soft expressive features, natural connected anatomy and relaxed bent limbs, paper texture, pastel watercolor background, soft sunshine, quiet healing mood. No puppet joints, no thin stick limbs, no 3D. Composition: 3:2 landscape, one main full-body character centered and entirely visible, feet inside frame with generous margin, props unobtrusive, clear readable silhouette at small thumbnail size. A complete attractive finished illustration, not a UI mockup. Absolutely no text, labels, letters, branding, watermark, border, panels or contact sheet.
