from __future__ import annotations
import sys
import os
import math
import json
import random as _random
import select
import subprocess
import datetime
import html
import re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
    QPushButton, QLabel, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPoint, QPointF, QRectF,
)
from PyQt6.QtGui import (
    QTextCursor, QPainter, QColor, QPen, QIcon, QPixmap, QImage, QFont,
    QPainterPath, QRadialGradient, QLinearGradient, QGuiApplication,
)

import gui.themes as themes
from gui import icons
from gui.themes import current as theme, build_qss
from gui.widgets import (
    GlassPanel, LayerRow, Spinner, StatusRing, NetSpeedGraph, set_backdrop,
)
from gui.settings_panel import SettingsPanel
from core.config import cfg, cfg_port
from core.updater import VERSION
import core.browser as browser
import core.autostart as autostart

from core.daemon_client import DaemonClient, DaemonError

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
_PIXEL_BLOCKS = [
    (_rng.random(), _rng.random(), _rng.randint(0, 2), _rng.uniform(0, 2 * math.pi))
    for _ in range(80)
]
del _rng

# ── Circuit theme: CPU + data-path layout ─────────────────────
# The "CPU" is centred at (_CPU_CX, _CPU_CY) as fractions of the
# widget size.  Twelve orthogonal PCB traces radiate outward from
# the four chip edges.  Each trace is a list of (x_frac, y_frac)
# waypoints; consecutive pairs form horizontal or vertical segments.
_CPU_CX, _CPU_CY = 0.50, 0.20   # chip centre
_CPU_HW, _CPU_HH = 0.080, 0.080  # half-widths (fractions)

# Orthogonal PCB traces radiating from the four chip edges.
# Each entry is a list of (x_frac, y_frac) waypoints; adjacent
# pairs form horizontal or vertical segments — no diagonals.
# Traces are grouped in parallel "bus" bundles (3 per side) with
# deliberate spacing so the routing reads as organised data buses.
_CPU_TRACES = [
    # ══ right bus ════════════════════════════════════════════
    # Bus R-A: exits top-right, turns down, exits right edge
    [(0.580, 0.170), (0.720, 0.170), (0.720, 0.095), (0.960, 0.095)],
    # Bus R-B: exits mid-right, turns down into lower-right quadrant
    [(0.580, 0.210), (0.820, 0.210), (0.820, 0.400), (0.960, 0.400)],
    # Bus R-C: long run — exits lower-right, two bends, bottom-right corner
    [(0.580, 0.250), (0.660, 0.250), (0.660, 0.520),
     (0.860, 0.520), (0.860, 0.780)],

    # ══ left bus ════════════════════════════════════════════
    # Bus L-A: exits top-left, turns up, exits left edge
    [(0.420, 0.170), (0.280, 0.170), (0.280, 0.090), (0.040, 0.090)],
    # Bus L-B: exits mid-left, turns down into lower-left
    [(0.420, 0.210), (0.160, 0.210), (0.160, 0.440), (0.040, 0.440)],
    # Bus L-C: long run — two bends, bottom-left corner
    [(0.420, 0.250), (0.340, 0.250), (0.340, 0.580),
     (0.130, 0.580), (0.130, 0.840)],

    # ══ bottom bus ══════════════════════════════════════════
    # Bus B-A: exits lower-left, turns left, exits bottom
    [(0.450, 0.280), (0.450, 0.490), (0.240, 0.490), (0.240, 0.960)],
    # Bus B-B: exits straight down, turns right, exits bottom-right
    [(0.500, 0.280), (0.500, 0.660), (0.640, 0.660), (0.640, 0.960)],
    # Bus B-C: exits lower-right, two bends toward right edge
    [(0.550, 0.280), (0.550, 0.430), (0.780, 0.430),
     (0.780, 0.710), (0.960, 0.710)],

    # ══ top bus ═════════════════════════════════════════════
    # Bus T-A: exits top-left, turns left, exits left edge
    [(0.450, 0.120), (0.450, 0.048), (0.140, 0.048)],
    # Bus T-B: exits straight up, turns right, exits top edge
    [(0.500, 0.120), (0.500, 0.026), (0.860, 0.026), (0.960, 0.026)],
    # Bus T-C: exits top-right, two bends along right side
    [(0.550, 0.120), (0.550, 0.058), (0.940, 0.058), (0.940, 0.180)],
]


_GLOW_FALLBACK = {
    "off":        (115, 115, 115),
    "connecting": (170, 170, 170),
    "on":         (245, 245, 245),
    "error":      (255, 255, 255),
}
_SERVICE_MAP = {
    # Tor is a managed subprocess — checked separately via TorManager.is_running().
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

    def __init__(self, action: str, layers: dict,
                 runner_proc: "DaemonClient | None" = None):
        super().__init__()
        self._action      = action
        self._layers      = layers
        self._runner      = runner_proc
        self.new_runner: "DaemonClient | None" = None  # set on connect success

    def run(self) -> None:
        try:
            if self._action == "connect":
                self._do_connect()
            else:
                self._do_disconnect()
            self.done.emit(True, self._action)
        except Exception as exc:
            self.done.emit(False, str(exc))

    # ── connect ───────────────────────────────────────────────

    def _do_connect(self) -> None:
        try:
            proc = DaemonClient.connect()
        except DaemonError as exc:
            raise RuntimeError(str(exc))

        # Wait for the daemon to confirm the session is ready.
        while True:
            raw = proc.stdout.readline()
            if not raw:
                proc.close()
                raise RuntimeError(
                    "Privileged daemon closed the connection during startup.")
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.log.emit(line)   # show HEAL and startup messages in the GUI
            if line == "[RUNNER] started":
                break
            if "[ERR]" in line:
                proc.close()
                raise RuntimeError(line)

        self.new_runner = proc

        # Send connect command
        proc.stdin.write(f"connect {json.dumps(self._layers)}\n".encode())
        proc.stdin.flush()

        # Read log lines until connected
        while True:
            raw = proc.stdout.readline()
            if not raw:
                raise RuntimeError(
                    "Privileged process terminated unexpectedly during connection.")
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.log.emit(line)
            if "[OK] All selected layers are active." in line:
                return
            if "[ERR]" in line:
                # Runner stays alive; GUI will show error and leave runner running
                # so a future disconnect can clean up properly.
                raise RuntimeError(line)

    # ── disconnect ────────────────────────────────────────────

    def _do_disconnect(self) -> None:
        proc = self._runner
        if proc is None or proc.poll() is not None:
            return
        with proc.lock:
            try:
                proc.stdin.write(b"disconnect\n")
                proc.stdin.flush()
            except OSError:
                proc.close()
                return
            while True:
                try:
                    raw = proc.stdout.readline()
                except OSError:
                    break
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip()
                if line:
                    self.log.emit(line)
                if "[OK] All layers disconnected." in line:
                    break
        # Closing the socket ends the daemon-side session and is itself a safe
        # disconnect (the daemon rolls back on EOF).
        proc.close()


# ── Panic worker ─────────────────────────────────────────────

class _PanicWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, runner_proc: "DaemonClient | None"):
        super().__init__()
        self._runner = runner_proc

    def run(self) -> None:
        from core.panic import user_space_panic
        msgs: list[str] = []
        user_space_panic(log_fn=msgs.append)

        if self._runner and self._runner.poll() is None:
            try:
                # Wait for an in-flight circuit query rather than racing it —
                # but never forever: panic must not hang on a stuck worker.
                got = self._runner.lock.acquire(timeout=10)
                try:
                    self._runner.stdin.write(b"panic\n")
                    self._runner.stdin.flush()
                    while True:
                        raw = self._runner.stdout.readline()
                        if not raw:
                            break
                        line = raw.decode(errors="replace").rstrip()
                        if line:
                            msgs.append(line)
                        if "[PANIC] Connection closed." in line:
                            break
                finally:
                    if got:
                        self._runner.lock.release()
            except Exception as exc:
                msgs.append(f"[PANIC] Error: {exc}")
            finally:
                self._runner.close()
        else:
            # Runner not running — cannot remove firewall rules without root.
            # The next runner launch (on reconnect) runs _startup_heal() which
            # removes stale nftables tables automatically.
            msgs.append(
                "[PANIC] WARNING: Privileged process unavailable — "
                "firewall rules may still be active. "
                "Click CONNECT to trigger automatic cleanup on next startup."
            )

        self.done.emit("\n".join(msgs))


