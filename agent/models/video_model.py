"""Model keys supported by the existing first-frame video transports."""
from typing import Literal

VideoModel = Literal['veo_3_1_i2v_lite', 'abra_i2v_8s',
                     'veo_3_1_i2v_lite_low_priority', 'veo_3_1_i2v_s_fast_ultra']
VIDEO_MODEL_NAMES = {
    'veo_3_1_i2v_lite': 'Veo 3.1 Lite',
    'abra_i2v_8s': 'Omni 1.1 Flash · 8 giây',
    'veo_3_1_i2v_lite_low_priority': 'Veo 3.1 Lite · ưu tiên thấp',
    'veo_3_1_i2v_s_fast_ultra': 'Veo 3.1 Fast · Ultra',
}
