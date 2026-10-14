import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from agent.api import media_workbench as api
from agent.db import crud, schema
from agent.models.speech import Cue, SpeechDocument
from agent.services import media_jobs as jobs, speech_store as store, subtitle_translation as translation


@pytest.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(jobs, 'ROOT', tmp_path / 'media')
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'speech')
    monkeypatch.setattr(translation, '_semaphore', asyncio.Semaphore(1))
    monkeypatch.setattr(translation, 'executable', lambda _: 'fixture-codex')
    role = {'provider': 'codex', 'model': 'fixture-model', 'effort': 'low'}
    monkeypatch.setattr(translation, 'assignment', lambda _: dict(role))
    await schema.init_db()
    p = await crud.create_project(name='Translation tests', material='realistic')

    async def create(count):
        v = await crud.create_video(p['id'], 'Subtitle test')
        s = await crud.create_scene(v['id'], 0, 'Source')
        await crud.update_scene(s['id'], duration=count)
        doc = SpeechDocument(revision=1, mode='srt', timeline=store.timeline_key(await crud.list_scenes(v['id'])),
            cues=[Cue(start=i, end=i + 1, text=f'Hello {i}', speaker=f'Person {i % 2}', speed=1.1) for i in range(count)])
        store.atomic_json(store.video_dir(v['id']) / 'script.json', doc.model_dump(mode='json'))
        return v['id'], doc

    try:
        yield create, role
    finally:
        await jobs.shutdown()
        await schema.close_db()


def input_cues(prompt):
    return json.loads(prompt.rsplit('\n', 1)[-1])['cues']


def valid_reply(prompt):
    return json.dumps({'cues': [{'id': c['id'], 'text': 'VI: ' + c['text']} for c in input_cues(prompt)][::-1]})


async def finish(vid):
    task = jobs._tasks.get(vid)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 10)
    return jobs.latest(vid)


async def run(vid, revision=1, **settings):
    await api.translate(vid, api.TranslateRequest(revision=revision, **settings))
    return await finish(vid)


