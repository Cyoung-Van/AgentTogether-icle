#!/usr/bin/env python3
"""compact_session.py — 确定性会话压缩(analyze-session Step 1,不使用 LLM)。

输入: SessionEvent[] (capture-store 标准事件)
输出: AnalysisBundle:
  {
    "schema_version": "icle-analysis-bundle/v0.1",
    "session_id": ...,
    "events": [compact events],
    "noise_dropped": <int>,
    "boundaries": [PossibleBoundary candidates]  # Step 2 规则粗边界也在此
  }

过滤噪音: heartbeat / 重复 streaming chunk / UI 事件 / 空内容 / 纯 JSON 工具回显。
压缩: 超长 content 截断、tool 结果压缩为摘要。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

NOISE_KINDS = {"heartbeat", "usage", "meta"}  # 会话时间线噪音(usage/meta 无任务信号)
CONTENT_LIMIT = 400          # 单事件 content 保留上限
TOOL_RESULT_LIMIT = 120      # tool_result 摘要上限
MAX_EVENTS = 2000            # bundle 事件上限(超出按 seq 截断,防 token 爆炸)
BOUNDARY_GAP_SECONDS = 300   # 明显时间间隔阈值(5 分钟)
BOUNDARY_MIN_CONF = 0.4      # 规则边界置信度下限


def _parse_ts(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(ts)
            except ValueError:
                return None
    return None


def _extract_text(data) -> str:
    """递归提取 JSON wire 中的第一段可读文本(与前端 prettyContent 同策略)。"""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        for item in data:
            found = _extract_text(item)
            if found:
                return found
        return ""
    if isinstance(data, dict):
        for key in ("text", "content", "message", "input"):
            if key in data:
                found = _extract_text(data[key])
                if found:
                    return found
        return ""
    return ""


def _is_noise(event: dict) -> bool:
    kind = event.get("kind")
    if kind in NOISE_KINDS:
        return True
    content = (event.get("content") or "").strip()
    if not content:
        return True
    # JSON 包裹事件(如 kimi wire):递归有可读 text 则保留,纯工具回显则噪音
    if content.startswith("{") and content.endswith("}"):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return False
        return not _extract_text(data)
    return False


def compact(events: list[dict]) -> dict:
    """压缩事件 + 产出规则粗边界(PossibleBoundary[],Step 2 的信号)。"""
    session_id = events[0].get("session_id") if events else ""
    compact_events: list[dict] = []
    noise_dropped = 0
    boundaries: list[dict] = []
    prev_ts: float | None = None
    prev_project = None
    seen: set[tuple] = set()  # (kind, content) 去重(重复 streaming chunk)

    for event in events:
        if _is_noise(event):
            noise_dropped += 1
            continue
        kind = event.get("kind", "unknown")
        content = (event.get("content") or "").strip()
        key = (kind, content)
        if key in seen:
            noise_dropped += 1
            continue
        seen.add(key)

        # JSON wire 事件:提取可读 text 作为 content(LLM 只看到自然语言)
        if content.startswith("{") and content.endswith("}"):
            try:
                data = json.loads(content)
                extracted = _extract_text(data)
                if extracted:
                    content = extracted
            except json.JSONDecodeError:
                pass

        ts = _parse_ts(event.get("ts") or event.get("created_at"))
        project = event.get("project")
        ce = {
            "event_id": f"e{event.get('seq')}",
            "seq": event.get("seq"),
            "turn_id": None,  # v0.1 不聚合 turn,保留 seq 顺序
            "ts": event.get("ts") or event.get("created_at"),
            "role": kind,
            "content": content[:CONTENT_LIMIT],
        }
        if kind == "tool_result":
            ce["tool_result_summary"] = content[:TOOL_RESULT_LIMIT]
            ce["content"] = ""
        if project:
            ce["project"] = project

        # 规则粗边界信号
        signals: list[str] = []
        if ts is not None and prev_ts is not None and (ts - prev_ts) > BOUNDARY_GAP_SECONDS:
            signals.append("time_gap")
        if project and prev_project and project != prev_project:
            signals.append("project_switch")
        if kind == "user" and signals:
            signals.append("new_user_request")
        if signals:
            boundaries.append({
                "event_id": ce["event_id"],
                "signals": signals,
                "boundary_probability": min(1.0, BOUNDARY_MIN_CONF + 0.15 * len(signals)),
            })

        compact_events.append(ce)
        prev_ts = ts
        prev_project = project

        if len(compact_events) >= MAX_EVENTS:
            break

    return {
        "schema_version": "icle-analysis-bundle/v0.1",
        "session_id": session_id,
        "events": compact_events,
        "noise_dropped": noise_dropped,
        "boundaries": boundaries,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: compact_session.py <events.jsonl> [out.json]", file=sys.stderr)
        return 2
    events = []
    for line in Path(argv[0]).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    bundle = compact(events)
    out = argv[1] if len(argv) > 1 else None
    if out:
        Path(out).write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(bundle, ensure_ascii=False))
    return 0


compact_session = compact  # 入口约定:与文件名同名


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
