"""Build a clean macOS application and DMG; no personal data is copied.

Usage: python3 desktop/build-macos.py
       python3 desktop/build-macos.py --identity 'Developer ID Application: …' --notary-profile PROFILE

Builds are local previews unless BOTH signing and notarization succeed. Only
then is a RELEASE-READY marker emitted. Secrets are read from Keychain, never
from source or build arguments. Requires Python 3.12+, Node and Xcode tools.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

DESKTOP = Path(__file__).resolve().parent
ROOT = DESKTOP.parent
CACHE = DESKTOP / '.cache'
BUILD = DESKTOP / 'build'
RELEASE = ROOT / 'release'
LOCK = json.loads((DESKTOP / 'runtime-lock.json').read_text())
RECEIPT = CACHE / 'notarization-receipt.json'
spec = importlib.util.spec_from_file_location('media_build', DESKTOP / 'media-build.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def configure_target(arch):
    global LOCK, CACHE, BUILD, RECEIPT
    configuration = json.loads((DESKTOP / 'runtime-lock.json').read_text())
    if arch not in {'arm64', 'x64'}:
        raise ValueError(f'Unsupported macOS architecture: {arch}')
    LOCK = {key: value for key, value in configuration.items() if key != 'targets'}
    LOCK.update(configuration.get('targets', {}).get(arch, {}))
    if LOCK['arch'] != arch:
        raise ValueError(f'No locked runtime for {arch}')
    CACHE = DESKTOP / '.cache' / 'x64' if arch == 'x64' else DESKTOP / '.cache'
    BUILD = DESKTOP / 'build-x64' if arch == 'x64' else DESKTOP / 'build'
    RECEIPT = CACHE / 'notarization-receipt.json'
    media.CACHE = CACHE


def release_file(name):
    file = Path(name)
    return RELEASE / (f'{file.stem}-x64{file.suffix}' if LOCK['arch'] == 'x64' else name)


def run(*args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    try:
        return subprocess.run(list(map(str, args)), check=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr, flush=True)
        raise


def digest(file):
    with file.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


PAYLOAD_DIRS = ('app', 'runtime/app', 'runtime/web', 'runtime/extension', 'runtime/fonts')


def payload_digest(app):
    """Fingerprint platform-independent code/assets, excluding native runtimes."""
    resources = app / 'Contents/Resources'
    result = hashlib.sha256()
    for name in PAYLOAD_DIRS:
        folder = resources / name
        if not folder.is_dir():
            raise RuntimeError(f'Incomplete frozen bundle: {name}')
        for file in sorted(folder.rglob('*')):
            if file.is_symlink():
                raise RuntimeError(f'Unexpected frozen payload symlink: {file}')
            if file.is_file():
                result.update(str(file.relative_to(resources)).encode() + b'\0')
                result.update(digest(file).encode() + b'\0')
    return result.hexdigest()


def copy_snapshot(source, destination):
    source_resources = source / 'Contents/Resources'
    resources = destination / 'Contents/Resources'
    for name in PAYLOAD_DIRS:
        shutil.copytree(source_resources / name, resources / name)
    shutil.copy2(source_resources / 'electron.icns', resources / 'electron.icns')
    guide = (source_resources / 'runtime/HUONG-DAN-MACOS.html').read_text()
    if LOCK['arch'] == 'x64':
        guide = guide.replace('Apple Silicon (M1 trở lên)', 'Mac Intel (x86_64)')
    (resources / 'runtime/HUONG-DAN-MACOS.html').write_text(guide)


def copy_code(target):
    target.mkdir(parents=True, exist_ok=True)
    # Positive lists, not a copy of the workspace: excludes credentials, DBs,
    # downloaded research repos, outputs, logs and all private signing material.
    for file in (ROOT / 'agent').rglob('*'):
        rel = file.relative_to(ROOT)
        if file.is_symlink():
            raise RuntimeError(f'Unexpected source symlink: {rel}')
        if not file.is_file() or '__pycache__' in rel.parts:
            continue
        if file.suffix not in {'.py', '.json', '.txt', '.md', '.wav'} and file.name != 'LICENSE':
            continue
        if file.name in {'active_project.json', 'providers.json'}:
            continue
        if file.suffix != '.py' and 'assets' not in rel.parts and file.name not in {'models.json', 'media_limits.json'}:
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
    # Public defaults, independent of the build machine's chosen AI account/model.
    (target / 'agent/providers.json').write_text(json.dumps({'active': 'codex', 'roles': {}}, indent=2) + '\n')
    for folder in ['tools', 'skills/song-templates']:
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (target / 'scripts').mkdir()
    for name in ['muse_gateway.py', 'muse_gateway_runtime.py']:
        shutil.copy2(ROOT / 'scripts' / name, target / 'scripts' / name)
    for file in ROOT.glob('requirements*.txt'):
        shutil.copy2(file, target / file.name)
    for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'BRANDING.md']:
        shutil.copy2(ROOT / name, target / name)


def portable_python(target):
    artifact = LOCK['python']
    archive = media.download(**artifact)
    with tarfile.open(archive) as tar:
        tar.extractall(target.parent, filter='data')
    python = target / 'bin/python3'
    wheels = CACHE / 'wheels'
    wheels.mkdir(exist_ok=True)
    machine = 'x86_64' if LOCK['arch'] == 'x64' else 'arm64'
    wheel_platform = f'macosx_{LOCK["minimum_macos"].replace(".", "_")}_{machine}'
    run(python, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:', '--dest', wheels,
        '--platform', wheel_platform,
        '-r', DESKTOP / 'requirements-macos.lock', env={**os.environ, 'PIP_DISABLE_PIP_VERSION_CHECK': '1'})
    run(python, '-m', 'pip', 'install', '--no-index', '--find-links', wheels,
        '-r', DESKTOP / 'requirements-macos.lock', env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    run(python, '-m', 'pip', 'check')
    # Preserve installed metadata/licenses, remove only reproducible bytecode.
    for folder in target.rglob('__pycache__'):
        shutil.rmtree(folder)
    return python


def rename_app(app):
    contents = app / 'Contents'
    plist = contents / 'Info.plist'
    info = plistlib.loads(plist.read_bytes())
    minimum = max(tuple(map(int, str(info.get('LSMinimumSystemVersion', '12.0')).split('.'))),
                  tuple(map(int, LOCK['minimum_macos'].split('.'))))
    version = LOCK['version'].split('-')[0]
    info.update(CFBundleName='FrameWeaver', CFBundleDisplayName='FrameWeaver',
                CFBundleIdentifier='com.frameweaver.desktop', CFBundleExecutable='FrameWeaver',
                CFBundleShortVersionString=version, CFBundleVersion=LOCK['bundle_version'],
                LSMinimumSystemVersion='.'.join(map(str, minimum)),
                NSHumanReadableCopyright='FrameWeaver contributors. Includes open source software; see Third Party Notices.',
                CFBundleURLTypes=[])
    # Avoid inherited Electron document associations.
    info.pop('CFBundleDocumentTypes', None)
    plist.write_bytes(plistlib.dumps(info))
    (contents / 'MacOS/Electron').rename(contents / 'MacOS/FrameWeaver')
    for helper in (contents / 'Frameworks').glob('Electron Helper*.app'):
        helper_plist = helper / 'Contents/Info.plist'
        data = plistlib.loads(helper_plist.read_bytes())
        old = data.get('CFBundleExecutable', helper.stem)
        new = old.replace('Electron', 'FrameWeaver')
        data.update(CFBundleExecutable=new, CFBundleName=new, CFBundleDisplayName=new,
                    CFBundleIdentifier='com.frameweaver.desktop.helper' + ('.' + old.split('(')[1].rstrip(')').lower() if '(' in old else ''),
                    CFBundleShortVersionString=version, CFBundleVersion=LOCK['bundle_version'])
        helper_plist.write_bytes(plistlib.dumps(data))
        (helper / 'Contents/MacOS' / old).rename(helper / 'Contents/MacOS' / new)
        helper.rename(helper.with_name(helper.name.replace('Electron', 'FrameWeaver')))
    return info['LSMinimumSystemVersion']


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as handle:
        return handle.read(4) in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')


def sign(app, identity):
    base = ['codesign', '--force', '--sign', identity]
    if identity != '-':
        base += ['--options', 'runtime', '--timestamp']
    binaries = sorted((p for p in app.rglob('*') if macho(p)), key=lambda p: len(p.parts), reverse=True)
    machine = 'x86_64' if LOCK['arch'] == 'x64' else 'arm64'
    # Removing inherited signatures is necessary after renaming helper bundles.
    for binary in binaries:
        run('lipo', binary, '-verify_arch', machine, stdout=subprocess.DEVNULL)
        # Signing a bundle's main executable also validates its nested bundles.
        # Defer those executables until the inside-out bundle pass below.
        if binary.parent.name == 'MacOS' and binary.parent.parent.name == 'Contents':
            continue
        run(*base, '--entitlements', DESKTOP / 'entitlements.plist', binary, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    bundles = sorted((p for p in app.rglob('*') if not p.is_symlink() and p.suffix in {'.app', '.framework'}), key=lambda p: len(p.parts), reverse=True)
    for bundle in [*bundles, app]:
        run(*base, '--entitlements', DESKTOP / 'entitlements.plist', bundle, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', app)


def source_archive(destination):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        for name, url, sha in media.SOURCES:
            source = media.download(name, url, sha)
            out.write(source, 'media-sources/' + name)
        for name in ['media-build.py', 'runtime-lock.json', 'README.md']:
            out.write(DESKTOP / name, name)
        out.writestr('media-sources/SHA256SUMS', ''.join(f'{sha}  {name}\n' for name, _, sha in media.SOURCES))


def save_receipt(state):
    temporary = RECEIPT.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(RECEIPT)


def notarize(file, phase, args):
    """Persist Apple's receipt before waiting, so a timeout never needs re-upload."""
    state = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    key = f'{phase}_submission'
    if args.resume == phase:
        entry = state[key]
        submission = entry['id']
        if entry.get('sha256') and entry['sha256'] != digest(file):
            raise RuntimeError('Submitted archive changed; restore it before resuming.')
    else:
        result = run('xcrun', 'notarytool', 'submit', file, '--keychain-profile', args.notary_profile,
                     '--output-format', 'json', stdout=subprocess.PIPE, text=True)
        submission = json.loads(result.stdout)['id']
        if phase == 'app':
            state.pop('dmg_submission', None)
        state[key] = {'id': submission, 'sha256': digest(file),
                      'submitted_at': datetime.now(timezone.utc).isoformat(), 'status': 'In Progress'}
        state.update(application='FrameWeaver', version=LOCK['version'], arch=LOCK['arch'],
                     profile=args.notary_profile, identity=args.identity, app=str(BUILD / 'FrameWeaver.app'))
        if phase == 'app':
            state['archive'] = str(file)
        save_receipt(state)
    print(f'Apple {phase} submission: {submission}', flush=True)
    try:
        run('xcrun', 'notarytool', 'wait', submission, '--keychain-profile', args.notary_profile, '--timeout', '30m')
    except subprocess.CalledProcessError:
        # A wait timeout does not cancel Apple's job. Read the authoritative
        # status before deciding whether this is pending, rejected or accepted.
        pass
    result = run('xcrun', 'notarytool', 'info', submission, '--keychain-profile', args.notary_profile,
                 '--output-format', 'json', stdout=subprocess.PIPE, text=True)
    status = json.loads(result.stdout)['status']
    state[key]['status'] = status
    save_receipt(state)
    if status == 'In Progress':
        raise RuntimeError(f'Apple is still processing {submission}. Continue later with --resume {phase}.')
    if status != 'Accepted':
        report = CACHE / f'notary-{phase}-{submission}.json'
        run('xcrun', 'notarytool', 'log', submission, '--keychain-profile', args.notary_profile, report)
        raise RuntimeError(f'Apple returned {status}; see {report}')


