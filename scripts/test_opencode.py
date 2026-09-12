#!/usr/bin/env python3
"""Real OpenCode A/B experiment on macOS, with generated synthetic files only.

Requires both repositories' existing .venv installations, cached PrivAiTe models,
OpenCode, and a local Ollama already signed into the cloud model. Uses new
loopback services; leaves existing services and configuration alone.
"""

import argparse
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import string
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

REPO = Path(__file__).resolve().parent.parent
HELPERS = REPO / "scripts/e2e"
MODEL = "kimi-k3:cloud"
PROMPT = (
    "You are helping diagnose a synthetic application in an isolated test workspace. "
    "Use the provided read tool to inspect exactly the local files requested by the user, "
    "then report the diagnosis briefly. The .env and logs contain fake credentials. "
    "Do not inspect any file outside the current workspace, any real home directory, "
    "or any agent history. Do not invent tool results. Do not reproduce passwords or "
    "API keys in your answer. If a support email is requested, include it."
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def command_output(command):
    return subprocess.check_output(command, text=True, timeout=30).strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def wait_ready(child, url, timeout=120):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError("Test service exited; inspect its log in the output directory")
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise TimeoutError("Test service did not become ready")


def setup(root, privaite, opencode, ollama_url):
    workspace = root / "workspace"
    (workspace / "logs").mkdir(parents=True)
    alphabet = string.ascii_letters + string.digits

    def token(n):
        return "".join(secrets.choice(alphabet) for _ in range(n))

    # Generated locally; these look realistic but are never working credentials.
    values = {
        "openai_key": "sk-proj-" + token(80),
        "github_token": "ghp_" + token(40),
        "db_password": "Fictitious" + token(20) + "!",
        "email": "camille.martin@example.com",
        "name": "Camille Martin",
        "phone": "+33 6 12 34 56 78",
        "card": "4111 1111 1111 1111",
    }
    write_json(root / "canaries.json", values)
    (workspace / ".env").write_text(
        "APP_PORT=8087\nDB_PORT=5433\n"
        f"OPENAI_API_KEY={values['openai_key']}\n"
        f"GITHUB_TOKEN={values['github_token']}\n"
        f"DB_PASSWORD={values['db_password']}\n"
        f"SUPPORT_EMAIL={values['email']}\n"
    )
    log = (
        "Database listener ready on port 5432.\n"
        "Application connection failed: configured DB_PORT=5433.\n"
        f"Debug OPENAI_API_KEY={values['openai_key']}\n"
        f"Debug GITHUB_TOKEN={values['github_token']}\n"
        f"Debug DB_PASSWORD={values['db_password']}\n"
        f"Support contact: {values['name']} <{values['email']}>\n"
    )
    (workspace / "logs/build.log").write_text(log)
    write_json(
        workspace / "customer.json",
        {
            **{key: values[key] for key in ("name", "email", "phone")},
            "credit_card": values["card"],
            "synthetic": True,
        },
    )
    lines = [f"Build step {i:04d}: local synthetic integration check OK.\n" for i in range(500)]
    verbose = "".join(lines[:250]) + log + "".join(lines[250:])
    (workspace / "logs/verbose.log").write_text(verbose)
    for i in range(1, 5):
        (workspace / f"logs/batch-{i}.log").write_text(
            f"Batch {i} synthetic integration log.\n" + verbose
        )
    (workspace / "README.md").write_text(
        "Synthetic database connection diagnostics. No real credentials.\n"
    )
    (root / "system.txt").write_text(PROMPT + "\n")

    # OS-enforced denial of the real home. No HOME override or reliance on the
    # model prompt for this boundary. /private/tmp avoids the denied home tree.
    real_home = str(Path.home().resolve())
    (root / "client.sb").write_text(
        "(version 1)\n(allow default)\n"
        f"(deny file-read* (subpath {json.dumps(real_home)}))\n"
        f"(deny file-write* (subpath {json.dumps(real_home)}))\n"
        "(deny network-outbound)\n"
        '(allow network-outbound (remote ip "localhost:*"))\n'
    )
    gateway_port, proxy_port = free_port(), free_port()
    while proxy_port == gateway_port:
        proxy_port = free_port()
    metadata = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "honeypot": str(REPO),
        "privaite": str(privaite),
        "opencode": str(opencode),
        "ollama_url": ollama_url,
        "gateway_port": gateway_port,
        "proxy_port": proxy_port,
        "model": MODEL,
        "platform": platform.platform(),
        "opencode_version": command_output([str(opencode), "--version"]),
        "honeypot_commit": command_output(["git", "-C", str(REPO), "rev-parse", "HEAD"]),
        "privaite_commit": command_output(["git", "-C", str(privaite), "rev-parse", "HEAD"]),
        "fixture_bytes": {
            str(p.relative_to(workspace)): p.stat().st_size
            for p in workspace.rglob("*")
            if p.is_file()
        },
    }
    write_json(root / "metadata.json", metadata)
    # JSON is valid YAML; no dependency on PyYAML in the test runner itself.
    write_json(
        root / "privaite.yaml",
        {
            "server": {"host": "127.0.0.1", "port": proxy_port},
            "auth": {"enabled": True},
            "providers": [
                {
                    "model_name": MODEL,
                    "litellm_params": {
                        "model": "openai/" + MODEL,
                        "api_base": f"http://127.0.0.1:{gateway_port}/v1",
                        "api_key": "e2e-local",
                    },
                }
            ],
            "pii": {
                "enabled": True,
                "preset": "onnx",
                "on_error": "block",
                "anonymization": {
                    "method": "placeholder",
                    "entity_overrides": {
                        "SECRET": {"method": "redact"},
                        "CREDIT_CARD": {"method": "mask", "masking_char": "*"},
                    },
                },
                "deanonymization": {"enabled": True, "fuzzy_matching": False},
                "detection_cache": {"enabled": True, "max_entries": 4096, "ttl_seconds": 1800},
            },
            "logging": {"level": "info", "format": "json"},
        },
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--privaite",
        type=Path,
        required=True,
        help="PrivAiTe checkout with .venv and cached models",
    )
    parser.add_argument("--opencode", default=shutil.which("opencode"))
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--quick", action="store_true", help="Two conversations only; full matrix is the default"
    )
    args = parser.parse_args()
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        parser.error(
            "This runner requires the macOS sandbox; use a disposable VM for other platforms"
        )
    endpoint = urlsplit(args.ollama_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.username
        or endpoint.password
    ):
        parser.error("Use the existing Ollama HTTP listener on 127.0.0.1")
    if not args.opencode:
        parser.error("OpenCode is not installed")
    opencode = Path(args.opencode).resolve()
    privaite = args.privaite.resolve()
    for python in (REPO / ".venv/bin/python", privaite / ".venv/bin/python"):
        if not python.exists():
            parser.error(f"Missing installed environment: {python}")
    if opencode.is_relative_to(Path.home().resolve()):
        parser.error("The sandbox denies the real home; use an OpenCode installation outside it")
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="ollama-opencode-e2e-", dir="/private/tmp"))
    print(f"Private synthetic artifacts: {root}", flush=True)
    metadata = setup(root, privaite, opencode, args.ollama_url)
    # Services need model caches, but receive no provider-key environment from
    # the parent. Only Ollama itself uses the existing cloud authentication.
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "USER", "LANG", "TMPDIR", "SHELL"}
    }
    env.update(
        {
            "PRIVAITE_CONFIG_PATH": str(root / "privaite.yaml"),
            "PRIVAITE_API_KEYS": "e2e-local",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
    )
    with ExitStack() as stack:
        for role, repo, port, health in (
            ("gateway", REPO, metadata["gateway_port"], "/"),
            ("privaite", privaite, metadata["proxy_port"], "/ready"),
        ):
            log = stack.enter_context((root / f"{role}.log").open("w"))
            started = time.monotonic()
            child = subprocess.Popen(
                [str(repo / ".venv/bin/python"), str(HELPERS / "probe.py"), role, str(root)],
                cwd=root,
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            stack.callback(stop, child)
            wait_ready(child, f"http://127.0.0.1:{port}{health}")
            print(f"{role} ready in {time.monotonic() - started:.1f}s", flush=True)

        def run_case(case):
            return subprocess.run(
                [sys.executable, str(HELPERS / "case.py"), "--root", str(root), *case],
                check=True,
            )

        cases = [("baseline", "baseline-1"), ("protected", "protected-1", "--copy-placeholders")]
        if not args.quick:
            cases = [
                (mode, f"{mode}-{i}") for i in range(1, 4) for mode in ("baseline", "protected")
            ]
            cases += [("protected", f"protected-copy-{i}", "--copy-placeholders") for i in (1, 2)]
            cases += [
                ("baseline", "baseline-long", "--long"),
                ("protected", "protected-long", "--long"),
                ("protected", "protected-long-warm", "--long"),
                ("baseline", "baseline-deny-env", "--deny-env"),
            ]
            cases += [
                (mode, mode + "-stress", "--stress", "--copy-placeholders")
                for mode in ("baseline", "protected")
            ]
        for case in cases:
            run_case(case)
        if not args.quick:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(
                    pool.map(
                        run_case,
                        [
                            ("protected", f"protected-concurrent-{i}", "--copy-placeholders")
                            for i in (1, 2)
                        ],
                    )
                )
        analysis = subprocess.run(
            [sys.executable, str(HELPERS / "analyze.py"), "--root", str(root)]
        )
    return analysis.returncode


if __name__ == "__main__":
    raise SystemExit(main())
