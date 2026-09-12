#!/usr/bin/env python3
"""Non-billable Docker checks. Inference is exercised only in explicit demo mode."""
import argparse
import json
import subprocess
import httpx
from serve import local_address

parser = argparse.ArgumentParser()
parser.add_argument("--bind", type=local_address, default="127.0.0.1")
parser.add_argument("--port", type=int, default=11435)
args = parser.parse_args()
names = ["ollama-observation-lab-" + name + "-1" for name in ("edge", "gateway", "relay")]
containers = json.loads(subprocess.check_output(["docker", "inspect", *names]))
demo = False
local = False
for container in containers:
    assert container["State"]["Running"], container["Name"]
    assert container["HostConfig"]["ReadonlyRootfs"]
    assert container["Config"]["User"] in ("101:101", "10001:10001")
    assert "ALL" in container["HostConfig"]["CapDrop"]
    ports = container["HostConfig"]["PortBindings"] or {}
    if container["Name"].endswith("edge-1"):
        assert ports["8080/tcp"] == [{"HostIp": args.bind, "HostPort": str(args.port)}]
    else:
        assert not ports
    if container["Name"].endswith("gateway-1"):
        demo = "HONEYPOT_MODE=demo" in container["Config"]["Env"]
        local = "HONEYPOT_MODE=local" in container["Config"]["Env"]
    print(container["Name"], "non-root, racine RO, publication", ports)
host = f"[{args.bind}]" if ":" in args.bind else args.bind
with httpx.Client(base_url=f"http://{host}:{args.port}", trust_env=False, timeout=10) as client:
    response = client.get("/api/tags")
    response.raise_for_status()
    assert response.json()["models"][0]["name"] == "kimi-k3:cloud"
    if demo:
        response = client.post("/api/chat", json={"model": "another-model", "messages": [{"role": "user", "content": "bonjour"}], "stream": True})
        response.raise_for_status()
        assert "simulée" in response.json()["message"]["content"]
        print("Streaming simulé : OK")
    assert client.post("/api/pull", json={}).status_code == 404
    print("Entrée HTTP et routes de gestion : OK")

check = '''import socket, httpx, sys
try:
    connection = socket.create_connection(("1.1.1.1", 443), timeout=3)
except OSError:
    print("Sortie Internet directe de gateway : bloquée")
else:
    connection.close()
    raise SystemExit("ECHEC : gateway peut joindre Internet directement")
with httpx.Client(trust_env=False, timeout=20) as client:
    response = client.post("http://relay:8081/api/chat", json={})
    expected = 400 if sys.argv[1] == "local" else 401
    assert response.status_code == expected, response.status_code
    assert client.post("http://relay:8081/api/pull", json={}).status_code == 404
    print("Relais : Ollama joignable, requête vide refusée sans inférence, autres routes bloquées")
'''
subprocess.run(["docker", "compose", "exec", "-T", "gateway", "python", "-c", check, "local" if local or demo else "cloud"], check=True)
