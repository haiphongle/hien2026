"""One isolated model process per task; no GPU memory retained between tasks."""
import asyncio
import json
from agent.services.process_control import kill_tree, spawn_options


class ProPainterSession:
    def __init__(self, python, worker, source):
        self.command = [str(python), '-u', str(worker), '--source', str(source), '--serve']
        self.process = None

    async def close(self):
        proc, self.process = self.process, None
        if proc is None:
            return
        if proc.returncode is None:
            kill_tree(proc.pid)
        await proc.wait()

    async def run(self, work, width, height, dilation, on_line, timeout=300):
        """The enclosing session lock serializes requests and owns cancellation."""
        try:
            if self.process is None:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    **spawn_options(), limit=1024 * 1024)
            proc = self.process
            tail = ''

            async def consume():
                nonlocal tail
                proc.stdin.write((json.dumps(dict(work=str(work), width=width, height=height, dilation=dilation)) + '\n').encode())
                await proc.stdin.drain()
                while line := await proc.stdout.readline():
                    text = line.decode(errors='replace').strip()
                    tail = (tail + '\n' + text)[-4000:]
                    if text.startswith('FLOWKIT_DONE:'):
                        result = json.loads(text.partition(':')[2])
                        if not result.get('ok'):
                            raise RuntimeError(f'ProPainter không xử lý được vùng đã chọn: {result.get("error", tail)}')
                        return
                    on_line(text)
                raise RuntimeError(f'ProPainter đã dừng trước khi hoàn tất: {tail[-1200:]}')

            try:
                await asyncio.wait_for(consume(), timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f'ProPainter quá thời gian chờ ({timeout}s). Bấm chạy lại để tiếp tục phần còn thiếu.') from exc
        except BaseException:
            # Includes cancellation: never leave inference running after the UI stops.
            await self.close()
            raise
