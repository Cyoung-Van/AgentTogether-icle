"""Task split templates (fixed skeleton library).

User-sourced spec (2026-08): replace the coarse software/migration templates
with ~10-12 high-frequency work templates, each carrying metadata:

    template_id / name / version
    applicable_task_types  -> matches profile.primary_type (TASK_TYPES in task.py)
    recommended_difficulty -> D1..D5 (DIFFICULTY_LEVELS)
    children: [{key, title, description, optional}]

Design intent (user): the template is the STABLE skeleton — the LLM planning
skill customizes the steps semantically for a concrete task, it never invents
the structure (fixed standard + LLM proposal). `key` is the stable identity
(the LLM must not add/remove keys), `optional` marks steps the user may uncheck
in the confirmation UI (e.g. Documentation) before creating subtasks.

Sources referenced: Spec Kit (feature), bugfix checklists (Cypress et al.),
Aviator runbooks (refactor/migration), mgreiler code-review checklist,
Temurin release checklist, Counteractive incident response, The Turing Way
(reproducible research), production-ready checklists.

This module is the single source of truth — the WebUI loads templates from
GET /api/templates instead of hard-coding them.
"""

from __future__ import annotations

from typing import Any

SCHEMA = "icle-task-template/v0.1"
MAX_TEMPLATE_CHILDREN = 10

# Valid enum values reused from task.py (kept local to avoid circular imports;
# task.py must stay compatible — do not rename these).
_PRIMARY_TYPES = (
    "CODING", "RESEARCH", "ANALYSIS", "WRITING", "PLANNING", "DATA",
    "SYSTEM_OPERATION", "MULTIMODAL", "OTHER",
)
_DIFFICULTIES = ("D1", "D2", "D3", "D4", "D5")

BLANK_TEMPLATE_ID = "blank"

