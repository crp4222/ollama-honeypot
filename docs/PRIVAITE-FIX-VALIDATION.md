# PrivAiTe 0.4.3 follow-up validation

Measured on 2026-09-12 with the same host, installed detectors, Ollama cloud
model and isolation described in the [original experiment](OPENCODE-EXPERIMENT.md).
The original measurements remain available; this follow-up tests the fixes in
PrivAiTe source commit `7798804a27c5558d550e48b667e2fa2a83bdded6`.
The source was uncommitted during the full OpenCode run and committed afterwards.
No real credentials or personal files were used.

## Changes under test

- Identical ONNX input windows reuse predictions within one scrub operation.
  The cache is bounded to 128 windows, uses salted hashes and detection metadata,
  applies the current text's offsets, and clears on completion or cancellation.
  It does not share predictions between requests. The existing optional whole-text
  detection cache remains a separate feature.
- Built-in email, phone and URL detections preserve recognized surrounding
  delimiters, common email assignment labels, and `<path>` wrappers. Refinement
  happens before union merging; arbitrary secret punctuation and custom-pattern
  spans are preserved. This does not exempt path contents from scanning.
- [English placeholder instructions](https://github.com/crp4222/PrivAiTe/blob/7798804a27c5558d550e48b667e2fa2a83bdded6/docs/placeholder-instructions.txt)
  ask a cooperative model to copy placeholders exactly, including in tool
  arguments. PrivAiTe does not inject them automatically.

## Actual OpenCode results

The full runner completed **16/16 conversations and 34/34 model requests with
HTTP 200**, with no network timeout or conversation watchdog expiry. All ten
protected conversations completed the expected database-port diagnosis. The
seven planted values were visible downstream in the short unfiltered baseline;
none of the planted values actually read, nor any tested eight-character secret
fragment, appeared downstream in the protected conversations. Long-log cases
contain five of the seven canaries. This is a small fixture result, not a general
guarantee about secret detection.

| Scenario | Direct wall time | Protected wall time | Protected PII processing, all turns |
| --- | --- | --- | --- |
| Three small files, first conversation | 8.12 s | 12.53 s | 4.00 s |
| Small files, explicit short copy instruction, two trials | — | 7.62–8.82 s | 0.49–0.94 s |
| One new 26,337-byte log | 7.58 s | 15.95 s | 8.12 s |
| Four new logs, 105,488 bytes total | 14.37 s | 26.16 s | 17.46 s |
| Two simultaneous clients | — | 9.10–12.09 s each | 0.72–0.76 s each |

The four-log PII time was 31.91 seconds in the original experiment, versus
17.46 seconds here. These separate autonomous runs have different generated
credentials and cloud responses, so the wall-time comparison is observational.
The service became ready in 12.1 seconds. Fresh non-repeating context still needs
inference, and a long first response can still exceed a stricter client timeout.
The runner uses the timeout settings documented in the original experiment.

A separate local replay used the **same captured synthetic request** from the
original four-log experiment, with warmed installed models and the optional
whole-text detection cache disabled. Window reuse was switched off/on/on/off:

| Window reuse | Complete PII processing | ONNX inference calls |
| --- | --- | --- |
| Disabled, two passes | 33.16–33.20 s | 40 each |
| Enabled, two passes | 19.22–19.23 s | 19 each |

This is about 42% less processing time on that repeated-content request. Outputs,
reversible mappings, and detector entities including types, spans and scores
were exactly equal in all four passes. This comparison includes the entire
request history and uses a different cache setup from the live run above; its
absolute times should not be interchanged with the live four-log measurements.

The downstream short tool results retained `SUPPORT_EMAIL=`, all three matched
`<path>` wrappers, and one shared email placeholder for the same underlying email.
Real-detector regression tests also verify valid JSON around a phone value and
that restoration returns the value without an assignment label or delimiter.
False positives and other overly broad detections remain possible.

Without a copy instruction, **two of three short protected conversations failed
to return the correct email**. All five protected explicit-copy conversations
returned it, including stress and concurrent cases. A boundary fix cannot force
a model to preserve a placeholder. The instruction is a response-fidelity aid,
not protection against an endpoint that replaces prompts or ignores instructions.

## Validation and reproduction

PrivAiTe: **565 tests passed, 1 skipped**, plus Ruff lint/format and mypy checks
including both integrations. Its comparative benchmark was rerun on 120 documents
and 14 clean documents: span recall 84.9%, strict recall 81.0%, and 2 clean-document
false positives are unchanged. The independent benchmark's known missed secrets
remain documented; these changes do not fix all detection misses.

The lab suite passed **37 tests and 31 subtests**. New regression tests cover
cache isolation, cancellation, late worker writes, bounded retention, unchanged
policy blocking, model-input masks, live offsets, structured boundaries and
custom/secret punctuation. No production service is used to capture test inputs.

```sh
python3 scripts/test_opencode.py --privaite /path/to/PrivAiTe

# Exercise the exact English document in the protected quick conversation.
python3 scripts/test_opencode.py --privaite /path/to/PrivAiTe --quick \
  --placeholder-instructions /path/to/PrivAiTe/docs/placeholder-instructions.txt
```

The optional instruction file is copied into the private artifact directory and
its SHA-256 is recorded. It replaces the short copy instruction in explicit-copy
cases only; unassisted cases still run without it. The instruction is supplied
in the test's user task because the lab replaces client system messages.

That exact English file was checked in a separate quick run: **2/2 conversations,
4/4 HTTP 200 model requests**, with no timeout or checked canary disclosure after
PrivAiTe. The protected conversation returned the correct email and diagnosis.
It took 12.90 seconds including cold text detection, versus 7.49 seconds directly.
This is one successful cooperative trial of the full instruction text, not proof
that every model or tool workflow will follow it.

Aggregated results are in [the follow-up JSON](privaite-fix-results-2026-09-12.json).
Raw fixtures, captures, transcripts, configurations and local paths remain private.

## Recheck after the GitHub push

The complete matrix was rerun after pushing PrivAiTe `7798804`, lab `5fc1914`
and benchmark `068f52a` to their respective `main` branches. It used newly
generated credentials and the exact English instruction file for all explicit-copy
cases, with its SHA-256 recorded in the private run metadata.

**16/16 conversations and 34/34 HTTP 200 requests completed**, with no timeout,
client failure, checked full-canary disclosure or tested eight-character secret
fragment in the ten protected conversations. All completed the expected database
diagnosis. The full instruction restored the email in all five protected
explicit-copy cases; the three short unassisted cases all failed to return it.
Those unassisted failures remain visible in the report rather than being excluded.

| Scenario | Direct wall time | Protected wall time | Protected PII processing |
| --- | --- | --- | --- |
| Small files, first conversation | 15.11 s | 20.76 s | 4.81 s |
| Small files, full English instruction | — | 8.94–9.07 s | 0.57–0.87 s |
| One new large log | 9.60 s | 23.90 s | 8.87 s |
| Four new logs | 9.06 s | 28.79 s | 17.48 s |
| Two concurrent clients | — | 12.60–13.18 s each | 0.86–0.88 s each |

Model initialization took 20.9 seconds in this run. The long protected case chose
an extra glob/read turn, illustrating why autonomous wall times cannot isolate
filter cost. These are observed times with the documented generous timeouts,
not a latency guarantee for arbitrary histories, clients or cloud availability.

The [GitHub CI for the pushed PrivAiTe commit](https://github.com/crp4222/PrivAiTe/actions/runs/34704961387)
passed tests, lint and formatting on Python 3.11, 3.12 and 3.13, type checking,
and the Docker build. The lab again passed 37 tests and 31 subtests locally.

A fresh GitHub clone of lab `5fc1914`, without the operator's `.env`, credentials
or captures, also passed the README's Docker demo path on a separate temporary
Compose project and loopback port: labelled simulated streaming response,
synthetic request capture, viewer output, and refusal of the model-management
route. All three containers ran as non-root with read-only root filesystems and
all capabilities dropped; only the edge published a loopback port. Test services
and their volumes were removed afterwards. This smoke test exercises simulated
capture; real model/tool behavior is covered by the OpenCode matrix above.

The setup now supplies the same [bounded educational prompt](../config/system.educational.txt)
as the tested runner and pins the PrivAiTe source installation. At this check,
PyPI still serves 0.4.2; a plain `pip install privaite` will not reproduce the
0.4.3 fixes. The pushed lab remains private, and it does not yet include a license
file. Decide public access and licensing before describing it as an open-source
project readers can reuse.