async def test_malformed_third_group_preserves_40_cues_and_resumes_only_missing(project, monkeypatch):
    create, _ = project
    vid, doc = await create(47)
    original = (store.video_dir(vid) / 'script.json').read_bytes()
    previous = {'document': doc.model_dump(mode='json'), 'original': doc.model_dump(mode='json'), 'language': 'vi'}
    target = jobs.directory(vid) / 'translation.json'
    store.atomic_json(target, previous)
    previous_bytes = target.read_bytes()
    first_ids = {str(c.id) for c in doc.cues[:40]}

    async def malformed(_provider, prompt, **options):
        rows = input_cues(prompt)
        if all(c['id'] in first_ids for c in rows):
            return valid_reply(prompt)
        return json.dumps({'cues': [{'id': str(uuid4()), 'text': 'Bad ID'} for _ in rows]})
    monkeypatch.setattr(translation, 'translate_batch', malformed)
    job = await run(vid)
    assert job['status'] == 'FAILED' and job['progress'] == 81
    progress = (await api.get_workbench(vid))['translation_progress']
    assert progress['completed'] == 40 and progress['total'] == 47 and progress['compatible']
    assert target.read_bytes() == previous_bytes
    assert (store.video_dir(vid) / 'script.json').read_bytes() == original

    requested = []
    async def correct(_provider, prompt, **options):
        requested.extend(options['cue_ids'])
        data = json.loads(prompt.rsplit('\n', 1)[-1])
        assert all(c['translation'].startswith('VI: ') for c in data['preceding_context'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    # Restore from disk with a fresh semaphore, as after a server restart.
    monkeypatch.setattr(translation, '_semaphore', asyncio.Semaphore(1))
    assert (await run(vid))['status'] == 'COMPLETED'
    assert requested == [str(c.id) for c in doc.cues[40:]]
    candidate = store.read_json(target)['document']
    assert [c['text'] for c in candidate['cues']] == ['VI: ' + c.text for c in doc.cues]
    assert [{k: v for k, v in c.items() if k != 'text'} for c in candidate['cues']] == [c.model_dump(mode='json', exclude={'text'}) for c in doc.cues]
    assert (store.video_dir(vid) / 'script.json').read_bytes() == original


async def test_invalid_ids_retry_and_then_split_only_failed_group(project, monkeypatch):
    create, _ = project
    vid, _ = await create(4)
    sizes = []
    async def small(_provider, prompt, **options):
        rows = input_cues(prompt)
        sizes.append(len(rows))
        if len(rows) > 2:
            return json.dumps({'cues': [{'id': 'wrong', 'text': 'Bad'} for _ in rows]})
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', small)
    assert (await run(vid))['status'] == 'COMPLETED'
    assert sizes == [4, 4, 2, 2]
    assert translation.checkpoint_status(vid)['completed'] == 4


async def test_retry_budget_is_bounded_and_successful_subgroups_stay_saved(project, monkeypatch):
    create, _ = project
    vid, _ = await create(20)
    calls = []
    async def single_only(_provider, prompt, **options):
        rows = input_cues(prompt)
        calls.append(len(rows))
        return valid_reply(prompt) if len(rows) == 1 else '{"cues": []}'
    monkeypatch.setattr(translation, 'translate_batch', single_only)
    job = await run(vid)
    assert job['status'] == 'FAILED' and 'giới hạn thử lại' in job['error']
    assert len(calls) == translation.MAX_GROUP_CALLS
    assert translation.checkpoint_status(vid)['completed'] == 2
    assert not (jobs.directory(vid) / 'translation.json').exists()


async def test_cancel_preserves_checkpoint_and_resume_skips_completed_cues(project, monkeypatch):
    create, _ = project
    vid, doc = await create(5)
    monkeypatch.setattr(translation, 'BATCH_SIZE', 2)
    entered = asyncio.Event()
    async def blocking(_provider, prompt, **options):
        if options['cue_ids'][0] == str(doc.cues[0].id):
            return valid_reply(prompt)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(translation, 'translate_batch', blocking)
    await api.translate(vid, api.TranslateRequest(revision=1))
    await asyncio.wait_for(entered.wait(), 3)
    assert (await api.cancel(vid))['status'] == 'CANCELLED'
    assert translation.checkpoint_status(vid)['completed'] == 2
    requested = []
    async def correct(_provider, prompt, **options):
        requested.extend(options['cue_ids'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    assert (await run(vid))['status'] == 'COMPLETED'
    assert requested == [str(c.id) for c in doc.cues[2:]]


@pytest.mark.parametrize('change', ['language', 'synopsis', 'glossary', 'model', 'source', 'restart'])
async def test_checkpoint_invalidates_on_changed_source_or_settings(project, monkeypatch, change):
    create, role = project
    vid, doc = await create(3)
    calls = []
    async def correct(_provider, prompt, **options):
        calls.append(options['cue_ids'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    assert (await run(vid))['status'] == 'COMPLETED'
    calls.clear()
    # Reusing a complete compatible checkpoint makes no new AI call.
    assert (await run(vid))['status'] == 'COMPLETED' and not calls
    options = {}
    if change in {'language', 'synopsis', 'glossary'}:
        options[change] = 'en' if change == 'language' else 'New linguistic context'
    elif change == 'model':
        role['model'] = 'different-model'
        assert not translation.checkpoint_status(vid)['compatible']
    elif change == 'source':
        doc.revision += 1
        doc.cues[0].text = 'Changed source'
        store.atomic_json(store.video_dir(vid) / 'script.json', doc.model_dump(mode='json'))
        assert not translation.checkpoint_status(vid)['compatible']
    elif change == 'restart':
        options['restart'] = True
    assert (await run(vid, revision=doc.revision, **options))['status'] == 'COMPLETED'
    assert calls == [[str(c.id) for c in doc.cues]]


async def test_provider_failure_is_not_retried(project, monkeypatch):
    create, _ = project
    vid, _ = await create(3)
    calls = []
    async def unavailable(*args, **options):
        calls.append(1)
        raise RuntimeError('CLI/account unavailable')
    monkeypatch.setattr(translation, 'translate_batch', unavailable)
    assert (await run(vid))['status'] == 'FAILED'
    assert len(calls) == 1


async def test_timeout_splits_immediately_and_preserves_cue_ids(project, monkeypatch):
    create, _ = project
    vid, doc = await create(6)
    sizes = []
    async def slow(_provider, prompt, **options):
        rows = input_cues(prompt)
        sizes.append(len(rows))
        if len(rows) > 3:
            raise TimeoutError('CLI timed out')
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', slow)
    assert (await run(vid))['status'] == 'COMPLETED'
    assert sizes == [6, 3, 3]
    result = store.read_json(jobs.directory(vid) / 'translation.json')['document']
    assert [c['id'] for c in result['cues']] == [str(c.id) for c in doc.cues]


async def test_timeout_single_cue_keeps_checkpoint_and_resume_skips_success(project, monkeypatch):
    create, _ = project
    vid, doc = await create(4)
    calls = []
    async def slow(_provider, prompt, **options):
        rows = input_cues(prompt)
        calls.append(options['cue_ids'])
        if len(rows) > 2 or str(doc.cues[-1].id) in options['cue_ids']:
            raise TimeoutError('CLI timed out')
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', slow)
    job = await run(vid)
    assert job['status'] == 'FAILED' and 'AI quá thời gian ở câu 4' in job['error']
    assert translation.checkpoint_status(vid)['completed'] == 3
    assert len(calls) == 5 and not (jobs.directory(vid) / 'translation.json').exists()
    calls.clear()
    async def correct(_provider, prompt, **options):
        calls.append(options['cue_ids'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    assert (await run(vid))['status'] == 'COMPLETED'
    assert calls == [[str(doc.cues[-1].id)]]


async def test_shortening_18_cues_uses_small_persisted_groups(project, monkeypatch):
    create, _ = project
    vid, doc = await create(18)
    calls = []
    async def correct(_provider, prompt, **options):
        assert 'ORIGINAL language' in prompt
        calls.append(options['cue_ids'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    selection = {str(c.id): 5 for c in doc.cues}
    process = translation.runner(vid, doc, 'configured', 'original', '', '', selection=selection)
    await process(lambda **changes: None)
    assert [len(c) for c in calls] == [6, 6, 6]
    assert translation.checkpoint_status(vid, 'shortening')['completed'] == 18
    assert store.load_document(vid) == doc
    assert store.read_json(jobs.directory(vid) / 'shortening.json')['original'] == doc.model_dump(mode='json')


async def test_cancel_after_timeout_does_not_start_split_requests(project, monkeypatch):
    create, _ = project
    vid, doc = await create(4)
    cancelled = [False]
    calls = []
    async def slow(*args, **options):
        calls.append(1)
        cancelled[0] = True
        raise TimeoutError('CLI timed out')
    monkeypatch.setattr(translation, 'translate_batch', slow)
    process = translation.runner(vid, doc, 'configured', 'vi', '', '', cancelled=lambda: cancelled[0])
    await process(lambda **changes: None)
    assert calls == [1] and translation.checkpoint_status(vid)['completed'] == 0


async def test_source_changed_while_translating_never_publishes_stale_candidate(project, monkeypatch):
    create, _ = project
    vid, doc = await create(3)
    async def change_source(_provider, prompt, **options):
        changed = doc.model_copy(deep=True)
        changed.revision += 1
        store.atomic_json(store.video_dir(vid) / 'script.json', changed.model_dump(mode='json'))
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', change_source)
    job = await run(vid)
    assert job['status'] == 'FAILED' and 'đã đổi' in job['error']
    assert not (jobs.directory(vid) / 'translation.json').exists()
    assert not translation.checkpoint_status(vid)['compatible']


async def test_failed_checkpoint_write_retains_last_commit(project, monkeypatch):
    create, _ = project
    vid, _ = await create(25)
    async def correct(_provider, prompt, **options):
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    original = translation.atomic_json
    def fail_late(path, data):
        if Path(path).name == 'translation-progress.json' and len(data['rows']) > 20:
            raise OSError('disk full')
        original(path, data)
    monkeypatch.setattr(translation, 'atomic_json', fail_late)
    assert (await run(vid))['status'] == 'FAILED'
    assert translation.checkpoint_status(vid)['completed'] == 20
    monkeypatch.setattr(translation, 'atomic_json', original)
    assert (await run(vid))['status'] == 'COMPLETED'


async def test_structured_output_constrains_ids_and_count(project, monkeypatch):
    _, role = project
    ids = [str(uuid4()), str(uuid4())]
    async def complete(selected, prompt, *, schema, binary):
        rows = schema['properties']['cues']
        assert rows['minItems'] == rows['maxItems'] == 2
        assert rows['items']['properties']['id']['enum'] == ids
        assert rows['items']['properties']['text']['maxLength'] == 1500
        assert selected == role and binary == 'fixture-codex'
        return '{"cues": []}'
    monkeypatch.setattr(translation.ai_tasks, 'complete', complete)
    await translation.translate_batch(role, 'test', cue_ids=ids)


async def test_shorten_selected_preserves_other_cues_and_translation(project, monkeypatch):
    create, _ = project
    vid, doc = await create(5)
    selected = [str(doc.cues[i].id) for i in (1, 3)]
    original = (store.video_dir(vid) / 'script.json').read_bytes()
    previous = {'kind': 'translation', 'document': doc.model_dump(mode='json')}
    store.atomic_json(jobs.directory(vid) / 'translation.json', previous)
    monkeypatch.setattr(api.speech, 'cue_status', lambda _vid, cue: {'duration': 2 if str(cue.id) == selected[0] else None})

    async def shorten_reply(_role, prompt, **options):
        data = json.loads(prompt.rsplit('\n', 1)[-1])
        assert 'ORIGINAL language' in prompt and 'negation, names, numbers' in prompt
        assert options['cue_ids'] == selected
        assert data['cues'][0]['target_characters'] == 3  # 7 chars / 2s in a 1s slot, with margin.
        assert data['cues'][1]['target_characters'] == 22
        assert data['preceding_context'][0]['text'] == doc.cues[0].text
        return json.dumps({'cues': [{'id': cid, 'text': 'Hi'} for cid in reversed(selected)]})
    monkeypatch.setattr(translation, 'translate_batch', shorten_reply)
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected))
    assert (await finish(vid))['status'] == 'COMPLETED'
    state = await api.get_workbench(vid)
    assert state['shortening_progress']['compatible']
    assert state['shortening_progress']['completed'] == 2
    candidate = state['shortening']
    assert candidate['original'] == doc.model_dump(mode='json')
    assert [c['text'] for c in candidate['document']['cues']] == ['Hello 0', 'Hi', 'Hello 2', 'Hi', 'Hello 4']
    assert [{k: v for k, v in c.items() if k != 'text'} for c in candidate['document']['cues']] == [c.model_dump(mode='json', exclude={'text'}) for c in doc.cues]
    assert state['translation'] == previous
    assert (store.video_dir(vid) / 'script.json').read_bytes() == original


async def test_shorten_cancel_resume_and_changed_selection(project, monkeypatch):
    create, _ = project
    vid, doc = await create(5)
    monkeypatch.setattr(translation, 'BATCH_SIZE', 1)
    monkeypatch.setattr(api.speech, 'cue_status', lambda *_: {'duration': None})
    selected = [str(c.id) for c in doc.cues[::2]]
    entered = asyncio.Event()
    async def blocked(_role, prompt, **options):
        if options['cue_ids'] == selected[:1]:
            return valid_reply(prompt)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(translation, 'translate_batch', blocked)
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected))
    await asyncio.wait_for(entered.wait(), 3)
    assert (await api.cancel(vid))['status'] == 'CANCELLED'
    assert translation.checkpoint_status(vid, 'shortening')['completed'] == 1
    calls = []
    async def correct(_role, prompt, **options):
        calls.extend(options['cue_ids'])
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected))
    assert (await finish(vid))['status'] == 'COMPLETED'
    assert calls == selected[1:]
    calls.clear()
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected[:1]))
    assert (await finish(vid))['status'] == 'COMPLETED'
    assert calls == selected[:1]


async def test_shorten_rejects_stale_revision_unknown_and_duplicate_ids(project):
    create, _ = project
    vid, doc = await create(2)
    for revision, ids, status in [(2, [doc.cues[0].id], 409), (1, [uuid4()], 422), (1, [doc.cues[0].id] * 2, 422)]:
        with pytest.raises(HTTPException) as exc:
            await api.shorten(vid, api.ShortenRequest(revision=revision, cue_ids=ids))
        assert exc.value.status_code == status
    assert not jobs.latest(vid)


async def test_shorten_malformed_answer_and_source_change_cannot_overwrite_script(project, monkeypatch):
    create, _ = project
    vid, doc = await create(2)
    monkeypatch.setattr(api.speech, 'cue_status', lambda *_: {'duration': None})
    selected = [str(doc.cues[0].id)]
    original = (store.video_dir(vid) / 'script.json').read_bytes()
    async def wrong(*args, **kwargs):
        return json.dumps({'cues': [{'id': str(doc.cues[1].id), 'text': 'Wrong cue'}]})
    monkeypatch.setattr(translation, 'translate_batch', wrong)
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected))
    assert (await finish(vid))['status'] == 'FAILED'
    assert not (jobs.directory(vid) / 'shortening.json').exists()
    assert (store.video_dir(vid) / 'script.json').read_bytes() == original
    async def changed(_role, prompt, **kwargs):
        candidate = doc.model_copy(deep=True)
        candidate.cues[1].text = 'Another tab edited this cue'
        store.atomic_json(store.video_dir(vid) / 'script.json', candidate.model_dump(mode='json'))
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', changed)
    await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=selected))
    assert (await finish(vid))['status'] == 'FAILED'
    assert not (jobs.directory(vid) / 'shortening.json').exists()
    assert not translation.checkpoint_status(vid, 'shortening')['compatible']