def build(args):
    global BUILD, RELEASE
    configure_target(args.arch)
    if args.build_dir:
        BUILD = args.build_dir.resolve()
    if args.release_dir:
        RELEASE = args.release_dir.resolve()
    if platform.system() != 'Darwin':
        raise RuntimeError('Build this package on macOS.')
    if args.notary_profile and (not args.identity or args.identity == '-'):
        raise RuntimeError('Notarization requires a Developer ID Application identity.')
    if args.resume and (not args.notary_profile or not args.identity):
        raise RuntimeError('Resume requires the original identity and Keychain profile.')
    CACHE.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    RELEASE.mkdir(parents=True, exist_ok=True)
    release_file('RELEASE-READY.txt').unlink(missing_ok=True)
    if args.resume:
        app = BUILD / 'FrameWeaver.app'
        manifest = json.loads((app / 'Contents/Resources/runtime/BUILD-MANIFEST.json').read_text())
        if not manifest['signed'] or manifest['version'] != LOCK['version'] or manifest['arch'] != LOCK['arch']:
            raise RuntimeError('Resume requires the original signed app for this version.')
        run('codesign', '--verify', '--deep', '--strict', app)
        finish_distribution(args, app, manifest)
        return
    app = BUILD / 'FrameWeaver.app'
    snapshot = None
    if args.from_app:
        source = args.from_app.resolve()
        if source == app.resolve():
            raise RuntimeError('Frozen source app must differ from the build destination.')
        original_manifest = source / 'Contents/Resources/runtime/BUILD-MANIFEST.json'
        original = json.loads(original_manifest.read_text())
        if original['version'] != LOCK['version'] or not original['signed']:
            raise RuntimeError('Frozen source must be a signed bundle of this version.')
        run('codesign', '--verify', '--deep', '--strict', source)
        snapshot = {'version': original['version'], 'arch': original['arch'],
                    'manifest_sha256': digest(original_manifest), 'payload_sha256': payload_digest(source)}
    if not args.skip_web and not args.from_app:
        run('npm', 'run', 'build', cwd=ROOT / 'dashboard')
    ffmpeg = media.build(LOCK['arch'])
    if app.exists():
        shutil.rmtree(app)
    artifact = LOCK['electron']
    electron = media.download(**artifact)
    run('ditto', '-x', '-k', electron, BUILD)
    (BUILD / 'Electron.app').rename(app)
    minimum = rename_app(app)
    resources = app / 'Contents/Resources'
    (resources / 'default_app.asar').unlink(missing_ok=True)
    runtime = resources / 'runtime'
    runtime.mkdir()
    if args.from_app:
        copy_snapshot(args.from_app.resolve(), app)
        if payload_digest(app) != snapshot['payload_sha256']:
            raise RuntimeError('Frozen application payload changed during copy.')
    else:
        wrapper = resources / 'app'
        wrapper.mkdir()
        for name in ['main.cjs', 'preload.cjs', 'package.json', 'startup.html', 'startup.cjs']:
            shutil.copy2(DESKTOP / name, wrapper / name)
        copy_code(runtime / 'app')
        shutil.copytree(ROOT / 'dashboard/dist', runtime / 'web')
        shutil.copytree(ROOT / 'extension', runtime / 'extension', ignore=shutil.ignore_patterns('*.pem', '*.crx', '__pycache__', '_metadata', '.DS_Store'))
        shutil.copy2(DESKTOP / 'HUONG-DAN-MACOS.html', runtime / 'HUONG-DAN-MACOS.html')
    shutil.copytree(ffmpeg, runtime / 'media')
    python = portable_python(runtime / 'python')
    font_dir = runtime / 'fonts'
    font_dir.mkdir(exist_ok=True)
    font_artifacts = LOCK['fonts']
    font_archives = [media.download(**item) for item in font_artifacts]
    with zipfile.ZipFile(font_archives[0]) as archive:
        for filename in ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSerif.ttf', 'DejaVuSansMono.ttf']:
            member = next(n for n in archive.namelist() if n.endswith('/ttf/' + filename))
            (font_dir / filename).write_bytes(archive.read(member))
        license_name = next(n for n in archive.namelist() if n.endswith('/LICENSE'))
        (font_dir / 'DejaVu-LICENSE.txt').write_bytes(archive.read(license_name))
    for file in font_archives[1:]:
        shutil.copy2(file, font_dir / file.name)
    if not args.from_app:
        run(python, DESKTOP / 'icon.py', BUILD / 'FrameWeaver.iconset')
        run('iconutil', '-c', 'icns', BUILD / 'FrameWeaver.iconset', '-o', resources / 'electron.icns')
        shutil.rmtree(BUILD / 'FrameWeaver.iconset')
    for file in [runtime / 'media/bin/ffmpeg', runtime / 'media/bin/ffprobe']:
        # No build-machine dylib paths may escape into the release.
        deps = '\n'.join(subprocess.check_output(['otool', '-L', str(file)], text=True).splitlines()[1:])
        if '/opt/homebrew' in deps or '/Users/' in deps:
            raise RuntimeError(f'Nonportable media binary: {file}')
        run(file, '-version', stdout=subprocess.DEVNULL)
    env = {**os.environ, 'PYTHONPATH': str(runtime / 'app'), 'PYTHONDONTWRITEBYTECODE': '1'}
    with tempfile.TemporaryDirectory(prefix='frameweaver-import-check-') as temp:
        check_env = {**env, 'FRAMEWEAVER_DESKTOP': '1', 'FRAMEWEAVER_DATA_DIR': temp,
                     'FRAMEWEAVER_WEB_DIR': str(runtime / 'web')}
        run(python, '-c', 'import agent.main; from agent.services import editing; '
            'assert len(editing.fonts()) == 5; print("All API routers and 5 fonts OK")', env=check_env)
    if snapshot and payload_digest(app) != snapshot['payload_sha256']:
        raise RuntimeError('Frozen application payload changed before signing.')
    manifest = {'application': 'FrameWeaver', 'version': LOCK['version'], 'arch': LOCK['arch'], 'minimum_macos': minimum,
                'runtime_assets': [LOCK['electron'], LOCK['python'], *LOCK['fonts']],
                'media_sources': [{'name': n, 'url': u, 'sha256': s} for n, u, s in media.SOURCES],
                'wheels': {p.name: digest(p) for p in sorted((CACHE / 'wheels').glob('*.whl'))},
                'signed': bool(args.identity), 'notarized': False}
    if snapshot:
        manifest['source_snapshot'] = snapshot
    (runtime / 'BUILD-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    sign(app, args.identity or '-')
    finish_distribution(args, app, manifest)


def finish_distribution(args, app, manifest):
    label = f'FrameWeaver-{LOCK["version"]}-macOS-{LOCK["arch"]}'
    if args.notary_profile:
        if args.resume != 'dmg':
            archive_dir = getattr(args, 'archive_dir', None) or BUILD
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive = archive_dir / f'{label}.zip'
            if args.resume != 'app':
                run('ditto', '-c', '-k', '--keepParent', app, archive)
            notarize(archive, 'app', args)
        run('xcrun', 'stapler', 'staple', app)
        run('spctl', '--assess', '--type', 'execute', '--verbose=2', app)
        if args.resume != 'dmg':
            archive.unlink()
        manifest['notarized'] = True
    dmg = RELEASE / f'{label}{"" if manifest["notarized"] else "-preview"}.dmg'
    if args.resume != 'dmg':
        dmg.unlink(missing_ok=True)
        # Include only the app and the standard Applications link in the disk image.
        image_source = BUILD / 'image'
        if image_source.exists(): shutil.rmtree(image_source)
        image_source.mkdir()
        (image_source / 'Applications').symlink_to('/Applications')
        # APFS clones avoid a second full-size app on a space-constrained build Mac.
        run('/bin/cp', '-cR', app, image_source / 'FrameWeaver.app')
        run('hdiutil', 'create', '-volname', 'FrameWeaver', '-srcfolder', image_source,
            '-format', 'UDZO', '-ov', dmg)
        shutil.rmtree(image_source)
        if args.identity:
            run('codesign', '--force', '--sign', args.identity, '--timestamp', dmg)
    if args.notary_profile:
        notarize(dmg, 'dmg', args)
        run('xcrun', 'stapler', 'staple', dmg)
        run('xcrun', 'stapler', 'validate', dmg)
    sources = RELEASE / f'{label}-media-sources.zip'
    source_archive(sources)
    shutil.copy2(DESKTOP / 'RELEASE-NOTES-vi.md', release_file('RELEASE-NOTES-vi.md'))
    manifest.update(files={p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in [dmg, sources]})
    (RELEASE / f'{label}-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    release_file('SHA256SUMS.txt').write_text(''.join(f'{v["sha256"]}  {n}\n' for n, v in manifest['files'].items()))
    marker = release_file('RELEASE-READY.txt')
    marker.unlink(missing_ok=True)
    if manifest['notarized']:
        marker.write_text('Developer ID signed, notarized and stapled. Upload the DMG, media sources, manifest, checksums and release notes together.\n')
    print(json.dumps({'app': str(app), 'dmg': str(dmg), 'release_ready': manifest['notarized'], 'bytes': dmg.stat().st_size}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=['arm64', 'x64'], default='arm64')
    parser.add_argument('--from-app', type=Path, help='Reuse code/UI/assets from this signed frozen bundle, replacing native runtimes only')
    parser.add_argument('--build-dir', type=Path, help='Keep this build in a separate folder, preserving previous apps')
    parser.add_argument('--release-dir', type=Path, help='Write installers and release metadata to this folder')
    parser.add_argument('--archive-dir', type=Path, help='Store temporary notarization ZIPs here (can be an external drive); reuse for --resume app')
    parser.add_argument('--skip-web', action='store_true')
    parser.add_argument('--identity', help='Developer ID Application certificate name from Keychain')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile name (not a password)')
    parser.add_argument('--resume', choices=['app', 'dmg'], help='Continue the saved submission without rebuilding or re-uploading')
    build(parser.parse_args())
