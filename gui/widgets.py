from __future__ import annotations
import math
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor, QPen

from gui.themes import current as theme


# ──────────────────────────────────────────────────────────────
#  ToggleSwitch
# ──────────────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._pos: float = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"handlePos")
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    handlePos = pyqtProperty(float, _get_pos, _set_pos)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool, silent: bool = False) -> None:
        if v == self._checked:
            return
        self._checked = v
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if v else 0.0)
        self._anim.start()
        if not silent:
            self.toggled.emit(v)

    def mousePressEvent(self, _e) -> None:
        self.setChecked(not self._checked)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        c = theme()
        t = self._pos
        if c["name"] == "dark":
            off_rgb, on_rgb = (28, 36, 52),  (44, 186, 92)
        else:
            off_rgb, on_rgb = (175, 184, 193), (26, 127, 55)

        r = int(off_rgb[0] + (on_rgb[0] - off_rgb[0]) * t)
        g = int(off_rgb[1] + (on_rgb[1] - off_rgb[1]) * t)
        b = int(off_rgb[2] + (on_rgb[2] - off_rgb[2]) * t)

        p.setBrush(QColor(r, g, b))
        p.drawRoundedRect(0, 0, 44, 24, 12, 12)
        p.setBrush(QColor(255, 255, 255, 235))
        hx = int(3 + t * 17)
        p.drawEllipse(hx, 3, 18, 18)


# ──────────────────────────────────────────────────────────────
#  Spinner
# ──────────────────────────────────────────────────────────────