TASK_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "feature",
        "name": "Feature Development",
        "version": 1,
        "applicable_task_types": ["CODING"],
        "recommended_difficulty": ["D2", "D3", "D4"],
        "children": [
            {"key": "requirements", "title": "Requirements",
             "description": "Clarify requirements, boundaries and acceptance criteria",
             "optional": False},
            {"key": "analysis", "title": "Existing System Analysis",
             "description": "Read existing implementation, interfaces and dependencies",
             "optional": False},
            {"key": "design", "title": "Design",
             "description": "Design data structures / API / interactions; note compatibility and risks",
             "optional": False},
            {"key": "implement", "title": "Implementation",
             "description": "Implement the core feature",
             "optional": False},
            {"key": "test", "title": "Testing",
             "description": "Unit / integration / real-scenario verification",
             "optional": False},
            {"key": "docs", "title": "Documentation & Acceptance",
             "description": "Update documentation and complete acceptance",
             "optional": True},
        ],
    },
    {
        "template_id": "bugfix",
        "name": "Bug Fix",
        "version": 1,
        "applicable_task_types": ["CODING"],
        "recommended_difficulty": ["D2", "D3", "D4"],
        "children": [
            {"key": "reproduce", "title": "Reproduce",
             "description": "Stably reproduce the issue and save the failing case",
             "optional": False},
            {"key": "root_cause", "title": "Root Cause Analysis",
             "description": "Locate the true root cause; rule out surface symptoms",
             "optional": False},
            {"key": "fix_design", "title": "Fix Design",
             "description": "Choose the minimal-risk fix",
             "optional": True},
            {"key": "implement", "title": "Implementation",
             "description": "Apply the fix",
             "optional": False},
            {"key": "regression", "title": "Regression Test",
             "description": "Add a test for the bug and run the existing suite",
             "optional": False},
            {"key": "boundary", "title": "Boundary Verification",
             "description": "Check adjacent scenarios and side effects",
             "optional": True},
        ],
    },
    {
        "template_id": "refactor",
        "name": "Refactoring",
        "version": 1,
        "applicable_task_types": ["CODING"],
        "recommended_difficulty": ["D2", "D3", "D4"],
        "children": [
            {"key": "baseline", "title": "Establish Baseline",
             "description": "All tests pass; record behavior/performance baseline",
             "optional": False},
            {"key": "analyze", "title": "Analyze",
             "description": "Identify smells, coupling and duplication",
             "optional": False},
            {"key": "design", "title": "Design Target",
             "description": "Define the target structure; state which behaviors must NOT change",
             "optional": True},
            {"key": "incremental", "title": "Incremental Refactor",
             "description": "Small-step changes",
             "optional": False},
            {"key": "verify", "title": "Continuous Verification",
             "description": "Run tests after every stage",
             "optional": False},
            {"key": "final", "title": "Final Validation",
             "description": "Behavior unchanged, no performance regression, update docs",
             "optional": False},
        ],
    },
    {
        "template_id": "migration",
        "name": "Migration",
        "version": 1,
        "applicable_task_types": ["CODING", "SYSTEM_OPERATION"],
        "recommended_difficulty": ["D3", "D4", "D5"],
        "children": [
            {"key": "inventory", "title": "Inventory",
             "description": "Survey the current state of what will be migrated",
             "optional": False},
            {"key": "dependency", "title": "Dependency Analysis",
             "description": "Map dependants and coupling",
             "optional": False},
            {"key": "design", "title": "Migration Design",
             "description": "Design the migration path and cutover strategy",
             "optional": True},
            {"key": "dry_run", "title": "Dry Run",
             "description": "Execute the migration on a safe copy",
             "optional": False},
            {"key": "migrate", "title": "Migrate",
             "description": "Run the real migration",
             "optional": False},
            {"key": "verify", "title": "Verify",
             "description": "Validate migrated state correctness",
             "optional": False},
            {"key": "rollback", "title": "Rollback Preparation",
             "description": "Document and verify the rollback path",
             "optional": True},
        ],
    },
    {
        "template_id": "review",
        "name": "Code Review",
        "version": 1,
        "applicable_task_types": ["CODING", "WRITING"],
        "recommended_difficulty": ["D2", "D3"],
        "children": [
            {"key": "intent", "title": "Understand Intent",
             "description": "Read the task / PR / design goals",
             "optional": False},
            {"key": "correctness", "title": "Correctness Review",
             "description": "Does it implement the requirements; any obvious bugs",
             "optional": False},
            {"key": "architecture", "title": "Architecture & Maintainability",
             "description": "Boundaries, coupling, maintainability",
             "optional": True},
            {"key": "tests", "title": "Test Review",
             "description": "Do tests cover the key cases",
             "optional": True},
            {"key": "risk", "title": "Risk Review",
             "description": "Security, performance, compatibility",
             "optional": True},
            {"key": "report", "title": "Review Report",
             "description": "Classify findings: blocker / major / minor / suggestion",
             "optional": False},
        ],
    },
    {
        "template_id": "release",
        "name": "Release",
        "version": 1,
        "applicable_task_types": ["CODING", "SYSTEM_OPERATION"],
        "recommended_difficulty": ["D3", "D4"],
        "children": [
            {"key": "scope", "title": "Release Scope",
             "description": "Confirm version, feature scope and breaking changes",
             "optional": False},
            {"key": "validation", "title": "Pre-release Validation",
             "description": "CI, tests, security and dependency checks",
             "optional": False},
            {"key": "prepare", "title": "Prepare Release",
             "description": "Version bump, changelog, docs, artifacts",
             "optional": False},
            {"key": "deploy", "title": "Deploy / Publish",
             "description": "Staging then production",
             "optional": False},
            {"key": "smoke", "title": "Smoke Test",
             "description": "Verify core paths after deploy",
             "optional": False},
            {"key": "monitor", "title": "Rollback / Monitor",
             "description": "Watch metrics; confirm rollback readiness",
             "optional": True},
            {"key": "closeout", "title": "Closeout",
             "description": "Release notes and retrospective",
             "optional": True},
        ],
    },
    {
        "template_id": "incident",
        "name": "Incident Response",
        "version": 1,
        "applicable_task_types": ["SYSTEM_OPERATION", "CODING"],
        "recommended_difficulty": ["D3", "D4", "D5"],
        "children": [
            {"key": "triage", "title": "Triage",
             "description": "Confirm the symptom, assess impact scope and severity",
             "optional": False},
            {"key": "containment", "title": "Containment",
             "description": "Stop the impact from spreading",
             "optional": False},
            {"key": "investigation", "title": "Investigation",
             "description": "Collect logs and locate the fault source",
             "optional": False},
            {"key": "mitigation", "title": "Mitigation / Fix",
             "description": "Apply a temporary mitigation or fix",
             "optional": False},
            {"key": "recovery", "title": "Recovery Verification",
             "description": "Service recovered; key metrics normal",
             "optional": False},
            {"key": "root_cause", "title": "Root Cause",
             "description": "Analyze the root cause after recovery",
             "optional": True},
            {"key": "followup", "title": "Follow-up",
             "description": "Postmortem and prevention tasks",
             "optional": True},
        ],
    },
    {
        "template_id": "research",
        "name": "Research",
        "version": 1,
        "applicable_task_types": ["RESEARCH"],
        "recommended_difficulty": ["D2", "D3", "D4"],
        "children": [
            {"key": "question", "title": "Define Question",
             "description": "Precisely define the research question",
             "optional": False},
            {"key": "search", "title": "Search",
             "description": "Gather relevant sources",
             "optional": False},
            {"key": "screening", "title": "Source Screening",
             "description": "Filter authoritative and relevant sources",
             "optional": False},
            {"key": "extraction", "title": "Evidence Extraction",
             "description": "Extract key facts with references",
             "optional": False},
            {"key": "cross_validation", "title": "Cross Validation",
             "description": "Verify across sources; identify conflicts",
             "optional": False},
            {"key": "synthesis", "title": "Synthesis",
             "description": "Synthesize the findings",
             "optional": True},
            {"key": "conclusion", "title": "Conclusion",
             "description": "Answer the original question",
             "optional": False},
            {"key": "sources", "title": "Sources",
             "description": "Organize citations and limitations",
             "optional": True},
        ],
    },
    {
        "template_id": "data_analysis",
        "name": "Data Analysis",
        "version": 1,
        "applicable_task_types": ["DATA", "ANALYSIS"],
        "recommended_difficulty": ["D2", "D3", "D4"],
        "children": [
            {"key": "question", "title": "Define Question",
             "description": "State the analytical question precisely",
             "optional": False},
            {"key": "acquire", "title": "Inspect / Acquire Data",
             "description": "Locate, access and inspect the data",
             "optional": False},
            {"key": "clean", "title": "Clean & Validate Data",
             "description": "Clean, validate and document transformations",
             "optional": False},
            {"key": "eda", "title": "Exploratory Analysis",
             "description": "Explore distributions, gaps and anomalies",
             "optional": False},
            {"key": "model", "title": "Analysis / Modeling",
             "description": "Run the core analysis or model",
             "optional": False},
            {"key": "validate", "title": "Validate Results",
             "description": "Sanity-check and validate the results",
             "optional": False},
            {"key": "visualize", "title": "Visualization",
             "description": "Produce charts and tables",
             "optional": True},
            {"key": "report", "title": "Interpretation & Report",
             "description": "Write conclusions and limitations",
             "optional": True},
        ],
    },
    {
        "template_id": "security_audit",
        "name": "Security Audit",
        "version": 1,
        "applicable_task_types": ["CODING", "SYSTEM_OPERATION"],
        "recommended_difficulty": ["D3", "D4", "D5"],
        "children": [
            {"key": "scope", "title": "Define Scope",
             "description": "Scope and threat surface (entry points, trust boundaries, credentials)",
             "optional": False},
            {"key": "config", "title": "Configuration Review",
             "description": "Review deployment and runtime configuration",
             "optional": False},
            {"key": "code", "title": "Code Review",
             "description": "Review application code for vulnerabilities",
             "optional": False},
            {"key": "deps", "title": "Dependency Review",
             "description": "Check dependencies for known vulnerabilities",
             "optional": False},
            {"key": "verify", "title": "Verify Findings",
             "description": "Confirm suspected issues (finding ≠ confirmed vulnerability)",
             "optional": False},
            {"key": "classify", "title": "Risk Classification",
             "description": "Rate severity and risk for each finding",
             "optional": False},
            {"key": "remediation", "title": "Remediation Plan",
             "description": "Recommend fixes with priorities",
             "optional": True},
        ],
    },
    {
        "template_id": "documentation",
        "name": "Documentation",
        "version": 1,
        "applicable_task_types": ["WRITING", "CODING"],
        "recommended_difficulty": ["D1", "D2", "D3"],
        "children": [
            {"key": "audience", "title": "Define Audience & Goal",
             "description": "Define target audience and doc goal",
             "optional": False},
            {"key": "audit", "title": "Audit Existing Docs",
             "description": "Review current documentation coverage",
             "optional": True},
            {"key": "structure", "title": "Design Structure",
             "description": "Plan information architecture",
             "optional": False},
            {"key": "write", "title": "Write / Update Content",
             "description": "Write or update the content",
             "optional": False},
            {"key": "examples", "title": "Examples & Usage",
             "description": "Add examples and usage sections",
             "optional": True},
            {"key": "accuracy", "title": "Accuracy Check",
             "description": "Verify technical accuracy",
             "optional": False},
            {"key": "publish", "title": "Link / Build Validation & Publish",
             "description": "Validate links/build and publish",
             "optional": True},
        ],
    },
    {
        "template_id": "production_readiness",
        "name": "Production Readiness",
        "version": 1,
        "applicable_task_types": ["CODING", "SYSTEM_OPERATION"],
        "recommended_difficulty": ["D4", "D5"],
        "children": [
            {"key": "functional", "title": "Functional Validation",
             "description": "Core functionality works end-to-end",
             "optional": False},
            {"key": "testing", "title": "Testing",
             "description": "Unit, integration and edge-case coverage",
             "optional": False},
            {"key": "security", "title": "Security",
             "description": "Security review and hardening",
             "optional": False},
            {"key": "performance", "title": "Performance",
             "description": "Latency / throughput / resource checks",
             "optional": False},
            {"key": "observability", "title": "Observability",
             "description": "Logs, metrics, tracing and alerts",
             "optional": False},
            {"key": "data", "title": "Data / Backup",
             "description": "Data integrity and backup/restore",
             "optional": True},
            {"key": "deploy", "title": "Deployment",
             "description": "Repeatable deployment path",
             "optional": False},
            {"key": "rollback", "title": "Rollback",
             "description": "Proven rollback path",
             "optional": False},
            {"key": "ops_docs", "title": "Operational Docs",
             "description": "Runbooks and operational documentation",
             "optional": True},
            {"key": "gono", "title": "Go / No-Go",
             "description": "Final decision and sign-off",
             "optional": False},
        ],
    },
]


