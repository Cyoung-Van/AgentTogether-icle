"""IntelligenceProvider (P5): pluggable LLM intelligence layer.

Boundary (plan §13): LLM may understand/classify/compare/suggest; its output
is always a PROPOSAL/EVIDENCE that the central system validates before any
state mutation. Every LLM call records model, revision, prompt hash and input
hash (audit-ready by construction).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class IntelligenceError(ValueError):
    pass


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- providers


class IntelligenceProvider(Protocol):
    name: str
    model: str
    revision: str

    def complete(self, prompt: str) -> str: ...


class NoneProvider:
    """Offline provider: refuses LLM work loudly; deterministic fallbacks only."""

    name = "none"
    model = "none"
    revision = "none"

    def complete(self, prompt: str) -> str:
        raise IntelligenceError(
            "provider 'none': LLM intelligence unavailable; use deterministic paths"
        )


class KimiProvider:
    """kimi CLI as an LLM backend (headless `kimi -p`)."""

    name = "kimi"
    model = "kimi-code/k3-256k"
    revision = "kimi-cli"

    def __init__(self, timeout: float = 300):
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        try:
            completed = subprocess.run(
                ["kimi", "-p", prompt],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                env=dict(os.environ),
            )
        except subprocess.TimeoutExpired as exc:
            raise IntelligenceError("kimi provider timeout") from exc
        if completed.returncode != 0:
            raise IntelligenceError(f"kimi provider failed: {completed.stderr.strip()[:200]}")
        return completed.stdout.strip()


class _TemperatureRejected(Exception):
    """The endpoint refused the requested temperature; retry without it."""


class OpenAICompatibleProvider:
    """Any OpenAI-compatible chat-completions API (base_url + key + model).

    The API key is read from the store settings file at call time; it is never
    logged, never returned by the API layer in full, and errors are sanitized.
    """

    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120):
        if not base_url.startswith("https://") and not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost"):
            raise IntelligenceError("base_url must be https (or localhost for testing)")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.revision = f"{self.base_url}"
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        return self.complete_with_usage(prompt)[0]

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 400 and "temperature" in body:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 — the body is best-effort
                    pass
                if "temperature" in detail.lower():
                    raise _TemperatureRejected from exc
            raise

    def complete_with_usage(self, prompt: str) -> tuple[str, dict[str, Any]]:
        """Chat completion returning (content, usage) — usage enables cost
        settlement (v0.4 P11 CostActual). usage may be empty on providers that
        do not return it; callers must treat missing usage as cost=null."""
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        try:
            payload = self._post(body)
        except _TemperatureRejected:
            # Some coding models pin temperature (Kimi's coding endpoint allows
            # only 1). Determinism is preferred, not required — drop the field
            # rather than fail the call.
            body.pop("temperature")
            try:
                payload = self._post(body)
            except Exception as exc:
                raise IntelligenceError(
                    f"openai provider failed: {type(exc).__name__}: {str(exc)[:200]}"
                ) from exc
        except Exception as exc:
            # sanitize: never include the key in error text
            raise IntelligenceError(
                f"openai provider failed: {type(exc).__name__}: {str(exc)[:200]}"
            ) from exc
        try:
            content = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise IntelligenceError(f"openai provider: unexpected response shape") from exc
        usage = payload.get("usage") or {}
        try:
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            clean_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
                "prompt_tokens_details": {
                    "cached_tokens": int(
                        prompt_details.get("cached_tokens", usage.get("prompt_cache_hit_tokens", 0))
                    )
                },
                "completion_tokens_details": {
                    "reasoning_tokens": int(
                        completion_details.get("reasoning_tokens", usage.get("reasoning_tokens", 0))
                    )
                },
            }
        except (TypeError, ValueError):
            clean_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return content, clean_usage


PROVIDERS = {
    "none": NoneProvider,
    "kimi": KimiProvider,
}


def get_provider(name: str) -> IntelligenceProvider:
    if name not in PROVIDERS:
        raise IntelligenceError(f"unknown intelligence provider: {name}")
    return PROVIDERS[name]()


def _parse_json_object(text: str, *, component: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM reply (tolerant of prose)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise IntelligenceError(f"{component}: no JSON object in LLM reply")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise IntelligenceError(f"{component}: invalid JSON in LLM reply: {exc}") from exc
    if not isinstance(value, dict):
        raise IntelligenceError(f"{component}: LLM reply is not a JSON object")
    return value


def _audit(provider: IntelligenceProvider, component: str, prompt: str, output: dict[str, Any]) -> dict[str, Any]:
    """Attach mandatory provenance to every LLM-produced artifact."""
    return {
        "component": component,
        "provider": provider.name,
        "judge_model": provider.model,
        "judge_revision": provider.revision,
        "prompt_sha256": _sha(prompt),
        "input_received": True,
        "created_at": _now(),
        "output": output,
    }


def _invoke_skill(
    provider: IntelligenceProvider,
    mode: str,
    *,
    payload: dict[str, Any],
    refs: list[str],
    language: str,
    component: str,
    script: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """load skill → prompt → complete → validate. Returns (skill, prompt, parsed)."""
    from .skills import build_mode_prompt, load_skill, run_script

    skill = load_skill("task-intelligence")
    prompt = build_mode_prompt(
        skill,
        mode,
        payload=payload,
        refs=refs,
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component=component)
    try:
        parsed = run_script(skill, script, parsed)
    except Exception as exc:
        raise IntelligenceError(f"{component} validation failed: {exc}") from exc
    return skill, prompt, parsed


def _skill_provenance(
    provider: IntelligenceProvider,
    skill: dict[str, Any],
    prompt: str,
    language: str,
) -> dict[str, Any]:
    return {
        "skill": {
            "name": "icle-task-intelligence",
            "version": SKILL_VERSION,
            "sha256": _sha(skill["text"]),
        },
        "provider": provider.name,
        "model": provider.model,
        "revision": provider.revision,
        "prompt_sha256": _sha(prompt),
        "input_received": True,
        "created_at": _now(),
        "language": language,
    }


def propose_tasks(
    provider: IntelligenceProvider,
    events: list[dict[str, Any]],
    language: str = "en",
) -> dict[str, Any]:
    """CLI adapter: session extraction is analyze-session, flattened for the old contract."""
    analysis = analyze_session(provider, events, language=language)
    if analysis.get("status") != "ok" or not analysis.get("tasks"):
        raise IntelligenceError("task_extractor: no tasks proposed")
    proposals = []
    valid_seqs = {event["seq"] for event in events if isinstance(event.get("seq"), int)}
    for task in analysis["tasks"]:
        start = str((task.get("boundaries") or {}).get("start_event") or "")
        end = str((task.get("boundaries") or {}).get("end_event") or "")
        try:
            from_seq = int(start[1:] if start.startswith("e") else start)
            to_seq = int(end[1:] if end.startswith("e") else end)
        except ValueError as exc:
            raise IntelligenceError(f"task_extractor: invalid range in proposal: {task}") from exc
        if from_seq > to_seq or from_seq not in valid_seqs or to_seq not in valid_seqs:
            raise IntelligenceError(f"task_extractor: invalid range in proposal: {task}")
        proposals.append({
            "from_seq": from_seq,
            "to_seq": to_seq,
            "description": str(task.get("goal") or task.get("title") or "")[:300],
            "type": str(task.get("subtype") or task.get("task_type") or "other").lower(),
            "confidence": float((task.get("confidence") or {}).get("boundary") or 0.0),
        })
    prompt_hash = (analysis.get("provenance") or {}).get("prompt_sha256") or ""
    return {
        "component": "task_extractor",
        "provider": provider.name,
        "judge_model": provider.model,
        "judge_revision": provider.revision,
        "prompt_sha256": prompt_hash,
        "input_received": True,
        "created_at": _now(),
        "output": {"tasks": proposals},
        "analysis": analysis,
    }


# ---------------------------------------------------------------- 2. PairwiseJudge

def judge_pair(
    provider: IntelligenceProvider,
    *,
    task: str,
    result_a: str,
    result_b: str,
    language: str = "en",
) -> dict[str, Any]:
    """Skill judge-pair → verdict proposal with full provenance."""
    skill, prompt, parsed = _invoke_skill(
        provider,
        "judge-pair",
        payload={
            "task": task[:1500],
            "result_a": result_a[:4000],
            "result_b": result_b[:4000],
        },
        refs=["judging", "schemas"],
        language=language,
        component="pairwise_judge",
        script="validate_verdict",
    )
    return _audit(provider, "pairwise_judge", prompt, parsed)


# ---------------------------------------------------------------- 3. TaskSimilarity

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[一-鿿]", re.U)
_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "it"}


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if token.lower() not in _STOP}


def similar_episodes(
    episodes: list[dict[str, Any]],
    query: str,
    *,
    project_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Deterministic similarity v0.1: token Jaccard + same-project bonus.

    No LLM needed; semantic/embedding similarity is an LLM-layer upgrade later.
    """
    query_tokens = _tokens(query)
    scored = []
    for episode in episodes:
        request = episode["task_start"]["original_user_request"]
        episode_tokens = _tokens(request)
        if not episode_tokens or not query_tokens:
            similarity = 0.0
        else:
            similarity = len(query_tokens & episode_tokens) / len(query_tokens | episode_tokens)
        bonus = 0.2 if project_id and episode["project_id"] == project_id else 0.0
        scored.append(
            {
                "episode_id": episode["episode_id"],
                "project_id": episode["project_id"],
                "request": request[:80],
                "score": round(similarity + bonus, 4),
                "token_overlap": sorted(query_tokens & episode_tokens)[:10],
            }
        )
    scored.sort(key=lambda item: -item["score"])
    return scored[:limit]


