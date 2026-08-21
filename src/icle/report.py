"""AgentTaskReport: the fixed evaluation form every execution must return.

Why this exists: J needs per-task observations. A bare accept flag carries one
bit, so the engine could not score anything. This module defines ONE fixed form
(no free-form schema per agent), turns it into the metric contract that
TaskSpec declares, and derives only those declared metrics.

Discipline:
- The template is fixed here. Agents fill it; they never define it.
- Fields are verifiable claims (counts, ran/passed), not a self-awarded score.
- A missing field is skipped, never scored as 0.
- The user's accept remains the required metric; the form adds resolution.
- Token counts and duration are cost facts, not scored metrics. Missing
  consumption blocks accept; it is never treated as $0.
"""

from __future__ import annotations

import json
import re
from typing import Any

REPORT_SCHEMA = "icle-agent-task-report/v0.1"
REPORT_CONTRACT_ID = "icle-agent-task-report/v0.1"
CONSOLIDATED_SCHEMA = "icle-task-report-consolidation/v0.1"

# Agents emit the form after this marker so it is separable from prose.
REPORT_MARKER = "ICLE_TASK_REPORT"

REPORT_STATUSES = ("observed", "missing", "invalid")
CONSOLIDATION_SOURCES = ("finishing_agent", "intelligence_summary", "single_agent", "none")

_MAX_LIST = 40
_MAX_ITEM = 200
_MAX_TEXT = 1000

# ---------------------------------------------------------------- fixed template

# field -> kind. This tuple IS the form; nothing else is accepted.
FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("requirements_total", "count"),
    ("requirements_met", "count"),
    ("verification_ran", "flag"),
    ("verification_passed", "flag"),
    ("tests_total", "count"),
    ("tests_passed", "count"),
    ("input_tokens", "count"),
    ("output_tokens", "count"),
    ("cache_read_tokens", "count"),
    ("reasoning_tokens", "count"),
    ("duration_s", "number"),
    ("files_changed", "list"),
    ("commands_run", "list"),
    ("blocked", "flag"),
    ("blocked_reason", "text"),
    ("summary", "text"),
)
FORM_FIELD_NAMES = tuple(name for name, _ in FORM_FIELDS)
CONSUMPTION_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
    "duration_s",
)
# Usage-only JSON must not be mistaken for the evaluation form.
QUALITY_FIELD_NAMES = tuple(name for name in FORM_FIELD_NAMES if name not in CONSUMPTION_FIELDS)

# Metrics the evaluation engine may score. TaskSpec declares exactly these, so
# an undeclared field can never reach the generator and reject the whole run.
#
# user_accept is deliberately NOT here. The local A probe scores a baseline model
# on these same TaskSpecs, and a baseline run has no user accept — keeping the
# accept in the scored set would hand the agent weight the baseline can never
# earn and make C an artifact of that gap. The accept still gates the evidence
# (only accepted runs are exported as `completed`; reject/redo become
# subject_failure) and is recorded in outcome.diagnostics.
METRIC_CONTRACT: tuple[dict[str, Any], ...] = (
    {
        "metric_id": "requirement_coverage",
        "min": 0,
        "max": 1,
        "direction": "maximize",
        "weight": 2,
        "required": False,
    },
    {
        "metric_id": "verification_passed",
        "min": 0,
        "max": 1,
        "direction": "maximize",
        "weight": 2,
        "required": False,
    },
    {
        "metric_id": "test_pass_rate",
        "min": 0,
        "max": 1,
        "direction": "maximize",
        "weight": 1,
        "required": False,
    },
)
METRIC_IDS = tuple(metric["metric_id"] for metric in METRIC_CONTRACT)


class ReportError(ValueError):
    pass


def blank_report() -> dict[str, Any]:
    """Empty form used by the UI and by manual fill-in."""
    report: dict[str, Any] = {"schema_version": REPORT_SCHEMA}
    for name, kind in FORM_FIELDS:
        if name in CONSUMPTION_FIELDS:
            report[name] = None
        elif kind == "count":
            report[name] = 0
        elif kind == "flag":
            report[name] = False
        elif kind == "list":
            report[name] = []
        else:
            report[name] = ""
    return report


