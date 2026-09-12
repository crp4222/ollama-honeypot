# OpenCode + PrivAiTe: measured behavior

Measured on **2026-09-12**, using actual OpenCode tool execution and cloud model
responses. This is a small reproducible integration experiment, not a detection
benchmark or a claim that every secret will be caught.

The [0.4.3 follow-up](PRIVAITE-FIX-VALIDATION.md) measures subsequent PrivAiTe
performance and boundary fixes. The results below describe the original source.

## Setup

| Component | Tested configuration |
| --- | --- |
| Client | OpenCode 1.18.30, OpenAI-compatible provider, streaming |
| Model | `kimi-k3:cloud`, through existing Ollama 0.33.3 |
| Lab source | `e631475` |
| PrivAiTe source | `bdcb5a6ae6176d0d4cea4bb79a7a73067a92b8dc` (0.4.2 source) |
| Detection | `onnx` preset, q4f16, CPU; Presidio + privacy-filter |
| Detector revision | `7ffa9a043d54d1be65afb281eddf0ffbe629385b` |
| Relevant dependencies | ONNX Runtime 1.27.0, Transformers 5.12.1, LiteLLM 1.89.3 |
| Host | Apple M1 Pro, 10 CPU cores, 16 GiB RAM |
| Privacy policy | Fail closed; reversible placeholders; SECRET redacted; CREDIT_CARD masked; fuzzy restoration disabled |
| Detection cache | Enabled, 4,096 entries, 1,800-second TTL |

The server instances listened on new loopback ports and used a separate, bounded
operator prompt. OpenCode ran in a temporary directory with synthetic `.env`,
`customer.json`, and log files. Its environment did not contain provider secrets.
The macOS sandbox denied access to the real home directory and outbound network
connections except loopback. OpenCode permissions allowed reading the fixtures
and denied shell execution, writes, and external directories. Global plugins,
skills, MCP servers, and configuration were excluded.

Both routes used the same underlying model and lab policy:

```text
OpenCode -> lab -> existing Ollama -> cloud model
OpenCode -> PrivAiTe -> lab -> existing Ollama -> cloud model
```

The task was to diagnose a database port mismatch by reading the fixtures. Expected
values were application port `8087`, database port `5432`, and the fixture support
email. Seven canaries covered an API key, a GitHub-style token, a password, email,
name, phone, and test card. Random credentials were generated locally and had no
account behind them. The larger log scenarios contained five of those canaries;
phone and card were absent, rather than successfully redacted there.

## Disclosure and task completion

Across the primary matrix, **17 of 18 OpenCode invocations completed**. One initial
concurrent invocation failed locally with `database is locked`, before making a
model request. Giving each client separate XDG data/config/cache/state directories
fixed that test setup; both concurrent clients then completed.

The completed invocations generated **35 model requests**, all HTTP 200. No network
timeout or OpenCode watchdog expiry occurred. One capture ended with
`client_disconnected` after the provider's `finish_reason: tool_calls` and `[DONE]`
had already arrived. The client executed those tools and completed the next turn;
this was not an observed truncated answer.

- **Without PrivAiTe:** all seven canaries reached the lab in full in each of the
  three short baseline runs, although the final answers did not print the keys.
- **With PrivAiTe:** none of the full canaries actually read reached the lab in
  any of the eleven completed protected runs. No contiguous eight-character
  segment of the three generated secrets was found in those downstream requests.
  This does not rule out shorter fragments, encodings, or leaks in other inputs.
- **Useful diagnosis:** all completed runs identified the expected database port.
  Denying `.env` still allowed the diagnosis from the log, but the model incorrectly
  guessed the application port. The same credentials in that permitted log still
  reached the unfiltered endpoint. A file permission protects the denied file,
  not copies of its contents elsewhere.
- **Restoration limitation:** the first three protected short runs invented a
  different email. Inspection of the provider's response confirmed that invention
  happened before restoration. Asking the model to copy the actual placeholder
  verbatim restored the correct email in the two explicit follow-up trials, the
  stress run, and the successful concurrent trials. The long-log trials also
  returned the correct email without the extra instruction.

The extra instruction is a useful cooperative-model test, not an enforcement
mechanism against a hostile endpoint. A restorer cannot recover a value from an
unrelated string invented by the model.

## Latency

| Scenario | Without PrivAiTe | With PrivAiTe | Time inside PII processing, protected run |
| --- | --- | --- | --- |
| Three small files, first pass | 8.39 s | 15.26 s | 5.09 s |
| Same small inputs, exact repeats | 7.23–7.81 s | 8.67–10.19 s | about 0.001 s total |
| Small inputs + explicit placeholder instruction, two trials | — | 10.85–10.95 s | 0.51–0.57 s |
| One new 26,337-byte log | 6.61 s | 15.32 s | 8.54 s |
| Repeated large log, new short user prompt | — | 7.32 s | 0.64 s |
| Four new logs, 105,488 bytes total | 9.49 s | 40.20 s | 31.91 s |
| Two simultaneous clients, separate client storage | — | 13.50–13.51 s each | 0.89–0.90 s each |

