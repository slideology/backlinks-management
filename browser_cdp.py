from __future__ import annotations

from urllib.parse import urlparse


DEFAULT_CDP_URL = "http://127.0.0.1:9222"


def normalize_cdp_url(url: str) -> str:
    text = str(url or "").strip() or DEFAULT_CDP_URL
    parsed = urlparse(text)
    scheme = parsed.scheme or "http"
    hostname = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9222
    return f"{scheme}://{hostname}:{port}"


def merge_browser_config(config: dict | None = None) -> dict:
    payload = dict(config or {})
    merged = {
        "connect_cdp_url": DEFAULT_CDP_URL,
        "allow_only_cdp_url": DEFAULT_CDP_URL,
        "bring_to_front": False,
        "require_cdp": True,
    }
    merged.update(payload)
    merged["connect_cdp_url"] = normalize_cdp_url(str(merged.get("connect_cdp_url", DEFAULT_CDP_URL)))
    merged["allow_only_cdp_url"] = normalize_cdp_url(str(merged.get("allow_only_cdp_url", DEFAULT_CDP_URL)))
    merged["bring_to_front"] = bool(merged.get("bring_to_front", False))
    merged["require_cdp"] = bool(merged.get("require_cdp", True))
    return merged


def ensure_allowed_cdp_url(cdp_url: str, browser_cfg: dict | None = None) -> str:
    merged = merge_browser_config(browser_cfg)
    normalized = normalize_cdp_url(cdp_url)
    allowed = merged["allow_only_cdp_url"]
    if normalized != allowed:
        raise RuntimeError(
            f"禁止连接非白名单 CDP 端口：当前={normalized}，仅允许={allowed}"
        )
    return normalized
