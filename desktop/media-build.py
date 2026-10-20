"""Build portable GPL FFmpeg CLI tools from checksum-pinned source.

No Homebrew libraries are linked. Corresponding sources and build instructions
are retained for distribution alongside the binary release.
"""
import hashlib
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
SOURCES = (
    ("pkgconf-3.0.8.tar.xz", "https://github.com/pkgconf/pkgconf/releases/download/pkgconf-3.0.8/pkgconf-3.0.8.tar.xz",
     "943f0bc88bcaeceb630b00262e2a8ad5ea4fac3fba72a4267d8b9ec6b35d1194"),
    ("ffmpeg-8.1.tar.xz", "https://ffmpeg.org/releases/ffmpeg-8.1.tar.xz",
     "b072aed6871998cce9b36e7774033105ca29e33632be5b6347f3206898e0756a"),
    ("x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz",
     "https://code.videolan.org/videolan/x264/-/archive/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz",
     "cd71a7515b0e9a012e1ac9b1f8415bebcaf6fc97d4db32286642ac4c0fbe24f9"),
)


def download(name, url, sha256):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / name
    shared = ROOT / '.cache' / name
    if shared != target and shared.is_file():
        with shared.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() == sha256:
                return shared
    if not target.exists() or hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() != sha256:
        temporary = target.with_suffix('.download')
        subprocess.run(['curl', '-fL', '--retry', '3', '--max-time', '600', url, '-o', str(temporary)], check=True)
        if hashlib.file_digest(temporary.open('rb'), 'sha256').hexdigest() != sha256:
            temporary.unlink()
            raise RuntimeError(f'Checksum mismatch: {name}')
        temporary.replace(target)
    return target


def build(arch='arm64'):
    if os.uname().sysname != 'Darwin' or arch not in {'arm64', 'x64'}:
        raise RuntimeError('Build macOS media tools for arm64 or x64 on macOS.')
    machine = 'x86_64' if arch == 'x64' else 'arm64'
    destination = CACHE / 'media'
    if (destination / 'bin/ffmpeg').is_file() and (destination / 'bin/ffprobe').is_file():
        for name in ['ffmpeg', 'ffprobe']:
            subprocess.run(['lipo', destination / 'bin' / name, '-verify_arch', machine], check=True)
        return destination
    # Autotools/Makefiles do not reliably quote source paths containing spaces.
    work = Path(tempfile.gettempdir()) / (f'frameweaver-media-{arch}-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12])
    work.mkdir(parents=True, exist_ok=True)
    for name, url, digest in SOURCES:
        archive = download(name, url, digest)
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter='data')
    minimum = '13.0' if arch == 'x64' else '12.0'
    flags = f'-arch {machine} -mmacosx-version-min={minimum}'
    env = {**os.environ, 'MACOSX_DEPLOYMENT_TARGET': minimum, 'CC': f'clang -arch {machine}'}
    if arch == 'x64' and not shutil.which('nasm'):
        raise RuntimeError('NASM is required to build optimized Intel FFmpeg/x264.')
    # FFmpeg only needs x264's pkg-config metadata; ship the small, standard
    # pkgconf implementation as a build dependency if the host lacks it.
    pkgconf = shutil.which('pkg-config') or shutil.which('pkgconf')
    x264 = next(work.glob('x264-*'))
    prefix = work / 'prefix'
    jobs = str(min(os.cpu_count() or 2, 8))
    with (CACHE / 'media-build.log').open('w') as log:
        def run(args, cwd):
            print(' '.join(map(str, args)), flush=True)
            subprocess.run(list(map(str, args)), cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        if not pkgconf:
            pkgconf_source = work / 'pkgconf-3.0.8'
            run(['./configure', f'--prefix={work / "tools"}', '--disable-shared'], pkgconf_source)
            run(['make', f'-j{jobs}'], pkgconf_source)
            run(['make', 'install'], pkgconf_source)
            pkgconf = str(work / 'tools/bin/pkgconf')
        run(['./configure', f'--prefix={prefix}', '--enable-static', '--disable-cli', '--disable-opencl',
             *(['--host=x86_64-apple-darwin'] if arch == 'x64' else []),
             f'--extra-cflags={flags}', f'--extra-ldflags={flags}'], x264)
        run(['make', f'-j{jobs}'], x264)
        run(['make', 'install'], x264)
        env['PKG_CONFIG_PATH'] = str(prefix / 'lib/pkgconfig')
        ffmpeg = work / 'ffmpeg-8.1'
        run(['./configure', f'--prefix={work / "staging"}', '--disable-shared', '--enable-static',
             '--disable-autodetect', '--enable-gpl', '--enable-libx264', '--disable-debug', '--disable-doc',
             '--disable-ffplay', '--enable-videotoolbox', '--enable-audiotoolbox', '--enable-zlib',
             *(['--arch=x86_64', '--target-os=darwin', '--enable-cross-compile'] if arch == 'x64' else []),
             f'--pkg-config={pkgconf}', f'--extra-cflags={flags}', f'--extra-ldflags={flags}'], ffmpeg)
        run(['make', f'-j{jobs}'], ffmpeg)
        (destination / 'bin').mkdir(parents=True, exist_ok=True)
        for name in ['ffmpeg', 'ffprobe']:
            shutil.copy2(ffmpeg / name, destination / 'bin' / name)
            subprocess.run(['lipo', destination / 'bin' / name, '-verify_arch', machine], check=True)
        for name in ['COPYING.GPLv2', 'COPYING.GPLv3', 'LICENSE.md']:
            shutil.copy2(ffmpeg / name, destination / name)
        shutil.copy2(x264 / 'COPYING', destination / 'COPYING.x264')
    # Keep archives, remove object files after a successful build to save space.
    shutil.rmtree(work)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=['arm64', 'x64'], default='arm64')
    args = parser.parse_args()
    if args.arch == 'x64':
        CACHE = ROOT / '.cache/x64'
    print(build(args.arch))
