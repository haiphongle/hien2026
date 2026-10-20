"""Optional local Muse gateway. Run with FrameWeaver's Python; --install on first use.

Uses its own venv and Chromium profile; never reads the user's browser cookies.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.paths import DATA_DIR

STATE = DATA_DIR / '.flowkit' / 'muse-gateway'
PYTHON = STATE / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
REVISION = 'efc06dd9741efe3b56a4bb02bb4761c0003eae30'
PACKAGE = f'https://github.com/crisng95/muse2api/archive/{REVISION}.zip#sha256=2eb53421aecdb428bde0e2fef95970e076ccc891021ebba502e5662834d05e31'
# The desktop build's smoke checks use separate ports and empty data directories.
PORT = int(os.environ.get('FRAMEWEAVER_DESKTOP_TEST_MUSE_PORT', '18610'))
CDP_PORT = int(os.environ.get('FRAMEWEAVER_DESKTOP_TEST_MUSE_CDP_PORT', '19210'))
BASE_URL = f'http://127.0.0.1:{PORT}'


def local_url(value):
    return value in {BASE_URL, f'http://localhost:{PORT}'}


def port_busy(port):
    import socket
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def installed():
    if not PYTHON.is_file():
        return False
    packages = PYTHON.parent.parent / 'Lib/site-packages/muse2api/app.py'
    if not packages.is_file() and not any((PYTHON.parent.parent / 'lib').glob('python*/site-packages/muse2api/app.py')):
        return False
    # A marker is written only after imports succeed. Accept the old launcher's
    # successfully launched environment too, without reinstalling an active gateway.
    for name in ['installed.json', 'process.json']:
        try:
            if json.loads((STATE / name).read_text()).get('revision') == REVISION:
                return True
        except (OSError, ValueError):
            pass
    return False


def find_chrome():
    import shutil
    candidates = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                  '/Applications/Chromium.app/Contents/MacOS/Chromium']
    if os.name == 'nt':
        candidates += [str(Path(os.environ.get(base, '')) / tail) for base, tail in [
            ('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe'),
            ('PROGRAMFILES(X86)', 'Microsoft/Edge/Application/msedge.exe'),
            ('LOCALAPPDATA', 'Google/Chrome/Application/chrome.exe'),
        ]]
    return next((p for p in candidates if Path(p).is_file()), None) or next(
        (p for name in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']
         if (p := shutil.which(name))), None)


def environment(config, chrome=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('MUSE2API_')}
    env.pop('PYTHONHOME', None)
    env.update({
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'FRAMEWEAVER_DATA_DIR': str(DATA_DIR),
        'MUSE2API_HOST': '127.0.0.1', 'MUSE2API_PORT': str(PORT),
        'MUSE2API_DRIVER': 'browser', 'MUSE2API_DATA_DIR': str(STATE / 'data'),
        'MUSE2API_API_KEY': config.api_key, 'MUSE2API_ADMIN_KEY': config.api_key,
        'MUSE2API_BILLING_ENABLED': 'false', 'MUSE2API_ACCOUNT_MAX_CONCURRENCY': '1',
        'MUSE2API_MAX_FAILOVER': '0', 'MUSE2API_CDP_PORT': str(CDP_PORT),
    })
    chrome = chrome or find_chrome()
    if chrome:
        env['MUSE2API_CHROMIUM_PATH'] = str(Path(chrome).resolve())
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install the pinned gateway into an isolated environment')
    parser.add_argument('--prepare-only', action='store_true', help='Install/verify without starting a server')
    parser.add_argument('--background', action='store_true', help='Keep running independently of this terminal; log in .flowkit/muse-gateway')
    parser.add_argument('--chrome', help='Path to Chrome/Chromium (normally found automatically)')
    args = parser.parse_args()
    from agent.services import muse_settings
    config = muse_settings.load()
    if not local_url(config.base_url):
        parser.error('FrameWeaver đang trỏ tới gateway khác. Kiểm tra Cài đặt trước khi chạy gateway nội bộ.')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.install:
        print('FRAMEWEAVER_STAGE:Đang tạo môi trường Muse riêng', flush=True)
        if not PYTHON.exists():
            venv.EnvBuilder(with_pip=True).create(PYTHON.parent.parent)
        print('FRAMEWEAVER_STAGE:Đang tải và cài gateway Muse', flush=True)
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '--disable-pip-version-check', PACKAGE], check=True)
    if not PYTHON.exists():
        parser.error('Chạy lại với --install để cài gateway riêng.')
    print('FRAMEWEAVER_STAGE:Đang kiểm tra gateway đã cài', flush=True)
    subprocess.run([str(PYTHON), '-c', 'from muse2api.app import create_app; from muse2api.drivers.browser.driver import BrowserDriver'], check=True)
    marker = STATE / 'installed.json'
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'revision': REVISION}))
    temporary.replace(marker)
    if args.prepare_only:
        return
    if port_busy(PORT) or port_busy(CDP_PORT):
        parser.error('Cổng 18610 hoặc trình duyệt Muse 19210 đang được dùng. Đóng phiên Muse cũ trước khi khởi động thêm.')
    if not config.api_key:
        import secrets
        config = muse_settings.save({'api_key': secrets.token_urlsafe(32), 'enabled': True})
    env = environment(config, args.chrome)
    command = [str(PYTHON), str(ROOT / 'scripts' / 'muse_gateway_runtime.py')]
    print(f'Gateway: {BASE_URL}/dashboard', flush=True)
    print('Vào Cài đặt FrameWeaver → Muse → Kết nối tài khoản Muse trên máy để nhập phiên.', flush=True)
    print('Khóa quản trị là api_key trong .flowkit/muse.json; không chia sẻ file này.', flush=True)
    if args.background:
        log = STATE / 'gateway.log'
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'ab') as handle:
            options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen(command, cwd=STATE, env=env, stdin=subprocess.DEVNULL,
                                       stdout=handle, stderr=subprocess.STDOUT, **options)
        (STATE / 'process.json').write_text(json.dumps({'pid': process.pid, 'revision': REVISION}))
        print(f'Đã khởi động PID {process.pid}; xem gateway.log để xác nhận sẵn sàng.')
    else:
        subprocess.run(command, cwd=STATE, env=env, check=True)


if __name__ == '__main__':
    main()