class Spinner(QWidget):
    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.hide()

    def start(self) -> None:
        self._angle = 0
        self.show()
        self._timer.start(14)

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _step(self) -> None:
        self._angle = (self._angle + 10) % 360
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width() // 2 - 2
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._angle)
        pen = QPen(QColor(theme()["blue"]), 2,
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(-r, -r, r * 2, r * 2, 0, 270 * 16)


# ──────────────────────────────────────────────────────────────
#  StatusRing  — central animated ring (replaces old StatusBanner)
# ──────────────────────────────────────────────────────────────

_RING_COLORS = {
    "off":        (210, 55,  48),
    "connecting": (210, 153, 50),
    "on":         (44,  186, 92),
    "error":      (210, 55,  48),
}


class StatusRing(QWidget):
    def __init__(self, size: int = 104, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._state = "off"
        self._pulse = 0.5

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_pulse(self, t: float) -> None:
        self._pulse = t
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r, g, b = _RING_COLORS.get(self._state, _RING_COLORS["off"])
        intensity = (0.5 + 0.5 * self._pulse) if self._state == "connecting" else 0.85
        cx, cy = self.width() / 2, self.height() / 2
        outer = self.width() / 2 - 4

        # Outer glow rings
        for i in range(12, 0, -1):
            a = int(intensity * 90 * ((12 - i) / 12) ** 1.6)
            p.setPen(QPen(QColor(r, g, b, a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(
                int(cx - outer - i), int(cy - outer - i),
                int((outer + i) * 2), int((outer + i) * 2),
            )

        # Main ring
        ring_pen = QPen(QColor(r, g, b, int(200 * intensity)), 2.5)
        ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ring_pen)
        p.setBrush(QColor(r, g, b, int(18 * intensity)))
        p.drawEllipse(
            int(cx - outer), int(cy - outer),
            int(outer * 2), int(outer * 2),
        )

        # Center symbol
        sym_r = outer * 0.36
        if self._state == "on":
            # Checkmark
            pen2 = QPen(QColor(r, g, b, int(230 * intensity)), 2.5,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen2)
            p.drawLine(int(cx - sym_r * 0.7), int(cy),
                       int(cx - sym_r * 0.1), int(cy + sym_r * 0.7))
            p.drawLine(int(cx - sym_r * 0.1), int(cy + sym_r * 0.7),
                       int(cx + sym_r * 0.8), int(cy - sym_r * 0.65))

        elif self._state == "connecting":
            # Pulsing dot
            a = int(120 + 135 * self._pulse)
            p.setBrush(QColor(r, g, b, a))
            p.setPen(Qt.PenStyle.NoPen)
            dot = int(sym_r * 0.55)
            p.drawEllipse(int(cx - dot), int(cy - dot), dot * 2, dot * 2)

        else:
            # Lock shape (disconnected/error)
            p.setPen(QPen(QColor(r, g, b, int(160 * intensity)), 1.8,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            bw, bh = int(sym_r * 0.85), int(sym_r * 0.7)
            by = int(cy - sym_r * 0.1)
            p.drawRoundedRect(int(cx - bw // 2), by, bw, bh, 3, 3)
            # Shackle arc
            p.setBrush(Qt.BrushStyle.NoBrush)
            ar = int(bw * 0.38)
            p.drawArc(int(cx - ar), int(by - ar * 2 + 2), ar * 2, ar * 2, 0, 180 * 16)


# ──────────────────────────────────────────────────────────────
#  ServiceCard  — compact
# ──────────────────────────────────────────────────────────────

_CARD_META: dict[str, tuple[str, str]] = {
    "tor":      ("TOR",      "Onion routing"),
    "dnscrypt": ("DNSCRYPT", "Encrypted DNS"),
    "i2p":      ("I2P",      "P2P overlay"),
    "lokinet":  ("LOKINET",  "LLARP network"),
}

# Service-specific accent colors (r, g, b)
_CARD_COLORS: dict[str, tuple[int, int, int]] = {
    "tor":      (138, 110, 210),   # purple
    "dnscrypt": ( 78, 182, 255),   # blue
    "i2p":      ( 78, 210, 160),   # teal
    "lokinet":  (255, 160,  60),   # orange
}


class ServiceCard(QFrame):
    toggled          = pyqtSignal(str, bool)
    settings_clicked = pyqtSignal(str)

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self._tag = tag
        title, desc = _CARD_META[tag]
        ar, ag, ab  = _CARD_COLORS[tag]

        self.setObjectName("serviceCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(132)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 14, 12)
        root.setSpacing(0)

        # ── icon circle ────────────────────────────────────
        icon_frame = QWidget()
        icon_frame.setFixedSize(38, 38)
        icon_frame.setObjectName("cardIconFrame")
        icon_frame.setStyleSheet(
            f"background: rgba({ar},{ag},{ab},0.13);"
            f"border: 1px solid rgba({ar},{ag},{ab},0.30);"
            f"border-radius: 19px;"
        )
        icon_inner = QVBoxLayout(icon_frame)
        icon_inner.setContentsMargins(0, 0, 0, 0)
        icon_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl = QLabel(_SERVICE_ICON[tag])
        self._icon_lbl.setStyleSheet(
            f"color: rgba({ar},{ag},{ab},0.92); font-size:14px;"
            f"background:transparent; border:none;"
        )
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_inner.addWidget(self._icon_lbl)

        # ── toggle ─────────────────────────────────────────
        self._toggle = ToggleSwitch(checked=True)
        self._toggle.toggled.connect(lambda v: self.toggled.emit(self._tag, v))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addWidget(icon_frame)
        top.addStretch()
        top.addWidget(self._toggle)

        # ── title ──────────────────────────────────────────
        title_lbl = QLabel(title)
        title_lbl.setObjectName("cardTitle")
        title_lbl.setStyleSheet(
            f"color: rgba({ar},{ag},{ab},0.95);"
            f"font-size:12px; font-weight:700; letter-spacing:2px;"
            f"background:transparent;"
        )

        # ── desc + status row ──────────────────────────────
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(6)

        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("cardDesc")

        self._dot    = QLabel("●")
        self._dot.setObjectName("cardDot")
        self._status = QLabel("READY")
        self._status.setObjectName("cardStatus")

        gear = QPushButton("⚙")
        gear.setObjectName("gearBtn")
        gear.setFixedSize(20, 20)
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.clicked.connect(lambda: self.settings_clicked.emit(self._tag))

        bot.addWidget(self._dot)
        bot.addWidget(self._status, stretch=1)
        bot.addWidget(gear)

        root.addLayout(top)
        root.addSpacing(10)
        root.addWidget(title_lbl)
        root.addSpacing(6)
        root.addWidget(desc_lbl)
        root.addStretch()
        root.addLayout(bot)

    @property
    def is_checked(self) -> bool:
        return self._toggle.isChecked()

    def set_status(self, state: str) -> None:
        labels = {
            "active":     "ACTIVE",
            "connecting": "WAIT",
            "error":      "ERROR",
            "":           "READY",
        }
        self._status.setText(labels.get(state, "READY"))
        for w in (self._dot, self._status):
            w.setProperty("state", state)
            w.style().unpolish(w)
            w.style().polish(w)
        self.setProperty("status", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_enabled_ui(self, enabled: bool) -> None:
        self._toggle.setEnabled(enabled)


_SERVICE_ICON: dict[str, str] = {
    "tor":      "⬡",
    "dnscrypt": "⊕",
    "i2p":      "◈",
    "lokinet":  "⬢",
}
