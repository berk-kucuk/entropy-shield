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
    pyqtProperty, pyqtSignal, QRectF, QPointF, QSize, QPoint,
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient, QPainterPath,
    QConicalGradient,
)

from gui.themes import current as theme
from gui import icons


def _qc(rgb, a: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, int(a))))


# ──────────────────────────────────────────────────────────────
#  Frosted glass
# ──────────────────────────────────────────────────────────────
#
# The window background (_GlowFrame in main_window) renders, alongside the
# full-resolution backdrop, a heavily down-sampled copy of it every frame.
# Drawing a region of that tiny image scaled back up with smooth filtering is
# a cheap, convincing Gaussian-like blur: each GlassPanel shows exactly the
# blurred patch of aurora/effects that lies behind it, so the glass "moves"
# with the animated background like real frosted glass.

_backdrop = None   # widget with .frost() -> (QImage | None, scale)


def set_backdrop(provider) -> None:
    global _backdrop
    _backdrop = provider


def paint_frost(p: QPainter, widget: QWidget, target: QRectF) -> None:
    """Paint the blurred backdrop behind *widget* into *target* (widget coords)."""
    if _backdrop is None:
        return
    img, scale = _backdrop.frost()
    if img is None or img.isNull():
        return
    origin = widget.mapTo(_backdrop, QPoint(0, 0))
    src = QRectF((origin.x() + target.x()) / scale, (origin.y() + target.y()) / scale,
                 target.width() / scale, target.height() / scale)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(target, img, src)


class GlassPanel(QFrame):
    """A frosted-glass surface: blurred backdrop + tint + sheen + hairline."""

    def __init__(self, parent=None, radius: int | None = None, strong: bool = False):
        super().__init__(parent)
        self._radius = radius
        self._strong = strong
        self.setObjectName("glassPanel")

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = float(self._radius if self._radius is not None else c["radius"])
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        p.save()
        p.setClipPath(path)
        paint_frost(p, self, rect)
        # tint
        fill = c["glass_strong"] if self._strong else c["glass"]
        p.fillRect(rect, _qc(fill, fill[3]))
        # sheen: light falling from the top edge
        sheen = QLinearGradient(0, 0, 0, rect.height() * 0.55)
        hi = c["glass_hi"]
        sheen.setColorAt(0.0, QColor(255, 255, 255, hi))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(rect, sheen)
        p.restore()

        # hairline border + brighter top edge
        bw = float(c["bw"])
        gb = c["glass_border"]
        p.setBrush(Qt.BrushStyle.NoBrush)
        edge = QLinearGradient(0, 0, 0, rect.height())
        edge.setColorAt(0.0, _qc(gb, min(255, gb[3] * 2.2)))
        edge.setColorAt(0.35, _qc(gb, gb[3]))
        edge.setColorAt(1.0, _qc(gb, gb[3] * 0.7))
        p.setPen(QPen(edge, bw))
        half = bw / 2
        p.drawRoundedRect(rect.adjusted(half, half, -half, -half), r - half, r - half)


# ──────────────────────────────────────────────────────────────
#  ToggleSwitch
# ──────────────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._pos: float = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"handlePos")
        self._anim.setDuration(180)
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
        if self.isEnabled():
            self.setChecked(not self._checked)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        self.update()

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        t = self._pos
        w, h = float(self.width()), float(self.height())
        pixel = c["name"] == "pixel"
        tr = 3.0 if pixel else h / 2

        # track: off colour fades into the accent gradient
        off = QColor(255, 255, 255, 34) if c["dark"] else QColor(0, 0, 0, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(off)
        p.drawRoundedRect(QRectF(0, 0, w, h), tr, tr)
        if t > 0:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0.0, _qc(c["accent"], 255 * t))
            g.setColorAt(1.0, _qc(c["accent2"], 255 * t))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(0, 0, w, h), tr, tr)

        # knob with a soft shadow
        d = h - 6
        x = 3 + t * (w - d - 6)
        kr = 2.0 if pixel else d / 2
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRoundedRect(QRectF(x, 4, d, d), kr, kr)
        # knob: white when off, the accent's contrast colour when on (so a
        # white track on the dark themes keeps a visible, black knob)
        on = QColor(c["on_accent"])
        k = QColor(int(255 + (on.red() - 255) * t), int(255 + (on.green() - 255) * t),
                   int(255 + (on.blue() - 255) * t), 250)
        p.setBrush(k)
        p.drawRoundedRect(QRectF(x, 3, d, d), kr, kr)


