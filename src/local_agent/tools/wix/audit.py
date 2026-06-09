from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# SEO heuristics. These are read-only checks; nothing here mutates Wix content.
TITLE_MIN_LEN = 15
TITLE_MAX_LEN = 60
META_MIN_LEN = 70
META_MAX_LEN = 160
THIN_CONTENT_MIN_CHARS = 300


@dataclass
class SeoFinding:
    post_id: str
    title: str
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class SeoAuditReport:
    total_posts: int = 0
    posts_with_issues: int = 0
    findings: list[SeoFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_posts": self.total_posts,
            "posts_with_issues": self.posts_with_issues,
            "findings": [
                {"post_id": f.post_id, "title": f.title, "issues": list(f.issues)}
                for f in self.findings
            ],
        }

    def summary(self) -> str:
        lines = [
            "Wix Blog SEO Audit (read-only)",
            f"Posts scanned: {self.total_posts}",
            f"Posts with issues: {self.posts_with_issues}",
            "",
        ]
        if not self.findings:
            lines.append("No posts found to audit.")
            return "\n".join(lines)

        clean = [f for f in self.findings if f.ok]
        flagged = [f for f in self.findings if not f.ok]

        if flagged:
            lines.append("Flagged posts:")
            for finding in flagged:
                title = finding.title or "(untitled)"
                lines.append(f"- {title} [{finding.post_id}]")
                for issue in finding.issues:
                    lines.append(f"    - {issue}")
        if clean:
            lines.append("")
            lines.append(f"{len(clean)} post(s) passed all checks.")
        return "\n".join(lines)


def _text_len(value: Any) -> int:
    return len(str(value).strip()) if value else 0


def _extract_plain_text(rich_content: Any) -> str:
    """Best-effort plain-text extraction from a Wix RICH_CONTENT node tree."""
    if not isinstance(rich_content, dict):
        return ""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rich_content.get("nodes", rich_content))
    return " ".join(p for p in parts if p).strip()


def _audit_single_post(post: dict[str, Any]) -> SeoFinding:
    post_id = str(post.get("id") or post.get("_id") or "").strip()
    title = str(post.get("title") or "").strip()
    seo = post.get("seoData") or post.get("seo") or {}
    if not isinstance(seo, dict):
        seo = {}

    issues: list[str] = []

    # SEO title: prefer explicit seoData tag, fall back to post title.
    seo_title = _seo_title(seo) or title
    title_len = _text_len(seo_title)
    if title_len == 0:
        issues.append("Missing SEO title.")
    elif title_len < TITLE_MIN_LEN:
        issues.append(f"SEO title too short ({title_len} chars; aim for >= {TITLE_MIN_LEN}).")
    elif title_len > TITLE_MAX_LEN:
        issues.append(f"SEO title too long ({title_len} chars; aim for <= {TITLE_MAX_LEN}).")

    # Meta description.
    meta = _seo_description(seo) or str(post.get("excerpt") or "").strip()
    meta_len = _text_len(meta)
    if meta_len == 0:
        issues.append("Missing meta description.")
    elif meta_len < META_MIN_LEN:
        issues.append(f"Meta description too short ({meta_len} chars; aim for >= {META_MIN_LEN}).")
    elif meta_len > META_MAX_LEN:
        issues.append(f"Meta description too long ({meta_len} chars; aim for <= {META_MAX_LEN}).")

    # Slug.
    slug = str(post.get("slug") or "").strip()
    if not slug:
        issues.append("Missing URL slug.")

    # Thin content.
    body = _extract_plain_text(post.get("richContent"))
    if not body:
        body = str(post.get("contentText") or "").strip()
    content_len = len(body)
    if content_len < THIN_CONTENT_MIN_CHARS:
        issues.append(
            f"Thin content ({content_len} chars; aim for >= {THIN_CONTENT_MIN_CHARS})."
        )

    # Structured data.
    if not _has_structured_data(seo):
        issues.append("Missing structured data (schema.org / JSON-LD) in SEO settings.")

    return SeoFinding(post_id=post_id, title=title, issues=issues)


def _seo_tags(seo: dict[str, Any]) -> list[dict[str, Any]]:
    tags = seo.get("tags")
    return [t for t in tags if isinstance(t, dict)] if isinstance(tags, list) else []


def _seo_title(seo: dict[str, Any]) -> str:
    for tag in _seo_tags(seo):
        if tag.get("type") == "title":
            return str(tag.get("children") or "").strip()
    return ""


def _seo_description(seo: dict[str, Any]) -> str:
    for tag in _seo_tags(seo):
        if tag.get("type") == "meta":
            props = tag.get("props") or {}
            if isinstance(props, dict) and props.get("name") == "description":
                return str(props.get("content") or "").strip()
    return ""


def _has_structured_data(seo: dict[str, Any]) -> bool:
    for tag in _seo_tags(seo):
        if tag.get("type") == "script":
            props = tag.get("props") or {}
            if isinstance(props, dict) and "ld+json" in str(props.get("type", "")).lower():
                return True
        if tag.get("type") == "structuredData":
            return True
    return False


def audit_blog_posts(posts: list[dict[str, Any]]) -> SeoAuditReport:
    """Run the read-only SEO audit over fetched Wix blog posts."""
    findings = [_audit_single_post(post) for post in (posts or []) if isinstance(post, dict)]
    return SeoAuditReport(
        total_posts=len(findings),
        posts_with_issues=sum(1 for f in findings if not f.ok),
        findings=findings,
    )
