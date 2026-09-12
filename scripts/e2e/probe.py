"""Test-only instrumentation. Logs request bodies; use synthetic data only."""

import contextvars
import json
import sys
import time
from pathlib import Path

ROLE = sys.argv[1]
ROOT = Path(sys.argv[2]).resolve()
METADATA = json.loads((ROOT / "metadata.json").read_text())
request_number = contextvars.ContextVar("request_number", default=None)


def record(event, **values):
    item = {"event": event, "mono": time.monotonic(), "request": request_number.get(), **values}
    with (ROOT / (ROLE + "-metrics.jsonl")).open("a") as output:
        output.write(json.dumps(item) + "\n")


if ROLE == "gateway":
    sys.path.insert(0, METADATA["honeypot"])
    from honeypot.app import Settings, create_app

    app = create_app(
        Settings(
            mode="local",
            upstream=METADATA["ollama_url"],
            prompt_file=ROOT / "system.txt",
            data_dir=ROOT / "gateway-captures",
            per_day=0,
            total=0,
            max_output=2048,
            timeout=120,
        )
    )
    port = METADATA["gateway_port"]
else:
    sys.path.insert(0, METADATA["privaite"])
    from privaite.pii.engine import PIIEngine

    original = PIIEngine.process_request

    async def timed_process(self, *args, **kwargs):
        start = time.monotonic()
        try:
            return await original(self, *args, **kwargs)
        finally:
            record("pii_processing", duration=time.monotonic() - start)

    PIIEngine.process_request = timed_process
    from privaite.app import create_app

    app = create_app()
    port = METADATA["proxy_port"]


class Observe:
    def __init__(self, inner):
        self.inner = inner
        self.number = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.inner(scope, receive, send)
        self.number += 1
        token = request_number.set(self.number)
        start = time.monotonic()
        record("start", path=scope["path"])
        raw = bytearray()
        first_body = True

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.request":
                raw.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    record("request_body", bytes=len(raw), body=raw.decode("utf-8"))
            return message

        async def observed_send(message):
            nonlocal first_body
            if message["type"] == "http.response.start":
                record("headers", status=message["status"], elapsed=time.monotonic() - start)
            if message["type"] == "http.response.body" and message.get("body"):
                if first_body:
                    record("first_body", elapsed=time.monotonic() - start)
                    first_body = False
            await send(message)

        try:
            await self.inner(scope, observed_receive, observed_send)
        except BaseException as error:
            record("error", kind=type(error).__name__)
            raise
        finally:
            record("end", elapsed=time.monotonic() - start)
            request_number.reset(token)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(Observe(app), host="127.0.0.1", port=port, access_log=False, log_level="warning")
