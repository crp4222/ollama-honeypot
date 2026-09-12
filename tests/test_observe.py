import base64
import json

from honeypot.observe import ConversationView, safe


def viewer():
    blocks = []
    return ConversationView(emit=lambda rid, kind, text: blocks.append((rid, kind, text))), blocks


def feed(view, data, rid="r1", split=0):
    pieces = [data] if not split else [data[i:i + split] for i in range(0, len(data), split)]
    for part in pieces:
        view.show({"event": "upstream_chunk", "request_id": rid, "data_b64": base64.b64encode(part).decode()})


def done(view, rid="r1", complete=True):
    view.show({"event": "end", "request_id": rid, "complete": complete, "reason": "upstream_eof" if complete else "timeout"})


def incoming(view, messages, rid="r1"):
    view.show({"event": "incoming", "request_id": rid, "path": "/v1/chat/completions",
               "body": json.dumps({"messages": messages})})


def sse(*events):
    return "".join("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n" for event in events).encode()


def test_openai_fragments_become_one_thinking_block_and_one_answer():
    view, blocks = viewer()
    incoming(view, [{"role": "system", "content": "SYSTEM SECRET"}, {"role": "user", "content": "Question ?"}])
    events = [{"id": "hidden-id", "object": "chat.completion.chunk", "system_fingerprint": "hidden-fingerprint",
               "choices": [{"delta": {"content": "", "reasoning": part}, "finish_reason": None}]}
              for part in (" de", " ré", "ponse", " END", "POINT", "_OK")]
    events += [{"choices": [{"delta": {"content": "Réponse"}}]},
               {"choices": [{"delta": {"content": " complète"}, "finish_reason": "stop"}]}]
    feed(view, sse(*events) + b'data: [DONE]\r\n\r\n', split=1)
    done(view)
    assert blocks == [("r1", "QUESTION", "Question ?"),
                      ("r1", "THINKING", " de réponse ENDPOINT_OK"),
                      ("r1", "RÉPONSE", "Réponse complète")]


def test_fragmented_openai_tool_names_arguments_and_parallel_calls():
    view, blocks = viewer()
    events = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_", "arguments": '{"pa'}},
                                                   {"index": 1, "id": "call_2", "function": {"name": "ls", "arguments": '{'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '"path":"."}'}},
                                                   {"index": 0, "function": {"name": "file", "arguments": 'th":"café.txt"}'}}]}, "finish_reason": "tool_calls"}]},
    ]
    feed(view, sse(*events), split=7)
    done(view)
    assert [(kind, json.loads(text)) for _, kind, text in blocks] == [
        ("APPEL OUTIL · read_file (call_1)", {"path": "café.txt"}),
        ("APPEL OUTIL · ls (call_2)", {"path": "."}),
    ]


