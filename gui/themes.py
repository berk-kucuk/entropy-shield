from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
#  All themes use a strictly monochrome palette (black · grey · white).
#  Visual identity comes from corner radius, font, and background animation.
# ─────────────────────────────────────────────────────────────────────────────

# ── glow palettes ─────────────────────────────────────────────────────────────

_GLOW_OLED = {
    "off":        (90,  90,  90),
    "connecting": (170, 170, 170),
    "on":         (220, 220, 220),
    "error":      (210,  40,  40),
}
_GLOW_LIGHT = {
    "off":        (150, 150, 150),
    "connecting": (90,  90,  90),
    "on":         (20,  20,  20),
    "error":      (180,  30,  30),
}
_GLOW_BINARY = {
    "off":        (80,  80,  80),
    "connecting": (175, 175, 175),
    "on":         (215, 215, 215),
    "error":      (200,  40,  40),
}
_GLOW_CIRCUIT = {
    "off":        (75,  75,  80),
    "connecting": (155, 155, 160),
    "on":         (205, 205, 210),
    "error":      (195,  45,  45),
}
_GLOW_PIXEL = {
    "off":        (80,  80,  80),
    "connecting": (175, 175, 175),
    "on":         (215, 215, 215),
    "error":      (200,  40,  55),
}

# ── themes ────────────────────────────────────────────────────────────────────

# Pure OLED black — smooth, elegant
OLED: dict = {
    "name":          "oled",
    "bg":            "#000000",
    "bg_rgb":        (0, 0, 0),
    "frame_bg":      "#080808",
    "card_bg":       "#101010",
    "card_bg_h":     "#1a1a1a",
    "border":        "rgba(255,255,255,0.10)",
    "border_soft":   "rgba(255,255,255,0.05)",
    "border_hover":  "rgba(255,255,255,0.45)",
    "border_active": "rgba(230,230,230,0.80)",
    "border_error":  "rgba(210,40,40,0.80)",
    "text":          "#f2f2f2",
    "text_muted":    "#666666",
    "text_dim":      "#303030",
    "green":         "#e8e8e8",
    "blue":          "#aaaaaa",
    "yellow":        "#c8c8c8",
    "red":           "#f03c3c",
    "log_fg":        "#606060",
    "log_bg":        "#020202",
    "scrollbar":     "rgba(255,255,255,0.08)",
    "input_bg":      "rgba(255,255,255,0.04)",
    "input_border":  "rgba(255,255,255,0.10)",
    "tab_active":    "#e8e8e8",
    "title_btn":     "rgba(255,255,255,0.04)",
    "close_hover":   "#f03c3c",
    "min_hover":     "#c8c8c8",
    "section_bg":    "rgba(255,255,255,0.02)",
    "radius":        18,
    "glow":          _GLOW_OLED,
}

# Clean light — white panels, dark text
LIGHT: dict = {
    "name":          "light",
    "bg":            "#ececec",
    "bg_rgb":        (236, 236, 236),
    "frame_bg":      "#f8f8f8",
    "card_bg":       "#ffffff",
    "card_bg_h":     "#ffffff",
    "border":        "rgba(0,0,0,0.10)",
    "border_soft":   "rgba(0,0,0,0.05)",
    "border_hover":  "rgba(0,0,0,0.40)",
    "border_active": "rgba(10,10,10,0.70)",
    "border_error":  "rgba(180,30,30,0.65)",
    "text":          "#0a0a0a",
    "text_muted":    "#606060",
    "text_dim":      "#b0b0b0",
    "green":         "#111111",
    "blue":          "#444444",
    "yellow":        "#555555",
    "red":           "#c02020",
    "log_fg":        "#333333",
    "log_bg":        "#ffffff",
    "scrollbar":     "rgba(0,0,0,0.12)",
    "input_bg":      "rgba(0,0,0,0.04)",
    "input_border":  "rgba(0,0,0,0.12)",
    "tab_active":    "#111111",
    "title_btn":     "rgba(0,0,0,0.04)",
    "close_hover":   "#c02020",
    "min_hover":     "#444444",
    "section_bg":    "rgba(0,0,0,0.02)",
    "radius":        16,
    "glow":          _GLOW_LIGHT,
}

