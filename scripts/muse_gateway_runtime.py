"""Run the pinned optional gateway with recoverable browser readiness.

Upstream reconnects CDP on generation, but FrameWeaver checks /readyz first. Recover
that idle connection during readiness, without submitting a generation request.
"""
import asyncio
from contextvars import ContextVar
import logging
import time

log = logging.getLogger('frameweaver.muse_gateway')
_reply_snapshot = ContextVar('frameweaver_muse_reply', default=None)


class FinalChatSnapshot:
    async def _state(self, tab):
        state = await super()._state(tab)
        capture = _reply_snapshot.get()
        if capture is not None:
            capture['text'] = state.get('lastText', '')
        return state

    async def chat_stream(self, account, req):
        # DOM output can be rewritten while generating. Concatenating upstream
        # deltas then corrupts JSON; expose only the final observed reply.
        capture, emitted = {}, False
        token = _reply_snapshot.set(capture)
        try:
            async for _ in super().chat_stream(account, req):
                emitted = True
            if not emitted or req.cancel and req.cancel.is_set():
                return
            text = capture.get('text', '').strip()
            if not text:
                from muse2api.errors import UpstreamError
                raise UpstreamError('Muse completed without a readable final reply')
            yield text
        finally:
            _reply_snapshot.reset(token)


class RecoveringBrowserHealth:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._health_reconnect_lock = asyncio.Lock()
        self._health_retry_after = 0.0

    async def startup(self):
        try:
            await super().startup()
        except BaseException:
            # ASGI does not call shutdown after failed startup. Close the private
            # browser here so a timeout/cancellation cannot leave its port busy.
            await super().shutdown()
            raise

    async def health(self):
        state = await super().health()
        if state.get('ok'):
            return state
        async with self._health_reconnect_lock:
            state = await super().health()
            if state.get('ok') or time.monotonic() < self._health_retry_after:
                return state
            try:
                # Reuse upstream's serialized reconnect and stale-context cleanup.
                await asyncio.wait_for(self._browser_session(), timeout=25)
            except Exception as exc:
                self._health_retry_after = time.monotonic() + 10
                log.warning('Browser readiness recovery failed (%s)', type(exc).__name__)
            return await super().health()


def main():
    import uvicorn
    from muse2api.app import create_app
    from muse2api.config import get_settings
    from muse2api.drivers.browser.driver import BrowserDriver
    from muse2api.log import setup_logging

    class LocalBrowserDriver(FinalChatSnapshot, RecoveringBrowserHealth, BrowserDriver):
        pass

    settings = get_settings()
    if settings.driver != 'browser':
        raise SystemExit('FrameWeaver gateway requires the browser driver.')
    setup_logging(settings.log_level)
    app = create_app(settings, driver=LocalBrowserDriver(settings))
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == '__main__':
    main()
