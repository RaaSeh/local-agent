from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class GoogleLead:
    name: str
    address: str
    phone: str
    maps_uri: str
    website: str
    place_id: str
    types: list[str]
    query: str


def _community_queries() -> list[str]:
    communities = [
        "Nanaimo",
        "Ladysmith",
        "Chemainus",
        "Duncan",
        "North Cowichan",
        "Crofton",
        "Maple Bay",
        "Lake Cowichan",
        "Shawnigan Lake",
        "Mill Bay",
        "Parksville",
    ]
    intents = [
        "industrial yard",
        "fabrication shop",
        "marine repair",
        "equipment yard",
        "manufacturing facility",
        "storage yard",
    ]
    queries: list[str] = []
    for city in communities:
        for intent in intents:
            queries.append(f"{intent} {city} BC")
    return queries


def _search_google_places(api_key: str, max_queries: int = 12) -> list[GoogleLead]:
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.nationalPhoneNumber,places.googleMapsUri,places.websiteUri,places.types"
        ),
        "Content-Type": "application/json",
    }
    url = "https://places.googleapis.com/v1/places:searchText"

    seen: set[str] = set()
    leads: list[GoogleLead] = []

    for query in _community_queries()[:max_queries]:
        payload = {"textQuery": query, "pageSize": 5}
        r = httpx.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        for p in data.get("places", []):
            place_id = p.get("id", "").strip()
            if not place_id or place_id in seen:
                continue
            seen.add(place_id)
            leads.append(
                GoogleLead(
                    name=p.get("displayName", {}).get("text", "").strip(),
                    address=p.get("formattedAddress", "").strip(),
                    phone=p.get("nationalPhoneNumber", "").strip(),
                    maps_uri=p.get("googleMapsUri", "").strip(),
                    website=p.get("websiteUri", "").strip(),
                    place_id=place_id,
                    types=[t for t in p.get("types", []) if isinstance(t, str)],
                    query=query,
                )
            )
    return leads


def _registry_lookup(base_url: str, api_key: str, names: list[str]) -> dict[str, list[dict[str, str]]]:
    headers = {"Accept": "application/json"}
    # Generic header name to support common API key gateways.
    if api_key:
        headers["x-api-key"] = api_key

    out: dict[str, list[dict[str, str]]] = {}
    with httpx.Client(timeout=25) as client:
        for name in names[:25]:
            try:
                r = client.get(base_url, params={"q": name}, headers=headers)
                r.raise_for_status()
                data: Any = r.json()
            except Exception:
                out[name] = []
                continue

            rows: list[dict[str, str]] = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("results") or data.get("items") or data.get("data") or []
            else:
                items = []

            if not isinstance(items, list):
                items = []

            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                nm = str(item.get("name") or item.get("legalName") or item.get("identifier") or "").strip()
                status = str(item.get("status") or item.get("state") or item.get("businessStatus") or "").strip()
                if nm:
                    rows.append({"name": nm, "status": status})
            out[name] = rows
    return out


def build_external_research_context() -> str:
    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    bc_url = os.getenv("BC_REGISTRY_API_URL", "").strip()
    bc_key = os.getenv("BC_REGISTRY_API_KEY", "").strip()

    if not google_key:
        raise RuntimeError("Missing required environment variable: GOOGLE_MAPS_API_KEY")

    max_queries = int(os.getenv("LEAD_RESEARCH_MAX_QUERIES", "12"))
    max_queries = max(1, min(max_queries, 30))

    google_error = ""
    try:
        google_leads = _search_google_places(google_key, max_queries=max_queries)
    except Exception as ex:
        google_leads = []
        google_error = str(ex)
    names = [g.name for g in google_leads if g.name]
    bc_enabled = bool(bc_url and bc_key)
    bc_hits = _registry_lookup(bc_url, bc_key, names) if bc_enabled else {}

    lines: list[str] = []
    lines.append("External Verified Research Inputs")
    lines.append("Use the entries below instead of inventing contacts.")
    if google_error:
        lines.append(f"Google Places lookup error: {google_error}")
        lines.append("No Google candidates were retrieved for this run. Use only verified existing records and move uncertain leads to backlog.")
    if not bc_enabled:
        lines.append("BC registry enrichment: unavailable (missing BC_REGISTRY_API_URL or BC_REGISTRY_API_KEY).")
    lines.append("")
    lines.append("Google Places candidates:")

    for idx, lead in enumerate(google_leads[:40], start=1):
        if not lead.name or not lead.maps_uri:
            continue
        lines.append(f"{idx}. name={lead.name}")
        lines.append(f"   address={lead.address or 'Unknown'}")
        lines.append(f"   phone={lead.phone or 'Not found'}")
        lines.append(f"   maps_uri={lead.maps_uri}")
        lines.append(f"   website={lead.website or 'Not found'}")
        lines.append(f"   source_query={lead.query}")
        if lead.types:
            lines.append(f"   types={', '.join(lead.types[:6])}")
        if bc_enabled:
            hits = bc_hits.get(lead.name, [])
            if hits:
                lines.append("   bc_registry_matches:")
                for h in hits[:3]:
                    lines.append(f"   - {h.get('name', '')} (status={h.get('status', 'unknown')})")
            else:
                lines.append("   bc_registry_matches: none returned")
    lines.append("")
    lines.append("Important:")
    lines.append("- Candidate Call List may only include entries that have a direct phone number and maps_uri from this research.")
    lines.append("- Anything else goes to Research Backlog.")

    return "\n".join(lines)