from __future__ import annotations

import os
from typing import Any

import httpx

WIX_API_BASE = "https://www.wixapis.com"


class WixConfigError(RuntimeError):
    """Raised when required Wix configuration is missing."""


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise WixConfigError(f"Missing required environment variable: {name}")
    return value


class WixClient:
    """Thin Wix REST API client for Phase 1 SEO/content work.

    Auth uses an account-owner Wix API key, loaded from the same environment
    mechanism as every other integration in this repo (see ``local_agent.config``
    and ``leads.sources``). Site-scoped calls additionally send a ``wix-site-id``
    header so the account-level key resolves to a specific site.

    DRAFT-ONLY BY DESIGN — DO NOT ADD A PUBLISH METHOD.
    -------------------------------------------------------------------
    This client intentionally implements NO publish endpoint. There is no
    method that calls ``POST /blog/v3/draft-posts/{id}/publish``. Chad only ever
    creates and updates DRAFTS; a human reviews and publishes manually from the
    Wix dashboard. This is the human-in-the-loop approval gate for content.

    Wix docs:
      - Create Draft Post (used):
        https://dev.wix.com/docs/api-reference/business-solutions/blog/draft-posts/create-draft-post
      - Publish Draft Post (intentionally NOT used):
        https://dev.wix.com/docs/api-reference/business-solutions/blog/draft-posts/publish-draft-post
    """

    def __init__(
        self,
        api_key: str | None = None,
        site_id: str | None = None,
        base_url: str = WIX_API_BASE,
        timeout: float = 30.0,
    ):
        self.api_key = (api_key if api_key is not None else _require_env("WIX_API_KEY")).strip()
        # Site id is required for site-scoped blog/CMS calls but kept optional at
        # construction so account-level helpers stay usable without it.
        self.site_id = (site_id if site_id is not None else (os.getenv("WIX_SITE_ID") or "")).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls, base_url: str = WIX_API_BASE, timeout: float = 30.0) -> "WixClient":
        """Build a client from environment variables (the repo's config pattern)."""
        return cls(
            api_key=_require_env("WIX_API_KEY"),
            site_id=(os.getenv("WIX_SITE_ID") or "").strip() or None,
            base_url=base_url,
            timeout=timeout,
        )

    # -- internal helpers -------------------------------------------------

    def _headers(self, site_scoped: bool = True) -> dict[str, str]:
        if not self.api_key:
            raise WixConfigError("Missing WIX_API_KEY")
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        if site_scoped:
            if not self.site_id:
                raise WixConfigError("Missing required environment variable: WIX_SITE_ID")
            headers["wix-site-id"] = self.site_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        site_scoped: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = httpx.request(
            method,
            url,
            headers=self._headers(site_scoped=site_scoped),
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    # -- read: SEO audit inputs ------------------------------------------

    def list_blog_posts(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List published blog posts with SEO-relevant fields.

        GET https://www.wixapis.com/blog/v3/posts
        """
        params = {
            "paging.limit": max(1, min(int(limit), 100)),
            "paging.offset": max(0, int(offset)),
            # Ask Wix to include SEO and rich-content fields used by the audit.
            "fieldsets": ["SEO", "RICH_CONTENT", "URL"],
        }
        data = self._request("GET", "/blog/v3/posts", params=params)
        posts = data.get("posts")
        return posts if isinstance(posts, list) else []

    def list_categories(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List blog categories.

        GET https://www.wixapis.com/blog/v3/categories
        """
        params = {
            "paging.limit": max(1, min(int(limit), 100)),
            "paging.offset": max(0, int(offset)),
        }
        data = self._request("GET", "/blog/v3/categories", params=params)
        categories = data.get("categories")
        return categories if isinstance(categories, list) else []

    def list_site_members(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List site members to help resolve a ``memberId`` for draft authorship.

        GET https://www.wixapis.com/members/v1/members
        For 3rd-party apps, Create Draft Post requires a ``memberId`` — this
        helper exists so callers can resolve one before creating a draft.
        """
        params = {
            "paging.limit": max(1, min(int(limit), 100)),
            "paging.offset": max(0, int(offset)),
        }
        data = self._request("GET", "/members/v1/members", params=params)
        members = data.get("members")
        return members if isinstance(members, list) else []

    # -- write: DRAFTS ONLY ----------------------------------------------

    def create_draft_post(self, draft_post: dict[str, Any]) -> dict[str, Any]:
        """Create a blog Draft Post.

        POST https://www.wixapis.com/blog/v3/draft-posts

        Note: for 3rd-party apps Wix requires ``memberId`` on the draft post.
        Use ``list_site_members`` to resolve one. This creates a DRAFT only —
        it is never published. See the no-publish design note on this class.
        """
        if not isinstance(draft_post, dict) or not draft_post:
            raise ValueError("create_draft_post requires a non-empty draft_post payload")
        return self._request("POST", "/blog/v3/draft-posts", json_body={"draftPost": draft_post})

    def update_draft_post(self, draft_post_id: str, draft_post: dict[str, Any]) -> dict[str, Any]:
        """Update an existing blog Draft Post.

        PATCH https://www.wixapis.com/blog/v3/draft-posts/{id}

        Updates a DRAFT in place; it remains a draft and is never published.
        """
        draft_post_id = (draft_post_id or "").strip()
        if not draft_post_id:
            raise ValueError("update_draft_post requires draft_post_id")
        if not isinstance(draft_post, dict) or not draft_post:
            raise ValueError("update_draft_post requires a non-empty draft_post payload")
        return self._request(
            "PATCH",
            f"/blog/v3/draft-posts/{draft_post_id}",
            json_body={"draftPost": draft_post},
        )

    # -- intentionally NOT implemented -----------------------------------
    #
    # publish_draft_post(...) is DELIBERATELY OMITTED.
    #
    # The Wix "Publish Draft Post" endpoint
    # (POST /blog/v3/draft-posts/{id}/publish) is intentionally NOT wrapped by
    # this client. Publishing is the human approval gate: a person reviews the
    # draft and publishes it manually from the Wix dashboard. Do not add a
    # publish method here.
    # https://dev.wix.com/docs/api-reference/business-solutions/blog/draft-posts/publish-draft-post
