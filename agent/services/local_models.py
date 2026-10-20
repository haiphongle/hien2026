"""Manage registered local weights; never environments or generated media."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid

import anyio
import httpx

from agent.paths import DATA_DIR
from agent.services import media_jobs as jobs, model_storage, speech_voices, transcription, whisper_models
from agent.services.speech_store import atomic_json, read_json
from agent.services.tts_runtime import TTS_SEMAPHORE

lock = asyncio.Lock()
_maintenance = set()
HF = 'https://huggingface.co'
# SDK-compatible revisions; existing installations keep their own saved revision.
REVISIONS = {
    'pnnbao-ump/VieNeu-TTS-v3-Turbo': '61b85e3d937fbbacb387714180e8182823512523',
    'OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX': 'ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae',
    'hexgrad/Kokoro-82M': 'f3ff3571791e39611d31c381e3a41a3af07b4987',
    'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice': '85e237c12c027371202489a0ec509ded67b5e4b5',
}
VIENEU_GRAPHS = ('vieneu_prefill.onnx', 'vieneu_decode_step.onnx', 'vieneu_acoustic_cached.onnx',
    'vieneu_backbone_shared.data', 'vieneu_v3_heads.npz', 'config.json', 'tokenizer.json')
CODEC_FILES = ('moss_audio_tokenizer_decode_full.onnx', 'moss_audio_tokenizer_decode_shared.data',
    'moss_audio_tokenizer_decode_step.onnx', 'codec_browser_onnx_meta.json',
    'moss_audio_tokenizer_encode.onnx', 'moss_audio_tokenizer_encode.data')

# Fixed releases compatible with the existing inference code. SHA256 values pin
# the working checkpoint bytes, including older GitHub releases without digests.
FILE_MODELS = {
    'propainter': [dict(repo='sczhou/ProPainter', revision='v0.1.0', name=name, path=name,
        size=size, hash=digest, source_url='https://github.com/sczhou/ProPainter',
        url=f'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/{name}')
        for name, size, digest in (
            ('ProPainter.pth', 157780510, '12c070c4b48f374c91d8a2a17851140b85c159621080989f9e191bbc18bd6591'),
            ('raft-things.pth', 21108000, 'fcfa4125d6418f4de95d84aec20a3c5f4e205101715a79f193243c186ac9a7e1'),
            ('recurrent_flow_completion.pth', 20348681, '22939a1a7900da878dbe1ccd011d646b1bfb30b8290039d8ff0e0c2fefbfd283'))],
    'diarization': [dict(repo=repo, revision=rev, name=name, path=path, size=size, hash=digest,
        source_url=f'{HF}/{repo}', url=f'{HF}/{repo}/resolve/{rev}/{name}')
        for repo, rev, name, path, size, digest in (
            ('csukuangfj/sherpa-onnx-pyannote-segmentation-3-0', '9403a6902bb58e3d5ae8c7e77c3422de279db2e0',
             'model.onnx', 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx', 5992913,
             '220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079'),
            ('csukuangfj/sherpa-onnx-pyannote-segmentation-3-0', '9403a6902bb58e3d5ae8c7e77c3422de279db2e0',
             'model.int8.onnx', 'sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx', 1540506,
             'd582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d'),
            ('csukuangfj/speaker-embedding-models', '0743f301363dec56491a490f6d6cbc9d67f9a3bf',
             '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx', 'embedding.onnx', 39593761,
             '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b'))],
}


def registry():
    return {**{f'whisper-{key}': {'name': name, 'engine': 'Whisper', 'model': key, 'category': 'Nhận dạng',
        'root': transcription.model_path(key).parent} for key, name in transcription.MODELS.items()},
        **{key: {'name': name, 'engine': key, 'model': service.worker.model, 'category': 'Giọng đọc',
            'root': service.worker.cache_dir, 'worker': service.worker, 'sdk': service.SDK_VERSION}
        for key, name in (('vieneu', 'VieNeu v3 Turbo'), ('kokoro', 'Kokoro 82M'), ('qwen3', 'Qwen3 TTS 0.6B'))
        for service in [speech_voices.engine_service(key)]},
        **{key: {'name':name, 'engine':key, 'model':key, 'category':category,
            'root':model_storage.location(key, default, preserve_existing=True), 'default_root':default,
            'files':FILE_MODELS[key], 'extra_paths':['segmentation.tar.bz2'] if key == 'diarization' else []}
        for key, name, category, default in (
            ('propainter', 'ProPainter · xóa vật thể', 'Xử lý hình ảnh', DATA_DIR/'.flowkit'/'propainter'/'weights'),
            ('diarization', 'Phân biệt người nói · Sherpa ONNX', 'Phân tích âm thanh', DATA_DIR/'.flowkit'/'models'/'diarization'))}}


def config(ident):
    if ident not in registry():
        raise ValueError('Model không có trong danh sách được quản lý.')
    return registry()[ident]


def job_key(ident):
    item = config(ident)
    return whisper_models.job_key(item['model']) if item['engine'] == 'Whisper' else str(
        uuid.uuid5(uuid.NAMESPACE_URL, f'frameweaver:local-model:{ident}'))


def manifest_path(ident):
    return jobs.directory(job_key(ident))/'installation.json'


def receipt(ident, item):
    saved = read_json(manifest_path(ident), {})
    return saved if saved.get('model') == item['model'] else {}


async def disk_operation(function, *args):
    """Cancellation must drain mutations before releasing the model/semaphore lock."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def model_slot(item):
    async with TTS_SEMAPHORE:
        if item['engine'] == 'propainter':
            from agent.services.propainter_trial import LOCK
            async with LOCK:
                yield
        else:
            yield


