"""External model evaluation baselines with auditable freshness.

External benchmark results are priors, not ICLE experience.  A result is only
usable when it is matched to a concrete model identity and the cached source is
fresh.  Agent/CLI names are never treated as model names.

The fetchers intentionally use public, maintainer-published benchmark pages:
SWE-bench's machine-readable leaderboard and Aider's published benchmark
 tables.  Each snapshot stores the source URL, retrieval time, content hash,
parser version, and normalized records so a later ranking remains explainable.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import tomllib
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

SCHEMA = "icle-external-evaluation-snapshot/v0.2"
LEGACY_SCHEMAS = {"icle-external-evaluation-snapshot/v0.1"}
PARSER_VERSION = "external-evaluation-v0.2"
EXTERNAL_DIRNAME = "external-evaluations"
MAX_RESULT_AGE_DAYS = 365

# A benchmark is relevant only to the task domains listed here.  Scores from
# unrelated domains are never silently averaged into a recommendation.  The
# metadata is part of the decision contract: a score without a license,
# metric, evaluation scope, and freshness policy is not a strong prior.
SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "swebench_verified": {
        "url": "https://www.swebench.com/",
        "repository": "https://github.com/swebench/swebench",
        "license": "MIT",
        "license_url": "https://github.com/swebench/swebench/blob/main/LICENSE",
        "authority": "benchmark_maintainer",
        "benchmark": "SWE-bench Verified",
        "benchmark_version": "Verified",
        "domain": "coding",
        "metric": "resolved_rate",
        "score_unit": "fraction",
        "evaluation_scope": "agent_model",
        "supports_reasoning": False,
        "result_date_required": True,
        "provenance_tier": "official_leaderboard",
        "ttl_days": 45,
        "parser": "swebench_html_json",
    },
    "aider_edit": {
        "url": "https://aider.chat/docs/leaderboards/edit.html",
        "repository": "https://github.com/Aider-AI/aider",
        "license": "Apache-2.0",
        "license_url": "https://github.com/Aider-AI/aider/blob/main/LICENSE.txt",
        "authority": "project_maintainer",
        "benchmark": "Aider Code Editing",
        "benchmark_version": "published leaderboard",
        "domain": "coding",
        "metric": "correct_edit_percent",
        "score_unit": "fraction",
        "evaluation_scope": "model_only",
        "supports_reasoning": False,
        "result_date_required": True,
        "provenance_tier": "official_leaderboard",
        "ttl_days": 30,
        "parser": "aider_html_table",
    },
    "aider_refactor": {
        "url": "https://aider.chat/docs/leaderboards/refactor.html",
        "repository": "https://github.com/Aider-AI/aider",
        "license": "Apache-2.0",
        "license_url": "https://github.com/Aider-AI/aider/blob/main/LICENSE.txt",
        "authority": "project_maintainer",
        "benchmark": "Aider Refactoring",
        "benchmark_version": "published leaderboard",
        "domain": "refactoring",
        "metric": "correct_edit_percent",
        "score_unit": "fraction",
        "evaluation_scope": "model_only",
        "supports_reasoning": False,
        "result_date_required": True,
        "provenance_tier": "official_leaderboard",
        "ttl_days": 30,
        "parser": "aider_html_table",
    },
    "terminal_bench_2_1": {
        "url": "https://www.tbench.ai/leaderboard/terminal-bench/2.1",
        "repository": "https://github.com/harbor-framework/terminal-bench-2-1",
        "license": "Apache-2.0",
        "license_url": "https://github.com/harbor-framework/terminal-bench-2-1/blob/main/LICENSE",
        "authority": "benchmark_maintainer_verified",
        "benchmark": "Terminal-Bench 2.1",
        "benchmark_version": "2.1",
        "domain": "terminal",
        "metric": "accuracy",
        "score_unit": "fraction",
        "evaluation_scope": "agent_model",
        "supports_reasoning": True,
        "result_date_required": True,
        "provenance_tier": "verified_submission",
        "ttl_days": 30,
        "parser": "terminal_bench_next",
    },
}

DOMAIN_ALIASES = {
    "coding": "coding",
    "code": "coding",
    "bug": "coding",
    "debugging": "coding",
    "implementation": "coding",
    "refactor": "refactoring",
    "refactoring": "refactoring",
    "terminal": "terminal",
    "tool-use": "terminal",
    "research": "research",
    "planning": "planning",
    "plan": "planning",
    "writing": "writing",
    "documentation": "writing",
}


class ExternalEvaluationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_result_date(value: str | None) -> datetime | None:
    """Parse benchmark dates without changing their natural-date meaning."""
    parsed = _parse_time(value)
    if parsed is not None:
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as handle:
        handle.write(content)
        temp = handle.name
    Path(temp).replace(path)


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_model(value: str | None) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^(openrouter|openai|anthropic|google|deepseek|kimi|ollama|provider)[/:]", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _model_aliases(value: str | None) -> list[str]:
    """Return conservative aliases for one displayed model identity.

    Do not tokenize a display label into arbitrary words.  That made labels
    such as ``Gemini ... (whole)`` match the unrelated model ``whole`` and
    allowed provider/CLI text to become a false model identity.  Provider
    prefixes and documented reasoning suffixes are handled explicitly.
    """
    raw = str(value or "").strip()
    if not raw:
        return []
    candidates = [raw]
    without_brackets = re.sub(r"\s*\[[^]]+\]\s*$", "", raw).strip()
    if without_brackets != raw:
        candidates.append(without_brackets)
    without_parenthetical = re.sub(
        r"\s*\((?:thinking|reasoning|high|medium|med|low|whole|diff|patch)\)\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    if without_parenthetical != raw:
        candidates.append(without_parenthetical)
    for item in tuple(candidates):
        if "/" in item:
            candidates.append(item.rsplit("/", 1)[-1].strip())
    out: list[str] = []
    for item in candidates:
        normalized = _normalize_model(item)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _model_family(value: str | None) -> str | None:
    """Return an explicit model-family key for documented sibling variants.

    This is deliberately narrower than prefix matching.  The GPT-5.6
    ``sol/terra/luna`` variants are a documented family; arbitrary names such
    as ``gpt-5`` and ``gpt-5.6-sol`` must not match one another.
    """
    normalized = _normalize_model(value)
    match = re.fullmatch(r"(gpt\d+)(?:sol|terra|luna)", normalized)
    return match.group(1) if match else None


def _model_family_matches(identity: dict[str, Any], record: dict[str, Any]) -> bool:
    identity_family = _model_family(identity.get("model"))
    record_family = _model_family(record.get("model"))
    return bool(identity_family and record_family and identity_family == record_family)


def _model_matches(identity: dict[str, Any], record: dict[str, Any]) -> bool:
    identity_aliases = set(identity.get("aliases") or [])
    record_aliases = set(record.get("model_aliases") or [])
    if not identity_aliases or not record_aliases:
        return False
    # External scores are inherited only on exact normalized identity.  Prefix
    # or substring matching is unsafe: GPT-5.6-sol must not inherit GPT-5, and
    # DeepSeek V4 must not inherit DeepSeek V3.  Provider identity is an
    # additional boundary when both sides publish it.
    identity_provider = str(identity.get("provider") or "").strip().casefold()
    record_provider = str(record.get("provider") or "").strip().casefold()
    if identity_provider and record_provider and identity_provider != record_provider:
        return False
    return bool(identity_aliases & record_aliases)


def _agent_aliases(value: str | None) -> list[str]:
    normalized = _normalize_model(value)
    aliases = {normalized} if normalized else set()
    known = {
        "claude": {"claude", "claudecode"},
        "codex": {"codex", "openaicodex"},
        "gemini": {"gemini", "geminicli"},
        "kimi": {"kimi", "kimicode"},
        "aider": {"aider"},
        "hermes": {"hermes"},
        "opencode": {"opencode"},
        "pi": {"pi"},
        "qwen": {"qwen", "qwencode"},
    }
    for values in known.values():
        if normalized in values:
            aliases.update(values)
    return sorted(aliases)


def _agent_match(agent_id: str, record: dict[str, Any]) -> bool:
    return bool(set(_agent_aliases(agent_id)) & set(record.get("agent_aliases") or []))


def normalize_domain(value: str | None) -> str:
    raw = str(value or "coding").strip().lower()
    return DOMAIN_ALIASES.get(raw, raw or "coding")


class _TableParser(HTMLParser):
    """Extract visible table cell text without depending on page CSS."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = " ".join(" ".join(self._cell).split())
            self._row.append(html.unescape(text))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _record(
    *, source_id: str, benchmark: str, domain: str, model: str,
    score: float, retrieved_at: str, result_date: str | None = None,
    evaluated_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    catalog = SOURCE_CATALOG[source_id]
    metadata = metadata or {}
    return {
        "source_id": source_id,
        "benchmark": benchmark,
        "benchmark_version": catalog.get("benchmark_version"),
        "domain": normalize_domain(domain),
        "metric": catalog.get("metric"),
        "score_unit": catalog.get("score_unit", "fraction"),
        "model": model,
        "model_aliases": _model_aliases(model),
        "evaluated_agent": evaluated_agent,
        "agent_aliases": _agent_aliases(evaluated_agent),
        "reasoning_effort": reasoning_effort or metadata.get("reasoning_effort"),
        "sample_count": metadata.get("n_trials") or metadata.get("sample_count"),
        "standard_error": metadata.get("accuracy_stderr") or metadata.get("stderr"),
        "score": round(max(0.0, min(1.0, float(score))), 4),
        "result_date": result_date,
        "retrieved_at": retrieved_at,
        "metadata": metadata,
    }


def _parse_swebench(source_id: str, content: str, retrieved_at: str) -> list[dict[str, Any]]:
    marker = '<script type="application/json" id="leaderboard-data">'
    start = content.find(marker)
    if start < 0:
        raise ExternalEvaluationError("SWE-bench leaderboard JSON was not found")
    start += len(marker)
    end = content.find("</script>", start)
    if end < 0:
        raise ExternalEvaluationError("SWE-bench leaderboard JSON is incomplete")
    try:
        boards = json.loads(content[start:end])
    except json.JSONDecodeError as exc:
        raise ExternalEvaluationError("SWE-bench leaderboard JSON is invalid") from exc
    catalog = SOURCE_CATALOG[source_id]
    records: list[dict[str, Any]] = []
    for board in boards if isinstance(boards, list) else []:
        if str(board.get("name", "")).lower() != "verified":
            continue
        for item in board.get("results", []):
            if item.get("warning") or item.get("resolved") in (None, ""):
                continue
            try:
                score = float(str(item["resolved"]).rstrip("%"))
                if score > 1:
                    score /= 100.0
            except (TypeError, ValueError):
                continue
            model = str(item.get("model_display") or item.get("name") or "").strip()
            if not model:
                continue
            records.append(_record(
                source_id=source_id,
                benchmark=catalog["benchmark"],
                domain=catalog["domain"],
                model=model,
                score=score,
                retrieved_at=retrieved_at,
                result_date=str(item.get("date") or "") or None,
                evaluated_agent=str(item.get("agent") or "") or None,
                metadata={
                    "model_name": item.get("name"),
                    "model_org": item.get("model_org"),
                    "agent": item.get("agent"),
                    "site": item.get("site"),
                },
            ))
    if not records:
        raise ExternalEvaluationError("SWE-bench Verified has no usable model results")
    return records


def _parse_aider(source_id: str, content: str, retrieved_at: str) -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(content)
    catalog = SOURCE_CATALOG[source_id]
    records: list[dict[str, Any]] = []
    for row in parser.rows:
        if len(row) < 2 or row[0].lower() == "model":
            continue
        model = row[0].strip()
        if not model or model.lower() in {"model", "total", "average"}:
            continue
        percentages = re.findall(r"(\d+(?:\.\d+)?)\s*%", " ".join(row[1:]))
        if not percentages:
            continue
        try:
            score = float(percentages[0]) / 100.0
        except ValueError:
            continue
        records.append(_record(
            source_id=source_id,
            benchmark=catalog["benchmark"],
            domain=catalog["domain"],
            model=model,
            score=score,
            retrieved_at=retrieved_at,
            evaluated_agent="Aider",
            metadata={"correct_edit_percent": percentages[0], "table_columns": row[1:]},
        ))
    if not records:
        raise ExternalEvaluationError("Aider leaderboard has no usable model rows")
    return records


def _decode_next_payload(content: str) -> str:
    chunks: list[str] = []
    for raw in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', content, re.S):
        try:
            chunks.append(json.loads('"' + raw + '"'))
        except json.JSONDecodeError:
            continue
    return "\n".join(chunks)


def _label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("name") or "")
    return str(value or "")


