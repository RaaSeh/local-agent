from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RetrievalHit:
    path: str
    score: int
    snippet: str


class RetrievalIndex:
    """Simple file retrieval with lightweight indexing and source citations."""

    def __init__(
        self,
        workspace_root: str | Path,
        config_path: str | Path = "config/retrieval.yaml",
        state_dir: str | Path = "state/retrieval",
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.config_path = self.workspace_root / Path(config_path)
        self.state_dir = self.workspace_root / Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.state_dir / "index.json"
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        return {
            "enabled": True,
            "default_citations": True,
            "include_paths": ["README.md", "prompts", "src", "docs", "workspaces"],
            "include_extensions": [".md", ".txt", ".yaml", ".yml", ".toml", ".py", ".json"],
            "max_file_chars": 12000,
        }

    def _include_paths(self) -> list[Path]:
        paths = []
        for raw in self._config.get("include_paths", []):
            path = self.workspace_root / Path(str(raw))
            if path.exists():
                paths.append(path)
        return paths

    def _include_extensions(self) -> set[str]:
        return {str(ext).lower() for ext in self._config.get("include_extensions", [])}

    def _iter_files(self):
        include_ext = self._include_extensions()
        for base in self._include_paths():
            if base.is_file():
                if base.suffix.lower() in include_ext:
                    yield base
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in include_ext:
                    continue
                yield path

    def rebuild(self) -> int:
        max_chars = int(self._config.get("max_file_chars", 12000))
        docs: list[dict[str, Any]] = []
        for path in self._iter_files():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
            docs.append(
                {
                    "path": str(path.relative_to(self.workspace_root)).replace("\\", "/"),
                    "text": text,
                    "tokens": self._tokenize(text),
                }
            )
        payload = {"documents": docs}
        self.index_path.write_text(json.dumps(payload), encoding="utf-8")
        return len(docs)

    def ensure_index(self) -> int:
        if not self.index_path.exists():
            return self.rebuild()
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "documents" not in payload:
                return self.rebuild()
            return len(payload.get("documents", []))
        except json.JSONDecodeError:
            return self.rebuild()

    def _load_index(self) -> list[dict[str, Any]]:
        self.ensure_index()
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        docs = payload.get("documents", [])
        if isinstance(docs, list):
            return docs
        return []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9_\-/\.]+", text.lower())

    def query(self, query_text: str, limit: int = 5, snippet_chars: int = 360) -> list[RetrievalHit]:
        terms = [t for t in self._tokenize(query_text) if len(t) > 1]
        if not terms:
            return []

        hits: list[RetrievalHit] = []
        for doc in self._load_index():
            tokens = set(doc.get("tokens", []))
            score = sum(1 for term in terms if term in tokens)
            if score <= 0:
                continue
            text = str(doc.get("text", ""))
            snippet = self._build_snippet(text=text, terms=terms, max_chars=snippet_chars)
            hits.append(RetrievalHit(path=str(doc.get("path", "")), score=score, snippet=snippet))

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    @staticmethod
    def _build_snippet(text: str, terms: list[str], max_chars: int) -> str:
        lowered = text.lower()
        best_index = -1
        for term in terms:
            idx = lowered.find(term)
            if idx != -1:
                best_index = idx
                break
        if best_index == -1:
            return text[:max_chars]

        start = max(best_index - int(max_chars * 0.3), 0)
        end = min(start + max_chars, len(text))
        return text[start:end].replace("\n", " ").strip()

    def render_hits(self, query_text: str, limit: int = 5, include_citations: bool = True) -> str:
        hits = self.query(query_text=query_text, limit=limit)
        if not hits:
            return "No retrieval matches."

        lines = ["Retrieved sources:"]
        for idx, hit in enumerate(hits, start=1):
            source = f"[{hit.path}]" if include_citations else hit.path
            lines.append(f"{idx}. {source} (score={hit.score})")
            lines.append(f"   {hit.snippet}")
        return "\n".join(lines)
