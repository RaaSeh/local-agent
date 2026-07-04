from __future__ import annotations

import re
from typing import Any

from local_agent.integrations.sanity.client import DRAFT_PREFIX, SanityClient, SanityError

# --- Assumed document shapes (Phase 1) -------------------------------------
# The real Sanity schema is defined in a later phase. For now we assume:
#   tenant: _type "tenant"; fields: name, subdomain, brand (object)
#   page:   _type "page";   fields: title, slug (object {current}), body (text),
#           tenant (reference {_type:"reference", _ref}), seoTitle, seoDescription,
#           structuredData (object/JSON-LD)
# These are documented in README.md and intentionally kept simple.

# SEO heuristics used by seo_audit_tenant.
SEO_TITLE_MIN = 15
SEO_TITLE_MAX = 60
SEO_DESC_MIN = 50
SEO_DESC_MAX = 160
BODY_THIN_MIN = 300


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "untitled"


def _tenant_draft_id(subdomain_slug: str) -> str:
    return f"{DRAFT_PREFIX}tenant-{_slugify(subdomain_slug)}"


def _page_draft_id(tenant_ref: str, page_slug: str) -> str:
    tenant_part = _slugify(tenant_ref.replace(DRAFT_PREFIX, ""))
    return f"{DRAFT_PREFIX}page-{tenant_part}-{_slugify(page_slug)}"


def _client(client: SanityClient | None) -> SanityClient:
    return client if client is not None else SanityClient.from_env()


def provision_tenant(
    name: str,
    subdomain_slug: str,
    brand: dict[str, Any] | None = None,
    *,
    client: SanityClient | None = None,
) -> dict[str, Any]:
    """Create a DRAFT ``tenant`` document. Never publishes."""
    if not str(name or "").strip():
        raise SanityError("provision_tenant requires a name")
    if not str(subdomain_slug or "").strip():
        raise SanityError("provision_tenant requires a subdomain_slug")
    subdomain = _slugify(subdomain_slug)
    doc = {
        "_id": _tenant_draft_id(subdomain),
        "_type": "tenant",
        "name": str(name).strip(),
        "subdomain": subdomain,
        "brand": brand or {},
    }
    return _client(client).create_draft_document(doc)


def draft_tenant_page(
    tenant_ref: str,
    title: str,
    body: str,
    seo_title: str | None = None,
    seo_description: str | None = None,
    slug: str | None = None,
    structured_data: dict[str, Any] | None = None,
    *,
    client: SanityClient | None = None,
) -> dict[str, Any]:
    """Create a DRAFT ``page`` document referencing a tenant. Never publishes."""
    if not str(tenant_ref or "").strip():
        raise SanityError("draft_tenant_page requires a tenant_ref")
    if not str(title or "").strip():
        raise SanityError("draft_tenant_page requires a title")
    page_slug = _slugify(slug or title)
    doc: dict[str, Any] = {
        "_id": _page_draft_id(tenant_ref, page_slug),
        "_type": "page",
        "title": str(title).strip(),
        "slug": {"_type": "slug", "current": page_slug},
        "body": str(body or ""),
        "tenant": {"_type": "reference", "_ref": tenant_ref},
        "seoTitle": (seo_title or "").strip(),
        "seoDescription": (seo_description or "").strip(),
    }
    if structured_data is not None:
        doc["structuredData"] = structured_data
    return _client(client).create_draft_document(doc)


def update_tenant_page(
    draft_page_id: str,
    fields: dict[str, Any],
    *,
    client: SanityClient | None = None,
) -> dict[str, Any]:
    """Patch a DRAFT page document. The id must carry the ``drafts.`` prefix."""
    return _client(client).patch_draft_document(draft_page_id, fields)


def list_tenants(*, client: SanityClient | None = None) -> Any:
    """READ-ONLY: return all tenant documents via GROQ."""
    groq = '*[_type == "tenant"]{_id, name, subdomain, brand}'
    return _client(client).query(groq)


def list_tenant_pages(tenant_ref: str, *, client: SanityClient | None = None) -> Any:
    """READ-ONLY: return pages referencing the given tenant via GROQ."""
    groq = (
        '*[_type == "page" && tenant._ref == $tenant_ref]'
        "{_id, title, slug, seoTitle, seoDescription, structuredData, body}"
    )
    return _client(client).query(groq, params={"tenant_ref": tenant_ref})


def seo_audit_tenant(tenant_ref: str, *, client: SanityClient | None = None) -> dict[str, Any]:
    """READ-ONLY: audit a tenant's pages for SEO weaknesses.

    Flags missing/weak seoTitle, missing/over- or under-length seoDescription,
    thin body content, and missing structured data. Returns structured findings
    plus a human-readable summary. Performs no mutations.
    """
    pages = list_tenant_pages(tenant_ref, client=client) or []
    findings: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        issues: list[str] = []
        seo_title = str(page.get("seoTitle") or "").strip()
        seo_desc = str(page.get("seoDescription") or "").strip()
        body = str(page.get("body") or "").strip()

        if not seo_title:
            issues.append("missing seoTitle")
        elif len(seo_title) < SEO_TITLE_MIN:
            issues.append(f"weak seoTitle ({len(seo_title)} chars < {SEO_TITLE_MIN})")
        elif len(seo_title) > SEO_TITLE_MAX:
            issues.append(f"long seoTitle ({len(seo_title)} chars > {SEO_TITLE_MAX})")

        if not seo_desc:
            issues.append("missing seoDescription")
        elif len(seo_desc) < SEO_DESC_MIN:
            issues.append(f"short seoDescription ({len(seo_desc)} chars < {SEO_DESC_MIN})")
        elif len(seo_desc) > SEO_DESC_MAX:
            issues.append(f"long seoDescription ({len(seo_desc)} chars > {SEO_DESC_MAX})")

        if len(body) < BODY_THIN_MIN:
            issues.append(f"thin body ({len(body)} chars < {BODY_THIN_MIN})")

        if not page.get("structuredData"):
            issues.append("missing structuredData")

        findings.append(
            {
                "page_id": page.get("_id"),
                "title": page.get("title"),
                "issues": issues,
                "ok": not issues,
            }
        )

    pages_with_issues = [f for f in findings if not f["ok"]]
    summary_lines = [
        f"SEO audit for tenant {tenant_ref}: {len(findings)} page(s), "
        f"{len(pages_with_issues)} with issues.",
    ]
    for finding in pages_with_issues:
        summary_lines.append(
            f"- {finding.get('title') or finding.get('page_id')}: "
            + "; ".join(finding["issues"])
        )
    if not pages_with_issues:
        summary_lines.append("- No SEO issues detected.")

    return {
        "tenant_ref": tenant_ref,
        "page_count": len(findings),
        "pages_with_issues": len(pages_with_issues),
        "findings": findings,
        "summary": "\n".join(summary_lines),
    }
