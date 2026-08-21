"""Skills: 小众 agent 会话捕获的 LLM 兜底机制(附加功能)。

内置 adapter(capture.py)覆盖主流 agent;没有内置 adapter 的小众 agent
(gemini/aider/qwen 及未来新增),接入 LLM 后可按 `skills/capture/<agent>.md`
的说明,由 LLM 现场解析原始会话并标准化入库。

Skill 文件是给 LLM 的指令 + 已知信息(路径/glob/格式注意),机器只负责:
  1. 解析 SOURCE/GLOB 找到会话文件
  2. 采样前几个文件的前几行(控制 token)
  3. 调 LLM,要求返回严格 JSON
  4. 校验(白名单 kind / 必需字段)后写 capture-store(复用幂等写入)

绝不把 LLM 输出未经校验写入;解析失败 → 报错并提示人工确认。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .capture import EVENT_KINDS, CaptureError, _existing_seqs, _write_index, _write_session

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills" / "capture"

# LLM 单次解析的样本上限:文件数 × 每个文件行数(控制 token,够判断格式即可)
SAMPLE_FILES = 8
SAMPLE_LINES = 12
SAMPLE_LINE_LIMIT = 600


def find_skill(agent_type: str, skills_dir: str | Path | None = None) -> Path | None:
    root = Path(skills_dir) if skills_dir else SKILLS_DIR
    path = root / f"{agent_type}.md"
    return path if path.is_file() else None


def parse_skill_sources(skill_text: str) -> list[dict[str, str]]:
    """从 skill 文本提取 SOURCE/GLOB 段(简化约定,支持多组)。

    ## SOURCE
    ~/.gemini/tmp
    ## GLOB
    **/*.jsonl
    """
    sources = []
    matches = re.findall(
        r"##\s+SOURCE\s*\n(.+?)\n##\s+GLOB\s*\n(.+?)(?:\n##|\Z)",
        skill_text, re.DOTALL,
    )
    for source, glob_pattern in matches:
        sources.append({
            "source": source.strip(),
            "glob": glob_pattern.strip(),
        })
    return sources


def scan_skill_files(sources: list[dict[str, str]], limit: int = 50) -> list[Path]:
    """按 skill 的 SOURCE+GLOB 找出会话文件(只读,最多 limit 个)。"""
    files: list[Path] = []
    for item in sources:
        root = Path(item["source"]).expanduser()
        if not root.is_dir():
            continue
        files.extend(sorted(root.glob(item["glob"])))
    return files[:limit]


def _sample_events(files: list[Path]) -> list[dict]:
    """每个文件采前 SAMPLE_LINES 行、每行截断,作为 LLM 的格式样本。"""
    samples = []
    for path in files[:SAMPLE_FILES]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        shown = []
        for line in lines[:SAMPLE_LINES]:
            line = line[:SAMPLE_LINE_LIMIT]
            shown.append(line)
        samples.append({"file": str(path), "lines": shown})
    return samples


def build_prompt(agent_type: str, skill_text: str, samples: list[dict]) -> str:
    return f"""你是 ICLE 的会话捕获适配器。请按下面的 skill 说明,把该 agent 的原始会话解析成标准化事件。

# 目标 agent:{agent_type}

## Skill 说明
{skill_text[:3000]}

## 原始会话样本(每个文件前几行)
{json.dumps(samples, ensure_ascii=False)[:6000]}

## 任务
1. 分析样本结构,确定每个会话文件对应的 session_id、title(可空)、事件序列。
2. 提取文本类事件(kind 只能是:user/assistant/tool_call/tool_result/failure/meta),跳过噪音。
3. 只输出一个 JSON(不要 markdown 代码块、不要解释),格式:
{{"sessions":[{{"session_id":"<去重 id>","title":"<可空>","events":[{{"seq":1,"kind":"user","content":"...","ts":"<可空>"}}]}}]}}
内容为人类可读文本;无法确定的字段用空字符串。"""
    # fmt: off


def parse_llm_result(raw: str, agent_type: str) -> list[dict]:
    """解析并严格校验 LLM 输出:白名单 kind、必需字段、seq 连续。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptureError(f"LLM skill parse failed (not JSON): {exc}") from exc
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, list):
        raise CaptureError("LLM skill parse failed: missing sessions[]")
    cleaned = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("session_id") or "").strip()
        if not session_id:
            raise CaptureError("LLM skill parse failed: empty session_id")
        events = []
        for ev in item.get("events") or []:
            if not isinstance(ev, dict):
                continue
            kind = ev.get("kind")
            if kind not in EVENT_KINDS:
                raise CaptureError(f"LLM skill parse failed: unknown kind {kind!r}")
            content = ev.get("content")
            if not isinstance(content, str):
                raise CaptureError("LLM skill parse failed: non-string content")
            events.append({
                "schema_version": "icle-session-event/v0.1",
                "session_id": session_id,
                "agent_id": agent_type,
                "seq": int(ev.get("seq") or 0),
                "kind": kind,
                "ts": ev.get("ts"),
                "project": None,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "detail": {"source": "llm-skill"},
            })
        if events:
            cleaned.append({
                "session_id": session_id,
                "title": str(item.get("title") or "").strip() or None,
                "events": events,
            })
    if not cleaned:
        raise CaptureError("LLM skill parse failed: no sessions parsed")
    return cleaned


def capture_llm(
    agent_type: str,
    store: str | Path,
    provider,
    skills_dir: str | Path | None = None,
) -> dict:
    """按 skill 用 LLM 解析该 agent 的原始会话并写入 capture-store。

    无 skill → CaptureError(要求内置 adapter 或人工补充 skill)。
    """
    skill = find_skill(agent_type, skills_dir)
    if skill is None:
        raise CaptureError(f"no capture skill for {agent_type} (skills/capture/{agent_type}.md missing)")
    skill_text = skill.read_text(encoding="utf-8")
    sources = parse_skill_sources(skill_text)
    if not sources:
        raise CaptureError(f"skill {skill.name} missing SOURCE/GLOB sections")
    files = scan_skill_files(sources)
    if not files:
        raise CaptureError(f"skill {skill.name}: no session files matched ({len(sources)} source(s))")
    samples = _sample_events(files)
    prompt = build_prompt(agent_type, skill_text, samples)
    raw = provider.complete(prompt)
    parsed = parse_llm_result(raw, agent_type)
    captured = []
    for item in parsed:
        session_id = item["session_id"]
        existing = _existing_seqs(store, agent_type, session_id)
        events = [ev for ev in item["events"] if ev["seq"] not in existing]
        captured.append(
            _write_session(
                store, agent_type, session_id, events, title=item["title"]
            )
        )
    return _write_index(store, captured)


# ---------------------------------------------------------------- generic skill runner
#
# 通用 Skill 执行器:任何注册在 skills/<name>/ 下的标准 Skill 都可被加载执行。
# - SKILL.md 负责流程与判断原则(references/ 是细节,schemas/ 是机器结构)
# - scripts/ 是 LLM 不该负责的确定性工作(Python,由本模块 importlib 加载执行)
# - 调用方只需:load_skill → read_reference → run_script → build_mode_prompt
#   → provider.complete → validate_mode_output
# 现有 capture skill(技能:会话捕获)与本 task-intelligence skill 共用这套机制。

import importlib.util  # noqa: E402

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def load_skill(name: str) -> dict:
    """返回 skill 的 SKILL.md 全文与目录路径;不存在 → CaptureError。"""
    root = SKILLS_ROOT / name
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise CaptureError(f"skill not found: {name} (skills/{name}/SKILL.md missing)")
    return {"name": name, "root": root, "text": skill_md.read_text(encoding="utf-8")}


def read_reference(skill: dict, ref: str) -> str:
    """读取 skill 的 references/<ref>.md(progressive disclosure 细节)。"""
    path = Path(skill["root"]) / "references" / f"{ref}.md"
    if not path.is_file():
        raise CaptureError(f"skill {skill['name']}: reference missing: references/{ref}.md")
    return path.read_text(encoding="utf-8")


def run_script(skill: dict, script: str, *args: object, fn: str | None = None) -> object:
    """importlib 加载执行 skill 的 scripts/<script>.py 中的函数。

    约定:脚本提供与文件名同名的入口函数(例如 compact_session / validate_plan);
    多入口脚本可显式传 fn(例如 replan 校验用 validate_replan_proposal)。
    """
    path = Path(skill["root"]) / "scripts" / f"{script}.py"
    if not path.is_file():
        raise CaptureError(f"skill {skill['name']}: script missing: scripts/{script}.py")
    spec = importlib.util.spec_from_file_location(f"icle_skill_{skill['name']}_{script}", path)
    if spec is None or spec.loader is None:
        raise CaptureError(f"skill {skill['name']}: cannot load script {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn_name = fn or script
    function = getattr(module, fn_name, None)
    if not callable(function):
        raise CaptureError(f"skill {skill['name']}: scripts/{script}.py has no {fn_name}() function")
    return function(*args)


def build_mode_prompt(
    skill: dict,
    mode: str,
    *,
    payload: dict,
    refs: list[str] | None = None,
    extra_instruction: str = "",
) -> str:
    """组装 mode 的执行 prompt:SKILL.md + 指定 references + payload + 语言指令。"""
    parts = [f"# Skill: {skill['name']} (version from SKILL.md)\n"]
    parts.append(f"## Operating mode: {mode}\n")
    parts.append("## SKILL.md\n" + skill["text"][:4000])
    for ref in refs or []:
        parts.append(f"\n## Reference: {ref}\n" + read_reference(skill, ref)[:6000])
    parts.append(f"\n## Input payload\n{json.dumps(payload, ensure_ascii=False)[:8000]}")
    if extra_instruction:
        parts.append(f"\n## Extra instructions\n{extra_instruction}")
    parts.append(
        "\n## Output contract\n"
        "Return ONLY a JSON object matching the schemas in references. "
        "No markdown fences, no commentary."
    )
    return "\n".join(parts)