# ──────────────────────────────────────────────────────────────
#  Spinner
# ──────────────────────────────────────────────────────────────

class Spinner(QWidget):
    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.hide()

    def start(self) -> None:
        self._angle = 0
        self.show()
        self._timer.start(16)

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _step(self) -> None:
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width() / 2 - 2
        p.translate(self.width() / 2, self.height() / 2)
        p.setPen(QPen(QColor(255, 255, 255, 40) if c["dark"] else QColor(0, 0, 0, 40), 2))
        p.drawEllipse(QRectF(-r, -r, r * 2, r * 2))
        p.rotate(self._angle)
        g = QConicalGradient(0, 0, 0)
        g.setColorAt(0.0, _qc(c["amber_rgb"]))
        g.setColorAt(0.75, _qc(c["amber_rgb"], 0))
        p.setPen(QPen(g, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(-r, -r, r * 2, r * 2), 0, 270 * 16)


# ──────────────────────────────────────────────────────────────
#  StatusRing — the state indicator in the hero panel
# ──────────────────────────────────────────────────────────────

class StatusRing(QWidget):
    def __init__(self, size: int = 96, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._state = "off"
        self._pulse = 0.5
        self._spin = 0.0
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._spin_step)

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "connecting":
            self._spin_timer.start(16)
        else:
            self._spin_timer.stop()
        self.update()

    def set_pulse(self, t: float) -> None:
        self._pulse = t
        self.update()

    def _spin_step(self) -> None:
        self._spin = (self._spin + 4.5) % 360
        self.update()

    def _color(self):
        glow = theme()["glow"]
        return glow.get(self._state, glow["off"])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if theme()["name"] == "circuit":
            self._paint_cpu_chip(p)
        else:
            self._paint_ring(p)

    # ── ring ──────────────────────────────────────────────────

    def _paint_ring(self, p: QPainter) -> None:
        c = theme()
        col = self._color()
        pixel = c["name"] == "pixel"
        W = float(self.width())
        cx = cy = W / 2
        R = W / 2 - 8
        live = self._state in ("on", "connecting", "error")
        inten = (0.55 + 0.45 * self._pulse) if self._state == "connecting" else 1.0

        p.setPen(Qt.PenStyle.NoPen)
        ring = QRectF(cx - R, cy - R, 2 * R, 2 * R)
        rr = 6.0 if pixel else R

        # glassy inner disc
        p.setBrush(QColor(255, 255, 255, 8) if c["dark"] else QColor(255, 255, 255, 150))
        p.drawRoundedRect(ring, rr, rr)

        # track
        p.setBrush(Qt.BrushStyle.NoBrush)
        track = QColor(255, 255, 255, 30) if c["dark"] else QColor(0, 0, 0, 30)
        p.setPen(QPen(track, 2.0))
        p.drawRoundedRect(ring, rr, rr)

        # indicator
        if self._state == "connecting":
            g = QConicalGradient(cx, cy, -self._spin)
            g.setColorAt(0.0, _qc(col))
            g.setColorAt(0.35, _qc(col, 0))
            g.setColorAt(1.0, _qc(col, 0))
            p.setPen(QPen(g, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawRoundedRect(ring, rr, rr)
        elif live:
            p.setPen(QPen(_qc(col), 3.0))
            p.drawRoundedRect(ring, rr, rr)

        self._paint_glyph(p, cx, cy, R * 0.42, col, inten)

    def _paint_glyph(self, p, cx, cy, s, col, inten) -> None:
        if self._state == "connecting":
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(3):
                ph = (self._spin / 360.0 * 2 * math.pi) * 2 - i * 0.9
                a = 0.35 + 0.65 * (math.sin(ph) + 1) / 2
                p.setBrush(_qc(col, 255 * a))
                d = s * 0.32
                x = cx + (i - 1) * s * 0.7
                p.drawEllipse(QRectF(x - d / 2, cy - d / 2, d, d))
            return
        name = {"on": "shield-check", "error": "shield-alert"}.get(self._state, "power")
        size = s * 2.2
        icons.draw(p, name, QRectF(cx - size / 2, cy - size / 2, size, size),
                   _qc(col, 240 * inten), stroke=1.6)

    # ── CPU chip (circuit theme) ──────────────────────────────

    def _paint_cpu_chip(self, p: QPainter) -> None:
        """IC/CPU chip shape — square body with pin pads, inner die, state symbol."""
        r, g, b   = self._color()
        intensity = (0.5 + 0.5 * self._pulse) if self._state == "connecting" else 0.90

        W  = float(self.width())
        cx = W / 2.0
        cy = float(self.height()) / 2.0

        chip_s = W * 0.60
        pin_w  = chip_s * 0.115
        pin_h  = chip_s * 0.072
        pin_n  = 4
        die_s  = chip_s * 0.52
        chip_x = cx - chip_s / 2
        chip_y = cy - chip_s / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b, int(150 * intensity)))
        sp = chip_s / (pin_n + 1)
        for i in range(1, pin_n + 1):
            px_ = chip_x + i * sp - pin_w / 2
            p.drawRect(QRectF(px_, chip_y - pin_h,  pin_w, pin_h))
            p.drawRect(QRectF(px_, chip_y + chip_s, pin_w, pin_h))
            py_ = chip_y + i * sp - pin_w / 2
            p.drawRect(QRectF(chip_x - pin_h,  py_, pin_h, pin_w))
            p.drawRect(QRectF(chip_x + chip_s, py_, pin_h, pin_w))

        body = QLinearGradient(chip_x, chip_y, chip_x, chip_y + chip_s)
        body.setColorAt(0.0, QColor(r, g, b, int(40 * intensity)))
        body.setColorAt(1.0, QColor(r, g, b, int(12 * intensity)))
        p.setPen(QPen(QColor(r, g, b, int(190 * intensity)), 1.6))
        p.setBrush(body)
        p.drawRoundedRect(QRectF(chip_x, chip_y, chip_s, chip_s), 5, 5)

        dot_r = chip_s * 0.06
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b, int(90 * intensity)))
        p.drawEllipse(QRectF(chip_x + chip_s * 0.115 - dot_r, chip_y + chip_s * 0.115 - dot_r,
                             dot_r * 2, dot_r * 2))

        die_x = cx - die_s / 2
        die_y = cy - die_s / 2
        p.setPen(QPen(QColor(r, g, b, int(90 * intensity)), 0.9))
        p.setBrush(QColor(r, g, b, int(10 * intensity)))
        p.drawRect(QRectF(die_x, die_y, die_s, die_s))

        self._paint_glyph(p, cx, cy, die_s * 0.36, (r, g, b), intensity)


