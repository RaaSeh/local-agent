from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from local_agent.config import get_env, load_env

# Documents that live under the ``drafts.`` id namespace are unpublished in
# Sanity: they are invisible to the public/published dataset and only show up in
# Sanity Studio for a human to review. Chad is only ever allowed to touch these.
DRAFT_PREFIX = "drafts."


class SanityError(RuntimeError):
    """Raised for Sanity configuration or API errors."""


@dataclass(frozen=True)
class SanityConfig:
    project_id: str
    api_token: str
    dataset: str = "production"
    api_version: str = "2024-01-01"

    @property
    def base_url(self) -> str:
        # https://www.sanity.io/docs/http-api
        return f"https://{self.project_id}.api.sanity.io/v{self.api_version}/data"


class SanityClient:
    """Minimal Sanity HTTP API client with a strict draft-only write model.

    DRAFT-ONLY DESIGN (intentional):
    Every document this client creates or patches MUST carry an ``_id`` that
    starts with ``drafts.``. In Sanity, a document with that id prefix is an
    unpublished draft and is invisible to the public dataset/CDN. Publishing in
    Sanity means writing/replacing the document WITHOUT the ``drafts.`` prefix
    (or deleting the ``drafts.*`` copy while creating the bare-id copy).

    This client deliberately implements NO publish/promote/unpublish operation.
    Publishing is a human action performed in Sanity Studio. The ``_require_draft_id``
    guard below makes it impossible to accidentally write a non-draft document.
    """

    def __init__(self, config: SanityConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> "SanityClient":
        load_env()
        project_id = get_env("SANITY_PROJECT_ID")
        api_token = get_env("SANITY_API_TOKEN")
        dataset = get_env("SANITY_DATASET", "production")
        api_version = get_env("SANITY_API_VERSION", "2024-01-01")
        return cls(
            SanityConfig(
                project_id=project_id.strip(),
                api_token=api_token.strip(),
                dataset=dataset.strip(),
                api_version=api_version.strip(),
            )
        )

    # -- internals ---------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _require_draft_id(doc_id: str) -> str:
        doc_id = (doc_id or "").strip()
        if not doc_id.startswith(DRAFT_PREFIX):
            raise SanityError(
                f"Refusing to write non-draft document id {doc_id!r}: Chad may only "
                f"create or update documents whose _id starts with {DRAFT_PREFIX!r}."
            )
        return doc_id

    # -- reads -------------------------------------------------------------

    def query(self, groq: str, params: dict[str, Any] | None = None) -> Any:
        """Run a read-only GROQ query against the dataset.

        https://www.sanity.io/docs/http-api -> /query/{dataset}?query=...
        """
        if not str(groq or "").strip():
            raise SanityError("query requires a non-empty GROQ string")
        query_params: dict[str, str] = {"query": groq}
        for key, value in (params or {}).items():
            query_params[f"${key}"] = value
        url = f"{self.config.base_url}/query/{self.config.dataset}"
        response = httpx.get(url, params=query_params, headers=self._headers, timeout=30)
        response.raise_for_status()
        return response.json().get("result")

    # -- draft-only writes -------------------------------------------------

    def _mutate(self, mutations: list[dict[str, Any]]) -> dict[str, Any]:
        url = f"{self.config.base_url}/mutate/{self.config.dataset}"
        response = httpx.post(
            url,
            params={"returnIds": "true"},
            json={"mutations": mutations},
            headers=self._headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_draft_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Create a draft document. The ``_id`` MUST carry the ``drafts.`` prefix."""
        if not isinstance(doc, dict):
            raise SanityError("create_draft_document requires a document dict")
        self._require_draft_id(str(doc.get("_id", "")))
        if not str(doc.get("_type", "")).strip():
            raise SanityError("create_draft_document requires a _type")
        return self._mutate([{"create": doc}])

    def patch_draft_document(self, draft_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Patch (set fields on) an existing draft document, draft id required."""
        draft_id = self._require_draft_id(draft_id)
        if not isinstance(fields, dict) or not fields:
            raise SanityError("patch_draft_document requires a non-empty fields dict")
        return self._mutate([{"patch": {"id": draft_id, "set": fields}}])
