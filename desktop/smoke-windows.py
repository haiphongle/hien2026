"""Test the real Windows installer/runtime with empty accounts and isolated data.

No UI credentials, generation requests, paid media, or heavy inference models.
The CI runner's Chrome is used only by a private empty Muse browser profile.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import tempfile
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'release/windows-x64'
PORTS = (18118,19238,18615,19215)


def open_port(port):
    with socket.socket() as connection:
        connection.settimeout(.3)
        return connection.connect_ex(('127.0.0.1',port))==0


def wait_ports(ports, opened=True, timeout=90, process=None):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if all(open_port(p)==opened for p in ports): return
        if opened and process is not None and process.poll() is not None:
            raise RuntimeError(f'Desktop exited before startup: {process.returncode}; see electron-smoke.log')
        time.sleep(.3)
    raise TimeoutError(f'Test ports did not become {"ready" if opened else "closed"}: {ports}')


def runtime_checks(runtime, data, env):
    code=r'''
import asyncio,json,subprocess,uuid
from pathlib import Path
from agent.paths import DATA_DIR
from agent.services import editing,timeline,codex_install,codex_auth
from agent.models.timeline import TimelineDocument
async def checks():
    reports=[]
    for width,height in [(640,360),(360,640)]:
        source=DATA_DIR/f'source-{width}.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size={width}x{height}:rate=25:duration=2',
          '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(source)],check=True)
        vid=str(uuid.uuid4())
        asset=await timeline.register(vid,vid,'test','Tư liệu kiểm tra',source,'source')
        doc=timeline.initial(asset).model_dump(mode='json');tid=str(uuid.uuid4())
        doc['tracks'].append(dict(id=tid,name='Tiêu đề',kind='text'))
        doc['clips'].append(dict(id=str(uuid.uuid4()),track_id=tid,start=0,duration=2,text='Bình yên · 平安',caption=dict(size=8,box_width=.8,box_height=.3,x=.5,y=.5)))
        saved=timeline.save(vid,0,TimelineDocument.model_validate(doc))
        assert timeline.load(vid)==saved
        target=DATA_DIR/f'export-{width}.mp4'
        await timeline.render(vid,TimelineDocument.model_validate(saved['document']),target,lambda **kw:None)
        info=editing.probe(target)
        assert info['has_audio'] and abs(info['duration']-2)<.1 and (info['width'],info['height'])==(width,height)
        reports.append(info)
    installer=codex_install.CodexInstaller();installer.start();await installer.task
    assert installer.job['status']=='COMPLETED',installer.job
    assert codex_install.executable()
    async with codex_auth.CodexSession(codex_install.executable()) as session:
        account=await session.request('account/read')
        assert account.get('account') is None
    assert len(editing.fonts())==5
    print('REPORT:'+json.dumps(dict(renders=reports,codex=codex_install.VERSION,codex_account='signed_out',fonts=5)))
asyncio.run(checks())
'''
    command=[str(runtime/'python/python.exe'),'-c',code]
    result=subprocess.run(command,cwd=runtime/'app',env={**env,'FRAMEWEAVER_DESKTOP':'1',
        'FRAMEWEAVER_DATA_DIR':str(data),'PYTHONPATH':str(runtime/'app'),
        'PATH':str(runtime/'media/bin')+os.pathsep+env['PATH']},capture_output=True,text=True,encoding='utf-8',timeout=900)
    (RELEASE/'runtime-smoke.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Runtime export/Codex checks failed; see runtime-smoke.log')
    return json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith('REPORT:')))


def crash_and_reap(process):
    # Simulate the owning window crashing: the backend must exit gracefully from
    # its private stdin pipe / parent watcher and close its owned Muse browser.
    children=psutil.Process(process.pid).children(recursive=True)
    process.kill();process.wait(timeout=15)
    try:
        wait_ports(PORTS,False,timeout=30)
    finally:
        for child in reversed(children):
            try: child.kill()
            except psutil.NoSuchProcess: pass


def main():
    if os.name!='nt': raise RuntimeError('Run this smoke test on Windows x64.')
    assert not any(open_port(p) for p in PORTS),'Test ports already occupied'
    installer=next(RELEASE.glob('*-Windows-x64-Setup.exe'))
    with tempfile.TemporaryDirectory(prefix='FrameWeaver thử ') as work:
        work=Path(work)
        installed=work/'Ứng dụng có dấu';data=work/'Dữ liệu có dấu'
        # NSIS requires /D= to be the final unquoted command-line parameter.
        subprocess.run(f'"{installer}" /S /D={installed}',check=True,timeout=180)
        executable=installed/'FrameWeaver.exe';assert executable.is_file()
        runtime=installed/'resources/runtime'
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
             'FRAMEWEAVER_DESKTOP_TEST_DATA':str(data),
             'FRAMEWEAVER_DESKTOP_TEST_PORT':str(PORTS[0]),'FRAMEWEAVER_DESKTOP_TEST_WS_PORT':str(PORTS[1]),
             'FRAMEWEAVER_DESKTOP_TEST_MUSE_PORT':str(PORTS[2]),'FRAMEWEAVER_DESKTOP_TEST_MUSE_CDP_PORT':str(PORTS[3])}
        process=None
        electron_log=(RELEASE/'electron-smoke.log').open('wb')
        try:
            process=subprocess.Popen([str(executable),'--enable-logging=stderr'],env=env,
                                     stdout=electron_log,stderr=subprocess.STDOUT)
            wait_ports(PORTS[:2],process=process)
            assert 'Startup IPC accepted: ready' in (data/'desktop-startup.log').read_text(encoding='utf-8'), 'Startup bridge rejected the real local page'
            with httpx.Client(base_url=f'http://127.0.0.1:{PORTS[0]}',trust_env=False,timeout=15,
                              headers={'X-FlowKit-Local-Auth':'1'}) as client:
                health=client.get('/health').raise_for_status().json()
                assert health['status']=='ok' and not health['extension_connected']
                assert client.get('/settings').status_code==200
                client.patch('/api/muse',json={'base_url':f'http://127.0.0.1:{PORTS[2]}'}).raise_for_status()
                assert client.get('/api/muse/gateway').json()['state']=='MISSING'
                client.post('/api/muse/gateway/install').raise_for_status()
                try: wait_ports(PORTS[2:3],timeout=900)
                except TimeoutError:
                    print('Gateway status:',client.get('/api/muse/gateway').json());raise
                state=client.get('/api/muse/gateway').raise_for_status().json()
                if state['busy'] and not state['running']:
                    # The manager caches health for 3s; a newly bound TCP port
                    # can precede its next successful application health check.
                    time.sleep(4)
                    state=client.get('/api/muse/gateway').raise_for_status().json()
                assert state['installed'] and state['owned'] and state['running'],state
                settings=client.get('/api/muse').json();assert settings['enabled'] and settings['auto_start']
            marker=data/'.flowkit/muse-gateway/installed.json';stamp=marker.stat().st_mtime_ns
            report=runtime_checks(runtime,data,env)
            crash_and_reap(process);process=None
            process=subprocess.Popen([str(executable),'--enable-logging=stderr'],env=env,
                                     stdout=electron_log,stderr=subprocess.STDOUT)
            wait_ports(PORTS[:3],timeout=120,process=process)
            assert marker.stat().st_mtime_ns==stamp,'Gateway was unexpectedly reinstalled'
            crash_and_reap(process);process=None
            # Check the NSIS uninstall policy against the real default data path.
            default_data=Path(os.environ['APPDATA'])/'FrameWeaver'
            default_data.mkdir(exist_ok=True)
            sentinel=default_data/'windows-smoke-keep.txt';sentinel.write_text('keep',encoding='utf-8')
            uninstaller=next(installed.glob('Uninstall*.exe'))
            subprocess.run([str(uninstaller),'/S'],check=True,timeout=120)
            deadline=time.monotonic()+30
            while executable.exists() and time.monotonic()<deadline: time.sleep(.3)
            assert not executable.exists(),'Silent uninstall did not remove the application'
            assert sentinel.read_text(encoding='utf-8')=='keep'
            sentinel.unlink()
            report.update(platform='Windows',arch='x64',runner='GitHub Actions windows-2022',signed=False,
                installer_sha256=hashlib.file_digest(installer.open('rb'),'sha256').hexdigest(),
                installed_app_startup=True,unicode_paths=True,app_crash_cleanup=True,
                muse_fresh_install=True,muse_autostart_without_reinstall=True,muse_paid_generation=False,
                data_preserved_on_uninstall=True,physical_user_pc_tested=False,heavy_inference_tested=False)
            (RELEASE/'VERIFICATION-WINDOWS.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
            print(json.dumps(report,indent=2,ensure_ascii=False))
        finally:
            if process and process.poll() is None: crash_and_reap(process)
            electron_log.close()
            print('Installed executable:',executable,'Test data exists:',data.exists(),flush=True)
            if (data/'desktop-startup.log').is_file():
                shutil.copy2(data/'desktop-startup.log',RELEASE/'desktop-startup.log')
            if (data/'logs/backend.log').is_file():
                shutil.copy2(data/'logs/backend.log',RELEASE/'desktop-smoke.log')
            gateway_log=data/'.flowkit/muse-gateway/gateway.log'
            if gateway_log.is_file(): shutil.copy2(gateway_log,RELEASE/'gateway-smoke.log')


if __name__=='__main__': main()
