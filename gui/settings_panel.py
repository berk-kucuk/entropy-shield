from __future__ import annotations
import os
import sys
import subprocess

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QSpinBox, QLineEdit, QTabWidget,
    QApplication, QSizePolicy, QFileDialog, QMessageBox, QComboBox,
)
from PyQt6.QtCore import (
    Qt, QRect, QPropertyAnimation, QEasingCurve, pyqtSignal,
    pyqtProperty,
)
from PyQt6.QtGui import QPainter, QColor, QLinearGradient

from gui.themes import current as theme
from gui.widgets import ToggleSwitch
from core.config import cfg
import core.autostart as autostart

_RUNNER_PATH  = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "privileged_runner.py",
)
_SUDOERS_FILE = "/etc/sudoers.d/entropy-shield"


def _nopasswd_active() -> bool:
    return os.path.exists(_SUDOERS_FILE)

_PANEL_W = 430


# ──────────────────────────────────────────────────────────────
#  SettingsPanel
# ──────────────────────────────────────────────────────────────

class SettingsPanel(QWidget):
    saved  = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(parent.rect())

        self._card = QFrame(self)
        self._card.setObjectName("settingsCard")
        self._card.setGeometry(self.width(), 0, _PANEL_W, self.height())

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        card_lay.addWidget(self._build_header())
        card_lay.addWidget(self._build_tabs(), stretch=1)
        card_lay.addWidget(self._build_footer())

        self._anim = QPropertyAnimation(self._card, b"geometry")
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.hide()

    # ── public ────────────────────────────────────────────────

    def open(self) -> None:
        self.setGeometry(self.parent().rect())
        self._card.setGeometry(self.width(), 14, _PANEL_W, self.height() - 28)
        self._populate()
        self.show()
        self.raise_()

        end = QRect(self.width() - _PANEL_W - 14, 14, _PANEL_W, self.height() - 28)
        self._anim.setStartValue(self._card.geometry())
        self._anim.setEndValue(end)
        self._anim.start()

    def close_panel(self) -> None:
        start = self._card.geometry()
        end   = QRect(self.width(), 14, _PANEL_W, self.height() - 28)
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        try:
            self._anim.finished.disconnect(self._on_close_done)
        except (TypeError, RuntimeError):
            pass
        self._anim.finished.connect(self._on_close_done)
        self._anim.start()

    # ── internal ──────────────────────────────────────────────

    def _on_close_done(self) -> None:
        self._anim.finished.disconnect(self._on_close_done)
        self.hide()
        self.closed.emit()

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(68)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 0, 16, 0)
        title = QLabel("SETTINGS")
        title.setObjectName("panelTitle")
        close = QPushButton("✕")
        close.setObjectName("settingsBtn")
        close.clicked.connect(self.close_panel)
        lay.addWidget(title)
        lay.addStretch()
        lay.addWidget(close)
        return w

    def _build_tabs(self) -> QTabWidget:
        self._tabs = QTabWidget()
        self._tabs.addTab(self._scrollable(self._tab_tor()),          "TOR")
        self._tabs.addTab(self._scrollable(self._tab_dnscrypt()),     "DNSCRYPT")
        self._tabs.addTab(self._scrollable(self._tab_i2p()),          "I2P")
        self._tabs.addTab(self._scrollable(self._tab_onion_server()), "ONION SERVER")
        self._tabs.addTab(self._scrollable(self._tab_routing()),      "ROUTING")
        self._tabs.addTab(self._scrollable(self._tab_general()),      "GENERAL")
        return self._tabs

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(70)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 10, 20, 10)
        save  = QPushButton("SAVE")
        save.setObjectName("saveBtn")
        save.clicked.connect(self._on_save)
        close = QPushButton("CANCEL")
        close.setObjectName("closeBtn")
        close.clicked.connect(self.close_panel)
        lay.addWidget(save)
        lay.addSpacing(8)
        lay.addWidget(close)
        return w

    # ── tabs ──────────────────────────────────────────────────

    def _scrollable(self, inner: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("settingSection")
        return lbl

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setObjectName("settingLabel")
        lbl.setFixedWidth(145)
        lay.addWidget(lbl)
        lay.addWidget(widget)
        return lay

    def _spinbox(self, lo: int = 1, hi: int = 65535) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setFixedWidth(110)
        sb.setAlignment(Qt.AlignmentFlag.AlignRight)
        return sb

    def _tab_tor(self) -> QWidget:
        from PyQt6.QtWidgets import QTextEdit, QComboBox
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._tor_trans  = self._spinbox()
        self._tor_dns    = self._spinbox()
        self._tor_socks  = self._spinbox()
        self._tor_ctrl   = self._spinbox()
        self._tor_exit   = QLineEdit()
        self._tor_exit.setPlaceholderText("{de},{nl},{ch}")
        self._tor_strict = ToggleSwitch()

        # Bridge settings
        self._bridge_enabled   = ToggleSwitch(checked=False)
        self._bridge_transport = QComboBox()
        self._bridge_transport.addItems(["obfs4", "meek-azure", "snowflake", "manual"])
        self._bridge_transport.setFixedWidth(110)
        self._bridge_lines = QTextEdit()
        self._bridge_lines.setPlaceholderText(
            "Bridge obfs4 IP:PORT FINGERPRINT cert=... iat-mode=0\n"
            "(one bridge per line, from bridges.torproject.org)"
        )
        self._bridge_lines.setFixedHeight(80)
        self._bridge_lines.setObjectName("log")

        lay.addWidget(self._section("TOR NETWORK"))
        lay.addLayout(self._row("Trans Port", self._tor_trans))
        lay.addLayout(self._row("DNS Port", self._tor_dns))
        lay.addLayout(self._row("SOCKS Port", self._tor_socks))
        lay.addSpacing(8)
        lay.addWidget(self._section("EXIT POLICY"))
        lay.addLayout(self._row("Exit Nodes", self._tor_exit))
        lay.addLayout(self._row("Strict Nodes", self._tor_strict))
        lay.addSpacing(8)
        lay.addWidget(self._section("BRIDGES (Censorship Circumvention)"))
        lay.addLayout(self._row("Use Bridges", self._bridge_enabled))
        lay.addLayout(self._row("Transport", self._bridge_transport))
        lbl_b = QLabel("Bridge Lines")
        lbl_b.setObjectName("settingLabel")
        lay.addWidget(lbl_b)
        lay.addWidget(self._bridge_lines)
        lay.addSpacing(8)
        lay.addWidget(self._section("ADVANCED"))
        lay.addLayout(self._row("Control Port", self._tor_ctrl))
        lay.addStretch()
        return w

    def _tab_dnscrypt(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._dns_port    = self._spinbox()
        self._dns_dnssec  = ToggleSwitch(checked=False)
        self._dns_nolog   = ToggleSwitch(checked=True)
        self._dns_nofilter = ToggleSwitch(checked=True)

        lay.addWidget(self._section("DNSCRYPT PROXY"))
        lay.addLayout(self._row("Listen Port", self._dns_port))
        lay.addSpacing(8)
        lay.addWidget(self._section("SERVER FILTERS"))
        lay.addLayout(self._row("Require DNSSEC", self._dns_dnssec))
        lay.addLayout(self._row("No-Log Only", self._dns_nolog))
        lay.addLayout(self._row("No-Filter Only", self._dns_nofilter))
        lay.addStretch()
        return w

    def _tab_i2p(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._i2p_http  = self._spinbox()
        self._i2p_socks = self._spinbox()
        self._i2p_bw    = self._spinbox(lo=0)

        lay.addWidget(self._section("I2P DAEMON"))
        lay.addLayout(self._row("HTTP Proxy Port", self._i2p_http))
        lay.addLayout(self._row("SOCKS Port", self._i2p_socks))
        lay.addSpacing(8)
        lay.addWidget(self._section("BANDWIDTH"))
        lay.addLayout(self._row("Max KB/s  (0=∞)", self._i2p_bw))
        lay.addStretch()
        return w

    def _tab_onion_server(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._onion_local_port = self._spinbox(1, 65535)
        self._onion_hs_port    = self._spinbox(1, 65535)

        # Directory picker: QLineEdit + Browse button side by side
        self._onion_serve_dir = QLineEdit()
        self._onion_serve_dir.setPlaceholderText("Default: home directory")
        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("closeBtn")
        browse_btn.setFixedWidth(72)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_serve_dir)

        dir_widget = QWidget()
        dir_lay = QHBoxLayout(dir_widget)
        dir_lay.setContentsMargins(0, 0, 0, 0)
        dir_lay.setSpacing(6)
        dir_lay.addWidget(self._onion_serve_dir)
        dir_lay.addWidget(browse_btn)

        lay.addWidget(self._section("ONION HTTP SERVER"))
        lay.addLayout(self._row("Serve Directory", dir_widget))
        lay.addLayout(self._row("Local HTTP Port", self._onion_local_port))
        lay.addLayout(self._row("Onion Port", self._onion_hs_port))

        note = QLabel(
            "Starts a built-in HTTP file server on Local HTTP Port and\n"
            "publishes it as a Tor hidden service (.onion).\n\n"
            "Serve Directory: the folder whose contents will be visible\n"
            "at your .onion address. Leave blank to use your home folder.\n\n"
            "Enabling this automatically activates Tor. Your .onion\n"
            "address is shown in the activity log after connecting."
        )
        note.setObjectName("settingLabel")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:11px; opacity:0.7;")
        lay.addSpacing(6)
        lay.addWidget(note)
        lay.addStretch()
        return w

    def _tab_routing(self) -> QWidget:
        from PyQt6.QtWidgets import (QTableWidget, QTableWidgetItem,
                                     QHeaderView, QComboBox)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        self._routing_enabled = ToggleSwitch(checked=False)
        lay.addWidget(self._section("PER-APP ROUTING"))
        lay.addLayout(self._row("Enable Rules", self._routing_enabled))

        note_r = QLabel(
            "Route specific apps directly (bypass Tor) or block them entirely.\n"
            "Enter a Linux username or numeric UID.  Action:\n"
            "  tor = route through Tor (default)\n"
            "  direct = bypass Tor (clearnet)\n"
            "  block = drop all traffic"
        )
        note_r.setObjectName("settingLabel")
        note_r.setWordWrap(True)
        note_r.setStyleSheet("font-size:11px; opacity:0.7;")
        lay.addWidget(note_r)

        self._routing_table = QTableWidget(0, 3)
        self._routing_table.setHorizontalHeaderLabels(["App Name", "User / UID", "Action"])
        self._routing_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._routing_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._routing_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._routing_table.setFixedHeight(140)
        self._routing_table.setObjectName("log")
        lay.addWidget(self._routing_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Rule")
        add_btn.setObjectName("closeBtn")
        add_btn.clicked.connect(self._add_routing_rule)
        del_btn = QPushButton("− Remove")
        del_btn.setObjectName("closeBtn")
        del_btn.clicked.connect(self._del_routing_rule)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()
        return w

    def _add_routing_rule(self) -> None:
        from PyQt6.QtWidgets import QComboBox, QTableWidgetItem
        row = self._routing_table.rowCount()
        self._routing_table.insertRow(row)
        self._routing_table.setItem(row, 0, QTableWidgetItem("AppName"))
        self._routing_table.setItem(row, 1, QTableWidgetItem("username"))
        combo = QComboBox()
        combo.addItems(["tor", "direct", "block"])
        self._routing_table.setCellWidget(row, 2, combo)

    def _del_routing_rule(self) -> None:
        rows = sorted(set(i.row() for i in self._routing_table.selectedItems()),
                      reverse=True)
        for r in rows:
            self._routing_table.removeRow(r)

    def _browse_serve_dir(self) -> None:
        current = self._onion_serve_dir.text().strip() or ""
        path = QFileDialog.getExistingDirectory(
            self, "Select directory to serve", current or "")
        if path:
            self._onion_serve_dir.setText(path)

    def _tab_general(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["OLED", "Light", "Binary", "Circuit", "Pixel"])
        self._theme_combo.setFixedWidth(110)

        self._kill_switch_toggle    = ToggleSwitch(checked=True)
        self._auto_connect_toggle   = ToggleSwitch(checked=False)
        self._autostart_toggle      = ToggleSwitch(checked=True)
        self._auto_reconnect_toggle = ToggleSwitch(checked=True)
        self._reconnect_delay       = self._spinbox(lo=5, hi=120)
        self._reconnect_delay.setFixedWidth(80)
        self._update_check_toggle   = ToggleSwitch(checked=True)

        self._auth_btn = QPushButton()
        self._auth_btn.setObjectName("closeBtn")
        self._auth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auth_btn.clicked.connect(self._on_auth_toggled)
        self._refresh_auth_btn()

        lay.addWidget(self._section("APPEARANCE"))
        lay.addLayout(self._row("Theme", self._theme_combo))
        lay.addSpacing(8)
        lay.addWidget(self._section("BEHAVIOUR"))
        lay.addLayout(self._row("Kill Switch",      self._kill_switch_toggle))
        lay.addLayout(self._row("Auto-Connect",     self._auto_connect_toggle))
        lay.addLayout(self._row("Start on Login",   self._autostart_toggle))
        lay.addLayout(self._row("Auto-Reconnect",   self._auto_reconnect_toggle))
        lay.addLayout(self._row("Reconnect Delay",  self._reconnect_delay))
        lay.addLayout(self._row("Update Check",     self._update_check_toggle))
        lay.addSpacing(8)
        lay.addWidget(self._section("PRIVILEGE"))
        lay.addLayout(self._row("Auth Mode", self._auth_btn))

        note = QLabel(
            "Kill Switch: if a privacy service drops, emergency disconnect.\n\n"
            "Auto-Reconnect: after a kill-switch event, automatically\n"
            "reconnect after the configured delay.\n\n"
            "Update Check: check GitHub Releases for a newer version\n"
            "once per session (no telemetry, HTTPS only).\n\n"
            "Auth Mode: when Passwordless is OFF, connect/disconnect\n"
            "asks for your sudo password via pkexec.\n"
            "Enable to write a sudoers entry for password-free operation."
        )
        note.setObjectName("settingLabel")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:11px; opacity:0.7;")
        lay.addSpacing(6)
        lay.addWidget(note)
        lay.addStretch()
        return w

    def _refresh_auth_btn(self) -> None:
        if _nopasswd_active():
            self._auth_btn.setText("Passwordless: ON  (click to disable)")
        else:
            self._auth_btn.setText("Passwordless: OFF  (click to enable)")

    def _on_auth_toggled(self) -> None:
        username = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if _nopasswd_active():
            cmd = ["pkexec", sys.executable, _RUNNER_PATH, "--remove-nopasswd"]
        else:
            cmd = ["pkexec", sys.executable, _RUNNER_PATH,
                   "--setup-nopasswd", username, sys.executable, _RUNNER_PATH]

        self._auth_btn.setEnabled(False)
        self._auth_btn.setText("Waiting for authentication…")

        import threading
        def _run():
            subprocess.run(cmd, capture_output=True, timeout=60)
            # Refresh button in the GUI thread
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._on_auth_done)

        threading.Thread(target=_run, daemon=True).start()

    def _on_auth_done(self) -> None:
        self._auth_btn.setEnabled(True)
        self._refresh_auth_btn()

    # ── populate / save ───────────────────────────────────────

    def _populate(self) -> None:
        from PyQt6.QtWidgets import QComboBox, QTableWidgetItem
        self._tor_trans.setValue(cfg().get("tor", "trans_port"))
        self._tor_dns.setValue(cfg().get("tor", "dns_port"))
        self._tor_socks.setValue(cfg().get("tor", "socks_port"))
        self._tor_ctrl.setValue(cfg().get("tor", "control_port"))
        self._tor_exit.setText(cfg().get("tor", "exit_nodes"))
        self._tor_strict.setChecked(cfg().get("tor", "strict_nodes"), silent=True)

        # Bridge settings
        br = cfg().get("bridges")
        self._bridge_enabled.setChecked(br.get("enabled", False), silent=True)
        tmap = {"obfs4": 0, "meek-azure": 1, "snowflake": 2, "manual": 3}
        self._bridge_transport.setCurrentIndex(tmap.get(br.get("transport", "obfs4"), 0))
        self._bridge_lines.setPlainText("\n".join(br.get("lines", [])))

        self._dns_port.setValue(cfg().get("dnscrypt", "port"))
        self._dns_dnssec.setChecked(cfg().get("dnscrypt", "require_dnssec"), silent=True)
        self._dns_nolog.setChecked(cfg().get("dnscrypt", "require_nolog"), silent=True)
        self._dns_nofilter.setChecked(cfg().get("dnscrypt", "require_nofilter"), silent=True)

        self._i2p_http.setValue(cfg().get("i2p", "http_port"))
        self._i2p_socks.setValue(cfg().get("i2p", "socks_port"))
        self._i2p_bw.setValue(cfg().get("i2p", "max_bandwidth"))

        self._onion_serve_dir.setText(cfg().get("onion_server", "serve_dir"))
        self._onion_local_port.setValue(cfg().get("onion_server", "local_port"))
        self._onion_hs_port.setValue(cfg().get("onion_server", "hs_port"))

        # Routing table
        self._routing_enabled.setChecked(
            cfg().get("per_app_routing").get("enabled", False), silent=True)
        self._routing_table.setRowCount(0)
        for rule in cfg().get("per_app_routing").get("rules", []):
            from PyQt6.QtWidgets import QComboBox
            row = self._routing_table.rowCount()
            self._routing_table.insertRow(row)
            self._routing_table.setItem(row, 0, QTableWidgetItem(rule.get("name", "")))
            self._routing_table.setItem(row, 1, QTableWidgetItem(str(rule.get("uid_or_user", ""))))
            combo = QComboBox()
            combo.addItems(["tor", "direct", "block"])
            action = rule.get("action", "tor")
            combo.setCurrentIndex({"tor": 0, "direct": 1, "block": 2}.get(action, 0))
            self._routing_table.setCellWidget(row, 2, combo)

        theme_map = {"oled": 0, "dark": 0, "light": 1, "binary": 2, "circuit": 3, "pixel": 4}
        self._theme_combo.setCurrentIndex(theme_map.get(cfg().get("theme"), 0))
        self._kill_switch_toggle.setChecked(cfg().get("kill_switch"),   silent=True)
        self._auto_connect_toggle.setChecked(cfg().get("auto_connect"), silent=True)
        self._autostart_toggle.setChecked(autostart.is_enabled(),       silent=True)
        ar = cfg().get("auto_reconnect")
        self._auto_reconnect_toggle.setChecked(ar.get("enabled", True), silent=True)
        self._reconnect_delay.setValue(ar.get("delay_seconds", 15))
        self._update_check_toggle.setChecked(cfg().get("update_check"), silent=True)
        self._refresh_auth_btn()

    def _validate_ports(self) -> str | None:
        """Return an error string if any ports conflict, else None."""
        tor_trans  = self._tor_trans.value()
        tor_dns    = self._tor_dns.value()
        tor_socks  = self._tor_socks.value()
        tor_ctrl   = self._tor_ctrl.value()
        dns_port   = self._dns_port.value()
        i2p_http   = self._i2p_http.value()
        i2p_socks  = self._i2p_socks.value()
        onion_port = self._onion_local_port.value()

        named = {
            "Tor TransPort":    tor_trans,
            "Tor DNSPort":      tor_dns,
            "Tor SOCKSPort":    tor_socks,
            "Tor ControlPort":  tor_ctrl,
            "DNSCrypt Port":    dns_port,
            "I2P HTTP Port":    i2p_http,
            "I2P SOCKS Port":   i2p_socks,
            "Onion HTTP Port":  onion_port,
        }

        seen: dict[int, str] = {}
        for name, port in named.items():
            if port in seen:
                return f"Port conflict: {name} and {seen[port]} both use port {port}."
            seen[port] = name
        return None

    def _on_save(self) -> None:
        conflict = self._validate_ports()
        if conflict:
            QMessageBox.warning(self, "Port Conflict", conflict)
            return

        cfg().set("tor", "trans_port",   self._tor_trans.value())
        cfg().set("tor", "dns_port",     self._tor_dns.value())
        cfg().set("tor", "socks_port",   self._tor_socks.value())
        cfg().set("tor", "control_port", self._tor_ctrl.value())
        cfg().set("tor", "exit_nodes",   self._tor_exit.text().strip())
        cfg().set("tor", "strict_nodes", self._tor_strict.isChecked())

        # Bridges
        transport_names = ["obfs4", "meek-azure", "snowflake", "manual"]
        bridge_raw = self._bridge_lines.toPlainText().strip()
        bridge_lines = [l.strip() for l in bridge_raw.splitlines() if l.strip()]
        cfg().set("bridges", "enabled",   self._bridge_enabled.isChecked())
        cfg().set("bridges", "transport", transport_names[self._bridge_transport.currentIndex()])
        cfg().set("bridges", "lines",     bridge_lines)

        # Per-app routing
        rules = []
        for row in range(self._routing_table.rowCount()):
            name_item = self._routing_table.item(row, 0)
            uid_item  = self._routing_table.item(row, 1)
            combo     = self._routing_table.cellWidget(row, 2)
            if name_item and uid_item and combo:
                rules.append({
                    "name":         name_item.text().strip(),
                    "uid_or_user":  uid_item.text().strip(),
                    "action":       combo.currentText(),
                })
        cfg().set("per_app_routing", "enabled", self._routing_enabled.isChecked())
        cfg().set("per_app_routing", "rules",   rules)

        cfg().set("dnscrypt", "port",             self._dns_port.value())
        cfg().set("dnscrypt", "require_dnssec",   self._dns_dnssec.isChecked())
        cfg().set("dnscrypt", "require_nolog",    self._dns_nolog.isChecked())
        cfg().set("dnscrypt", "require_nofilter", self._dns_nofilter.isChecked())

        cfg().set("i2p", "http_port",     self._i2p_http.value())
        cfg().set("i2p", "socks_port",    self._i2p_socks.value())
        cfg().set("i2p", "max_bandwidth", self._i2p_bw.value())

        cfg().set("onion_server", "serve_dir",  self._onion_serve_dir.text().strip())
        cfg().set("onion_server", "local_port", self._onion_local_port.value())
        cfg().set("onion_server", "hs_port",    self._onion_hs_port.value())

        theme_names = ["oled", "light", "binary", "circuit", "pixel"]
        theme_name  = theme_names[self._theme_combo.currentIndex()]
        cfg().set("theme", theme_name)
        cfg().set("kill_switch",  self._kill_switch_toggle.isChecked())
        cfg().set("auto_connect", self._auto_connect_toggle.isChecked())
        cfg().set("autostart",    self._autostart_toggle.isChecked())
        cfg().set("auto_reconnect", "enabled",        self._auto_reconnect_toggle.isChecked())
        cfg().set("auto_reconnect", "delay_seconds",  self._reconnect_delay.value())
        cfg().set("update_check", self._update_check_toggle.isChecked())
        cfg().save()

        # Apply autostart change immediately
        if self._autostart_toggle.isChecked():
            autostart.enable()
        else:
            autostart.disable()

        self.saved.emit()
        self.close_panel()

    # ── paint ─────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p    = QPainter(self)
        name = theme()["name"]
        if name == "oled":
            base = QColor(0, 0, 0, 220)
        elif name == "light":
            base = QColor(20, 20, 20, 130)
        else:
            base = QColor(10, 10, 10, 200)
        p.fillRect(self.rect(), base)

    def mousePressEvent(self, e) -> None:
        if not self._card.geometry().contains(e.pos()):
            self.close_panel()
