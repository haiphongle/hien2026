"""Persistent model locations and verified copies. Environments never move."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import string
import uuid

import anyio

from agent.paths import DATA_DIR
from agent.services.speech_store import atomic_json, read_json

CONFIG = DATA_DIR/'.flowkit'/'model-storage.json'
settings_lock = asyncio.Lock()


def settings():
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {'locations': {}, 'default': None}
    except (OSError, ValueError) as exc:
        raise ValueError('Không đọc được cấu hình nơi lưu model. Chưa tự đổi về ổ trong.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('locations'), dict):
        raise ValueError('Cấu hình nơi lưu model không hợp lệ. Chưa tự đổi về ổ trong.')
    def valid_base(entry):
        return isinstance(entry, dict) and isinstance(entry.get('base'), str) and Path(entry['base']).is_absolute() and isinstance(entry.get('token'), str) and bool(entry['token'])
    if data.get('default') is not None and not valid_base(data['default']):
        raise ValueError('Nơi lưu model mặc định không hợp lệ. Chưa tự đổi về ổ trong.')
    for entry in data['locations'].values():
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not Path(entry['path']).is_absolute() or entry.get('mode') not in {'manual','default','existing'} or entry.get('base') is not None and not valid_base(entry):
            raise ValueError('Vị trí model đã lưu không hợp lệ. Chưa tự đổi về ổ trong.')
    return data


def location(ident, fallback, *, preserve_existing=False):
    data = settings()
    entry = data['locations'].get(ident)
    if entry:
        return Path(entry['path'])
    # Newly registered legacy engines must keep their installed folder even if
    # a global download default was selected before this engine was managed.
    if preserve_existing and Path(fallback).exists():
        return Path(fallback)
    return Path(data['default']['base'])/ident if data.get('default') else Path(fallback)


def assert_available(ident, path):
    data = settings()
    entry = data['locations'].get(ident)
    if not entry and data.get('default') and Path(path).resolve().is_relative_to(Path(data['default']['base'])):
        entry = data['default']
    if entry and entry.get('base'):
        base = Path(entry['base'])
        if read_json(base/'.frameweaver-storage.json', {}).get('token') != entry['token']:
            raise ValueError(f'Không tìm thấy ổ/thư mục chứa model: {base}. Kết nối lại ổ hoặc kiểm tra nơi lưu; không tự tải sang ổ trong.')
    for part in (Path(path), *Path(path).parents):
        if part.is_symlink() and not part.exists():
            raise ValueError(f'Không tìm thấy ổ chứa model: {part.resolve()}. Kết nối lại ổ rồi thử lại.')
    resolved = Path(path).resolve()
    if os.name == 'nt' and not Path(resolved.anchor).is_dir():
        raise ValueError(f'Không tìm thấy ổ chứa model: {resolved.anchor}.')
    if len(resolved.parts) > 2 and resolved.parts[1] == 'Volumes' and not Path(*resolved.parts[:3]).is_mount():
        raise ValueError(f'Không tìm thấy ổ chứa model: {Path(*resolved.parts[:3])}. Kết nối lại ổ rồi thử lại.')


def assert_weights(ident, root, names):
    assert_available(ident, root)
    if any((root/name).exists() for name in ('model-invalid.json', 'model-removed.json')):
        raise ValueError('Model đã xóa hoặc cần kiểm tra. Vào Cài đặt → Model trên máy để tải lại.')
    if not all((root/name).is_file() for name in names):
        raise ValueError('Thiếu file model. Vào Cài đặt → Model trên máy để tải hoặc kiểm tra nơi lưu.')


def folder(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('Chọn một thư mục có sẵn trên máy đang chạy FrameWeaver.')
    return path.resolve()


def browse(value=None):
    from agent.config import BASE_DIR
    path = folder(value) if value else Path.home()
    roots = [Path.home(), BASE_DIR]
    if os.name == 'nt':
        roots += [Path(f'{letter}:/') for letter in string.ascii_uppercase if Path(f'{letter}:/').is_dir()]
    else:
        for base in (Path('/Volumes'), Path('/media')/Path.home().name, Path('/mnt')):
            if base.is_dir():
                roots += [p for p in base.iterdir() if p.is_dir()]
    return {'path': str(path), 'parent': str(path.parent), 'roots': list(dict.fromkeys(str(p) for p in roots)),
        'folders': sorted([{'name': p.name, 'path': str(p)} for p in path.iterdir()
            if not p.name.startswith('.') and p.is_dir()], key=lambda p: p['name'].casefold())}


def base_for(directory, *, create=False):
    parent = folder(directory)
    base = parent/'FrameWeaver-models'
    if base.is_symlink():
        raise ValueError('Thư mục FrameWeaver-models tại đích không được là liên kết. Chọn thư mục thực tế.')
    if create:
        base.mkdir(exist_ok=True)
        marker = base/'.frameweaver-storage.json'
        if not marker.exists():
            atomic_json(marker, {'token': str(uuid.uuid4())})
        if not read_json(marker, {}).get('token'):
            raise ValueError('Dấu nhận diện thư mục model ở đích không hợp lệ.')
    return base


def bind(base, target, mode):
    return {'path': str(target), 'base': str(base), 'token': read_json(base/'.frameweaver-storage.json')['token'], 'mode': mode}


def info():
    data = settings()
    default = data.get('default')
    online = not default or read_json(Path(default['base'])/'.frameweaver-storage.json', {}).get('token') == default['token']
    return {'default': default, 'online': online}


def apply_workers():
    from agent.services import local_models as models
    for ident, item in models.registry().items():
        if not item.get('worker'):
            continue
        worker = item['worker']
        worker.cache_dir = location(ident, getattr(worker, 'default_cache_dir', worker.cache_dir))
        worker._probe = None


async def set_default(directory):
    from agent.services import local_models as models, media_jobs as jobs
    async with settings_lock:
        if models.in_use() or any((jobs.latest(models.job_key(key)) or {}).get('status') in jobs.ACTIVE for key in models.registry()):
            raise ValueError('Đợi các tác vụ dùng model trên máy hoàn tất trước khi đổi nơi lưu mặc định.')
        data = settings()
        base = base_for(directory, create=True) if directory else None
        for ident, item in models.registry().items():
            current = data['locations'].get(ident)
            existing = any(p.exists() for p in models.paths(item)) or (item['root']/'model-removed.json').exists()
            if current and current['mode'] != 'default':
                continue
            if existing:
                data['locations'][ident] = current or {'path':str(item['root'].absolute()), 'mode':'existing'}
                continue  # A default never relocates an existing model.
            if base:
                data['locations'][ident] = bind(base, base/ident, 'default')
            else:
                data['locations'].pop(ident, None)
        data['default'] = {'base': str(base), 'token': read_json(base/'.frameweaver-storage.json')['token']} if base else None
        async with models.TTS_SEMAPHORE:
            atomic_json(CONFIG, data)
            apply_workers()
        return info()


def move_record(ident):
    from agent.services import local_models as models
    return models.jobs.directory(models.job_key(ident))/'location-move.json'


def source_files(item):
    from agent.services import local_models as models
    root = item['root'].resolve()
    targets = models.paths(item)
    if item['engine'] != 'Whisper':
        targets += [root/name for name in ('ready.json', 'clone_ready.json', 'model-invalid.json', 'model-removed.json')]
    files = []
    for path in models.files_in(targets):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not path.is_file():
            raise ValueError('Tư liệu model có liên kết trỏ ra ngoài cache hoặc không đọc được. Chưa chuyển file.')
        stat = path.stat()
        files.append({'path': str(path.relative_to(item['root'] if path.is_relative_to(item['root']) else root)),
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    return sorted(files, key=lambda f: f['path'])


def preview(ident, directory):
    from agent.services import local_models as models
    item = models.config(ident)
    assert_available(ident, item['root'])
    old = read_json(move_record(ident), {})
    if old.get('previous') and old.get('target') == str(item['root']):
        raise ValueError('Model có bản cũ đang giữ dự phòng. Kiểm tra bản mới và xử lý bản cũ trước khi chuyển tiếp.')
    base = base_for(directory)
    target = base/ident
    source = item['root'].resolve()
    if target == source or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Nơi lưu mới phải khác và nằm ngoài thư mục model hiện tại.')
    if target.exists() or target.is_symlink():
        raise ValueError(f'Thư mục đích đã tồn tại: {target}. Chọn thư mục cha khác để giữ dữ liệu đang có.')
    files = source_files(item)
    attributes = {f['path']:(source/f['path']).stat() for f in files}
    snapshots = {(attributes[f['path']].st_dev, attributes[f['path']].st_ino)
        for f in files if 'snapshots' in Path(f['path']).parts}
    # Snapshot files are materialized on the destination. Their redundant HF blob
    # copies are unnecessary, especially on exFAT without symlinks/hard links.
    copied = [f for f in files if not ('blobs' in Path(f['path']).parts
        and (attributes[f['path']].st_dev, attributes[f['path']].st_ino) in snapshots)]
    size = sum(f['size'] for f in copied)  # Bound remaining copies on filesystems without hard links.
    free = shutil.disk_usage(folder(directory)).free
    if free < size+64*1024**2:
        raise ValueError('Ổ đích không đủ dung lượng cho bản sao model và phần dự phòng 64 MB.')
    payload = {'id': ident, 'source': str(source), 'target': str(target), 'directory': str(folder(directory)), 'files': files,
        'copy_paths':[f['path'] for f in copied]}
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    executable = Path(shutil.which(item['worker'].python) or item['worker'].python).absolute() if item.get('worker') else None
    # Resolve the venv directory, not its python symlink to the system binary.
    runtime = executable.parent.resolve()/executable.name if executable else None
    return {**payload, 'size_bytes': size, 'free_bytes': free, 'token': token,
        'runtime_path': str(runtime) if runtime else None,
        'runtime_in_source': bool(runtime and runtime.is_relative_to(source))}


async def copy_files(source, stage, files, update):
    total, done, previous, links = sum(f['size'] for f in files), 0, -1, {}
    for entry in files:
        original, target = source/entry['path'], stage/entry['path']
        stat = original.stat()
        if (stat.st_size, stat.st_mtime_ns) != (entry['size'], entry['mtime_ns']):
            raise ValueError('File nguồn đã đổi trong khi chuyển. Bản cũ vẫn được giữ.')
        target.parent.mkdir(parents=True, exist_ok=True)
        inode = (stat.st_dev, stat.st_ino)
        linked = False
        if inode in links:
            try:
                os.link(links[inode], target)
                linked = True
            except OSError:
                pass  # exFAT/Windows: a regular copy works without symlink privileges.
        if not linked:
            digest = hashlib.sha256()
            async with await anyio.open_file(original, 'rb') as src, await anyio.open_file(target, 'wb') as dst:
                while block := await src.read(1024*1024):
                    digest.update(block)
                    await dst.write(block)
                    done += len(block)
                    progress = min(97, round(97*done/max(1,total)))
                    if progress != previous:
                        update(stage=f'Sao chép {entry["path"]}', progress=progress, bytes_downloaded=done, bytes_total=total)
                        previous = progress
            check = hashlib.sha256()
            async with await anyio.open_file(target, 'rb') as dst:
                while block := await dst.read(1024*1024):
                    check.update(block)
            if check.digest() != digest.digest() or target.stat().st_size != entry['size']:
                raise ValueError('Bản sao không khớp SHA256; chưa đổi nơi lưu model.')
            links[inode] = target
        else:
            done += entry['size']
        after = original.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError('File nguồn đã đổi trong khi chuyển. Bản cũ vẫn được giữ.')
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def start_moves(plans):
    from agent.services import local_models as models, media_jobs as jobs
    if models.in_use():
        raise ValueError('Đợi các tác vụ dùng model trên máy hoàn tất trước khi chuyển model.')
    checked = []
    for plan in plans:
        current = preview(plan['id'], plan['directory'])
        if current['token'] != plan['token']:
            raise ValueError('Nơi lưu hoặc file model đã thay đổi. Xem lại kế hoạch chuyển.')
        if (jobs.latest(models.job_key(plan['id'])) or {}).get('status') in jobs.ACTIVE:
            raise ValueError('Model đang có tác vụ. Đợi hoàn tất hoặc hủy trước.')
        checked.append(current)
    if len({p['id'] for p in checked}) != len(checked):
        raise ValueError('Danh sách chuyển bị trùng model.')
    by_disk = {}
    for plan in checked:
        key = folder(plan['directory']).stat().st_dev
        by_disk[key] = by_disk.get(key, 0)+plan['size_bytes']
        if by_disk[key]+64*1024**2 > plan['free_bytes']:
            raise ValueError('Ổ đích không đủ dung lượng cho toàn bộ nhóm model.')
    return [jobs.start(models.job_key(p['id']), 'model_move', lambda update, p=p: relocate(p, update),
        metadata={'action':'relocate', 'model':p['id'], 'target':p['target']}) for p in checked]


async def relocate(plan, update):
    from agent.services import local_models as models
    ident = plan['id']
    async with models.lock, settings_lock, models.model_slot(models.config(ident)):
        item = models.config(ident)
        assert_available(ident, item['root'])
        if str(item['root'].resolve()) != plan['source'] or source_files(item) != plan['files']:
            raise ValueError('Model đã đổi sau khi lập kế hoạch. Chưa chuyển dữ liệu.')
        base = base_for(plan['directory'], create=True)
        target = Path(plan['target'])
        if target.exists() or target.is_symlink():
            raise ValueError('Thư mục đích đã xuất hiện; chưa ghi đè dữ liệu.')
        if shutil.disk_usage(base).free < plan['size_bytes']+64*1024**2:
            raise ValueError('Ổ đích không còn đủ dung lượng. Bản cũ vẫn được giữ.')
        stage = base/f'.{ident}-{uuid.uuid4()}.moving'
        stage.mkdir()
        published = False
        try:
            if item.get('worker'):
                await models.disk_operation(item['worker'].close)
            before = models.inventory(ident)
            await copy_files(Path(plan['source']), stage, [f for f in plan['files'] if f['path'] in plan['copy_paths']], update)
            # No await between publishing the directory and committing its location.
            if target.exists() or target.is_symlink():
                raise ValueError('Thư mục đích đã có dữ liệu trong lúc sao chép; chưa ghi đè.')
            stage.rename(target)
            published = True
            saved = models.receipt(ident, item)
            if saved.get('files'):
                for entry in saved['files']:
                    entry['mtime_ns'] = (target/entry['path']).stat().st_mtime_ns
                saved['verified'] = before['verified']
            data = settings()
            data['locations'][ident] = bind(base, target, 'manual')
            previous = {'path':plan['source'], 'files':plan['files'], 'model':item['model']}
            atomic_json(move_record(ident), {'previous':previous if before['installed'] and plan['files'] else None, 'target':str(target)})
            atomic_json(CONFIG, data)
            if item.get('worker'):
                item['worker'].cache_dir = target
                item['worker']._probe = None
            if saved.get('files'):
                atomic_json(models.manifest_path(ident), saved)
            if item['engine'] == 'Whisper':
                verification = models.jobs.directory(models.job_key(ident))/'verification.json'
                if before['verified']:
                    atomic_json(verification, {'file':models.whisper_models.snapshot(target/f'ggml-{item["model"]}.bin'), 'valid':True})
                else:
                    verification.unlink(missing_ok=True)
            update(stage='Đã đổi nơi lưu; bản cũ được giữ dự phòng', result={'path':str(target), 'previous':plan['source'] if plan['files'] else None})
        except BaseException:
            if published and str(location(ident, item['root'])) != str(target):
                await models.disk_operation(shutil.rmtree, target)
            raise
        finally:
            if stage.exists():
                await models.disk_operation(shutil.rmtree, stage)


async def delete_previous(ident):
    from agent.services import local_models as models
    async with settings_lock:
        record = read_json(move_record(ident), {})
        old = record.get('previous')
        if not old:
            raise ValueError('Model không có bản cũ đang được theo dõi.')
        if models.in_use() or (models.jobs.latest(models.job_key(ident)) or {}).get('status') in models.jobs.ACTIVE:
            raise ValueError('Đợi các tác vụ đang chạy hoàn tất trước khi xóa bản cũ.')
        current = models.inventory(ident)
        if not current['ready'] or not current['verified']:
            raise ValueError('Kiểm tra file model tại nơi mới thành công trước khi xóa bản cũ.')
        item = models.config(ident)
        previous = {**item, 'root':Path(old['path'])}
        if str(item['root']) != record.get('target') or item['root'].resolve() == previous['root'].resolve() or item['model'] != old['model']:
            raise ValueError('Đường dẫn bản cũ không còn an toàn để xóa.')
        assert_available('__previous__', previous['root'])
        if source_files(previous) != old['files']:
            raise ValueError('Bản cũ đã thay đổi từ khi chuyển. Chưa tự xóa để giữ dữ liệu của bạn.')
        async with models.model_slot(item):
            await models.disk_operation(models.erase_weights, previous)
            if item['engine'] != 'Whisper':
                for name in ('ready.json', 'clone_ready.json', 'model-invalid.json', 'model-removed.json'):
                    (previous['root']/name).unlink(missing_ok=True)
            atomic_json(move_record(ident), {**record, 'previous':None})
        return {'ok':True}
