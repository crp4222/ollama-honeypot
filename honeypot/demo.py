"""Clearly labelled offline responses for testing capture, never real inference."""
import json
import httpx


def demo_response(request):
    body = json.loads(request.content)
    answer = "Réponse simulée du laboratoire : aucune requête n'a été envoyée à Ollama Cloud."
    model = body["model"]
    if request.url.path == "/v1/messages":
        result = {"id": "msg_demo", "type": "message", "role": "assistant", "model": model,
                  "content": [{"type": "text", "text": answer}], "stop_reason": "end_turn",
                  "usage": {"input_tokens": 0, "output_tokens": 0}}
        if body["stream"]:
            start = {**result, "content": [], "stop_reason": None}
            events = [
                ("message_start", {"type": "message_start", "message": start}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 0}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            return httpx.Response(200, text="".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events), headers={"content-type": "text/event-stream"})
    elif request.url.path == "/v1/chat/completions":
        result = {"id": "chatcmpl_demo", "object": "chat.completion", "created": 0, "model": model,
                  "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}
        if body["stream"]:
            chunk = {**result, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}]}
            end = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    else:
        result = {"model": model, "done": True, "done_reason": "stop", "eval_count": 0}
        if request.url.path == "/api/generate":
            result["response"] = answer
        else:
            result["message"] = {"role": "assistant", "content": answer}
        if body["stream"]:
            return httpx.Response(200, text=json.dumps(result) + "\n", headers={"content-type": "application/x-ndjson"})
    return httpx.Response(200, json=result)