# ---------------------------------------------------------------- 4. FailureAnalyzer

FAILURE_RULES = [
    (re.compile(r"(?i)timeout|timed out"), "timeout"),
    (re.compile(r"(?i)authorization grant|login|unauthorized|no model configured"), "auth"),
    (re.compile(r"(?i)command not found|no such file"), "environment"),
    (re.compile(r"(?i)syntaxerror|indentationerror"), "coding"),
    (re.compile(r"(?i)failed \(failures|assertion|tests? failed"), "test_failure"),
    (re.compile(r"(?i)quota|budget|rate.?limit|429"), "budget"),
    (re.compile(r"(?i)connection error|network|econnrefused"), "network"),
]


def analyze_failure(
    *,
    status: str,
    stderr: str,
    duration_ms: int | None = None,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Deterministic failure classification v0.1 (LLM analysis optional later)."""
    categories = [name for pattern, name in FAILURE_RULES if pattern.search(stderr)]
    if status == "failed" and not categories:
        categories = ["unknown"]
    if status != "failed":
        categories = []
    return {
        "component": "failure_analyzer",
        "provider": "rules",
        "categories": categories or ["not_a_failure"],
        "likely_user_action": _suggest_action(categories),
        "created_at": _now(),
    }


def _suggest_action(categories: list[str]) -> str:
    mapping = {
        "timeout": "increase budget or split the task",
        "auth": "re-login the agent CLI, then resume",
        "environment": "check tool availability in the runtime",
        "coding": "hand back with the syntax error visible",
        "test_failure": "inspect failing tests; may be real capability signal",
        "budget": "retry later or reduce scope",
        "network": "check connectivity/provider status",
        "unknown": "manual trajectory review needed",
    }
    if not categories:
        return "none"
    return mapping.get(categories[0], "manual review")


# ---------------------------------------------------------------- 5. TaskProfiler (UI-23)

# 语言前置条件:前端界面语言 zh/en 透传到 LLM prompt,让生成文本跟随界面。
# 枚举值(CODING/D1/shell 等)与代号保持原样;仅约束 prose(理由/描述/评论)。
def _language_instruction(language: str | None) -> str:
    if language and str(language).lower().startswith("zh"):
        return (
            "IMPORTANT: the user's UI language is Chinese. "
            "All prose you output (reason, description, comments, summaries) "
            "MUST be in Simplified Chinese. Keep enum-like values "
            "(CODING, D1, shell, etc.) and code as-is."
        )
    return (
        "IMPORTANT: the user's UI language is English. "
        "All prose you output (reason, description, comments, summaries) MUST be in English. "
        "Keep enum-like values (CODING, D1, shell, etc.) and code as-is."
    )


def propose_task_profile(
    provider: IntelligenceProvider,
    *,
    title: str,
    description: str,
    language: str = "en",
) -> dict[str, Any]:
    """Skill analyze-task → TaskProfile proposal (validated, never persisted here)."""
    skill, prompt, profile = _invoke_skill(
        provider,
        "analyze-task",
        payload={"title": title[:500], "description": description[:3000]},
        refs=["profiling", "schemas"],
        language=language,
        component="task_profiler",
        script="validate_profile",
    )
    profile["reason"] = str(profile.get("reason", ""))[:500]
    return {
        "schema_version": "icle-task-profile/v0.1",
        **profile,
        "source": "llm",
        "provenance": _skill_provenance(provider, skill, prompt, language),
    }


def propose_rating(
    provider: IntelligenceProvider,
    *,
    task: str,
    result: str,
    language: str = "en",
) -> dict[str, Any]:
    """Skill judge-result → AgentRating proposal (source=llm)."""
    skill, prompt, parsed = _invoke_skill(
        provider,
        "judge-result",
        payload={"task": task[:1500], "result": result[:4000]},
        refs=["judging", "schemas"],
        language=language,
        component="rating_judge",
        script="validate_rating",
    )
    return {
        "schema_version": "icle-agent-rating/v0.1",
        "source": "llm",
        "dimensions": parsed["dimensions"],
        "overall_preference": parsed["overall_preference"],
        "would_use_again": parsed["would_use_again"],
        "comment": parsed["comment"],
        "provenance": _skill_provenance(provider, skill, prompt, language),
    }


# ---------------------------------------------------------------- 7. analyze-session (skill v0.1)
#
# 由 icle-task-intelligence skill 驱动(建议文档 §三-§十):
#   确定性 compact → 规则粗边界 → LLM 语义切段(带 evidence_ref)
#   → Task Reconstruction(original vs final intent) → 行为信号抽取
#   → SessionAnalysisProposal(只输出 Proposal,绝不落库)
# 长会话走 Map → Merge → Verify 分块(建议 §十),确定性校验由
# scripts/validate_analysis.py 完成(evidence 回指/枚举/conf/白名单)。

ANALYZE_CHUNK = 300        # 事件数超过此值 → 分块分析
SKILL_VERSION = "0.2.0"    # 与 SKILL.md frontmatter 保持一致
ANALYSIS_SCHEMA = "icle-session-analysis/v0.1"


def _analyze_chunk(
    provider: IntelligenceProvider,
    skill: dict,
    bundle: dict,
    language: str,
) -> dict:
    """对单个 chunk(或整段)执行 analyze-session:组装 prompt → LLM → 结构化。"""
    from .skills import build_mode_prompt

    prompt = build_mode_prompt(
        skill, "analyze-session",
        payload={"bundle": bundle},
        refs=["session-analysis", "schemas"],
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="session_analyzer")
    parsed["schema_version"] = ANALYSIS_SCHEMA
    parsed.setdefault("session_id", bundle.get("session_id"))
    parsed.setdefault("status", "ok")
    parsed.setdefault("facts", [])
    parsed.setdefault("inferences", [])
    parsed.setdefault("proposals", [])
    parsed.setdefault("confidence", {})
    parsed.setdefault("tasks", [])
    return parsed


def _chunk_bundle(bundle: dict, size: int | None = None) -> list[dict]:
    """按规则边界优先、size 兜底切块;每块是独立 bundle(事件+空边界)。

    size 默认取模块级 ANALYZE_CHUNK(运行时解析,便于测试调小阈值)。
    """
    size = size or ANALYZE_CHUNK
    events = bundle.get("events", [])
    boundary_ids = {b["event_id"] for b in bundle.get("boundaries", [])}
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for ev in events:
        current.append(ev)
        if ev["event_id"] in boundary_ids and len(current) >= size // 2:
            chunks.append(current)
            current = []
        elif len(current) >= size:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return [
        {
            "schema_version": "icle-analysis-bundle/v0.1",
            "session_id": bundle.get("session_id"),
            "events": chunk,
            "noise_dropped": 0,
            "boundaries": [],
        }
        for chunk in chunks
    ]


def _merge_partials(partials: list[dict], session_id: str) -> dict:
    """Map→Merge:拼接各 chunk 的 facts/tasks 等;candidate_id 重编号 c1..cn。"""
    merged: dict = {
        "schema_version": ANALYSIS_SCHEMA,
        "session_id": session_id,
        "status": "ok",
        "facts": [],
        "inferences": [],
        "proposals": [],
        "confidence": {"boundary": 0.0, "intent": 0.0, "outcome": 0.0, "task_profile": 0.0},
        "tasks": [],
    }
    for partial in partials:
        if partial.get("status") != "ok":
            continue  # 某 chunk 拒绝分析 → 跳过该块
        for section in ("facts", "inferences", "proposals"):
            merged[section].extend(partial.get(section) or [])
        for task in partial.get("tasks") or []:
            merged["tasks"].append(task)
    # 重编号 + 置信度取各 chunk 平均值(简化;边界置信度合并留给 Verify pass)
    for i, task in enumerate(merged["tasks"], 1):
        task["candidate_id"] = f"c{i}"
    if merged["tasks"]:
        keys = ("boundary", "intent", "outcome", "task_profile")
        for key in keys:
            values = [
                (task.get("confidence") or {}).get(key) or 0.0
                for task in merged["tasks"]
            ]
            if values:
                merged["confidence"][key] = round(sum(values) / len(values), 2)
    return merged


def analyze_session(
    provider: IntelligenceProvider,
    events: list[dict[str, Any]],
    *,
    language: str = "en",
) -> dict[str, Any]:
    """Skill-driven session analysis -> SessionAnalysisProposal(只输出 Proposal)。

    事件为 capture-store 标准 SessionEvent。返回前经 scripts/validate_analysis.py
    确定性校验;校验失败(LLM 输出非法)抛 IntelligenceError,绝不静默入库。
    """
    from .skills import build_mode_prompt, load_skill, run_script

    skill = load_skill("task-intelligence")
    bundle = run_script(skill, "compact_session", events)

    if len(bundle["events"]) <= ANALYZE_CHUNK:
        proposal = _analyze_chunk(provider, skill, bundle, language)
        prompt = build_mode_prompt(
            skill, "analyze-session",
            payload={"bundle": bundle},
            refs=["session-analysis", "schemas"],
            extra_instruction=_language_instruction(language),
        )
    else:
        # 长会话:Map → Merge → Verify(建议 §十)
        partials = [
            _analyze_chunk(provider, skill, chunk, language)
            for chunk in _chunk_bundle(bundle)
        ]
        proposal = _merge_partials(partials, bundle.get("session_id"))
        prompt = f"map-merge over {len(partials)} chunks of session {bundle.get('session_id')}"

    # 确定性校验(scripts/validate_analysis.py):evidence 回指/枚举/conf/白名单
    try:
        run_script(skill, "validate_analysis", proposal, bundle)
    except Exception as exc:
        raise IntelligenceError(f"session analysis validation failed: {exc}") from exc

    proposal["provenance"] = {
        "skill": {"name": "icle-task-intelligence", "version": SKILL_VERSION,
                  "sha256": _sha(skill["text"])},
        "provider": provider.name,
        "model": provider.model,
        "revision": provider.revision,
        "prompt_sha256": _sha(prompt),
        "input_received": True,
        "created_at": _now(),
        "language": language,
    }
    return proposal


# ---------------------------------------------------------------- 8. plan-task / replan-task (skill v0.2/v0.3)
#
# 由 icle-task-intelligence skill 的 plan-task / replan-task mode 驱动:
#   plan-task: Task Ledger → 拆不拆判断 → 1-3 个 PlanCandidate
#              (只给 required_capabilities,绝不指定 agent;不计算成本)
#   replan-task: 保留原 ledger/plan → 失败分类 → 保留有效步骤 → 局部重规划
# 输出经 scripts/validate_plan.py 确定性校验(白名单/DAG/无 agent 字段)。

PLAN_SCHEMA = "icle-task-plan-proposal/v0.1"
REPLAN_SCHEMA = "icle-replan-proposal/v0.1"


def plan_task(
    provider: IntelligenceProvider,
    *,
    title: str,
    description: str,
    difficulty: str = "D3",
    risk: str = "R1",
    context_requirement: str = "MEDIUM",
    decomposition: str = "recommended",
    project_path: str = "",
    language: str = "en",
) -> dict[str, Any]:
    """Skill plan-task → PlanProposal(1-3 PlanCandidate,只输出 Proposal)。

    替代旧的散落 PLANNER_PROMPT 路径:规划前先构建 Task Ledger,再决定拆不拆,
    输出候选由 ICLE 的 CostEngine/RecommendationEngine 排序。不落库,由 API 层
    决定保存哪个候选。
    """
    from .skills import build_mode_prompt, load_skill, run_script

    skill = load_skill("task-intelligence")
    payload = {
        "task": {"title": title, "description": description},
        "profile": {"difficulty": difficulty, "risk": risk,
                    "context_requirement": context_requirement,
                    "decomposition": decomposition},
        "project_path": project_path,
    }
    prompt = build_mode_prompt(
        skill, "plan-task",
        payload=payload,
        refs=["planning", "schemas"],
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="task_planner_skill")
    parsed["schema_version"] = PLAN_SCHEMA
    if not isinstance(parsed.get("candidates"), list) or not parsed["candidates"]:
        raise IntelligenceError("task_planner_skill: no candidates proposed")
    # 确定性校验(白名单/DAG/无 agent 字段)
    try:
        run_script(skill, "validate_plan", parsed)
    except Exception as exc:
        raise IntelligenceError(f"task plan validation failed: {exc}") from exc
    parsed["provenance"] = {
        "skill": {"name": "icle-task-intelligence", "version": SKILL_VERSION,
                  "sha256": _sha(skill["text"])},
        "provider": provider.name, "model": provider.model, "revision": provider.revision,
        "prompt_sha256": _sha(prompt), "input_received": True,
        "created_at": _now(), "language": language,
    }
    return parsed


def replan_task(
    provider: IntelligenceProvider,
    *,
    original_plan: dict,
    failure: str,
    progress: str = "",
    language: str = "en",
) -> dict[str, Any]:
    """Skill replan-task → ReplanProposal(保留有效步骤,局部重规划)。"""
    from .skills import build_mode_prompt, load_skill, run_script

    skill = load_skill("task-intelligence")
    payload = {
        "original_plan": {"strategy": original_plan.get("strategy"),
                          "steps": original_plan.get("steps")},
        "failure": failure,
        "progress": progress,
    }
    prompt = build_mode_prompt(
        skill, "replan-task",
        payload=payload,
        refs=["planning", "schemas"],
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="task_replanner")
    parsed["schema_version"] = REPLAN_SCHEMA
    original_step_ids = {
        str(step.get("step_id", f"S{i}")) for i, step in enumerate(original_plan.get("steps") or [], 1)
    }
    try:
        run_script(skill, "validate_plan", parsed, list(original_step_ids), fn="validate_replan_proposal")
    except Exception as exc:
        raise IntelligenceError(f"task replan validation failed: {exc}") from exc
    parsed["provenance"] = {
        "skill": {"name": "icle-task-intelligence", "version": SKILL_VERSION,
                  "sha256": _sha(skill["text"])},
        "provider": provider.name, "model": provider.model, "revision": provider.revision,
        "prompt_sha256": _sha(prompt), "input_received": True,
        "created_at": _now(), "language": language,
    }
    return parsed


# ---------------------------------------------------------------- 9. split-task (skill v0.5)
#
# LLM 拆分子任务(评审 §20):skill 只出 TaskSplitProposal,人工最终确认;
# 落库与手动拆分走同一个 POST /tasks/{id}/children API。

SPLIT_SCHEMA = "icle-task-split-proposal/v0.2"
SPLIT_MAX_CHILDREN = 8
SPLIT_CANDIDATES = 2


def _validate_children(children: object, *, component: str) -> None:
    """公共校验:children 必须为 list,1..8 个,每个 title/reason 非空。"""
    if not isinstance(children, list):
        raise IntelligenceError(f"{component}: children must be a list")
    if len(children) > SPLIT_MAX_CHILDREN:
        raise IntelligenceError(f"{component}: too many children ({len(children)} > {SPLIT_MAX_CHILDREN})")
    for index, child in enumerate(children, 1):
        if not isinstance(child, dict) or not (child.get("title") or "").strip():
            raise IntelligenceError(f"{component}: children[{index}].title required")
        if not isinstance(child.get("reason"), str) or not child["reason"].strip():
            raise IntelligenceError(f"{component}: children[{index}].reason required")
        if not isinstance(child.get("description"), str):
            child["description"] = ""


def propose_split_task(
    provider: IntelligenceProvider,
    *,
    task: dict[str, Any],
    language: str = "en",
) -> dict[str, Any]:
    """Skill split-task stage 1:LLM 自己规划两套最合理的拆分雏形(不同维度)。

    LLM 决定拆法(模块/阶段/组件/依赖流里选两套最适合的),UI 不再预设 mode;
    用户看到两套雏形后选一套,再由 refine_split_task 精细化。
    不值得拆 → needs_split=false + 理由(拒绝而非硬拆)。
    """
    from .skills import build_mode_prompt, load_skill

    skill = load_skill("task-intelligence")
    payload = {
        "task": {"title": task.get("title"), "description": task.get("description")},
        "profile": (task.get("profile") or {}),
    }
    prompt = build_mode_prompt(
        skill, "split-task",
        payload=payload,
        refs=["planning", "schemas"],
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="task_splitter")
    parsed["schema_version"] = SPLIT_SCHEMA
    parsed.setdefault("parent_task_id", task.get("task_id"))
    candidates = parsed.get("candidates")
    parsed.setdefault("needs_split", bool(candidates))
    if parsed.get("needs_split") is False:
        # 拒绝态:允许 candidates 为空,但必须有理由
        parsed["candidates"] = []
        if not isinstance(parsed.get("reason"), str) or not parsed["reason"]:
            raise IntelligenceError("task_splitter: needs_split=false requires a reason")
        parsed["provenance"] = _split_provenance(provider, skill, prompt, language)
        return parsed
    if not isinstance(candidates, list):
        raise IntelligenceError("task_splitter: candidates must be a list")
    if len(candidates) != SPLIT_CANDIDATES:
        raise IntelligenceError(
            f"task_splitter: expected exactly {SPLIT_CANDIDATES} candidates, got {len(candidates)}"
        )
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            raise IntelligenceError(f"task_splitter: candidates[{index}] must be an object")
        if not (candidate.get("approach") or "").strip():
            raise IntelligenceError(f"task_splitter: candidates[{index}].approach required")
        candidate.setdefault("candidate_id", f"c{index}")
        _validate_children(candidate.get("children"), component=f"task_splitter:candidates[{index}]")
    parsed["provenance"] = _split_provenance(provider, skill, prompt, language)
    return parsed


def refine_split_task(
    provider: IntelligenceProvider,
    *,
    task: dict[str, Any],
    candidate: dict[str, Any],
    language: str = "en",
) -> dict[str, Any]:
    """Skill split-task stage 2:精细化用户选中的一套雏形。

    雏形来自 propose_split_task 的 candidates(前端回传,Proposal 不落库);
    输出单套完整 children(同 schema v0.2),仍由用户确认后落库。
    """
    from .skills import build_mode_prompt, load_skill

    if not isinstance(candidate, dict) or not (candidate.get("approach") or "").strip():
        raise IntelligenceError("task_splitter: refine requires a candidate with approach")
    skill = load_skill("task-intelligence")
    payload = {
        "task": {"title": task.get("title"), "description": task.get("description")},
        "profile": (task.get("profile") or {}),
        "selected_candidate": candidate,
    }
    prompt = build_mode_prompt(
        skill, "split-task",
        payload=payload,
        refs=["planning", "schemas"],
        extra_instruction=(
            f"{_language_instruction(language)}\n"
            "Stage 2 (refine): elaborate the selected candidate into concrete "
            "children. Keep its approach and rationale."
        ),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="task_splitter")
    parsed["schema_version"] = SPLIT_SCHEMA
    parsed.setdefault("parent_task_id", task.get("task_id"))
    parsed["candidate_id"] = candidate.get("candidate_id")
    parsed["approach"] = candidate.get("approach")
    parsed["rationale"] = candidate.get("rationale")
    # 容错:LLM 偶发仍按 stage-1 输出 candidates(而非单套 children)→ 取第一套
    children = parsed.get("children")
    if children is None and isinstance(parsed.get("candidates"), list) and parsed["candidates"]:
        children = parsed["candidates"][0].get("children")
    if not isinstance(children, list):
        raise IntelligenceError(f"task_splitter:refine: children must be a list (reply head: {reply[:160]!r})")
    parsed["children"] = children
    _validate_children(children, component="task_splitter:refine")
    parsed["provenance"] = _split_provenance(provider, skill, prompt, language)
    return parsed


def _split_provenance(provider: IntelligenceProvider, skill: dict, prompt: str, language: str) -> dict[str, Any]:
    return {
        "skill": {"name": "icle-task-intelligence", "version": SKILL_VERSION,
                  "sha256": _sha(skill["text"])},
        "provider": provider.name, "model": provider.model, "revision": provider.revision,
        "prompt_sha256": _sha(prompt), "input_received": True,
        "created_at": _now(), "language": language,
    }


# ---------------------------------------------------------------- 10. template-customize (skill v0.6)
#
# 「固定模板保证规划结构稳定,LLM 只做语义定制」:用户选定模板(来自
# templates.py)后,LLM 把每个步骤具体化为当前任务的语义,不增删骨架。
# 输出 TemplatePlanProposal(不落库),由用户确认后走 /children。

TPL_SCHEMA = "icle-template-plan/v0.1"


def plan_from_template(
    provider: IntelligenceProvider,
    *,
    task: dict[str, Any],
    template: dict[str, Any],
    language: str = "en",
) -> dict[str, Any]:
    """按固定模板骨架做语义定制 → TemplatePlanProposal(不落库)。

    template 来自 templates.py(前端回传或后端按 id 取),children keys 是
    稳定骨架:LLM 只能改 title/description,不能增删 key、不能改 optional。
    """
    from .skills import build_mode_prompt, load_skill

    children = template.get("children") or []
    if not children:
        raise IntelligenceError("template_customizer: template has no children")
    skill = load_skill("task-intelligence")
    payload = {
        "task": {"title": task.get("title"), "description": task.get("description")},
        "profile": (task.get("profile") or {}),
        "template": {
            "template_id": template.get("template_id"),
            "name": template.get("name"),
            "children": [
                {"key": c.get("key"), "title": c.get("title"),
                 "description": c.get("description"), "optional": c.get("optional")}
                for c in children
            ],
        },
    }
    prompt = build_mode_prompt(
        skill, "template-customize",
        payload=payload,
        refs=["schemas"],
        extra_instruction=_language_instruction(language),
    )
    reply = provider.complete(prompt)
    parsed = _parse_json_object(reply, component="template_customizer")
    parsed["schema_version"] = TPL_SCHEMA
    parsed["template_id"] = template.get("template_id")
    if parsed.get("fits_template") is False:
        parsed["children"] = []
        if not isinstance(parsed.get("reason"), str) or not parsed["reason"]:
            raise IntelligenceError("template_customizer: fits_template=false requires a reason")
        parsed["provenance"] = _tpl_provenance(provider, skill, prompt, language)
        return parsed
    # 骨架校验:keys 必须与模板完全一致(不多不少不重排)
    result = parsed.get("children")
    if not isinstance(result, list):
        raise IntelligenceError("template_customizer: children must be a list")
    expected = [c["key"] for c in children]
    got = [c.get("key") for c in result] if all(isinstance(c, dict) for c in result) else None
    if got != expected:
        raise IntelligenceError(
            f"template_customizer: keys must be exactly {expected}, got {got} "
            f"(reply head: {reply[:160]!r})"
        )
    for child, original in zip(result, children):
        if not isinstance(child, dict) or not (child.get("title") or "").strip():
            raise IntelligenceError(f"template_customizer: {original['key']}.title required")
        if not isinstance(child.get("description"), str):
            child["description"] = ""
        # optional 由模板决定,LLM 输出被强制覆盖
        child["optional"] = original.get("optional")
    parsed["provenance"] = _tpl_provenance(provider, skill, prompt, language)
    return parsed


def _tpl_provenance(provider: IntelligenceProvider, skill: dict, prompt: str, language: str) -> dict[str, Any]:
    return {
        "skill": {"name": "icle-task-intelligence", "version": SKILL_VERSION,
                  "sha256": _sha(skill["text"])},
        "provider": provider.name, "model": provider.model, "revision": provider.revision,
        "prompt_sha256": _sha(prompt), "input_received": True,
        "created_at": _now(), "language": language,
    }


# ---------------------------------------------------------------- 11. summarize-reports (skill v0.2)
#
# 计数与标志由 merge_reports 决定。skill 只许改 summary / blocked_reason。

def summarize_task_reports(
    provider: IntelligenceProvider,
    *,
    entries: list[dict[str, Any]],
    merged: dict[str, Any],
    language: str = "en",
) -> dict[str, Any]:
    """Skill summarize-reports: narrative only; counts stay deterministic."""
    from .report import normalize_report, report_metrics

    payload = [
        {"task_id": item.get("task_id"), "agent": item.get("agent"), "report": item.get("report")}
        for item in entries
        if item.get("report_status") == "observed"
    ]
    skill, prompt, narrative = _invoke_skill(
        provider,
        "summarize-reports",
        payload={"merged": merged, "entries": payload},
        refs=["reports", "schemas"],
        language=language,
        component="report_summary",
        script="validate_report_summary",
    )
    summary = normalize_report({**merged, **narrative})
    for key, value in merged.items():
        if key not in ("summary", "blocked_reason"):
            summary[key] = value
    if not report_metrics(summary):
        raise IntelligenceError("report_summary: consolidated report yields no metric")
    summary["provenance"] = _skill_provenance(provider, skill, prompt, language)
    return summary
