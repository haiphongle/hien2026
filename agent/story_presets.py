"""Style recipes from the SRT reference UI, isolated from existing project presets."""

ANATOMY = {
    'stick': 'Strict stick figures: large pure white circular heads, tiny dot eyes, minimal mouths and torsos, thin single-line arms and legs. Clothing uses simple flat shapes. Preserve readable age and identity through silhouettes; no filled human limbs or realistic anatomy.',
    'human': 'Full-body 2D cartoon humans with complete torsos, shoulders, arms, hands, legs and feet. Simplified natural proportions, expressive illustrated faces, coherent hair and clothing, organic connected contours. Never use stick figures or puppet joints.',
    'semi': 'Semi-realistic 2D illustrated humans with natural proportions, complete anatomy and expressive drawn faces. Stylized non-photographic appearance; consistent hair and clothing. No stick figures, chibi or 3D.',
    'chibi': 'Chibi 2D characters with oversized heads, compact complete bodies, short arms and legs, cute expressive eyes and consistent clothing. No stick-line limbs or realistic adult proportions.',
}
RENDERING = {
    'minimal': 'Minimalist 2D illustration, simplified geometry, limited palette, clean uniform outlines and no unnecessary detail.',
    'flat': 'Flat vector 2D illustration, crisp edges, solid fills, uniform line weight, minimal shading and no realistic texture.',
    'animation': 'Polished 2D cartoon animation, expressive organic acting, coherent cel shading and readable silhouettes.',
    'hand': 'Hand-drawn 2D illustration, organic varied linework, softly colored surfaces and traditional drawn animation feeling.',
    'cinematic': 'Cinematic 2D film illustration, intentional composition, atmospheric lighting, layered depth and expressive acting.',
    'folk': 'Vietnamese folk-inspired 2D illustration, traditional decorative motifs, warm earthy pigments and graphic stylization.',
}
BACKGROUNDS = {
    'white': 'Pure white background (#FFFFFF), no room, landscape, horizon, gradient or decoration. Dark readable outlines. Only essential foreground action props.',
    'black': 'Pure black background (#000000), no room, landscape, horizon, gradient or decoration. High-contrast readable contours and visible clothing. Only essential foreground action props.',
    'scene': 'Coherent illustrated story environment with appropriate architecture, furniture, weather and meaningful objects. Maintain location, palette and prop continuity across shots.',
}
COSTUMES = {
    'simple': 'Simple flat clothing with limited colors and minimal accessories.',
    'modern': 'Modern everyday clothing appropriate to each story character.',
    'vietnamese': 'Vietnamese everyday clothing appropriate to the story era and location.',
    'traditional': 'Traditional Vietnamese period clothing; no modern accessories unless specified.',
    'custom': 'Follow the exact approved Character Bible clothing and accessories.',
}

# New IDs keep saved projects and their reference caches unchanged.
_RECIPES = (
    ('stick_white', 'Stickman – Nền trắng', 'Đầu tròn trắng, tay chân nét đen, nền trắng.', 'stick', 'white', 'minimal', 'simple', '#ffffff', '#222222'),
    ('stick_black', 'Stickman – Nền đen', 'Đầu tròn trắng, tay chân nét trắng sáng.', 'stick', 'black', 'minimal', 'simple', '#000000', '#ffffff'),
    ('human_white', 'Nhân vật 2D – Nền trắng', 'Cơ thể hoạt hình đầy đủ, trang phục hiện đại.', 'human', 'white', 'flat', 'modern', '#ffffff', '#232323'),
    ('human_black', 'Nhân vật 2D – Nền đen', 'Nhân vật màu rõ nét trên nền đen.', 'human', 'black', 'flat', 'modern', '#000000', '#ffffff'),
    ('dressed', 'Nhân vật 2D có trang phục', 'Quần áo, tóc và phụ kiện nhất quán; đời thường Việt Nam.', 'human', 'scene', 'flat', 'vietnamese', '#c9a578', '#2d251e'),
    ('animation', 'Hoạt hình 2D', 'Nhân vật và bối cảnh hoạt hình đầy đủ.', 'human', 'scene', 'animation', 'modern', '#7bb6c6', '#173248'),
    ('cinematic', '2D Cinematic', 'Nhân vật bán hiện thực, bố cục và ánh sáng điện ảnh.', 'semi', 'scene', 'cinematic', 'vietnamese', '#765e75', '#f2d3a3'),
    ('chibi', 'Chibi Cartoon', 'Đầu lớn, thân nhỏ, biểu cảm đáng yêu.', 'chibi', 'scene', 'animation', 'modern', '#e9b8d6', '#623e60'),
    ('hand_drawn', 'Hand-drawn 2D', 'Nét vẽ tay mềm, màu thủ công.', 'human', 'scene', 'hand', 'simple', '#d3c39d', '#665342'),
    ('folk', 'Truyện dân gian Việt Nam', 'Hoa văn dân gian, kiến trúc và trang phục cổ.', 'human', 'scene', 'folk', 'traditional', '#b88d56', '#593a20'),
    ('moral', 'Truyện nhân quả 2D', 'Nhân vật giàu cảm xúc, kể chuyện điện ảnh.', 'semi', 'scene', 'cinematic', 'vietnamese', '#af855a', '#34271f'),
    ('documentary', 'Documentary 2D', 'Minh họa tối giản, thông tin rõ ràng.', 'human', 'white', 'minimal', 'simple', '#e6e6e6', '#333333'),
)

HTML_PRESETS = tuple({
    'id': f'srt_{key}', 'name': name, 'description': description, 'collection': 'html',
    'shape': 'stick' if anatomy == 'stick' else 'illustrated', 'local_actor': False,
    'anatomy': anatomy, 'background_type': background, 'rendering': rendering, 'costume': costume,
    'background': swatch, 'ink': ink,
    'character_design': ANATOMY[anatomy],
    'material_override': {
        'style_instruction': ' '.join((ANATOMY[anatomy], RENDERING[rendering], BACKGROUNDS[background])),
        'scene_prefix': ' '.join((ANATOMY[anatomy], RENDERING[rendering], BACKGROUNDS[background])),
        'negative_prompt': 'No identity changes, gender swaps, disconnected limbs, photorealism, 3D, lettering, captions, logos or watermarks. '
                           + ('No filled human limbs or chibi bodies.' if anatomy == 'stick' else 'No stick figures or stick-line limbs.'),
    },
} for key, name, description, anatomy, background, rendering, costume, swatch, ink in _RECIPES)
