#!/usr/bin/env python3
"""Explicit loopback/RFC1918 bindings only; never configure the router."""
import argparse
import ipaddress
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def local_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Utilise une adresse IP précise, pas un nom DNS.") from exc
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
    allowed = address == ipaddress.ip_address("::1") or any(address in ipaddress.ip_network(n) for n in networks)
    if not allowed:
        raise argparse.ArgumentTypeError("Cette version accepte seulement loopback ou une adresse LAN privée ; 0.0.0.0 et les IP publiques sont refusées.")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Honeypot Ollama local : aucun port WAN ni UPnP.")
    parser.add_argument("--bind", type=local_address, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--mode", choices=["demo", "local", "cloud"], default="local")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choisis un port entre 1024 et 65535.")
    os.umask(0o077)
    os.environ["HONEYPOT_MODE"] = args.mode
    # No request-controlled URLs, environment proxies, tools, shell or filesystem actions.
    os.environ.pop("TRUST_EDGE_IP", None)
    import uvicorn
    from honeypot.app import create_app
    print(f"Mode {args.mode} ; écoute {args.bind}:{args.port} ; captures privées dans {ROOT / 'captures'}", flush=True)
    uvicorn.run(create_app(), host=args.bind, port=args.port, workers=1, proxy_headers=False,
                access_log=False, server_header=False, limit_concurrency=32, timeout_keep_alive=5,
                h11_max_incomplete_event_size=16384)