def _validate_template(template: dict[str, Any]) -> None:
    """Structural validation for one template (used by tests and load)."""
    template_id = template.get("template_id")
    if not isinstance(template_id, str) or not template_id:
        raise ValueError("template_id required")
    if not isinstance(template.get("name"), str) or not template["name"]:
        raise ValueError(f"{template_id}: name required")
    if not isinstance(template.get("version"), int) or template["version"] < 1:
        raise ValueError(f"{template_id}: version must be an int >= 1")
    task_types = template.get("applicable_task_types")
    if not isinstance(task_types, list) or not task_types:
        raise ValueError(f"{template_id}: applicable_task_types required")
    for t in task_types:
        if t not in _PRIMARY_TYPES:
            raise ValueError(f"{template_id}: unknown primary_type {t!r}")
    difficulties = template.get("recommended_difficulty")
    if not isinstance(difficulties, list) or not difficulties:
        raise ValueError(f"{template_id}: recommended_difficulty required")
    for d in difficulties:
        if d not in _DIFFICULTIES:
            raise ValueError(f"{template_id}: unknown difficulty {d!r}")
    children = template.get("children")
    if not isinstance(children, list) or not 1 <= len(children) <= MAX_TEMPLATE_CHILDREN:
        raise ValueError(f"{template_id}: children must be 1..{MAX_TEMPLATE_CHILDREN}")
    seen_keys: set[str] = set()
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            raise ValueError(f"{template_id}: children[{index}] must be an object")
        key = child.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{template_id}: children[{index}].key required")
        if key in seen_keys:
            raise ValueError(f"{template_id}: duplicate child key {key!r}")
        seen_keys.add(key)
        if not isinstance(child.get("title"), str) or not child["title"]:
            raise ValueError(f"{template_id}: children[{index}].title required")
        if not isinstance(child.get("description"), str):
            raise ValueError(f"{template_id}: children[{index}].description must be a string")
        if not isinstance(child.get("optional"), bool):
            raise ValueError(f"{template_id}: children[{index}].optional must be a bool")


