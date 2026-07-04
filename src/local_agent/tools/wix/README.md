# Wix SEO / Content Integration — Phase 1

Phase 1 of Chad's Wix integration: a **read-only SEO audit** of existing blog
content plus **draft-only** blog authoring. Chad never publishes — a human
reviews and publishes manually from the Wix dashboard.

## Configuration

The Wix integration uses the **same environment-variable mechanism as every
other integration in this repo** (`local_agent.config` / `os.getenv`, loaded
from `.env`). No new config mechanism is introduced.

Add these to your `.env` (see `.env.example`):

```
# Account-owner Wix API key
WIX_API_KEY=your_wix_account_api_key
# Site id for site-scoped blog/CMS calls (sent as the wix-site-id header)
WIX_SITE_ID=your_wix_site_id
```

- `WIX_API_KEY` — an **account-owner API key**. Sent as the `Authorization`
  header on every request.
- `WIX_SITE_ID` — required for site-scoped blog/CMS calls; sent as the
  `wix-site-id` header so the account-level key resolves to a specific site.

The client is built with `WixClient.from_env()`, which raises
`WixConfigError` (a `RuntimeError`) if `WIX_API_KEY` is missing — matching the
repo's "Missing required environment variable" convention.

## Phase 1 scope

- **SEO audit (read-only)** — `wix_seo_audit`: lists blog posts
  (`GET /blog/v3/posts`) and flags missing/weak SEO titles, missing /
  over-long / under-long meta descriptions, thin content, and missing slugs /
  structured data. Returns structured data plus a human-readable summary.
- **Create draft post** — `wix_create_draft_post`: `POST /blog/v3/draft-posts`.
  For 3rd-party apps Wix requires a `memberId` on the draft; use
  `WixClient.list_site_members()` to resolve one.
- **Update draft post** — `wix_update_draft_post`:
  `PATCH /blog/v3/draft-posts/{id}`.
- **List categories** — `WixClient.list_categories()` (`GET /blog/v3/categories`).

These are registered in the static `TaskRegistry` under the `wix_seo` task type
and surface in the rendered tool manifest alongside the workspace tools.

## Draft-only / no-publish design (intentional)

This client implements **NO publish endpoint**. There is deliberately no method
that calls `POST /blog/v3/draft-posts/{id}/publish`. The approval gate is that
Chad only ever creates/updates **drafts**; publishing is a manual human step in
the Wix dashboard. A test (`test_wix_client_has_no_publish_method`,
`test_wix_client_source_calls_no_publish_endpoint`) enforces this.

Wix docs:

- Create Draft Post (used):
  https://dev.wix.com/docs/api-reference/business-solutions/blog/draft-posts/create-draft-post
- Publish Draft Post (**intentionally NOT used**):
  https://dev.wix.com/docs/api-reference/business-solutions/blog/draft-posts/publish-draft-post

## Out of scope (Phase 2)

Page-level meta tags and structured data (schema.org / JSON-LD) management for
non-blog site pages are **out of scope** for Phase 1.
