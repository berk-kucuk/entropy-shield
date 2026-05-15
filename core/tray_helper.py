#!/usr/bin/env python3
"""
tray_helper.py — Entropy Shield sistem tray yardımcısı
Ana süreç (root) tarafından orijinal kullanıcı kimliğiyle subprocess
olarak başlatılır.

Protokol (stdin/stdout):
  Ana süreç → helper : "notify" | "ack_quit"
  Helper → ana süreç : "show"   | "disconnect" | "quit"
"""
from __future__ import annotations
import sys, os, threading

# ── Platform plugin seçimi ────────────────────────────────────────────────
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


class TrayHelper(QObject):
    def __init__(self, icon_path: str):
        super().__init__()
        icon = (QIcon(icon_path) if icon_path and os.path.exists(icon_path)
                else QIcon.fromTheme("network-vpn"))

        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("Entropy Shield")

        self._menu = QMenu()
        a = QAction("Göster / Gizle");  a.triggered.connect(self._send_show);       self._menu.addAction(a)
        self._menu.addSeparator()
        b = QAction("Bağlantıyı Kes"); b.triggered.connect(self._send_disconnect); self._menu.addAction(b)
        self._menu.addSeparator()
        c = QAction("Çıkış");           c.triggered.connect(self._send_quit);        self._menu.addAction(c)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._activated)
        self._tray.show()

        QTimer.singleShot(1000, self._check)

        self._reader = _StdinReader()
        self._reader.command.connect(self._on_cmd)
        self._reader.start()

    def _check(self):
        vis = self._tray.isVisible()
        sys.stderr.write(f"[tray_helper] tray visible={vis}\n")
        sys.stderr.flush()

    def _activated(self, r):
        sys.stderr.write(f"[tray_helper] activated reason={r}\n")
        sys.stderr.flush()
        # Context: X11 ve bazı Wayland compositorlarda tetiklenir.
        # Unknown: bazı DE'ler tanımsız reason gönderebilir.
        if r in (QSystemTrayIcon.ActivationReason.Context,
                 QSystemTrayIcon.ActivationReason.Unknown):
            self._show_menu()
        elif r in (QSystemTrayIcon.ActivationReason.Trigger,
                   QSystemTrayIcon.ActivationReason.DoubleClick):
            self._send_show()

    def _show_menu(self) -> None:
        # Wayland'da QCursor.pos() (0,0) döner; tray ikon geometrisini kullan.
        geo = self._tray.geometry()
        if geo.isValid() and not geo.isEmpty():
            pos = geo.topLeft()
        else:
            pos = QCursor.pos()
        self._menu.popup(pos)

    def _send_show(self):
        sys.stdout.write("show\n"); sys.stdout.flush()

    def _send_disconnect(self):
        sys.stdout.write("disconnect\n"); sys.stdout.flush()

    def _send_quit(self):
        self._tray.hide()
        sys.stdout.write("quit\n"); sys.stdout.flush()
        QTimer.singleShot(8000, QApplication.quit)

    def _on_cmd(self, cmd: str):
        if cmd == "notify":
            self._tray.showMessage(
                "Entropy Shield",
                "Arka planda çalışıyor. Tray ikonuna tıklayın.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )
        elif cmd == "ack_quit":
            self._tray.hide()
            QApplication.quit()


def main():
    icon_path = sys.argv[1] if len(sys.argv) > 1 else ""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.stderr.write("[tray_helper] WARN: isSystemTrayAvailable=False, devam ediliyor\n")
        sys.stderr.flush()

    helper = TrayHelper(icon_path)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
