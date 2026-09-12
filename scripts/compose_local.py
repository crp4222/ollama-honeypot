#!/usr/bin/env python3
"""Validate the host binding before invoking Docker Compose, with no shell."""
import argparse
import os
import subprocess
from pathlib import Path
from serve import local_address

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description="Lancer les conteneurs sur loopback ou une IP LAN précise.")
parser.add_argument("--bind", type=local_address, default="127.0.0.1")
parser.add_argument("--port", type=int, default=11435)
parser.add_argument("--mode", choices=["demo", "local", "cloud"], default="local")
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.error("Port invalide.")
key_path = ROOT / "private/ollama_api_key"
if args.mode == "cloud" and (not key_path.exists() or not key_path.read_text().strip()):
    parser.error("Exécute d'abord python scripts/set_key.py pour fournir une clé dédiée.")
binding = f"[{args.bind}]" if ":" in args.bind else args.bind
env = {**os.environ, "BIND_HOST": binding, "PORT": str(args.port), "HONEYPOT_MODE": args.mode}
command = ["docker", "compose", "-f", "compose.yaml"]
if args.mode == "cloud":
    command.extend(["-f", "compose.cloud.yaml"])
subprocess.run([*command, "up", "--build", "--force-recreate", "-d"], cwd=ROOT, env=env, check=True)