# Hacker/terminal — pure black, monospace, white binary rain
BINARY: dict = {
    "name":          "binary",
    "bg":            "#000000",
    "bg_rgb":        (0, 0, 0),
    "frame_bg":      "#070707",
    "card_bg":       "#0d0d0d",
    "card_bg_h":     "#161616",
    "border":        "rgba(255,255,255,0.11)",
    "border_soft":   "rgba(255,255,255,0.055)",
    "border_hover":  "rgba(255,255,255,0.48)",
    "border_active": "rgba(215,215,215,0.82)",
    "border_error":  "rgba(200,40,40,0.82)",
    "text":          "#e5e5e5",
    "text_muted":    "#808080",
    "text_dim":      "#363636",
    "green":         "#e5e5e5",
    "blue":          "#a0a0a0",
    "yellow":        "#c0c0c0",
    "red":           "#f03030",
    "log_fg":        "#787878",
    "log_bg":        "#000000",
    "scrollbar":     "rgba(255,255,255,0.09)",
    "input_bg":      "rgba(255,255,255,0.04)",
    "input_border":  "rgba(255,255,255,0.11)",
    "tab_active":    "#e5e5e5",
    "title_btn":     "rgba(255,255,255,0.04)",
    "close_hover":   "#f03030",
    "min_hover":     "#c0c0c0",
    "section_bg":    "rgba(255,255,255,0.025)",
    "radius":        14,
    "glow":          _GLOW_BINARY,
}

# PCB/circuit-board — very dark grey, circuit trace background, sharp corners
CIRCUIT: dict = {
    "name":          "circuit",
    "bg":            "#05050a",
    "bg_rgb":        (5, 5, 10),
    "frame_bg":      "#0c0c14",
    "card_bg":       "#111118",
    "card_bg_h":     "#191920",
    "border":        "rgba(200,200,210,0.10)",
    "border_soft":   "rgba(200,200,210,0.05)",
    "border_hover":  "rgba(220,220,230,0.45)",
    "border_active": "rgba(210,210,220,0.80)",
    "border_error":  "rgba(195,45,45,0.80)",
    "text":          "#d8d8e0",
    "text_muted":    "#606068",
    "text_dim":      "#2c2c36",
    "green":         "#d0d0d8",
    "blue":          "#9090a0",
    "yellow":        "#b0b0b8",
    "red":           "#e83838",
    "log_fg":        "#585868",
    "log_bg":        "#030308",
    "scrollbar":     "rgba(200,200,210,0.08)",
    "input_bg":      "rgba(200,200,210,0.04)",
    "input_border":  "rgba(200,200,210,0.10)",
    "tab_active":    "#d0d0d8",
    "title_btn":     "rgba(200,200,210,0.04)",
    "close_hover":   "#e83838",
    "min_hover":     "#b0b0b8",
    "section_bg":    "rgba(200,200,210,0.025)",
    "radius":        14,
    "glow":          _GLOW_CIRCUIT,
}

# Minecraft-like retro pixel — Pixeled font, chunky borders, dark grey bg
PIXEL: dict = {
    "name":          "pixel",
    "bg":            "#0c0c0c",
    "bg_rgb":        (12, 12, 12),
    "frame_bg":      "#131313",
    "card_bg":       "#1a1a1a",
    "card_bg_h":     "#242424",
    "border":        "rgba(255,255,255,0.18)",
    "border_soft":   "rgba(255,255,255,0.07)",
    "border_hover":  "rgba(255,255,255,0.60)",
    "border_active": "rgba(235,235,235,0.88)",
    "border_error":  "rgba(200,40,55,0.90)",
    "text":          "#e8e8e8",
    "text_muted":    "#888888",
    "text_dim":      "#484848",
    "green":         "#e8e8e8",
    "blue":          "#bebebe",
    "yellow":        "#d0d0d0",
    "red":           "#ee3838",
    "log_fg":        "#606060",
    "log_bg":        "#0c0c0c",
    "scrollbar":     "rgba(255,255,255,0.10)",
    "input_bg":      "rgba(255,255,255,0.05)",
    "input_border":  "rgba(255,255,255,0.18)",
    "tab_active":    "#e8e8e8",
    "title_btn":     "rgba(255,255,255,0.05)",
    "close_hover":   "#ee3838",
    "min_hover":     "#d0d0d0",
    "section_bg":    "rgba(255,255,255,0.03)",
    "radius":        12,
    "glow":          _GLOW_PIXEL,
}