def _parse_terminal_bench(source_id: str, content: str, retrieved_at: str) -> list[dict[str, Any]]:
    payload = _decode_next_payload(content)
    marker = '"rows":['
    start = payload.find(marker)
    if start < 0:
        raise ExternalEvaluationError("Terminal-Bench rows were not found")
    try:
        rows, _ = json.JSONDecoder().raw_decode(payload[start + len('"rows":'):])
    except json.JSONDecodeError as exc:
        raise ExternalEvaluationError("Terminal-Bench rows are invalid") from exc
    catalog = SOURCE_CATALOG[source_id]
    records: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        metadata = row.get("metadata") or {}
        metrics = row.get("metrics") or {}
        model = _label(metadata.get("model_display")).strip()
        agent = _label(metadata.get("agent_display")).strip()
        try:
            score = float(metrics.get("accuracy"))
            if score > 1:
                score /= 100.0
        except (TypeError, ValueError):
            continue
        if not model or not agent:
            continue
        records.append(_record(
            source_id=source_id,
            benchmark=catalog["benchmark"],
            domain=catalog["domain"],
            model=model,
            score=score,
            retrieved_at=retrieved_at,
            result_date=str(metadata.get("date") or "") or None,
            evaluated_agent=agent,
            reasoning_effort=str(metadata.get("reasoning_effort") or "") or None,
            metadata={
                "rank": row.get("rank"),
                "n_trials": metrics.get("n_trials") or row.get("n_trials"),
                "accuracy_stderr": metrics.get("accuracy_stderr"),
                "reasoning_effort": metadata.get("reasoning_effort"),
                "agent_url": (metadata.get("agent_display") or {}).get("url"),
                "model_url": (metadata.get("model_display") or {}).get("url"),
                "verification_url": (metadata.get("pr_url") or {}).get("url"),
            },
        ))
    if not records:
        raise ExternalEvaluationError("Terminal-Bench has no usable verified rows")
    return records