def prompt_block() -> str:
    """Mandatory form instruction appended to every execution prompt."""
    return (
        f"\nREQUIRED FINAL REPORT\n"
        f"After finishing the work you MUST print the line {REPORT_MARKER} and then a\n"
        "single JSON object as the last thing in your output. Report what you\n"
        "actually did; do not estimate and do not grade yourself.\n"
        f"{REPORT_MARKER}\n"
        "{\n"
        '  "requirements_total": <int, explicit requirements in this step>,\n'
        '  "requirements_met": <int, how many you actually completed>,\n'
        '  "verification_ran": <true|false, did you run the verification>,\n'
        '  "verification_passed": <true|false, did it pass>,\n'
        '  "tests_total": <int, 0 if none>,\n'
        '  "tests_passed": <int, 0 if none>,\n'
        '  "input_tokens": <int, tokens you consumed as input; required>,\n'
        '  "output_tokens": <int, tokens you produced as output; required>,\n'
        '  "cache_read_tokens": <int, 0 if none or unknown>,\n'
        '  "reasoning_tokens": <int, 0 if none or unknown>,\n'
        '  "duration_s": <number, wall-clock seconds this step took; required>,\n'
        '  "files_changed": ["<relative path>"],\n'
        '  "commands_run": ["<command>"],\n'
        '  "blocked": <true|false>,\n'
        '  "blocked_reason": "<empty if not blocked>",\n'
        '  "summary": "<one or two sentences>"\n'
        "}"
    )


# ---------------------------------------------------------------- normalization


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+", text):
            return int(text)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "y", "1", "pass", "passed"}:
            return True
        if text in {"false", "no", "n", "0", "fail", "failed"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:_MAX_LIST]:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:_MAX_ITEM])
    return out


def normalize_report(data: Any) -> dict[str, Any]:
    """Coerce a filled form onto the fixed template; drop unknown fields.

    Unusable values are omitted rather than defaulted, so a field the agent
    never answered cannot silently become evidence.
    """
    if not isinstance(data, dict):
        raise ReportError("report must be a JSON object")
    report: dict[str, Any] = {"schema_version": REPORT_SCHEMA}
    for name, kind in FORM_FIELDS:
        if name not in data:
            continue
        raw = data[name]
        if kind == "count":
            value = _count(raw)
        elif kind == "number":
            value = _number(raw)
        elif kind == "flag":
            value = _flag(raw)
        elif kind == "list":
            value = _strings(raw)
        else:
            value = str(raw).strip()[:_MAX_TEXT] if raw is not None else ""
        if value is None:
            continue
        report[name] = value
    # A count of met/passed can never exceed its total.
    for total_key, part_key in (("requirements_total", "requirements_met"), ("tests_total", "tests_passed")):
        total, part = report.get(total_key), report.get(part_key)
        if isinstance(total, int) and isinstance(part, int) and part > total:
            report[part_key] = total
    if not report.get("verification_ran"):
        report.pop("verification_passed", None)
    extra = sorted(set(data) - set(FORM_FIELD_NAMES) - {"schema_version"})
    if extra:
        report["ignored_fields"] = extra[:_MAX_LIST]
    return report


def report_metrics(report: dict[str, Any] | None) -> dict[str, float]:
    """Derive ONLY the declared form metrics. Unanswered fields are absent."""
    report = report or {}
    metrics: dict[str, float] = {}
    total = report.get("requirements_total")
    met = report.get("requirements_met")
    if isinstance(total, int) and total > 0 and isinstance(met, int):
        metrics["requirement_coverage"] = round(min(met, total) / total, 6)
    if report.get("verification_ran") is True and isinstance(report.get("verification_passed"), bool):
        metrics["verification_passed"] = 1.0 if report["verification_passed"] else 0.0
    tests_total = report.get("tests_total")
    tests_passed = report.get("tests_passed")
    if isinstance(tests_total, int) and tests_total > 0 and isinstance(tests_passed, int):
        metrics["test_pass_rate"] = round(min(tests_passed, tests_total) / tests_total, 6)
    return metrics


def is_usable(report: dict[str, Any] | None) -> bool:
    """A form counts as observed only when it yields at least one metric."""
    return bool(report_metrics(report))


# ---------------------------------------------------------------- parsing


