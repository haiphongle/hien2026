"""Material registry — built-in and custom visual styles for image generation."""
from agent.story_presets import HTML_PRESETS

_BUILTIN_IDS: frozenset[str] = frozenset({
    "realistic", "3d_pixar", "anime", "ghibli", "stop_motion", "minecraft",
    "oil_painting", "watercolor", "comic_book", "cyberpunk", "claymation",
    "lego", "retro_vhs", "stickman_light", "stickman_dark", "stickman_chalk",
    "cartoon_light", "cartoon_dark", "cartoon_chalk", "cartoon_studio", "cartoon_story", "anime_monochrome", "stickman_pencil",
})
_BUILTIN_IDS |= frozenset(p['id'] for p in HTML_PRESETS)

# Studio styles share the material registry; only the original palettes support
# the procedural actor. Generated styles must retain their own character design.
STORY_PRESETS = (
    {'id': 'stickman_light', 'background': '#ffffff', 'ink': '#222222', 'second_ink': '#5279b4', 'shape': 'stick'},
    {'id': 'stickman_dark', 'background': '#121216', 'ink': '#ffffff', 'second_ink': '#f4c26a', 'shape': 'stick'},
    {'id': 'stickman_chalk', 'background': '#24262b', 'ink': '#a6f6ff', 'second_ink': '#f4c26a', 'shape': 'stick'},
    {'id': 'cartoon_light', 'background': '#ffffff', 'ink': '#222222', 'shape': 'solid', 'fill': '#ffffff', 'outline': '#222222',
     'preview': '/story-styles/cartoon_light.webp', 'description': 'Nét đen mềm, thân đầy đặn, biểu cảm rõ trên nền trắng.', 'reference_version': 2},
    {'id': 'cartoon_dark', 'background': '#000000', 'ink': '#ffffff', 'shape': 'solid', 'fill': '#ffffff', 'outline': '#111111',
     'preview': '/story-styles/cartoon_dark.webp', 'description': 'Nhân vật trắng nổi bật trên nền đen, dáng vẽ liền mạch.', 'reference_version': 2},
    {'id': 'cartoon_chalk', 'background': '#24262b', 'ink': '#a6f6ff', 'shape': 'solid', 'fill': '#efffff', 'outline': '#74999f',
     'preview': '/story-styles/cartoon_chalk.webp', 'description': 'Nét phấn phát sáng, nhân vật đầy đặn và không gian trầm lắng.', 'reference_version': 2},
    {'id': 'cartoon_studio', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/cartoon_studio.webp', 'description': '2D có quần áo, dáng mềm tự nhiên trong studio trắng tối giản.',
     'character_design': 'Fully clothed 2D cartoon person, plain red knit beanie without pom-pom, mustard yellow T-shirt, navy trousers and white sneakers.'},
    {'id': 'cartoon_story', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/cartoon_story.webp', 'description': '2D có quần áo, bối cảnh nhiều đồ vật và ánh sáng kể chuyện.',
     'character_design': 'Fully clothed 2D cartoon person, plain red knit beanie without pom-pom, mustard yellow T-shirt, navy trousers and white sneakers.'},
    {'id': '3d_pixar', 'name': 'Hoạt hình 3D', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/3d_pixar.webp', 'description': 'Nhân vật tròn mềm, quần áo đầy đủ, chiều sâu và ánh sáng 3D.',
     'character_design': 'Original fully clothed 3D cartoon person, sage green jacket, cream T-shirt, blue trousers and sneakers, soft rounded proportions.'},
    {'id': 'anime', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/anime.webp', 'description': 'Nhân vật anime có quần áo, nét thanh và màu cel-shading.',
     'character_design': 'Original fully clothed anime person, light blue jacket, cream shirt and dark trousers, expressive anime eyes and coherent stylized anatomy.'},
    {'id': 'anime_monochrome', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/anime_monochrome.webp', 'description': 'Manga/webtoon đen trắng, bối cảnh có đồ vật, bóng đổ và ánh sáng dịu.',
     'character_design': 'Original fully clothed monochrome anime adult, expressive eyes, simple everyday clothes and coherent drawn anatomy.'},
    {'id': 'stickman_pencil', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/stickman_pencil.svg', 'description': 'Người que trong tranh chì: nhà cửa, cây cối, đồ vật và chiều sâu trên giấy ngà.',
     'character_design': 'Simple expressive stick figure, circular head, small hair tuft, dot eyes, thin curved line arms and legs, consistent hand-drawn proportions.'},
    {'id': 'ghibli', 'name': 'Tranh vẽ chữa lành', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/ghibli.webp', 'description': 'Nét vẽ tay, màu nước dịu, nhân vật và cảnh vật gần gũi.',
     'character_design': 'Original fully clothed hand-painted cartoon person, moss green cardigan, cream shirt and brown trousers, rounded gentle features.'},
    {'id': 'comic_book', 'name': 'Truyện tranh màu retro', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/comic_book.svg', 'description': 'Nét mực rõ, màu retro, chấm in và bối cảnh đời thường nhiều đồ vật.',
     'material_override': {'style_instruction': 'Retro everyday-life color comic illustration, coherent fully clothed expressive characters, bold hand-inked contours, restrained teal, rust and cream palette, subtle halftone shading and printed paper texture. Detailed domestic settings and meaningful objects, warm illustrated lighting.',
                           'scene_prefix': 'Retro color comic illustration, bold ink contours, teal, rust and cream palette, subtle printed halftone texture, detailed everyday setting and meaningful props.',
                           'negative_prompt': 'No superhero costumes, photorealism, 3D, thin-line stick figures, disconnected limbs, lettering, captions, speech bubbles, logos or watermarks.'},
     'character_design': 'Original fully clothed retro comic adult, teal cardigan, cream shirt and rust-colored trousers, expressive face, coherent hand-drawn anatomy, bold ink contours.'},
    {'id': 'claymation', 'name': 'Hoạt hình đất sét', 'shape': 'illustrated', 'local_actor': False,
     'preview': '/story-styles/claymation.svg', 'description': 'Nhân vật tròn mềm bằng đất sét, dấu nặn thủ công và thế giới tí hon ấm áp.',
     'material_override': {'style_instruction': 'Original handcrafted modeling-clay animation aesthetic, softly rounded expressive characters with coherent sculpted anatomy and complete clothing. Subtle fingerprint textures, imperfect handmade edges, miniature practical sets with detailed everyday props, warm soft studio lighting.',
                           'scene_prefix': 'Handcrafted clay animation illustration, rounded sculpted forms, fingerprint texture, miniature handmade set, warm practical lighting and meaningful props.',
                           'negative_prompt': 'No photorealistic people, polished plastic, glossy digital humanoids, flat line figures, disconnected body parts, lettering, captions, logos or watermarks.'},
     'character_design': 'Original fully clothed modeling-clay character, rounded sculpted anatomy, sage green sweater, cream trousers, small visible handmade textures, friendly expressive face.'},
)
STORY_PRESETS = HTML_PRESETS + STORY_PRESETS


