"""Summarize downstream disclosure and utility; do not print raw fixtures."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
ROOT = args.root.resolve()
values = json.loads((ROOT / "canaries.json").read_text())
metrics = {}
for role in ["gateway", "privaite"]:
    p = ROOT / (role + "-metrics.jsonl")
    metrics[role] = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

results = []
for path in sorted((ROOT / "runs").glob("*/result.json")):
    run = json.loads(path.read_text())
    selected = {}
    for role, rows in metrics.items():
        window = [e for e in rows if run["start"] <= e["mono"] <= run["end"]]
        ids = {
            e["request"]
            for e in window
            if e["event"] == "request_body" and "Experiment ID: " + run["label"] in e["body"]
        }
        selected[role] = [e for e in window if e["request"] in ids]
    requests = [json.loads(e["body"]) for e in selected["gateway"] if e["event"] == "request_body"]
    transmitted = json.dumps(requests, ensure_ascii=False)
    tool_outputs = json.dumps(run["tool_calls"], ensure_ascii=False)
    pii = [e["duration"] for e in selected["privaite"] if e["event"] == "pii_processing"]
    r = {k: run[k] for k in ["label", "mode", "long", "wall_seconds", "exit_code", "timed_out"]}
    r.update(
        {
            "requests_to_model": len(requests),
            "tools": [
                {"name": x["name"], "status": x["state"].get("status")} for x in run["tool_calls"]
            ],
            "tool_output_contains": {name: value in tool_outputs for name, value in values.items()},
            "endpoint_contains_full_value": {
                name: value in transmitted for name, value in values.items()
            },
            "diagnosis_correct": any("5432" in answer for answer in run["answers"]),
            "email_returned_to_client": any(values["email"] in answer for answer in run["answers"]),
            "errors": len(run["errors"]),
            "app_port_correct": any("8087" in answer for answer in run["answers"]),
            "database_locked": "database is locked" in (path.parent / "stderr.log").read_text(),
            "stress": run.get("stress", False),
            "deny_env": run.get("deny_env", False),
            "copy_placeholders": run.get("copy_placeholders", False),
            "secret_fragment_8_found": {
                name: any(value[i : i + 8] in transmitted for i in range(len(value) - 7))
                for name, value in values.items()
                if name in ["openai_key", "github_token", "db_password"]
            },
            "email_local_parts_found": {
                part: part in transmitted.lower() for part in ["camille", "martin"]
            },
            "pii_seconds": [round(x, 4) for x in pii],
            "gateway_statuses": [
                e["status"] for e in selected["gateway"] if e["event"] == "headers"
            ],
            "proxy_statuses": [
                e["status"] for e in selected["privaite"] if e["event"] == "headers"
            ],
            "first_body_seconds": {
                role: [round(e["elapsed"], 4) for e in rows if e["event"] == "first_body"]
                for role, rows in selected.items()
            },
        }
    )
    results.append(r)

(ROOT / "results-summary.json").write_text(json.dumps(results, indent=2))

failed = []
for r in results:
    problems = []
    if r["exit_code"] or r["timed_out"] or r["errors"]:
        problems.append("client failure")
    if (
        not r["requests_to_model"]
        or any(s != 200 for s in r["gateway_statuses"] + r["proxy_statuses"])
        or len(r["gateway_statuses"]) != r["requests_to_model"]
    ):
        problems.append("HTTP failure or no model request")
    if r["mode"] == "protected" and len(r["proxy_statuses"]) != r["requests_to_model"]:
        problems.append("missing proxy response")
    required = (
        list(values)
        if not (r["long"] or r["stress"])
        else ["openai_key", "github_token", "db_password", "email", "name"]
    )
    if not all(r["tool_output_contains"][key] for key in required):
        problems.append("fixture coverage incomplete")
    if not r["diagnosis_correct"]:
        problems.append("wrong diagnosis")
    if not (r["long"] or r["stress"] or r["deny_env"]) and not r["app_port_correct"]:
        problems.append("wrong application port")
    if r["deny_env"] and not any(
        t["name"] == "read" and t["status"] == "error" for t in r["tools"]
    ):
        problems.append("expected denied read not observed")
    if r["mode"] == "protected":
        if any(r["endpoint_contains_full_value"].values()) or any(
            r["secret_fragment_8_found"].values()
        ):
            problems.append("canary disclosure")
    elif not all(r["endpoint_contains_full_value"][key] for key in required):
        problems.append("baseline disclosure not established")
    if r["copy_placeholders"] and not r["email_returned_to_client"]:
        problems.append("email restoration failed")
    if problems:
        failed.append({"label": r["label"], "problems": problems})
print(
    json.dumps(
        {
            "conversations": len(results),
            "failures": failed,
            "results_file": str(ROOT / "results-summary.json"),
        },
        indent=2,
    )
)
raise SystemExit(bool(failed) or not results)
