#!/usr/bin/env python3
"""Store a dedicated key without echo, shell history, or modifying an Ollama login."""
import getpass
import os
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "private"
root.mkdir(mode=0o700, exist_ok=True)
root.chmod(0o700)
key = getpass.getpass("Clé API Ollama dédiée au laboratoire (saisie masquée) : ").strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit("Clé vide ou invalide.")
fd, temporary = tempfile.mkstemp(dir=root)
with os.fdopen(fd, "w") as handle:
    handle.write(key + "\n")
    # Parent directory is 0700. The file is readable when mounted individually
    # as a Docker secret for UID 10001, but inaccessible to other host users.
    os.fchmod(handle.fileno(), 0o444)
os.replace(temporary, root / "ollama_api_key")
print("Clé enregistrée dans private/ollama_api_key.")
