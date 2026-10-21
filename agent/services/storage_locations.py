"""Relocate data folders through stable links; keep runtime installations fixed."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

from agent.paths import DATA_DIR, RESOURCE_DIR, DESKTOP

CONFIG = DATA_DIR/'.flowkit'/'storage-locations.json'
JOB = DATA_DIR/'.flowkit'/'storage-location-job.json'
_task = None
_cancel = threading.Event()
writers = 0
_sizes = {}
_size_lock = threading.Lock()


def anchors():
    return {'output':Path(os.environ.get('FRAMEWEAVER_OUTPUT_DIR', DATA_DIR/'output')).expanduser().absolute(),
        'build-cache':RESOURCE_DIR/'desktop'/'.cache', 'build-output':RESOURCE_DIR/'desktop'/'build',
        'release':RESOURCE_DIR/'release'}


def read(path, fallback):
    return json.loads(path.read_text()) if path.is_file() else fallback


def save(path, data):
    from agent.services.speech_store import atomic_json
    atomic_json(path, data)


def settings():
    data = read(CONFIG, {'locations':{}})
    if not isinstance(data, dict) or not isinstance(data.get('locations'), dict):
        raise ValueError('Không đọc được cấu hình nơi lưu dữ liệu.')
    for ident, entry in data['locations'].items():
        if ident not in anchors() or not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and Path(entry[key]).is_absolute() for key in ('base','target')) or not isinstance(entry.get('token'), str):
            raise ValueError('Cấu hình nơi lưu dữ liệu không hợp lệ; chưa đổi thư mục.')
    return data


def busy():
    return _task is not None and not _task.done()


def offline(ident):
    root = anchors()[ident]
    entry = settings()['locations'].get(ident)
    if entry:
        base = Path(entry['base'])
        if read(base/'.frameweaver-storage.json', {}).get('token') != entry['token']:
            return f'Không tìm thấy nơi lưu {root.name}: {base}. Kết nối lại ổ chứa dữ liệu.'
        if not root.is_symlink() or root.resolve() != Path(entry['target']) or not root.is_dir():
            return f'Liên kết nơi lưu không còn đúng: {root}. Kiểm tra thư mục trước khi tiếp tục.'
    if root.is_symlink() and not root.exists():
        return f'Không tìm thấy ổ chứa dữ liệu: {root.resolve()}.'
    return None


def request_error(method, path):
    if not path.startswith('/api/') or path.startswith('/api/storage-locations'):
        return None
    if busy() and method not in {'GET', 'HEAD', 'OPTIONS'}:
        return 'Đang chuyển nơi lưu. Đợi hoàn tất hoặc hủy trước khi chỉnh sửa/tạo mới.'
    if path.startswith(('/api/videos/', '/api/content', '/api/gallery', '/api/music', '/api/story-backgrounds')) or method not in {'GET', 'HEAD', 'OPTIONS'}:
        return offline('output')
    return None


def snapshot(root):
    """Do not follow nested links, including the build's Applications shortcut."""
    result = []
    if not root.exists():
        return result
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs+files):
            path = Path(base)/name
            if path == root/'.frameweaver-copy.json':
                continue
            info = path.lstat()
            kind = 'link' if path.is_symlink() else 'dir' if path.is_dir() else 'file'
            if kind == 'file' and not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Không chuyển file đặc biệt đang mở: {path.name}.')
            result.append({'path':path.relative_to(root).as_posix(), 'kind':kind,
                'size':info.st_size if kind == 'file' else 0,
                'mtime':info.st_mtime_ns if kind == 'file' else 0,
                'mode':stat.S_IMODE(info.st_mode), 'link':os.readlink(path) if kind == 'link' else None})
    return sorted(result, key=lambda item:item['path'])


