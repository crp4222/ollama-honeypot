# Ollama Honeypot

**See what a coding agent sends to its model after reading a file.**

This local lab records the requests, tool calls, tool results, and responses
between a coding agent and an operator-controlled model endpoint. Run an agent
against synthetic files, inspect what leaves the client, then repeat with a
redaction proxy in the path.

I also maintain [PrivAiTe](https://github.com/crp4222/PrivAiTe), the optional proxy
used below. You can use the lab independently or compare another filter.

## What reached the model endpoint

An actual **OpenCode + PrivAiTe 0.4.3 from PyPI** run read a synthetic `.env`,
customer record, and build log. The task was to diagnose a database connection
failure. Without filtering, all seven planted values reached the endpoint—even
though the final answer omitted the API keys.

![Captured tool results with and without PrivAiTe: the baseline sends synthetic credentials and email; the protected request redacts secrets and substitutes the email. Some labels are also over-redacted.](docs/evidence/request-comparison.png)

*Rendered excerpts from captured HTTP requests, not a simulated conversation or
an OpenCode UI screenshot. The three generated credential values are replaced
with `SYNTHETIC_…` labels for publication; `[SECRET]` and typed placeholders are
PrivAiTe's actual output. File-path wrappers are omitted.*
[Text excerpts and provenance](docs/evidence/README.md).

Both conversations identified the port mismatch. In the protected run, the
support email was restored in the answer returned to the client:

![Actual protected OpenCode answer: database port mismatch diagnosed, application port 8087, expected database port 5432, synthetic support email restored.](docs/evidence/client-answer.png)

| This installed-package test | Direct | Through PrivAiTe |
| --- | --- | --- |
| Full planted values received by the endpoint | 7 / 7 | 0 / 7 |
| Database diagnosis and requested email | Correct | Correct, with copy instructions |
| Conversation time | 7.49 s | 14.05 s |
| Model requests | 2 | 3 |
| Network timeouts | 0 | 0 |

The protected run spent **3.25 s in filtering** and chose an extra read. The total
time difference includes cloud inference and agent behavior. This is one pair of
conversations, not a general leak rate or a latency guarantee.

## Try it

**Capture-only demo:** Python 3.11+ and Docker Desktop or OrbStack. No model
account is needed; initial builds download dependencies.

```sh
git clone https://github.com/crp4222/ollama-honeypot.git
cd ollama-honeypot
python3 scripts/compose_local.py --bind 127.0.0.1 --mode demo
```

In a second terminal, from the checkout:

```sh
python3 scripts/watch.py --docker --follow
```

Send a request in another terminal:

```sh
curl -sS http://127.0.0.1:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"kimi-k3:cloud","messages":[{"role":"user","content":"Hello from the synthetic lab."}],"stream":true}'
```

This mode returns labelled simulated responses to exercise recording and display.
For actual tool reads and model responses, follow the
[OpenCode A/B setup](docs/SETUP.md#run-the-controlled-agent-experiment). It uses
your existing Ollama cloud connection, installs **`privaite==0.4.3`** from PyPI,
and runs the client in a disposable environment with synthetic files.

```sh
# After completing that setup on macOS:
python3 scripts/test_opencode.py --privaite-python .venv-privaite/bin/python --quick
```

Use the documented [English copy instructions](docs/OPENCODE-EXPERIMENT.md#reproduce)
to reproduce the email-restoration trial shown above.

## What this demonstrates

```text
Synthetic file → client tool result → [optional PrivAiTe] → lab captures → Ollama → cloud model
```

The client executes tools according to its own permissions. The lab controls the
model endpoint and replaces client system instructions with its own
[bounded educational prompt](config/system.txt). It does not bypass a client's
sandbox. Capture happens before the existing Ollama connection, which handles
cloud authentication; this is not a decrypted cloud TLS capture.

Run the client in an isolated environment containing only test data. Server
containers alone do not isolate a client running on your laptop. The launcher
accepts loopback or a specific private LAN address; Internet exposure is not
part of the setup.

## Results and limits

- **Broader 0.4.3 test:** 16 OpenCode conversations, 34 HTTP 200 model requests,
  no timeout, and no checked full canary or eight-character secret fragment in
  the ten protected conversations. [Report](docs/PRIVAITE-FIX-VALIDATION.md#recheck-after-the-github-push).
- **Large new tool results remain slow:** approximately 17.5 s of filtering for
  112 KB of new logs (111,656 bytes) in that run. Exact repeats benefit from caching.
- **Detection can miss data and remove useful context.** The capture above still
  shows over-redacted `.env` labels. Other benchmark inputs contain known leaks.
- **Restoration needs model cooperation.** All five explicit-copy cases in the
  broader run returned the correct email; all three unassisted short cases did
  not. The instructions help fidelity; they do not constrain a hostile endpoint.
- **Client coverage is specific:** this lab's full workflow was tested with
  OpenCode. Native Codex `/v1/responses` is not implemented; a full Claude Code
  workflow requires separate validation.

[Current experiment and reproduction](docs/OPENCODE-EXPERIMENT.md) ·
[Detailed setup](docs/SETUP.md) · [Operating guide (FR)](docs/USAGE.fr.md) ·
[Historical 0.4.2 measurements](docs/OPENCODE-0.4.2.md)

## License

[BSD 3-Clause](LICENSE) — Copyright © 2026 crp4222.
