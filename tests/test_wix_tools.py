from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from local_agent.orchestration.registry import TaskRegistry
from local_agent.orchestration.tools import ToolExecutor
from local_agent.tools.wix import WixClient, WixConfigError, audit_blog_posts
from local_agent.tools.wix import client as wix_client_module


def _json_response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(payload).encode("utf-8")
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _patch_request(payload: dict):
    """Patch httpx.request inside the wix client to return a canned payload."""
    return patch.object(
        wix_client_module.httpx,
        "request",
        return_value=_json_response(payload),
    )


# -- config / auth ----------------------------------------------------------


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("WIX_API_KEY", raising=False)
    with pytest.raises(WixConfigError):
        WixClient.from_env()


def test_site_scoped_call_requires_site_id():
    client = WixClient(api_key="k", site_id="")
    with pytest.raises(WixConfigError):
        client.list_blog_posts()


def test_headers_include_api_key_and_site_id():
    client = WixClient(api_key="secret-key", site_id="site-123")
    with _patch_request({"posts": []}) as mock_req:
        client.list_blog_posts(limit=10)
    _, kwargs = mock_req.call_args
    headers = kwargs["headers"]
    assert headers["Authorization"] == "secret-key"
    assert headers["wix-site-id"] == "site-123"


# -- read methods -----------------------------------------------------------


def test_list_blog_posts_returns_posts():
    client = WixClient(api_key="k", site_id="s")
    with _patch_request({"posts": [{"id": "1"}, {"id": "2"}]}) as mock_req:
        posts = client.list_blog_posts()
    assert [p["id"] for p in posts] == ["1", "2"]
    args, kwargs = mock_req.call_args
    assert args[0] == "GET"
    assert args[1].endswith("/blog/v3/posts")


def test_list_categories_hits_categories_endpoint():
    client = WixClient(api_key="k", site_id="s")
    with _patch_request({"categories": [{"id": "c1"}]}) as mock_req:
        cats = client.list_categories()
    assert cats == [{"id": "c1"}]
    assert mock_req.call_args.args[1].endswith("/blog/v3/categories")


def test_list_site_members_helps_resolve_member_id():
    client = WixClient(api_key="k", site_id="s")
    with _patch_request({"members": [{"id": "m1"}]}) as mock_req:
        members = client.list_site_members()
    assert members == [{"id": "m1"}]
    assert mock_req.call_args.args[1].endswith("/members/v1/members")


# -- draft write methods ----------------------------------------------------


def test_create_draft_post_posts_to_draft_endpoint():
    client = WixClient(api_key="k", site_id="s")
    with _patch_request({"draftPost": {"id": "d1"}}) as mock_req:
        result = client.create_draft_post({"title": "Hello", "memberId": "m1"})
    args, kwargs = mock_req.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/blog/v3/draft-posts")
    assert kwargs["json"] == {"draftPost": {"title": "Hello", "memberId": "m1"}}
    assert result["draftPost"]["id"] == "d1"


def test_update_draft_post_patches_by_id():
    client = WixClient(api_key="k", site_id="s")
    with _patch_request({"draftPost": {"id": "d1"}}) as mock_req:
        client.update_draft_post("d1", {"title": "Updated"})
    args, kwargs = mock_req.call_args
    assert args[0] == "PATCH"
    assert args[1].endswith("/blog/v3/draft-posts/d1")


def test_create_draft_post_validates_payload():
    client = WixClient(api_key="k", site_id="s")
    with pytest.raises(ValueError):
        client.create_draft_post({})


# -- CRITICAL: no publish endpoint -----------------------------------------


def test_wix_client_has_no_publish_method():
    """The client must never expose a publish capability (draft-only design)."""
    client = WixClient(api_key="k", site_id="s")
    assert not hasattr(client, "publish_draft_post")
    public = [name for name in dir(client) if not name.startswith("_")]
    assert not any("publish" in name.lower() for name in public)


