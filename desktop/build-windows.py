"""Build the Windows x64 desktop snapshot, using the macOS builder's safe copy list.

Run --prepare before media-windows.sh, then run on Windows with --web-dir.
No credentials, user databases, model weights or development environments are copied.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

DESKTOP = Path(__file__).resolve().parent
ROOT = DESKTOP.parent
CACHE = DESKTOP / '.cache/windows'
BUILD = DESKTOP / 'build-windows'
RELEASE = ROOT / 'release/windows-x64'
spec = importlib.util.spec_from_file_location('macos_distribution', DESKTOP / 'build-macos.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
LOCK = {**shared.LOCK, **json.loads((DESKTOP / 'windows-lock.json').read_text())}
shared.media.CACHE = CACHE


def run(*args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def prepare():
    CACHE.mkdir(parents=True, exist_ok=True)
    for item in [LOCK['electron'], LOCK['python'], *LOCK['fonts']]:
        shared.media.download(**item)
    # Build exactly the same FFmpeg/x264 sources as the Mac release.
    for name, url, sha in shared.media.SOURCES[1:]:
        shared.media.download(name, url, sha)


def pe_arch(path):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError(f'Not a PE executable: {path}')
        stream.seek(0x3c)
        offset = int.from_bytes(stream.read(4), 'little')
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError(f'Invalid PE header: {path}')
        return int.from_bytes(stream.read(2), 'little')


def build(web):
    if os.name != 'nt':
        raise RuntimeError('Build/test this package on Windows x64; --prepare works on macOS too.')
    if not (web / 'index.html').is_file():
        raise RuntimeError('Missing frozen production dashboard.')
    media = CACHE / 'media'
    for name in ['ffmpeg.exe', 'ffprobe.exe']:
        if pe_arch(media / 'bin' / name) != 0x8664:
            raise ValueError('Media tools must target Windows x64.')
    prepare()
    app = BUILD / 'FrameWeaver'
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    with zipfile.ZipFile(CACHE / LOCK['electron']['name']) as archive:
        archive.extractall(app)
    (app / 'electron.exe').rename(app / 'FrameWeaver.exe')
    resources = app / 'resources'
    (resources / 'default_app.asar').unlink(missing_ok=True)
    wrapper, runtime = resources / 'app', resources / 'runtime'
    wrapper.mkdir()
    for name in ['main.cjs', 'preload.cjs', 'startup.html', 'startup.cjs']:
        shutil.copy2(DESKTOP / name, wrapper / name)
    package = json.loads((DESKTOP / 'package.json').read_text())
    package.update(version=LOCK['version'], description='FrameWeaver video studio for Windows x64')
    (wrapper / 'package.json').write_text(json.dumps(package, indent=2), encoding='utf-8')
    runtime.mkdir()
    shared.copy_code(runtime / 'app')
    shutil.copytree(web, runtime / 'web')
    shutil.copytree(ROOT / 'extension', runtime / 'extension', ignore=shutil.ignore_patterns('*.pem','*.crx','_metadata','.DS_Store'))
    shutil.copytree(media, runtime / 'media')
    shutil.copy2(DESKTOP / 'HUONG-DAN-WINDOWS.html', runtime / 'HUONG-DAN-WINDOWS.html')
    with tarfile.open(CACHE / LOCK['python']['name']) as archive:
        archive.extractall(runtime, filter='data')
    python = runtime / 'python/python.exe'
    if pe_arch(python) != 0x8664:
        raise ValueError('Bundled Python must target Windows x64.')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONUTF8':'1', 'PYTHONNOUSERSITE':'1', 'PIP_DISABLE_PIP_VERSION_CHECK':'1'}
    wheels = CACHE / 'wheels'
    wheels.mkdir(exist_ok=True)
    run(python, '-m', 'pip', 'download', '--only-binary=:all:', '--dest', wheels, '-r', DESKTOP / 'requirements-macos.lock', env=env)
    run(python, '-m', 'pip', 'install', '--no-index', '--find-links', wheels, '-r', DESKTOP / 'requirements-macos.lock', env=env)
    run(python, '-m', 'pip', 'check', env=env)
    for folder in (runtime / 'python').rglob('__pycache__'):
        shutil.rmtree(folder)
    fonts = runtime / 'fonts'
    fonts.mkdir()
    with zipfile.ZipFile(CACHE / LOCK['fonts'][0]['name']) as archive:
        for name in ['DejaVuSans.ttf','DejaVuSans-Bold.ttf','DejaVuSerif.ttf','DejaVuSansMono.ttf']:
            member = next(n for n in archive.namelist() if n.endswith('/ttf/' + name))
            (fonts / name).write_bytes(archive.read(member))
        member = next(n for n in archive.namelist() if n.endswith('/LICENSE'))
        (fonts / 'DejaVu-LICENSE.txt').write_bytes(archive.read(member))
    for item in LOCK['fonts'][1:]:
        shutil.copy2(CACHE / item['name'], fonts / item['name'])
    from PIL import Image, ImageDraw, ImageFont
    icon = Image.new('RGBA',(256,256));draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((10,10,246,246),radius=45,fill='#8255EE')
    font = ImageFont.truetype(str(fonts / 'DejaVuSans-Bold.ttf'),85)
    draw.text((128,125),'FW',font=font,anchor='mm',fill='white')
    icon.save(BUILD / 'icon.ico',sizes=[(16,16),(32,32),(48,48),(128,128),(256,256)])
    manifest = {'application':'FrameWeaver','version':LOCK['version'],'platform':'win32','arch':'x64',
                'signed':False,'runtime_assets':[LOCK['electron'],LOCK['python'],*LOCK['fonts']],
                'media_sources':[{'name':n,'url':u,'sha256':s} for n,u,s in shared.media.SOURCES[1:]],
                'wheels':{p.name:shared.digest(p) for p in sorted(wheels.glob('*.whl'))}}
    (runtime / 'BUILD-MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Build complete; run smoke-windows.py before packaging the installer.', flush=True)


def finalize():
    app = BUILD / 'FrameWeaver'
    manifest = json.loads((app / 'resources/runtime/BUILD-MANIFEST.json').read_text())
    installers = list(RELEASE.glob('*-Windows-x64-Setup.exe'))
    if len(installers) != 1 or not (RELEASE / 'VERIFICATION-WINDOWS.json').is_file():
        raise RuntimeError('Installer and successful Windows smoke report required.')
    # The public preview is explicitly unsigned; never label it signed accidentally.
    signature = run('powershell','-NoProfile','-Command',f"(Get-AuthenticodeSignature -LiteralPath '{installers[0]}').Status",capture_output=True,text=True)
    if signature.stdout.strip() != 'NotSigned':
        raise RuntimeError('Unexpected signing state; inspect before publishing.')
    sources = RELEASE / f'FrameWeaver-{LOCK["version"]}-Windows-x64-media-sources.zip'
    with zipfile.ZipFile(sources,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,url,sha in shared.media.SOURCES[1:]:
            archive.write(CACHE / name,'media-sources/'+name)
        for name in ['media-windows.sh','windows-lock.json','README-WINDOWS.md']:
            archive.write(DESKTOP / name,name)
    shutil.copy2(DESKTOP / 'README-WINDOWS.md', RELEASE / 'README-WINDOWS.md')
    files=[installers[0],sources,RELEASE/'VERIFICATION-WINDOWS.json',RELEASE/'README-WINDOWS.md']
    manifest['files']={p.name:{'sha256':shared.digest(p),'bytes':p.stat().st_size} for p in files}
    output=RELEASE/f'FrameWeaver-{LOCK["version"]}-Windows-x64-manifest.json'
    output.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (RELEASE/'SHA256SUMS-WINDOWS.txt').write_text(''.join(f'{shared.digest(p)}  {p.name}\n' for p in [*files,output]),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--finalize',action='store_true')
    parser.add_argument('--web-dir',type=Path,default=DESKTOP/'web')
    args=parser.parse_args()
    if args.prepare: prepare()
    elif args.finalize: finalize()
    else: build(args.web_dir.resolve())
