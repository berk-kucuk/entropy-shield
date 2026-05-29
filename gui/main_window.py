from __future__ import annotations
import sys
import os
import math
import pwd
import random as _random
import select
import subprocess
import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPoint, QRectF,
)
from PyQt6.QtGui import (
    QTextCursor, QPainter, QColor, QPen, QIcon, QPixmap,
    QPainterPath, QRadialGradient,
)

import gui.themes as themes
from gui.themes import current as theme, build_qss
from gui.widgets import ServiceCard, Spinner, StatusRing, NetSpeedBar
from gui.settings_panel import SettingsPanel
from core.connection import ConnectionManager
from core.config import cfg
import core.browser as browser
import core.autostart as autostart

_LOGOS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logos"
)
_TRAY_HELPER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "tray_helper.py",
)

def _make_window_icon(path: str) -> "QIcon":
    """Return a multi-resolution square QIcon suitable for window/taskbar use."""
    icon = QIcon()
    if path and os.path.exists(path):
        src = QPixmap(path)
        if not src.isNull():
            side = min(src.width(), src.height())
            x    = (src.width()  - side) // 2
            y    = (src.height() - side) // 2
            sq   = src.copy(x, y, side, side)
            for sz in (16, 24, 32, 48, 64, 128, 256, 512):
                icon.addPixmap(sq.scaled(
                    sz, sz,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            return icon
    return QIcon()


def _logo_for_theme(theme_name: str) -> str:
    mapping = {
        "oled":    "oled.png",
        "dark":    "dark.png",
        "light":   "dark.png",
        "binary":  "binary.png",
        "circuit": "circuit.png",
        "pixel":   "pixel.png",
    }
    filename = mapping.get(theme_name, "dark.png")
    path = os.path.join(_LOGOS_DIR, filename)
    if os.path.exists(path):
        return path
    # fallback: dark.png first, then anything available
    for f in ("dark.png", "oled.png", "binary.png", "circuit.png", "pixel.png"):
        fb = os.path.join(_LOGOS_DIR, f)
        if os.path.exists(fb):
            return fb
    return ""


# ── pre-baked random data for special theme backgrounds ──────
_rng = _random.Random(42)
_BINARY_CHARS = [
    (_rng.random(), _rng.random(), _rng.choice("01"), _rng.uniform(0, 2 * math.pi))
    for _ in range(160)
]
_CIRCUIT_NODES = [
    (_rng.random(), _rng.random(), _rng.uniform(0, 2 * math.pi))
    for _ in range(24)
]
_PIXEL_BLOCKS = [
    (_rng.random(), _rng.random(), _rng.randint(0, 2), _rng.uniform(0, 2 * math.pi))
    for _ in range(80)
]
del _rng


_GLOW_FALLBACK = {
    "off":        (210, 55,  48),
    "connecting": (210, 153, 50),
    "on":         (44,  186, 92),
    "error":      (210, 55,  48),
}
_SERVICE_MAP = {
    "tor":      "tor",
    "dnscrypt": "dnscrypt-proxy",
    "i2p":      "i2pd",
}
_CORNER_R = 22


def _glow_color(state: str) -> tuple[int, int, int]:
    return theme().get("glow", _GLOW_FALLBACK).get(state, _GLOW_FALLBACK["off"])


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (80, 80, 80)


# ── background worker ─────────────────────────────────────────

class _Worker(QThread):
    log  = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, action: str, mgr: "ConnectionManager", layers: dict):
        super().__init__()
        self._action = action
        self._mgr    = mgr
        self._layers = layers

    def run(self) -> None:
        emit = self.log.emit
        self._mgr._log        = emit
        self._mgr._tor._log   = emit
        self._mgr._dns._log   = emit
        self._mgr._i2p._log   = emit
        self._mgr._onion._log = emit
        self._mgr._fw._log    = emit
        try:
            if self._action == "connect":
                self._mgr.connect(**self._layers)
            else:
                self._mgr.disconnect()
            self.done.emit(True, self._action)
        except Exception as exc:
            if self._action == "connect":
                try:
                    self._mgr.disconnect()
                except Exception:
                    pass
            self.done.emit(False, str(exc))


# ── IP check worker ───────────────────────────────────────────

class _IpCheckWorker(QThread):
    result = pyqtSignal(str)

    def __init__(self, socks_port: int):
        super().__init__()
        self._port = socks_port

    def run(self) -> None:
        import json
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "12",
                 "--socks5-hostname", f"127.0.0.1:{self._port}",
                 "https://check.torproject.org/api/ip"],
                capture_output=True, text=True, timeout=18,
            )
            if r.returncode == 0:
                data   = json.loads(r.stdout)
                ip     = data.get("IP", "?")
                is_tor = data.get("IsTor", False)
                mark   = "✓ via Tor" if is_tor else "✗ NOT via Tor"
                self.result.emit(f"[IP CHECK] {ip}  {mark}")
            else:
                self.result.emit(
                    f"[IP CHECK] Failed: {r.stderr.strip() or 'curl error'}")
        except FileNotFoundError:
            self.result.emit("[IP CHECK] curl not found — install curl.")
        except Exception as e:
            self.result.emit(f"[IP CHECK] Error: {e}")