def test_wix_client_source_calls_no_publish_endpoint():
    """No executable code in the client should reference the publish REST path.

    The publish path may appear in docstrings/comments documenting the
    intentional omission, but must never appear in an actual string literal
    used at runtime.
    """
    import ast
    import inspect

    source = inspect.getsource(wix_client_module)
    tree = ast.parse(source)

    # Collect docstring nodes so we can exclude them from the check.
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            assert "/publish" not in node.value


# -- SEO audit (read-only) --------------------------------------------------


def test_audit_flags_missing_and_weak_fields():
    posts = [
        {"id": "1", "title": "x", "slug": "", "excerpt": "", "seoData": {}},
    ]
    report = audit_blog_posts(posts)
    assert report.total_posts == 1
    assert report.posts_with_issues == 1
    issues = report.findings[0].issues
    joined = " ".join(issues).lower()
    assert "seo title too short" in joined
    assert "meta description" in joined
    assert "slug" in joined
    assert "thin content" in joined
    assert "structured data" in joined


def test_audit_passes_well_formed_post():
    seo = {
        "tags": [
            {"type": "title", "children": "A Solid SEO Title For Testing Length"},
            {
                "type": "meta",
                "props": {
                    "name": "description",
                    "content": (
                        "This is a meta description that is comfortably within the "
                        "recommended length window for search engine result pages."
                    ),
                },
            },
            {"type": "structuredData"},
        ]
    }
    post = {
        "id": "1",
        "title": "Title",
        "slug": "a-solid-post",
        "seoData": seo,
        "richContent": {"nodes": [{"text": "x" * 500}]},
    }
    report = audit_blog_posts([post])
    assert report.posts_with_issues == 0
    assert report.findings[0].ok


def test_audit_summary_is_human_readable():
    report = audit_blog_posts([{"id": "1", "title": "T", "seoData": {}}])
    summary = report.summary()
    assert "Wix Blog SEO Audit" in summary
    assert "Posts scanned: 1" in summary


# -- registry / tool wiring -------------------------------------------------


def test_registry_routes_wix_requests():
    registry = TaskRegistry()
    route = registry.route_for("run a wix seo audit on my blog")
    assert route.task_type == "wix_seo"
    assert "wix_seo_audit" in route.allowed_tools
    assert route.requires_confirmation is True


def test_wix_tools_render_in_manifest():
    registry = TaskRegistry()
    manifest = registry.render_tool_manifest("wix_seo")
    assert "wix_seo_audit" in manifest
    assert "wix_create_draft_post" in manifest
    assert "wix_update_draft_post" in manifest


def test_executor_runs_wix_seo_audit(monkeypatch):
    monkeypatch.setenv("WIX_API_KEY", "k")
    monkeypatch.setenv("WIX_SITE_ID", "s")
    executor = ToolExecutor(".")
    with _patch_request({"posts": [{"id": "1", "title": "x", "seoData": {}}]}):
        results = executor.execute([{"tool": "wix_seo_audit", "limit": 5}])
    assert results[0]["ok"] is True
    payload = json.loads(results[0]["output"])
    assert payload["report"]["total_posts"] == 1
    assert "summary" in payload


def test_executor_create_draft_accepts_json_string(monkeypatch):
    monkeypatch.setenv("WIX_API_KEY", "k")
    monkeypatch.setenv("WIX_SITE_ID", "s")
    executor = ToolExecutor(".")
    draft = json.dumps({"title": "Hello", "memberId": "m1"})
    with _patch_request({"draftPost": {"id": "d1"}}):
        results = executor.execute([{"tool": "wix_create_draft_post", "draft_post": draft}])
    assert results[0]["ok"] is True
    assert json.loads(results[0]["output"])["draftPost"]["id"] == "d1"


def test_raise_for_status_propagates(monkeypatch):
    client = WixClient(api_key="k", site_id="s")
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock()
    )
    with patch.object(wix_client_module.httpx, "request", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            client.list_blog_posts()
