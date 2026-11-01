"""Run any app through Tor: the picker dialog and the "running" panel.

Neither needs Entropy Shield to be connected. An app run from here lives in
a network namespace whose only way out is Tor (core/torify), so it works on
its own — and it stays fail-closed: if Tor stops, the app has no network.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.torify import apps
from core.torify.launcher import request
from gui.widgets import GlassPanel

# Exit countries are shown as codes with a name for the common ones; Tor
# reports ISO 3166 codes, and a code is better than a guess.
_COUNTRIES = {
    "DE": "Germany", "NL": "Netherlands", "FR": "France", "US": "United States",
    "SE": "Sweden", "CH": "Switzerland", "FI": "Finland", "GB": "United Kingdom",
    "RO": "Romania", "AT": "Austria", "CA": "Canada", "NO": "Norway",
    "LU": "Luxembourg", "PL": "Poland", "IS": "Iceland", "CZ": "Czechia",
    "BG": "Bulgaria", "DK": "Denmark", "ES": "Spain", "IT": "Italy",
}


def _ago(t: float) -> str:
    m = int((time.time() - t) // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min"


class RunAppDialog(QDialog):
    """Pick an installed app; it starts through Tor when the dialog closes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("torifyDialog")
        self.setWindowTitle("Run an app through Tor")
        self.resize(460, 560)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        title = QLabel("RUN AN APP THROUGH TOR")
        title.setObjectName("sectionTitle")
        v.addWidget(title)
        note = QLabel("The app gets no network of its own: everything it sends goes "
                      "through Tor, or nowhere. Links it opens in another app do not.")
        note.setObjectName("sectionHint")
        note.setWordWrap(True)
        v.addWidget(note)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search apps…")
        self._search.textChanged.connect(self._filter)
        v.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("appList")
        self._list.itemDoubleClicked.connect(lambda _i: self._run())
        v.addWidget(self._list, 1)
        for app in apps.installed_apps():
            item = QListWidgetItem(app.name)
            icon = QIcon.fromTheme(app.icon) if app.icon and "/" not in app.icon \
                else QIcon(app.icon) if app.icon else QIcon()
            item.setIcon(icon)
            item.setToolTip(app.comment or app.path)
            item.setData(Qt.ItemDataRole.UserRole, app.path)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("toolBtn")
        cancel.clicked.connect(self.reject)
        go = QPushButton("Run through Tor")
        go.setObjectName("connectBtn")
        go.setStyleSheet("min-height: 40px; padding: 0 22px; font-size: 13px;")
        go.clicked.connect(self._run)
        row.addWidget(cancel)
        row.addWidget(go)
        v.addLayout(row)
        self._search.setFocus()

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        first = None
        for i in range(self._list.count()):
            item = self._list.item(i)
            hidden = bool(text) and text not in item.text().lower()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = item
        if first is not None:
            self._list.setCurrentItem(first)

    def _run(self) -> None:
        item = self._list.currentItem()
        if item is None or item.isHidden():
            return
        apps.spawn(["--desktop", item.data(Qt.ItemDataRole.UserRole)])
        self.accept()


class _ListWorker(QThread):
    result = pyqtSignal(list)

    def run(self) -> None:
        try:
            reply = request({"op": "list"})
            self.result.emit(reply.get("sessions") or [])
        except Exception:  # noqa: BLE001 — a refresh tick must never raise
            self.result.emit([])


class TorifyPanel(GlassPanel):
    """The apps running through Tor right now. Hidden while there are none."""

    def __init__(self, log=None):
        super().__init__()
        self._log = log or (lambda _m: None)
        self._worker: _ListWorker | None = None
        self._rows: dict[str, tuple[QWidget, QLabel]] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 12, 12, 10)
        v.setSpacing(4)
        hdr = QHBoxLayout()
        title = QLabel("RUNNING THROUGH TOR")
        title.setObjectName("sectionTitle")
        hint = QLabel("each app on its own circuit")
        hint.setObjectName("sectionHint")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(hint)
        v.addLayout(hdr)
        self._body = QVBoxLayout()
        self._body.setSpacing(0)
        v.addLayout(self._body)
        self.setVisible(False)

        self._timer = QTimer(self, interval=3000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(500, self.refresh)

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _ListWorker()
        self._worker.result.connect(self._show)
        self._worker.start()

    def _row(self, s: dict) -> tuple[QWidget, QLabel]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(s.get("app", "app"))
        name.setObjectName("layerName")
        desc = QLabel()
        desc.setObjectName("layerDesc")
        text.addWidget(name)
        text.addWidget(desc)
        h.addLayout(text, 1)
        btn = QPushButton("New identity")
        btn.setObjectName("toolBtn")
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.setToolTip("New Tor circuits for this app only. Connections it already\n"
                       "has stay on the old circuit until it reconnects.")
        sid = s.get("session", "")
        btn.clicked.connect(lambda: self._new_identity(sid, s.get("app", "app")))
        h.addWidget(btn)
        return w, desc

    def _show(self, sessions: list) -> None:
        live = {s.get("session"): s for s in sessions if isinstance(s, dict)}
        for sid in list(self._rows):
            if sid not in live:
                w, _ = self._rows.pop(sid)
                w.setParent(None)
                w.deleteLater()
        for sid, s in live.items():
            if sid not in self._rows:
                self._rows[sid] = self._row(s)
                self._body.addWidget(self._rows[sid][0])
            exit_ = s.get("exit") or {}
            cc = exit_.get("country", "")
            if s.get("via") == "system":
                where = "through Entropy Shield's Tor"
            elif cc:
                where = f"exits in {_COUNTRIES.get(cc, cc)} ({cc})"
            else:
                where = "circuit opens with the first connection"
            self._rows[sid][1].setText(f"{where}  ·  {_ago(s.get('started', time.time()))}")
        self.setVisible(bool(live))

    def _new_identity(self, sid: str, app: str) -> None:
        try:
            ok = request({"op": "new_identity", "session": sid}).get("ok")
        except Exception:  # noqa: BLE001
            ok = False
        self._log(f"[TOR] New identity for {app}." if ok
                  else f"[TOR] Could not renew {app}'s identity.")
        QTimer.singleShot(1500, self.refresh)