def test_anthropic_thinking_text_tool_and_tool_result():
    view, blocks = viewer()
    events = [
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Je "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "réfléchis."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "HIDDEN"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tool_1", "name": "Read", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"file_'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": 'path":"demo.py"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_stop"},
    ]
    feed(view, b': keepalive\r\n\r\n' + sse(*events), split=3)
    done(view)
    incoming(view, [
        {"role": "user", "content": "Ancienne question"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool_1", "name": "Read", "input": {"file_path": "demo.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": [{"type": "text", "text": "print('bonjour')"}]}]},
    ], rid="r2")
    assert blocks[0] == ("r1", "THINKING", "Je réfléchis.")
    assert blocks[1][1] == "APPEL OUTIL · Read (tool_1)"
    assert json.loads(blocks[1][2]) == {"file_path": "demo.py"}
    assert blocks[2] == ("r2", "RÉSULTAT OUTIL · Read", "print('bonjour')")
    assert len(blocks) == 3


def test_native_ndjson_and_generate():
    for records in ([{"message": {"thinking": "Ré"}}, {"message": {"thinking": "flexion"}},
                     {"message": {"content": "Bon"}}, {"message": {"content": "jour"}, "done": True}],
                    [{"thinking": "Réflexion", "response": ""}, {"response": "Bonjour", "done": True}]):
        view, blocks = viewer()
        feed(view, b''.join(json.dumps(record, ensure_ascii=False).encode() + b'\n' for record in records), split=2)
        done(view)
        assert blocks == [("r1", "THINKING", "Réflexion"), ("r1", "RÉPONSE", "Bonjour")]


def test_non_stream_json_and_native_tool_arguments():
    formats = [
        {"choices": [{"message": {"content": "Salut", "reasoning_content": "Penser"}, "finish_reason": "stop"}]},
        {"type": "message", "content": [{"type": "thinking", "thinking": "Penser"}, {"type": "text", "text": "Salut"}]},
        {"message": {"thinking": "Penser", "content": "Salut"}, "done": True},
    ]
    for record in formats:
        view, blocks = viewer()
        feed(view, json.dumps(record).encode(), split=5)
        done(view)
        assert blocks == [("r1", "THINKING", "Penser"), ("r1", "RÉPONSE", "Salut")]
    view, blocks = viewer()
    feed(view, json.dumps({"message": {"tool_calls": [{"function": {"name": "read", "arguments": {"path": "x"}}}]}, "done": True}).encode())
    done(view)
    assert blocks[0][1] == "APPEL OUTIL · read"
    assert json.loads(blocks[0][2]) == {"path": "x"}


def test_context_is_not_repeated_and_openai_tool_results_are_named():
    view, blocks = viewer()
    incoming(view, [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "ancienne question"},
        {"role": "assistant", "content": "ancienne réponse", "tool_calls": [{"id": "call1", "function": {"name": "list_files", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call1", "content": "a.py\nb.py"},
        {"role": "user", "content": "Lis a.py"},
    ])
    assert blocks == [("r1", "RÉSULTAT OUTIL · list_files", "a.py\nb.py"), ("r1", "QUESTION", "Lis a.py")]


def test_interleaved_requests_do_not_mix_text():
    view, blocks = viewer()
    feed(view, sse({"choices": [{"delta": {"content": "Bon"}}]}), rid="one")
    feed(view, sse({"choices": [{"delta": {"content": "Hel"}}]}), rid="two")
    feed(view, sse({"choices": [{"delta": {"content": "jour"}, "finish_reason": "stop"}]}), rid="one")
    feed(view, sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}), rid="two")
    done(view, "one")
    done(view, "two")
    assert blocks == [("one", "RÉPONSE", "Bonjour"), ("two", "RÉPONSE", "Hello")]


def test_noise_hidden_but_real_errors_visible():
    view, blocks = viewer()
    for kind in ("startup", "policy", "upstream_request", "upstream_response"):
        view.show({"event": kind, "request_id": "r1", "body": "secret", "status": 200})
    assert not blocks
    feed(view, b'{"error":{"message":"Quota atteint"}}')
    done(view, complete=False)
    assert blocks == [("r1", "ERREUR", "Quota atteint")]


def test_exit_flushes_unterminated_sse_and_preserves_partial_tool_arguments():
    view, blocks = viewer()
    feed(view, b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{\\"path\\":"}}]}}]}')
    view.finish()
    assert blocks == [("r1", "APPEL OUTIL · read", '{"path":')]


def test_terminal_escape_sequences_are_not_interpreted(capsys):
    ConversationView.print_block("r1", "QUESTION", '\x1b[2Jhello\x07')
    assert '\x1b' not in capsys.readouterr().out
    assert safe('\x1b[31m') == '\\u001b[31m'


def test_malformed_optional_tool_fields_do_not_break_following_questions():
    view, blocks = viewer()
    incoming(view, [{"role": "assistant", "tool_calls": [{"id": "bad", "function": None}]},
                    {"role": "tool", "tool_call_id": ["bad"], "name": 3, "content": "result"},
                    {"role": "user", "content": "Question valide"}])
    feed(view, sse({"choices": [None, {"delta": {"tool_calls": "bad", "content": "Réponse valide"}}]}))
    done(view)
    assert blocks[-2:] == [("r1", "QUESTION", "Question valide"), ("r1", "RÉPONSE", "Réponse valide")]