def _parse_source(source_id: str, content: str, retrieved_at: str) -> list[dict[str, Any]]:
    config = SOURCE_CATALOG.get(source_id)
    if config is None:
        raise ExternalEvaluationError(f"unknown external evaluation source: {source_id}")
    if config["parser"] == "swebench_html_json":
        return _parse_swebench(source_id, content, retrieved_at)
    if config["parser"] == "aider_html_table":
        return _parse_aider(source_id, content, retrieved_at)
    if config["parser"] == "terminal_bench_next":
        return _parse_terminal_bench(source_id, content, retrieved_at)
    raise ExternalEvaluationError(f"unsupported parser: {config['parser']}")


def _upgrade_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Add v0.2 metadata in memory without rewriting historical snapshots."""
    source_id = snapshot.get("source_id")
    catalog = SOURCE_CATALOG.get(source_id, {})
    upgraded = dict(snapshot)
    for key in (
        "license", "license_url", "repository", "benchmark_version", "metric",
        "score_unit", "evaluation_scope", "provenance_tier",
    ):
        if key not in upgraded and catalog.get(key) is not None:
            upgraded[key] = catalog[key]
    records = []
    raw_records = snapshot.get("records") if isinstance(snapshot.get("records"), list) else []
    for original in raw_records:
        record = dict(original) if isinstance(original, dict) else {}
        model = str(record.get("model") or "")
        if not record.get("model_aliases") and model:
            record["model_aliases"] = _model_aliases(model)
        record.setdefault("benchmark_version", upgraded.get("benchmark_version"))
        record.setdefault("metric", upgraded.get("metric"))
        record.setdefault("score_unit", upgraded.get("score_unit", "fraction"))
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        record.setdefault("reasoning_effort", metadata.get("reasoning_effort"))
        record.setdefault("sample_count", metadata.get("n_trials") or metadata.get("sample_count"))
        record.setdefault("standard_error", metadata.get("accuracy_stderr") or metadata.get("stderr"))
        records.append(record)
    upgraded["records"] = records
    return upgraded


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") not in {SCHEMA, *LEGACY_SCHEMAS}:
        raise ExternalEvaluationError("external snapshot schema_version mismatch")
    for field in ("source_id", "source_url", "retrieved_at", "fresh_until", "content_hash"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ExternalEvaluationError(f"external snapshot missing {field}")
    if snapshot["source_id"] not in SOURCE_CATALOG:
        raise ExternalEvaluationError("external snapshot has unknown source")
    for field in ("retrieved_at", "fresh_until"):
        if _parse_time(snapshot[field]) is None:
            raise ExternalEvaluationError(f"external snapshot has invalid {field}")
    if snapshot.get("schema_version") == SCHEMA:
        for field in ("license", "benchmark_version", "metric", "evaluation_scope", "provenance_tier"):
            if not isinstance(snapshot.get(field), str) or not snapshot[field]:
                raise ExternalEvaluationError(f"external snapshot missing {field}")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ExternalEvaluationError("external snapshot records must be a list")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("model_aliases"):
            raise ExternalEvaluationError("external snapshot record has no model identity")
        score = record.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ExternalEvaluationError("external snapshot score must be 0..1")
        result_date = record.get("result_date")
        if result_date is not None and _parse_result_date(str(result_date)) is None:
            raise ExternalEvaluationError("external snapshot record has invalid result_date")
        # One source may publish multiple independent runs for the same model
        # and date. Reject only a byte-for-byte semantic duplicate; do not
        # collapse differing scores or configurations into one fictitious run.
        fingerprint = hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            raise ExternalEvaluationError("external snapshot contains duplicate evaluation records")
        seen.add(fingerprint)


def _snapshot_dir(store: str | Path) -> Path:
    return Path(store) / EXTERNAL_DIRNAME


def list_snapshots(store: str | Path) -> list[dict[str, Any]]:
    """Return the newest valid snapshot per source; older files remain auditable."""
    directory = _snapshot_dir(store)
    newest: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return []
    for path in sorted(directory.glob("*.json")):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            validate_snapshot(snapshot)
            snapshot = _upgrade_snapshot(snapshot)
        except (OSError, json.JSONDecodeError, ExternalEvaluationError):
            continue
        source_id = snapshot["source_id"]
        previous = newest.get(source_id)
        if previous is None or snapshot["retrieved_at"] > previous["retrieved_at"]:
            newest[source_id] = snapshot
    return [newest[key] for key in sorted(newest)]


def save_snapshot(store: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Append a timestamped source snapshot instead of overwriting history."""
    validate_snapshot(snapshot)
    suffix = hashlib.sha256(
        f"{snapshot['retrieved_at']}:{snapshot['content_hash']}".encode("utf-8")
    ).hexdigest()[:12]
    path = _snapshot_dir(store) / f"{snapshot['source_id']}__{suffix}.json"
    _atomic_write(path, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    return snapshot


def dataset_quality(store: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Return an auditable health summary for the cached external datasets.

    This is deliberately a read-only report. It counts invalid/undated/stale
    rows but never silently repairs, deletes, or promotes them into a score.
    """
    now = now or datetime.now(timezone.utc)
    directory = _snapshot_dir(store)
    report: list[dict[str, Any]] = []
    invalid_files = 0
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                validate_snapshot(raw)
            except (OSError, json.JSONDecodeError, ExternalEvaluationError):
                invalid_files += 1
    for snapshot in list_snapshots(store):
        records = snapshot.get("records", [])
        dated = 0
        current = 0
        undated = 0
        stale_result = 0
        for record in records:
            result_date = record.get("result_date")
            if not result_date:
                undated += 1
                continue
            dated += 1
            if _record_current(record, now):
                current += 1
            else:
                stale_result += 1
        config = SOURCE_CATALOG.get(snapshot["source_id"], {})
        report.append({
            "source_id": snapshot["source_id"],
            "benchmark": snapshot.get("benchmark"),
            "benchmark_version": snapshot.get("benchmark_version"),
            "domain": snapshot.get("domain"),
            "metric": snapshot.get("metric"),
            "license": snapshot.get("license"),
            "license_complete": bool(snapshot.get("license") and snapshot.get("license_url")),
            "provenance_tier": snapshot.get("provenance_tier"),
            "retrieved_at": snapshot.get("retrieved_at"),
            "fresh_until": snapshot.get("fresh_until"),
            "snapshot_fresh": _snapshot_fresh(snapshot, now),
            "records": len(records),
            "dated_records": dated,
            "current_records": current,
            "undated_records": undated,
            "stale_result_records": stale_result,
            "result_date_coverage": round(dated / len(records), 4) if records else 0.0,
            "usable_current_records": current,
            "quality_status": (
                "healthy" if current else "undated-only" if undated and not current else "stale"
            ),
            "configured_result_date_required": bool(config.get("result_date_required")),
        })
    return {
        "schema_version": "icle-external-evaluation-quality/v0.1",
        "generated_at": _now(),
        "invalid_snapshot_files": invalid_files,
        "sources": report,
    }


def default_fetch(url: str, *, timeout: float = 15.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ICLE external-evaluation-sync/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def refresh_snapshots(
    store: str | Path,
    *,
    source_ids: list[str] | None = None,
    timeout: float = 15.0,
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Fetch and persist public benchmark snapshots; failures never erase cache."""
    selected = source_ids or list(SOURCE_CATALOG)
    fetch = fetcher or (lambda url: default_fetch(url, timeout=timeout))
    results: list[dict[str, Any]] = []
    for source_id in selected:
        config = SOURCE_CATALOG.get(source_id)
        if config is None:
            results.append({"source_id": source_id, "ok": False, "error": "unknown source"})
            continue
        retrieved_at = _now()
        try:
            content = fetch(config["url"])
            records = _parse_source(source_id, content, retrieved_at)
            fresh_until = (
                datetime.now(timezone.utc) + timedelta(days=int(config["ttl_days"]))
            ).isoformat()
            snapshot = {
                "schema_version": SCHEMA,
                "source_id": source_id,
                "source_url": config["url"],
                "repository": config.get("repository"),
                "license": config.get("license"),
                "license_url": config.get("license_url"),
                "authority": config["authority"],
                "benchmark": config["benchmark"],
                "benchmark_version": config.get("benchmark_version"),
                "domain": config["domain"],
                "metric": config.get("metric"),
                "score_unit": config.get("score_unit", "fraction"),
                "evaluation_scope": config.get("evaluation_scope"),
                "provenance_tier": config.get("provenance_tier"),
                "retrieved_at": retrieved_at,
                "fresh_until": fresh_until,
                "content_hash": _content_hash(content),
                "parser_version": PARSER_VERSION,
                "records": records,
            }
            save_snapshot(store, snapshot)
            results.append({
                "source_id": source_id,
                "ok": True,
                "records": len(records),
                "retrieved_at": retrieved_at,
                "fresh_until": fresh_until,
            })
        except Exception as exc:
            results.append({"source_id": source_id, "ok": False, "error": str(exc)})
    return {"sources": results, "snapshots": len(list_snapshots(store)), "created_at": _now()}


def _snapshot_fresh(snapshot: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    fresh_until = _parse_time(snapshot.get("fresh_until"))
    return bool(fresh_until and now <= fresh_until)


def _record_current(record: dict[str, Any], now: datetime | None = None) -> bool:
    """A recent fetch cannot make an old or undated benchmark result current."""
    now = now or datetime.now(timezone.utc)
    result_at = _parse_result_date(record.get("result_date"))
    if result_at is None:
        return False
    age = (now - result_at).days
    return -7 <= age <= MAX_RESULT_AGE_DAYS


def external_baseline(
    store: str | Path,
    *,
    model_identity: dict[str, Any] | None,
    agent_id: str | None = None,
    domain: str = "coding",
    allow_model_family: bool = False,
) -> dict[str, Any]:
    """Return a fresh model-matched prior.

    Model-family matching is opt-in for a clearly labeled cold-start profile
    estimate. Routing and ordinary external ranking remain exact-model first.
    """
    identity = model_identity or {}
    domain = normalize_domain(domain)
    matched: list[dict[str, Any]] = []
    stale_matches: list[dict[str, Any]] = []
    match_weights = {
        "agent+model": 1.0,
        "model-only": 0.65,
        "agent-only": 0.35,
        "agent+model-family": 0.55,
        "model-family": 0.45,
    }
    for snapshot in list_snapshots(store):
        for record in snapshot.get("records", []):
            record_domain = normalize_domain(record.get("domain"))
            if domain != "general" and record_domain != domain:
                continue
            model_match = _model_matches(identity, record)
            family_match = allow_model_family and _model_family_matches(identity, record)
            agent_match = bool(agent_id and _agent_match(agent_id, record))
            if model_match and agent_match:
                match_kind = "agent+model"
            elif model_match:
                match_kind = "model-only"
            elif family_match and agent_match:
                match_kind = "agent+model-family"
            elif family_match:
                match_kind = "model-family"
            else:
                # Agent-only rows are useful as shell evidence, but are not a
                # model prior. A known model must never inherit another model's
                # benchmark score merely because the CLI brand matches.
                continue
            enriched = {
                **record,
                "match_kind": match_kind,
                "match_weight": match_weights[match_kind],
                "source_url": snapshot["source_url"],
                "authority": snapshot["authority"],
                "license": snapshot.get("license"),
                "license_url": snapshot.get("license_url"),
                "repository": snapshot.get("repository"),
                "benchmark_version": snapshot.get("benchmark_version"),
                "metric": snapshot.get("metric"),
                "evaluation_scope": snapshot.get("evaluation_scope"),
                "provenance_tier": snapshot.get("provenance_tier"),
                "fresh_until": snapshot["fresh_until"],
                "snapshot_retrieved_at": snapshot["retrieved_at"],
            }
            current = _snapshot_fresh(snapshot) and _record_current(record)
            (matched if current else stale_matches).append(enriched)
    snapshots = [
        {
            "source_id": snapshot["source_id"],
            "benchmark": snapshot["benchmark"],
            "source_url": snapshot["source_url"],
            "authority": snapshot["authority"],
            "license": snapshot.get("license"),
            "license_url": snapshot.get("license_url"),
            "repository": snapshot.get("repository"),
            "benchmark_version": snapshot.get("benchmark_version"),
            "metric": snapshot.get("metric"),
            "evaluation_scope": snapshot.get("evaluation_scope"),
            "provenance_tier": snapshot.get("provenance_tier"),
            "retrieved_at": snapshot["retrieved_at"],
            "fresh_until": snapshot["fresh_until"],
            "fresh": _snapshot_fresh(snapshot),
        }
        for snapshot in list_snapshots(store)
        if domain == "general" or normalize_domain(snapshot.get("domain")) == domain
    ]
    if not matched:
        unresolved = not identity.get("model")
        return {
            "score": None,
            "status": "unresolved-model" if unresolved else ("stale" if stale_matches else "unavailable"),
            "confidence": "none",
            "model": identity.get("model"),
            "model_source": identity.get("source"),
            "records": stale_matches,
            "sources_checked": snapshots,
            "reason": (
                "model identity is unresolved; external results cannot be matched safely"
                if unresolved
                else "matched model baseline is stale"
                if stale_matches
                else "fresh authoritative sources were checked but no result matches this concrete model"
            ),
        }
    # Within one benchmark keep only the strongest match tier.  A documented
    # model-family result must not be diluted by a weaker Agent-only row from
    # the same leaderboard. Then collapse rows per benchmark so a source with
    # many configurations cannot dominate the prior.
    strength_order = {
        "agent+model": 5,
        "model-only": 4,
        "agent+model-family": 3,
        "model-family": 2,
        "agent-only": 1,
    }
    by_source_all: dict[str, list[dict[str, Any]]] = {}
    for item in matched:
        by_source_all.setdefault(item["source_id"], []).append(item)
    by_source: dict[str, list[dict[str, Any]]] = {}
    for source_id, items in by_source_all.items():
        strongest_for_source = max(strength_order[item["match_kind"]] for item in items)
        by_source[source_id] = [
            item for item in items
            if strength_order[item["match_kind"]] == strongest_for_source
        ]
    scored_records = [item for items in by_source.values() for item in items]
    source_scores: list[tuple[float, float]] = []
    for items in by_source.values():
        total_weight = sum(float(item["match_weight"]) for item in items)
        # Weak matches are shrunk toward neutral 0.5.  Match weight therefore
        # represents uncertainty instead of merely cancelling in an average.
        source_score = sum(
            (0.5 + float(item["match_weight"]) * (float(item["score"]) - 0.5))
            * float(item["match_weight"])
            for item in items
        ) / total_weight
        source_scores.append((source_score, max(float(item["match_weight"]) for item in items)))
    total_source_weight = sum(weight for _, weight in source_scores)
    score = round(sum(value * weight for value, weight in source_scores) / total_source_weight, 4)
    strongest = max(
        (item["match_kind"] for item in matched),
        key=lambda kind: strength_order[kind],
    )
    reason = {
        "agent+model": f"{len(scored_records)} fresh result(s) match this Agent and exact model",
        "model-only": f"{len(scored_records)} fresh result(s) match the exact model, but were run by another Agent",
        "agent+model-family": f"{len(scored_records)} fresh result(s) match this Agent and the documented model family, not the exact model",
        "model-family": f"{len(scored_records)} fresh result(s) match the documented model family, not the exact model",
        "agent-only": f"{len(scored_records)} fresh result(s) match the Agent shell, but use different models",
    }[strongest]
    return {
        "score": score,
        "status": "fresh",
        "confidence": "medium" if strongest == "agent+model" else "low",
        "match_strength": strongest,
        "model": identity.get("model"),
        "model_source": identity.get("source"),
        "records": scored_records,
        "related_records": matched,
        "sources_checked": snapshots,
        "reason": reason,
    }


def infer_domain(task: str | None) -> str:
    """Infer the routing domain from English or Chinese task text.

    Specialized intent is checked before the coding fallback.  This is a
    deterministic display/routing projection, not an LLM classification.
    """
    text = str(task or "").casefold()
    domain_keywords = (
        ("refactoring", (
            "refactor", "code cleanup", "restructure code", "重构", "整理代码",
            "代码整理", "技术债",
        )),
        ("terminal", (
            "terminal", "shell", "command line", " cli ", "deployment", "deploy",
            "service startup", "environment", "log file", "命令行", "终端", "部署",
            "运维", "环境", "启动失败", "服务启动", "日志", "配置问题", "macos",
        )),
        ("research", (
            "research", "survey", "literature", "benchmark comparison", "investigate",
            "compare benchmarks", "调研", "研究", "文献", "检索", "资料", "公开基准",
            "基准比较", "比较基准", "横向比较",
        )),
        ("planning", (
            "planning", "roadmap", "milestone", "decompose", "workflow design",
            "architecture plan", "规划", "计划", "路线图", "里程碑", "任务拆解",
            "工作流", "方案设计", "架构规划",
        )),
        ("writing", (
            "documentation", "write a blog", "write a report", "write documentation",
            "readme", "撰写", "写作", "说明文档", "项目文档", "实验报告", "文章",
            "小说",
        )),
    )
    compact = re.sub(r"\s+", "", text)
    if compact in {"设计", "规划", "计划"}:
        return "planning"
    if compact in {"调研", "研究", "分析"}:
        return "research"
    padded = f" {text} "
    for domain, keywords in domain_keywords:
        if any(keyword in padded for keyword in keywords):
            return domain
    return "coding"


def _structured_models(value: Any, found: list[tuple[str, str | None]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"model", "model_id", "modelId", "model_name", "modelName"} and isinstance(item, str):
                found.append((item, value.get("provider") if isinstance(value.get("provider"), str) else None))
            else:
                _structured_models(item, found)
    elif isinstance(value, list):
        for item in value:
            _structured_models(item, found)


def _captured_models(store: Path, agent_id: str) -> list[tuple[str, str | None]]:
    capture_root = store.parent / "capture-store" / agent_id
    found: list[tuple[str, str | None]] = []
    if not capture_root.is_dir():
        return found
    for path in list(capture_root.glob("*/events.jsonl"))[:200]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[:2000]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = event.get("content")
            if not isinstance(content, str) or not content.lstrip().startswith(("{", "[")):
                continue
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                continue
            _structured_models(parsed, found)
    return found


def _configured_model_identity(agent_id: str) -> tuple[str, str | None] | None:
    """Read only non-secret model/provider fields from known local config files."""
    home = Path.home()
    try:
        if agent_id == "kimi":
            data = tomllib.loads((home / ".kimi-code/config.toml").read_text(encoding="utf-8"))
            model = data.get("default_model")
            if isinstance(model, str) and model:
                provider, _, bare = model.partition("/")
                return bare or model, provider or None
        if agent_id == "codex":
            data = tomllib.loads((home / ".codex/config.toml").read_text(encoding="utf-8"))
            model = data.get("model")
            if isinstance(model, str) and model:
                return model, str(data.get("model_provider") or "openai")
        if agent_id == "pi":
            data = json.loads((home / ".pi/agent/settings.json").read_text(encoding="utf-8"))
            model = data.get("defaultModel")
            if isinstance(model, str) and model:
                return model, str(data.get("defaultProvider") or "") or None
        if agent_id == "claude":
            data = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
            environment = data.get("env") if isinstance(data.get("env"), dict) else data
            model = environment.get("ANTHROPIC_MODEL")
            if isinstance(model, str) and model:
                return model, "anthropic-compatible"
        if agent_id == "opencode":
            text = (home / ".config/opencode/opencode.jsonc").read_text(encoding="utf-8")
            match = re.search(r'["\']model["\']\s*:\s*["\']([^"\']+)', text)
            if match:
                model = match.group(1)
                provider, _, bare = model.partition("/")
                return bare or model, provider or None
        if agent_id == "hermes":
            text = (home / ".hermes/config.yaml").read_text(encoding="utf-8")
            section = re.search(r"(?ms)^model:\s*\n(?P<body>(?:^[ \t]+.*\n?)*)", text)
            body = section.group("body") if section else ""
            model_match = re.search(r"(?m)^\s+default:\s*([^#\n]+)", body)
            provider_match = re.search(r"(?m)^\s+provider:\s*([^#\n]+)", body)
            if model_match:
                return model_match.group(1).strip().strip("\"'"), (
                    provider_match.group(1).strip().strip("\"'") if provider_match else None
                )
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return None
    return None


def resolve_model_identity(
    store: str | Path,
    agent_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a concrete model from provider IDs, revisions, or structured captures."""
    store = Path(store)
    metadata = metadata or {}
    # Provider Model IDs are explicit and therefore the strongest identity.
    if metadata.get("agent_type") == "provider-model" or "/" in agent_id:
        provider, _, model = agent_id.partition("/")
        if provider and model:
            return {"model": model, "provider": provider, "source": "provider_model", "confidence": "high", "aliases": _model_aliases(model)}

    configured_identity = _configured_model_identity(agent_id)
    if configured_identity is not None:
        model, provider = configured_identity
        return {"model": model, "provider": provider, "source": "local_config", "confidence": "high", "aliases": _model_aliases(model)}

    explicit = store / "agent-models.json"
    if explicit.is_file():
        try:
            configured = json.loads(explicit.read_text(encoding="utf-8"))
            item = configured.get(agent_id) if isinstance(configured, dict) else None
            if isinstance(item, dict) and item.get("model"):
                return {"model": item["model"], "provider": item.get("provider"), "source": "user_binding", "confidence": "high", "aliases": _model_aliases(item["model"])}
        except (OSError, json.JSONDecodeError):
            pass

    observed: list[tuple[str, str | None]] = []
    for directory in (store / "episodes", store / "replays"):
        paths = directory.glob("*.json") if directory.name == "episodes" else directory.glob("*/replay.json")
        for path in paths:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            revisions = [doc.get("source_agent_revision"), doc.get("target_agent_revision")]
            for revision in revisions:
                if isinstance(revision, dict) and revision.get("agent_id") == agent_id:
                    model = str(revision.get("model") or "")
                    if model and model not in {"unknown", "captured-session"}:
                        observed.append((model, revision.get("provider")))
    observed.extend(_captured_models(store, agent_id))
    counts: dict[tuple[str, str | None], int] = {}
    for item in observed:
        model = str(item[0]).strip()
        if model and model.lower() not in {"unknown", "captured-session"}:
            counts[item] = counts.get(item, 0) + 1
    if counts:
        (model, provider), count = max(counts.items(), key=lambda item: item[1])
        return {"model": model, "provider": provider, "source": "observed_session", "confidence": "medium", "observations": count, "aliases": _model_aliases(model)}
    return {"model": None, "provider": None, "source": "unresolved", "confidence": "none", "aliases": []}


def rank_agent(
    store: str | Path,
    *,
    agent_id: str,
    local: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    domain: str = "coding",
) -> dict[str, Any]:
    identity = resolve_model_identity(store, agent_id, metadata=metadata)
    external = external_baseline(
        store,
        model_identity=identity,
        agent_id=agent_id,
        domain=domain,
    )
    local_score = local.get("score") if local else None
    local_count = int((local or {}).get("outcomes", 0)) + int((local or {}).get("pairwise", 0))
    if local_score is not None and external.get("score") is not None:
        local_weight = min(0.8, local_count / (local_count + 5.0))
        score = round((1 - local_weight) * float(external["score"]) + local_weight * float(local_score), 4)
        source = "external+local"
    elif local_score is not None:
        score = round(float(local_score), 4)
        source = "local"
    else:
        score = external.get("score")
        source = "external" if score is not None else "none"
    if source == "external+local":
        confidence = "medium" if local_count >= 5 else "low"
    elif source == "external":
        confidence = external.get("confidence", "low")
    elif source == "local":
        confidence = "medium" if local_count >= 5 else "low"
    else:
        confidence = "insufficient-data"
    return {
        "model_identity": identity,
        "external_baseline": external,
        "local_score": local_score,
        "local_evidence_count": local_count,
        "score": score,
        "score_source": source,
        "confidence": confidence,
        "flags": (["external-baseline-fresh"] if external.get("status") == "fresh" else [])
        + (["external-baseline-stale"] if external.get("status") == "stale" else [])
        + (["model-unresolved"] if identity.get("model") is None else []),
    }
