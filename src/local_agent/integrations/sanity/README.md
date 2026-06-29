# Sanity multi-tenant integration (Phase 1)

Chad tooling to provision **tenants** and draft **pages/SEO** in
[Sanity](https://www.sanity.io/) (a headless CMS) for the multi-tenant web
platform. Each service business (e.g. diving/yacht cleaning, canvas
maintenance) is a *tenant* on its own subdomain.

## Scope (Phase 1)

This phase adds **only the Chad-side tooling**:

- A minimal Sanity HTTP API client (`client.py`).
- Registry-driven tools (`tools.py`) wired into the static tool registry.
- Tests with all HTTP mocked.

**Out of scope (later phases):** the Next.js/Vercel frontend and the real
Sanity schema (the document shapes here are documented assumptions). This branch
is **independent of the open Wix Phase 1 PR** and works standalone on `main`.

## Draft-only / no-publish design (the human approval gate)

**Chad may only ever create or update DRAFTS — it can never publish.**

In Sanity, a document whose `_id` starts with `drafts.` is an unpublished draft:
it is invisible to the public/published dataset and the CDN, and only appears in
Sanity Studio for a human to review. Publishing means writing/replacing the
document **without** the `drafts.` prefix (or deleting the `drafts.*` copy while
creating the bare-id copy).

How this is enforced in code:

- Every write goes through `SanityClient.create_draft_document` /
  `patch_draft_document`, which call `_require_draft_id` and **raise
  `SanityError`** if the `_id` does not start with `drafts.`.
- The client implements **no** publish / promote / unpublish method — this
  omission is intentional and documented at the top of `client.py`. A test
  (`test_client_has_no_publish_method`) asserts no such method ever appears.

Publishing is performed by the human operator in Sanity Studio.

## Configuration (env vars)

Loaded via the repo's standard `local_agent.config` mechanism
(`load_env()` + `get_env()`), the same pattern used by the other integrations.
Set these in your `.env` (see `.env.example`):

| Variable             | Required | Default        | Notes                                  |
| -------------------- | -------- | -------------- | -------------------------------------- |
| `SANITY_PROJECT_ID`  | yes      | —              | Sanity project id                      |
| `SANITY_API_TOKEN`   | yes      | —              | Token with write access (Editor)       |
| `SANITY_DATASET`     | no       | `production`   | Dataset name                           |
| `SANITY_API_VERSION` | no       | `2024-01-01`   | Sanity API date version                |

Missing required vars raise the repo-standard
`RuntimeError("Missing required environment variable: ...")` via `get_env`.

API base URL: `https://{PROJECT_ID}.api.sanity.io/v{API_VERSION}/data`
- Query (read): `GET /query/{dataset}?query=<GROQ>`
- Mutate (write): `POST /mutate/{dataset}` with `{"mutations": [...]}`
- Auth header: `Authorization: Bearer {SANITY_API_TOKEN}`

## Assumed document schemas (Phase 1)

The real schema is defined in a later phase. For now we assume:

- **tenant** — `_type: "tenant"`; fields: `name`, `subdomain`, `brand` (object)
- **page** — `_type: "page"`; fields: `title`, `slug` (`{_type:"slug", current}`),
  `body` (text), `tenant` (`{_type:"reference", _ref}`), `seoTitle`,
  `seoDescription`, `structuredData` (object / JSON-LD)

## Tools

Draft-writing (draft-only):
- `provision_tenant(name, subdomain_slug, brand)` → creates a draft `tenant`.
- `draft_tenant_page(tenant_ref, title, body, seo_title, seo_description, slug, structured_data)`
  → creates a draft `page` referencing the tenant.
- `update_tenant_page(draft_page_id, fields)` → patches a draft page.

Read-only (GROQ):
- `list_tenants()`
- `list_tenant_pages(tenant_ref)`
- `seo_audit_tenant(tenant_ref)` → returns structured findings + a human-readable
  summary flagging missing/weak `seoTitle`, missing/over- or under-length
  `seoDescription`, thin `body`, and missing `structuredData`.

## References

- Sanity HTTP API: https://www.sanity.io/docs/http-api
- Sanity Agent Actions: https://www.sanity.io/docs/agent-actions
