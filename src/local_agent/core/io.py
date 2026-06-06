import json
from pathlib import Path
from datetime import datetime

RUNS_DIR = Path("runs")
DIGESTS_DIR = RUNS_DIR / "digests"

def ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)

def write_json(prefix: str, record: dict) -> Path:
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RUNS_DIR / f"{prefix}-{ts}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path

def write_digest_markdown(date_yyyy_mm_dd: str, md: str) -> Path:
    ensure_dirs()
    path = DIGESTS_DIR / f"{date_yyyy_mm_dd}.md"
    path.write_text(md, encoding="utf-8")
    return path