"""Run a fresh, sandboxed OpenCode conversation against a test instance."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["baseline", "protected"])
parser.add_argument("label")
parser.add_argument("--long", action="store_true")
parser.add_argument("--stress", action="store_true")
parser.add_argument("--timeout", type=int, default=240)
parser.add_argument("--copy-placeholders", action="store_true")
parser.add_argument("--deny-env", action="store_true")
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
ROOT = args.root.resolve()
METADATA = json.loads((ROOT / "metadata.json").read_text())
run = ROOT / "runs" / args.label
run.mkdir(parents=True, exist_ok=False)
for area in ["config", "cache", "data", "state"]:
    (run / "xdg" / area).mkdir(parents=True, exist_ok=True)
config = {
    "model": "lab/kimi-k3:cloud",
    "small_model": "lab/kimi-k3:cloud",
    "share": "disabled",
    "provider": {
        "lab": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": f"http://127.0.0.1:{METADATA['gateway_port'] if args.mode == 'baseline' else METADATA['proxy_port']}/v1",
                "apiKey": "e2e-local",
                "timeout": 150000,
            },
            "models": {"kimi-k3:cloud": {"name": "Synthetic e2e"}},
        }
    },
    "agent": {"build": {"steps": 6}},
    "permission": {
        "*": "deny",
        "read": {"*": "allow", "*.env": "allow", "*.env.*": "allow"},
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "external_directory": "deny",
    },
    "mcp": {},
}
if args.deny_env:
    config["permission"]["read"]["*.env"] = "deny"
    config["permission"]["read"]["*.env.*"] = "deny"
config_path = run / "opencode.json"
config_path.write_text(json.dumps(config))
env = {
    key: value
    for key, value in os.environ.items()
    if key in ["PATH", "HOME", "USER", "LANG", "TMPDIR", "TERM", "SHELL"]
}
for variable, area in [
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_STATE_HOME", "state"),
]:
    env[variable] = str(run / "xdg" / area)
env.update(
    {
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_DISABLE_CLAUDE_CODE": "true",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
        "OPENCODE_DISABLE_MODELS_FETCH": "true",
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_SHARE": "true",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
    }
)
prompt = (
    "Use the read tool to read logs/verbose.log completely. Identify the database port mismatch. "
    "Reply with DB_PORT_EXPECTED and the support email, without reproducing passwords or API keys."
    if args.long
    else "Use the read tool to read .env, logs/build.log, and customer.json. "
    "Diagnose why the app cannot connect to the database. Reply with APP_PORT, DB_PORT_EXPECTED, "
    "and the support email. Do not reproduce passwords or API keys. All data in this workspace is synthetic."
)
if args.stress:
    prompt = (
        "Read logs/batch-1.log, logs/batch-2.log, logs/batch-3.log, and logs/batch-4.log "
        "completely using the read tool. Identify the database port mismatch. Reply with "
        "DB_PORT_EXPECTED and the support email, without reproducing passwords or API keys."
    )
if args.copy_placeholders:
    prompt += (
        " If the support email is represented by a placeholder such as <EMAIL_ADDRESS_1>, "
        "copy the actual placeholder from the tool result verbatim into your answer. "
        "Never invent a replacement email."
    )
prompt += " Experiment ID: " + args.label
command = [
    "/usr/bin/sandbox-exec",
    "-f",
    str(ROOT / "client.sb"),
    METADATA["opencode"],
    "run",
    "--pure",
    "--agent",
    "build",
    "--format",
    "json",
    "--title",
    "Synthetic e2e " + args.label,
    "-m",
    "lab/kimi-k3:cloud",
    prompt,
]
start = time.monotonic()
with (run / "stdout.jsonl").open("w") as stdout, (run / "stderr.log").open("w") as stderr:
    child = subprocess.Popen(
        command,
        cwd=ROOT / "workspace",
        env=env,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    timed_out = False
    try:
        exit_code = child.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        exit_code = child.returncode
end = time.monotonic()
events = []
for line in (run / "stdout.jsonl").read_text().splitlines():
    try:
        events.append(json.loads(line))
    except ValueError:
        pass
tools = []
answers = []
errors = []
for event in events:
    if event.get("type") == "tool_use":
        part = event.get("part", {})
        tools.append({"name": part.get("tool"), "state": part.get("state", {})})
    if event.get("type") == "text":
        answers.append(event.get("part", {}).get("text", ""))
    if event.get("type") == "error":
        errors.append(event)
result = {
    "mode": args.mode,
    "label": args.label,
    "long": args.long,
    "start": start,
    "end": end,
    "stress": args.stress,
    "copy_placeholders": args.copy_placeholders,
    "deny_env": args.deny_env,
    "wall_seconds": round(end - start, 3),
    "exit_code": exit_code,
    "timed_out": timed_out,
    "event_count": len(events),
    "tool_calls": tools,
    "answers": answers,
    "errors": errors,
}
(run / "result.json").write_text(json.dumps(result, indent=2))
print(
    json.dumps(
        {
            k: v
            for k, v in result.items()
            if k not in ["start", "end", "answers", "tool_calls", "errors"]
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "tools": [{"name": t["name"], "status": t["state"].get("status")} for t in tools],
            "answer_count": len(answers),
            "error_count": len(errors),
        }
    )
)