def _json_objects(text: str) -> list[dict[str, Any]]:
    """Every balanced JSON object in the text, in order of appearance."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return found
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        index = max(end, start + 1)


def parse_report(output: str) -> tuple[dict[str, Any] | None, str]:
    """Extract the form from agent output. Returns (report, status).

    The marker is preferred; otherwise the last JSON object that looks like the
    form wins, because agents usually print the report after their prose.
    """
    text = output or ""
    if not text.strip():
        return None, "missing"
    marker = text.rfind(REPORT_MARKER)
    candidates = _json_objects(text[marker + len(REPORT_MARKER):] if marker >= 0 else text)
    shaped = [
        item for item in candidates
        if len(set(item) & set(QUALITY_FIELD_NAMES)) >= 3
    ]
    if not shaped:
        if marker >= 0 and candidates:
            return None, "invalid"
        return None, "missing"
    report = normalize_report(shaped[-1])
    return (report, "observed") if is_usable(report) else (report, "invalid")


# ---------------------------------------------------------------- consolidation


def _entry(task_id: str, agent: str, report: dict[str, Any] | None, status: str, order: int) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "agent": agent,
        "order": order,
        "report_status": status,
        "report": report,
    }


def consolidation(
    entries: list[dict[str, Any]],
    *,
    report: dict[str, Any] | None,
    source: str,
    reason: str = "",
) -> dict[str, Any]:
    if source not in CONSOLIDATION_SOURCES:
        raise ReportError(f"unknown consolidation source: {source}")
    return {
        "schema_version": CONSOLIDATED_SCHEMA,
        "report_contract_id": REPORT_CONTRACT_ID,
        "source": source,
        "report": report,
        "report_status": "observed" if is_usable(report) else "missing",
        "metrics": report_metrics(report),
        "inputs": [
            {
                "task_id": item["task_id"],
                "agent": item["agent"],
                "order": item["order"],
                "report_status": item["report_status"],
            }
            for item in entries
        ],
        "reason": reason,
    }


def finishing_consolidation(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """No intelligence layer: the finishing agent's form is authoritative."""
    usable = [item for item in entries if item.get("report_status") == "observed"]
    if not usable:
        return consolidation(entries, report=None, source="none", reason="no_agent_report")
    winner = max(usable, key=lambda item: item.get("order", 0))
    source = "single_agent" if len(entries) <= 1 else "finishing_agent"
    return consolidation(
        entries,
        report=winner["report"],
        source=source,
        reason=f"finishing_agent:{winner['task_id']}",
    )


def merge_reports(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministic roll-up used as the summary skeleton and LLM fallback.

    Counts add up across subtasks; flags are conjunctive (one unverified or
    blocked subtask keeps the whole chain unverified/blocked).
    """
    usable = [item["report"] for item in entries if item.get("report_status") == "observed"]
    if not usable:
        return None
    merged: dict[str, Any] = {"schema_version": REPORT_SCHEMA}
    for key in (
        "requirements_total", "requirements_met", "tests_total", "tests_passed",
        "input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens",
    ):
        values = [row[key] for row in usable if isinstance(row.get(key), int)]
        if values:
            merged[key] = sum(values)
    durations = [row["duration_s"] for row in usable if isinstance(row.get("duration_s"), (int, float))]
    if durations:
        merged["duration_s"] = round(sum(float(item) for item in durations), 3)
    ran = [row.get("verification_ran") for row in usable if isinstance(row.get("verification_ran"), bool)]
    if ran:
        merged["verification_ran"] = all(ran)
    passed = [row.get("verification_passed") for row in usable if isinstance(row.get("verification_passed"), bool)]
    if merged.get("verification_ran") and passed:
        merged["verification_passed"] = all(passed)
    else:
        merged.pop("verification_passed", None)
    blocked = [row.get("blocked") for row in usable if isinstance(row.get("blocked"), bool)]
    if blocked:
        merged["blocked"] = any(blocked)
    for key in ("files_changed", "commands_run"):
        seen: list[str] = []
        for row in usable:
            for item in row.get(key) or []:
                if item not in seen:
                    seen.append(item)
        if seen:
            merged[key] = seen[:_MAX_LIST]
    reasons = [str(row.get("blocked_reason") or "").strip() for row in usable]
    merged["blocked_reason"] = "; ".join(r for r in reasons if r)[:_MAX_TEXT]
    summaries = [str(row.get("summary") or "").strip() for row in usable]
    merged["summary"] = " ".join(s for s in summaries if s)[:_MAX_TEXT]
    return normalize_report(merged)


def has_consumption(report: dict[str, Any] | None) -> bool:
    """Token counts and duration were filled — zero is a claim, missing is not."""
    report = report or {}
    return (
        isinstance(report.get("input_tokens"), int)
        and isinstance(report.get("output_tokens"), int)
        and isinstance(report.get("duration_s"), (int, float))
    )


def usage_from_report(report: dict[str, Any] | None) -> dict[str, int]:
    report = report or {}
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens"):
        value = report.get(key)
        if isinstance(value, int) and value >= 0:
            usage[key] = value
    return usage


def apply_measured_consumption(
    report: dict[str, Any] | None,
    *,
    usage: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any] | None:
    """Fill omitted consumption from runner measurement / provider usage."""
    if report is None:
        return None
    filled = dict(report)
    usage = usage or {}
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens"):
        if not isinstance(filled.get(key), int) and int(usage.get(key, 0) or 0) > 0:
            filled[key] = int(usage[key])
    if not isinstance(filled.get("duration_s"), (int, float)) and duration_ms:
        filled["duration_s"] = round(int(duration_ms) / 1000.0, 3)
    return normalize_report(filled)