These are observed wall times, not a statistically stable speed ratio. They include
OpenCode startup, cloud inference, network variation, and proxy work. The PII column
times `PIIEngine.process_request` separately; it includes detection and replacement
across the messages. No detectors were disabled to improve the numbers.

The proxy needed roughly **13 seconds to initialize** its installed detector models
in the initial experiment. The isolated runner validation took 15.6 seconds to
become ready. Wait for `/ready` before connecting the client. Model downloads and
first installation are additional costs.

Exact repeated texts benefit enormously from the opt-in detection cache. A changed
user prompt or a newly read file still needs scanning. In the four-log test, the
second request waited **33.32 seconds for its first response body**, including
31.39 seconds of PII processing before forwarding to the lab. This is why a small
warm-cache example must not be presented as typical large-context latency.

The client provider options specified a 150-second request timeout. The lab used a
120-second upstream timeout and the runner a 240-second conversation watchdog.
The experiment did not simulate a cloud outage,
high user load, or all possible client/reverse-proxy timeouts. First-body metrics
also need not represent the first useful answer token.

## Issues to address or disclose before publication

1. **Cold detection cost on large tool results.** Keep the service warm and document
   the optional detection cache. Further performance work should measure changed
   histories and large files while keeping detection coverage and fail-closed
   behavior. Raising timeouts alone is not a speed improvement.
2. **Model fidelity to placeholders.** Include an explicit copy instruction in a
   cooperative demonstration and show an unassisted case too. Do not claim that
   reversible substitution guarantees a semantically identical response.
3. **Over-broad detection spans.** In the short fixture, some detections included
   the `SUPPORT_EMAIL=` label, surrounding quotes/brackets, and parts of a tool's
   path markup. The same underlying email acquired multiple placeholders because
   the detected spans differed. No secret leakage was found in that case, but the
   extra removals can reduce context quality. Boundary changes need dedicated
   detection tests and the PrivAiTe detection benchmark, not an unmeasured trim.
4. **Client storage isolation.** Concurrent CLI tests must not share an OpenCode
   database. The runner now creates separate directories for each invocation.

No PrivAiTe detector or production service configuration was changed during this
experiment. The improvements here are a repeatable test runner, explicit cache
configuration in the lab example, and evidence of the remaining limitations.

## Reproduce

On macOS, install OpenCode outside your real home directory, install this lab's
dependencies in `.venv`, and install PrivAiTe with its detector models already
cached. The existing local Ollama must be signed in and able to run `kimi-k3:cloud`.
These commands use that account for actual cloud inference:

```sh
# From the lab checkout; adjust only the PrivAiTe checkout path.
python3 scripts/test_opencode.py --privaite /path/to/PrivAiTe --quick

# Full matrix: 16 conversations, including large logs and two concurrent clients.
python3 scripts/test_opencode.py --privaite /path/to/PrivAiTe
```

The runner creates new loopback services and synthetic files under a private
temporary directory, prints the directory location, and stops its services on
completion. It does not reconfigure the existing lab, Ollama, or the user's proxy.
Other platforms need an equivalent disposable client VM; the runner refuses to
silently omit the macOS sandbox.

It saves the exact prompt, configurations, client outputs, capture events, and
metrics locally. The ASGI measurement wrapper logs **synthetic request bodies**;
it belongs only in this experiment, never in a proxy handling real private data.
No raw captures or generated credential-shaped values are committed to this repo.

The summary checks fixture coverage at the client, full canaries and secret
fragments after PrivAiTe, the expected port, email restoration, HTTP responses,
and client failures. Missing reads or model requests cannot count as successful
redaction. Check the complete answers as well: the automatic port check only
looks for the expected value, not a general semantic correctness proof.

Each invocation has its own experiment marker and client storage. That marker
changes a short prompt, so the full runner's warm cases are not byte-identical to
the first three pairs measured above. Cloud responses and runtimes vary. The
`--quick` runner was validated separately: 7.92 seconds baseline and 14.91 seconds
protected, with correct diagnosis/email and no detected canary disclosure after
PrivAiTe.

A subsequent **full run of the checked-in runner completed 16/16 conversations
and 32/32 model requests with HTTP 200**, using newly generated credentials. Its
ten protected conversations again had no full canary or tested secret-fragment
disclosure. The unassisted short cases again invented an email in all three trials;
the explicit-copy cases restored it. The four-log run took 39.95 seconds protected
versus 7.83 seconds baseline. Those additional results are included separately in
the JSON artifact, rather than replacing the initial measurements.

The [machine-readable primary results](opencode-results-2026-09-12.json) include
the failed initial client invocation as well as successful runs. Raw artifacts
remain private. Repository checks also passed: **37 tests + 31 subtests** in the
lab, and **530 tests, 1 skipped** in PrivAiTe.
