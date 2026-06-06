from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


class RunStore:
    def __init__(self, runs_dir: str | Path = "runs"):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def save(self, workflow: str, payload: dict) -> Path:
        run_id = payload.get("run_id") or _utc_now_compact()
        path = self.runs_dir / f"{workflow}-{run_id}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def append_index(self, payload: dict, index_name: str = "run_index.jsonl") -> Path:
        path = self.runs_dir / index_name
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
        return path

    def recent_run_summaries(self, limit: int = 5, workflow: str | None = None) -> list[dict]:
        runs: list[tuple[float, Path]] = []
        pattern = f"{workflow}-*.json" if workflow else "*.json"
        for path in self.runs_dir.glob(pattern):
            if not path.is_file() or path.name == "run_index.jsonl":
                continue
            if workflow is None and path.name == "run_index.jsonl":
                continue
            try:
                runs.append((path.stat().st_mtime, path))
            except OSError:
                continue

        summaries: list[dict] = []
        for _, path in sorted(runs, key=lambda item: item[0], reverse=True)[:limit]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            summary = {
                "path": str(path),
                "run_id": payload.get("run_id"),
                "workflow": path.stem.split("-", 1)[0],
                "business_profile": payload.get("business_profile"),
                "status": payload.get("status"),
                "confidence": payload.get("confidence"),
                "goal": str(payload.get("goal", ""))[:180],
                "final_summary": str(payload.get("final_memo", {}).get("summary", ""))[:320],
            }
            summaries.append(summary)
        return summaries

    def render_recent_runs(self, limit: int = 5, workflow: str | None = None, max_chars: int = 3000) -> str:
        summaries = self.recent_run_summaries(limit=limit, workflow=workflow)
        if not summaries:
            return "No past runs recorded."

        lines = ["Recent runs:"]
        for item in summaries:
            lines.append(
                f"- {item.get('workflow')} {item.get('run_id')} | {item.get('business_profile')} | {item.get('status')} | confidence={item.get('confidence')}"
            )
            goal = str(item.get("goal", "")).strip()
            if goal:
                lines.append(f"  Goal: {goal}")
            final_summary = str(item.get("final_summary", "")).strip()
            if final_summary:
                lines.append(f"  Summary: {final_summary}")
        return "\n".join(lines)[:max_chars]
