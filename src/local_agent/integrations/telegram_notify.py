from __future__ import annotations

import os

import httpx


def _resolve_chat_ids() -> list[int]:
    raw = os.getenv("TELEGRAM_STATUS_CHAT_IDS", "").strip() or os.getenv(
        "TELEGRAM_ALLOWED_CHAT_IDS", ""
    ).strip()
    chat_ids: list[int] = []
    for value in raw.split(","):
        cleaned = value.strip()
        if not cleaned:
            continue
        try:
            chat_ids.append(int(cleaned))
        except ValueError:
            continue
    return chat_ids


def send_status_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = _resolve_chat_ids()
    if not token or not chat_ids or not text.strip():
        return False

    base_url = f"https://api.telegram.org/bot{token}"
    with httpx.Client(timeout=20) as client:
        for chat_id in chat_ids:
            response = client.post(
                f"{base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3500]},
            )
            response.raise_for_status()
    return True