async def test_reusing_completed_shortening_keeps_review_identity(project, monkeypatch):
    create, _ = project
    vid, doc = await create(2)
    monkeypatch.setattr(api.speech, 'cue_status', lambda *_: {'duration': None})
    calls = []
    async def correct(_role, prompt, **options):
        calls.append(1)
        return valid_reply(prompt)
    monkeypatch.setattr(translation, 'translate_batch', correct)
    body = api.ShortenRequest(revision=1, cue_ids=[doc.cues[0].id])
    await api.shorten(vid, body)
    assert (await finish(vid))['status'] == 'COMPLETED'
    result = jobs.directory(vid) / 'shortening.json'
    original = result.read_bytes()
    await api.shorten(vid, body)
    assert (await finish(vid))['status'] == 'COMPLETED'
    assert len(calls) == 1
    assert result.read_bytes() == original  # Keeps the user's review edits bound to the same candidate.

    await api.shorten(vid, body.model_copy(update={'restart': True}))
    assert (await finish(vid))['status'] == 'COMPLETED'
    assert len(calls) == 2
    assert json.loads(result.read_bytes())['id'] != json.loads(original)['id']


@pytest.mark.parametrize('change', ['script', 'timeline', 'speech'])
async def test_shorten_rechecks_after_reading_audio(project, monkeypatch, change):
    create, _ = project
    vid, doc = await create(2)
    monkeypatch.setattr(api.speech, 'cue_status', lambda *_: {'duration': None})
    async def changed_while_probing(fn, *args):
        result = fn(*args)
        if change == 'script':
            changed = doc.model_copy(update={'revision': 2})
            store.atomic_json(store.video_dir(vid) / 'script.json', changed.model_dump(mode='json'))
        elif change == 'timeline':
            scenes = await crud.list_scenes(vid)
            await crud.update_scene(scenes[0]['id'], duration=3)
        else:
            def busy(_vid):
                raise HTTPException(409, 'TTS is running')
            monkeypatch.setattr(api, 'ensure_idle', busy)
        return result
    monkeypatch.setattr(api.anyio.to_thread, 'run_sync', changed_while_probing)
    with pytest.raises(HTTPException) as exc:
        await api.shorten(vid, api.ShortenRequest(revision=1, cue_ids=[doc.cues[0].id]))
    assert exc.value.status_code == 409
    assert not jobs.latest(vid)