# ── state ─────────────────────────────────────────────────────────────────────

_current: dict = OLED


def current() -> dict:
    return _current


def set_theme(name: str) -> None:
    global _current
    _current = {
        "oled":    OLED,
        "dark":    OLED,    # legacy alias
        "light":   LIGHT,
        "binary":  BINARY,
        "circuit": CIRCUIT,
        "pixel":   PIXEL,
    }.get(name, OLED)


# ── QSS builder ───────────────────────────────────────────────────────────────

def build_qss(c: dict) -> str:
    is_dark = c["name"] != "light"
    r       = c.get("radius", 16)

    if c["name"] == "oled":
        connect_normal  = "rgba(6,6,6,0.98)"
        connect_hover   = "rgba(255,255,255,0.09)"
        connect_checked = "rgba(220,220,220,0.14)"
        card_top        = "#0c0c0c"
        card_top_h      = "#181818"
    elif c["name"] == "binary":
        connect_normal  = "rgba(0,0,0,0.98)"
        connect_hover   = "rgba(255,255,255,0.09)"
        connect_checked = "rgba(215,215,215,0.14)"
        card_top        = "#080808"
        card_top_h      = "#121212"
    elif c["name"] == "circuit":
        connect_normal  = "rgba(5,5,10,0.98)"
        connect_hover   = "rgba(210,210,220,0.09)"
        connect_checked = "rgba(200,200,210,0.14)"
        card_top        = "#0e0e16"
        card_top_h      = "#161620"
    elif c["name"] == "pixel":
        connect_normal  = "rgba(6,6,6,0.98)"
        connect_hover   = "rgba(255,255,255,0.09)"
        connect_checked = "rgba(230,230,230,0.14)"
        card_top        = "#161616"
        card_top_h      = "#202020"
    else:  # light
        connect_normal  = "rgba(245,245,245,0.95)"
        connect_hover   = "rgba(0,0,0,0.08)"
        connect_checked = "rgba(10,10,10,0.14)"
        card_top        = "#ffffff"
        card_top_h      = "#ffffff"

    # Per-theme font
    if c["name"] == "pixel":
        font_family = "'Pixeled','Courier New','Lucida Console',monospace"
        font_size   = "9px"
    elif c["name"] == "binary":
        font_family = "'JetBrains Mono','Fira Code','Cascadia Code','Courier New',monospace"
        font_size   = "12px"
    else:
        font_family = "'Inter','SF Pro Display','Segoe UI','Noto Sans',sans-serif"
        font_size   = "12px"

    # Pixel theme uses thicker, blocky borders
    bw  = "2px" if c["name"] == "pixel" else "1px"
    bw2 = "2px" if c["name"] == "pixel" else "1.5px"

    rr  = r                     # widget border-radius
    rbtn = max(0, r - 6)        # button border-radius
    rinp = max(0, r - 7)        # input border-radius

    # Font size scaler: pixel theme scales all explicit sizes to ~5px base
    _ps = 9 / 12 if c["name"] == "pixel" else 1.0
    def pf(n: int) -> str:
        return f"{max(5, round(n * _ps))}px"

    return f"""
/* ── global ── */
* {{
    font-family: {font_family};
    font-size: {font_size};
    outline: none;
    box-sizing: border-box;
}}
QMainWindow {{ background: transparent; }}
QWidget#outerBg {{ background: transparent; }}
QWidget#glowFrame {{ background: transparent; }}
QWidget {{ color: {c['text']}; background: transparent; }}

/* ── title bar ── */
QWidget#appHeader {{
    background: transparent;
    border-bottom: 1px solid {c['border_soft']};
}}
QLabel#appTitle {{
    font-size: {pf(13)};
    font-weight: 800;
    color: {c['text']};
    letter-spacing: 6px;
    background: transparent;
}}
QLabel#logoLbl {{ background: transparent; border: none; }}

/* title buttons */
QPushButton#settingsBtn,
QPushButton#quitBtn,
QPushButton#minBtn {{
    background: {c['title_btn']};
    border: 1px solid {c['border']};
    border-radius: 13px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
    font-family: 'Inter','SF Pro Display','Segoe UI','Noto Sans',sans-serif;
    font-size: {pf(13)};
    padding: 0 0 1px 0;
    color: {c['text_dim']};
}}
QPushButton#minBtn:hover {{
    background: rgba(180,180,180,0.13);
    border-color: {c['min_hover']};
    color: {c['min_hover']};
}}
QPushButton#settingsBtn:hover {{
    background: rgba(180,180,180,0.10);
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#quitBtn:hover {{
    background: rgba(200,40,40,0.14);
    border-color: {c['close_hover']};
    color: {c['close_hover']};
}}

/* ── status ── */
QLabel#statusTitle {{
    font-size: {pf(15)};
    font-weight: 700;
    letter-spacing: 5px;
    background: transparent;
    color: {c['text_muted']};
}}
QLabel#statusSub {{
    font-size: {pf(9)};
    color: {c['text_dim']};
    background: transparent;
    letter-spacing: 3px;
}}

/* ── service cards ── */
QFrame#serviceCard {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_top}, stop:1 {c['card_bg']});
    border: {bw} solid {c['border']};
    border-radius: {rr}px;
}}
QFrame#serviceCard:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_top_h}, stop:1 {c['card_bg_h']});
    border-color: {c['border_hover']};
}}
QFrame#serviceCard[status="active"],
QFrame#serviceCard[status="connecting"] {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_top_h}, stop:1 {c['card_bg_h']});
}}
QLabel#cardTitle {{
    font-size: {pf(10)}; font-weight: 800;
    letter-spacing: 2px; background: transparent;
}}
QLabel#cardDesc  {{ font-size: {pf(9)}; color: {c['text_dim']}; background: transparent; }}
QLabel#cardDot   {{ font-size: {pf(7)}; color: {c['text_dim']}; background: transparent; }}
QLabel#cardDot[state="active"]     {{ color: {c['green']};  }}
QLabel#cardDot[state="connecting"] {{ color: {c['yellow']}; }}
QLabel#cardDot[state="error"]      {{ color: {c['red']};    }}
QLabel#cardStatus {{
    font-size: {pf(8)}; color: {c['text_dim']};
    letter-spacing: 1px; background: transparent;
}}
QLabel#cardStatus[state="active"]     {{ color: {c['green']};  }}
QLabel#cardStatus[state="connecting"] {{ color: {c['yellow']}; }}
QLabel#cardStatus[state="error"]      {{ color: {c['red']};    }}
QPushButton#gearBtn {{
    background: transparent;
    border: 1px solid {c['border_soft']};
    border-radius: {max(0, rr - 8)}px;
    color: {c['text_dim']}; font-size: {pf(10)}; padding: 0;
    min-width: 20px; max-width: 20px;
    min-height: 20px; max-height: 20px;
}}
QPushButton#gearBtn:hover {{
    background: rgba(180,180,180,0.07);
    color: {c['text_muted']};
    border-color: {c['border_hover']};
}}

/* ── connect button ── */
QPushButton#connectBtn {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {connect_normal}, stop:1 {c['card_bg']});
    border: {bw2} solid {c['border']};
    border-radius: {rr}px;
    font-size: {pf(11)}; font-weight: 800;
    color: {c['text_muted']};
    letter-spacing: 8px;
    min-height: 52px;
}}
QPushButton#connectBtn:hover:!checked {{
    background: {connect_hover};
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#connectBtn:checked {{
    background: {connect_checked};
    border: {bw2} solid {c['border_active']};
    color: {c['green']};
}}
QPushButton#connectBtn:disabled {{ opacity: 0.45; }}

/* ── browser buttons ── */
QPushButton#torBrowserBtn,
QPushButton#i2pBrowserBtn {{
    background: {c['card_bg']};
    border: {bw} solid {c['border']};
    border-radius: {rr}px;
    font-size: {pf(9)}; font-weight: 800;
    color: {c['text_dim']};
    letter-spacing: 3px;
    min-height: 34px; padding: 0 14px;
}}
QPushButton#torBrowserBtn:enabled:hover {{
    background: rgba(180,180,180,0.09);
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#i2pBrowserBtn:enabled:hover {{
    background: rgba(180,180,180,0.09);
    border-color: {c['green']};
    color: {c['green']};
}}
QPushButton#torBrowserBtn:disabled,
QPushButton#i2pBrowserBtn:disabled {{ opacity: 0.28; }}

/* ── tool buttons ── */
QPushButton#toolBtn {{
    background: {c['title_btn']};
    border: 1px solid {c['border']};
    border-radius: {rinp}px;
    color: {c['text_dim']}; font-size: {pf(8)}; font-weight: 700;
    letter-spacing: 1px; min-height: 22px; padding: 0 8px;
}}
QPushButton#toolBtn:enabled:hover {{
    background: rgba(180,180,180,0.09);
    border-color: {c['blue']}; color: {c['blue']};
}}
QPushButton#toolBtn:disabled {{ opacity: 0.25; }}

/* ── log ── */
QLabel#logLabel {{
    font-size: {pf(8)}; font-weight: 700;
    color: {c['text_dim']}; letter-spacing: 3px; background: transparent;
}}
QTextEdit#log {{
    background-color: {c['log_bg']};
    border: {bw} solid {c['border_soft']};
    border-radius: {rr}px;
    color: {c['log_fg']};
    font-size: {pf(10)};
    font-family: 'JetBrains Mono','Fira Code','Cascadia Code','Consolas',monospace;
    padding: 8px 10px;
    selection-background-color: {c['blue']};
}}

/* ── settings ── */
QFrame#settingsCard {{
    background-color: {c['frame_bg']};
    border: {bw} solid {c['border']};
    border-radius: {rr + 2}px;
}}
QLabel#panelTitle {{
    font-size: {pf(11)}; font-weight: 800;
    color: {c['text']}; letter-spacing: 5px;
}}
QLabel#settingSection {{
    font-size: {pf(8)}; color: {c['text_dim']};
    letter-spacing: 3px; font-weight: 700;
}}
QLabel#settingLabel {{ font-size: {pf(11)}; color: {c['text_muted']}; }}
QPushButton#saveBtn {{
    background-color: {c['green']};
    border: none; border-radius: {rbtn}px;
    color: {"#000000" if is_dark else "#ffffff"};
    font-size: {pf(11)}; font-weight: 700;
    letter-spacing: 2px; min-height: 38px; padding: 0 20px;
}}
QPushButton#saveBtn:hover {{ border: 1px solid {c['green']}; }}
QPushButton#closeBtn {{
    background: transparent;
    border: 1px solid {c['border']};
    border-radius: {rbtn}px;
    color: {c['text_muted']};
    font-size: {pf(11)}; min-height: 38px; padding: 0 16px;
}}
QPushButton#closeBtn:hover {{
    border-color: {c['red']}; color: {c['red']};
}}

/* ── inputs ── */
QSpinBox, QLineEdit {{
    background: {c['input_bg']};
    border: {bw} solid {c['input_border']};
    border-radius: {rinp}px;
    color: {c['text']}; padding: 5px 10px;
    min-height: 30px;
    selection-background-color: {c['blue']};
}}
QSpinBox:focus, QLineEdit:focus {{ border-color: {c['blue']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent; border: none; width: 16px;
}}

/* ── combo ── */
QComboBox {{
    background: {c['input_bg']};
    border: {bw} solid {c['input_border']};
    border-radius: {rinp}px;
    color: {c['text']}; padding: 5px 10px;
    min-height: 30px; min-width: 120px;
}}
QComboBox:focus {{ border-color: {c['blue']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {c['frame_bg']};
    border: 1px solid {c['border']};
    border-radius: {rinp}px;
    color: {c['text']};
    selection-background-color: {c['blue']};
    outline: none; padding: 4px;
}}

/* ── tabs ── */
QTabWidget::pane {{
    border: {bw} solid {c['border']};
    border-radius: {rr}px;
    background-color: {c['card_bg']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {c['text_dim']};
    padding: 7px 12px; border: none;
    font-size: {pf(8)}; letter-spacing: 2px; font-weight: 700;
}}
QTabBar::tab:selected {{
    color: {c['text']};
    border-bottom: 2px solid {c['tab_active']};
}}
QTabBar::tab:hover:!selected {{ color: {c['text_muted']}; }}

/* ── scrollbars ── */
QScrollBar:vertical {{
    background: transparent; width: 4px; border: none; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['scrollbar']}; border-radius: 2px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 4px; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c['scrollbar']}; border-radius: 2px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""