# ──────────────────────────────────────────────────────────────
#  Layer icons
# ──────────────────────────────────────────────────────────────

class _LayerBadge(QWidget):
    """Rounded glass tile holding a layer icon; glows when the layer is live."""

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self._tag = tag
        self._state = ""
        self.setFixedSize(38, 38)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        live = self._state == "active"
        rgb = c["layers"][self._tag]
        rad = min(10.0, float(c["radius"]) * 0.6)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        p.setBrush(QColor(255, 255, 255, 10) if c["dark"] else QColor(0, 0, 0, 8))
        gb = c["glass_border"]
        p.setPen(QPen(_qc(gb, gb[3]), 1.0))
        p.drawRoundedRect(rect, rad, rad)
        ic = 20.0 if self._tag == "tor" else 18.0
        icons.draw(p, icons.LAYER_ICONS[self._tag],
                   QRectF(rect.center().x() - ic / 2, rect.center().y() - ic / 2, ic, ic),
                   _qc(rgb, 255 if live else 170))


# ──────────────────────────────────────────────────────────────
#  LayerRow — one privacy layer in the "Privacy layers" panel
# ──────────────────────────────────────────────────────────────

_LAYER_META: dict[str, tuple[str, str]] = {
    "tor":          ("Tor",          "Onion routing for all TCP traffic"),
    "dnscrypt":     ("DNSCrypt",     "Encrypted, authenticated DNS"),
    "i2p":          ("I2P",          "Invisible Internet overlay"),
    "onion_server": ("Onion Server", "Publish a folder as a .onion site"),
}

