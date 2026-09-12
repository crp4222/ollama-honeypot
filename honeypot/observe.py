"""Semantic terminal rendering of captured Ollama, OpenAI and Anthropic traffic."""
import base64
import codecs
import json
from collections import OrderedDict

from .policy import INFERENCE_PATHS


def safe(text):
    return "".join(c if c in "\n\t" or c.isprintable() else f"\\u{ord(c):04x}" for c in str(text))


def readable(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return "" if content is None else readable(content)


def mapping(value):
    return value if isinstance(value, dict) else {}


def sequence(value):
    return value if isinstance(value, list) else []


class WireDecoder:
    """HTTP chunks are not SSE events, JSON records, or UTF-8 character boundaries."""
    def __init__(self, consume, fail):
        self.consume, self.fail = consume, fail
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.buffer = ""
        self.sse = None
        self.data_lines = []
        self.closed = False

    def _event(self):
        if not self.data_lines:
            return
        data = "\n".join(self.data_lines)
        self.data_lines.clear()
        if data.strip() == "[DONE]":
            self.consume(None)
            return
        try:
            self.consume(json.loads(data))
        except (ValueError, RecursionError):
            self.fail("Événement de réponse incomplet ou illisible.")

    def feed(self, data=b"", final=False):
        if self.closed:
            return
        self.buffer += self.decoder.decode(data, final=final)
        if self.sse is None:
            start = self.buffer.lstrip()
            if not start:
                return
            if start[0] in "{[":
                self.sse = False
            elif start[0] in "de:ir":
                self.sse = True
            elif not final:
                return
        if self.sse:
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                self._line(line.rstrip("\r"))
            if final:
                if self.buffer:
                    self._line(self.buffer.rstrip("\r"))
                self.buffer = ""
                self._event()
        else:
            decoder = json.JSONDecoder()
            while self.buffer.strip():
                self.buffer = self.buffer.lstrip()
                try:
                    value, end = decoder.raw_decode(self.buffer)
                except (ValueError, RecursionError):
                    if final:
                        self.fail("Réponse JSON incomplète ou illisible.")
                    break
                self.buffer = self.buffer[end:]
                self.consume(value)
        self.closed = final

    def _line(self, line):
        if not line:
            self._event()
        elif line.startswith("data:"):
            self.data_lines.append(line[5:].removeprefix(" "))


class Exchange:
    def __init__(self, rid, emit):
        self.rid, self.emit = rid, emit
        self.kind = None
        self.parts = []
        self.tools = OrderedDict()
        self.failed = False
        self.wire = WireDecoder(self.consume, self.error)

    def error(self, message):
        self.flush()
        self.emit(self.rid, "ERREUR", readable(message))
        self.failed = True

    def text(self, kind, value):
        if not isinstance(value, str) or not value:
            return
        if kind != self.kind:
            self.flush_text()
            self.kind = kind
        self.parts.append(value)

    def flush_text(self):
        if self.parts:
            value = "".join(self.parts)
            if value.strip():
                self.emit(self.rid, self.kind, value)
        self.kind, self.parts = None, []

    def tool(self, key, call, fragmented=False):
        self.flush_text()
        item = self.tools.setdefault(key, {"name": "", "id": "", "input": {}, "parts": []})
        function = mapping(call.get("function", call))
        for field, source in (("id", call.get("id")), ("name", function.get("name"))):
            if isinstance(source, str) and source:
                if fragmented and item[field] != source:
                    item[field] += source
                else:
                    item[field] = source
        arguments = function.get("arguments", call.get("input"))
        if isinstance(arguments, str):
            item["parts"].append(arguments)
        elif arguments is not None:
            item["input"] = arguments

    def flush_tool(self, key):
        item = self.tools.pop(key, None)
        if item is None:
            return
        arguments = item["input"]
        if item["parts"]:
            arguments = "".join(item["parts"])
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass  # Preserve partial arguments when a stream is interrupted.
        label = "APPEL OUTIL · " + (item["name"] or "outil inconnu")
        if item["id"]:
            label += " (" + item["id"] + ")"
        self.emit(self.rid, label, readable(arguments))

    def flush(self):
        self.flush_text()
        for key in list(self.tools):
            self.flush_tool(key)

    def message(self, message, namespace="message", fragmented=False):
        if not isinstance(message, dict):
            return
        for field in ("thinking", "reasoning", "reasoning_content"):
            if message.get(field):
                self.text("THINKING", text_content(message[field]))
                break
        content = message.get("content")
        if isinstance(content, list):
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    self.text("RÉPONSE", block.get("text"))
                elif kind == "thinking":
                    self.text("THINKING", block.get("thinking"))
                elif kind == "tool_use":
                    self.tool((namespace, index), block)
                elif kind == "redacted_thinking":
                    self.text("THINKING", "[non fourni par le modèle]")
        else:
            self.text("RÉPONSE", content)
        for index, call in enumerate(sequence(message.get("tool_calls"))):
            if isinstance(call, dict):
                self.tool((namespace, call.get("index", index)), call, fragmented)
        if isinstance(message.get("function_call"), dict):
            self.tool((namespace, "legacy"), message["function_call"], fragmented)

    def consume(self, event):
        if event is None:
            self.flush()
            return
        if not isinstance(event, dict):
            return
        if event.get("error"):
            detail = event["error"]
            self.error(detail.get("message", detail) if isinstance(detail, dict) else detail)
            return
        kind = event.get("type")
        if kind == "content_block_start":
            block = mapping(event.get("content_block"))
            index = event.get("index", 0)
            if block.get("type") == "tool_use":
                self.tool(("anthropic", index), block)
            elif block.get("type") == "thinking":
                self.text("THINKING", block.get("thinking"))
            elif block.get("type") == "text":
                self.text("RÉPONSE", block.get("text"))
        elif kind == "content_block_delta":
            delta = mapping(event.get("delta"))
            if delta.get("type") == "text_delta":
                self.text("RÉPONSE", delta.get("text"))
            elif delta.get("type") == "thinking_delta":
                self.text("THINKING", delta.get("thinking"))
            elif delta.get("type") == "input_json_delta":
                self.tool(("anthropic", event.get("index", 0)), {"arguments": delta.get("partial_json", "")}, True)
        elif kind == "content_block_stop":
            self.flush_text()
            self.flush_tool(("anthropic", event.get("index", 0)))
        elif kind == "message_stop":
            self.flush()
        elif kind == "message_start":
            self.message(event.get("message", {}))
        elif kind == "message":
            self.message(event)
            self.flush()
        elif "choices" in event:
            for index, choice in enumerate(sequence(event["choices"])):
                if not isinstance(choice, dict):
                    continue
                self.message(choice.get("delta") or choice.get("message") or {},
                             namespace=("choice", choice.get("index", index)), fragmented="delta" in choice)
                if choice.get("finish_reason"):
                    self.flush()
        elif "message" in event or "response" in event:
            self.message(event.get("message", {}), namespace="ollama")
            self.text("THINKING", event.get("thinking"))
            self.text("RÉPONSE", event.get("response"))
            if event.get("done"):
                self.flush()

    def finish(self):
        self.wire.feed(final=True)
        self.flush()


class ConversationView:
    def __init__(self, emit=None, history=False):
        self.emit = emit or self.print_block
        self.history = history
        self.active = OrderedDict()

    @staticmethod
    def print_block(rid, label, content):
        print(f"\n[{safe(rid[:10])}] {safe(label)}\n{safe(content)}", flush=True)

    def exchange(self, rid):
        if rid not in self.active:
            if len(self.active) >= 128:
                _, oldest = self.active.popitem(last=False)
                oldest.finish()
            self.active[rid] = Exchange(rid, self.emit)
        return self.active[rid]

    def incoming(self, rid, body):
        if not isinstance(body, dict):
            return
        self.exchange(rid)
        if isinstance(body.get("prompt"), str) and body["prompt"]:
            self.emit(rid, "QUESTION", body["prompt"])
            return
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            return
        # Find names for tool results in the context, without reprinting that context.
        names = {}
        for message in messages:
            if not isinstance(message, dict):
                continue
            for call in sequence(message.get("tool_calls")):
                if isinstance(call, dict) and isinstance(call.get("id"), str):
                    name = mapping(call.get("function")).get("name")
                    names[call["id"]] = name if isinstance(name, str) else ""
            if isinstance(message.get("content"), list):
                for block in message["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        name = block.get("name")
                        names[block["id"]] = name if isinstance(name, str) else ""
        if not self.history:
            last = max((i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "assistant"), default=-1)
            messages = messages[last + 1:]
        for message in messages:
            if not isinstance(message, dict) or message.get("role") in ("system", "developer"):
                continue
            role = message.get("role")
            content = message.get("content")
            if role == "tool":
                ident = message.get("tool_call_id", "")
                ident = ident if isinstance(ident, str) else ""
                name = message.get("name") or message.get("tool_name") or names.get(ident) or ident
                name = name if isinstance(name, str) else ""
                self.emit(rid, "RÉSULTAT OUTIL" + (" · " + name if name else ""), text_content(content))
            elif role == "assistant":
                prior = Exchange(rid, self.emit)
                prior.message(message)
                prior.flush()
            elif role == "user":
                text = text_content(content)
                if text.strip():
                    self.emit(rid, "QUESTION", text)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            ident = block.get("tool_use_id", "")
                            ident = ident if isinstance(ident, str) else ""
                            name = names.get(ident) or ident
                            label = "RÉSULTAT OUTIL" + (" · " + name if name else "")
                            if block.get("is_error"):
                                label += " [échec]"
                            self.emit(rid, label, text_content(block.get("content")))

    def show(self, event):
        rid, kind = event.get("request_id"), event.get("event")
        if not rid:
            return
        if kind == "incoming" and event.get("path") in INFERENCE_PATHS:
            try:
                self.incoming(rid, json.loads(event.get("body", "")))
            except (ValueError, TypeError):
                pass
        elif kind == "upstream_chunk":
            exchange = self.exchange(rid)
            try:
                exchange.wire.feed(base64.b64decode(event["data_b64"], validate=True))
            except (ValueError, KeyError):
                exchange.error("Morceau de capture illisible.")
        elif kind in {"end", "blocked"}:
            exchange = self.active.pop(rid, None)
            if exchange:
                exchange.finish()
                if not exchange.failed and (kind == "blocked" or not event.get("complete")):
                    self.emit(rid, "ERREUR", event.get("reason", "Échange interrompu."))

    def finish(self):
        for exchange in self.active.values():
            exchange.finish()
        self.active.clear()