async def close_worker(item):
    if worker := item.get('worker'):
        await disk_operation(worker.close)
        worker._probe = None


def specifications(item):
    """File sets match the pinned SDKs used by the existing isolated workers."""
    model = item['model']
    if item['engine'] == 'vieneu':
        return [(model, ['config.json', 'speaker_encoder.onnx', 'denoiser.onnx',
            *[f'onnx_update/{name}' for name in VIENEU_GRAPHS]]),
            ('OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX', list(CODEC_FILES))]
    if item['engine'] == 'kokoro':
        return [(model, ['config.json', 'kokoro-v1_0.pth',
            *[f'voices/{preset[0]}.pt' for preset in speech_voices.kokoro.PRESETS]])]
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', model):
        raise ValueError('Model dùng đường dẫn tùy chỉnh. Chỉ quản lý tự động model từ kho Hugging Face.')
    return [(model, None)]


def repo_path(root, repo):
    return root/'huggingface'/'hub'/('models--'+repo.replace('/', '--'))


def revision(root, repo):
    folder = repo_path(root, repo)
    ref = folder/'refs'/'main'
    value = ref.read_text().strip() if ref.is_file() else ''
    if re.fullmatch(r'[a-f0-9]{40}', value):
        return value
    versions = sorted(p.name for p in (folder/'snapshots').glob('*') if re.fullmatch(r'[a-f0-9]{40}', p.name))
    return versions[0] if len(versions) == 1 else None


def paths(item):
    root = item['root']
    if item.get('files'):
        return [root/entry['path'] for entry in item['files']] + [root/name for name in item.get('extra_paths', [])]
    if item['engine'] == 'Whisper':
        return [root/f'ggml-{item["model"]}.bin']
    result = []
    for repo, _ in specifications(item):
        result.append(repo_path(root, repo))
        if item['engine'] == 'vieneu':
            result.append(root/'models'/hashlib.sha256(repo.encode()).hexdigest()[:12])
    return result


def files_in(targets):
    for target in targets:
        if target.is_file() or target.is_symlink():
            yield target
        elif target.is_dir():
            for base, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = [name for name in dirs if not (Path(base)/name).is_symlink()]
                for name in files:
                    yield Path(base)/name


def disk_bytes(targets):
    seen, size = set(), 0
    for path in files_in(targets):
        try:
            info = path.stat()
        except OSError:
            continue
        inode = (info.st_dev, info.st_ino)
        if inode not in seen:
            seen.add(inode)
            size += info.st_size
    return size


