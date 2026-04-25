from __future__ import annotations
import sys
import os
import math
import pwd
import select
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QTextCursor, QPainter, QColor, QPen, QIcon, QPixmap,
)

import gui.themes as themes
from gui.themes import current as theme, build_qss
from gui.widgets import ServiceCard, Spinner, StatusRing
from gui.settings_panel import SettingsPanel
from core.connection import ConnectionManager
from core.config import cfg

# Logo path — relative to this file (gui/) → parent → logos/
_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logos", "entropy-logo.png",
)

_TRAY_HELPER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "tray_helper.py",
)

_GLOW_COLORS = {
    "off":        (210, 55,  48),
    "connecting": (210, 153, 50),
    "on":         (44,  186, 92),
    "error":      (210, 55,  48),
}


# ── worker ────────────────────────────────────────────────────

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
        self._mgr._log      = emit
        self._mgr._tor._log = emit
        self._mgr._dns._log = emit
        self._mgr._i2p._log = emit
        self._mgr._loki._log = emit
        self._mgr._fw._log  = emit
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


# ── inner glow frame ─────────────────────────────────────────

class _GlowFrame(QWidget):
    """
    Full-area container that paints a colour-shifting glow border
    spreading inward from its edges.  Child widgets live inside
    with enough margin that the glow is always visible.
    """

    _GLOW_W = 10   # pixels of glow zone on each edge
    _RADIUS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgb   = _GLOW_COLORS["off"]
        self._alpha = 0.45

    def set_color(self, rgb: tuple[int, int, int], alpha: float) -> None:
        self._rgb   = rgb
        self._alpha = alpha
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r, g, b = self._rgb
        alpha   = self._alpha
        rect    = self.rect()
        rad     = self._RADIUS
        gw      = self._GLOW_W

        # Spread glow rings inward
        for i in range(gw, 0, -1):
            factor = ((gw - i + 1) / gw) ** 2.0
            a = int(alpha * 180 * factor)
            p.setPen(QPen(QColor(r, g, b, a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(
                rect.adjusted(i, i, -i, -i),
                max(2, rad - i // 2), max(2, rad - i // 2),
            )

        # Crisp colour border on the outer edge
        p.setPen(QPen(QColor(r, g, b, int(230 * alpha)), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), rad, rad)


# ── main window ───────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Entropy Shield")

        # Window icon → shows in taskbar / panel / alt-tab
        if os.path.exists(_LOGO_PATH):
            self.setWindowIcon(QIcon(_LOGO_PATH))

        self.setMinimumSize(540, 620)
        self.resize(540, 620)

        self._connected     = False
        self._worker: _Worker | None = None
        self._mgr           = ConnectionManager(lambda _: None)
        self._active_layers: list[str] = []
        self._tray_proc: subprocess.Popen | None = None

        # Glow animation state
        self._glow_state  = "off"
        self._glow_rgb    = _GLOW_COLORS["off"]
        self._glow_alpha  = 0.45
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)

        self._apply_theme()
        self._build_ui()

        self._settings = SettingsPanel(self._glow_frame)
        self._settings.saved.connect(self._on_settings_saved)

        self._build_tray()
        self._append_log("[>] Entropy Shield ready.")

    # ── glow ──────────────────────────────────────────────────

    def _set_glow(self, state: str) -> None:
        self._glow_state = state
        self._glow_rgb   = _GLOW_COLORS.get(state, _GLOW_COLORS["off"])
        if state == "connecting":
            self._pulse_phase = 0.0
            self._pulse_timer.start(28)
        else:
            self._pulse_timer.stop()
            self._glow_alpha = 0.85 if state == "on" else 0.45
            self._glow_frame.set_color(self._glow_rgb, self._glow_alpha)
            self._status_ring.set_pulse(0.7)
        self._glow_frame.set_color(self._glow_rgb, self._glow_alpha)

    def _pulse_step(self) -> None:
        self._pulse_phase = (self._pulse_phase + 0.07) % (2 * math.pi)
        t = (math.sin(self._pulse_phase) + 1) / 2
        self._glow_alpha = 0.25 + 0.65 * t
        self._glow_frame.set_color(self._glow_rgb, self._glow_alpha)
        self._status_ring.set_pulse(t)

    # ── theme ─────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        themes.set_theme(cfg().get("theme"))
        self.setStyleSheet(build_qss(theme()))

    def _on_settings_saved(self) -> None:
        self._apply_theme()
        self._append_log(f"[>] Settings saved. Theme: {cfg().get('theme')}.")

    # ── build UI ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Outer wrapper gives the glow frame a little breathing room
        outer = QWidget()
        outer.setObjectName("outerBg")
        self.setCentralWidget(outer)

        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(10, 10, 10, 10)
        outer_lay.setSpacing(0)

        self._glow_frame = _GlowFrame()
        outer_lay.addWidget(self._glow_frame)

        content = QVBoxLayout(self._glow_frame)
        pad = _GlowFrame._GLOW_W + 6
        content.setContentsMargins(pad, pad, pad, pad)
        content.setSpacing(0)

        content.addWidget(self._build_header())
        content.addSpacing(16)
        content.addLayout(self._build_status_area())
        content.addSpacing(14)
        content.addLayout(self._build_cards())
        content.addSpacing(12)
        content.addWidget(self._build_connect_row())
        content.addSpacing(10)
        content.addWidget(self._build_log(), stretch=1)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("appHeader")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(10)

        # Logo
        if os.path.exists(_LOGO_PATH):
            pix = QPixmap(_LOGO_PATH).scaled(
                36, 36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_lbl = QLabel()
            logo_lbl.setPixmap(pix)
            logo_lbl.setFixedSize(36, 36)
            logo_lbl.setObjectName("logoLbl")
            lay.addWidget(logo_lbl)

        title_lbl = QLabel("ENTROPY SHIELD")
        title_lbl.setObjectName("appTitle")
        lay.addWidget(title_lbl)
        lay.addStretch()

        btn_settings = QPushButton("⚙")
        btn_settings.setObjectName("settingsBtn")
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.clicked.connect(self._open_settings)
        lay.addWidget(btn_settings)

        return bar

    def _build_status_area(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)

        ring_row = QHBoxLayout()
        ring_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._status_ring = StatusRing(100)
        self._status_ring.set_state("off")
        ring_row.addWidget(self._status_ring)

        self._status_lbl = QLabel("DISCONNECTED")
        self._status_lbl.setObjectName("statusTitle")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_sub = QLabel("Select layers and connect")
        self._status_sub.setObjectName("statusSub")
        self._status_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        col.addLayout(ring_row)
        col.addWidget(self._status_lbl)
        col.addWidget(self._status_sub)
        return col

    def _build_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self._card_tor   = ServiceCard("tor")
        self._card_dns   = ServiceCard("dnscrypt")
        self._card_i2p   = ServiceCard("i2p")
        self._card_loki  = ServiceCard("lokinet")
        for card in (self._card_tor, self._card_dns, self._card_i2p, self._card_loki):
            card.settings_clicked.connect(self._open_settings_tab)
            row.addWidget(card)
        return row

    def _build_connect_row(self) -> QWidget:
        w = QWidget()
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

    def _build_log(self) -> QTextEdit:
        self._log = QTextEdit()
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(108)
        return self._log

    # ── settings ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        self._settings.setGeometry(self._glow_frame.rect())
        self._settings.open()

    def _open_settings_tab(self, tag: str) -> None:
        tab_map = {"tor": 0, "dnscrypt": 1, "i2p": 2, "lokinet": 3}
        self._settings._tabs.setCurrentIndex(tab_map.get(tag, 0))
        self._open_settings()

    # ── status helpers ────────────────────────────────────────

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
            f"font-size:15px;font-weight:700;letter-spacing:4px;"
            f"background:transparent;"
        )
        self._status_sub.setText(sub)

    # ── connect / disconnect ───────────────────────────────────

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self._start_worker("disconnect", {})
        else:
            layers = {
                "use_tor":      self._card_tor.is_checked,
                "use_dnscrypt": self._card_dns.is_checked,
                "use_i2p":      self._card_i2p.is_checked,
                "use_lokinet":  self._card_loki.is_checked,
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

        if success and info == "connect":
            self._connected = True
            self._connect_btn.setChecked(True)
            self._connect_btn.setText("DISCONNECT")
            self._set_glow("on")
            layers_str = "  ·  ".join(l.upper() for l in self._active_layers)
            self._set_status("on", "PROTECTED", layers_str)
            for card in self._active_cards():
                card.set_status("active")

        elif success and info == "disconnect":
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("CONNECT")
            self._set_glow("off")
            self._set_status("off", "DISCONNECTED", "Select layers and connect")
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_loki):
                card.set_status("")

        else:
            self._connected = False
            self._active_layers = []
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("CONNECT")
            self._set_glow("error")
            short = info[:70] if len(info) > 70 else info
            self._set_status("error", "ERROR", short)
            for card in (self._card_tor, self._card_dns,
                         self._card_i2p, self._card_loki):
                card.set_status("error" if card.is_checked else "")
            self._append_log(f"[ERR] {info}")

    # ── helpers ───────────────────────────────────────────────

    def _active_cards(self) -> list[ServiceCard]:
        return [c for c in (self._card_tor, self._card_dns,
                             self._card_i2p, self._card_loki)
                if c.is_checked]

    def _set_cards_enabled(self, enabled: bool) -> None:
        for card in (self._card_tor, self._card_dns,
                     self._card_i2p, self._card_loki):
            card.set_enabled_ui(enabled)

    def _append_log(self, msg: str) -> None:
        self._log.append(msg)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    # ── system tray ───────────────────────────────────────────────────
    #
    # KDE Plasma modern DE'ler SNI (StatusNotifierItem) protokolünü kullanır.
    # Qt'nin QSystemTrayIcon'u SNI'yi D-Bus üzerinden register eder; ancak
    # uygulama root olarak çalışırken kendi D-Bus session'ı yoktur.
    #
    # Çözüm: tray_helper.py'yi orijinal kullanıcı kimliğiyle subprocess
    # olarak başlatmak. Helper SNI ikonunu kendi session D-Bus'ına kaydeder;
    # ana süreçle stdin/stdout pipe üzerinden haberleşir.
    # ─────────────────────────────────────────────────────────────────

    def _original_uid(self) -> int | None:
        """pkexec/sudo öncesi orijinal kullanıcının UID'ini döndür."""
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
        """
        Kullanıcının oturum ortam değişkenlerini topla.
        Önce loginctl/systemctl üzerinden dener (root'tan erişilebilir),
        sonra /proc taramasına düşer.
        """
        runtime_dir = f"/run/user/{uid}"
        env: dict[str, str] = {
            "HOME":                   user_home,
            "USER":                   username,
            "LOGNAME":                username,
            "XDG_RUNTIME_DIR":        runtime_dir,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
            "PATH":                   os.environ.get("PATH", "/usr/bin:/bin"),
        }

        # ── 1. loginctl show-session → DISPLAY / WAYLAND_DISPLAY ──
        # loginctl, oturum değişkenlerini root'tan da okuyabilir.
        try:
            session_id = None
            out = subprocess.check_output(
                ["loginctl", "list-sessions", "--no-legend"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.split()
                # SESSION  UID  USER  SEAT  TTY
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
                    elif line.startswith("Type="):
                        val = line.split("=", 1)[1].strip()
                        if val == "wayland":
                            # Wayland oturumu — display soketi tahmin et
                            wd = os.environ.get("WAYLAND_DISPLAY", "")
                            if not wd:
                                # Kullanıcının runtime_dir'inde wayland-* soketi ara
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

        # ── 2. systemctl --user show-environment (machinectl aracılığıyla) ──
        if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
            try:
                out = subprocess.check_output(
                    ["machinectl", "shell", f"{username}@",
                     "/usr/bin/env"],
                    text=True, stderr=subprocess.DEVNULL, timeout=3,
                )
                for line in out.splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        if k in ("DISPLAY", "WAYLAND_DISPLAY",
                                 "DBUS_SESSION_BUS_ADDRESS",
                                 "XDG_SESSION_TYPE"):
                            env[k] = v.strip()
            except Exception:
                pass

        # ── 3. /proc taraması — root /proc okuyabiliyorsa ──────────
        if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
            try:
                for pid_s in os.listdir("/proc"):
                    if not pid_s.isdigit():
                        continue
                    try:
                        stat_path = f"/proc/{pid_s}/status"
                        status = open(stat_path).read()
                        if f"\nUid:\t{uid}\t" not in status:
                            continue
                        raw = open(f"/proc/{pid_s}/environ", "rb").read()
                        for item in raw.split(b"\x00"):
                            if b"=" not in item:
                                continue
                            k, _, v = item.partition(b"=")
                            key = k.decode(errors="replace")
                            if key in ("DISPLAY", "WAYLAND_DISPLAY",
                                       "DBUS_SESSION_BUS_ADDRESS",
                                       "XDG_SESSION_TYPE",
                                       "XDG_RUNTIME_DIR"):
                                env.setdefault(key, v.decode(errors="replace"))
                        if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
                            break
                    except (PermissionError, FileNotFoundError, ProcessLookupError):
                        continue
            except Exception:
                pass

        # ── 4. Wayland socket fallback ──────────────────────────────
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
        """
        tray_helper.py'yi orijinal kullanıcı olarak subprocess başlat.
        Helper SNI ikonunu kendi D-Bus session'ına kaydeder →
        KDE Plasma sistem tepsisinde görünür.
        """
        uid = self._original_uid()
        if uid is None:
            self._append_log("[!] Tray disabled.")
            return

        try:
            pw = pwd.getpwuid(uid)
        except KeyError:
            self._append_log(f"[!] UID {uid} user not found.")
            return

        username = pw.pw_name
        env      = self._user_env(uid, username, pw.pw_dir)

        self._append_log(
            f"[>] Tray env: DBUS={env.get('DBUS_SESSION_BUS_ADDRESS', '?')[:40]} "
            f"DISPLAY={env.get('DISPLAY', '?')} "
            f"WAYLAND={env.get('WAYLAND_DISPLAY', '?')}"
        )

        launched = False
        for cmd in (
            ["runuser", "-u", username, "--",
             sys.executable, _TRAY_HELPER, _LOGO_PATH],
            ["su", "-s", "/bin/sh", username, "-c",
             f"{sys.executable} {_TRAY_HELPER} {_LOGO_PATH}"],
        ):
            try:
                self._tray_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                launched = True
                break
            except FileNotFoundError:
                continue
            except Exception as exc:
                self._append_log(f"[!] Tray helper error. : {exc}")
                return

        if not launched:
            self._append_log("[!] runuser/su bulunamadı — tray disabled.")
            return

        # Helper'ın başlayıp başlamadığını 600 ms bekle
        import time
        time.sleep(0.6)
        if self._tray_proc.poll() is not None:
            err = self._tray_proc.stderr.read().decode(errors="replace").strip()
            self._append_log(f"[!] Tray helper closed. Hata: {err or '(yok)'}")
            self._tray_proc = None
            return

        self._tray_poll = QTimer(self)
        self._tray_poll.timeout.connect(self._poll_tray)
        self._tray_poll.start(100)
        self._append_log(f"[>] Tray helper started: ({username}).")

    def _poll_tray(self) -> None:
        """Helper process'ten gelen show/quit mesajlarını oku."""
        if not self._tray_proc:
            return
        if self._tray_proc.poll() is not None:
            try:
                err = self._tray_proc.stderr.read().decode(errors="replace").strip()
                if err:
                    self._append_log(f"[!] Tray helper output: {err}")
            except Exception:
                pass
            self._tray_proc = None
            self._tray_poll.stop()
            return

        ready, _, _ = select.select([self._tray_proc.stdout], [], [], 0)
        if not ready:
            return
        try:
            line = self._tray_proc.stdout.readline().decode(errors="replace").strip()
        except Exception:
            return

        if line == "show":
            self._tray_show()
        elif line == "quit":
            self._tray_quit()

    def _tray_send(self, cmd: str) -> None:
        if self._tray_proc and self._tray_proc.poll() is None:
            try:
                self._tray_proc.stdin.write((cmd + "\n").encode())
                self._tray_proc.stdin.flush()
            except Exception:
                pass

    def _tray_show(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self) -> None:
        if self._connected:
            try:
                self._mgr.disconnect()
            except Exception:
                pass
        self._tray_send("quit")
        if self._tray_proc:
            try:
                self._tray_proc.wait(timeout=2)
            except Exception:
                self._tray_proc.kill()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    # ── close event ───────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """X butonuna basılınca kapat değil, tray'e / taskbar'a küçült."""
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
