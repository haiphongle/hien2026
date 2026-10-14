"""Opt-in real local models; reuses installed voice demos, never synthesizes or uploads."""
import json
import os

import numpy as np
import soundfile as sf

import pytest

from agent.services import speech_analysis as analysis, vieneu
from agent.services.speech_analysis_core import assign_turns
from agent.services.speech_analysis_worker import prepare, speakers

pytestmark = pytest.mark.skipif(os.environ.get('FLOWKIT_TEST_SPEECH_MODELS') != '1', reason='Opt-in installed local models')


async def test_vietnamese_two_voice_diarization_and_whisper(tmp_path):
    selected = [next(v for v in vieneu.catalog() if v['name'] == name) for name in ('Thiện Minh', 'Mai Anh')]
    samples = [vieneu.preview_path(v['id']) for v in selected]
    if not all(path.is_file() for path in samples) or not analysis.capabilities()['speakers_ready']:
        pytest.skip('Install the speaker models and the two voice demos first')
    items = [{'id': str(i), 'path': str(samples[i % 2]), 'start': i * 10, 'end': i * 10 + 9,
              'text': vieneu.SAMPLE_TEXT} for i in range(4)]
    wav = tmp_path / 'dialogue.wav'
    prepared = prepare({'items': items, 'directory': str(tmp_path)})
    layout, frames = [], 0
    with sf.SoundFile(wav, 'w', samplerate=16000, channels=1, subtype='PCM_16') as target:
        for item in prepared:
            target.write(np.zeros(16000))
            frames += 16000
            start = frames / 16000
            samples, _ = sf.read(item['path'])
            target.write(samples)
            frames += len(samples)
            layout.append({**item, 'packed_start': start, 'packed_end': frames / 16000})
    turns = speakers({'wav': str(wav), 'segmentation': str(analysis.SEGMENTATION),
                      'embedding': str(analysis.EMBEDDING), 'num_speakers': 2})
    source_cues = [{'id': i['id'], 'start': i['packed_start'], 'end': i['packed_end'], 'text': i['text']} for i in layout]
    mapped = assign_turns(source_cues, turns)
    labels = [cue['speaker'] for cue in mapped['cues']]
    assert labels[0] and labels[1] and labels[0] != labels[1], mapped
    assert labels[0] == labels[2] and labels[1] == labels[3], mapped
    assert not any(cue['needs_review'] for cue in mapped['cues']), mapped
    report = await analysis.recognize(prepared, 'small', 'vi', tmp_path, lambda **changes: None)
    print(json.dumps({'speaker_labels': labels, 'word_differences': [c['difference'] for c in report]}, ensure_ascii=False))
    assert all(c['recognized'] for c in report), report
    assert sum(c['difference'] for c in report) / len(report) < .35, report