def in_use():
    # Includes queued work: deleting between ASR chunks or voice cues is unsafe.
    from agent.api import vieneu as previews
    from agent.services import speech_jobs
    from agent.services.propainter_trial import LOCK
    return TTS_SEMAPHORE.locked() or LOCK.locked() or bool(speech_jobs._tasks) or any(
        not task.done() for task in previews._tasks.values()) or any(
        (read_json(jobs.directory(key)/'job.json', {}).get('kind') in
         {'transcribe', 'content_transcribe', 'stickman', 'analysis_words', 'analysis_speakers',
          'propainter-clips', 'propainter-trial'}) for key in list(jobs._tasks))


def inventory(ident, *, occupied=None):
    item = config(ident)
    root = item['root']
    default_root = item.get('default_root') or (transcription.MODEL_DIR if item['engine'] == 'Whisper' else DATA_DIR/'.flowkit'/f'{item["engine"]}-cache')
    try:
        model_storage.assert_available(ident, root)
        storage_error = None
    except ValueError as exc:
        storage_error = str(exc)
    state = jobs.latest(job_key(ident))
    active = bool(state and state['status'] in jobs.ACTIVE)
    managed = paths(item)
    manifest = receipt(ident, item)
    sources = []
    if item['engine'] == 'Whisper':
        status = whisper_models.status(item['model'], root)
        installed, ready, verified = status['installed'], status['ready'], status['verified']
        expected = status['size_bytes']
        sources = [{'repo': 'ggerganov/whisper.cpp', 'url': f'{HF}/ggerganov/whisper.cpp',
            'revision': whisper_models.REVISION, 'files': [f'ggml-{item["model"]}.bin'],
            'download_url': f'{HF}/ggerganov/whisper.cpp/resolve/{whisper_models.REVISION}/ggml-{item["model"]}.bin'}]
        loaded = False
    elif item.get('files'):
        entries = item['files']
        installed = any((root/entry['path']).is_file() for entry in entries)
        expected = sum(entry['size'] for entry in entries)
        ready = all((root/entry['path']).is_file() and (root/entry['path']).stat().st_size == entry['size'] for entry in entries)
        ready = ready and not any((root/name).exists() for name in ('model-invalid.json', 'model-removed.json'))
        verified = ready and manifest.get('verified', False) and all(
            (root/entry['path']).stat().st_mtime_ns == entry.get('mtime_ns') for entry in manifest.get('files', []))
        loaded = False  # These workers exit at the end of each task.
        for entry in entries:
            source = next((s for s in sources if s['repo'] == entry['repo']), None)
            if source is None:
                source = {'repo':entry['repo'], 'url':entry['source_url'], 'revision':entry['revision'], 'files':[], 'file_urls':{}}
                sources.append(source)
            source['files'].append(entry['name'])
            source['file_urls'][entry['name']] = entry['url']
    else:
        installed = any(path.exists() for path in managed)
        expected = manifest.get('size_bytes')
        intact = bool(manifest.get('files')) and all(
            (root/f['path']).is_file() and (root/f['path']).stat().st_size == f['size'] for f in manifest.get('files', []))
        verified = intact and manifest.get('verified', False) and all(
            (root/f['path']).stat().st_mtime_ns == f.get('mtime_ns') for f in manifest.get('files', []))
        marker = read_json(root/'ready.json', {})
        legacy_complete = all(revision(root, repo) and (all(
            (repo_path(root, repo)/'snapshots'/revision(root, repo)/name).is_file() for name in names)
            if names else any((repo_path(root, repo)/'snapshots'/revision(root, repo)).glob('*.safetensors')))
            for repo, names in specifications(item))
        ready = intact if manifest else bool(legacy_complete) and marker.get('model') == item['model']
        if (root/'model-removed.json').is_file():
            ready = False
        if manifest.get('verified') is False:
            ready = False
        for repo, names in specifications(item):
            recorded = [entry['name'] for entry in manifest.get('files', []) if entry['repo'] == repo]
            rev = revision(root, repo) or manifest.get('source_revisions', {}).get(repo) or REVISIONS.get(repo)
            if not names and rev:
                snapshot = repo_path(root, repo)/'snapshots'/rev
                names = sorted(path.relative_to(snapshot).as_posix() for path in files_in([snapshot])
                    if path.suffix in {'.json', '.safetensors', '.txt'} or 'speech_tokenizer' in path.parts)
            sources.append({'repo': repo, 'url': f'{HF}/{repo}', 'revision': rev,
                'files': recorded or names or ['*.json', '*.safetensors', '*.txt', 'speech_tokenizer/*']})
        worker = item['worker']
        loaded = bool(worker._process and worker._process.poll() is None)
    previous = read_json(model_storage.move_record(ident), {})
    return {'id': ident, 'name': item['name'], 'engine': item['engine'], 'category': item['category'],
        'model': item['model'], 'installed': installed, 'ready': bool(ready and not storage_error), 'verified': bool(verified and not storage_error),
        'size_bytes': disk_bytes(managed), 'expected_bytes': expected, 'path': str(root.absolute()),
        'default_path': str(default_root.absolute()),
        'real_path': str(root.resolve()), 'storage_paths': [str(path.resolve()) for path in managed],
        'sources': sources, 'loaded': loaded, 'in_use': bool(in_use() if occupied is None else occupied), 'job': state,
        'verified_at': manifest.get('verified_at'), 'storage_error':storage_error,
        'previous': previous.get('previous') if previous.get('target') == str(root) else None}


