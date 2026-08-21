"""icle CLI: capture subcommand (P1)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .active import BudgetError, consume_budget, get_budget, set_budget, suggest_replays
from .capture import CaptureError, capture_hermes, capture_kimi
from .collab import author_reviewer, handoff, parallel_compare
from .discovery import cmd_doctor, cmd_scan
from .evolution import cmd_rebuild_c_state
from .episode import EpisodeError, create_episode, list_episodes, show_episode
from .intelligence import (
    IntelligenceError,
    analyze_failure,
    get_provider,
    judge_pair,
    propose_tasks,
    similar_episodes,
)
from .judge import JudgeError, record_judgment, record_result_mark, route_suggest
from .replay import DIRECT_CLI_AGENTS, ReplayError, list_replays, make_direct_executor, replay
from .router import RouterError, recommend_agent


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _cmd_serve(args) -> int:
    if not _is_loopback_host(args.host) and not os.environ.get("ICLE_TOKEN", "").strip():
        print("icle: refusing non-loopback host without ICLE_TOKEN", file=sys.stderr)
        return 2
    try:
        import uvicorn
    except ImportError:
        print("icle: uvicorn not installed; use the project venv (.venv)", file=sys.stderr)
        return 2
    from .api.app import create_app

    app = create_app(store=args.store, capture_store=args.capture_store)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icle", description="Personal Agent Experience & Routing System")
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture", help="capture agent sessions into normalized SessionEvent store")
    capture.add_argument("--agent", required=True, choices=["hermes", "kimi"])
    capture.add_argument("--source", required=True, type=Path, help="hermes state.db or kimi sessions dir")
    capture.add_argument("--store", required=True, type=Path)
    capture.add_argument("--json", action="store_true", dest="as_json")

    episode_cmd = commands.add_parser("episode", help="task episode management")
    episode_sub = episode_cmd.add_subparsers(dest="episode_command", required=True)
    create = episode_sub.add_parser("create", help="manually mark a captured session as a TaskEpisode")
    create.add_argument("--store", required=True, type=Path)
    create.add_argument("--capture-store", required=True, type=Path)
    create.add_argument("--session", required=True)
    create.add_argument("--agent", required=True, choices=["hermes", "kimi"])
    create.add_argument("--project-id", required=True)
    create.add_argument("--project", required=True, type=Path)
    create.add_argument("--request", required=True, help="the original user request (verbatim or faithful)")
    create.add_argument("--from-seq", type=int, default=None)
    create.add_argument("--to-seq", type=int, default=None)
    create.add_argument("--json", action="store_true", dest="as_json")
    list_cmd = episode_sub.add_parser("list")
    list_cmd.add_argument("--store", required=True, type=Path)
    list_cmd.add_argument("--json", action="store_true", dest="as_json")
    show = episode_sub.add_parser("show")
    show.add_argument("episode_id")
    show.add_argument("--store", required=True, type=Path)
    show.add_argument("--json", action="store_true", dest="as_json")

    replay_cmd = commands.add_parser("replay", help="replay an episode from T0 with another agent")
    replay_cmd.add_argument("episode_id")
    replay_cmd.add_argument("--store", required=True, type=Path)
    replay_cmd.add_argument("--capture-store", default=None, type=Path)
    replay_cmd.add_argument("--agent", required=True, choices=sorted(DIRECT_CLI_AGENTS))
    replay_cmd.add_argument("--mode", default="CLEAN",
                            choices=["CLEAN", "PROJECT_STATE", "SELECTED_HISTORY"])
    replay_cmd.add_argument("--timeout", type=float, default=900)
    replay_cmd.add_argument("--json", action="store_true", dest="as_json")
    replay_list = commands.add_parser("replays", help="list replay runs")
    replay_list.add_argument("--store", required=True, type=Path)
    replay_list.add_argument("--episode", default=None)
    replay_list.add_argument("--json", action="store_true", dest="as_json")

    judge_cmd = commands.add_parser("judge", help="record a human judgment")
    judge_cmd.add_argument("--store", required=True, type=Path)
    judge_cmd.add_argument("--episode", default=None, help="episode id (single subject)")
    judge_cmd.add_argument("--pair", nargs=2, default=None, metavar=("REPLAY_A", "REPLAY_B"))
    judge_cmd.add_argument("--kind", required=True,
                           choices=["prefer_a", "prefer_b", "tie", "inconclusive"])
    judge_cmd.add_argument("--tags", nargs="*", default=[])
    judge_cmd.add_argument("--note", default="")
    judge_cmd.add_argument("--json", action="store_true", dest="as_json")

    mark_cmd = commands.add_parser("mark", help="record accept/edit/reject/redo or implicit signal")
    mark_cmd.add_argument("episode_id")
    mark_cmd.add_argument("--store", required=True, type=Path)
    mark_cmd.add_argument("--agent", required=True)
    mark_cmd.add_argument("--mark", required=True)
    mark_cmd.add_argument("--note", default="")
    mark_cmd.add_argument("--json", action="store_true", dest="as_json")

    route_cmd = commands.add_parser("route", help="light rule-based agent suggestion")
    route_cmd.add_argument("--store", required=True, type=Path)
    route_cmd.add_argument("--project-id", default=None)
    route_cmd.add_argument("--json", action="store_true", dest="as_json")

    extract_cmd = commands.add_parser("extract", help="LLM task discovery proposal from a captured session")
    extract_cmd.add_argument("--capture-store", required=True, type=Path)
    extract_cmd.add_argument("--session", required=True)
    extract_cmd.add_argument("--agent", required=True, choices=["hermes", "kimi"])
    extract_cmd.add_argument("--provider", default="kimi")
    extract_cmd.add_argument("--json", action="store_true", dest="as_json")

    llm_judge = commands.add_parser("judge-llm", help="LLM pairwise judge on two replay results")
    llm_judge.add_argument("--store", required=True, type=Path)
    llm_judge.add_argument("--pair", nargs=2, required=True, metavar=("REPLAY_A", "REPLAY_B"))
    llm_judge.add_argument("--provider", default="kimi")
    llm_judge.add_argument("--json", action="store_true", dest="as_json")

    similar_cmd = commands.add_parser("similar", help="deterministic similar-episode search")
    similar_cmd.add_argument("--store", required=True, type=Path)
    similar_cmd.add_argument("--query", required=True)
    similar_cmd.add_argument("--project-id", default=None)
    similar_cmd.add_argument("--limit", type=int, default=10)
    similar_cmd.add_argument("--json", action="store_true", dest="as_json")

    recommend_cmd = commands.add_parser("recommend", help="full experience-router recommendation")
    recommend_cmd.add_argument("--store", required=True, type=Path)
    recommend_cmd.add_argument("--task", required=True)
    recommend_cmd.add_argument("--project-id", default=None)
    recommend_cmd.add_argument("--json", action="store_true", dest="as_json")

    budget_cmd = commands.add_parser("budget", help="replay budget show/set")
    budget_cmd.add_argument("--store", required=True, type=Path)
    budget_cmd.add_argument("--set", type=int, default=None, metavar="PER_DAY")
    budget_cmd.add_argument("--json", action="store_true", dest="as_json")

    suggest_cmd = commands.add_parser("suggest-replays", help="information-value replay suggestions")
    suggest_cmd.add_argument("--store", required=True, type=Path)
    suggest_cmd.add_argument("--limit", type=int, default=5)
    suggest_cmd.add_argument("--json", action="store_true", dest="as_json")

    collab_cmd = commands.add_parser("collab", help="multi-agent collaboration patterns")
    collab_sub = collab_cmd.add_subparsers(dest="collab_command", required=True)
    ho = collab_sub.add_parser("handoff")
    ho.add_argument("episode_id")
    ho.add_argument("--store", required=True, type=Path)
    ho.add_argument("--agent-a", required=True)
    ho.add_argument("--agent-b", required=True)
    ho.add_argument("--json", action="store_true", dest="as_json")
    par = collab_sub.add_parser("parallel")
    par.add_argument("episode_id")
    par.add_argument("--store", required=True, type=Path)
    par.add_argument("--agents", nargs="+", required=True)
    par.add_argument("--json", action="store_true", dest="as_json")
    ar = collab_sub.add_parser("author-review")
    ar.add_argument("episode_id")
    ar.add_argument("--store", required=True, type=Path)
    ar.add_argument("--author", required=True)
    ar.add_argument("--reviewer", required=True)
    ar.add_argument("--json", action="store_true", dest="as_json")

    serve_cmd = commands.add_parser("serve", help="serve the ICLE API (+ built WebUI later)")
    serve_cmd.add_argument("--store", default=Path("./experience-store"), type=Path)
    serve_cmd.add_argument("--capture-store", default=Path("./capture-store"), type=Path)
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)

    # v0.4 P9: local agent discovery
    scan_cmd = commands.add_parser("scan", help="scan local agent CLIs")
    scan_agents_cmd = scan_cmd.add_subparsers(dest="scan_command", required=True)
    scan_agents = scan_agents_cmd.add_parser("agents", help="detect installed agent CLIs")
    scan_agents.add_argument("--store", default=Path("./experience-store"), type=Path)
    scan_agents.add_argument("--capture-store", default=Path("./capture-store"), type=Path)

    doctor_cmd = commands.add_parser("doctor", help="agent health diagnostics")
    doctor_agents_cmd = doctor_cmd.add_subparsers(dest="doctor_command", required=True)
    doctor_agents = doctor_agents_cmd.add_parser("agents", help="per-agent health table")
    doctor_agents.add_argument("--store", default=Path("./experience-store"), type=Path)
    doctor_agents.add_argument("--capture-store", default=Path("./capture-store"), type=Path)

    measure_cmd = commands.add_parser("measure", help="A/B/J/C measurement maintenance")
    measure_sub = measure_cmd.add_subparsers(dest="measure_command", required=True)
    rebuild_c = measure_sub.add_parser(
        "rebuild-c-state",
        help="retire the C belief ledger and rebuild it from current evidence",
    )
    rebuild_c.add_argument("--store", default=Path("./experience-store"), type=Path)
    rebuild_c.add_argument("--reason", required=True, help="why the ledger is being retired")
    rebuild_c.add_argument(
        "--confirm", action="store_true", help="required: this retires the current chain"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _cmd_serve(args)

        if args.command == "measure":
            return cmd_rebuild_c_state(args.store, reason=args.reason, confirm=args.confirm)

        if args.command == "scan":
            return cmd_scan(args.store, args.capture_store)

        if args.command == "doctor":
            return cmd_doctor(args.store, args.capture_store)

        if args.command == "capture":
            if args.agent == "hermes":
                index = capture_hermes(args.source, args.store)
            else:
                index = capture_kimi(args.source, args.store)
            if args.as_json:
                print(json.dumps(index, ensure_ascii=False, indent=2))
            else:
                total_new = sum(s["new_events"] for s in index["sessions"])
                print(f"captured {total_new} new event(s) across {len(index['sessions'])} session(s) -> {args.store}")
            return 0

        if args.command == "episode":
            if args.episode_command == "create":
                revision = {
                    "schema_version": "icle-agent-revision/v0.1",
                    "agent_id": args.agent,
                    "revision_id": "manual-capture",
                    "model": "unknown",
                    "cli": "unknown",
                    "provider": "unknown",
                    "persona_sha256": None,
                    "memory_sha256": None,
                    "tools_sha256": None,
                    "execution_provider": "direct_cli",
                    "created_at": "2026-08-14T00:00:00Z",
                }
                doc = create_episode(
                    args.store,
                    session_id=args.session,
                    agent_revision=revision,
                    project_id=args.project_id,
                    project_path=args.project,
                    user_request=args.request,
                    from_seq=args.from_seq,
                    to_seq=args.to_seq,
                    capture_store=args.capture_store,
                )
                print(json.dumps(doc, ensure_ascii=False, indent=2))
            elif args.episode_command == "list":
                print(json.dumps(list_episodes(args.store), ensure_ascii=False, indent=2))
            else:
                print(json.dumps(show_episode(args.store, args.episode_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "replay":
            revision = {
                "schema_version": "icle-agent-revision/v0.1",
                "agent_id": args.agent,
                "revision_id": "manual-replay",
                "model": "unknown", "cli": "unknown", "provider": "unknown",
                "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
                "execution_provider": "direct_cli",
                "created_at": "2026-08-14T00:00:00Z",
            }
            run = replay(
                args.store,
                args.episode_id,
                target_agent_revision=revision,
                mode=args.mode,
                capture_store=args.capture_store,
                timeout_sec=args.timeout,
            )
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 0

        if args.command == "replays":
            print(json.dumps(list_replays(args.store, args.episode), ensure_ascii=False, indent=2))
            return 0

        if args.command == "judge":
            if args.pair:
                subject = {"type": "replay_pair", "a": args.pair[0], "b": args.pair[1]}
            elif args.episode:
                subject = {"type": "episode", "id": args.episode}
            else:
                print("icle: judge requires --episode or --pair", file=sys.stderr)
                return 2
            result = record_judgment(
                args.store, subject=subject, kind=args.kind,
                reason_tags=args.tags, note=args.note,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "mark":
            result = record_result_mark(
                args.store, episode_id=args.episode_id, mark=args.mark,
                agent_id=args.agent, note=args.note,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "route":
            print(json.dumps(route_suggest(args.store, project_id=args.project_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "extract":
            events_path = args.capture_store / args.agent / args.session / "events.jsonl"
            if not events_path.is_file():
                raise EpisodeError(f"captured session not found: {events_path}")
            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            result = propose_tasks(get_provider(args.provider), events)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "judge-llm":
            def _load(replay_id: str) -> tuple[str, str]:
                root = args.store / "replays" / replay_id
                run = json.loads((root / "replay.json").read_text(encoding="utf-8"))
                stdout = (root / "stdout.log").read_text(encoding="utf-8") if (root / "stdout.log").is_file() else ""
                return run, stdout

            run_a, out_a = _load(args.pair[0])
            run_b, out_b = _load(args.pair[1])
            if run_a["episode_id"] != run_b["episode_id"]:
                raise JudgeError("pair replays must belong to the same episode")
            episode = show_episode(args.store, run_a["episode_id"])
            result = judge_pair(
                get_provider(args.provider),
                task=episode["task_start"]["original_user_request"],
                result_a=out_a[-3000:],
                result_b=out_b[-3000:],
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "similar":
            episodes = [
                show_episode(args.store, entry["episode_id"])
                for entry in list_episodes(args.store)
            ]
            print(json.dumps(
                similar_episodes(episodes, args.query, project_id=args.project_id, limit=args.limit),
                ensure_ascii=False, indent=2,
            ))
            return 0
        if args.command == "recommend":
            print(json.dumps(
                recommend_agent(args.store, task=args.task, project_id=args.project_id),
                ensure_ascii=False, indent=2,
            ))
            return 0

        if args.command == "budget":
            budget = set_budget(args.store, args.set) if args.set else get_budget(args.store)
            print(json.dumps(budget, ensure_ascii=False, indent=2))
            return 0

        if args.command == "suggest-replays":
            print(json.dumps(suggest_replays(args.store, limit=args.limit), ensure_ascii=False, indent=2))
            return 0

        if args.command == "collab":
            if args.collab_command == "handoff":
                result = handoff(
                    args.store, args.episode_id,
                    agent_a=args.agent_a, agent_b=args.agent_b,
                    executor_a=make_direct_executor(args.agent_a),
                    executor_b=make_direct_executor(args.agent_b),
                )
            elif args.collab_command == "parallel":
                result = parallel_compare(
                    args.store, args.episode_id,
                    agents=[(a, make_direct_executor(a)) for a in args.agents],
                )
            else:
                result = author_reviewer(
                    args.store, args.episode_id,
                    author=args.author, reviewer=args.reviewer,
                    executor_author=make_direct_executor(args.author),
                    executor_reviewer=make_direct_executor(args.reviewer),
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except (BudgetError, CaptureError, EpisodeError, IntelligenceError, JudgeError, OSError, ReplayError, RouterError) as exc:
        print(f"icle: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
