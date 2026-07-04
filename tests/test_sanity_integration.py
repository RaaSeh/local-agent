from __future__ import annotations

import inspect

import httpx
import pytest

from local_agent.integrations.sanity import DRAFT_PREFIX, SanityClient, SanityConfig, SanityError
from local_agent.integrations.sanity import client as sanity_client
from local_agent.integrations.sanity import tools as sanity_tools
from local_agent.orchestration.registry import TaskRegistry


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("POST", "http://localhost")

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=httpx.Response(self.status_code)
            )


def _make_client() -> SanityClient:
    return SanityClient(
        SanityConfig(project_id="proj123", api_token="tok", dataset="production", api_version="2024-01-01")
    )


# -- draft-only write model -------------------------------------------------


def test_created_ids_always_carry_drafts_prefix(monkeypatch):
    captured: list[dict] = []

    def fake_post(url, params, json, headers, timeout):
        captured.append({"url": url, "json": json})
        return _FakeResponse(200, {"results": [{"id": json["mutations"][0]["create"]["_id"]}]})

    monkeypatch.setattr(sanity_client.httpx, "post", fake_post)
    client = _make_client()

    sanity_tools.provision_tenant("Diving Co", "diving", client=client)
    sanity_tools.draft_tenant_page(
        tenant_ref=f"{DRAFT_PREFIX}tenant-diving",
        title="Hull Cleaning",
        body="x" * 400,
        client=client,
    )

    assert captured, "expected mutate calls"
    for call in captured:
        created = call["json"]["mutations"][0]["create"]
        assert created["_id"].startswith(DRAFT_PREFIX)


def test_create_draft_rejects_non_draft_id():
    client = _make_client()
    with pytest.raises(SanityError):
        client.create_draft_document({"_id": "tenant-published", "_type": "tenant"})


def test_patch_draft_rejects_non_draft_id():
    client = _make_client()
    with pytest.raises(SanityError):
        client.patch_draft_document("page-published", {"title": "x"})


def test_update_tenant_page_patches_draft(monkeypatch):
    captured: list[dict] = []

    def fake_post(url, params, json, headers, timeout):
        captured.append(json)
        return _FakeResponse(200, {"results": []})

    monkeypatch.setattr(sanity_client.httpx, "post", fake_post)
    client = _make_client()

    sanity_tools.update_tenant_page(
        f"{DRAFT_PREFIX}page-diving-hull", {"seoTitle": "Better"}, client=client
    )

    mutation = captured[0]["mutations"][0]
    assert "patch" in mutation
    assert mutation["patch"]["id"].startswith(DRAFT_PREFIX)


# -- no publish/promote method exists --------------------------------------


def test_client_has_no_publish_method():
    forbidden = {"publish", "promote", "unpublish", "publish_document", "promote_draft"}
    methods = {name for name, _ in inspect.getmembers(SanityClient, predicate=inspect.isfunction)}
    assert forbidden.isdisjoint(methods), f"client must not expose publish/promote: {methods & forbidden}"
    tool_names = {name for name, _ in inspect.getmembers(sanity_tools, inspect.isfunction)}
    assert forbidden.isdisjoint(tool_names)


# -- read-only tools issue GROQ queries and never mutate --------------------


def test_list_tenants_uses_query_endpoint_and_never_mutates(monkeypatch):
    get_calls: list[dict] = []

    def fake_get(url, params, headers, timeout):
        get_calls.append({"url": url, "params": params})
        return _FakeResponse(200, {"result": [{"_id": "drafts.tenant-diving", "name": "Diving Co"}]})

    def fail_post(*args, **kwargs):
        raise AssertionError("read-only tool must not POST/mutate")

    monkeypatch.setattr(sanity_client.httpx, "get", fake_get)
    monkeypatch.setattr(sanity_client.httpx, "post", fail_post)
    client = _make_client()

    result = sanity_tools.list_tenants(client=client)

    assert result[0]["name"] == "Diving Co"
    assert "/query/production" in get_calls[0]["url"]
    assert '_type == "tenant"' in get_calls[0]["params"]["query"]


def test_seo_audit_flags_weaknesses(monkeypatch):
    pages = [
        {
            "_id": "drafts.page-diving-good",
            "title": "Good",
            "seoTitle": "Professional Hull Cleaning Services",
            "seoDescription": "We provide professional diving and hull cleaning for yachts across the coast region.",
            "body": "y" * 400,
            "structuredData": {"@type": "Service"},
        },
        {
            "_id": "drafts.page-diving-bad",
            "title": "Bad",
            "seoTitle": "",
            "seoDescription": "short",
            "body": "thin",
        },
    ]

    def fake_get(url, params, headers, timeout):
        return _FakeResponse(200, {"result": pages})

    monkeypatch.setattr(sanity_client.httpx, "get", fake_get)
    client = _make_client()

    report = sanity_tools.seo_audit_tenant("drafts.tenant-diving", client=client)

    assert report["page_count"] == 2
    assert report["pages_with_issues"] == 1
    bad = next(f for f in report["findings"] if f["title"] == "Bad")
    assert "missing seoTitle" in bad["issues"]
    assert any("seoDescription" in issue for issue in bad["issues"])
    assert any("thin body" in issue for issue in bad["issues"])
    assert "missing structuredData" in bad["issues"]


# -- config / from_env ------------------------------------------------------


def test_from_env_requires_project_id(monkeypatch):
    monkeypatch.delenv("SANITY_PROJECT_ID", raising=False)
    monkeypatch.delenv("SANITY_API_TOKEN", raising=False)
    monkeypatch.setattr(sanity_client, "load_env", lambda: None)
    with pytest.raises(RuntimeError, match="SANITY_PROJECT_ID"):
        SanityClient.from_env()


def test_from_env_builds_config(monkeypatch):
    monkeypatch.setattr(sanity_client, "load_env", lambda: None)
    monkeypatch.setenv("SANITY_PROJECT_ID", "abc")
    monkeypatch.setenv("SANITY_API_TOKEN", "secret")
    monkeypatch.delenv("SANITY_DATASET", raising=False)
    monkeypatch.delenv("SANITY_API_VERSION", raising=False)

    client = SanityClient.from_env()

    assert client.config.project_id == "abc"
    assert client.config.dataset == "production"
    assert client.config.api_version == "2024-01-01"
    assert client.config.base_url == "https://abc.api.sanity.io/v2024-01-01/data"


# -- static registry --------------------------------------------------------


def test_registry_exposes_cms_tools():
    registry = TaskRegistry()
    for name in (
        "provision_tenant",
        "draft_tenant_page",
        "update_tenant_page",
        "list_tenants",
        "list_tenant_pages",
        "seo_audit_tenant",
    ):
        assert name in registry.tools

    manifest = registry.render_tool_manifest("cms_provisioning")
    assert "provision_tenant" in manifest
    assert registry.allowed_tools_for("cms_provisioning")