# ── Health check worker ──────────────────────────────────────

class _HealthCheckWorker(QThread):
    dropped = pyqtSignal(str, str)  # (service_name, reason_detail)

    def __init__(self, active_layers: list, runner_alive: bool):
        super().__init__()
        self._active_layers = active_layers
        self._runner_alive  = runner_alive

    @staticmethod
    def _is_active(service: str) -> str:
        """Return the raw systemctl is-active output for *service*."""
        r = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True,
        )
        return r.stdout.strip()

    @staticmethod
    def _last_log(service: str) -> str:
        """Return the last few journal lines for *service* (best-effort)."""
        try:
            r = subprocess.run(
                ["journalctl", "-u", service, "-n", "5", "--no-pager",
                 "--output=short"],
                capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _tor_socks_alive(port: int) -> bool:
        """Return True if Tor's SocksPort is accepting TCP connections."""
        import socket as _sock
        try:
            s = _sock.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            return True
        except OSError:
            return False

    def run(self) -> None:
        import time
        if not self._runner_alive:
            self.dropped.emit("__runner__", "Runner process exited.")
            return

        # Tor runs as a managed subprocess (not a systemd service) so we
        # detect crashes by probing its SocksPort directly.
        if "tor" in self._active_layers:
            from core.config import cfg_port as _cfg_port
            socks_port = _cfg_port("tor", "socks_port")
            if not self._tor_socks_alive(socks_port):
                time.sleep(3)
                if not self._tor_socks_alive(socks_port):
                    self.dropped.emit(
                        "tor",
                        f"Tor SocksPort :{socks_port} is unreachable — process may have crashed.",
                    )
                    return

        for layer, service in _SERVICE_MAP.items():
            if layer not in self._active_layers:
                continue
            state = self._is_active(service)
            if state == "active":
                continue
            # Not active — wait 3 s and recheck to filter out transient states
            # (e.g. "activating", "reloading") that resolve on their own.
            time.sleep(3)
            state = self._is_active(service)
            if state != "active":
                detail = self._last_log(service)
                self.dropped.emit(service, detail)
                return


# ── Circuit info worker ───────────────────────────────────────

class _CircuitInfoWorker(QThread):
    result = pyqtSignal(dict)

    def __init__(self, runner_proc: "DaemonClient"):
        super().__init__()
        self._runner = runner_proc

    def run(self) -> None:
        import json as _json
        try:
            if self._runner.poll() is not None:
                self.result.emit({})
                return
            # Non-blocking: if another exchange (disconnect, panic, new
            # circuit) holds the socket, skip this refresh tick.
            if not self._runner.lock.acquire(blocking=False):
                self.result.emit({})
                return
            try:
                self._runner.stdin.write(b"circuit_info\n")
                self._runner.stdin.flush()
                # The daemon may push unsolicited log lines (e.g. a Tor
                # warning) at any time; skip them instead of taking one as the
                # reply and leaving the real reply for the next command.
                for _ in range(50):
                    raw = self._runner.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode(errors="replace").rstrip()
                    if line.startswith("[CIRCUIT] "):
                        try:
                            self.result.emit(_json.loads(line[10:]))
                            return
                        except Exception:
                            break
            finally:
                self._runner.lock.release()
        except Exception:
            pass
        self.result.emit({})


# ── Leak test worker ──────────────────────────────────────────

class _LeakTestWorker(QThread):
    progress = pyqtSignal(str)
    done     = pyqtSignal(list)

    def __init__(self, socks_port: int, dns_port: int):
        super().__init__()
        self._socks = socks_port
        self._dns   = dns_port

    def run(self) -> None:
        from core.leak_test import LeakTester
        tester  = LeakTester(self._socks, self._dns)
        results = []
        tests = [
            tester.test_tor_exit,
            tester.test_dns_leak,
            tester.test_ipv6_leak,
            tester.test_webrtc_udp,
            tester.test_timezone,
            tester.test_hostname,
        ]
        for fn in tests:
            self.progress.emit(f"[LEAK TEST] Running: {fn.__name__.replace('test_', '').upper()}…")
            r = fn()
            results.append(r)
        self.done.emit(results)


# ── Update check worker ───────────────────────────────────────

class _UpdateCheckWorker(QThread):
    result = pyqtSignal(bool, str)

    def __init__(self, socks_port: int):
        super().__init__()
        self._socks = socks_port

    def run(self) -> None:
        from core.updater import check_for_update
        try:
            available, latest = check_for_update(self._socks)
            self.result.emit(available, latest)
        except Exception:
            self.result.emit(False, "")


# ── New circuit worker ────────────────────────────────────────

class _NewCircuitWorker(QThread):
    result = pyqtSignal(str)

    def __init__(self, runner_proc: "DaemonClient"):
        super().__init__()
        self._runner = runner_proc

    def run(self) -> None:
        try:
            if self._runner is None or self._runner.poll() is not None:
                raise RuntimeError("Privileged process is not running.")
            with self._runner.lock:
                self._runner.stdin.write(b"new_circuit\n")
                self._runner.stdin.flush()
                # Skip unsolicited log lines; the reply is one of these two.
                for _ in range(50):
                    raw = self._runner.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode(errors="replace").rstrip()
                    if (line.startswith("[TOR] New Tor circuit requested")
                            or line.startswith("[ERR]")):
                        self.result.emit(line)
                        return
            self.result.emit("[TOR] No response from circuit.")
        except Exception as e:
            self.result.emit(f"[TOR] Circuit renewal failed: {e}")


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

def _qc(rgb, a: float = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, int(a))))


