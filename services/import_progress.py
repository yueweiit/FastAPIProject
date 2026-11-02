"""Per-request progress stream; no shared job registry or database schema."""
import asyncio
import json


# Keep accepted imports alive if the browser disconnects. Each runner owns its
# database session; closing a progress view is not a request to undo an import.
_running_imports: set[asyncio.Task] = set()


class ImportProgress:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=1)

    def publish(self, event):
        # A slow/disconnected browser must never hold an inventory transaction.
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(event)

    async def update(self, stage, processed=None, total=None):
        self.publish({"type": "progress", "stage": stage,
                      "processed": processed, "total": total})
        await asyncio.sleep(0)

    async def stream(self, runner):
        task = asyncio.create_task(runner(self))
        _running_imports.add(task)
        task.add_done_callback(_running_imports.discard)
        while True:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=10)
            except asyncio.TimeoutError:
                event = {"type": "keepalive"}
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] == "result":
                break
