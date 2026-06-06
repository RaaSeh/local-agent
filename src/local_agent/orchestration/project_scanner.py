from __future__ import annotations

import os
from pathlib import Path

# Files read in full whenever the directory is scanned, in priority order.
_PRIORITY_FILES = [
    "SESSION_CONTEXT.md",
    "STATUS.md",
    "README.md",
    "WORKSPACE_ORGANIZATION.md",
]

# Max chars to include per file so the context stays phone-readable.
_FILE_CHAR_LIMIT = 2000
# Max chars for the combined snapshot injected into prompts.
_SNAPSHOT_CHAR_LIMIT = 6000


def scan_external_project(project_dir: str | Path | None) -> str:
    """
    Return a compact textual snapshot of an external project directory.

    Reads priority status/readme files in full, then lists the top-level
    directory tree so the model knows what actually exists on disk.

    Returns an empty string if project_dir is None or does not exist.
    """
    if not project_dir:
        return ""

    root = Path(project_dir).resolve()
    if not root.exists():
        return ""

    parts: list[str] = [f"## External project snapshot: {root}"]

    # --- Priority files read in full ---
    for name in _PRIORITY_FILES:
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")[:_FILE_CHAR_LIMIT]
            parts.append(f"\n### {name}\n{text}")

    # --- Top-level directory listing ---
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = []
        for entry in entries[:60]:
            if entry.is_dir():
                # Show one level deeper for important dirs
                if entry.name in {"agents", "core", "workflows", "tests", "docs", "reference"}:
                    children = sorted(entry.iterdir(), key=lambda p: p.name.lower())
                    child_names = ", ".join(c.name for c in children[:12])
                    lines.append(f"  {entry.name}/  [{child_names}]")
                else:
                    lines.append(f"  {entry.name}/")
            else:
                lines.append(f"  {entry.name}")
        parts.append("\n### Directory tree\n" + "\n".join(lines))
    except PermissionError:
        pass

    return "\n".join(parts)[:_SNAPSHOT_CHAR_LIMIT]


def detect_project_path_in_text(text: str) -> str | None:
    """
    Extract the first Windows or Unix absolute path from free-form text and
    return it if it exists on disk.
    """
    import re

    # Windows absolute paths like C:\Users\...
    win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'\[\]<>|,;]+", text)
    for raw in win_paths:
        candidate = Path(raw.rstrip(".,)"))
        if candidate.exists():
            return str(candidate)

    # POSIX absolute paths
    posix_paths = re.findall(r"/[^\s\"'\[\]<>|,;]+", text)
    for raw in posix_paths:
        candidate = Path(raw.rstrip(".,)"))
        if candidate.exists():
            return str(candidate)

    return None


def get_external_project_dir() -> str | None:
    """Return EXTERNAL_PROJECT_DIR env var if set and the path exists."""
    raw = os.getenv("EXTERNAL_PROJECT_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return str(path) if path.exists() else None