def story_category(style_id):
    if style_id.startswith('srt_'):
        preset = next(p for p in HTML_PRESETS if p['id'] == style_id)
        return 'stick' if preset['anatomy'] == 'stick' else 'drawing' if preset['rendering'] in {'hand', 'folk'} else '2d'
    if style_id.startswith('stickman_') and style_id != 'stickman_pencil':
        return 'stick'
    if style_id in {'3d_pixar', 'claymation'}:
        return '3d'
    return 'drawing' if style_id in {'ghibli', 'stickman_pencil'} else '2d'


def story_material(style_id):
    material = get_material(style_id)
    preset = next((p for p in STORY_PRESETS if p['id'] == style_id), {})
    return {**(material or {}), **preset.get('material_override', {})} or None

MATERIALS: dict[str, dict] = {
    "realistic": {
        "id": "realistic",
        "name": "Photorealistic",
        "style_instruction": (
            "Photorealistic RAW photograph, shot on Canon EOS R5, 35mm lens, "
            "natural available light, real footage."
        ),
        "negative_prompt": (
            "NOT 3D render, NOT CGI, NOT digital art, NOT illustration, "
            "NOT anime, NOT painting, NOT cartoon."
        ),
        "scene_prefix": (
            "Real RAW photograph, shot on Canon EOS R5, 35mm lens, "
            "natural available light."
        ),
        "lighting": "Studio lighting, highly detailed",
    },
    "3d_pixar": {
        "id": "3d_pixar",
        "name": "3D Pixar",
        "style_instruction": (
            "3D animated style, Pixar-quality rendering, Disney-Pixar aesthetic. "
            "Smooth subsurface scattering skin, expressive cartoon eyes, "
            "stylized proportions, vibrant saturated colors."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT photograph, NOT live action, NOT anime, "
            "NOT flat 2D."
        ),
        "scene_prefix": (
            "3D animated Pixar-quality rendering, vibrant colors, "
            "cinematic lighting."
        ),
        "lighting": "Studio lighting, global illumination, highly detailed",
    },
    "anime": {
        "id": "anime",
        "name": "Anime",
        "style_instruction": (
            "Japanese anime style, cel-shaded rendering, vibrant saturated colors, "
            "clean sharp linework, large expressive eyes, stylized anatomy. "
            "High-quality anime production, studio Ghibli meets modern anime aesthetic."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT 3D render, NOT oil painting, "
            "NOT sketch, NOT watercolor, NOT Western cartoon."
        ),
        "scene_prefix": (
            "Anime style, cel-shaded, vibrant colors, clean linework, "
            "dramatic anime lighting."
        ),
        "lighting": "Anime-style dramatic lighting, highly detailed",
    },
    "stop_motion": {
        "id": "stop_motion",
        "name": "Felt & Wood Stop Motion",
        "style_instruction": (
            "Stop-motion animation style with handcrafted felt and wood puppets. "
            "Visible felt fabric texture, wooden joints and dowels, "
            "miniature handmade set pieces, warm craft workshop lighting. "
            "Laika Studios / Wes Anderson stop-motion aesthetic."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT 3D render, NOT digital, NOT anime, "
            "NOT smooth surfaces, NOT plastic."
        ),
        "scene_prefix": (
            "Stop-motion style, handcrafted felt and wood puppets, "
            "miniature set, warm workshop lighting."
        ),
        "lighting": "Warm practical miniature lighting, macro photography detail",
    },
    "minecraft": {
        "id": "minecraft",
        "name": "Minecraft",
        "style_instruction": (
            "Minecraft voxel art style, blocky cubic geometry, pixel textures, "
            "16x16 texture resolution aesthetic, square heads and bodies. "
            "Everything made of cubes and rectangular prisms. "
            "Minecraft game screenshot aesthetic."
        ),
        "negative_prompt": (
            "NOT smooth, NOT round, NOT photorealistic, NOT anime, "
            "NOT organic curves, NOT high-poly."
        ),
        "scene_prefix": (
            "Minecraft style, blocky voxel world, pixel textures, "
            "cubic geometry, game screenshot aesthetic."
        ),
        "lighting": "Minecraft-style ambient lighting, block shadows",
    },
    "oil_painting": {
        "id": "oil_painting",
        "name": "Oil Painting",
        "style_instruction": (
            "Classical oil painting on canvas, visible thick brushstrokes, "
            "rich impasto texture, warm color palette, chiaroscuro lighting. "
            "Renaissance masters meets impressionist technique. "
            "Museum-quality fine art painting."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT digital art, NOT 3D render, NOT anime, "
            "NOT flat colors, NOT cartoon."
        ),
        "scene_prefix": (
            "Oil painting style, visible brushstrokes, rich impasto texture, "
            "warm palette, dramatic chiaroscuro lighting."
        ),
        "lighting": "Dramatic chiaroscuro lighting, rich tonal depth",
    },
    "ghibli": {
        "id": "ghibli",
        "name": "Studio Ghibli",
        "style_instruction": (
            "Studio Ghibli anime style, hand-painted watercolor backgrounds, "
            "soft pastel colors, gentle rounded character designs, whimsical atmosphere. "
            "Hayao Miyazaki aesthetic, detailed natural environments, magical realism."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT 3D render, NOT dark, NOT gritty, "
            "NOT sharp edges, NOT Western cartoon."
        ),
        "scene_prefix": (
            "Studio Ghibli anime style, hand-painted watercolor backgrounds, "
            "soft pastel colors, gentle whimsical atmosphere."
        ),
        "lighting": "Soft natural Ghibli lighting, golden hour warmth, dappled sunlight",
    },
    "watercolor": {
        "id": "watercolor",
        "name": "Watercolor",
        "style_instruction": (
            "Soft watercolor painting on cold-press paper, loose wet brushwork, "
            "translucent color washes bleeding into each other, white paper showing through. "
            "Delicate ink outlines, impressionistic and dreamy."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT 3D render, NOT digital art, NOT anime, "
            "NOT sharp edges, NOT bold outlines."
        ),
        "scene_prefix": (
            "Watercolor painting style, soft wet brushwork, "
            "translucent color washes, delicate ink outlines."
        ),
        "lighting": "Soft diffused natural light, watercolor wash",
    },
    "comic_book": {
        "id": "comic_book",
        "name": "Comic Book",
        "style_instruction": (
            "American comic book art style, bold black ink outlines, flat vibrant colors "
            "with halftone dot shading, dynamic action poses, dramatic foreshortening. "
            "Marvel/DC superhero comic aesthetic, Ben-Day dots, speech bubble ready."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT 3D render, NOT anime, NOT watercolor, "
            "NOT soft edges, NOT muted colors."
        ),
        "scene_prefix": (
            "Comic book style, bold ink outlines, vibrant flat colors, "
            "halftone shading, dynamic composition."
        ),
        "lighting": "High contrast comic lighting, dramatic shadows, rim light",
    },
    "cyberpunk": {
        "id": "cyberpunk",
        "name": "Cyberpunk",
        "style_instruction": (
            "Cyberpunk sci-fi aesthetic, neon-lit dark urban environment, "
            "holographic displays, rain-slicked streets reflecting neon signs. "
            "Blade Runner meets Ghost in the Shell, high-tech low-life, "
            "chrome and glass, purple and cyan color palette."
        ),
        "negative_prompt": (
            "NOT natural environment, NOT bright daylight, NOT historical, "
            "NOT cartoon, NOT fantasy medieval."
        ),
        "scene_prefix": (
            "Cyberpunk aesthetic, neon-lit dark urban, holographic displays, "
            "rain-slicked streets, purple and cyan neon."
        ),
        "lighting": "Neon rim lighting, volumetric fog, cyan and magenta",
    },
    "claymation": {
        "id": "claymation",
        "name": "Claymation",
        "style_instruction": (
            "Clay animation style, characters made of modeling clay with visible "
            "fingerprint textures, slightly imperfect sculpted features. "
            "Wallace & Gromit / Aardman aesthetic, miniature handmade sets, "
            "warm practical lighting on tiny clay world."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT digital, NOT anime, NOT smooth skin, "
            "NOT 3D render, NOT glass or metal surfaces."
        ),
        "scene_prefix": (
            "Claymation style, clay puppet characters with fingerprint textures, "
            "miniature handmade sets, warm practical lighting."
        ),
        "lighting": "Warm miniature set lighting, soft shadows, macro detail",
    },
    "lego": {
        "id": "lego",
        "name": "LEGO",
        "style_instruction": (
            "LEGO brick style, characters are LEGO minifigures with yellow skin "
            "and claw hands, environments built entirely from LEGO bricks and plates. "
            "Visible brick studs, ABS plastic texture, The LEGO Movie aesthetic."
        ),
        "negative_prompt": (
            "NOT photorealistic, NOT organic, NOT smooth, NOT anime, "
            "NOT round shapes, NOT natural materials."
        ),
        "scene_prefix": (
            "LEGO style, minifigure characters, brick-built environments, "
            "visible studs, plastic ABS texture."
        ),
        "lighting": "Bright toy photography lighting, sharp focus, product shot quality",
    },
    "retro_vhs": {
        "id": "retro_vhs",
        "name": "Retro VHS",
        "style_instruction": (
            "1980s VHS tape aesthetic, analog video noise and scan lines, "
            "slightly washed-out warm colors, CRT TV curvature, tracking artifacts. "
            "Retro camcorder footage feel, date stamp overlay, nostalgic grain."
        ),
        "negative_prompt": (
            "NOT modern, NOT 4K, NOT clean, NOT digital, NOT anime, "
            "NOT sharp, NOT high-definition."
        ),
        "scene_prefix": (
            "Retro VHS style, analog scan lines, warm washed-out colors, "
            "CRT curvature, nostalgic 80s grain."
        ),
        "lighting": "Warm tungsten lighting, CRT glow, analog video bloom",
    },
    "stickman_light": {
        "id": "stickman_light", "name": "Người que · Nền trắng",
        "style_instruction": "Minimal flat black line illustration on a clean white background, simple geometric props, spacious composition.",
        "scene_prefix": "Minimal black line illustration on white, simple props, spacious composition.",
        "negative_prompt": "No photographs, realistic anatomy, 3D, lettering, captions, logos or watermarks.",
        "lighting": "Flat monochrome, no photographic lighting",
    },
    "stickman_dark": {
        "id": "stickman_dark", "name": "Người que · Nền đen",
        "style_instruction": "Minimal flat white line illustration on a deep black background, simple geometric props, spacious composition.",
        "scene_prefix": "Minimal white line illustration on black, simple props, spacious composition.",
        "negative_prompt": "No photographs, realistic anatomy, 3D, lettering, captions, logos or watermarks.",
        "lighting": "Flat monochrome, no photographic lighting",
    },
    "stickman_chalk": {
        "id": "stickman_chalk", "name": "Người que · Phấn sáng",
        "style_instruction": "Hand-drawn luminous chalk illustration on a dark charcoal blackboard, subtle cyan glow, sketchy chalk props, quiet atmospheric composition.",
        "scene_prefix": "Luminous chalk illustration on charcoal blackboard, subtle cyan glow, sketchy props.",
        "negative_prompt": "No photographs, realistic anatomy, 3D, lettering, captions, logos or watermarks.",
        "lighting": "Gentle chalk glow, dark charcoal backdrop",
    },
    "cartoon_studio": {
        "id": "cartoon_studio", "name": "2D có trang phục · Studio",
        "style_instruction": "Modern full-bodied 2D cel animation, soft coherent anatomy, smoothly connected torso and limbs, organic continuous contours, naturally curved elbows and knees, expressive simple faces, fully clothed characters. Bright white studio with subtle light-gray floor perspective and a few cyan glass icon-only props, spacious composition.",
        "scene_prefix": "Full-bodied colorful 2D cel animation, flowing organic contours, bright minimalist white studio, subtle gray floor perspective, a few cyan glass icon-only props.",
        "negative_prompt": "No thin-line stick figures, disjointed body parts, visible hinges, cut-out puppets, stiff straight limbs, circuit board textures, sci-fi wall panels, spaceship corridors, grunge, photorealistic skin, 3D, lettering, speech bubbles, logos or watermarks.",
        "lighting": "Soft high-key illustrated studio lighting",
    },
    "cartoon_story": {
        "id": "cartoon_story", "name": "2D có trang phục · Kể chuyện",
        "style_instruction": "Cinematic full-bodied 2D cel animation, fully clothed characters, soft coherent anatomy, smoothly connected torso and limbs, organic continuous contours, naturally curved elbows and knees, expressive simple faces. Rich full-color illustrated environments, meaningful props, warm cinematic light, atmospheric narrative depth; characters remain clean hand-drawn 2D.",
        "scene_prefix": "Full-bodied colorful hand-drawn 2D animation in a rich illustrated narrative environment, meaningful props, warm cinematic lighting and depth.",
        "negative_prompt": "No thin-line stick figures, disjointed body parts, visible hinges, cut-out puppets, stiff straight limbs, photorealistic skin, 3D humanoid CGI, chaotic line glitches, lettering, speech bubbles, logos or watermarks.",
        "lighting": "Warm cinematic illustrated light, soft atmospheric depth",
    },
    "anime_monochrome": {
        "id": "anime_monochrome", "name": "Anime · Đen trắng",
        "style_instruction": "2D monochrome anime/webtoon, black and white manga line art, coherent expressive anatomy, detailed domestic environments with meaningful objects, high contrast ink and soft illustrated interior lighting.",
        "scene_prefix": "2D monochrome anime/webtoon, black and white manga line art, detailed domestic environments, meaningful objects, high contrast ink, soft interior lighting.",
        "negative_prompt": "No color, photographs, 3D, rigid cut-out puppets, disjointed limbs, lettering, captions, speech bubbles, logos or watermarks.",
        "lighting": "Soft illustrated interior lighting, monochrome shadows",
    },
    "stickman_pencil": {
        "id": "stickman_pencil", "name": "Người que · Tranh chì kể chuyện",
        "style_instruction": "Cinematic hand-drawn pencil illustration on warm ivory textured paper, soft graphite hatching, simple expressive stick figures within richly detailed landscapes, houses, paths, trees and meaningful objects, layered atmospheric depth.",
        "scene_prefix": "Cinematic pencil illustration on warm ivory paper, soft graphite hatching, richly detailed environment with meaningful objects, layered depth and gentle light.",
        "negative_prompt": "No photographs, 3D, neon glow, blank studio backgrounds, lettering, captions, logos or watermarks.",
        "lighting": "Gentle graphite shading on warm ivory paper",
    },
}

