#!/usr/bin/env python3
"""Read private JSONL captures without serving them over HTTP."""
import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from honeypot.observe import ConversationView, safe


def show(event, selected, raw, wire=False, view=None):
    if selected and not (event.get("request_id") or "").startswith(selected):
        return
    if raw:
        print(json.dumps(event, ensure_ascii=True), flush=True)
        return
    if not wire:
        view.show(event)
        return
    rid = (event.get("request_id") or "startup")[:10]
    kind = event["event"]
    if kind == "upstream_chunk":
        # A chunk can split a UTF-8 character. Raw base64 in JSONL remains lossless.
        value = base64.b64decode(event["data_b64"]).decode("utf-8", errors="replace")
        print(f"[{rid}] RÉPONSE « {safe(value)} »", flush=True)
    else:
        data = {k: v for k, v in event.items() if k not in {"time", "request_id", "event"}}
        print(f"[{rid}] {kind}: {safe(json.dumps(data, ensure_ascii=False))}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Questions, réponses, thinking et outils, regroupés en blocs lisibles.")
    parser.add_argument("--file", type=Path, default=ROOT / "captures/events.jsonl")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--id", help="Filtrer un X-Request-ID ou son préfixe")
    formats = parser.add_mutually_exclusive_group()
    formats.add_argument("--json", action="store_true", help="Événements JSON bruts, sûrs pour le terminal")
    formats.add_argument("--wire", action="store_true", help="Ancien affichage détaillé des morceaux SSE/NDJSON")
    parser.add_argument("--history", action="store_true", help="Afficher aussi l'historique renvoyé dans chaque requête")
    parser.add_argument("--docker", action="store_true", help="Lire le volume privé du conteneur gateway")
    args = parser.parse_args()
    view = ConversationView(history=args.history)
    if args.docker:
        command = ["tail", "-n", "+1", "-F", "/data/events.jsonl"] if args.follow else ["cat", "/data/events.jsonl"]
        process = subprocess.Popen(["docker", "compose", "exec", "-T", "gateway", *command], cwd=ROOT,
                                   stdout=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            for line in process.stdout:
                if line.endswith("\n"):
                    show(json.loads(line), args.id, args.json, args.wire, view)
        except KeyboardInterrupt:
            pass
        finally:
            view.finish()
            process.terminate()
            process.wait()
        raise SystemExit(0)
    stream = None
    inode = None
    try:
        while True:
            if not args.file.exists():
                if not args.follow:
                    raise SystemExit("Aucune capture pour l'instant.")
                time.sleep(0.2)
                continue
            current = args.file.stat().st_ino
            if stream is None or inode != current:
                if stream:
                    stream.close()
                stream, inode = args.file.open(), current
            position = stream.tell()
            line = stream.readline()
            if line.endswith("\n"):
                show(json.loads(line), args.id, args.json, args.wire, view)
            elif args.follow:
                stream.seek(position)
                time.sleep(0.2)
            else:
                break
    except KeyboardInterrupt:
        pass
    finally:
        view.finish()
        if stream:
            stream.close()
