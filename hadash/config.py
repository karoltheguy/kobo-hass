"""Server configuration (config.yaml)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    ha_url: str = ""
    ha_token: str = ""
    mock: bool = False
    width: int = 600
    height: int = 800
    layout_path: Path = Path("layout.yaml")
    font_path: str | None = None
    bold_font_path: str | None = None
    icon_font_path: str | None = None  # emoji icons; a monochrome font reads best
    host: str = "0.0.0.0"
    port: int = 8080
    # After a tap, wait up to this long for HA to report the new state.
    tap_settle_timeout: float = 2.0
    # States are cached this long so /version and /dashboard.png share one fetch.
    state_cache_ttl: float = 1.0
    # Shared secret the Kobo sends as ?key=. From HADASH_SECRET only; empty = no check.
    secret: str = ""


# The Kobo puts the secret in a URL with plain shell, so no characters that need escaping.
SECRET_RE = re.compile(r"[A-Za-z0-9_-]{16,}")


class ConfigError(ValueError):
    pass


def secret_from_env() -> str:
    secret = os.environ.get("HADASH_SECRET", "").strip()
    if secret and not SECRET_RE.fullmatch(secret):
        raise ConfigError("HADASH_SECRET must be at least 16 characters of A-Z a-z 0-9 _ - "
                          "(try: openssl rand -hex 16)")
    return secret


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    ha = raw.get("homeassistant", {}) or {}
    screen = raw.get("screen", {}) or {}
    server = raw.get("server", {}) or {}
    fonts = raw.get("fonts", {}) or {}

    layout = Path(raw.get("layout", "layout.yaml"))
    if not layout.is_absolute():
        layout = path.parent / layout

    token = os.environ.get("HA_TOKEN") or ha.get("token", "")

    return Config(
        ha_url=str(ha.get("url", "")).rstrip("/"),
        ha_token=token,
        mock=bool(ha.get("mock", False)),
        width=int(screen.get("width", 600)),
        height=int(screen.get("height", 800)),
        layout_path=layout,
        font_path=fonts.get("regular"),
        bold_font_path=fonts.get("bold"),
        icon_font_path=fonts.get("icon"),
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8080)),
        tap_settle_timeout=float(server.get("tap_settle_timeout", 2.0)),
        state_cache_ttl=float(server.get("state_cache_ttl", 1.0)),
        secret=secret_from_env(),
    )