# ── glow frame ────────────────────────────────────────────────

class _GlowFrame(QWidget):
    """
    Central widget that paints:
      1. Rounded-rect background (solid, parent window is transparent).
      2. Animated ambient light blobs (slow breath cycle).
      3. Theme-specific background effects (binary rain, circuit traces, pixel grid).
      4. Animated colour-shifting glow border.
    """
    _GLOW_W   = 8
    _BLOB_FPS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgb        = _glow_color("off")
        self._alpha      = 0.45
        self._glow_state = "off"

        self._blob_phase  = 0.0
        self._blob_phase2 = math.pi

        self._blob_timer = QTimer(self)
        self._blob_timer.timeout.connect(self._blob_tick)
        self._blob_timer.start(1000 // self._BLOB_FPS)

    def _blob_tick(self) -> None:
        step = 2 * math.pi / (5 * self._BLOB_FPS)
        self._blob_phase  = (self._blob_phase  + step) % (2 * math.pi)
        self._blob_phase2 = (self._blob_phase2 + step) % (2 * math.pi)
        self.update()

    def set_color(self, rgb: tuple[int, int, int], alpha: float,
                  state: str = "") -> None:
        self._rgb        = rgb
        self._alpha      = alpha
        self._glow_state = state
        self.update()

    # ── special theme backgrounds ─────────────────────────────

    def _draw_binary_bg(self, p: QPainter, w: float, h: float) -> None:
        from PyQt6.QtGui import QFont
        font = QFont()
        font.setFamily("JetBrains Mono,Fira Code,Cascadia Code,Courier New,monospace")
        font.setPixelSize(11)
        p.setFont(font)
        for nx, ny, ch, ph in _BINARY_CHARS:
            t = (math.sin(self._blob_phase * 0.7 + ph) + 1) / 2
            if t > 0.88:
                color = QColor(215, 215, 215, int(210 * t))  # near-white
            elif t > 0.58:
                color = QColor(140, 140, 140, int(110 * t))  # mid gray
            else:
                color = QColor(55,  55,  55,  int(55  * t))  # dark gray
            p.setPen(color)
            p.drawText(int(nx * w), int(ny * h), ch)

    def _draw_circuit_bg(self, p: QPainter, w: float, h: float) -> None:
        spacing = 36
        p.setPen(QPen(QColor(180, 180, 190, 10), 1))
        x = 0
        while x <= w:
            p.drawLine(int(x), 0, int(x), int(h))
            x += spacing
        y = 0
        while y <= h:
            p.drawLine(0, int(y), int(w), int(y))
            y += spacing

        for nx, ny, ph in _CIRCUIT_NODES:
            gx = round(nx * w / spacing) * spacing
            gy = round(ny * h / spacing) * spacing
            t  = (math.sin(self._blob_phase + ph) + 1) / 2
            a  = int(30 + 95 * t)
            sz = 2.5 + t * 4.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(200, 200, 210, a))
            p.drawEllipse(int(gx - sz), int(gy - sz), int(sz * 2), int(sz * 2))

        p.setPen(QPen(QColor(190, 190, 200, 20), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        nodes = _CIRCUIT_NODES
        for i in range(0, len(nodes) - 1, 2):
            ax = round(nodes[i][0]   * w / spacing) * spacing
            ay = round(nodes[i][1]   * h / spacing) * spacing
            bx = round(nodes[i+1][0] * w / spacing) * spacing
            by = round(nodes[i+1][1] * h / spacing) * spacing
            mx = ax if abs(ax - bx) > abs(ay - by) else bx
            p.drawLine(int(ax), int(ay), int(mx), int(ay))
            p.drawLine(int(mx), int(ay), int(bx), int(by))

    def _draw_pixel_bg(self, p: QPainter, w: float, h: float) -> None:
        cell = 20  # Minecraft-sized pixel blocks, snapped to grid
        # Strictly monochrome: white, mid-grey, dark-grey
        shades = [200, 140, 80]
        for nx, ny, ci, ph in _PIXEL_BLOCKS:
            t = (math.sin(self._blob_phase * 0.45 + ph) + 1) / 2
            if t < 0.20:
                continue
            lum = shades[ci % len(shades)]
            a   = int(8 + 28 * t)
            x   = (int(nx * (w - cell)) // cell) * cell
            y   = (int(ny * (h - cell)) // cell) * cell
            p.fillRect(x, y, cell, cell, QColor(lum, lum, lum, a))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect())
        c    = theme()
        r    = float(c.get("radius", _CORNER_R))

        # ── 1. solid rounded background ───────────────────────
        bg_rgb = c.get("bg_rgb", (0, 0, 0))
        path   = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.fillPath(path, QColor(*bg_rgb))

        p.setClipPath(path)

        fw, fh = rect.width(), rect.height()

        # ── 2. theme-specific background effects ──────────────
        theme_name = c["name"]
        if theme_name == "binary":
            self._draw_binary_bg(p, fw, fh)
        elif theme_name == "circuit":
            self._draw_circuit_bg(p, fw, fh)
        elif theme_name == "pixel":
            self._draw_pixel_bg(p, fw, fh)

        # ── 3. animated ambient blobs (monochrome) ────────────
        is_dark = c["name"] != "light"
        # All themes use white or black blobs at low alpha for a subtle vignette
        blob_lum  = 255 if is_dark else 0
        max_b     = 30  if is_dark else 12
        max_g     = 22  if is_dark else 8

        t1 = 0.45 + 0.55 * (math.sin(self._blob_phase)  + 1) / 2
        t2 = 0.45 + 0.55 * (math.sin(self._blob_phase2) + 1) / 2

        g1 = QRadialGradient(fw * 0.88, fh * 0.06, fw * 0.60)
        g1.setColorAt(0.0, QColor(blob_lum, blob_lum, blob_lum, int(max_b * t1)))
        g1.setColorAt(1.0, QColor(blob_lum, blob_lum, blob_lum, 0))
        p.fillRect(rect, g1)

        g2 = QRadialGradient(fw * 0.12, fh * 0.94, fw * 0.52)
        g2.setColorAt(0.0, QColor(blob_lum, blob_lum, blob_lum, int(max_g * t2)))
        g2.setColorAt(1.0, QColor(blob_lum, blob_lum, blob_lum, 0))
        p.fillRect(rect, g2)

        # Accent glow from current status colour
        cr, cg, cb = self._rgb
        t3       = 0.3 + 0.7 * (math.sin(self._blob_phase + math.pi * 0.66) + 1) / 2
        accent_a = int((20 if is_dark else 8) * t3 * max(0.2, self._alpha))
        g3 = QRadialGradient(fw * 0.50, fh * 0.35, fw * 0.55)
        g3.setColorAt(0.0, QColor(cr, cg, cb, accent_a))
        g3.setColorAt(1.0, QColor(cr, cg, cb, 0))
        p.fillRect(rect, g3)

        p.setClipping(False)

        # ── 4. glow rings ─────────────────────────────────────
        cr2, cg2, cb2 = self._rgb
        alpha         = self._alpha
        gw            = self._GLOW_W

        for i in range(gw, 0, -1):
            factor = ((gw - i + 1) / gw) ** 2.2
            a      = int(alpha * 150 * factor)
            p.setPen(QPen(QColor(cr2, cg2, cb2, a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            ri = rect.adjusted(i, i, -i, -i)
            ri_r = max(0.0, r - i * 0.5)
            p.drawRoundedRect(ri, ri_r, ri_r)

        # ── 5. crisp outer border ──────────────────────────────
        border_a = int(200 * alpha)
        p.setPen(QPen(QColor(cr2, cg2, cb2, border_a), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), r, r)


# ── draggable title bar ───────────────────────────────────────

class _TitleBar(QWidget):
    """
    Custom title bar.
    • Drag: tries startSystemMove() for Wayland; falls back to manual QPoint
      tracking for X11 — safe on all platforms.
    • ✕ button minimises to tray (does NOT quit).
    """

    _LOGO_MAX_W = 140
    _LOGO_MAX_H = 44
    _TOTAL_H    = 62

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self._win      = win
        self._drag_pos: QPoint | None = None

        self.setObjectName("appHeader")
        self.setFixedHeight(self._TOTAL_H)

        # Single horizontal row: [logo] [title] [spacer] [buttons]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)

        # ── logo (left) ───────────────────────────────────────
        self._logo_lbl = QLabel()
        self._logo_lbl.setObjectName("logoLbl")
        self._logo_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._logo_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._set_logo_pixmap(_logo_for_theme(theme()["name"]))
        lay.addWidget(self._logo_lbl)

        # ── title beside logo ─────────────────────────────────
        title = QLabel("ENTROPY SHIELD")
        title.setObjectName("appTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(title)
        lay.addStretch()

        # ⎯  Minimize
        btn_min = QPushButton("⎯")
        btn_min.setObjectName("minBtn")
        btn_min.setFixedSize(26, 26)
        btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_min.setToolTip("Minimize")
        btn_min.clicked.connect(win.showMinimized)
        lay.addWidget(btn_min)

        # ⚙  Settings
        btn_set = QPushButton("⚙")
        btn_set.setObjectName("settingsBtn")
        btn_set.setFixedSize(26, 26)
        btn_set.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_set.setToolTip("Settings")
        btn_set.clicked.connect(win._open_settings)
        lay.addWidget(btn_set)

        # ✕  Hide to tray (NOT quit)
        btn_close = QPushButton("✕")
        btn_close.setObjectName("quitBtn")
        btn_close.setFixedSize(26, 26)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Minimize to tray")
        btn_close.clicked.connect(win.close)
        lay.addWidget(btn_close)

    def _set_logo_pixmap(self, path: str) -> None:
        if path:
            src = QPixmap(path)
            if not src.isNull():
                pix = src.scaled(self._LOGO_MAX_W, self._LOGO_MAX_H,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self._logo_lbl.setPixmap(pix)
                return
        self._logo_lbl.clear()

    def update_logo(self, theme_name: str) -> None:
        self._set_logo_pixmap(_logo_for_theme(theme_name))

    # ── drag handling ─────────────────────────────────────────

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            # Try native system move (required for Wayland; works on X11 too)
            handle = self._win.windowHandle()
            if handle:
                try:
                    handle.startSystemMove()
                    e.accept()
                    return
                except Exception:
                    pass
            # X11 fallback: manual QPoint tracking
            self._drag_pos = e.globalPosition().toPoint() - self._win.pos()
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if (e.buttons() & Qt.MouseButton.LeftButton) and self._drag_pos is not None:
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._drag_pos = None
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if self._win.isMaximized():
                self._win.showNormal()
            else:
                self._win.showMaximized()
        e.accept()


# ── main window ───────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Entropy Shield")

        # FramelessWindowHint → remove OS title bar
        # WA_TranslucentBackground → OS-level transparency for true rounded corners
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        _logo = _logo_for_theme(cfg().get("theme"))
        if _logo:
            self.setWindowIcon(_make_window_icon(_logo))

        self.setMinimumSize(560, 680)
        self.resize(560, 740)

        self._connected      = False
        self._worker: _Worker | None = None
        self._ip_worker: _IpCheckWorker | None = None
        self._mgr            = ConnectionManager(lambda _: None)
        self._active_layers: list[str] = []
        self._tray_proc: subprocess.Popen | None = None
        self._tray_username: str = ""
        self._quitting: bool = False

        self._glow_state  = "off"
        self._glow_rgb    = _glow_color("off")
        self._glow_alpha  = 0.45
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10_000)
        self._health_timer.timeout.connect(self._check_service_health)

        self._apply_theme()
        self._build_ui()

        self._settings = SettingsPanel(self._glow_frame)
        self._settings.saved.connect(self._on_settings_saved)

        self._build_tray()
        self._append_log("[>] Entropy Shield ready.")

        if cfg().get("autostart"):
            if autostart.enable():
                self._append_log("[>] Autostart enabled.")
        else:
            autostart.disable()

        if cfg().get("auto_connect"):
            QTimer.singleShot(1600, self._auto_connect)

    # ── glow ──────────────────────────────────────────────────

    def _set_glow(self, state: str) -> None:
        self._glow_state = state
        self._glow_rgb   = _glow_color(state)
        if state == "connecting":
            self._pulse_phase = 0.0
            self._pulse_timer.start(28)
        else:
            self._pulse_timer.stop()
            self._glow_alpha = 0.85 if state == "on" else 0.45
            self._status_ring.set_pulse(0.7)
            self._glow_frame.set_color(self._glow_rgb, self._glow_alpha, state)

    def _pulse_step(self) -> None:
        self._pulse_phase = (self._pulse_phase + 0.07) % (2 * math.pi)
        t = (math.sin(self._pulse_phase) + 1) / 2
        self._glow_alpha = 0.25 + 0.65 * t
        self._glow_frame.set_color(
            self._glow_rgb, self._glow_alpha, self._glow_state)
        self._status_ring.set_pulse(t)

    # ── theme ─────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        themes.set_theme(cfg().get("theme"))
        self.setStyleSheet(build_qss(theme()))

    def _on_settings_saved(self) -> None:
        self._apply_theme()
        theme_name = cfg().get("theme")
        logo_path  = _logo_for_theme(theme_name)
        if hasattr(self, "_glow_frame"):
            self._glow_frame.update()
        if hasattr(self, "_title_bar"):
            self._title_bar.update_logo(theme_name)
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_onion):
            card.refresh_theme()
        if logo_path:
            self.setWindowIcon(_make_window_icon(logo_path))
        self._tray_send(f"icon:{logo_path}")
        self._append_log(f"[>] Settings saved. Theme: {theme_name}.")

    # ── build UI ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._glow_frame = _GlowFrame()
        self.setCentralWidget(self._glow_frame)

        pad = _GlowFrame._GLOW_W + 10
        content = QVBoxLayout(self._glow_frame)
        content.setContentsMargins(pad, pad, pad, pad)
        content.setSpacing(0)

        self._title_bar = _TitleBar(self)
        content.addWidget(self._title_bar)
        content.addSpacing(14)
        content.addLayout(self._build_status_area())
        content.addSpacing(14)
        content.addLayout(self._build_cards())
        content.addSpacing(10)
        content.addWidget(self._build_connect_row())
        content.addSpacing(6)
        content.addWidget(self._build_browser_row())
        content.addSpacing(8)
        content.addWidget(self._build_log(), stretch=1)

    def _build_status_area(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(4)

        ring_row = QHBoxLayout()
        ring_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._status_ring = StatusRing(108)
        self._status_ring.set_state("off")
        ring_row.addWidget(self._status_ring)

        self._status_lbl = QLabel("DISCONNECTED")
        self._status_lbl.setObjectName("statusTitle")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_sub = QLabel("Select layers and connect")
        self._status_sub.setObjectName("statusSub")
        self._status_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._speed_bar = NetSpeedBar()

        col.addLayout(ring_row)
        col.addWidget(self._status_lbl)
        col.addWidget(self._status_sub)
        col.addWidget(self._speed_bar)
        return col

    def _build_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._card_tor   = ServiceCard("tor")
        self._card_dns   = ServiceCard("dnscrypt")
        self._card_i2p   = ServiceCard("i2p")
        self._card_onion = ServiceCard("onion_server", checked=False)
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_onion):
            card.settings_clicked.connect(self._open_settings_tab)
            row.addWidget(card)
        self._card_tor.toggled.connect(self._on_tor_toggled)
        self._card_onion.toggled.connect(self._on_onion_toggled)
        return row

    def _build_connect_row(self) -> QWidget:
        w   = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._connect_btn = QPushButton("CONNECT")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.setCheckable(True)
        self._connect_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._connect_btn.clicked.connect(self._on_connect_clicked)

        self._spinner = Spinner(20)
        lay.addWidget(self._connect_btn)
        lay.addWidget(self._spinner)
        return w

    def _build_browser_row(self) -> QWidget:
        w   = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._tor_browser_btn = QPushButton("TOR BROWSER")
        self._tor_browser_btn.setObjectName("torBrowserBtn")
        self._tor_browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tor_browser_btn.setEnabled(False)
        self._tor_browser_btn.setToolTip(
            "Isolated browser routed through Tor.\nSupports Firefox, Chromium, Brave.")
        self._tor_browser_btn.clicked.connect(self._on_open_tor_browser)

        self._i2p_browser_btn = QPushButton("I2P BROWSER")
        self._i2p_browser_btn.setObjectName("i2pBrowserBtn")
        self._i2p_browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._i2p_browser_btn.setEnabled(False)
        self._i2p_browser_btn.setToolTip(
            "Isolated browser routed through I2P.\nSupports Firefox, Chromium, Brave.")
        self._i2p_browser_btn.clicked.connect(self._on_open_i2p_browser)

        lay.addWidget(self._tor_browser_btn)
        lay.addWidget(self._i2p_browser_btn)
        return w

    def _build_log(self) -> QWidget:
        wrapper = QWidget()
        lay     = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        hdr = QHBoxLayout()
        lbl = QLabel("ACTIVITY LOG")
        lbl.setObjectName("logLabel")
        hdr.addWidget(lbl)
        hdr.addStretch()

        self._ip_check_btn = QPushButton("IP CHECK")
        self._ip_check_btn.setObjectName("toolBtn")
        self._ip_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ip_check_btn.setEnabled(False)
        self._ip_check_btn.setToolTip("Check IP via Tor SOCKS (requires curl).")
        self._ip_check_btn.clicked.connect(self._on_ip_check)

        exp_btn = QPushButton("⬇ EXPORT")
        exp_btn.setObjectName("toolBtn")
        exp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exp_btn.setToolTip("Save activity log to file.")
        exp_btn.clicked.connect(self._export_log)

        hdr.addWidget(self._ip_check_btn)
        hdr.addSpacing(4)
        hdr.addWidget(exp_btn)

        self._log_widget = QTextEdit()
        self._log_widget.setObjectName("log")
        self._log_widget.setReadOnly(True)

        lay.addLayout(hdr)
        lay.addWidget(self._log_widget)
        return wrapper

    # ── settings ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        self._settings.setGeometry(self._glow_frame.rect())
        self._settings.open()

    def _open_settings_tab(self, tag: str) -> None:
        tab_map = {"tor": 0, "dnscrypt": 1, "i2p": 2, "onion_server": 3}
        self._settings._tabs.setCurrentIndex(tab_map.get(tag, 0))
        self._open_settings()

    def _on_tor_toggled(self, _tag: str, checked: bool) -> None:
        if not checked and self._card_onion.is_checked:
            self._card_onion._toggle.setChecked(False, silent=True)

    def _on_onion_toggled(self, _tag: str, checked: bool) -> None:
        if checked and not self._card_tor.is_checked:
            self._card_tor._toggle.setChecked(True, silent=True)

    # ── status ────────────────────────────────────────────────

    def _set_status(self, state: str, title: str, sub: str) -> None:
        c = theme()
        color_map = {
            "off":        c["text_muted"],
            "connecting": c["yellow"],
            "on":         c["green"],
            "error":      c["red"],
        }
        self._status_ring.set_state(state)
        self._status_lbl.setText(title)
        self._status_lbl.setStyleSheet(
            f"color:{color_map.get(state, c['text'])};"
            "font-size:15px;font-weight:700;letter-spacing:4px;"
            "background:transparent;"
        )
        self._status_sub.setText(sub)

    # ── connect / disconnect ───────────────────────────────────

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self._start_worker("disconnect", {})
        else:
            layers = {
                "use_tor":          self._card_tor.is_checked,
                "use_dnscrypt":     self._card_dns.is_checked,
                "use_i2p":          self._card_i2p.is_checked,
                "use_onion_server": self._card_onion.is_checked,
            }
            if not any(layers.values()):
                self._append_log("[!] Select at least one privacy layer.")
                self._connect_btn.setChecked(False)
                return
            self._active_layers = [
                k.replace("use_", "") for k, v in layers.items() if v
            ]
            self._start_worker("connect", layers)

    def _start_worker(self, action: str, layers: dict) -> None:
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText(
            "CONNECTING..." if action == "connect" else "DISCONNECTING...")
        self._spinner.start()
        self._set_cards_enabled(False)
        self._set_glow("connecting")
        if action == "connect":
            self._set_status("connecting", "CONNECTING...",
                             "Routing through selected layers…")
            for card in self._active_cards():
                card.set_status("connecting")

        self._worker = _Worker(action, self._mgr, layers)
        self._worker.log.connect(self._append_log)
        self._worker.done.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_done(self, success: bool, info: str) -> None:
        self._spinner.stop()
        self._connect_btn.setEnabled(True)
        self._set_cards_enabled(True)
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()

        if self._quitting:
            self._do_quit()
            return

        if success and info == "connect":
            self._connected = True
            self._connect_btn.setChecked(True)
            self._connect_btn.setText("DISCONNECT")
            self._set_glow("on")
            layers_str = "  ·  ".join(
                l.upper().replace("_", " ") for l in self._active_layers)
            self._set_status("on", "PROTECTED", layers_str)
            for card in self._active_cards():
                card.set_status("active")
            tor_on = "tor" in self._active_layers
            self._tor_browser_btn.setEnabled(tor_on)
            self._i2p_browser_btn.setEnabled("i2p" in self._active_layers)
            self._ip_check_btn.setEnabled(tor_on)
            self._speed_bar.set_active(True)
            self._tray_send("connected")
            if cfg().get("kill_switch"):
                self._health_timer.start()

        elif success and info == "disconnect":
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("CONNECT")
            self._set_glow("off")
            self._set_status("off", "DISCONNECTED", "Select layers and connect")
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_onion):
                card.set_status("")
            self._tor_browser_btn.setEnabled(False)
            self._i2p_browser_btn.setEnabled(False)
            self._ip_check_btn.setEnabled(False)
            self._speed_bar.set_active(False)
            self._health_timer.stop()
            self._tray_send("disconnected")

        else:
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("CONNECT")
            self._set_glow("error")
            short = info[:70] if len(info) > 70 else info
            self._set_status("error", "ERROR", short)
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_onion):
                card.set_status("error" if card.is_checked else "")
            self._tor_browser_btn.setEnabled(False)
            self._i2p_browser_btn.setEnabled(False)
            self._ip_check_btn.setEnabled(False)
            self._speed_bar.set_active(False)
            self._health_timer.stop()
            self._tray_send("disconnected")
            self._append_log(f"[ERR] {info}")

    # ── kill switch ───────────────────────────────────────────

    def _check_service_health(self) -> None:
        if not self._connected or self._worker is not None:
            return
        for layer, service in _SERVICE_MAP.items():
            if layer not in self._active_layers:
                continue
            r = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True, text=True,
            )
            if r.stdout.strip() != "active":
                self._append_log(
                    f"[!] KILL SWITCH: {service} dropped — "
                    "emergency disconnect triggered.")
                self._health_timer.stop()
                self._start_worker("disconnect", {})
                return

    # ── auto-connect ──────────────────────────────────────────

    def _auto_connect(self) -> None:
        if self._connected or self._worker is not None:
            return
        layers = {
            "use_tor":          self._card_tor.is_checked,
            "use_dnscrypt":     self._card_dns.is_checked,
            "use_i2p":          self._card_i2p.is_checked,
            "use_onion_server": self._card_onion.is_checked,
        }
        if not any(layers.values()):
            return
        self._active_layers = [k.replace("use_", "") for k, v in layers.items() if v]
        self._append_log("[>] Auto-connecting…")
        self._start_worker("connect", layers)

    # ── IP check ──────────────────────────────────────────────

    def _on_ip_check(self) -> None:
        if self._ip_worker and self._ip_worker.isRunning():
            return
        self._ip_check_btn.setEnabled(False)
        self._append_log("[IP CHECK] Checking via Tor… (up to 12 s)")
        self._ip_worker = _IpCheckWorker(cfg().get("tor", "socks_port"))
        self._ip_worker.result.connect(self._on_ip_result)
        self._ip_worker.start()

    def _on_ip_result(self, msg: str) -> None:
        self._append_log(msg)
        if self._connected and "tor" in self._active_layers:
            self._ip_check_btn.setEnabled(True)

    # ── log export ────────────────────────────────────────────

    def _export_log(self) -> None:
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.home() / f"entropy-shield-log-{ts}.txt"
        try:
            path.write_text(self._log_widget.toPlainText())
            self._append_log(f"[>] Log exported → {path}")
        except Exception as e:
            self._append_log(f"[!] Export failed: {e}")

    # ── browsers ──────────────────────────────────────────────

    def _on_open_tor_browser(self) -> None:
        try:
            browser.launch_tor(cfg().get("tor", "socks_port"), self._append_log)
        except Exception as exc:
            self._append_log(f"[BROWSER] {exc}")

    def _on_open_i2p_browser(self) -> None:
        try:
            browser.launch_i2p(
                cfg().get("i2p", "http_port"),
                cfg().get("i2p", "socks_port"),
                self._append_log,
            )
        except Exception as exc:
            self._append_log(f"[BROWSER] {exc}")

    # ── helpers ───────────────────────────────────────────────

    def _active_cards(self) -> list[ServiceCard]:
        return [c for c in (self._card_tor, self._card_dns,
                             self._card_i2p, self._card_onion)
                if c.is_checked]

    def _set_cards_enabled(self, enabled: bool) -> None:
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_onion):
            card.set_enabled_ui(enabled)

    def _append_log(self, msg: str) -> None:
        self._log_widget.append(msg)
        self._log_widget.moveCursor(QTextCursor.MoveOperation.End)

    # ── system tray ───────────────────────────────────────────

    def _original_uid(self) -> int | None:
        for var in ("PKEXEC_UID", "SUDO_UID"):
            val = os.environ.get(var)
            if val and val.isdigit():
                return int(val)
        try:
            out = subprocess.check_output(
                ["loginctl", "list-sessions", "--no-legend"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) != 0:
                    return int(parts[1])
        except Exception:
            pass
        return None

    def _user_env(self, uid: int, username: str, user_home: str) -> dict:
        runtime_dir = f"/run/user/{uid}"
        env: dict[str, str] = {
            "HOME":                   user_home,
            "USER":                   username,
            "LOGNAME":                username,
            "XDG_RUNTIME_DIR":        runtime_dir,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        }
        try:
            session_id = None
            out = subprocess.check_output(
                ["loginctl", "list-sessions", "--no-legend"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == str(uid):
                    session_id = parts[0]
                    break
            if session_id:
                props = subprocess.check_output(
                    ["loginctl", "show-session", session_id],
                    text=True, stderr=subprocess.DEVNULL,
                )
                for line in props.splitlines():
                    if line.startswith("Display="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            env["DISPLAY"] = val
                    elif line.startswith("Type=") and "wayland" in line:
                        wd = os.environ.get("WAYLAND_DISPLAY", "")
                        if not wd:
                            try:
                                for f in os.listdir(runtime_dir):
                                    if f.startswith("wayland-"):
                                        wd = f
                                        break
                            except Exception:
                                pass
                        if wd:
                            env["WAYLAND_DISPLAY"] = wd
                            env.setdefault("QT_QPA_PLATFORM", "wayland")
        except Exception:
            pass

        if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
            try:
                for pid_s in os.listdir("/proc"):
                    if not pid_s.isdigit():
                        continue
                    try:
                        if f"\nUid:\t{uid}\t" not in open(
                                f"/proc/{pid_s}/status").read():
                            continue
                        raw = open(f"/proc/{pid_s}/environ", "rb").read()
                        for item in raw.split(b"\x00"):
                            if b"=" not in item:
                                continue
                            k, _, v = item.partition(b"=")
                            key = k.decode(errors="replace")
                            if key in ("DISPLAY", "WAYLAND_DISPLAY",
                                       "DBUS_SESSION_BUS_ADDRESS",
                                       "XDG_RUNTIME_DIR",
                                       "QT_QPA_PLATFORMTHEME",
                                       "XDG_CURRENT_DESKTOP",
                                       "KDE_FULL_SESSION"):
                                env.setdefault(key, v.decode(errors="replace"))
                        if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
                            break
                    except (PermissionError, FileNotFoundError, ProcessLookupError):
                        continue
            except Exception:
                pass

        if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env:
            try:
                for f in os.listdir(runtime_dir):
                    if f.startswith("wayland-") and not f.endswith(".lock"):
                        env["WAYLAND_DISPLAY"] = f
                        env.setdefault("QT_QPA_PLATFORM", "wayland")
                        break
            except Exception:
                pass

        return env

    def _build_tray(self) -> None:
        uid = self._original_uid()
        if uid is None:
            self._append_log("[!] Tray disabled: could not determine real user UID.")
            return
        try:
            pw = pwd.getpwuid(uid)
        except KeyError:
            self._append_log(f"[!] Tray disabled: UID {uid} not found.")
            return

        username = pw.pw_name
        env      = self._user_env(uid, username, pw.pw_dir)
        self._append_log(
            f"[>] Tray env: DBUS={env.get('DBUS_SESSION_BUS_ADDRESS','?')[:40]} "
            f"DISPLAY={env.get('DISPLAY','?')} "
            f"WAYLAND={env.get('WAYLAND_DISPLAY','?')}"
        )

        tray_logo = _logo_for_theme(cfg().get("theme"))
        launched = False
        for cmd in (
            ["runuser", "-u", username, "--",
             sys.executable, _TRAY_HELPER, tray_logo],
            ["su", "-s", "/bin/sh", username, "-c",
             f"{sys.executable} {_TRAY_HELPER} {tray_logo}"],
        ):
            try:
                self._tray_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env,
                )
                launched = True
                break
            except FileNotFoundError:
                continue
            except Exception as exc:
                self._append_log(f"[!] Tray launch failed: {exc}")
                return

        if not launched:
            self._append_log("[!] runuser/su not found — tray disabled.")
            return

        self._tray_username = username
        self._tray_poll = QTimer(self)
        self._tray_poll.timeout.connect(self._poll_tray)
        self._tray_poll.start(100)
        QTimer.singleShot(600, self._check_tray_startup)

    def _check_tray_startup(self) -> None:
        if not self._tray_proc:
            return
        if self._tray_proc.poll() is not None:
            err = self._tray_proc.stderr.read().decode(errors="replace").strip()
            self._append_log(f"[!] Tray exited early: {err or '(no output)'}")
            self._tray_proc = None
            self._tray_poll.stop()
        else:
            self._append_log(f"[>] Tray started ({self._tray_username}).")
            self._tray_send("connected" if self._connected else "disconnected")

    def _poll_tray(self) -> None:
        if not self._tray_proc:
            return
        if self._tray_proc.poll() is not None:
            try:
                err = self._tray_proc.stderr.read().decode(errors="replace").strip()
                if err:
                    self._append_log(f"[!] Tray: {err}")
            except Exception:
                pass
            self._tray_proc = None
            self._tray_poll.stop()
            return

        ready, _, _ = select.select([self._tray_proc.stdout], [], [], 0)
        if not ready:
            return
        try:
            line = self._tray_proc.stdout.readline().decode(
                errors="replace").strip()
        except Exception:
            return

        if   line == "show":       self._tray_show()
        elif line == "connect":    self._tray_connect()
        elif line == "disconnect": self._tray_disconnect()
        elif line == "quit":       self._tray_quit()

    def _tray_send(self, cmd: str) -> None:
        if self._tray_proc and self._tray_proc.poll() is None:
            try:
                self._tray_proc.stdin.write((cmd + "\n").encode())
                self._tray_proc.stdin.flush()
            except Exception:
                pass

    def _do_quit(self) -> None:
        self._tray_send("ack_quit")
        if self._tray_proc:
            try:
                self._tray_proc.wait(timeout=3)
            except Exception:
                self._tray_proc.kill()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def _tray_show(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_connect(self) -> None:
        if self._connected or self._worker is not None:
            return
        layers = {
            "use_tor":          self._card_tor.is_checked,
            "use_dnscrypt":     self._card_dns.is_checked,
            "use_i2p":          self._card_i2p.is_checked,
            "use_onion_server": self._card_onion.is_checked,
        }
        if not any(layers.values()):
            self._append_log("[!] Tray connect: no layers selected.")
            return
        self._active_layers = [k.replace("use_", "") for k, v in layers.items() if v]
        self._start_worker("connect", layers)

    def _tray_disconnect(self) -> None:
        if self._connected and self._worker is None:
            self._start_worker("disconnect", {})

    def _tray_quit(self) -> None:
        self._quitting = True
        if self._connected and self._worker is None:
            self._start_worker("disconnect", {})
        elif self._worker is None:
            self._do_quit()

    # ── window events ─────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        # ✕ button → hide to tray (never quit from window close)
        event.ignore()
        if self._tray_proc and self._tray_proc.poll() is None:
            self.hide()
            self._tray_send("notify")
        else:
            self.showMinimized()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if hasattr(self, "_settings") and hasattr(self, "_glow_frame"):
            self._settings.setGeometry(self._glow_frame.rect())