def validate_all_templates() -> list[str]:
    """Validate every built-in template; returns [template_id, ...] that passed."""
    seen_ids: set[str] = set()
    for template in TASK_TEMPLATES:
        _validate_template(template)
        if template["template_id"] in seen_ids:
            raise ValueError(f"duplicate template_id {template['template_id']!r}")
        seen_ids.add(template["template_id"])
    return list(seen_ids)


def get_template(template_id: str) -> dict[str, Any] | None:
    for template in TASK_TEMPLATES:
        if template["template_id"] == template_id:
            return template
    return None


def list_templates(*, primary_type: str | None = None) -> list[dict[str, Any]]:
    """All templates, optionally filtered by profile.primary_type.

    A blank (manual) pseudo-template is always appended so the UI can offer it.
    """
    templates = list(TASK_TEMPLATES)
    if primary_type:
        templates = [t for t in templates if primary_type in t["applicable_task_types"]]
    templates = templates + [{
        "template_id": BLANK_TEMPLATE_ID,
        "name": "Blank",
        "version": 1,
        "applicable_task_types": list(_PRIMARY_TYPES),
        "recommended_difficulty": list(_DIFFICULTIES),
        "children": [],
    }]
    return templates


# ---------------------------------------------------------------- recommendation (v1, rule-based)
#
# Task Analyzer → recommend the best-fit template with confidence scores
# (deterministic; no LLM). Signals: profile primary_type/subtype/difficulty/risk
# + keyword hits on title/description. Output like:
#     bugfix 0.84 / refactor 0.11 / general 0.05
# The LLM only customizes the chosen template afterwards (template-customize).

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "feature": ("feature", "新增", "开发", "新功能", "实现"),
    "bugfix": ("bug", "fix", "修复", "崩溃", "错误", "报错", "defect", "broken", "fails", "异常"),
    "refactor": ("refactor", "重构", "清理", "dedupe", "smell", "优化结构"),
    "migration": ("migrat", "迁移", "升级", "migrate", "port", "导入"),
    "review": ("review", "评审", "code review", "审查"),
    "release": ("release", "发布", "部署", "版本", "deploy", "ship", "上线"),
    "incident": ("incident", "故障", "宕机", "outage", "告警", "服务不可用", "pager"),
    "research": ("research", "调研", "研究", "literature", "资料", "调查", "对比"),
    "data_analysis": ("analysis", "分析", "数据", "eda", "dashboard", "dataset", "统计"),
    "security_audit": ("security", "安全", "audit", "漏洞", "vulnerability", "渗透"),
    "documentation": ("doc", "文档", "说明", "readme", "手册", "guide"),
    "production_readiness": ("production", "上线前", "readiness", "生产", "go-live", "就绪"),
}


