from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..episode import EpisodeError, list_episodes, show_episode
from ..replay import list_replays

router = APIRouter()


@router.get("/episodes")
def episodes(
    request: Request,
    search: str | None = None,
    agent: str | None = None,
    project: str | None = None,
) -> dict:
    store = request.app.state.store
    items = list_episodes(store)
    replays = list_replays(store)
    replay_counts: dict[str, int] = {}
    replay_agents: dict[str, set[str]] = {}
    for entry in replays:
        episode_id = entry["episode_id"]
        replay_counts[episode_id] = replay_counts.get(episode_id, 0) + 1
        replay_agents.setdefault(episode_id, set()).add(entry.get("agent", ""))
    enriched = [{**item, "replays": replay_counts.get(item["episode_id"], 0)} for item in items]
    if search:
        needle = search.lower()
        enriched = [e for e in enriched if needle in e["request"].lower()]
    if agent:
        enriched = [
            e for e in enriched
            if e["agent"] == agent or agent in replay_agents.get(e["episode_id"], set())
        ]
    if project:
        enriched = [e for e in enriched if e["project_id"] == project]
    return {"episodes": enriched, "total": len(enriched)}


@router.get("/episodes/{episode_id}")
def episode_detail(request: Request, episode_id: str) -> dict:
    store = request.app.state.store
    try:
        episode = show_episode(store, episode_id)
    except EpisodeError:
        raise HTTPException(status_code=404, detail=f"unknown episode: {episode_id}")
    replays = [r for r in list_replays(store, episode_id)]
    return {"episode": episode, "replays": replays}
