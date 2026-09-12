import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from honeypot.app import Settings, create_app
from honeypot.policy import CLOUD_MODEL, InvalidRequest, normalize
from scripts.serve import local_address


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


class PolicyTests(unittest.TestCase):
    def test_generate_cannot_override_template_system_or_model(self):
        body = {"model": "evil:cloud", "prompt": "Bonjour", "system": "OVERRIDE", "template": "BYPASS",
                "raw": True, "context": [1, 2], "options": {"num_predict": 1000000, "num_ctx": 1000000}}
        effective, policy = normalize("/api/generate", body, "FIXED SYSTEM", 128)
        self.assertEqual(effective, {"model": CLOUD_MODEL, "prompt": "Bonjour", "system": "FIXED SYSTEM",
                                     "raw": False, "stream": True, "options": {"num_predict": 128, "temperature": 0.2}})
        self.assertEqual(policy["removed_system_messages"], 1)

    def test_all_chat_protocols_remove_privileged_client_messages(self):
        for path in ("/api/chat", "/v1/chat/completions", "/v1/messages"):
            with self.subTest(path=path):
                effective, _ = normalize(path, {"model": "other", "system": "OVERRIDE", "messages": [
                    {"role": "system", "content": "BYPASS"}, {"role": "developer", "content": "BYPASS"},
                    {"role": "user", "content": "bonjour"}]}, "FIXED SYSTEM", 128)
                self.assertNotIn("BYPASS", json.dumps(effective))
                self.assertNotIn("OVERRIDE", json.dumps(effective))
                self.assertEqual(effective["model"], CLOUD_MODEL)
                self.assertEqual(effective.get("system") or effective["messages"][0]["content"], "FIXED SYSTEM")

    def test_tools_preserve_roundtrip_without_server_execution(self):
        body = {"messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "test", "input": {"value": "hello"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "done"}]}],
            "tools": [{"name": "test", "input_schema": {"type": "object"}}]}
        effective, _ = normalize("/v1/messages", body, "SYSTEM", 128)
        self.assertEqual(effective["messages"], body["messages"])
        self.assertEqual(effective["tools"], body["tools"])

    def test_hosted_tools_and_remote_images_rejected(self):
        for fields in ({"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
                       {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "http://127.0.0.1"}}]}]}):
            with self.subTest(fields=fields), self.assertRaises(InvalidRequest):
                normalize("/v1/messages", {"messages": [{"role": "user", "content": "Hi"}], **fields}, "SYSTEM", 128)

    def test_no_wildcard_or_public_listener(self):
        import argparse
        for value in ("0.0.0.0", "::", "8.8.8.8", "100.64.0.1", "localhost", "192.0.2.2"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                local_address(value)
        for value in ("127.0.0.1", "192.168.1.10", "10.0.0.2", "172.16.1.1", "::1"):
            self.assertEqual(local_address(value), value)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prompt = self.root / "system.txt"
        self.prompt.write_text("SYSTEME FIXE DE TEST")
        self.key = self.root / "key"
        self.key.write_text("dedicated-secret-key")
        self.calls = []
        self.reply = lambda request: httpx.Response(200, json={"message": {"role": "assistant", "content": "bonjour"}, "done": True})

        async def upstream(request):
            self.calls.append(request)
            return self.reply(request)

        self.settings = Settings(mode="cloud", prompt_file=self.prompt, key_file=self.key,
                                 data_dir=self.root / "captures", per_day=50, total=100, max_output=128)
        self.transport = httpx.MockTransport(upstream)
        await self.start()

    async def start(self):
        self.app = create_app(self.settings, transport=self.transport)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://lab")

    async def stop(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def asyncTearDown(self):
        await self.stop()
        self.tmp.cleanup()

    def events(self):
        return [json.loads(line) for line in (self.settings.data_dir / "events.jsonl").read_text().splitlines()]

    async def chat(self, **extra):
        return await self.client.post("/api/chat", json={"model": "other-model", "messages": [{"role": "user", "content": "hello"}], **extra})

    async def test_request_response_and_auth_capture(self):
        original = {"model": "other", "system": "OVERRIDE", "messages": [{"role": "user", "content": "test"}], "stream": False}
        response = await self.client.post("/v1/messages", json=original, headers={"Authorization": "Bearer visitor-secret", "x-api-key": "visitor-secret", "Cookie": "private-cookie"})
        self.assertEqual(response.status_code, 200)
        call = self.calls[0]
        effective = json.loads(call.content)
        self.assertEqual(effective["model"], CLOUD_MODEL)
        self.assertEqual(effective["system"], "SYSTEME FIXE DE TEST")
        self.assertEqual(call.headers["authorization"], "Bearer dedicated-secret-key")
        self.assertNotIn("x-api-key", call.headers)
        self.assertNotIn("cookie", call.headers)
        events = self.events()
        self.assertEqual(json.loads(next(e["body"] for e in events if e["event"] == "incoming")), original)
        self.assertEqual(next(e["body"] for e in events if e["event"] == "upstream_request"), effective)
        logged_response = b"".join(base64.b64decode(e["data_b64"]) for e in events if e["event"] == "upstream_chunk")
        self.assertEqual(logged_response, response.content)
        for secret in ("dedicated-secret-key", "visitor-secret", "private-cookie"):
            self.assertNotIn(secret, json.dumps(events))

    async def test_stream_is_captured_losslessly_including_split_unicode(self):
        content = 'data: {"text":"été"}\n\ndata: [DONE]\n\n'.encode()
        stream = ByteStream([content[:17], content[17:18], content[18:]])
        self.reply = lambda request: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
        response = await self.client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(response.content, content)
        captured = b"".join(base64.b64decode(e["data_b64"]) for e in self.events() if e["event"] == "upstream_chunk")
        self.assertEqual(captured, content)
        self.assertTrue(stream.closed)
        self.assertTrue(self.events()[-1]["complete"])

    async def test_management_routes_and_query_strings_never_reach_cloud(self):
        for path in ("/api/pull", "/api/push", "/api/create", "/api/delete", "/api/copy", "/api/blobs/abc", "/api/web_search", "/admin", "/captures/events.jsonl", "/v1/responses", "/api/chat?url=https://example.com"):
            with self.subTest(path=path):
                response = await self.client.post(path, json={"model": "anything"})
                self.assertIn(response.status_code, (400, 404))
        self.assertEqual(self.calls, [])

    async def test_only_one_model_discoverable(self):
        tags = (await self.client.get("/api/tags")).json()
        self.assertEqual([m["name"] for m in tags["models"]], ["kimi-k3:cloud"])
        show = (await self.client.post("/api/show", json={"model": "other"})).json()
        self.assertNotIn("SYSTEME FIXE", json.dumps(show))
        self.assertEqual(self.calls, [])

    async def test_invalid_and_oversize_requests_never_reach_cloud(self):
        for body in (b'{"model":"a","model":"b"}', b'{"a":NaN}', b'[]', b'{broken', b'{' * 2000):
            response = await self.client.post("/api/chat", content=body)
            self.assertEqual(response.status_code, 400)
        response = await self.client.post("/api/chat", content=b'x' * (self.settings.max_body + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])

    async def test_quota_survives_restart(self):
        self.app.state.quota.total = 1
        self.assertEqual((await self.chat()).status_code, 200)
        self.assertEqual((await self.chat()).status_code, 429)
        await self.stop()
        self.settings.total = 1
        await self.start()
        self.assertEqual((await self.chat()).status_code, 429)
        self.assertEqual(len(self.calls), 1)

    async def test_unlimited_quotas_after_exhaustion_and_restart(self):
        self.app.state.quota.per_day = self.app.state.quota.total = 1
        self.assertEqual((await self.chat()).status_code, 200)
        self.assertEqual((await self.chat()).status_code, 429)
        await self.stop()
        self.settings.per_day = self.settings.total = 0
        await self.start()
        for _ in range(3):
            self.assertEqual((await self.chat()).status_code, 200)
        self.assertEqual(len(self.calls), 4)
        count = self.app.state.quota.db.execute("SELECT SUM(n) FROM counts").fetchone()[0]
        self.assertEqual(count, 4)

    async def test_unlimited_daily_quota_keeps_total_limit(self):
        self.app.state.quota.per_day, self.app.state.quota.total = 0, 1
        self.assertEqual((await self.chat()).status_code, 200)
        self.assertEqual((await self.chat()).status_code, 429)
        self.assertEqual(len(self.calls), 1)

    async def test_unlimited_total_quota_keeps_daily_limit(self):
        self.app.state.quota.per_day, self.app.state.quota.total = 1, 0
        self.assertEqual((await self.chat()).status_code, 200)
        self.assertEqual((await self.chat()).status_code, 429)
        self.assertEqual(len(self.calls), 1)

    async def test_negative_quotas_rejected(self):
        for per_day, total in ((-1, 1), (1, -1)):
            with self.subTest(per_day=per_day, total=total):
                with self.assertRaisesRegex(ValueError, "quotas"):
                    create_app(Settings(per_day=per_day, total=total))

    async def test_no_redirect_and_upstream_errors_kept_private(self):
        self.reply = lambda request: httpx.Response(302, text="account-detail", headers={"location": "https://evil.example"})
        response = await self.chat()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("account-detail", response.text)
        self.assertNotIn("location", response.headers)
        self.assertEqual(len(self.calls), 1)

    async def test_capture_failure_stops_inference(self):
        with patch.object(self.app.state.audit, "event", side_effect=OSError("disk full")):
            self.assertEqual((await self.chat()).status_code, 503)
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.chat()).status_code, 503)

    async def test_timeout_closes_upstream_and_releases_slot(self):
        self.settings.timeout = 0.02
        stream = ByteStream([b'{"message":{}}\n'], delay=0.1)
        self.reply = lambda request: httpx.Response(200, stream=stream)
        response = await self.chat(stream=True)
        self.assertIn("error", response.text)
        self.assertTrue(stream.closed)
        self.assertFalse(self.events()[-1]["complete"])
        self.reply = lambda request: httpx.Response(200, json={"done": True})
        self.assertEqual((await self.chat()).status_code, 200)

    async def test_response_limit_closes_upstream(self):
        self.settings.max_response = 8
        stream = ByteStream([b'a' * 32])
        self.reply = lambda request: httpx.Response(200, stream=stream)
        response = await self.chat(stream=True)
        self.assertIn("error", response.text)
        self.assertTrue(stream.closed)
        self.assertEqual(self.events()[-1]["reason"], "response_size_limit")

    async def test_non_stream_failure_is_not_a_successful_partial_json(self):
        self.settings.max_response = 8
        self.reply = lambda request: httpx.Response(200, stream=ByteStream([b'x' * 32]))
        response = await self.chat(stream=False)
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json())

    async def test_disconnected_client_closes_upstream_and_releases_slot(self):
        from starlette.requests import ClientDisconnect
        stream = ByteStream([b'{"message":{}}\n', b'{"done":true}\n'])
        self.reply = lambda request: httpx.Response(200, stream=stream)
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/chat",
                 "raw_path": b'/api/chat', "query_string": b'', "root_path": "", "headers": [],
                 "server": ("127.0.0.1", 11435), "client": ("127.0.0.1", 40000)}

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        with self.assertRaises(ClientDisconnect):
            await self.app(scope, receive, send)
        self.assertTrue(stream.closed)
        self.assertEqual(self.app.state.slots._value, self.settings.max_concurrent)

    async def test_concurrent_request_limit_rejects_without_forwarding(self):
        for _ in range(self.settings.max_concurrent):
            await self.app.state.slots.acquire()
        try:
            self.assertEqual((await self.chat()).status_code, 429)
            self.assertEqual(self.calls, [])
        finally:
            for _ in range(self.settings.max_concurrent):
                self.app.state.slots.release()

    async def test_offline_mode_does_not_need_key(self):
        cfg = Settings(prompt_file=self.prompt, key_file=self.root / "missing", data_dir=self.root / "offline")
        app = create_app(cfg)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://lab") as client:
                response = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hello"}]})
        self.assertIn("simulée", response.json()["message"]["content"])

    async def test_local_ollama_uses_cloud_tag_without_reading_or_forwarding_keys(self):
        cfg = Settings(mode="local", upstream="http://relay:8081", prompt_file=self.prompt,
                       key_file=self.root / "nonexistent-key", data_dir=self.root / "local")
        app = create_app(cfg, transport=self.transport)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://lab") as client:
                for path in ("/api/chat", "/api/generate", "/v1/messages", "/v1/chat/completions"):
                    with self.subTest(path=path):
                        response = await client.post(path, json={"model": "other", "system": "OVERRIDE", "prompt": "Hello",
                            "messages": [{"role": "system", "content": "OVERRIDE"}, {"role": "user", "content": "hello"}], "stream": False},
                            headers={"Authorization": "Bearer client-key", "x-api-key": "client-key"})
                        self.assertEqual(response.status_code, 200)
                        actual = json.loads(self.calls[-1].content)
                        self.assertEqual(actual["model"], "kimi-k3:cloud")
                        self.assertNotIn("OVERRIDE", json.dumps(actual))
                        self.assertNotIn("authorization", self.calls[-1].headers)
                        self.assertNotIn("x-api-key", self.calls[-1].headers)
        events = [json.loads(line) for line in (cfg.data_dir / "events.jsonl").read_text().splitlines()]
        self.assertTrue(all(e["destination"].startswith("http://relay:8081/") for e in events if e["event"] == "upstream_request"))

    def test_local_backend_rejects_arbitrary_destinations(self):
        for url in ("https://ollama.com", "http://192.168.1.1", "http://127.0.0.1:22"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_app(Settings(mode="local", upstream=url))

    def test_local_environment_defaults_to_existing_loopback_ollama(self):
        with patch.dict("os.environ", {"HONEYPOT_MODE": "local"}, clear=True):
            cfg = Settings.from_env()
        self.assertEqual(cfg.upstream, "http://127.0.0.1:11434")


if __name__ == "__main__":
    unittest.main()
