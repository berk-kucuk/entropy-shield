from __future__ import annotations
import re

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QSpinBox, QLineEdit, QTabWidget,
    QSizePolicy, QFileDialog, QMessageBox, QComboBox, QGridLayout,
)
from PyQt6.QtCore import (
    Qt, QRect, QRectF, QPropertyAnimation, QEasingCurve, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor

from gui.themes import current as theme
from gui.widgets import ToggleSwitch, paint_frost
from gui import icons
from core.config import cfg
from core.daemon_client import DaemonClient, DaemonError
import core.autostart as autostart


def _daemon_status() -> tuple[str, str]:
    """Return (short state, explanation) for the root privileged daemon.

    Privileged operations go through a root systemd daemon over a
    group-restricted Unix socket — a read-only indicator, nothing to toggle.
    """
    try:
        client = DaemonClient.connect(timeout=2.0)
        client.close()
        return ("●  Running",
                "Privileged operations run in the root entropy-shield service "
                "over a group-restricted socket — no pkexec or sudoers entry.")
    except DaemonError as exc:
        if "group" in str(exc):
            return ("●  No access",
                    "The service is running, but your user is not in the "
                    "'entropy-shield' group yet. Log out and back in after installing.")
        return ("●  Not running",
                "Start it with: sudo systemctl enable --now entropy-shield")


_ROUTE_ACTIONS = [
    ("tor",    "Through Tor"),
    ("direct", "Direct — bypass Tor"),
    ("block",  "Block all traffic"),
]
_USER_RE = re.compile(r"^(?:[0-9]{1,10}|[A-Za-z0-9_][A-Za-z0-9_.-]{0,31})$")


def _local_users() -> list[tuple[str, int]]:
    """Login accounts on this machine (uid ≥ 1000, real shell), for the picker."""
    import pwd
    out = []
    for pw in pwd.getpwall():
        if 1000 <= pw.pw_uid < 60000 and not pw.pw_shell.endswith(("nologin", "false")):
            out.append((pw.pw_name, pw.pw_uid))
    return sorted(out, key=lambda x: x[1])


_MARGIN = 14   # equal margin on all four sides of the settings card


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
        # Start off-screen to the right; real geometry set in open()
        self._card.setGeometry(self.width(), 0, self.width(), self.height())

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

    def _card_rect(self) -> QRect:
        """Card geometry with equal margins on all sides."""
        m = _MARGIN
        return QRect(m, m, self.width() - 2 * m, self.height() - 2 * m)

    # ── public ────────────────────────────────────────────────

    def open(self) -> None:
        self.setGeometry(self.parent().rect())
        dest = self._card_rect()
        # Start the card off to the right (slide-in animation)
        self._card.setGeometry(self.width(), dest.y(), dest.width(), dest.height())
        self._close_x.setIcon(icons.icon("x", theme()["text_muted"]))
        self._populate()
        self.show()
        self.raise_()

        self._anim.setStartValue(self._card.geometry())
        self._anim.setEndValue(dest)
        self._anim.start()

    def close_panel(self) -> None:
        dest = self._card_rect()
        off  = QRect(self.width(), dest.y(), dest.width(), dest.height())
        self._anim.stop()
        self._anim.setStartValue(self._card.geometry())
        self._anim.setEndValue(off)
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
        title = QLabel("Settings")
        title.setObjectName("panelTitle")
        close = QPushButton()
        close.setObjectName("settingsBtn")
        close.setIconSize(icons.qsize(16))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_x = close
        close.clicked.connect(self.close_panel)
        lay.addWidget(title)
        lay.addStretch()
        lay.addWidget(close)
        return w

    def _build_tabs(self) -> QTabWidget:
        self._tabs = QTabWidget()
        self._tabs.addTab(self._scrollable(self._tab_tor()),          "Tor")
        self._tabs.addTab(self._scrollable(self._tab_dnscrypt()),     "DNSCrypt")
        self._tabs.addTab(self._scrollable(self._tab_i2p()),          "I2P")
        self._tabs.addTab(self._scrollable(self._tab_onion_server()), "Onion")
        self._tabs.addTab(self._scrollable(self._tab_routing()),      "Routing")
        self._tabs.addTab(self._scrollable(self._tab_general()),      "General")
        return self._tabs

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(70)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 10, 20, 10)
        save  = QPushButton("Save changes")
        save.setObjectName("saveBtn")
        save.clicked.connect(self._on_save)
        close = QPushButton("Cancel")
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

    def _hint(self, text: str) -> QLabel:
        """Small explanatory text that wraps to the panel width."""
        lbl = QLabel(text)
        lbl.setObjectName("settingHint")
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return lbl

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("settingSection")
        return lbl

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setObjectName("settingLabel")
        if widget.maximumWidth() < 16_777_215:     # fixed-width control → right-aligned
            lay.addWidget(lbl, stretch=1)
            lay.addWidget(widget)
        else:                                      # free-width control fills the row
            lbl.setFixedWidth(140)
            lay.addWidget(lbl)
            lay.addWidget(widget, stretch=1)
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
        self._bridge_transport.addItems(["obfs4", "meek-azure", "manual"])
        self._bridge_transport.setFixedWidth(110)
        self._bridge_lines = QTextEdit()
        self._bridge_lines.setPlaceholderText(
            "Bridge obfs4 IP:PORT FINGERPRINT cert=... iat-mode=0\n"
            "(one bridge per line, from bridges.torproject.org)"
        )
        self._bridge_lines.setFixedHeight(80)
        self._bridge_lines.setObjectName("bridgeLines")

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
        self._onion_serve_dir.setPlaceholderText("Default: your Public folder (~/Public)")
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

        lay.addSpacing(6)
        for text in (
            "Serves a folder over a built-in HTTP server and publishes it as a "
            "Tor onion service. Turning this on also turns on Tor.",
            "Leave the folder blank to share your Public folder. Your home "
            "folder itself is never shared — it holds keys and passwords.",
            "Your .onion address appears in the activity log after connecting.",
        ):
            lay.addWidget(self._hint(text))
        lay.addStretch()
        return w

    def _tab_routing(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        self._routing_enabled = ToggleSwitch(checked=False)
        lay.addWidget(self._section("PER-APP ROUTING"))
        lay.addLayout(self._row("Enable rules", self._routing_enabled))
        lay.addWidget(self._hint(
            "Rules match the Linux user an app runs as. Give an app its own "
            "user (for example: sudo -u steam steam) to route it separately. "
            "Rules apply while Tor is connected."))

        # ── add-rule form ──────────────────────────────────────
        lay.addSpacing(4)
        lay.addWidget(self._section("ADD A RULE"))
        form = QFrame()
        form.setObjectName("formCard")
        g = QGridLayout(form)
        g.setContentsMargins(14, 14, 14, 14)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)

        def _lbl(t: str) -> QLabel:
            l = QLabel(t)
            l.setObjectName("settingLabel")
            return l

        self._rule_name = QLineEdit()
        self._rule_name.setPlaceholderText("App name, e.g. Steam")

        self._rule_user = QComboBox()
        self._rule_user.setEditable(True)
        self._rule_user.lineEdit().setPlaceholderText("username or UID")
        for name, uid in _local_users():
            self._rule_user.addItem(f"{name}  (uid {uid})", name)
        self._rule_user.setCurrentIndex(-1)

        self._rule_action = QComboBox()
        for key, label in _ROUTE_ACTIONS:
            self._rule_action.addItem(label, key)

        self._rule_error = QLabel("")
        self._rule_error.setObjectName("formError")
        self._rule_error.setWordWrap(True)
        self._rule_error.hide()

        add_btn = QPushButton("Add rule")
        add_btn.setObjectName("saveBtn")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_routing_rule)

        g.addWidget(_lbl("App"),     0, 0)
        g.addWidget(self._rule_name, 0, 1)
        g.addWidget(_lbl("Runs as"), 1, 0)
        g.addWidget(self._rule_user, 1, 1)
        g.addWidget(_lbl("Traffic"), 2, 0)
        g.addWidget(self._rule_action, 2, 1)
        g.addWidget(self._rule_error, 3, 0, 1, 2)
        g.addWidget(add_btn, 4, 1, alignment=Qt.AlignmentFlag.AlignRight)
        g.setColumnStretch(1, 1)
        lay.addWidget(form)

        # ── rule list ──────────────────────────────────────────
        lay.addSpacing(4)
        lay.addWidget(self._section("RULES"))
        self._rules_box = QVBoxLayout()
        self._rules_box.setSpacing(6)
        lay.addLayout(self._rules_box)
        self._routing_rules: list[dict] = []
        self._render_rules()
        lay.addStretch()
        return w

    def _rule_user_value(self) -> str:
        cb = self._rule_user
        text = cb.currentText().strip()
        i = cb.findText(text)
        return cb.itemData(i) if i >= 0 and cb.itemData(i) else text

    def _add_routing_rule(self) -> None:
        who = self._rule_user_value()
        if not _USER_RE.match(who):
            self._rule_error.setText(
                "Choose a user from the list, or type a username / numeric UID.")
            self._rule_error.show()
            return
        self._rule_error.hide()
        name = self._rule_name.text().strip() or who
        action = self._rule_action.currentData()
        # one rule per user — a newer rule replaces the old one
        self._routing_rules = [r for r in self._routing_rules if r["uid_or_user"] != who]
        self._routing_rules.append({"name": name[:64], "uid_or_user": who, "action": action})
        self._rule_name.clear()
        self._rule_user.setCurrentIndex(-1)
        self._rule_user.clearEditText()
        self._render_rules()

    def _remove_routing_rule(self, who: str) -> None:
        self._routing_rules = [r for r in self._routing_rules if r["uid_or_user"] != who]
        self._render_rules()

    def _render_rules(self) -> None:
        while self._rules_box.count():
            item = self._rules_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._routing_rules:
            self._rules_box.addWidget(self._hint(
                "No rules yet — every app follows the active privacy layers."))
            return
        labels = dict(_ROUTE_ACTIONS)
        for rule in self._routing_rules:
            row = QFrame()
            row.setObjectName("ruleRow")
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 8, 8, 8)
            h.setSpacing(10)
            text = QVBoxLayout()
            text.setSpacing(2)
            name = QLabel(rule["name"])
            name.setObjectName("ruleName")
            sub = QLabel(f"{rule['uid_or_user']}  ·  {labels.get(rule['action'], rule['action'])}")
            sub.setObjectName("settingHint")
            text.addWidget(name)
            text.addWidget(sub)
            h.addLayout(text, stretch=1)
            rm = QPushButton()
            rm.setObjectName("iconBtn")
            rm.setIcon(icons.icon("x", theme()["text_muted"]))
            rm.setIconSize(icons.qsize(14))
            rm.setToolTip("Remove rule")
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.clicked.connect(lambda _=False, who=rule["uid_or_user"]: self._remove_routing_rule(who))
            h.addWidget(rm, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._rules_box.addWidget(row)

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
        self._circuit_renewal_spin  = self._spinbox(lo=0, hi=120)
        self._circuit_renewal_spin.setFixedWidth(80)
        self._circuit_renewal_spin.setToolTip("0 = disabled. Tor renews identity every N minutes.")
        self._mac_toggle            = ToggleSwitch(checked=False)
        self._doh_block_toggle      = ToggleSwitch(checked=True)

        self._daemon_state = QLabel()
        self._daemon_state.setObjectName("settingValue")
        recheck = QPushButton("Re-check")
        recheck.setObjectName("ghostBtn")
        recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        recheck.clicked.connect(self._on_auth_toggled)
        daemon_box = QWidget()
        db = QHBoxLayout(daemon_box)
        db.setContentsMargins(0, 0, 0, 0)
        db.setSpacing(6)
        db.addWidget(self._daemon_state)
        db.addWidget(recheck)
        daemon_box.setMaximumWidth(daemon_box.sizeHint().width() + 120)
        self._daemon_detail = self._hint("")
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
        lay.addLayout(self._row("Update Check",        self._update_check_toggle))
        lay.addLayout(self._row("Circuit Renewal (min)", self._circuit_renewal_spin))
        lay.addSpacing(8)
        lay.addWidget(self._section("PRIVACY"))
        lay.addLayout(self._row("MAC Randomize",    self._mac_toggle))
        lay.addLayout(self._row("Block DoH",        self._doh_block_toggle))
        lay.addSpacing(8)
        lay.addWidget(self._section("PRIVILEGE"))
        lay.addLayout(self._row("Service", daemon_box))
        lay.addWidget(self._daemon_detail)

        lay.addSpacing(6)
        lay.addWidget(self._section("ABOUT THESE OPTIONS"))
        for text in (
            "Kill Switch — if a privacy service drops, disconnect everything at once.",
            "Auto-Reconnect — after a kill-switch event, reconnect after the delay.",
            "Update Check — once per session, through Tor, after the first Tor connect.",
            "Circuit Renewal — request a new Tor identity every N minutes (0 = off).",
            "MAC Randomize — random interface MAC on connect, restored on disconnect.",
            "Block DoH — in DNSCrypt-only mode, block DNS-over-HTTPS/TLS/QUIC so "
            "apps cannot bypass dnscrypt-proxy.",
        ):
            lay.addWidget(self._hint(text))
        lay.addStretch()
        return w

    def _refresh_auth_btn(self) -> None:
        state, detail = _daemon_status()
        self._daemon_state.setText(state)
        self._daemon_detail.setText(detail)

    def _on_auth_toggled(self) -> None:
        # Read-only indicator: clicking just re-checks the daemon status.
        self._refresh_auth_btn()

    # ── populate / save ───────────────────────────────────────

    def _populate(self) -> None:
        self._tor_trans.setValue(cfg().get("tor", "trans_port"))
        self._tor_dns.setValue(cfg().get("tor", "dns_port"))
        self._tor_socks.setValue(cfg().get("tor", "socks_port"))
        self._tor_ctrl.setValue(cfg().get("tor", "control_port"))
        self._tor_exit.setText(cfg().get("tor", "exit_nodes"))
        self._tor_strict.setChecked(cfg().get("tor", "strict_nodes"), silent=True)

        # Bridge settings
        br = cfg().get("bridges")
        self._bridge_enabled.setChecked(br.get("enabled", False), silent=True)
        tmap = {"obfs4": 0, "meek-azure": 1, "manual": 2}
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

        # Routing rules
        par = cfg().get("per_app_routing")
        self._routing_enabled.setChecked(par.get("enabled", False), silent=True)
        self._routing_rules = [dict(r) for r in par.get("rules", [])]
        self._rule_error.hide()
        self._render_rules()

        theme_map = {"oled": 0, "dark": 0, "light": 1, "binary": 2, "circuit": 3, "pixel": 4}
        self._theme_combo.setCurrentIndex(theme_map.get(cfg().get("theme"), 0))
        self._kill_switch_toggle.setChecked(cfg().get("kill_switch"),   silent=True)
        self._auto_connect_toggle.setChecked(cfg().get("auto_connect"), silent=True)
        self._autostart_toggle.setChecked(autostart.is_enabled(),       silent=True)
        ar = cfg().get("auto_reconnect")
        self._auto_reconnect_toggle.setChecked(ar.get("enabled", True), silent=True)
        self._reconnect_delay.setValue(ar.get("delay_seconds", 15))
        self._update_check_toggle.setChecked(cfg().get("update_check"),   silent=True)
        self._circuit_renewal_spin.setValue(cfg().get("circuit_renewal_minutes"))
        self._mac_toggle.setChecked(cfg().get("mac_randomize"),           silent=True)
        self._doh_block_toggle.setChecked(cfg().get("doh_block"),         silent=True)
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
        transport_names = ["obfs4", "meek-azure", "manual"]
        bridge_raw = self._bridge_lines.toPlainText().strip()
        bridge_lines = [l.strip() for l in bridge_raw.splitlines() if l.strip()]
        cfg().set("bridges", "enabled",   self._bridge_enabled.isChecked())
        cfg().set("bridges", "transport", transport_names[self._bridge_transport.currentIndex()])
        cfg().set("bridges", "lines",     bridge_lines)

        # Per-app routing
        rules = [dict(r) for r in self._routing_rules]
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
        cfg().set("update_check",            self._update_check_toggle.isChecked())
        cfg().set("circuit_renewal_minutes", self._circuit_renewal_spin.value())
        cfg().set("mac_randomize",           self._mac_toggle.isChecked())
        cfg().set("doh_block",     self._doh_block_toggle.isChecked())
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
        # Frosted modal: the blurred window backdrop, dimmed, behind the card.
        p = QPainter(self)
        paint_frost(p, self, QRectF(self.rect()))
        p.fillRect(self.rect(),
                   QColor(0, 0, 0, 120) if theme()["dark"] else QColor(0, 0, 0, 70))

    def mousePressEvent(self, e) -> None:
        if not self._card.geometry().contains(e.pos()):
            self.close_panel()
