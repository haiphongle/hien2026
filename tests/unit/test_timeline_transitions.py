"""Real FFmpeg joins, preserved sound/timing and range/PiP boundary regressions."""
from copy import deepcopy
import shutil
import uuid

import pytest
from pydantic import ValidationError

from agent.models.timeline import TimelineDocument
from agent.services import timeline, timeline_layers, timeline_transitions
from tests.unit.test_audio_mix import audio_project
from tests.unit.test_timeline import timeline_context, segments, pixels, rms


def document(state, kind):
    doc = segments(state['document'], [(0, 2, 2), (2, 0, 2)])
    doc['clips'][2]['transition'] = {'kind': kind, 'duration': .4}
    return TimelineDocument.model_validate(doc)


def test_model_rejects_invalid_transitions_and_preserves_old_documents():
    video, audio = str(uuid.uuid4()), str(uuid.uuid4())
    data = dict(tracks=[dict(id=video, name='Hình', kind='video'), dict(id=audio, name='Tiếng', kind='audio')], clips=[])
    doc = TimelineDocument.model_validate(data)
    assert timeline_transitions.joins(doc) == {}
    base = dict(id=str(uuid.uuid4()), track_id=video, asset_id=str(uuid.uuid4()), start=0, duration=2, transition=dict(kind='dissolve', duration=.4))
    TimelineDocument.model_validate({**data, 'clips': [base]})
    for changes in ({'track_id': audio}, {'transition': {'kind': 'invalid', 'duration': .4}}, {'transition': {'kind': 'dissolve', 'duration': float('nan')}}, {'transition': {'kind': 'light', 'duration': 3}}):
        with pytest.raises(ValidationError):
            TimelineDocument.model_validate({**data, 'clips': [{**base, **changes}]})


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('kind', ['dissolve', 'blur', 'black', 'slide', 'light'])
async def test_real_transition_render_has_same_duration_audio_and_final_picture(timeline_context, tmp_path, kind):
    vid, source, client = timeline_context
    state = (await client.get(f'/api/videos/{vid}/timeline')).json()
    doc = document(state, kind)
    before = deepcopy(doc.model_dump())
    target = tmp_path / f'{kind}.mp4'
    result = await timeline.render(vid, doc, target, lambda **_: None)
    assert result['duration'] == pytest.approx(4, abs=.05)
    assert result['has_audio']
    assert doc.model_dump() == before
    assert rms(target, .5) > .01
    assert rms(target, 3.3) == pytest.approx(rms(source, 1.3), abs=.003)  # original source silence
    assert pixels(target, 2.7)[:3] == pytest.approx((255, 255, 255), abs=10)
    rgb = pixels(target, 2.2)[:3]
    if kind in {'dissolve', 'blur'}:
        assert rgb[0] > 220 and 70 < rgb[1] < 200 and 70 < rgb[2] < 200
    elif kind == 'black':
        # Same fadeblack weight used by browser transitionWeights at p=.5.
        assert max(rgb) < 130
    elif kind == 'light':
        assert min(rgb) > 170


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_range_inside_transition_continues_phase_without_restarting(timeline_context, tmp_path):
    vid, _, client = timeline_context
    doc = document((await client.get(f'/api/videos/{vid}/timeline')).json(), 'dissolve')
    full = tmp_path / 'full.mp4'
    await timeline.render(vid, doc, full, lambda **_: None)
    selected = timeline_layers.range_document(doc, 2.2, 3.2)
    target = tmp_path / 'range.mp4'
    result = await timeline.render(vid, selected, target, lambda **_: None, source_document=doc, range_start=2.2)
    assert result['duration'] == pytest.approx(1, abs=.05)
    assert pixels(target, 0)[:3] == pytest.approx(pixels(full, 2.2)[:3], abs=12)
    assert pixels(target, .5)[:3] == pytest.approx((255, 255, 255), abs=10)


async def test_transition_save_reload_and_gap_bypass(timeline_context):
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    doc = document(state, 'dissolve').model_dump(mode='json')
    saved = await client.put(url, json={'revision': state['revision'], 'document': doc})
    assert saved.status_code == 200, saved.text
    reloaded = (await client.get(url)).json()
    assert reloaded['document']['clips'][2]['transition'] == {'kind': 'dissolve', 'duration': .4}
    for c in doc['clips'][2:]:
        c['start'] += .2
    assert timeline_transitions.joins(TimelineDocument.model_validate(doc)) == {}


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_split_layout_starting_inside_join_preserves_its_phase(timeline_context, tmp_path):
    vid, _, client = timeline_context
    doc = document((await client.get(f'/api/videos/{vid}/timeline')).json(), 'dissolve').model_dump(mode='json')
    track = str(uuid.uuid4())
    doc['tracks'].append(dict(id=track, name='Video chèn', kind='overlay'))
    doc['clips'].append({**doc['clips'][0], 'id': str(uuid.uuid4()), 'track_id': track, 'start': 2.1,
        'source_in': 0, 'duration': .8, 'group': None, 'transition': None,
        'overlay': dict(layout='split')})
    target = tmp_path / 'split.mp4'
    result = await timeline.render(vid, TimelineDocument.model_validate(doc), target, lambda **_: None)
    assert result['duration'] == pytest.approx(4, abs=.05)
    rgb = pixels(target, 2.2)[:3]
    assert 60 < rgb[0] < 200 and max(rgb[1:]) < 20  # outgoing red still at half opacity
    assert max(pixels(target, 2.7)[:3]) < 10  # left-pane letterbox after the join
    assert min(pixels(target, 3)[:3]) > 240  # full layout restored
