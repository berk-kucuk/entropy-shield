from __future__ import annotations
import math
import time
from collections import deque
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtProperty, pyqtSignal, QRectF, QPointF, QSize,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient, QPainterPath

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
        if c["name"] == "pixel":
            off_rgb, on_rgb = (55, 55, 55),   (200, 200, 200)
        elif c["name"] == "oled":
            off_rgb, on_rgb = (20, 20, 20),   (0, 210, 100)
        elif c["name"] == "dark":
            off_rgb, on_rgb = (28, 36, 52),   (44, 186, 92)
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
#  StatusRing  — central animated ring
# ──────────────────────────────────────────────────────────────

_RING_FALLBACK = {
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
        if theme()["name"] == "circuit":
            self._paint_cpu_chip(p)
        else:
            self._paint_ring(p)

    # ── ring (all non-circuit themes) ─────────────────────────

    def _paint_ring(self, p: QPainter) -> None:
        glow    = theme().get("glow", _RING_FALLBACK)
        r, g, b = glow.get(self._state, glow["off"])
        intensity = (0.5 + 0.5 * self._pulse) if self._state == "connecting" else 0.90
        cx, cy = self.width() / 2, self.height() / 2
        outer  = self.width() / 2 - 4

        for i in range(12, 0, -1):
            a = int(intensity * 90 * ((12 - i) / 12) ** 1.6)
            p.setPen(QPen(QColor(r, g, b, a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(
                int(cx - outer - i), int(cy - outer - i),
                int((outer + i) * 2), int((outer + i) * 2),
            )

        ring_pen = QPen(QColor(r, g, b, int(200 * intensity)), 2.5)
        ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ring_pen)
        p.setBrush(QColor(r, g, b, int(18 * intensity)))
        p.drawEllipse(
            int(cx - outer), int(cy - outer),
            int(outer * 2), int(outer * 2),
        )

        sym_r = outer * 0.36
        if self._state == "on":
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
            a = int(120 + 135 * self._pulse)
            p.setBrush(QColor(r, g, b, a))
            p.setPen(Qt.PenStyle.NoPen)
            dot = int(sym_r * 0.55)
            p.drawEllipse(int(cx - dot), int(cy - dot), dot * 2, dot * 2)
        else:
            p.setPen(QPen(QColor(r, g, b, int(160 * intensity)), 1.8,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            bw, bh = int(sym_r * 0.85), int(sym_r * 0.7)
            by = int(cy - sym_r * 0.1)
            p.drawRoundedRect(int(cx - bw // 2), by, bw, bh, 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ar = int(bw * 0.38)
            p.drawArc(int(cx - ar), int(by - ar * 2 + 2), ar * 2, ar * 2, 0, 180 * 16)

    # ── CPU chip (circuit theme only) ─────────────────────────

    def _paint_cpu_chip(self, p: QPainter) -> None:
        """IC/CPU chip shape — square body with pin pads, inner die, state symbol."""
        glow      = theme().get("glow", _RING_FALLBACK)
        r, g, b   = glow.get(self._state, glow["off"])
        intensity = (0.5 + 0.5 * self._pulse) if self._state == "connecting" else 0.90

        W  = float(self.width())
        cx = W / 2.0
        cy = float(self.height()) / 2.0

        chip_s = W * 0.63
        pin_w  = chip_s * 0.115
        pin_h  = chip_s * 0.072
        pin_n  = 4
        die_s  = chip_s * 0.52
        chip_x = cx - chip_s / 2
        chip_y = cy - chip_s / 2

        # ── outer glow halo ────────────────────────────────────
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(10, 0, -1):
            a      = int(intensity * 72 * ((10 - i + 1) / 10) ** 2.2)
            expand = i * 1.7
            p.setPen(QPen(QColor(r, g, b, a), 1))
            p.drawRoundedRect(
                QRectF(chip_x - expand, chip_y - expand,
                       chip_s + 2*expand, chip_s + 2*expand), 5, 5)

        # ── pin pads ───────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b, int(138 * intensity)))
        sp = chip_s / (pin_n + 1)
        for i in range(1, pin_n + 1):
            px = chip_x + i * sp - pin_w / 2
            p.drawRect(QRectF(px, chip_y - pin_h,  pin_w, pin_h))
            p.drawRect(QRectF(px, chip_y + chip_s, pin_w, pin_h))
            py = chip_y + i * sp - pin_w / 2
            p.drawRect(QRectF(chip_x - pin_h,     py, pin_h, pin_w))
            p.drawRect(QRectF(chip_x + chip_s,    py, pin_h, pin_w))

        # ── chip body ──────────────────────────────────────────
        p.setPen(QPen(QColor(r, g, b, int(172 * intensity)), 1.5))
        p.setBrush(QColor(r, g, b, int(14 * intensity)))
        p.drawRoundedRect(QRectF(chip_x, chip_y, chip_s, chip_s), 4, 4)

        # ── corner alignment circle (top-left, standard IC mark) ──
        dot_r = chip_s * 0.068
        dot_x = chip_x + chip_s * 0.115
        dot_y = chip_y + chip_s * 0.115
        p.setPen(QPen(QColor(r, g, b, int(105 * intensity)), 1.0))
        p.setBrush(QColor(r, g, b, int(52 * intensity)))
        p.drawEllipse(QRectF(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2))

        # ── inner die ──────────────────────────────────────────
        die_x = cx - die_s / 2
        die_y = cy - die_s / 2
        p.setPen(QPen(QColor(r, g, b, int(88 * intensity)), 0.9))
        p.setBrush(QColor(r, g, b, int(9 * intensity)))
        p.drawRect(QRectF(die_x, die_y, die_s, die_s))

        # Decorative bus lines inside die
        line_a = int(45 * intensity)
        p.setPen(QPen(QColor(r, g, b, line_a), 0.65))
        for frac in (0.32, 0.68):
            y_ln = die_y + die_s * frac
            p.drawLine(QPointF(die_x + die_s * 0.10, y_ln),
                       QPointF(die_x + die_s * 0.90, y_ln))
        x_ln = die_x + die_s * 0.50
        p.drawLine(QPointF(x_ln, die_y + die_s * 0.10),
                   QPointF(x_ln, die_y + die_s * 0.90))

        # ── state symbol inside die ────────────────────────────
        sym_r   = die_s * 0.22
        sym_pen = QPen(QColor(r, g, b, int(232 * intensity)), 2.1,
                       Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)

        if self._state == "on":
            p.setPen(sym_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(cx - sym_r * 0.65, cy),
                       QPointF(cx - sym_r * 0.10, cy + sym_r * 0.65))
            p.drawLine(QPointF(cx - sym_r * 0.10, cy + sym_r * 0.65),
                       QPointF(cx + sym_r * 0.76, cy - sym_r * 0.55))
        elif self._state == "connecting":
            a   = int(120 + 135 * self._pulse)
            dot = sym_r * 0.52
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b, a))
            p.drawEllipse(QRectF(cx - dot, cy - dot, dot * 2, dot * 2))
        else:
            bw  = sym_r * 1.62
            bh  = sym_r * 1.28
            bx  = cx - bw / 2
            by_ = cy - sym_r * 0.08
            p.setPen(QPen(QColor(r, g, b, int(158 * intensity)), 1.65,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(bx, by_, bw, bh), 3, 3)
            ar = bw * 0.36
            p.drawArc(QRectF(cx - ar, by_ - ar * 1.75, ar * 2, ar * 2),
                      0, 180 * 16)


# ──────────────────────────────────────────────────────────────
#  _ServiceIcon  — QPainter vector icon for each service
# ──────────────────────────────────────────────────────────────

class _ServiceIcon(QWidget):
    def __init__(self, tag: str, r: int, g: int, b: int, parent=None):
        super().__init__(parent)
        self._tag = tag
        self._r, self._g, self._b = r, g, b
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, g, b = self._r, self._g, self._b
        s = float(self.width())
        cx, cy = s / 2, s / 2

        def _pen(alpha: int = 230, width: float = 1.4) -> QPen:
            return QPen(QColor(r, g, b, alpha), width,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin)

        if self._tag == "tor":
            # Onion: three concentric rings, fading outward
            for rad, alpha in [(2.2, 240), (4.6, 165), (7.2, 85)]:
                p.setPen(_pen(alpha, 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        elif self._tag == "dnscrypt":
            # Shield with a lock inside
            w, h = s * 0.62, s * 0.78
            lx, rx = cx - w / 2, cx + w / 2
            ty, by = cy - h / 2, cy + h / 2
            mid = ty + h * 0.40
            path = QPainterPath()
            path.moveTo(cx, ty)
            path.lineTo(rx, mid)
            path.quadTo(rx, by - h * 0.04, cx, by)
            path.quadTo(lx, by - h * 0.04, lx, mid)
            path.lineTo(cx, ty)
            p.setPen(_pen())
            p.setBrush(QColor(r, g, b, 30))
            p.drawPath(path)
            # Lock body
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b, 210))
            kr = s * 0.10
            p.drawEllipse(QRectF(cx - kr, cy - kr * 0.2, kr * 2, kr * 2))
            # Lock shackle
            p.setPen(_pen(200, 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            sr = s * 0.085
            p.drawArc(QRectF(cx - sr, cy - sr * 2.4, sr * 2, sr * 2), 0, 180 * 16)

        elif self._tag == "i2p":
            # Triangle of three interconnected nodes
            nodes = [
                QPointF(cx + 6.5 * math.cos(math.radians(-90 + i * 120)),
                        cy + 6.5 * math.sin(math.radians(-90 + i * 120)))
                for i in range(3)
            ]
            p.setPen(_pen(170, 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(3):
                p.drawLine(nodes[i], nodes[(i + 1) % 3])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b, 235))
            for n in nodes:
                p.drawEllipse(QRectF(n.x() - 2.1, n.y() - 2.1, 4.2, 4.2))

        elif self._tag == "onion_server":
            # Signal arcs from a center point (hidden service broadcasting)
            src_y = cy + 4.5
            for rad, alpha in [(3.0, 230), (5.5, 150), (8.0, 70)]:
                p.setPen(_pen(alpha, 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(
                    QRectF(cx - rad, src_y - rad, rad * 2, rad * 2),
                    30 * 16, 120 * 16,
                )
            # Source dot
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b, 235))
            p.drawEllipse(QRectF(cx - 2.0, src_y - 2.0, 4.0, 4.0))


# ──────────────────────────────────────────────────────────────
#  ServiceCard  — compact
# ──────────────────────────────────────────────────────────────

_CARD_META: dict[str, tuple[str, str]] = {
    "tor":          ("TOR",          "Onion routing"),
    "dnscrypt":     ("DNSCRYPT",     "Encrypted DNS"),
    "i2p":          ("I2P",          "P2P overlay"),
    "onion_server": ("ONION SERVER", "Tor hidden service"),
}

_CARD_COLORS: dict[str, tuple[int, int, int]] = {
    "tor":          (138, 110, 210),
    "dnscrypt":     ( 78, 182, 255),
    "i2p":          ( 78, 210, 160),
    "onion_server": (210,  80, 180),
}
_CARD_COLORS_PIXEL: dict[str, tuple[int, int, int]] = {
    "tor":          (195, 195, 195),
    "dnscrypt":     (170, 170, 170),
    "i2p":          (210, 210, 210),
    "onion_server": (150, 150, 150),
}


class ServiceCard(QFrame):
    toggled          = pyqtSignal(str, bool)
    settings_clicked = pyqtSignal(str)

    @staticmethod
    def _resolve_accent(tag: str) -> tuple[int, int, int]:
        if theme()["name"] == "pixel":
            return _CARD_COLORS_PIXEL.get(tag, (180, 180, 180))
        return _CARD_COLORS[tag]

    def __init__(self, tag: str, checked: bool = True, parent=None):
        super().__init__(parent)
        self._tag = tag
        title, desc = _CARD_META[tag]
        ar, ag, ab  = self._resolve_accent(tag)

        self._current_status = ""

        self.setObjectName("serviceCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(140)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 14, 14)
        root.setSpacing(0)

        # ── icon circle (32×32) ────────────────────────────────
        self._icon_frame = QWidget()
        self._icon_frame.setFixedSize(32, 32)
        self._icon_frame.setObjectName("cardIconFrame")
        self._icon_frame.setStyleSheet(
            f"background: rgba({ar},{ag},{ab},0.13);"
            f"border: 1px solid rgba({ar},{ag},{ab},0.30);"
            f"border-radius: 16px;"
        )
        self._service_icon = _ServiceIcon(tag, ar, ag, ab)
        icon_inner = QVBoxLayout(self._icon_frame)
        icon_inner.setContentsMargins(0, 0, 0, 0)
        icon_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_inner.addWidget(self._service_icon)

        # ── toggle ─────────────────────────────────────────────
        self._toggle = ToggleSwitch(checked=checked)
        self._toggle.toggled.connect(lambda v: self.toggled.emit(self._tag, v))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addWidget(self._icon_frame)
        top.addStretch()
        top.addSpacing(8)
        top.addWidget(self._toggle)

        # ── title ──────────────────────────────────────────────
        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("cardTitle")
        self._title_lbl.setStyleSheet(
            f"color: rgba({ar},{ag},{ab},0.95);"
            f"font-size:{'5px' if theme()['name'] == 'pixel' else '11px'}; font-weight:700; letter-spacing:2px;"
            f"background:transparent;"
        )

        # ── desc + status row ──────────────────────────────────
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
        root.addWidget(self._title_lbl)
        root.addSpacing(6)
        root.addWidget(desc_lbl)
        root.addStretch()
        root.addLayout(bot)

    def refresh_theme(self) -> None:
        ar, ag, ab = self._resolve_accent(self._tag)
        self._icon_frame.setStyleSheet(
            f"background: rgba({ar},{ag},{ab},0.13);"
            f"border: 1px solid rgba({ar},{ag},{ab},0.30);"
            f"border-radius: 16px;"
        )
        self._service_icon._r = ar
        self._service_icon._g = ag
        self._service_icon._b = ab
        self._title_lbl.setStyleSheet(
            f"color: rgba({ar},{ag},{ab},0.95);"
            f"font-size:{'5px' if theme()['name'] == 'pixel' else '11px'}; font-weight:700; letter-spacing:2px;"
            f"background:transparent;"
        )
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        ar, ag, ab   = self._resolve_accent(self._tag)
        active       = self._current_status == "active"
        connecting   = self._current_status == "connecting"
        error        = self._current_status == "error"

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect   = QRectF(self.rect())
        radius = 16.0

        # ── clip to card outline ──────────────────────────────
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)

        # 1. Diagonal accent tint — stronger when active
        bg_alpha = 42 if active else (28 if connecting else 14)
        diag = QLinearGradient(0, 0, self.width() * 0.72, self.height() * 0.72)
        diag.setColorAt(0.0, QColor(ar, ag, ab, bg_alpha))
        diag.setColorAt(1.0, QColor(ar, ag, ab, 0))
        p.fillRect(rect, diag)

        # 2. Left vertical accent bar
        top_a = 255 if active else (200 if connecting else 140)
        bot_a = 70  if active else (50  if connecting else 22)
        bar   = QLinearGradient(0, 0, 0, self.height())
        bar.setColorAt(0.0, QColor(ar, ag, ab, top_a))
        bar.setColorAt(0.5, QColor(ar, ag, ab, (top_a + bot_a) // 2))
        bar.setColorAt(1.0, QColor(ar, ag, ab, bot_a))
        p.fillRect(QRectF(0, 0, 4, self.height()), bar)

        # 3. Icon area radial glow (top-left quadrant)
        glow = QLinearGradient(16, 16, 80, 80)
        glow.setColorAt(0.0, QColor(ar, ag, ab, 18 if active else 9))
        glow.setColorAt(1.0, QColor(ar, ag, ab, 0))
        p.fillRect(QRectF(4, 0, 76, 76), glow)

        # ── unclip for border ────────────────────────────────
        p.setClipping(False)

        # 4. Accent border (replaces generic CSS border when coloured state)
        if active or connecting or error:
            er, eg, eb = (ar, ag, ab) if not error else (210, 55, 48)
            # Outer soft halo
            halo = QPen(QColor(er, eg, eb, 28), 5.0)
            p.setPen(halo)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
            # Crisp inner border
            border_alpha = 190 if active else 130
            p.setPen(QPen(QColor(er, eg, eb, border_alpha), 1.5))
            p.drawRoundedRect(
                rect.adjusted(0.75, 0.75, -0.75, -0.75),
                radius - 0.5, radius - 0.5,
            )

    @property
    def is_checked(self) -> bool:
        return self._toggle.isChecked()

    def set_status(self, state: str) -> None:
        self._current_status = state
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
        self.update()   # repaint with new state

    def set_enabled_ui(self, enabled: bool) -> None:
        self._toggle.setEnabled(enabled)


# ──────────────────────────────────────────────────────────────
#  NetSpeedBar  — real-time download / upload speed
# ──────────────────────────────────────────────────────────────

class NetSpeedBar(QWidget):
    """Reads /proc/net/dev every second and shows ↓ / ↑ speeds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_rx:   int   = 0
        self._last_tx:   int   = 0
        self._last_time: float = 0.0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(20)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._down_lbl = QLabel("↓ —")
        self._down_lbl.setObjectName("speedLabel")
        self._up_lbl   = QLabel("↑ —")
        self._up_lbl.setObjectName("speedLabel")

        lay.addWidget(self._down_lbl)
        lay.addWidget(self._up_lbl)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.hide()

    # ── public ────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        if active:
            self._last_rx = self._last_tx = 0
            self._last_time = 0.0
            self._down_lbl.setText("↓ —")
            self._up_lbl.setText("↑ —")
            self.show()
        else:
            self.hide()

    # ── internals ─────────────────────────────────────────────

    @staticmethod
    def _read_bytes() -> tuple[int, int]:
        rx = tx = 0
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue
                    fields = data.split()
                    if len(fields) < 9:
                        continue
                    rx += int(fields[0])
                    tx += int(fields[8])
        except Exception:
            pass
        return rx, tx

    @staticmethod
    def _fmt(bps: float) -> str:
        if bps >= 1_048_576:
            return f"{bps / 1_048_576:.1f} MB/s"
        if bps >= 1_024:
            return f"{bps / 1_024:.0f} KB/s"
        return f"{max(0, bps):.0f} B/s"

    def _tick(self) -> None:
        rx, tx = self._read_bytes()
        now    = time.monotonic()

        if self._last_time > 0:
            dt = now - self._last_time
            if dt > 0 and self.isVisible():
                c       = theme()
                d_speed = max(0, rx - self._last_rx) / dt
                u_speed = max(0, tx - self._last_tx) / dt

                self._down_lbl.setText(f"↓  {self._fmt(d_speed)}")
                self._up_lbl.setText(f"↑  {self._fmt(u_speed)}")

                fs = "5px" if c["name"] == "pixel" else "10px"
                self._down_lbl.setStyleSheet(
                    f"color:{c['blue']};font-size:{fs};font-weight:700;"
                    "background:transparent;letter-spacing:1px;"
                )
                self._up_lbl.setStyleSheet(
                    f"color:{c['green']};font-size:{fs};font-weight:700;"
                    "background:transparent;letter-spacing:1px;"
                )

        self._last_rx   = rx
        self._last_tx   = tx
        self._last_time = now


# ──────────────────────────────────────────────────────────────
#  NetSpeedGraph  — real-time bandwidth graph (replaces NetSpeedBar)
# ──────────────────────────────────────────────────────────────

_HISTORY = 30  # seconds of rolling history


class NetSpeedGraph(QWidget):
    """Compact bandwidth graph: rolling 30-second line chart + current speed labels.

    Drop-in replacement for NetSpeedBar — same set_active() interface.
    """

    _FULL_H = 54  # fully-expanded height in pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        # Don't use setFixedHeight — we animate maximumHeight so the layout
        # smoothly pushes widgets below as the graph expands/collapses.
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)       # collapsed at start

        self._last_rx:   int   = 0
        self._last_tx:   int   = 0
        self._last_time: float = 0.0
        self._cur_down:  float = 0.0
        self._cur_up:    float = 0.0
        self._hist_down: deque[float] = deque(maxlen=_HISTORY)
        self._hist_up:   deque[float] = deque(maxlen=_HISTORY)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self._anim = QPropertyAnimation(self, b"maximumHeight")
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.hide()

    # ── public ────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        # Disconnect any pending hide callback from a previous collapse
        try:
            self._anim.finished.disconnect()
        except (TypeError, RuntimeError):
            pass

        if active:
            self._last_rx = self._last_tx = 0
            self._last_time = 0.0
            self._cur_down = self._cur_up = 0.0
            self._hist_down = deque(maxlen=_HISTORY)
            self._hist_up   = deque(maxlen=_HISTORY)
            self.show()
            self._anim.stop()
            self._anim.setStartValue(self.maximumHeight())
            self._anim.setEndValue(self._FULL_H)
            self._anim.start()
        else:
            self._anim.stop()
            self._anim.setStartValue(self.maximumHeight())
            self._anim.setEndValue(0)
            self._anim.finished.connect(self.hide)
            self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(300, self._FULL_H)

    # ── internals ─────────────────────────────────────────────

    @staticmethod
    def _read_bytes() -> tuple[int, int]:
        rx = tx = 0
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue
                    fields = data.split()
                    if len(fields) < 9:
                        continue
                    rx += int(fields[0])
                    tx += int(fields[8])
        except Exception:
            pass
        return rx, tx

    @staticmethod
    def _fmt(bps: float) -> str:
        if bps >= 1_048_576:
            return f"{bps / 1_048_576:.1f} MB/s"
        if bps >= 1_024:
            return f"{bps / 1_024:.0f} KB/s"
        return f"{max(0, bps):.0f} B/s"

    def _tick(self) -> None:
        rx, tx = self._read_bytes()
        now    = time.monotonic()
        if self._last_time > 0 and now > self._last_time:
            dt = now - self._last_time
            self._cur_down = max(0.0, (rx - self._last_rx) / dt)
            self._cur_up   = max(0.0, (tx - self._last_tx) / dt)
            self._hist_down.append(self._cur_down)
            self._hist_up.append(self._cur_up)
        self._last_rx   = rx
        self._last_tx   = tx
        self._last_time = now
        if self.isVisible():
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        c       = theme()
        W       = float(self.width())
        H       = float(self.height())
        LBL_W   = 88.0
        GAP     = 8.0
        GRAPH_W = 140.0

        # Graph centred horizontally; labels float to its right
        G_X = max(0.0, (W - GRAPH_W) / 2.0)
        G_W = GRAPH_W
        G_H = H - 8.0
        G_Y = 4.0

        if G_W < 10 or G_H < 6:
            p.end()
            return

        is_dark = c["name"] != "light"
        tx_x    = G_X + G_W + GAP

        # ── graph background ──────────────────────────────────
        p.fillRect(QRectF(G_X, G_Y, G_W, G_H),
                   QColor(128, 128, 128, 20 if is_dark else 8))

        down_pts = list(self._hist_down)
        up_pts   = list(self._hist_up)

        # ── labels (always visible) ───────────────────────────
        font = QFont()
        font.setPixelSize(10 if c["name"] != "pixel" else 7)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)

        p.setPen(QColor(c["blue"]))
        p.drawText(QRectF(tx_x, G_Y, LBL_W - 2, G_H / 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"↓  {self._fmt(self._cur_down)}")
        p.setPen(QColor(c["green"]))
        p.drawText(QRectF(tx_x, G_Y + G_H / 2, LBL_W - 2, G_H / 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"↑  {self._fmt(self._cur_up)}")

        # Need at least 2 real samples to draw a line
        n = min(len(down_pts), len(up_pts))
        if n < 2:
            return

        # ── scale ─────────────────────────────────────────────
        all_vals = down_pts + up_pts
        max_val  = max(max(all_vals), 2048.0)

        # Step width is fixed to _HISTORY so the x-scale stays constant
        # as the deque fills up.  New samples enter from the right.
        step    = G_W / (_HISTORY - 1)
        fill_a  = 28 if is_dark else 12

        def _draw_series(pts: list[float], hex_color: str) -> None:
            nn = len(pts)
            if nn < 2:
                return
            # Right-align: the newest point is always at G_X + G_W
            x0 = G_X + G_W - (nn - 1) * step

            color      = QColor(hex_color)
            fill_color = QColor(color)
            fill_color.setAlpha(fill_a)

            # Filled area under curve
            area = QPainterPath()
            area.moveTo(x0, G_Y + G_H)
            for i, val in enumerate(pts):
                x = x0 + i * step
                y = max(G_Y, min(G_Y + G_H,
                                 G_Y + G_H - (val / max_val) * G_H))
                area.lineTo(x, y)
            area.lineTo(x0 + (nn - 1) * step, G_Y + G_H)
            area.closeSubpath()
            p.fillPath(area, fill_color)

            # Line on top of fill
            p.setPen(QPen(color, 1.4,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            line = QPainterPath()
            for i, val in enumerate(pts):
                x = x0 + i * step
                y = max(G_Y, min(G_Y + G_H,
                                 G_Y + G_H - (val / max_val) * G_H))
                if i == 0:
                    line.moveTo(x, y)
                else:
                    line.lineTo(x, y)
            p.drawPath(line)

        _draw_series(down_pts, c["blue"])
        _draw_series(up_pts,   c["green"])