_STATE_TEXT = {"active": "ACTIVE", "connecting": "STARTING", "error": "ERROR", "": "READY"}


class LayerRow(QFrame):
    """Same interface the main window used with the old ServiceCard."""

    toggled          = pyqtSignal(str, bool)
    settings_clicked = pyqtSignal(str)

    def __init__(self, tag: str, checked: bool = True, parent=None):
        super().__init__(parent)
        self._tag = tag
        title, desc = _LAYER_META[tag]
        self.setObjectName("layerRow")
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(12)

        self._badge = _LayerBadge(tag)
        lay.addWidget(self._badge)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        name = QLabel(title)
        name.setObjectName("layerName")
        sub = QLabel(desc)
        sub.setObjectName("layerDesc")
        text.addStretch()
        text.addWidget(name)
        text.addWidget(sub)
        text.addStretch()
        lay.addLayout(text, stretch=1)

        self._status = QLabel(_STATE_TEXT[""])
        self._status.setObjectName("statePill")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._status, alignment=Qt.AlignmentFlag.AlignVCenter)

        gear = QPushButton()
        gear.setObjectName("gearBtn")
        gear.setIconSize(icons.qsize(15))
        self._gear = gear
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setToolTip(f"{title} settings")
        gear.clicked.connect(lambda: self.settings_clicked.emit(self._tag))
        lay.addWidget(gear)

        self._toggle = ToggleSwitch(checked=checked)
        self._toggle.setToolTip(f"Use {title}")
        self._toggle.toggled.connect(lambda v: self.toggled.emit(self._tag, v))
        lay.addWidget(self._toggle)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self._gear.setIcon(icons.icon("sliders-horizontal", theme()["text_muted"]))
        self._badge.update()
        self._toggle.update()
        self.update()

    @property
    def is_checked(self) -> bool:
        return self._toggle.isChecked()

    def set_status(self, state: str) -> None:
        self._status.setText(_STATE_TEXT.get(state, "READY"))
        self._status.setProperty("state", state)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._badge.set_state(state)

    def set_enabled_ui(self, enabled: bool) -> None:
        self._toggle.setEnabled(enabled)


# Backwards-compatible name.
ServiceCard = LayerRow


# ──────────────────────────────────────────────────────────────
#  NetSpeedGraph — live throughput in the hero panel
# ──────────────────────────────────────────────────────────────

_HISTORY = 40  # seconds of rolling history