def catalogue():
    occupied = in_use()
    return [inventory(key, occupied=occupied) for key in registry()]


async def metadata(client, item):
    if item.get('files'):
        return [dict(entry) for entry in item['files']]
    result = []
    for repo, names in specifications(item):
        saved = receipt(item['engine'], item)
        rev = revision(item['root'], repo) or saved.get('source_revisions', {}).get(repo) or REVISIONS.get(repo) or 'main'
        response = await client.get(f'{HF}/api/models/{repo}/revision/{rev}', params={'blobs': 'true'})
        response.raise_for_status()
        data = response.json()
        sha = data['sha']
        if not re.fullmatch(r'[a-f0-9]{40}', sha):
            raise ValueError('Kho model không trả revision hợp lệ.')
        siblings = {entry['rfilename']: entry for entry in data['siblings']}
        selected = names if names is not None else [name for name in siblings if
            name.endswith(('.json', '.safetensors', '.txt')) or name.startswith('speech_tokenizer/')]
        for name in selected:
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or name not in siblings:
                raise ValueError('Danh sách file model thiếu hoặc không hợp lệ.')
            entry = siblings[name]
            lfs = entry.get('lfs') or {}
            size = lfs.get('size', entry.get('size'))
            digest = lfs.get('sha256') or entry.get('blobId')
            if not isinstance(size, int) or size < 0 or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', digest or ''):
                raise ValueError('Kho model thiếu dung lượng hoặc mã kiểm tra file.')
            result.append({'repo': repo, 'revision': sha, 'name': name, 'size': size, 'hash': digest,
                'path': str((repo_path(Path(), repo)/'snapshots'/sha/name).as_posix())})
    if not result:
        raise ValueError('Không tìm thấy file model để tải.')
    return result


def hasher(entry):
    if len(entry['hash']) == 64:
        return hashlib.sha256()
    return hashlib.sha1(f'blob {entry["size"]}\0'.encode())


async def check_file(path, entry):
    if not path.is_file() or path.stat().st_size != entry['size']:
        return False
    digest = hasher(entry)
    async with await anyio.open_file(path, 'rb') as source:
        while block := await source.read(1024*1024):
            digest.update(block)
    return digest.hexdigest() == entry['hash']


def safe_path(root, relative):
    path = root/relative
    if not path.resolve().is_relative_to(root.resolve()) or path == root:
        raise ValueError('File model trỏ ra ngoài thư mục cache được quản lý.')
    return path


