from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QSpinBox, QLineEdit, QTabWidget,
    QApplication, QSizePolicy, QFileDialog,
)
from PyQt6.QtCore import (
    Qt, QRect, QPropertyAnimation, QEasingCurve, pyqtSignal,
    pyqtProperty,
)
from PyQt6.QtGui import QPainter, QColor, QLinearGradient

from gui.themes import current as theme
from gui.widgets import ToggleSwitch
from core.config import cfg

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
        self._tabs.addTab(self._tab_tor(),          "TOR")
        self._tabs.addTab(self._tab_dnscrypt(),     "DNSCRYPT")
        self._tabs.addTab(self._tab_i2p(),          "I2P")
        self._tabs.addTab(self._tab_onion_server(), "ONION SERVER")
        self._tabs.addTab(self._tab_general(),      "GENERAL")
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
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._tor_trans  = self._spinbox()
        self._tor_dns    = self._spinbox()
        self._tor_socks  = self._spinbox()
        self._tor_exit   = QLineEdit()
        self._tor_exit.setPlaceholderText("{de},{nl},{ch}")
        self._tor_strict = ToggleSwitch()

        lay.addWidget(self._section("TOR NETWORK"))
        lay.addLayout(self._row("Trans Port", self._tor_trans))
        lay.addLayout(self._row("DNS Port", self._tor_dns))
        lay.addLayout(self._row("SOCKS Port", self._tor_socks))
        lay.addSpacing(8)
        lay.addWidget(self._section("EXIT POLICY"))
        lay.addLayout(self._row("Exit Nodes", self._tor_exit))
        lay.addLayout(self._row("Strict Nodes", self._tor_strict))
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

        self._theme_toggle = ToggleSwitch(checked=(cfg().get("theme") == "dark"))

        theme_row = QHBoxLayout()
        dark_lbl  = QLabel("Dark Mode")
        dark_lbl.setObjectName("settingLabel")
        theme_row.addWidget(self._theme_toggle)
        theme_row.addSpacing(10)
        theme_row.addWidget(dark_lbl)
        theme_row.addStretch()

        lay.addWidget(self._section("APPEARANCE"))
        lay.addLayout(theme_row)
        lay.addStretch()
        return w

    # ── populate / save ───────────────────────────────────────

    def _populate(self) -> None:
        self._tor_trans.setValue(cfg().get("tor", "trans_port"))
        self._tor_dns.setValue(cfg().get("tor", "dns_port"))
        self._tor_socks.setValue(cfg().get("tor", "socks_port"))
        self._tor_exit.setText(cfg().get("tor", "exit_nodes"))
        self._tor_strict.setChecked(cfg().get("tor", "strict_nodes"), silent=True)

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

        self._theme_toggle.setChecked(cfg().get("theme") == "dark", silent=True)

    def _on_save(self) -> None:
        cfg().set("tor", "trans_port",   self._tor_trans.value())
        cfg().set("tor", "dns_port",     self._tor_dns.value())
        cfg().set("tor", "socks_port",   self._tor_socks.value())
        cfg().set("tor", "exit_nodes",   self._tor_exit.text().strip())
        cfg().set("tor", "strict_nodes", self._tor_strict.isChecked())

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

        theme_name = "dark" if self._theme_toggle.isChecked() else "light"
        cfg().set("theme", theme_name)
        cfg().save()

        self.saved.emit()
        self.close_panel()

    # ── paint ─────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        is_dark = theme()["name"] == "dark"
        if is_dark:
            base = QColor(4, 7, 12, 210)
        else:
            base = QColor(16, 24, 40, 140)
        p.fillRect(self.rect(), base)

    def mousePressEvent(self, e) -> None:
        if not self._card.geometry().contains(e.pos()):
            self.close_panel()
