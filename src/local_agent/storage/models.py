from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RunStatus = Literal["queued", "running", "blocked", "completed", "failed"]


@dataclass
class RunMetadata:
    run_id: str
    workflow: str
    status: RunStatus
    business_profile: str
    created_at: str