# Keep the same body design in references and scenes; only the surface changes.
for _preset in STORY_PRESETS:
    if _preset['shape'] != 'solid':
        continue
    _surface = {
        'cartoon_light': ('Nền trắng', 'white filled figures with bold black contours on a clean white background'),
        'cartoon_dark': ('Nền đen', 'white filled figures with black internal contours on a pure black background'),
        'cartoon_chalk': ('Phấn sáng', 'white filled chalk figures with subtle cyan luminous contours on a dark charcoal blackboard'),
    }[_preset['id']]
    _instruction = ('Minimal expressive 2D monochrome cartoon, ' + _surface[1] +
        '. Large round heads, simple eyes and eyebrows, rounded solid torsos, substantial filled arms and legs, '
        'simple hands and feet, flat shapes, clear gestures, simple geometric props, spacious composition. '
        'Continuous organic contours, smoothly connected shoulders and limbs, natural bent elbows and knees, '
        'soft weight-bearing poses rather than assembled rigid body parts.')
    MATERIALS[_preset['id']] = {
        'id': _preset['id'], 'name': 'Nhân vật 2D · ' + _surface[0],
        'style_instruction': _instruction,
        'scene_prefix': 'Minimal expressive 2D monochrome illustration, ' + _surface[1] + ', simple props, spacious composition.',
        'negative_prompt': 'No thin-line stick figures, disjointed body parts, visible hinges, cut-out puppets, stiff straight limbs, realistic anatomy, photographs, 3D, colored clothing, lettering, captions, logos or watermarks.',
        'lighting': 'Flat monochrome illustration' if _preset['id'] != 'cartoon_chalk' else 'Subtle luminous chalk edges',
    }


for _preset in HTML_PRESETS:
    MATERIALS[_preset['id']] = {'id': _preset['id'], 'name': _preset['name'], **_preset['material_override']}


def get_material(material_id: str) -> dict | None:
    """Get built-in or custom material by ID."""
    return MATERIALS.get(material_id)


def list_materials() -> list[dict]:
    """List all available materials (built-in + custom)."""
    return list(MATERIALS.values())


def register_material(material: dict) -> None:
    """Register a custom material at runtime."""
    if material["id"] in _BUILTIN_IDS:
        raise ValueError(f"Cannot override built-in material '{material['id']}'")
    MATERIALS[material["id"]] = material
