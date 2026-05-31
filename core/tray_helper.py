#!/usr/bin/env python3
"""
tray_helper.py — Entropy Shield system tray helper.
Launched as a subprocess by the main process (root) running as the real user.

Protocol  main → helper : "notify" | "ack_quit" | "connected" | "disconnected"
Protocol  helper → main : "show"   | "connect"  | "disconnect" | "quit"
"""
from __future__ import annotations
import sys
import os
import threading

# ── Platform plugin selection ─────────────────────────────────────────────────
if not os.environ.get("QT_QPA_PLATFORM"):
    runtime_dir  = os.environ.get("XDG_RUNTIME_DIR", "")
    wayland_disp = os.environ.get("WAYLAND_DISPLAY", "")

    if not wayland_disp and runtime_dir and os.path.isdir(runtime_dir):
        for _f in os.listdir(runtime_dir):
            if _f.startswith("wayland-") and not _f.endswith(".lock"):
                wayland_disp = _f
                os.environ["WAYLAND_DISPLAY"] = _f
                break

    os.environ["QT_QPA_PLATFORM"] = "wayland" if wayland_disp else "xcb"

sys.stderr.write(
    f"[tray_helper] platform={os.environ.get('QT_QPA_PLATFORM')} "
    f"WAYLAND={os.environ.get('WAYLAND_DISPLAY', '?')} "
    f"DISPLAY={os.environ.get('DISPLAY', '?')} "
    f"DBUS={os.environ.get('DBUS_SESSION_BUS_ADDRESS', '?')}\n"
)
sys.stderr.flush()

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui     import QIcon, QAction, QCursor
from PyQt6.QtCore    import QTimer, pyqtSignal, QObject


class _StdinReader(QObject):
    command = pyqtSignal(str)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            for raw in sys.stdin:
                cmd = raw.strip()
                if cmd:
                    self.command.emit(cmd)
        except Exception:
            pass


def _load_icon(path: str) -> QIcon:
    """Build a multi-resolution QIcon from a (possibly landscape) logo file."""
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import Qt as _Qt
    icon = QIcon()
    if path and os.path.exists(path):
        src = QPixmap(path)
        if not src.isNull():
            # Center-crop to square so tray icons look clean
            side = min(src.width(), src.height())
            x    = (src.width()  - side) // 2
            y    = (src.height() - side) // 2
            sq   = src.copy(x, y, side, side)
            for sz in (16, 22, 24, 32, 48, 64, 128, 256):
                icon.addPixmap(sq.scaled(
                    sz, sz,
                    _Qt.AspectRatioMode.IgnoreAspectRatio,
                    _Qt.TransformationMode.SmoothTransformation,
                ))
            return icon
    return QIcon.fromTheme("network-vpn")


class TrayHelper(QObject):
    def __init__(self, icon_path: str):
        super().__init__()
        self._connected = False

        icon = _load_icon(icon_path)

        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("Entropy Shield — Disconnected")

        # ── context menu ──────────────────────────────────────────
        self._menu = QMenu()

        # Keep all actions as instance vars — PyQt6 may GC local QAction refs
        self._act_show = QAction("Show / Hide")
        self._act_show.triggered.connect(self._send_show)
        self._menu.addAction(self._act_show)

        self._menu.addSeparator()

        # Dynamic connect/disconnect action — label updated on state change
        self._conn_action = QAction("Connect")
        self._conn_action.triggered.connect(self._send_connect_toggle)
        self._menu.addAction(self._conn_action)

        self._act_panic = QAction("☢  Emergency Disconnect")
        self._act_panic.triggered.connect(self._send_panic)
        self._menu.addAction(self._act_panic)

        self._menu.addSeparator()

        self._act_quit = QAction("Quit")
        self._act_quit.triggered.connect(self._send_quit)
        self._menu.addAction(self._act_quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._activated)
        self._tray.show()

        QTimer.singleShot(1000, self._check)

        self._reader = _StdinReader()
        self._reader.command.connect(self._on_cmd)
        self._reader.start()

    # ── tray checks ───────────────────────────────────────────────

    def _check(self):
        sys.stderr.write(f"[tray_helper] tray visible={self._tray.isVisible()}\n")
        sys.stderr.flush()

    def _activated(self, r):
        sys.stderr.write(f"[tray_helper] activated reason={r}\n")
        sys.stderr.flush()
        # Show menu on every activation (left-click, right-click, middle, unknown).
        # This guarantees Quit is always reachable regardless of DE/platform.
        self._show_menu()

    def _show_menu(self) -> None:
        # QCursor.pos() is the most reliable cross-platform position.
        self._menu.popup(QCursor.pos())

    # ── outgoing messages (helper → main) ─────────────────────────

    def _send_show(self):
        sys.stdout.write("show\n")
        sys.stdout.flush()

    def _send_connect_toggle(self):
        if self._connected:
            sys.stdout.write("disconnect\n")
        else:
            sys.stdout.write("connect\n")
        sys.stdout.flush()

    def _send_panic(self):
        sys.stdout.write("panic\n")
        sys.stdout.flush()

    def _send_quit(self):
        self._tray.hide()
        sys.stdout.write("quit\n")
        sys.stdout.flush()
        QTimer.singleShot(8000, QApplication.quit)

    # ── incoming messages (main → helper) ─────────────────────────

    def _on_cmd(self, cmd: str):
        if cmd == "connected":
            self._connected = True
            self._conn_action.setText("Disconnect")
            self._tray.setToolTip("Entropy Shield — Connected")

        elif cmd == "disconnected":
            self._connected = False
            self._conn_action.setText("Connect")
            self._tray.setToolTip("Entropy Shield — Disconnected")

        elif cmd == "notify":
            self._tray.showMessage(
                "Entropy Shield",
                "Running in the background. Click the tray icon to restore.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )

        elif cmd.startswith("icon:"):
            self._tray.setIcon(_load_icon(cmd[5:]))

        elif cmd == "ack_quit":
            self._tray.hide()
            QApplication.quit()


def main():
    icon_path = sys.argv[1] if len(sys.argv) > 1 else ""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.stderr.write("[tray_helper] WARN: isSystemTrayAvailable=False, continuing\n")
        sys.stderr.flush()

    helper = TrayHelper(icon_path)  # noqa: F841 — kept alive by app event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
