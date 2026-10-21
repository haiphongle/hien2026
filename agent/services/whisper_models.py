"""Pinned, verified Whisper weights. Downloads use the shared cancellable jobs."""
import asyncio
import hashlib
import shutil
import uuid

import anyio
import httpx

from agent.services import media_jobs as jobs
from agent.services.speech_store import atomic_json, read_json
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services import model_storage

# Public LFS sizes/SHA256 from ggerganov/whisper.cpp; never follow a moving revision.
REVISION = '5359861c739e955e79d9a303bcbc70fb988958b1'
FILES = {
    'base': (147951465, '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe'),
    'small': (487601967, '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b'),
    'large-v3-turbo-q5_0': (574041195, '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2'),
}
lock = asyncio.Lock()


def job_key(model):
    if model not in FILES:
        raise ValueError('Model Whisper không hợp lệ.')
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'frameweaver:whisper-model:{model}'))


def snapshot(path):
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def status(model, folder):
    path = folder / f'ggml-{model}.bin'
    installed = path.is_file()
    checked = read_json(jobs.directory(job_key(model))/'verification.json', {})
    matches = installed and checked.get('file') == snapshot(path)
    verified = bool(matches and checked.get('valid'))
    try:
        model_storage.assert_available(f'whisper-{model}', folder)
        storage_error = None
    except ValueError as exc:
        storage_error = str(exc)
    return {'ready': installed and (not matches or verified) and not storage_error, 'installed': installed, 'verified': verified,
            'storage_error': storage_error,
            'size_bytes': FILES[model][0], 'job': jobs.latest(job_key(model))}


def start(model, folder, *, verify=False):
    model_storage.assert_available(f'whisper-{model}', folder)
    key = job_key(model)
    active = jobs.latest(key)
    if active and active['status'] in jobs.ACTIVE:
        return active  # Repeated clicks never start a second download.
    target = folder / f'ggml-{model}.bin'
    if verify and not target.is_file():
        raise ValueError('Model chưa được tải. Bấm Tải model trước.')
    expected_size, expected_hash = FILES[model]
    verification = jobs.directory(key)/'verification.json'

    async def run(update):
        update(stage='Chờ lượt kiểm tra' if verify else 'Chờ lượt tải model')
        async with lock:
            folder.mkdir(parents=True, exist_ok=True)
            if verify:
                before = snapshot(target)
                digest, total = hashlib.sha256(), 0
                async with await anyio.open_file(target, 'rb') as source:
                    while block := await source.read(1024*1024):
                        digest.update(block); total += len(block)
                        update(stage='Kiểm tra file model', progress=min(99, round(100*total/max(1,before[0]))))
                if snapshot(target) != before:
                    raise ValueError('File model thay đổi trong lúc kiểm tra. Kiểm tra lại.')
                valid = total == expected_size and digest.hexdigest() == expected_hash
                atomic_json(verification, {'file':before, 'valid':valid})
                if not valid:
                    raise ValueError('File model thiếu hoặc sai SHA256. Bấm Tải lại để thay bằng file chính thức.')
                update(stage='File model hợp lệ', verified=True)
                return
            partial = folder/f'.ggml-{model}.part'
            try:
                partial.unlink(missing_ok=True)
                if shutil.disk_usage(folder).free < expected_size + 64*1024**2:
                    raise ValueError(f'Cần ít nhất {(expected_size+64*1024**2)/1024**2:.0f} MB trống để tải model. Dọn ổ đĩa rồi thử lại.')
                url = f'https://huggingface.co/ggerganov/whisper.cpp/resolve/{REVISION}/ggml-{model}.bin'
                total, previous, digest = 0, -1, hashlib.sha256()
                update(stage='Kết nối kho model chính thức', progress=0, bytes_total=expected_size, bytes_downloaded=0)
                async with httpx.AsyncClient(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(60, connect=15)) as client:
                    async with client.stream('GET', url) as response:
                        response.raise_for_status()
                        async with await anyio.open_file(partial, 'wb') as output:
                            async for block in response.aiter_bytes(1024*1024):
                                total += len(block)
                                if total > expected_size:
                                    raise ValueError('Kho model trả file khác dung lượng dự kiến. Chưa áp dụng file tải về.')
                                digest.update(block)
                                await output.write(block)
                                progress = min(98, round(98*total/expected_size))
                                if progress != previous:
                                    update(stage='Đang tải model', progress=progress, bytes_downloaded=total)
                                    previous = progress
                if total != expected_size or digest.hexdigest() != expected_hash:
                    raise ValueError('File tải chưa đủ hoặc sai SHA256. Bấm Tải lại; model đang có được giữ nguyên.')
                update(stage='Chờ lưu model đã xác minh', progress=99)
                # An ASR process may still be reading the old weights (especially on Windows).
                async with TTS_SEMAPHORE:
                    partial.replace(target)
                    atomic_json(verification, {'file':snapshot(target), 'valid':True})
                update(stage='Model đã tải và xác minh', verified=True)
            except httpx.HTTPError as exc:
                raise ValueError('Không tải được model từ Hugging Face. Kiểm tra mạng rồi bấm Tải lại.') from exc
            except OSError as exc:
                raise ValueError('Không lưu được model. Kiểm tra dung lượng và quyền ghi thư mục rồi thử lại.') from exc
            finally:
                partial.unlink(missing_ok=True)
    return jobs.start(key, 'whisper_model', run, metadata={'model':model, 'action':'verify' if verify else 'download'})