async def prepare(ident, action, update):
    item = config(ident)
    root = item['root']
    model_storage.assert_available(ident, root)
    async with lock:
        root.mkdir(parents=True, exist_ok=True)
        manifest = receipt(ident, item)
        async with httpx.AsyncClient(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(60, connect=15)) as client:
            entries = manifest.get('files') or await metadata(client, item)
            total = sum(entry['size'] for entry in entries)
            done = 0
            if action == 'download':
                # One file is staged at a time; leave the old snapshot usable on failure.
                needed = sum(entry['size'] for entry in entries if not (root/entry['path']).is_file())
                if shutil.disk_usage(root).free < max(needed, max(e['size'] for e in entries)) + 64*1024**2:
                    raise ValueError('Không đủ dung lượng trống để tải và xác minh model. Dọn ổ đĩa rồi thử lại.')
            for entry in entries:
                target = safe_path(root, entry['path'])
                if action == 'verify':
                    update(stage=f'Kiểm tra {entry["name"]}', progress=round(98*done/max(1,total)))
                    if not await check_file(target, entry):
                        atomic_json(manifest_path(ident), {'model': item['model'], 'files': entries, 'size_bytes': total, 'verified': False})
                        atomic_json(root/'model-invalid.json', {'error': entry['name']})
                        (root/'ready.json').unlink(missing_ok=True)
                        if item.get('worker'):
                            item['worker']._probe = None
                        raise ValueError(f'File thiếu hoặc sai mã kiểm tra: {entry["name"]}. Bấm Tải lại model.')
                    done += entry['size']
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    destination = target.resolve() if target.is_symlink() else target
                    partial = destination.with_name('.'+destination.name+'.frameweaver-part')
                    if partial.is_symlink():
                        raise ValueError('File tải tạm không được là liên kết.')
                    try:
                        count, digest, previous = 0, hasher(entry), -1
                        async with client.stream('GET', entry.get('url') or f'{HF}/{entry["repo"]}/resolve/{entry["revision"]}/{entry["name"]}') as response:
                            response.raise_for_status()
                            async with await anyio.open_file(partial, 'wb') as output:
                                async for block in response.aiter_bytes(1024*1024):
                                    count += len(block)
                                    if count > entry['size']:
                                        raise ValueError('File tải vượt dung lượng chính thức; chưa áp dụng.')
                                    digest.update(block)
                                    await output.write(block)
                                    progress = min(98, round(98*(done+count)/max(1,total)))
                                    if progress != previous:
                                        update(stage=f'Tải {entry["name"]}', progress=progress,
                                            bytes_downloaded=done+count, bytes_total=total)
                                        previous = progress
                        if count != entry['size'] or digest.hexdigest() != entry['hash']:
                            raise ValueError(f'File tải thiếu hoặc sai mã kiểm tra: {entry["name"]}. Model cũ được giữ nguyên.')
                        update(stage='Chờ lưu file model đã xác minh')
                        async with model_slot(item):
                            await close_worker(item)
                            # Same immutable hash: preserve SDK links and deduplicated blobs.
                            partial.replace(destination)
                        done += count
                    finally:
                        partial.unlink(missing_ok=True)
                entry['mtime_ns'] = target.stat().st_mtime_ns
            async with model_slot(item):
                await close_worker(item)
                # VieNeu materializes ONNX hard links; rebuild them from verified bytes.
                if item['engine'] == 'vieneu':
                    for repo, _ in specifications(item):
                        materialized = root/'models'/hashlib.sha256(repo.encode()).hexdigest()[:12]
                        safe_path(root, materialized.relative_to(root))
                        if materialized.is_dir() and not materialized.is_symlink():
                            await disk_operation(shutil.rmtree, materialized)
                for entry in entries if not item.get('files') else []:
                    ref = repo_path(root, entry['repo'])/'refs'/'main'
                    ref.parent.mkdir(parents=True, exist_ok=True)
                    ref.write_text(entry['revision'])
                atomic_json(root/'ready.json', {'model': item['model'], 'sdk': item.get('sdk'), 'ready_at': time.time()})
                atomic_json(manifest_path(ident), {'model': item['model'], 'files': entries, 'size_bytes': total, 'verified': True, 'verified_at': time.time()})
                (root/'model-removed.json').unlink(missing_ok=True)
                (root/'model-invalid.json').unlink(missing_ok=True)
            update(stage='Model đã xác minh', verified=True)