def recommend_template(task: dict[str, Any]) -> dict[str, Any]:
    """Deterministic template recommendation with confidence in [0, 1].

    Confidence = 0.55 * keyword hit ratio + 0.35 * profile fit + 0.10 * base.
    Returns sorted [{template_id, name, confidence}], always including a
    general (blank) fallback so the user can always override.
    """
    title = str(task.get("title") or "")
    description = str(task.get("description") or "")
    text = f"{title} {description}".lower()
    profile = task.get("profile") or {}
    primary = str(profile.get("primary_type") or "OTHER")
    subtype = str(profile.get("subtype") or "")
    difficulty = str(profile.get("difficulty") or "")
    risk = str(profile.get("risk") or "")

    scores: list[dict[str, Any]] = []
    for template in TASK_TEMPLATES:
        keywords = _KEYWORDS.get(template["template_id"], ())
        hits = sum(1 for kw in keywords if kw in text)
        keyword_score = min(hits / 3.0, 1.0) if keywords else 0.0

        profile_fit = 0.0
        if primary in template["applicable_task_types"]:
            profile_fit += 0.4
        if subtype and subtype in _template_subtype_hints(template["template_id"]):
            profile_fit += 0.3
        if difficulty in template["recommended_difficulty"]:
            profile_fit += 0.3
        elif difficulty:
            profile_fit += 0.1

        confidence = round(min(0.55 * keyword_score + 0.35 * profile_fit + 0.10, 0.97), 2)
        scores.append({"template_id": template["template_id"],
                       "name": template["name"], "confidence": confidence})

    scores.sort(key=lambda s: s["confidence"], reverse=True)
    general = max(confidence for s in scores)
    scores.append({"template_id": BLANK_TEMPLATE_ID, "name": "Blank",
                   "confidence": round(max(0.03, 1.0 - general), 2)})
    return {"recommendations": scores, "task_id": task.get("task_id")}


def _template_subtype_hints(template_id: str) -> tuple[str, ...]:
    return {
        "feature": ("implementation",),
        "bugfix": ("debugging",),
        "refactor": ("refactor",),
        "review": ("review",),
        "release": ("architecture",),
        "research": ("search", "literature", "comparison"),
        "security_audit": ("review",),
    }.get(template_id, ())