class NetSpeedGraph(QWidget):
    """Rolling throughput chart with current ↓/↑ figures; expands when active."""

    _FULL_H = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)

        self._last_rx = self._last_tx = 0
        self._last_time = 0.0
        self._cur_down = self._cur_up = 0.0
        self._hist_down: deque[float] = deque(maxlen=_HISTORY)
        self._hist_up:   deque[float] = deque(maxlen=_HISTORY)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self._anim = QPropertyAnimation(self, b"maximumHeight")
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    def set_active(self, active: bool) -> None:
        try:
            self._anim.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        if active:
            self._last_rx = self._last_tx = 0
            self._last_time = 0.0
            self._cur_down = self._cur_up = 0.0
            self._hist_down = deque(maxlen=_HISTORY)
            self._hist_up = deque(maxlen=_HISTORY)
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
        now = time.monotonic()
        if self._last_time > 0 and now > self._last_time:
            dt = now - self._last_time
            self._cur_down = max(0.0, (rx - self._last_rx) / dt)
            self._cur_up = max(0.0, (tx - self._last_tx) / dt)
            self._hist_down.append(self._cur_down)
            self._hist_up.append(self._cur_up)
        self._last_rx, self._last_tx, self._last_time = rx, tx, now
        if self.isVisible():
            self.update()

    def paintEvent(self, _e) -> None:
        c = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = float(self.width()), float(self.height())
        if H < 14:
            return
        tm = QColor(c["text_muted"])
        down_c, up_c = c["accent"], (tm.red(), tm.green(), tm.blue())
        pixel = c["name"] == "pixel"

        # ── figures (left) ─────────────────────────────────────
        LBL_W = 118.0
        small = QFont()
        small.setPixelSize(7 if pixel else 10)
        big = QFont()
        big.setPixelSize(8 if pixel else 14)
        big.setWeight(QFont.Weight.DemiBold)
        muted = QColor(c["text_muted"])
        for i, (label, val, col) in enumerate((("Download", self._cur_down, down_c),
                                               ("Upload", self._cur_up, up_c))):
            y = 6 + i * (H - 6) / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qc(col))
            p.drawEllipse(QRectF(2, y + 4, 6, 6))
            p.setFont(small)
            p.setPen(muted)
            p.drawText(QRectF(14, y - 1, LBL_W, 14), Qt.AlignmentFlag.AlignLeft, label)
            p.setFont(big)
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(14, y + 11, LBL_W, 18), Qt.AlignmentFlag.AlignLeft, self._fmt(val))

        # ── chart (right) ──────────────────────────────────────
        gx, gy = LBL_W + 6, 6.0
        gw, gh = W - gx - 2, H - 12
        if gw < 20:
            return
        grid = QColor(255, 255, 255, 18) if c["dark"] else QColor(0, 0, 0, 18)
        p.setPen(QPen(grid, 1, Qt.PenStyle.DashLine))
        for f in (0.0, 0.5, 1.0):
            yy = gy + gh * f
            p.drawLine(QPointF(gx, yy), QPointF(gx + gw, yy))

        down, up = list(self._hist_down), list(self._hist_up)
        if min(len(down), len(up)) < 2:
            return
        top = max(max(down + up), 2048.0) * 1.15
        step = gw / (_HISTORY - 1)

        def series(pts, rgb) -> None:
            n = len(pts)
            x0 = gx + gw - (n - 1) * step
            ys = [gy + gh - (v / top) * gh for v in pts]
            line = QPainterPath()
            line.moveTo(x0, ys[0])
            for i in range(1, n):
                xa, xb = x0 + (i - 1) * step, x0 + i * step
                xm = (xa + xb) / 2
                line.cubicTo(xm, ys[i - 1], xm, ys[i], xb, ys[i])
            area = QPainterPath(line)
            area.lineTo(x0 + (n - 1) * step, gy + gh)
            area.lineTo(x0, gy + gh)
            area.closeSubpath()
            fill = QLinearGradient(0, gy, 0, gy + gh)
            fill.setColorAt(0.0, _qc(rgb, 34 if c["dark"] else 28))
            fill.setColorAt(1.0, _qc(rgb, 0))
            p.fillPath(area, fill)
            p.setPen(QPen(_qc(rgb), 1.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)

        series(down, down_c)
        series(up, up_c)