class _GlowFrame(QWidget):
    """
    Central widget — the window's backdrop.  Paints:
      1. The theme's base gradient.
      2. A slowly drifting aurora (three soft colour fields) plus a status tint.
      3. The theme's signature effect (binary rain, PCB traces, pixel blocks).
      4. A hairline window border with a status-coloured glow.
    It also keeps a heavily down-sampled copy of 1-3 (``frost()``) that the
    GlassPanels draw scaled up — a real, in-window frosted-glass blur that does
    not depend on the desktop compositor.
    """
    _GLOW_W     = 6
    _FPS        = 30
    _FROST_DOWN = 4     # backdrop is re-rendered at 1/4 size …
    _FROST      = 16    # … then shrunk to 1/16: upscaling it back is the blur

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("glowFrame")
        self._rgb        = _glow_color("off")
        self._alpha      = 0.45
        self._glow_state = "off"

        self._blob_phase  = 0.0
        self._blob_phase2 = math.pi

        self._frost: QImage | None = None
        self._frost_key = None

        self._blob_timer = QTimer(self)
        self._blob_timer.timeout.connect(self._blob_tick)
        self._blob_timer.start(1000 // self._FPS)

    # Animate only while visible — the app spends most of its life in the tray.
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._blob_timer.start(1000 // self._FPS)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._blob_timer.stop()

    def _blob_tick(self) -> None:
        step = 2 * math.pi / (14 * self._FPS)      # one aurora cycle ≈ 14 s
        self._blob_phase  = (self._blob_phase  + step) % (2 * math.pi)
        self._blob_phase2 = (self._blob_phase2 + step) % (2 * math.pi)
        self.update()

    def set_color(self, rgb: tuple[int, int, int], alpha: float,
                  state: str = "") -> None:
        self._rgb        = rgb
        self._alpha      = alpha
        self._glow_state = state
        self.update()

    def frost(self) -> tuple["QImage | None", float]:
        return self._frost, float(self._FROST)

    def _window_radius(self) -> float:
        c = theme()
        return float(c["radius"] + (2 if c["name"] == "pixel" else 6))

    # ── backdrop ──────────────────────────────────────────────

    def _paint_backdrop(self, p: QPainter, w: float, h: float) -> None:
        c  = theme()
        ph = self._blob_phase
        full = QRectF(0, 0, w, h)

        base = QLinearGradient(0, 0, w * 0.3, h)
        base.setColorAt(0.0, _qc(c["bg_rgb"]))
        base.setColorAt(1.0, _qc(c["bg2_rgb"]))
        p.fillRect(full, base)

        # aurora: three soft colour fields drifting on slow Lissajous paths
        spots = (
            (0.16 + 0.10 * math.sin(ph),          0.10 + 0.06 * math.cos(ph * 0.8),  0.70),
            (0.88 + 0.07 * math.cos(ph * 0.9),    0.42 + 0.10 * math.sin(ph * 0.7),  0.62),
            (0.28 + 0.12 * math.sin(ph * 0.6 + 1), 0.88 + 0.05 * math.cos(ph),       0.72),
        )
        span = max(w, h)
        for (rgb, a), (fx, fy, fr) in zip(c["aurora"], spots):
            g = QRadialGradient(fx * w, fy * h, fr * span * 0.62)
            g.setColorAt(0.0, _qc(rgb, a))
            g.setColorAt(0.45, _qc(rgb, a * 0.38))
            g.setColorAt(1.0, _qc(rgb, 0))
            p.fillRect(full, g)

        name = c["name"]
        if name == "binary":
            self._draw_binary_bg(p, w, h)
        elif name == "circuit":
            self._draw_circuit_bg(p, w, h)
        elif name == "pixel":
            self._draw_pixel_bg(p, w, h)

    def _draw_binary_bg(self, p: QPainter, w: float, h: float) -> None:
        acc = theme()["accent"]
        font = QFont()
        font.setFamily("JetBrains Mono,Fira Code,Cascadia Code,DejaVu Sans Mono,monospace")
        font.setPixelSize(12)
        p.setFont(font)
        for nx, ny, ch, ph in _BINARY_CHARS:
            t = (math.sin(self._blob_phase * 2.2 + ph) + 1) / 2
            if t > 0.9:
                color = _qc(acc, 110 * t)
            elif t > 0.5:
                color = _qc(acc, 50 * t)
            else:
                color = _qc(acc, 16 * t)
            p.setPen(color)
            p.drawText(QPointF(nx * w, ny * h), ch)

    def _draw_circuit_bg(self, p: QPainter, w: float, h: float) -> None:
        """PCB backdrop: dot matrix, orthogonal traces with vias, data pulses."""
        tr, tg, tb = theme()["accent"]
        all_pts = [[(xf * w, yf * h) for xf, yf in trace] for trace in _CPU_TRACES]

        dot_sp = 26.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tr, tg, tb, 14))
        xd = dot_sp / 2
        while xd < w:
            yd = dot_sp / 2
            while yd < h:
                p.drawEllipse(QRectF(xd - 0.8, yd - 0.8, 1.6, 1.6))
                yd += dot_sp
            xd += dot_sp

        p.setBrush(Qt.BrushStyle.NoBrush)
        bus_alpha = [40, 32, 26] * 4
        for ti, pts in enumerate(all_pts):
            p.setPen(QPen(QColor(tr, tg, tb, bus_alpha[ti]), 2.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for i in range(len(pts) - 1):
                p.drawLine(QPointF(*pts[i]), QPointF(*pts[i + 1]))
        for ti, pts in enumerate(all_pts):
            a = bus_alpha[ti] + 26
            p.setPen(QPen(QColor(tr, tg, tb, a), 0.8))
            p.setBrush(QColor(tr, tg, tb, a - 14))
            for px_, py_ in pts:
                p.drawEllipse(QRectF(px_ - 3.5, py_ - 3.5, 7, 7))

        if self._glow_state not in ("on", "connecting"):
            return
        phase = self._blob_phase * 3
        cr, cg, cb = self._rgb
        n = len(all_pts)
        for ti, pts in enumerate(all_pts):
            seg = [math.hypot(pts[j + 1][0] - pts[j][0], pts[j + 1][1] - pts[j][1])
                   for j in range(len(pts) - 1)]
            total = sum(seg)
            if total < 1.0:
                continue
            dist = (((phase / (2 * math.pi)) + ti / n) % 1.0) * total
            cum, (px_, py_) = 0.0, pts[-1]
            for j, sl in enumerate(seg):
                if cum + sl >= dist:
                    f = (dist - cum) / sl if sl else 0.0
                    px_ = pts[j][0] + (pts[j + 1][0] - pts[j][0]) * f
                    py_ = pts[j][1] + (pts[j + 1][1] - pts[j][1]) * f
                    break
                cum += sl
            bright = 0.55 + 0.45 * math.sin(phase + ti * 0.88)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(cr, cg, cb, int(110 * bright)))
            p.drawEllipse(QRectF(px_ - 2, py_ - 2, 4, 4))

    def _draw_pixel_bg(self, p: QPainter, w: float, h: float) -> None:
        cell = 20
        cols = [rgb for rgb, _a in theme()["aurora"]]
        for nx, ny, ci, ph in _PIXEL_BLOCKS:
            t = (math.sin(self._blob_phase * 1.6 + ph) + 1) / 2
            if t < 0.25:
                continue
            x = (int(nx * (w - cell)) // cell) * cell
            y = (int(ny * (h - cell)) // cell) * cell
            p.fillRect(x, y, cell, cell, _qc(cols[ci % len(cols)], 6 + 22 * t))

    def _refresh_frost(self, w: int, h: int) -> None:
        key = (round(self._blob_phase, 4), w, h, theme()["name"],
               self._glow_state, round(self._alpha, 2))
        if key == self._frost_key and self._frost is not None:
            return
        d = self._FROST_DOWN
        small = QImage(max(1, w // d), max(1, h // d), QImage.Format.Format_ARGB32_Premultiplied)
        small.fill(Qt.GlobalColor.transparent)
        sp = QPainter(small)
        sp.setRenderHint(QPainter.RenderHint.Antialiasing)
        sp.scale(1 / d, 1 / d)
        self._paint_backdrop(sp, float(w), float(h))
        sp.end()
        f = self._FROST
        self._frost = small.scaled(max(1, w // f), max(1, h // f),
                                   Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        self._frost_key = key

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        r = self._window_radius()

        # Children (GlassPanels) paint after us in this same pass and read it.
        self._refresh_frost(self.width(), self.height())

        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.setClipPath(path)
        self._paint_backdrop(p, rect.width(), rect.height())
        p.setClipping(False)

        # neutral hairline window border
        gb = c["glass_border"]
        p.setPen(QPen(_qc(gb, min(255, gb[3] * 2)), 1.0))
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

    _LOGO_SIZE = 32
    _TOTAL_H   = 52

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self._win      = win
        self._drag_pos: QPoint | None = None

        self.setObjectName("appHeader")
        self.setFixedHeight(self._TOTAL_H)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(10)

        self._logo_lbl = QLabel()
        self._logo_lbl.setObjectName("logoLbl")
        self._logo_lbl.setFixedSize(self._LOGO_SIZE, self._LOGO_SIZE)
        self._logo_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_logo_pixmap(_logo_for_theme(theme()["name"]))
        lay.addWidget(self._logo_lbl)

        title = QLabel("Entropy Shield")
        title.setObjectName("appTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(title)
        lay.addWidget(maze_linux_tag("for Maze Linux"), alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch()

        btn_panic = QPushButton(" Panic")
        btn_panic.setIconSize(icons.qsize(15))
        self._btn_panic = btn_panic
        btn_panic.setObjectName("panicBtn")
        btn_panic.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_panic.setToolTip("Emergency disconnect — tears down every layer immediately")
        btn_panic.clicked.connect(win._on_panic)
        lay.addWidget(btn_panic)
        lay.addSpacing(2)

        self._icon_btns = []
        for name, tip, slot, role in (
            ("settings", "Settings",         win._open_settings, ""),
            ("minus",    "Minimize",         win.showMinimized,  ""),
            ("x",        "Minimize to tray", win.close,          "close"),
        ):
            b = QPushButton()
            b.setIconSize(icons.qsize(16))
            self._icon_btns.append((b, name))
            b.setObjectName("iconBtn")
            b.setProperty("role", role)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def refresh_icons(self) -> None:
        c = theme()
        self._btn_panic.setIcon(icons.icon("siren", c["red"]))
        for b, name in self._icon_btns:
            b.setIcon(icons.icon(name, c["text_muted"]))

    def _set_logo_pixmap(self, path: str) -> None:
        if path:
            src = QPixmap(path)
            if not src.isNull():
                dpr = self.devicePixelRatioF() or 1.0
                pix = src.scaled(int(self._LOGO_SIZE * dpr), int(self._LOGO_SIZE * dpr),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                pix.setDevicePixelRatio(dpr)
                self._logo_lbl.setPixmap(pix)
                return
        self._logo_lbl.clear()

    def update_logo(self, theme_name: str) -> None:
        self._set_logo_pixmap(_logo_for_theme(theme_name))

    # ── drag handling ─────────────────────────────────────────

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self._win.windowHandle()
            if handle:
                try:
                    handle.startSystemMove()
                    e.accept()
                    return
                except Exception:
                    pass
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


class _ConnectButton(QPushButton):
    """The primary action; hosts the progress spinner inside its left edge."""

    def __init__(self, text: str):
        super().__init__(text)
        self.spinner = Spinner(18, self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        s = self.spinner
        s.move(18, (self.height() - s.height()) // 2)


_LAYER_NAMES = {"tor": "Tor", "dnscrypt": "DNSCrypt", "i2p": "I2P",
                "onion_server": "Onion Server"}


def _layer_label(layers) -> str:
    return "  ·  ".join(_LAYER_NAMES.get(l, l) for l in layers)


def _title_case(text: str) -> str:
    """'CONNECTING...' → 'Connecting...'; already mixed-case text is kept."""
    return " ".join(w.capitalize() if w.isupper() else w for w in text.split(" "))


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

        # Fit smaller screens (1366×768 laptops): the log panel absorbs the
        # difference and can be collapsed.
        self.setMinimumSize(520, 700)
        avail = QGuiApplication.primaryScreen().availableGeometry() \
            if QGuiApplication.primaryScreen() else None
        h = min(900, avail.height() - 40) if avail else 860
        self.resize(560, max(700, h))

        self._connected      = False
        self._worker:          _Worker | None            = None
        self._ip_worker:       _IpCheckWorker | None     = None
        self._nc_worker:       _NewCircuitWorker | None  = None
        self._panic_worker:    _PanicWorker | None       = None
        self._leak_worker:     _LeakTestWorker | None    = None
        self._update_worker:   _UpdateCheckWorker | None = None
        self._circuit_worker:  _CircuitInfoWorker | None = None
        self._health_worker:   _HealthCheckWorker | None = None
        self._runner_proc:     "DaemonClient | None"     = None
        self._active_layers:   list[str]                 = []

        # Connection metadata for richer status display
        self._connect_time:    datetime.datetime | None  = None
        self._exit_country:    str                       = ""
        self._circuit_count:   int                       = 0
        self._reconnect_attempts: int                    = 0
        self._tray_proc: subprocess.Popen | None = None
        self._tray_username: str = ""
        self._quitting: bool = False
        self._reconnect_layers: dict | None = None  # set when a layer toggle triggers reconnect

        # Debounce timer: merges rapid card toggles into one reconnect
        self._layer_change_timer = QTimer(self)
        self._layer_change_timer.setSingleShot(True)
        self._layer_change_timer.setInterval(400)
        self._layer_change_timer.timeout.connect(self._apply_layer_change)

        self._glow_state  = "off"
        self._glow_rgb    = _glow_color("off")
        self._glow_alpha  = 0.45
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10_000)
        self._health_timer.timeout.connect(self._check_service_health)

        # Reconnect countdown timer (fires every second when reconnecting)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(1_000)
        self._reconnect_timer.timeout.connect(self._reconnect_tick)
        self._reconnect_countdown: int = 0

        # Status subtitle update timer (connection duration + circuit info)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5_000)
        self._status_timer.timeout.connect(self._refresh_status_sub)

        # Circuit info refresh (every 30s when connected via Tor)
        self._circuit_timer = QTimer(self)
        self._circuit_timer.setInterval(30_000)
        self._circuit_timer.timeout.connect(self._refresh_circuit_info)

        # Auto circuit renewal (interval set from config on connect)
        self._auto_circuit_timer = QTimer(self)
        self._auto_circuit_timer.timeout.connect(self._on_auto_circuit_renewal)
        self._nc_is_auto: bool = False

        self._apply_theme()
        self._build_ui()

        self._settings = SettingsPanel(self._glow_frame)
        self._settings.saved.connect(self._on_settings_saved)

        self._build_tray()
        self._append_log(f"[>] Entropy Shield v{VERSION} ready.")

        # Warn early (before the user even clicks Connect) if the privileged
        # daemon is unreachable — most commonly because the user has not logged
        # out/in since install, so their 'entropy-shield' group membership is
        # not active yet.  Deferred so the window paints first.
        QTimer.singleShot(600, self._check_daemon_ready)

        # The update check runs once per app session, through Tor, right after
        # the first successful Tor connect (see _on_worker_done) — never at
        # startup over the clearnet, where it told GitHub and anyone on the
        # path that this IP runs Entropy Shield.
        self._update_checked = False

        if cfg().get("autostart"):
            if autostart.enable():
                self._append_log("[>] Autostart enabled.")
        else:
            autostart.disable()

        if cfg().get("auto_connect"):
            QTimer.singleShot(1600, self._auto_connect)

    def _check_daemon_ready(self) -> None:
        """Surface a clear warning if the privileged daemon can't be reached.

        Runs once shortly after launch so the user is told *why* Connect would
        fail (re-login needed, or daemon not started) instead of only finding
        out when they click Connect.
        """
        try:
            client = DaemonClient.connect(timeout=2.0)
            client.close()
            return  # daemon reachable — all good, stay quiet
        except DaemonError as exc:
            msg = str(exc)

        if "group" in msg:
            self._append_log(
                "[!] Privileged daemon unreachable — you are not in the "
                "'entropy-shield' group yet. Log out and back in (or reboot), "
                "then reopen Entropy Shield. Connect will fail until then.")
            self._set_status("error", "RE-LOGIN NEEDED",
                             "Not in 'entropy-shield' group yet")
        else:
            self._append_log(
                "[!] Privileged daemon is not running. Start it with:  "
                "sudo systemctl enable --now entropy-shield")
            self._set_status("error", "DAEMON OFFLINE",
                             "Privileged backend unreachable")

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

    def _notify(self, title: str, body: str, ms: int = 3500) -> None:
        """Send a desktop notification via the tray helper."""
        self._tray_send(f"notify:{title}|{body}|{ms}")

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
        self._refresh_icons()
        if logo_path:
            self.setWindowIcon(_make_window_icon(logo_path))
        self._tray_send(f"icon:{logo_path}")
        self._append_log(f"[>] Settings saved. Theme: {theme_name}.")

        # Reconfigure auto circuit renewal if currently connected via Tor
        renewal = cfg().get("circuit_renewal_minutes")
        if self._connected and "tor" in self._active_layers:
            if renewal > 0:
                self._auto_circuit_timer.setInterval(renewal * 60_000)
                self._auto_circuit_timer.start()
            else:
                self._auto_circuit_timer.stop()

    # ── build UI ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._glow_frame = _GlowFrame()
        self.setCentralWidget(self._glow_frame)
        set_backdrop(self._glow_frame)

        content = QVBoxLayout(self._glow_frame)
        content.setContentsMargins(16, 8, 16, 16)
        content.setSpacing(12)
        self._content = content

        self._title_bar = _TitleBar(self)
        content.addWidget(self._title_bar)
        content.addWidget(self._build_status_area())
        content.addWidget(self._build_cards())
        content.addWidget(self._build_connect_row())
        content.addWidget(self._build_browser_row())
        content.addWidget(self._build_log(), stretch=1)
        content.addStretch(0)   # takes the space when the log is collapsed
        self._refresh_icons()

    def _build_status_area(self) -> QWidget:
        hero = GlassPanel()
        col = QVBoxLayout(hero)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(18)
        self._status_ring = StatusRing(96)
        self._status_ring.set_state("off")
        top.addWidget(self._status_ring)

        text = QVBoxLayout()
        text.setSpacing(4)
        badge_row = QHBoxLayout()
        self._status_badge = QLabel("NOT PROTECTED")
        self._status_badge.setObjectName("statusBadge")
        self._status_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        badge_row.addWidget(self._status_badge)
        badge_row.addStretch()

        self._status_lbl = QLabel("Disconnected")
        self._status_lbl.setObjectName("statusTitle")
        self._status_lbl.setProperty("state", "off")

        self._status_sub = QLabel("Select layers and connect")
        self._status_sub.setObjectName("statusSub")
        self._status_sub.setWordWrap(True)

        text.addStretch()
        text.addLayout(badge_row)
        text.addWidget(self._status_lbl)
        text.addWidget(self._status_sub)
        text.addStretch()
        top.addLayout(text, stretch=1)
        col.addLayout(top)

        self._speed_bar = NetSpeedGraph()
        col.addWidget(self._speed_bar)
        return hero

    def _build_cards(self) -> QWidget:
        panel = GlassPanel()
        col = QVBoxLayout(panel)
        col.setContentsMargins(10, 12, 10, 8)
        col.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(8, 0, 8, 6)
        title = QLabel("PRIVACY LAYERS")
        title.setObjectName("sectionTitle")
        hint = QLabel("Choose layers, then connect")
        hint.setObjectName("sectionHint")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(hint)
        col.addLayout(hdr)

        self._card_tor   = LayerRow("tor")
        self._card_dns   = LayerRow("dnscrypt")
        self._card_i2p   = LayerRow("i2p")
        self._card_onion = LayerRow("onion_server", checked=False)
        cards = (self._card_tor, self._card_dns, self._card_i2p, self._card_onion)
        for i, card in enumerate(cards):
            if i:
                div = QFrame()
                div.setObjectName("layerDivider")
                div.setFixedHeight(1)
                wrap = QHBoxLayout()
                wrap.setContentsMargins(58, 0, 8, 0)
                wrap.addWidget(div)
                col.addLayout(wrap)
            card.settings_clicked.connect(self._open_settings_tab)
            col.addWidget(card)
        self._card_tor.toggled.connect(self._on_tor_toggled)
        self._card_onion.toggled.connect(self._on_onion_toggled)
        for card in cards:
            card.toggled.connect(self._on_layer_toggled)
        return panel

    def _build_connect_row(self) -> QWidget:
        self._connect_btn = _ConnectButton("Connect")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.setCheckable(True)
        self._connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._connect_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._spinner = self._connect_btn.spinner
        return self._connect_btn

    def _tool_button(self, text: str, tip: str, slot, icon: str,
                     enabled: bool = False) -> QPushButton:
        b = QPushButton("  " + text)
        b.setProperty("icon_name", icon)
        b.setIconSize(icons.qsize(17))
        b.setObjectName("toolBtn")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setEnabled(enabled)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return b

    def _build_browser_row(self) -> QWidget:
        panel = GlassPanel()
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self._tor_browser_btn = self._tool_button(
            "Tor Browser",
            "Isolated, hardened browser profile routed through Tor.\n"
            "Supports Firefox, Chromium, Brave.", self._on_open_tor_browser,
            "brand-torbrowser")
        self._i2p_browser_btn = self._tool_button(
            "I2P Browser",
            "Isolated browser profile routed through I2P.\n"
            "Supports Firefox, Chromium, Brave.", self._on_open_i2p_browser, "globe")
        self._proxy_terminal_btn = self._tool_button(
            "Terminal",
            "Open a terminal with Tor proxy variables pre-set.\n"
            "curl http://abc.onion works there. No system files are changed.",
            self._on_open_proxy_terminal, "square-terminal")
        self._ip_check_btn = self._tool_button(
            "IP Check", "Show your public IP as seen through Tor (needs curl).",
            self._on_ip_check, "map-pin")
        self._new_circuit_btn = self._tool_button(
            "New Circuit",
            "Request a new Tor identity (SIGNAL NEWNYM): new exit node,\n"
            "fresh stream isolation.", self._on_new_circuit, "refresh-cw")
        self._leak_test_btn = self._tool_button(
            "Leak Test",
            "Run DNS, IPv6, WebRTC/UDP, timezone and hostname leak tests.",
            self._on_leak_test, "shield-check")

        self._tool_btns = (self._tor_browser_btn, self._i2p_browser_btn,
                           self._proxy_terminal_btn, self._ip_check_btn,
                           self._new_circuit_btn, self._leak_test_btn)
        for i, b in enumerate(self._tool_btns):
            grid.addWidget(b, i // 3, i % 3)
        return panel

    def _build_log(self) -> QWidget:
        panel = GlassPanel()
        self._log_panel = panel
        col = QVBoxLayout(panel)
        col.setContentsMargins(16, 10, 10, 12)
        col.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl = QLabel("ACTIVITY")
        lbl.setObjectName("sectionTitle")
        self._log_count = QLabel("0")
        self._log_count.setObjectName("countBadge")
        self._log_preview = QLabel("")
        self._log_preview.setObjectName("logPreview")
        self._log_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._log_preview.hide()

        exp_btn = QPushButton(" Export")
        exp_btn.setIconSize(icons.qsize(14))
        self._log_export = exp_btn
        exp_btn.setObjectName("ghostBtn")
        exp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exp_btn.setToolTip("Save the activity log to a file in your home folder.")
        exp_btn.clicked.connect(self._export_log)

        self._log_toggle = QPushButton(" Hide")
        self._log_toggle.setIconSize(icons.qsize(14))
        self._log_toggle.setObjectName("ghostBtn")
        self._log_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_toggle.clicked.connect(self._toggle_log)

        hdr.addWidget(lbl)
        hdr.addWidget(self._log_count)
        hdr.addWidget(self._log_preview, stretch=1)
        hdr.addStretch()
        hdr.addWidget(exp_btn)
        hdr.addWidget(self._log_toggle)

        self._log_widget = QTextEdit()
        self._log_widget.setObjectName("log")
        self._log_widget.setReadOnly(True)
        self._log_widget.setMinimumHeight(80)
        self._log_lines = 0
        self._log_last = ""

        col.addLayout(hdr)
        col.addWidget(self._log_widget, stretch=1)
        return panel

    def _refresh_icons(self) -> None:
        """(Re)tint every icon for the current theme."""
        c = theme()
        self._title_bar.refresh_icons()
        for b in self._tool_btns:
            b.setIcon(icons.icon(b.property("icon_name"), c["text"], disabled=c["text_dim"]))
        self._log_export.setIcon(icons.icon("download", c["text_muted"]))
        open_ = self._log_widget.isVisible() or not self.isVisible()
        self._log_toggle.setIcon(icons.icon(
            "chevron-down" if open_ else "chevron-right", c["text_muted"]))

    def _toggle_log(self) -> None:
        show = not self._log_widget.isVisible()
        self._log_widget.setVisible(show)
        self._log_preview.setVisible(not show)
        self._log_toggle.setText(" Hide" if show else " Show")
        self._refresh_icons()
        idx = self._content.indexOf(self._log_panel)
        self._content.setStretch(idx, 1 if show else 0)
        self._content.setStretch(self._content.count() - 1, 0 if show else 1)
        self._update_log_preview()

    def _update_log_preview(self) -> None:
        fm = self._log_preview.fontMetrics()
        w = max(40, self._log_preview.width())
        self._log_preview.setText(
            fm.elidedText(self._log_last, Qt.TextElideMode.ElideRight, w))

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

    def _on_layer_toggled(self, _tag: str, _checked: bool) -> None:
        """Called whenever any service card is toggled. Triggers reconnect if connected."""
        if not self._connected or self._worker is not None:
            return
        self._layer_change_timer.start()  # debounce: wait 400ms before acting

    def _apply_layer_change(self) -> None:
        """Reconnect with the current card selection after a layer toggle while connected."""
        if not self._connected or self._worker is not None:
            return
        layers = {
            "use_tor":          self._card_tor.is_checked,
            "use_dnscrypt":     self._card_dns.is_checked,
            "use_i2p":          self._card_i2p.is_checked,
            "use_onion_server": self._card_onion.is_checked,
        }
        new_active = sorted(k.replace("use_", "") for k, v in layers.items() if v)
        if new_active == sorted(self._active_layers):
            return  # no actual change (e.g. dependency auto-toggle)
        if not any(layers.values()):
            self._append_log("[>] All layers disabled — disconnecting…")
            self._start_worker("disconnect", {})
            return
        self._reconnect_layers = layers
        added   = [l for l in new_active if l not in self._active_layers]
        removed = [l for l in self._active_layers if l not in new_active]
        parts   = []
        if added:   parts.append("+" + ", ".join(l.upper() for l in added))
        if removed: parts.append("−" + ", ".join(l.upper() for l in removed))
        self._append_log(f"[>] Layer change ({', '.join(parts)}) — reconnecting…")
        self._start_worker("disconnect", {})

    # ── status ────────────────────────────────────────────────

    def _set_status(self, state: str, title: str, sub: str) -> None:
        badge = {
            "off":        "NOT PROTECTED",
            "connecting": "WORKING",
            "on":         "PROTECTED",
            "error":      "ATTENTION",
        }
        self._status_ring.set_state(state)
        self._status_lbl.setText(_title_case(title))
        self._status_badge.setText(badge.get(state, "NOT PROTECTED"))
        for w in (self._status_lbl, self._status_badge):
            w.setProperty("state", state)
            w.style().unpolish(w)
            w.style().polish(w)
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
            "Connecting…" if action == "connect" else "Disconnecting…")
        self._spinner.start()
        self._set_cards_enabled(False)
        self._set_glow("connecting")
        if action == "connect":
            self._set_status("connecting", "CONNECTING...",
                             "Routing through selected layers…")
            for card in self._active_cards():
                card.set_status("connecting")

        runner = self._runner_proc if action == "disconnect" else None
        self._worker = _Worker(action, layers, runner)
        self._worker.log.connect(self._append_log)
        self._worker.done.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_done(self, success: bool, info: str) -> None:
        self._spinner.stop()
        self._connect_btn.setEnabled(True)
        self._set_cards_enabled(True)
        w, self._worker = self._worker, None

        # Capture runner reference BEFORE deleteLater
        if success and info == "connect" and w is not None:
            self._runner_proc = getattr(w, "new_runner", None)
        elif info in ("disconnect",) or not success:
            if info == "disconnect" or not success:
                # A failed connect still leaves an open daemon session (the
                # daemon keeps it alive so the rollback can finish).  Close it
                # explicitly instead of dropping the last reference and relying
                # on the garbage collector to release root-held state.
                stale = self._runner_proc or (
                    getattr(w, "new_runner", None) if w is not None else None)
                if stale is not None:
                    try:
                        stale.close()
                    except Exception:
                        pass
                self._runner_proc = None

        if w is not None:
            w.deleteLater()

        if self._quitting:
            self._do_quit()
            return

        if success and info == "connect":
            self._connected = True
            self._connect_btn.setChecked(True)
            self._connect_btn.setText("Disconnect")
            self._set_glow("on")
            layers_str = _layer_label(self._active_layers)
            self._set_status("on", "PROTECTED", layers_str)
            for card in self._active_cards():
                card.set_status("active")
            tor_on = "tor" in self._active_layers
            self._tor_browser_btn.setEnabled(tor_on)
            self._i2p_browser_btn.setEnabled("i2p" in self._active_layers)
            self._proxy_terminal_btn.setEnabled(tor_on)
            self._ip_check_btn.setEnabled(tor_on)
            self._new_circuit_btn.setEnabled(tor_on)
            self._leak_test_btn.setEnabled(True)
            self._speed_bar.set_active(True)
            self._tray_send("connected")
            layers_label = "  ·  ".join(l.upper() for l in self._active_layers)
            self._notify("Connected", layers_label)
            self._connect_time    = datetime.datetime.now()
            self._reconnect_attempts = 0
            if tor_on and not self._update_checked:
                self._update_checked = True
                QTimer.singleShot(5000, self._run_update_check)
            self._exit_country    = ""
            self._circuit_count   = 0
            if cfg().get("kill_switch"):
                self._health_timer.start()
            self._status_timer.start()
            if tor_on:
                self._circuit_timer.start()
                QTimer.singleShot(3000, self._refresh_circuit_info)
                renewal = cfg().get("circuit_renewal_minutes")
                if renewal > 0:
                    self._auto_circuit_timer.setInterval(renewal * 60_000)
                    self._auto_circuit_timer.start()

        elif success and info == "disconnect":
            self._connected = False
            self._health_timer.stop()
            self._status_timer.stop()
            self._circuit_timer.stop()
            self._auto_circuit_timer.stop()
            self._connect_time  = None
            self._exit_country  = ""
            self._circuit_count = 0

            # Layer-change reconnect: skip the "disconnected" UI and go straight
            # back to connecting with the new layer set.
            if self._reconnect_layers is not None:
                layers = self._reconnect_layers
                self._reconnect_layers = None
                self._active_layers = [
                    k.replace("use_", "") for k, v in layers.items() if v]
                self._set_glow("connecting")
                self._set_status("connecting", "RECONNECTING…", "Applying layer changes…")
                self._connect_btn.setText("Connecting…")
                self._connect_btn.setChecked(True)
                QTimer.singleShot(200, lambda: self._start_worker("connect", layers))
                return

            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("Connect")
            self._set_glow("off")
            self._set_status("off", "DISCONNECTED", "Select layers and connect")
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_onion):
                card.set_status("")
            self._tor_browser_btn.setEnabled(False)
            self._i2p_browser_btn.setEnabled(False)
            self._proxy_terminal_btn.setEnabled(False)
            self._ip_check_btn.setEnabled(False)
            self._new_circuit_btn.setEnabled(False)
            self._leak_test_btn.setEnabled(False)
            self._speed_bar.set_active(False)
            self._tray_send("disconnected")

        else:
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("Connect")
            self._set_glow("error")
            short = info[:70] if len(info) > 70 else info
            self._set_status("error", "ERROR", short)
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_onion):
                card.set_status("error" if card.is_checked else "")
            self._tor_browser_btn.setEnabled(False)
            self._i2p_browser_btn.setEnabled(False)
            self._proxy_terminal_btn.setEnabled(False)
            self._ip_check_btn.setEnabled(False)
            self._new_circuit_btn.setEnabled(False)
            self._leak_test_btn.setEnabled(False)
            self._speed_bar.set_active(False)
            self._health_timer.stop()
            self._status_timer.stop()
            self._circuit_timer.stop()
            self._auto_circuit_timer.stop()
            self._connect_time   = None
            self._exit_country   = ""
            self._circuit_count  = 0
            self._tray_send("disconnected")
            self._append_log(info if info.startswith("[ERR]") else f"[ERR] {info}")

    # ── kill switch ───────────────────────────────────────────

    def _check_service_health(self) -> None:
        if not self._connected or self._worker is not None:
            return
        if self._health_worker and self._health_worker.isRunning():
            return

        runner_alive = (
            self._runner_proc is not None and self._runner_proc.poll() is None
        )
        self._health_worker = _HealthCheckWorker(
            list(self._active_layers), runner_alive
        )
        self._health_worker.dropped.connect(self._on_health_dropped)
        self._health_worker.start()

    def _on_health_dropped(self, service: str, detail: str) -> None:
        self._health_timer.stop()
        if service == "__runner__":
            self._append_log(
                "[!] KILL SWITCH: Privileged runner exited unexpectedly. "
                "Firewall rules may still be active — reconnect or reboot to clear.")
            self._runner_proc = None
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("Connect")
            self._set_glow("error")
            self._set_status("error", "ERROR", "Runner died — reconnect to restore")
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_onion):
                card.set_status("")
            self._tor_browser_btn.setEnabled(False)
            self._i2p_browser_btn.setEnabled(False)
            self._proxy_terminal_btn.setEnabled(False)
            self._ip_check_btn.setEnabled(False)
            self._new_circuit_btn.setEnabled(False)
            self._leak_test_btn.setEnabled(False)
            self._speed_bar.set_active(False)
            self._status_timer.stop()
            self._circuit_timer.stop()
            self._auto_circuit_timer.stop()
            self._tray_send("disconnected")
        else:
            self._append_log(
                f"[!] KILL SWITCH: {service} dropped — emergency disconnect triggered.")
            if detail:
                for ln in detail.splitlines()[-5:]:
                    if ln.strip():
                        self._append_log(f"[!] {ln.strip()}")
            self._notify("Kill Switch", f"{service} dropped — emergency disconnect", 5000)
            self._start_worker("disconnect", {})
            self._maybe_schedule_reconnect()

    def _maybe_schedule_reconnect(self) -> None:
        """Schedule auto-reconnect if the feature is enabled."""
        ar = cfg().get("auto_reconnect")
        if not ar.get("enabled", True):
            return
        max_attempts = ar.get("max_attempts", 3)
        if self._reconnect_attempts >= max_attempts:
            self._append_log(
                f"[!] Auto-reconnect: max attempts ({max_attempts}) reached. "
                "Reconnect manually.")
            return
        delay = ar.get("delay_seconds", 15)
        self._reconnect_countdown = delay
        self._reconnect_attempts += 1
        self._append_log(
            f"[>] Auto-reconnect in {delay}s "
            f"(attempt {self._reconnect_attempts}/{max_attempts})…")
        self._reconnect_timer.start()

    def _reconnect_tick(self) -> None:
        self._reconnect_countdown -= 1
        if self._reconnect_countdown <= 0:
            # If disconnect worker is still running, wait a couple more seconds
            # before attempting reconnect so we start from a clean state.
            if self._worker is not None:
                self._reconnect_countdown = 2
                return
            self._reconnect_timer.stop()
            if not self._connected:
                self._append_log("[>] Auto-reconnect: connecting…")
                self._auto_connect()
        else:
            self._set_status(
                "connecting",
                "AUTO-RECONNECT",
                f"Reconnecting in {self._reconnect_countdown}s…",
            )

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
        self._ip_worker = _IpCheckWorker(cfg_port("tor", "socks_port"))
        self._ip_worker.result.connect(self._on_ip_result)
        self._ip_worker.start()

    def _on_ip_result(self, msg: str) -> None:
        self._append_log(msg)
        if self._connected and "tor" in self._active_layers:
            self._ip_check_btn.setEnabled(True)

    # ── richer status ─────────────────────────────────────────

    def _refresh_status_sub(self) -> None:
        """Update status subtitle with duration, exit country, circuit count."""
        if not self._connected or not self._connect_time:
            return
        elapsed = datetime.datetime.now() - self._connect_time
        h, rem  = divmod(int(elapsed.total_seconds()), 3600)
        m, s    = divmod(rem, 60)
        dur     = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        parts = [_layer_label(self._active_layers)]

        info_parts = []
        if "tor" in self._active_layers:
            if self._exit_country:
                info_parts.append(f"Exit {self._exit_country}")
            if self._circuit_count:
                info_parts.append(f"{self._circuit_count} circuits")
        if info_parts:
            parts.extend(info_parts)
        parts.append(dur)

        self._status_sub.setText("  ·  ".join(parts))

    def _refresh_circuit_info(self) -> None:
        # Skip if any worker is active — avoids stdout race during connect/disconnect.
        # Also skip if new_circuit is in progress: both workers read the same stdout
        # pipe and interleaved reads would corrupt both responses.
        if not self._connected or not self._runner_proc or self._worker is not None:
            return
        if self._circuit_worker and self._circuit_worker.isRunning():
            return
        if self._nc_worker and self._nc_worker.isRunning():
            return
        self._circuit_worker = _CircuitInfoWorker(self._runner_proc)
        self._circuit_worker.result.connect(self._on_circuit_info)
        self._circuit_worker.start()

    def _on_circuit_info(self, info: dict) -> None:
        if not info:
            return
        self._circuit_count = info.get("circuit_count", 0)
        cc = info.get("exit_country", "")
        if cc and cc != self._exit_country:
            self._exit_country = cc
            if self._connected:
                self._append_log(f"[TOR] Exit country: {cc}")
        self._refresh_status_sub()

    # ── panic ─────────────────────────────────────────────────

    def _on_panic(self) -> None:
        if self._panic_worker and self._panic_worker.isRunning():
            return
        self._append_log("[PANIC] Emergency disconnect triggered!")
        self._set_glow("error")
        self._set_status("error", "PANIC", "Emergency disconnect…")
        self._panic_worker = _PanicWorker(self._runner_proc)
        self._panic_worker.done.connect(self._on_panic_done)
        self._panic_worker.start()

    def _on_panic_done(self, msg: str) -> None:
        for line in msg.splitlines():
            if line.strip():
                self._append_log(line)
        # Stop any running workers / UI elements
        self._spinner.stop()
        self._set_cards_enabled(True)
        if self._worker is not None:
            try:
                self._worker.terminate()
            except Exception:
                pass
            self._worker = None
        # Reset all state
        self._connected      = False
        self._active_layers  = []
        self._runner_proc    = None
        self._connect_time   = None
        self._exit_country   = ""
        self._circuit_count  = 0
        self._connect_btn.setChecked(False)
        self._connect_btn.setText("Connect")
        self._connect_btn.setEnabled(True)
        self._set_glow("off")
        self._set_status("off", "DISCONNECTED", "Select layers and connect")
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_onion):
            card.set_status("")
        self._tor_browser_btn.setEnabled(False)
        self._i2p_browser_btn.setEnabled(False)
        self._proxy_terminal_btn.setEnabled(False)
        self._ip_check_btn.setEnabled(False)
        self._new_circuit_btn.setEnabled(False)
        self._leak_test_btn.setEnabled(False)
        self._speed_bar.set_active(False)
        self._health_timer.stop()
        self._status_timer.stop()
        self._circuit_timer.stop()
        self._auto_circuit_timer.stop()
        self._reconnect_timer.stop()
        self._tray_send("disconnected")
        self._append_log("[PANIC] Emergency cleanup complete.")

    # ── leak test ─────────────────────────────────────────────

    def _on_leak_test(self) -> None:
        if self._leak_worker and self._leak_worker.isRunning():
            return
        self._leak_test_btn.setEnabled(False)
        self._append_log("[LEAK TEST] Starting tests…")
        socks = cfg_port("tor", "socks_port") if "tor" in self._active_layers else 9050
        dns   = cfg_port("tor", "dns_port")   if "tor" in self._active_layers else 5300
        self._leak_worker = _LeakTestWorker(socks, dns)
        self._leak_worker.progress.connect(self._append_log)
        self._leak_worker.done.connect(self._on_leak_done)
        self._leak_worker.start()

    def _on_leak_done(self, results: list) -> None:
        passed = sum(1 for r in results if r.passed)
        total  = len(results)
        self._append_log(f"[LEAK TEST] ── Results: {passed}/{total} passed ──")
        for r in results:
            icon = "✓" if r.passed else "✗"
            self._append_log(f"[LEAK TEST]  {icon}  {r.name}: {r.message}")
            if r.detail:
                self._append_log(f"[LEAK TEST]      {r.detail}")
        if passed == total:
            self._append_log("[LEAK TEST] All tests passed — no leaks detected.")
        else:
            self._append_log(f"[LEAK TEST] WARNING: {total - passed} test(s) failed!")
        if self._connected:
            self._leak_test_btn.setEnabled(True)

    # ── update check ──────────────────────────────────────────

    def _run_update_check(self) -> None:
        if not cfg().get("update_check"):
            return
        if not (self._connected and "tor" in self._active_layers):
            return
        self._update_worker = _UpdateCheckWorker(cfg_port("tor", "socks_port"))
        self._update_worker.result.connect(self._on_update_result)
        self._update_worker.start()

    def _on_update_result(self, available: bool, latest: str) -> None:
        if available:
            self._append_log(
                f"[UPDATE] New version available: v{latest}  "
                "(github.com/berk-kucuk/entropy-shield)"
            )

    # ── new circuit ───────────────────────────────────────────

    def _on_auto_circuit_renewal(self) -> None:
        self._on_new_circuit(auto=True)

    def _on_new_circuit(self, auto: bool = False) -> None:
        if self._nc_worker and self._nc_worker.isRunning():
            return
        # Also block if circuit_info is in flight — both read the same stdout pipe.
        if self._circuit_worker and self._circuit_worker.isRunning():
            return
        if not self._runner_proc or self._runner_proc.poll() is not None:
            self._append_log("[!] Privileged process is not running — cannot renew circuit.")
            return
        self._nc_is_auto = auto
        self._new_circuit_btn.setEnabled(False)
        self._append_log("[TOR] Requesting new circuit…")
        self._nc_worker = _NewCircuitWorker(self._runner_proc)
        self._nc_worker.result.connect(self._on_nc_result)
        self._nc_worker.start()

    def _on_nc_result(self, msg: str) -> None:
        self._append_log(msg)
        if self._nc_is_auto and "[ERR]" not in msg.upper():
            self._notify("New Circuit", "Tor identity rotated — new IP active")
        if self._connected and "tor" in self._active_layers:
            self._new_circuit_btn.setEnabled(True)

    # ── log export ────────────────────────────────────────────

    def _export_log(self) -> None:
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.home() / f"entropy-shield-log-{ts}.txt"
        try:
            # The log holds the .onion address, exit country and timing —
            # owner-only, not the umask's usual world-readable 0644.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._log_widget.toPlainText())
            self._append_log(f"[>] Log exported → {path}")
        except Exception as e:
            self._append_log(f"[!] Export failed: {e}")

    # ── browsers ──────────────────────────────────────────────

    def _on_open_tor_browser(self) -> None:
        self._tor_browser_btn.setEnabled(False)
        try:
            browser.launch_tor(cfg_port("tor", "socks_port"), self._append_log)
        except Exception as exc:
            self._append_log(f"[BROWSER] {exc}")
        finally:
            QTimer.singleShot(3000, lambda: self._tor_browser_btn.setEnabled(
                self._connected and "tor" in self._active_layers))

    def _on_open_i2p_browser(self) -> None:
        self._i2p_browser_btn.setEnabled(False)
        try:
            browser.launch_i2p(
                cfg_port("i2p", "http_port"),
                cfg_port("i2p", "socks_port"),
                self._append_log,
            )
        except Exception as exc:
            self._append_log(f"[BROWSER] {exc}")
        finally:
            QTimer.singleShot(3000, lambda: self._i2p_browser_btn.setEnabled(
                self._connected and "i2p" in self._active_layers))

    def _on_open_proxy_terminal(self) -> None:
        self._proxy_terminal_btn.setEnabled(False)
        try:
            browser.launch_proxy_terminal(
                cfg_port("tor", "socks_port"), self._append_log)
        except Exception as exc:
            self._append_log(f"[TERMINAL] {exc}")
        finally:
            QTimer.singleShot(3000, lambda: self._proxy_terminal_btn.setEnabled(
                self._connected and "tor" in self._active_layers))

    # ── helpers ───────────────────────────────────────────────

    def _active_cards(self) -> list[LayerRow]:
        return [c for c in (self._card_tor, self._card_dns,
                             self._card_i2p, self._card_onion)
                if c.is_checked]

    def _set_cards_enabled(self, enabled: bool) -> None:
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_onion):
            card.set_enabled_ui(enabled)

    _LOG_TAG = re.compile(r"^(\[[A-Z0-9 _>!-]+\])(.*)$", re.DOTALL)

    def _append_log(self, msg: str) -> None:
        c = theme()
        # Monochrome: problems are bright and bold, successes bright, the rest grey.
        upper = msg.upper()
        weight = "400"
        if any(k in upper for k in ("[ERR]", "[!]", "ERROR", "FAILED", "✗",
                                    "WARNING", "KILL SWITCH", "[PANIC]")):
            body, weight = c["text"], "700"
        elif "[OK]" in upper or "✓" in msg:
            body = c["text"]
        else:
            body = c["log_fg"]

        tag, rest = "", msg
        m = self._LOG_TAG.match(msg)
        if m:
            tag, rest = m.group(1), m.group(2)
        tag_col = c["text_muted"]

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        esc = lambda s: html.escape(s).replace("\n", "<br>")
        line = (f'<span style="color:{c["text_dim"]}">{ts}</span>&nbsp;&nbsp;'
                + (f'<span style="color:{tag_col};font-weight:600">{esc(tag)}</span>'
                   if tag else "")
                + f'<span style="color:{body};font-weight:{weight};white-space:pre-wrap">{esc(rest)}</span>')
        self._log_widget.append(line)
        self._log_widget.moveCursor(QTextCursor.MoveOperation.End)

        self._log_lines += 1
        self._log_count.setText(str(self._log_lines))
        self._log_last = msg.replace("\n", " ")
        if self._log_preview.isVisible():
            self._update_log_preview()

    # ── system tray ───────────────────────────────────────────

    def _build_tray(self) -> None:
        tray_logo = _logo_for_theme(cfg().get("theme"))
        try:
            self._tray_proc = subprocess.Popen(
                [sys.executable, _TRAY_HELPER, tray_logo],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            self._append_log(f"[!] Tray launch failed: {exc}")
            return

        # The helper logs to stderr on every tray click, and Qt adds its own
        # warnings.  Read the pipe continuously (keeping the tail for the
        # diagnostics below) — left unread it fills up after ~64 KB and the
        # helper blocks on write(), freezing the tray icon.
        import collections, threading
        self._tray_err_tail: "collections.deque[str]" = collections.deque(maxlen=40)

        def _drain_tray_stderr(pipe, tail) -> None:
            try:
                for raw in pipe:
                    tail.append(raw.decode(errors="replace").rstrip())
            except (OSError, ValueError):
                pass
        threading.Thread(target=_drain_tray_stderr,
                         args=(self._tray_proc.stderr, self._tray_err_tail),
                         daemon=True).start()

        self._tray_username = os.environ.get("USER", "")
        self._tray_poll = QTimer(self)
        self._tray_poll.timeout.connect(self._poll_tray)
        self._tray_poll.start(100)
        QTimer.singleShot(600, self._check_tray_startup)

    def _check_tray_startup(self) -> None:
        if not self._tray_proc:
            return
        if self._tray_proc.poll() is not None:
            err = "\n".join(self._tray_err_tail).strip()
            self._append_log(f"[!] Tray exited early: {err or '(no output)'}")
            self._tray_proc = None
            self._tray_poll.stop()
        else:
            self._append_log(f"[>] Tray started ({self._tray_username}).")
            self._tray_send("connected" if self._connected else "disconnected")

    def _poll_tray(self) -> None:
        if not self._tray_proc:
            return

        # Always drain stdout before checking if the proc is dead.
        # On Wayland the tray helper can crash immediately after writing
        # "quit", so the message may sit in the pipe buffer while poll()
        # already reports the process as gone.
        try:
            ready, _, _ = select.select([self._tray_proc.stdout], [], [], 0)
            if ready:
                line = self._tray_proc.stdout.readline().decode(
                    errors="replace").strip()
                if   line == "show":       self._tray_show()
                elif line == "connect":    self._tray_connect()
                elif line == "disconnect": self._tray_disconnect()
                elif line == "panic":      self._on_panic()
                elif line == "quit":       self._tray_quit()
        except Exception:
            pass

        if not self._tray_proc:
            return

        if self._tray_proc.poll() is not None:
            err = "\n".join(self._tray_err_tail).strip()
            if err:
                self._append_log(f"[!] Tray: {err}")
            self._tray_proc = None
            self._tray_poll.stop()
            if self._quitting:
                import os as _os
                _os._exit(0)

    def _tray_send(self, cmd: str) -> None:
        if self._tray_proc and self._tray_proc.poll() is None:
            try:
                self._tray_proc.stdin.write((cmd + "\n").encode())
                self._tray_proc.stdin.flush()
            except Exception:
                pass

    def _do_quit(self) -> None:
        import os as _os
        self._tray_send("ack_quit")
        if self._tray_proc:
            try:
                self._tray_proc.wait(timeout=1)
            except Exception:
                try:
                    self._tray_proc.kill()
                except Exception:
                    pass
            self._tray_proc = None
        _os._exit(0)

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


def maze_linux_tag(text: str):
    """The small "for Maze Linux" chip beside the app name (styled by QSS)."""
    tag = QLabel(text)
    tag.setObjectName("mazeLinuxTag")
    tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    tag.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return tag