def erase_weights(item):
    """Delete registered weight paths at a specified root, retaining shared blobs."""
    root = item['root']
    managed = paths(item)
    # Cache roots may live on an external disk. Nested links must stay inside it.
    for path in managed:
        safe_path(root, path.relative_to(root))
    hub = root/'huggingface'/'hub'
    blobs = {path.resolve() for path in files_in(managed) if path.is_symlink()
        and path.resolve().is_relative_to(hub.resolve()) and path.resolve().is_file()}
    for path in managed:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # HF versions may share blob files between repositories. Keep those still referenced.
    used = {path.resolve() for path in files_in([hub]) if path.is_symlink()}
    for blob in blobs - used:
        blob.unlink(missing_ok=True)


def remove_weights(ident):
    item = config(ident)
    root = item['root']
    saved = receipt(ident, item)
    sources = {repo: revision(root, repo) or saved.get('source_revisions', {}).get(repo)
        for repo, _ in specifications(item)} if item.get('worker') else {}
    erase_weights(item)
    if item['engine'] != 'Whisper':
        for name in ('ready.json', 'clone_ready.json', 'model-invalid.json'):
            (root/name).unlink(missing_ok=True)
        if item.get('worker'):
            item['worker']._probe = None
        if root.is_dir():
            atomic_json(root/'model-removed.json', {'deleted_at': time.time()})
        # Keep a tiny source receipt so reinstall uses the same immutable revision.
        atomic_json(manifest_path(ident), {**saved, 'model': item['model'], 'source_revisions': sources, 'verified': False})
    else:
        (jobs.directory(job_key(ident))/'verification.json').unlink(missing_ok=True)
        manifest_path(ident).unlink(missing_ok=True)


async def action(ident, operation):
    item = config(ident)
    key = job_key(ident)
    if ident in _maintenance:
        raise ValueError('Model đang được xóa hoặc giải phóng bộ nhớ. Đợi hoàn tất.')
    if operation == 'cancel':
        return await jobs.cancel(key)
    model_storage.assert_available(ident, item['root'])
    current = jobs.latest(key)
    if current and current['status'] in jobs.ACTIVE:
        if current.get('action') == operation:
            return current
        raise ValueError('Model đang được quản lý. Đợi hoàn tất hoặc hủy tác vụ trước.')
    if operation in {'delete', 'unload'}:
        if in_use():
            raise ValueError('Đang có tác vụ dùng model trên máy. Đợi hoàn tất hoặc dừng tác vụ trước.')
        _maintenance.add(ident)
        try:
            async with model_slot(item):
                await close_worker(item)
                if operation == 'delete':
                    await disk_operation(remove_weights, ident)
        finally:
            _maintenance.discard(ident)
        return {'status': 'COMPLETED', 'action': operation}
    if item['engine'] == 'Whisper':
        return whisper_models.start(item['model'], item['root'], verify=operation == 'verify')
    if operation == 'verify' and not inventory(ident)['installed']:
        raise ValueError('Model chưa được tải. Bấm Tải model trước.')
    async def run(update):
        try:
            await prepare(ident, operation, update)
        except httpx.HTTPError as exc:
            raise ValueError('Không tải/kiểm tra được nguồn model chính thức. Kiểm tra mạng rồi thử lại.') from exc
    return jobs.start(key, 'local_model', run, metadata={'model': ident, 'action': operation})


def open_folder(ident):
    path = config(ident)['root'].resolve()
    if not path.is_dir():
        raise ValueError('Thư mục model chưa tồn tại. Tải model trước.')
    command = ['open', str(path)] if sys.platform == 'darwin' else ['explorer', str(path)] if os.name == 'nt' else ['xdg-open', str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
