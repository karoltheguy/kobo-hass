"""Server configuration (config.yaml)."""

from __future__ import annotations

import os
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
    )