def signature(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def safe_source(source):
    if source.is_mount() or source == Path.home() or any(root.resolve().is_relative_to(source) for root in (DATA_DIR, RESOURCE_DIR)):
        raise ValueError('Không chuyển/xóa gốc ổ đĩa, thư mục người dùng hoặc toàn bộ source.')


def disk_size(root):
    from agent.services.local_models import disk_bytes
    key = str(root.resolve())
    with _size_lock:
        cached = _sizes.get(key)
        if not cached or time.monotonic()-cached[0] > 120:
            cached = (time.monotonic(), disk_bytes([root.resolve()]))
            _sizes[key] = cached
        return cached[1]


def inventory(selected_group='data'):
    from agent import config
    items = []
    for ident, name, group, note in (
        ('output', 'Kho tư liệu & dữ liệu hậu kỳ', 'data', 'Gồm video nguồn, ảnh, lời đọc, phụ đề, nhạc, timeline, bản xuất và cache xem trước.'),
        ('build-cache', 'Cache đóng gói macOS', 'build', 'Tệp tải và bộ công cụ dùng để build.'),
        ('build-output', 'Ứng dụng macOS đã build', 'build', 'Ứng dụng .app và ZIP; giữ cấu trúc liên kết và quyền thực thi.'),
        ('release', 'Bộ cài phát hành', 'build', 'DMG, checksum và các tệp phát hành.')):
        root = anchors()[ident]
        entry = settings()['locations'].get(ident, {})
        items.append(dict(id=ident, name=name, group=group, note=note, path=str(root), real_path=str(root.resolve()),
            size_bytes=disk_size(root) if group == selected_group else None, can_move=not (DESKTOP and group == 'build'),
            reason='Build được quản lý trong bản source, bên ngoài ứng dụng đã đóng gói.' if DESKTOP and group == 'build' else '',
            error=offline(ident), previous=entry.get('previous')))
    for ident, name, root, reason in (
        ('backend-env', 'Python chính của ứng dụng', Path(sys.prefix), 'Đường dẫn khởi động backend cố định; không chuyển bằng sao chép thư mục.'),
        ('kokoro-env', 'Môi trường Kokoro', Path(config.KOKORO_PYTHON).parent.parent, 'Cần cài lại tại đích và kiểm tra thư viện native trước khi đổi môi trường.'),
        ('vieneu-env', 'Môi trường VieNeu', Path(config.VIENEU_PYTHON).parent.parent, 'Cần cài lại tại đích và kiểm tra thư viện native trước khi đổi môi trường.'),
        ('qwen3-env', 'Môi trường Qwen3', Path(config.QWEN3_PYTHON).parent.parent, 'Môi trường có đường dẫn cài đặt riêng; vị trí thực tế có thể ở ổ ngoài.'),
        ('propainter-env', 'Môi trường ProPainter', DATA_DIR/'.venvs'/'propainter', 'Đang liên kết thư viện với Python chính; giữ vị trí để tránh hỏng xử lý.'),
        ('muse-runtime', 'Muse gateway & phiên đăng nhập', DATA_DIR/'.flowkit'/'muse-gateway', 'Gồm môi trường và hồ sơ trình duyệt đang dùng; giữ vị trí để bảo toàn phiên kết nối.'),
        ('frontend-env', 'Thư viện phát triển giao diện', RESOURCE_DIR/'dashboard'/'node_modules', 'Thư viện phát triển gắn với source; không phải trọng số model.')):
        items.append(dict(id=ident, name=name, group='environment', note=reason, reason=reason,
            path=str(root.absolute()), real_path=str(root.resolve()), size_bytes=disk_size(root) if selected_group == 'environment' else None,
            can_move=False, error=None, previous=None))
    items.append(dict(id='database', name='Database dự án', group='data',
        note='Database nhỏ, giữ cố định cùng cấu hình ứng dụng.', reason='Giữ vị trí cố định để bảo toàn liên kết dự án.',
        path=str(config.DB_PATH), real_path=str(config.DB_PATH.resolve()), size_bytes=config.DB_PATH.stat().st_size if config.DB_PATH.is_file() else 0,
        can_move=False, error=None, previous=None))
    state = read(JOB, None)
    if state and state['status'] in {'QUEUED','PROCESSING'} and not busy():
        state = {**state, 'status':'FAILED', 'error':'Tác vụ bị gián đoạn khi ứng dụng dừng; vị trí đã lưu được kiểm tra lại khi khởi động.'}
    return {'items':items, 'job':state, 'busy':busy()}


def preview(ident, directory):
    from agent.services.model_storage import folder
    if ident not in anchors() or DESKTOP and ident != 'output':
        raise ValueError('Mục này có vị trí cố định, không hỗ trợ chuyển.')
    if error := offline(ident):
        raise ValueError(error)
    if settings()['locations'].get(ident, {}).get('previous'):
        raise ValueError('Còn bản cũ dự phòng. Kiểm tra và xử lý bản cũ trước khi chuyển tiếp.')
    anchor = anchors()[ident]
    if anchor.exists() and not anchor.is_dir():
        raise ValueError('Vị trí nguồn không phải thư mục. Chưa chuyển dữ liệu.')
    source = anchor.resolve()
    safe_source(source)
    base = folder(directory)/'FrameWeaver-storage'
    target = base/ident
    if base.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError('Đích đã tồn tại hoặc thư mục lưu là liên kết. Chọn thư mục cha khác.')
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Chọn vị trí khác, nằm ngoài thư mục hiện tại.')
    for other, root in anchors().items():
        if other == ident:
            continue
        for path in {root.absolute(), root.resolve()}:
            if target.is_relative_to(path) or path.is_relative_to(target):
                raise ValueError(f'Không đặt các danh mục nơi lưu lồng vào nhau ({other}). Chọn thư mục ngoài các danh mục đang quản lý.')
    files = snapshot(source)
    size = sum(item['size'] for item in files)
    free = shutil.disk_usage(folder(directory)).free
    if free < size+64*1024**2:
        raise ValueError('Đích không đủ dung lượng; cần thêm 64 MB dự phòng.')
    payload = dict(id=ident, source=str(source), anchor=str(anchor), target=str(target), directory=str(folder(directory)), signature=signature(files))
    return {**payload, 'token':signature(payload), 'size_bytes':size, 'free_bytes':free}


async def require_idle():
    from agent.db import crud
    from agent.services import local_models, media_jobs, audio_mix_jobs, content_production
    if writers or local_models.in_use() or media_jobs.busy() or audio_mix_jobs._TASKS or content_production._planning:
        raise ValueError('Đợi tải lên, sản xuất và xử lý video/model hoàn tất trước khi chuyển nơi lưu.')
    if await crud.list_requests(status='PENDING') or await crud.list_requests(status='PROCESSING'):
        raise ValueError('Còn yêu cầu AI đang chờ/chạy. Dừng hoặc đợi hoàn tất trước khi chuyển nơi lưu.')
    if writers or media_jobs.busy():
        raise ValueError('Có công việc vừa bắt đầu. Đợi hoàn tất rồi thử lại.')


def copy_tree(source, stage, entries, update):
    done, total = 0, sum(item['size'] for item in entries)
    last = 0
    for item in entries:
        if _cancel.is_set():
            raise InterruptedError('Đã hủy chuyển nơi lưu.')
        original, target = source/item['path'], stage/item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if item['kind'] == 'dir':
            target.mkdir(exist_ok=True)
        elif item['kind'] == 'link':
            try:
                target.symlink_to(item['link'], target_is_directory=original.is_dir())
            except OSError as exc:
                raise ValueError('Ổ đích không tạo được liên kết bên trong bộ build. Chọn ổ hỗ trợ liên kết; thư mục nguồn được giữ nguyên.') from exc
        else:
            digest = hashlib.sha256()
            with original.open('rb') as src, target.open('wb') as dst:
                while block := src.read(1024*1024):
                    if _cancel.is_set():
                        raise InterruptedError('Đã hủy chuyển nơi lưu.')
                    digest.update(block);dst.write(block);done += len(block)
                    if time.monotonic()-last > .25:
                        update(stage=f'Sao chép {item["path"]}', progress=min(95, round(95*done/max(1,total))))
                        last = time.monotonic()
            with target.open('rb') as check:
                if hashlib.file_digest(check, 'sha256').digest() != digest.digest():
                    raise ValueError('Bản sao không khớp SHA256; chưa đổi nơi lưu.')
            shutil.copystat(original, target)
    for item in reversed(entries):
        if item['kind'] == 'dir':
            shutil.copystat(source/item['path'], stage/item['path'])


def finish_switch(ident, entry):
    """Persisted switch intent lets startup finish an interrupted link change."""
    anchor, target = anchors()[ident], Path(entry['target'])
    if read(Path(entry['base'])/'.frameweaver-storage.json', {}).get('token') != entry['token'] or not target.is_dir():
        raise ValueError('Chưa kết nối ổ đích để hoàn tất đổi nơi lưu dữ liệu.')
    anchor.parent.mkdir(parents=True, exist_ok=True)
    link = anchor.with_name(f'.{anchor.name}.frameweaver-link')
    link.unlink(missing_ok=True)
    link.symlink_to(target, target_is_directory=True)  # Fail before touching the source if links are unavailable.
    try:
        if anchor.exists() and not anchor.is_symlink():
            previous = Path(entry['previous']['path'])
            if previous.exists():
                raise ValueError('Thư mục dự phòng đã tồn tại; chưa ghi đè dữ liệu.')
            anchor.rename(previous)
        link.replace(anchor)
    except BaseException:
        previous = Path(entry['previous']['path'])
        if not anchor.exists() and previous.exists():
            previous.rename(anchor)
        raise
    finally:
        link.unlink(missing_ok=True)


def recover():
    data = settings()
    changed = False
    for ident, entry in list(data['locations'].items()):
        if entry.get('switching'):
            if ident not in anchors():
                raise ValueError('Danh mục nơi lưu không hợp lệ.')
            available = Path(entry['target']).is_dir() and read(Path(entry['base'])/'.frameweaver-storage.json', {}).get('token') == entry['token']
            if not available:
                anchor = anchors()[ident]
                if anchor.is_symlink() and anchor.resolve() == Path(entry['target']):
                    continue  # Keep the chosen offline location; do not restore stale media silently.
                previous = Path(entry['previous']['path'])
                if not anchor.exists() and previous.is_dir():
                    previous.rename(anchor)
                if entry.get('prior'):
                    data['locations'][ident] = entry['prior']
                else:
                    data['locations'].pop(ident, None)
                changed = True
                continue
            finish_switch(ident, entry)
            entry['switching'] = False
            entry.pop('prior', None)
            changed = True
    if changed:
        save(CONFIG, data)
    state = read(JOB, {})
    if state.get('status') in {'QUEUED','PROCESSING'} and state.get('stage_path'):
        ident = state.get('id')
        for key in ('stage_path','target_path'):
            path = Path(state[key]) if state.get(key) else None
            if path and ident in anchors() and path.parent.name == 'FrameWeaver-storage' and not path.is_symlink() and path.is_dir():
                owned = read(path/'.frameweaver-copy.json', {}).get('owner') == state.get('copy_owner') and bool(state.get('copy_owner'))
                committed = data['locations'].get(ident, {}).get('target') == str(path)
                if owned and not committed:
                    shutil.rmtree(path)
        save(JOB, {**state, 'status':'FAILED', 'error':'Tác vụ bị gián đoạn; vị trí đã chuyển được khôi phục nếu đã lưu, còn bản sao dở đã dọn.'})


async def relocate(plan, update):
    from agent.services.local_models import disk_operation
    ident = plan['id']
    source, target, anchor = Path(plan['source']), Path(plan['target']), Path(plan['anchor'])
    entries = snapshot(source)
    existed = source.is_dir()
    base = target.parent
    base.mkdir(parents=True, exist_ok=True)
    marker = base/'.frameweaver-storage.json'
    if not marker.exists():
        save(marker, {'token':str(uuid.uuid4())})
    stage = base/f'.{ident}-{uuid.uuid4()}.moving'
    stage.mkdir()
    owner = str(uuid.uuid4())
    save(stage/'.frameweaver-copy.json', {'owner':owner})
    update(stage_path=str(stage), target_path=str(target), copy_owner=owner)
    published = False
    old = settings()
    try:
        await disk_operation(copy_tree, source, stage, entries, update)
        if _cancel.is_set():
            raise InterruptedError('Đã hủy chuyển nơi lưu.')
        if signature(snapshot(source)) != plan['signature']:
            raise ValueError('Dữ liệu nguồn đã thay đổi trong khi sao chép; giữ nguyên nơi lưu cũ.')
        if target.exists() or target.is_symlink():
            raise ValueError('Đích đã xuất hiện; chưa ghi đè dữ liệu.')
        stage.rename(target);published = True
        previous = source if anchor.is_symlink() else anchor.with_name(f'.{anchor.name}.frameweaver-backup-{uuid.uuid4()}')
        entry = dict(base=str(base), target=str(target), token=read(marker, {})['token'], switching=True,
            prior=old['locations'].get(ident), previous=dict(path=str(previous), signature=plan['signature']))
        data = settings();data['locations'][ident] = entry
        save(CONFIG, data)
        try:
            finish_switch(ident, entry)
        except BaseException:
            save(CONFIG, old)
            raise
        entry['switching'] = False
        entry.pop('prior', None)
        if not existed:
            entry['previous'] = None
        save(CONFIG, data)
        _sizes.clear()
        update(stage='Đã đổi nơi lưu; bản cũ được giữ dự phòng', progress=100)
    finally:
        if stage.exists():
            await disk_operation(shutil.rmtree, stage)
        if published and settings()['locations'].get(ident, {}).get('target') != str(target):
            await disk_operation(shutil.rmtree, target)


async def delete_previous(ident, update):
    from agent.services.local_models import disk_operation
    if ident not in anchors() or (error := offline(ident)):
        raise ValueError(error if ident in anchors() else 'Danh mục không hợp lệ.')
    data = settings();entry = data['locations'].get(ident, {})
    previous = entry.get('previous')
    if not previous:
        raise ValueError('Không có bản cũ dự phòng.')
    source, target = Path(previous['path']), anchors()[ident].resolve()
    safe_source(source.resolve())
    if source.is_symlink() or source.resolve() == target or signature(snapshot(source)) != previous['signature']:
        raise ValueError('Bản cũ đã thay đổi; chưa xóa để bảo toàn dữ liệu.')
    files = snapshot(source)
    def verify():
        for index, item in enumerate(files):
            if _cancel.is_set():
                raise InterruptedError('Đã hủy kiểm tra bản cũ.')
            a, b = source/item['path'], target/item['path']
            if item['kind'] == 'file':
                if not b.is_file() or b.stat().st_size != a.stat().st_size:
                    raise ValueError('Bản mới đã thay đổi hoặc thiếu file. Giữ bản cũ để kiểm tra.')
                with a.open('rb') as old, b.open('rb') as new:
                    if hashlib.file_digest(old, 'sha256').digest() != hashlib.file_digest(new, 'sha256').digest():
                        raise ValueError('Bản mới đã thay đổi. Giữ bản cũ để kiểm tra.')
            elif item['kind'] == 'link' and (not b.is_symlink() or os.readlink(b) != item['link']):
                raise ValueError('Liên kết bản mới đã thay đổi. Giữ bản cũ để kiểm tra.')
            update(stage='Đối chiếu bản mới trước khi xóa bản cũ', progress=round(90*(index+1)/max(1,len(files))))
    await disk_operation(verify)
    update(stage='Đang xóa bản cũ đã đối chiếu', progress=95)
    await disk_operation(shutil.rmtree, source)
    entry['previous'] = None;save(CONFIG, data);_sizes.clear()


async def start(ident, action, directory=None, token=None):
    global _task
    if busy():
        raise ValueError('Đang có tác vụ nơi lưu. Đợi hoàn tất hoặc hủy trước.')
    await require_idle()
    if busy():
        raise ValueError('Đang có tác vụ nơi lưu.')
    plan = preview(ident, directory) if action == 'move' else None
    if plan and plan['token'] != token:
        raise ValueError('Nguồn hoặc đích đã thay đổi. Kiểm tra nơi chuyển lại.')
    _cancel.clear()
    state = dict(id=ident, action=action, status='QUEUED', progress=0, stage='Đang chờ', error=None)
    def update(**changes):
        state.update(changes);save(JOB, state)
    update()
    async def run():
        try:
            update(status='PROCESSING')
            if action == 'move':
                await relocate(plan, update)
            else:
                await delete_previous(ident, update)
            update(status='COMPLETED', stage='Hoàn tất', progress=100)
        except InterruptedError:
            update(status='CANCELLED', stage='Đã hủy', error=None)
        except Exception as exc:
            update(status='FAILED', error=str(exc))
    _task = asyncio.create_task(run())
    return state


async def stop():
    _cancel.set()
    if _task:
        await asyncio.shield(_task)
    return read(JOB, None)


def open_folder(ident):
    item = next((item for item in inventory()['items'] if item['id'] == ident), None)
    if not item or not Path(item['real_path']).is_dir():
        if not item or not Path(item['real_path']).is_file():
            raise ValueError('Thư mục chưa tồn tại hoặc ổ chưa kết nối.')
    path = Path(item['real_path'])
    path = path.parent if path.is_file() else path
    command = ['open', str(path)] if sys.platform == 'darwin' else ['explorer', str(path)] if os.name == 'nt' else ['xdg-open', str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
