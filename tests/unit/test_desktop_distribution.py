"""Desktop data isolation and the production dashboard's API boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.web import mount_dashboard


def test_desktop_configuration_does_not_write_into_source(tmp_path):
    root = Path(__file__).resolve().parents[2]
    originals = {name: (root / 'agent' / name).read_bytes() for name in ('models.json', 'providers.json')}
    code = '''
import json
from agent import config
from agent.api import models, providers, active_project
from agent.services import omni_flash, propainter_trial
from agent.paths import DATA_DIR, RESOURCE_DIR
assert config.BASE_DIR == DATA_DIR
assert config.OUTPUT_DIR == DATA_DIR / 'output'
assert propainter_trial.CACHE.is_relative_to(DATA_DIR)
assert models._MODELS_FILE == omni_flash._MODELS_FILE == config._MODELS_FILE
for p in [models._MODELS_FILE, providers._PROVIDERS_FILE, active_project._STATE_FILE]:
    assert p.is_relative_to(DATA_DIR)
    assert not p.is_relative_to(RESOURCE_DIR)
data = providers._read()
data['desktop_test'] = True
providers._write(data)
active_project._write_state({'project_id': 'test'})
print(json.dumps({'data': str(DATA_DIR)}))
'''
    env = {**os.environ, 'FRAMEWEAVER_DESKTOP': '1', 'FRAMEWEAVER_DATA_DIR': str(tmp_path / 'Data with spaces'),
           'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, check=True, text=True, capture_output=True)
    assert Path(json.loads(result.stdout)['data']).is_dir()
    for name, content in originals.items():
        assert (root / 'agent' / name).read_bytes() == content
    # Starting again must preserve the user's changes.
    target = tmp_path / 'Data with spaces/config/providers.json'
    assert json.loads(target.read_text())['desktop_test'] is True
    subprocess.run([sys.executable, '-c', 'from agent import config'], cwd=root, env=env, check=True)
    assert json.loads(target.read_text())['desktop_test'] is True


def test_production_spa_preserves_api_and_file_boundaries(tmp_path):
    (tmp_path / 'index.html').write_text('<title>FrameWeaver</title>')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets/app.js').write_text('console.log("ok")')
    app = FastAPI()

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    mount_dashboard(app, tmp_path)
    with TestClient(app) as client:
        assert client.get('/projects/example').status_code == 200
        assert client.get('/health').json() == {'status': 'ok'}
        assert client.get('/assets/app.js').text == 'console.log("ok")'
        assert client.get('/api/missing').status_code == 404
        assert client.get('/assets/missing.js').status_code == 404
        assert client.get('/missing.mp4').status_code == 404
        assert client.get('/%2e%2e/secret.txt').status_code == 404
