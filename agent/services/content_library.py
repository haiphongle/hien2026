"""Immutable source files and timed scripts; ASR shares the existing job runner."""
import asyncio
import hashlib
import json
import math
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import anyio

from agent.config import SHARED_OUTPUT_DIR
from agent.media_limits import MAX_DURATION, MAX_UPLOAD_BYTES
from agent.models.speech import SpeechDocument
from agent.services import media_jobs as jobs, speech_store as store, transcription

ROOT = SHARED_OUTPUT_DIR / 'content_library'
lock = asyncio.Lock()
_transcript_models = {}
EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.mp4', '.mov', '.mkv', '.webm', '.m4v', '.srt'}


def directory(ident):
    return ROOT / store.identifier(ident)


def read(ident):
    item = store.read_json(directory(ident) / 'item.json')
    if not item:
        raise FileNotFoundError('Không tìm thấy nguồn trong thư viện.')
    return item


def write(item):
    store.atomic_json(directory(item['id']) / 'item.json', item)
    return item


def transcript(ident):
    result = store.read_json(jobs.directory(ident) / 'transcript.json')
    return SpeechDocument.model_validate(result['document']) if result else None


def transcript_model(ident, revision):
    # Keep only the latest tiny metadata per source; no decoded cues on each poll.
    cached = _transcript_models.get(ident)
    if not cached or cached[0] != revision:
        result = store.read_json(jobs.directory(ident) / 'transcript.json', {})
        model = result.get('model')
        cached = (revision, (model, result.get('engine', 'Whisper') if model else None))
        _transcript_models[ident] = cached
    return cached[1]


def summary(item):
    ident = item['id']
    job = jobs.latest(ident)
    result_path = jobs.directory(ident) / 'transcript.json'
    revision = result_path.stat().st_mtime_ns if result_path.is_file() else None
    model, engine = transcript_model(ident, revision) if revision else (None, None)
    if job and (job['status'] in jobs.ACTIVE or not model):
        model, engine = job.get('model'), job.get('engine')
    return {**item, 'status': job['status'] if job else 'READY' if result_path.is_file() else 'NEW',
            'job': job, 'has_script': revision is not None, 'model': model, 'engine': engine,
            'script_revision': revision,
            'source_url': f'/api/content/{ident}/source' if item['kind'] != 'srt' else None,
            'srt_url': f'/api/content/{ident}/subtitles.srt'}


def entries():
    return sorted((summary(item) for p in ROOT.glob('*/item.json') if (item := store.read_json(p))),
                  key=lambda item: item['created_at'], reverse=True)


def parts(ident):
    item = read(ident)
    document = transcript(ident)
    if not document:
        return []
    # Original SRT bytes also keep visual guidance for older library entries.
    # Downloads/transcripts stay plain SRT; only the Studio/planning input adds it.
    notes = ''
    original = directory(ident) / 'source.srt'
    if item['kind'] == 'srt' and original.is_file():
        from agent.services.stickman import srt_input
        _, notes = srt_input(original.read_text(encoding='utf-8-sig'))
        notes = '\n' + notes if notes else ''
    groups, current = [], []
    for cue in document.cues:
        proposed = current + [cue]
        if len(store.srt_text([cue])) + len(notes) > 12000:
            raise ValueError('Một câu SRT và gợi ý hình ảnh vượt 12.000 ký tự. Rút ngắn câu hoặc phần gợi ý trước khi sản xuất.')
        if current and (len(proposed) > 200 or len(store.srt_text(proposed)) + len(notes) > 12000):
            groups.append(current)
            current = []
        current.append(cue)
    if current:
        groups.append(current)
    result = []
    for index, group in enumerate(groups):
        offset = group[0].start
        rebased = [c.model_copy(update={'start': round(c.start-offset, 3), 'end': round(c.end-offset, 3)}) for c in group]
        result.append({'id': f'{ident}:{index}', 'source_id': ident, 'index': index,
            'title': item['title'] + (f' · Phần {index+1}/{len(groups)}' if len(groups) > 1 else ''),
            'start': offset, 'end': group[-1].end, 'cues': len(group), 'srt': store.srt_text(rebased) + notes})
    return result


def require_space(required):
    if shutil.disk_usage(ROOT).free < required + 256 * 1024**2:
        raise ValueError('Ổ đĩa không đủ chỗ cho file và 256 MB dự phòng. Dọn ổ đĩa rồi tải lại.')


