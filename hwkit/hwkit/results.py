"""Structured hardware-validation results: one ``results.json`` per test session (board + phones + players).

Rules enforced by ``check``: every test of the catalog appears exactly once; status in PASS/FAIL/PARTIAL/NOT_RUN/BLOCKED; a PASS/FAIL/PARTIAL
needs observations and at least one evidence file that exists; the only allowed label for executed tests is REAL-HARDWARE (a simulated
result can never be entered as a hardware result)."""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATUSES = ("PASS", "FAIL", "PARTIAL", "NOT_RUN", "BLOCKED")


def catalog() -> list[dict]:
    with open(os.path.join(HERE, "catalog.json"), encoding="utf-8") as fh:
        return json.load(fh)["tests"]


def new_session(path: str, board: str, operator: str, phones: list[str], players: list[str], notes: str = "") -> dict:
    doc = {"format": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "board": board, "operator": operator,
           "phones": phones, "players": players, "notes": notes, "environment_file": "env.json",
           "tests": [{"id": t["id"], "title": t["title"], "status": "NOT_RUN", "label": None, "devices": [], "observations": "",
                      "evidence": [], "measurements": {}} for t in catalog()]}
    save(path, doc)
    return doc


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path: str, doc: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def set_result(path: str, test_id: str, status: str, observations: str, evidence: list[str] = (), devices: list[str] = (),
               measurements: dict | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    doc = load(path)
    t = next((t for t in doc["tests"] if t["id"] == test_id), None)
    if t is None:
        raise KeyError(f"unknown test id {test_id}")
    t.update(status=status, observations=observations, evidence=list(evidence), devices=list(devices),
             measurements=dict(measurements or {}), label="REAL-HARDWARE" if status in ("PASS", "FAIL", "PARTIAL") else None,
             updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    save(path, doc)
    return t


def check(path: str) -> list[str]:
    doc, base = load(path), os.path.dirname(os.path.abspath(path))
    problems = []
    ids = [t["id"] for t in doc["tests"]]
    want = [t["id"] for t in catalog()]
    for i in sorted(set(want) - set(ids)):
        problems.append(f"{i}: missing")
    for i in sorted({x for x in ids if ids.count(x) > 1}):
        problems.append(f"{i}: duplicated")
    for t in doc["tests"]:
        if t["status"] not in STATUSES:
            problems.append(f"{t['id']}: invalid status {t['status']!r}")
        if t["status"] in ("PASS", "FAIL", "PARTIAL"):
            if t.get("label") != "REAL-HARDWARE":
                problems.append(f"{t['id']}: executed test must be labelled REAL-HARDWARE")
            if not t.get("observations", "").strip():
                problems.append(f"{t['id']}: observations missing")
            if not t.get("evidence"):
                problems.append(f"{t['id']}: evidence missing")
            for e in t.get("evidence", []):
                if not os.path.exists(os.path.join(base, e)):
                    problems.append(f"{t['id']}: evidence file not found: {e}")
            if not t.get("devices"):
                problems.append(f"{t['id']}: devices (phone/player/board versions) missing")
    if not os.path.exists(os.path.join(base, doc.get("environment_file", "env.json"))):
        problems.append("env.json missing (run: python3 -m hwkit env)")
    return problems


def report(path: str) -> str:
    doc = load(path)
    counts = {s: sum(1 for t in doc["tests"] if t["status"] == s) for s in STATUSES}
    lines = [f"# Hardware validation results - {doc['board']} ({doc['created']})", "",
             f"Operator: {doc['operator']}  |  Phones: {', '.join(doc['phones']) or '-'}  |  Players: {', '.join(doc['players']) or '-'}", "",
             "Summary: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "",
             "| ID | Test | Status | Label | Observations | Evidence |", "|---|---|---|---|---|---|"]
    for t in doc["tests"]:
        obs = t["observations"].replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {t['id']} | {t['title']} | {t['status']} | {t['label'] or '-'} | {obs or '-'} | {', '.join(t['evidence']) or '-'} |")
    problems = check(path)
    lines += ["", "## Consistency check", ""] + ([f"- {p}" for p in problems] if problems else ["- no problems"])
    return "\n".join(lines) + "\n"
