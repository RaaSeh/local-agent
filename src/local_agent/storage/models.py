from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunMetadata:
    run_id: str
    workflow: str
    status: str
    business_profile: str
    created_at: str