async def upload(request, filename):
    name = Path(filename.replace('\\', '/')).name
    suffix = Path(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise ValueError('Chọn video, MP3, WAV, M4A, AAC, OGG, FLAC hoặc SRT.')
    limit = 1024**2 if suffix == '.srt' else MAX_UPLOAD_BYTES
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        raise ValueError('File vượt giới hạn tải lên (SRT 1 MB; video/audio 20 GB).')
    ROOT.mkdir(parents=True, exist_ok=True)
    require_space(int(declared) if declared.isdigit() else 0)
    with tempfile.TemporaryDirectory(prefix='upload-', dir=ROOT) as temp:
        source = Path(temp) / f'source{suffix}'
        total, next_check, digest = 0, 0, hashlib.sha256()
        async with await anyio.open_file(source, 'wb') as target:
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    raise ValueError('File vượt giới hạn tải lên.')
                if total >= next_check:
                    require_space(len(chunk))
                    next_check = total + 64 * 1024**2
                digest.update(chunk)
                await target.write(chunk)
        if not total:
            raise ValueError('File trống.')
        document = None
        if suffix == '.srt':
            from agent.services.stickman import srt_input
            text, _ = srt_input(source.read_text(encoding='utf-8-sig'))
            document = SpeechDocument(mode='srt', cues=store.parse_srt(text))
            if not document.cues:
                raise ValueError('File SRT chưa có câu nào.')
            info = {'duration': document.cues[-1].end, 'kind': 'srt'}
        else:
            raw = await jobs.process(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-format_whitelist', 'mov,matroska,webm,mp3,wav,flac,ogg,aac', '-show_entries',
                'format=duration:stream=codec_type', '-of', 'json', str(source)], timeout=30)
            probe = json.loads(raw)
            duration = float(probe.get('format', {}).get('duration', 0))
            streams = {s.get('codec_type') for s in probe.get('streams', [])}
            if 'audio' not in streams:
                raise ValueError('File không có tiếng để nhận dạng. Chọn file có lời nói hoặc nhập SRT.')
            info = {'duration': duration, 'kind': 'video' if 'video' in streams else 'audio'}
        if not math.isfinite(info['duration']) or not 0 < info['duration'] <= MAX_DURATION:
            raise ValueError('Nguồn cần dài từ trên 0 đến 180 phút.')
        async with lock:
            for p in ROOT.glob('*/item.json'):
                old = store.read_json(p)
                if old and old['sha256'] == digest.hexdigest() and not old.get('trashed_at'):
                    return summary(old)
            ident = str(uuid.uuid4())
            item = {'id': ident, 'title': Path(name).stem[:160] or 'Nguồn mới', 'filename': name,
                    'suffix': suffix, 'size_bytes': total, 'sha256': digest.hexdigest(),
                    'group': '', 'created_at': time.time(), 'trashed_at': None, **info}
            folder = directory(ident)
            folder.mkdir(parents=True)
            try:
                source.replace(folder / f'source{suffix}')
                if document:
                    store.atomic_json(jobs.directory(ident) / 'transcript.json', {'document': document.model_dump(mode='json'), 'kind': 'transcript'})
                write(item)
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                shutil.rmtree(jobs.directory(ident), ignore_errors=True)
                raise
            return summary(item)


async def change(ident, changes):
    async with lock:
        item = read(ident)
        job = jobs.latest(ident)
        if changes.get('trashed') and job and job['status'] in jobs.ACTIVE:
            raise ValueError('Dừng nhận dạng trước khi đưa nguồn vào Thùng rác.')
        if 'trashed' in changes:
            item['trashed_at'] = time.time() if changes.pop('trashed') else None
        for key, value in changes.items():
            item[key] = value.strip()
        return summary(write(item))


async def enqueue(ids, model, language, restart=False):
    if not transcription.binary() or model not in transcription.MODELS or not next(m['ready'] for m in transcription.models() if m['id'] == model):
        raise ValueError('Chưa có Whisper hoặc model trên máy. Chuẩn bị model trong Cài đặt/nhận dạng trước.')
    if language not in transcription.LANGUAGES:
        raise ValueError('Ngôn ngữ không hợp lệ.')
    async with lock:
        items = [read(ident) for ident in ids]
        if any(item.get('trashed_at') for item in items):
            raise ValueError('Khôi phục nguồn khỏi Thùng rác trước khi nhận dạng.')
        if any(not (directory(i['id']) / f"source{i['suffix']}").is_file() for i in items):
            raise ValueError('File nguồn bị thiếu. Tải lại nguồn trước khi nhận dạng.')
        for item in items:
            ident = item['id']
            job = jobs.latest(ident)
            if item['kind'] == 'srt' or job and job['status'] in jobs.ACTIVE:
                continue
            if not restart and (not job or job['status'] == 'COMPLETED') and transcript(ident):
                continue
            transcription.start(ident, model, language, SpeechDocument(timeline=item['sha256']), [], restart=restart,
                source_info={'path': str(directory(ident) / f"source{item['suffix']}"), 'duration': item['duration']})
        return [summary(i) for i in items]
