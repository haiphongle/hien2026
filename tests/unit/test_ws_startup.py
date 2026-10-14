"""A free API port alone must not conceal a failed extension listener."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent import main
from agent.db import crud


async def test_extension_bind_failure_aborts_api_startup_and_closes_db(monkeypatch):
    close = AsyncMock()
    worker = SimpleNamespace(start=AsyncMock(), drain=AsyncMock(), request_shutdown=Mock())
    monkeypatch.setattr(main, 'init_db', AsyncMock())
    monkeypatch.setattr(main, 'close_db', close)
    monkeypatch.setattr(crud, 'list_materials', AsyncMock(return_value=[]))
    monkeypatch.setattr(main, 'init_sdk', Mock())
    monkeypatch.setattr(main, 'get_flow_client', Mock())
    monkeypatch.setattr(main, 'get_worker_controller', Mock(return_value=worker))
    monkeypatch.setattr(main.websockets, 'serve', AsyncMock(side_effect=OSError('port already in use')))
    with pytest.raises(OSError, match='port already'):
        async with main.lifespan(main.app):
            pytest.fail('API must not report a successful startup')
    close.assert_awaited_once()
    worker.start.assert_not_called()
