"""Bundled SVG icons, tinted to the current theme.

Sources (pinned, licences next to the files in gui/icons/):
  * Lucide 1.64.0 (ISC)           — every UI icon
  * Simple Icons 16.36.0 (CC0)    — brand-*.svg: the Tor Project / Tor Browser
    logos.  The Tor names and onion logo are trademarks of The Tor Project,
    used here only to identify the Tor layer the app controls.

Lucide icons draw with ``stroke="currentColor"``; Simple Icons are plain filled
paths.  Both are recoloured by rewriting the SVG text before rendering, then
rasterised at the screen's device-pixel ratio so they stay sharp on HiDPI.
"""
from __future__ import annotations
import os
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QGuiApplication
try:
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:      # qt6-svg missing: run without icons rather than crash
    QSvgRenderer = None

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

# What each privacy layer is drawn with.
LAYER_ICONS = {
    "tor":          "brand-torproject",   # the real Tor onion
    "dnscrypt":     "globe-lock",         # encrypted name resolution
    "i2p":          "waypoints",          # garlic-routed peer-to-peer overlay
    "onion_server": "server",             # a hidden service you host
}


@lru_cache(maxsize=None)
def _source(name: str) -> str:
    with open(os.path.join(_DIR, name + ".svg"), encoding="utf-8") as f:
        return f.read()


def _hex(color) -> str:
    c = QColor(color) if not isinstance(color, QColor) else color
    return c.name(QColor.NameFormat.HexRgb)


@lru_cache(maxsize=512)
def _renderer(name: str, color: str, stroke: float):
    svg = _source(name)
    if "currentColor" in svg:
        svg = svg.replace("currentColor", color)
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    else:  # filled brand mark
        svg = svg.replace("<svg ", f'<svg fill="{color}" ', 1)
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def _dpr() -> float:
    app = QGuiApplication.instance()
    return app.devicePixelRatio() if app else 1.0


def draw(p: QPainter, name: str, rect: QRectF, color, stroke: float = 1.75) -> None:
    """Render icon *name* into *rect* on an active painter."""
    if QSvgRenderer is None:
        return
    _renderer(name, _hex(color), stroke).render(p, rect)


@lru_cache(maxsize=512)
def _pixmap(name: str, size: int, color: str, stroke: float, dpr: float) -> QPixmap:
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    if QSvgRenderer is None:
        return px
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _renderer(name, color, stroke).render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    px.setDevicePixelRatio(dpr)
    return px


def pixmap(name: str, size: int, color, stroke: float = 1.75) -> QPixmap:
    return _pixmap(name, size, _hex(color), stroke, _dpr())


def icon(name: str, color, disabled=None, size: int = 18, stroke: float = 1.75) -> QIcon:
    """A QIcon with a normal and (optionally dimmer) disabled rendering."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color, stroke), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, size, color, stroke), QIcon.Mode.Active)
    if disabled is not None:
        ic.addPixmap(pixmap(name, size, disabled, stroke), QIcon.Mode.Disabled)
    return ic


def qsize(n: int) -> QSize:
    return QSize(n, n)
