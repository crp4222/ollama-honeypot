"""Build every upstream request from an allowlist, never proxy arbitrary bodies."""
from copy import deepcopy

PUBLIC_MODEL = "kimi-k3:cloud"
CLOUD_MODEL = "kimi-k3"
INFERENCE_PATHS = {"/api/chat", "/api/generate", "/v1/chat/completions", "/v1/messages"}


class InvalidRequest(ValueError):
    pass


def output_limit(value, maximum):
    if value is None:
        return maximum
    if type(value) is not int or value < 1:
        raise InvalidRequest("Le nombre de tokens doit être un entier positif.")
    return min(value, maximum)


def validate_content(content):
    """Allow text and client-side tool exchanges; no URL/image fetching features."""
    if content is None or isinstance(content, str):
        return content
    if not isinstance(content, list) or len(content) > 256:
        raise InvalidRequest("Contenu invalide.")
    allowed = {"text", "tool_use", "tool_result", "thinking", "redacted_thinking"}
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in allowed:
            raise InvalidRequest("Cette démo accepte le texte et les échanges d'outils uniquement.")
        if block["type"] == "text" and not isinstance(block.get("text"), str):
            raise InvalidRequest("Bloc texte invalide.")
        if block["type"] == "tool_result":
            validate_content(block.get("content"))
    return deepcopy(content)


def messages_from(body, protocol):
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
        raise InvalidRequest("messages doit contenir entre 1 et 256 messages.")
    clean = []
    removed = 0
    roles = {"user", "assistant"} if protocol == "anthropic" else {"user", "assistant", "tool"}
    for message in messages:
        if not isinstance(message, dict):
            raise InvalidRequest("Message invalide.")
        role = message.get("role")
        if role in ("system", "developer"):
            removed += 1
            continue
        if role not in roles:
            raise InvalidRequest("Rôle de message non pris en charge.")
        item = {"role": role, "content": validate_content(message.get("content"))}
        if protocol != "anthropic":
            for key in ("tool_calls", "tool_call_id", "name", "tool_name", "thinking"):
                if key in message:
                    item[key] = deepcopy(message[key])
        clean.append(item)
    if not clean:
        raise InvalidRequest("Au moins un message non système est nécessaire.")
    return clean, removed


def tools_from(body, protocol):
    definitions = body.get("tools", [])
    if not isinstance(definitions, list) or len(definitions) > 64:
        raise InvalidRequest("Trop d'outils, ou format invalide.")
    clean = []
    for tool in definitions:
        if not isinstance(tool, dict):
            raise InvalidRequest("Définition d'outil invalide.")
        if protocol == "anthropic":
            if tool.get("type", "custom") != "custom" or not isinstance(tool.get("name"), str):
                raise InvalidRequest("Seuls les outils exécutés par le client sont acceptés.")
            clean.append({k: deepcopy(tool[k]) for k in ("name", "description", "input_schema") if k in tool})
        else:
            if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                raise InvalidRequest("Seuls les outils de type function sont acceptés.")
            function = tool["function"]
            if not isinstance(function.get("name"), str):
                raise InvalidRequest("Nom d'outil invalide.")
            clean.append({"type": "function", "function": {
                k: deepcopy(function[k]) for k in ("name", "description", "parameters") if k in function
            }})
    return clean


def normalize(path, body, system_prompt, max_tokens, model=CLOUD_MODEL):
    if not isinstance(body, dict):
        raise InvalidRequest("Un objet JSON est requis.")
    stream = body.get("stream", path.startswith("/api/"))
    if type(stream) is not bool:
        raise InvalidRequest("stream doit être un booléen.")
    protocol = "anthropic" if path == "/v1/messages" else "openai" if path.startswith("/v1/") else "ollama"
    clean = {"model": model, "stream": stream}
    removed = int("system" in body)
    if path == "/api/generate":
        if not isinstance(body.get("prompt"), str) or not body["prompt"]:
            raise InvalidRequest("prompt doit être un texte non vide.")
        clean.update(prompt=body["prompt"], system=system_prompt, raw=False)
    else:
        messages, removed_messages = messages_from(body, protocol)
        removed += removed_messages
        if protocol == "anthropic":
            clean.update(messages=messages, system=system_prompt)
        else:
            clean["messages"] = [{"role": "system", "content": system_prompt}, *messages]
        definitions = tools_from(body, protocol)
        if definitions:
            clean["tools"] = definitions
    if protocol == "ollama":
        options = body.get("options", {})
        if not isinstance(options, dict):
            raise InvalidRequest("options doit être un objet.")
        limit = output_limit(options.get("num_predict"), max_tokens)
        clean["options"] = {"num_predict": limit, "temperature": 0.2}
    else:
        requested = body.get("max_tokens", body.get("max_completion_tokens"))
        clean.update(max_tokens=output_limit(requested, max_tokens), temperature=0.2)
    # Client system/developer instructions, raw/template/context, model, URLs,
    # output schemas, tool_choice and all unknown top-level keys are never copied.
    return clean, {
        "requested_model": body.get("model"),
        "forced_model": model,
        "removed_system_messages": removed,
        "ignored_fields": sorted(set(body) - set(clean)),
    }
