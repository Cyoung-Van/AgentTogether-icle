"""Process-local status for ICLE's shared intelligence provider.

This is an operational projection, not append-only evidence.  It deliberately
stores only operation labels and timing, never prompts, task text, responses,
or credentials.  Multiple simultaneous LLM requests are tracked independently.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class IntelligenceBusyError(RuntimeError):
    """Raised when a model change races with an active intelligence request."""


OPERATION_LABELS: dict[str, tuple[str, str]] = {
    "analyze_session": ("Analyzing session", "正在分析会话"),
    "extract_tasks": ("Extracting task proposals", "正在提取任务 Proposal"),
    "split_task": ("Drafting subtask splits", "正在拆分子任务"),
    "refine_split": ("Refining subtask split", "正在细化子任务拆分"),
    "customize_template": ("Customizing task template", "正在定制任务模板"),
    "analyze_task": ("Analyzing task", "正在分析任务"),
    "plan_task": ("Planning task", "正在规划任务"),
    "replan_task": ("Replanning after failure", "正在根据失败重新规划"),
    "judge_result": ("Evaluating task result", "正在评估任务结果"),
    "summarize_reports": ("Summarizing evaluation reports", "正在汇总评估表"),
    "capture_session": ("Parsing Agent sessions", "正在解析 Agent 会话"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str, ended_at: str | None = None) -> int:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat((ended_at or _now()).replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1000))
    except ValueError:
        return 0


class IntelligenceStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._last: dict[str, Any] | None = None

    def start(self, operation: str) -> str:
        operation_id = "io-" + uuid.uuid4().hex[:12]
        en, zh = OPERATION_LABELS.get(operation, (operation, operation))
        entry = {
            "operation_id": operation_id,
            "operation": operation,
            "label": en,
            "label_zh": zh,
            "started_at": _now(),
        }
        with self._lock:
            self._active[operation_id] = entry
        return operation_id

    def finish(self, operation_id: str, *, outcome: str) -> None:
        if outcome not in {"completed", "failed"}:
            outcome = "failed"
        ended_at = _now()
        with self._lock:
            entry = self._active.pop(operation_id, None)
            if entry is None:
                return
            self._last = {
                **entry,
                "outcome": outcome,
                "ended_at": ended_at,
                "duration_ms": _duration_ms(entry["started_at"], ended_at),
            }

    def run_when_idle(self, callback: Callable[[], T]) -> T:
        """Run a short configuration mutation atomically with respect to requests."""
        with self._lock:
            if self._active:
                raise IntelligenceBusyError("wait for the current intelligence request to finish")
            return callback()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = [dict(item) for item in self._active.values()]
            last = dict(self._last) if self._last else None
        active.sort(key=lambda item: item["started_at"])
        for item in active:
            item["duration_ms"] = _duration_ms(item["started_at"])
        return {
            "state": "busy" if active else "idle",
            "active_count": len(active),
            "active": active,
            "last": last,
            "checked_at": _now(),
        }
