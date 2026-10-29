from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
#  Monochrome glass themes.
#
#  Black, white and grey — with exactly two exceptions: each privacy layer's
#  icon carries its own colour, and the status title turns green when
#  protected / red when not.  Every theme is a frosted
#  glass UI over an animated backdrop; each glass panel shows a truly blurred
#  copy of what lies behind it (see _GlowFrame / GlassPanel).  The blur is
#  rendered inside the window, so it looks the same on every desktop and
#  compositor.
#
#  Dark themes share the OLED black base and differ only in their signature
#  backdrop effect, typeface and corner radius: OLED (plain), Binary (rain),
#  Circuit (PCB traces), Pixel (pixel blocks).  States are told apart by tone,
#  icon and wording rather than colour: protected = full white, connecting =
#  mid grey, off = dim grey, error = white with an alert icon.
# ─────────────────────────────────────────────────────────────────────────────


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


_DARK = dict(
    dark=True,
    bg_rgb=(6, 6, 6), bg2_rgb=(0, 0, 0),
    aurora=[((255, 255, 255), 10), ((160, 160, 160), 9), ((255, 255, 255), 5)],
    accent=(245, 245, 245), off_rgb=(115, 115, 115),
    amber_rgb=(170, 170, 170), red_rgb=(255, 255, 255),
    text="#f5f5f5", text_muted="#a3a3a3", text_dim="#5e5e5e",
    glass=(255, 255, 255, 9), glass_strong=(17, 17, 17, 236),
    glass_border=(255, 255, 255, 22), glass_hi=6, frost=150,
    log_fg="#a8a8a8", input_bg="rgba(255,255,255,0.05)",
    input_border="rgba(255,255,255,0.13)", scrollbar="rgba(255,255,255,0.15)",
    on_accent="#000000",
    status_ok=(34, 197, 94), status_bad=(239, 68, 68),
    layers={
        "tor":          (178, 128, 230),   # Tor purple
        "dnscrypt":     (56, 189, 248),    # sky blue
        "i2p":          (251, 191, 36),    # amber
        "onion_server": (251, 113, 133),   # rose
    },
    font="'Inter','SF Pro Display','Segoe UI Variable','Segoe UI','Noto Sans',sans-serif",
    font_px=13, radius=18, bw=1,
)


def _theme(**kw) -> dict:
    """Fill in the derived/compatibility keys every theme shares."""
    t = dict(kw)
    t["accent2"] = t["accent"]
    t["glow"] = {"off": t["off_rgb"], "connecting": t["amber_rgb"],
                 "on": t["accent"], "error": t["red_rgb"]}
    # Keys older code paths still read.
    t["green"]  = _hex(t["accent"])
    t["blue"]   = _hex(t["amber_rgb"])
    t["yellow"] = _hex(t["amber_rgb"])
    t["red"]    = _hex(t["red_rgb"])
    t["bg"]     = _hex(t["bg_rgb"])
    return t


# OLED — pure black, plain frosted glass
OLED = _theme(**{**_DARK, "name": "oled"})

# Light — white/grey frosted glass, black accents
LIGHT = _theme(
    name="light", dark=False,
    bg_rgb=(242, 242, 242), bg2_rgb=(228, 228, 228),
    aurora=[((255, 255, 255), 170), ((160, 160, 160), 30), ((255, 255, 255), 120)],
    accent=(12, 12, 12), off_rgb=(120, 120, 120),
    amber_rgb=(100, 100, 100), red_rgb=(0, 0, 0),
    text="#0a0a0a", text_muted="#525252", text_dim="#a3a3a3",
    glass=(255, 255, 255, 150), glass_strong=(251, 251, 251, 242),
    glass_border=(0, 0, 0, 22), glass_hi=0, frost=110,
    log_fg="#3f3f3f", input_bg="rgba(255,255,255,0.7)",
    input_border="rgba(0,0,0,0.13)", scrollbar="rgba(0,0,0,0.2)",
    on_accent="#ffffff",
    status_ok=(22, 163, 74), status_bad=(220, 38, 38),
    layers={
        "tor":          (125, 70, 152),
        "dnscrypt":     (2, 132, 199),
        "i2p":          (217, 119, 6),
        "onion_server": (225, 29, 72),
    },
    font=_DARK["font"], font_px=13, radius=18, bw=1,
)

# Binary — OLED black with white binary rain, monospace type
BINARY = _theme(**{**_DARK, "name": "binary", "radius": 12, "font_px": 12,
                   "font": "'JetBrains Mono','Fira Code','Cascadia Code','DejaVu Sans Mono',monospace"})

# Circuit — OLED black with grey PCB traces and a CPU-chip status
CIRCUIT = _theme(**{**_DARK, "name": "circuit", "radius": 12})

# Pixel — OLED black, square glass, drifting pixel blocks.  Pixeled is only
# legible large: it sets the headings and buttons, while body text uses a
# monospace face so descriptions and the log stay readable.
PIXEL = _theme(**{**_DARK, "name": "pixel", "radius": 4, "bw": 2, "font_px": 12,
                  "glass_border": (255, 255, 255, 36), "glass_hi": 0,
                  "font": "'JetBrains Mono','Fira Code','DejaVu Sans Mono',monospace",
                  "display_font": "'Pixeled','Press Start 2P','Courier New',monospace"})

THEMES = {"oled": OLED, "light": LIGHT, "binary": BINARY,
          "circuit": CIRCUIT, "pixel": PIXEL}

# ── state ─────────────────────────────────────────────────────────────────────

_current: dict = OLED


def current() -> dict:
    return _current


def set_theme(name: str) -> None:
    global _current
    _current = THEMES.get("oled" if name == "dark" else name, OLED)


def rgba(rgb, a) -> str:
    """CSS rgba() from an (r, g, b) tuple and an alpha in 0..255."""
    r, g, b = rgb[:3]
    return f"rgba({r},{g},{b},{a / 255:.3f})"


def px(c: dict, n: float) -> str:
    """Scale a font size designed for 13 px body text to this theme's typeface."""
    return f"{max(6, round(n * c['font_px'] / 13))}px"


# ── QSS builder ───────────────────────────────────────────────────────────────

def _display_rules(c: dict) -> str:
    """Headings/buttons in the theme's display face (pixel theme only)."""
    face = c.get("display_font")
    if not face:
        return ""
    sizes = {  # Pixeled renders roughly twice its nominal pixel size
        "QLabel#appTitle": 8, "QLabel#statusTitle": 13, "QLabel#sectionTitle": 6,
        "QLabel#layerName": 7, "QPushButton#connectBtn": 9, "QLabel#panelTitle": 10,
        "QLabel#statusBadge": 6, "QLabel#statePill": 6, "QLabel#settingSection": 6,
        "QPushButton#saveBtn": 7,
    }
    return "\n".join(f"{sel} {{{{ font-family: {face}; font-size: {n}px; letter-spacing: 0.5px; }}}}"
                     .replace("{{", "{").replace("}}", "}")
                     for sel, n in sizes.items())


def build_qss(c: dict) -> str:
    dark   = c["dark"]
    r      = c["radius"]
    bw     = c["bw"]
    acc    = c["accent"]
    red    = c["red_rgb"]
    border = rgba(c["glass_border"], c["glass_border"][3])
    hover  = "rgba(255,255,255,0.07)" if dark else "rgba(0,0,0,0.05)"
    press  = "rgba(255,255,255,0.11)" if dark else "rgba(0,0,0,0.08)"
    ghost  = "rgba(255,255,255,0.05)" if dark else "rgba(255,255,255,0.55)"
    on_acc = c["on_accent"]
    rs     = max(2, r - 6)          # small radius (buttons, inputs)
    rp     = min(8, max(2, r // 2))  # chips: must stay below half their height or Qt drops it

    return f"""
* {{
    font-family: {c['font']};
    font-size: {px(c, 13)};
    outline: none;
}}
QMainWindow, QWidget#outerBg, QWidget#glowFrame {{ background: transparent; }}
QWidget {{ color: {c['text']}; background: transparent; }}
QToolTip {{
    background: {rgba(c['glass_strong'], 245)};
    color: {c['text']};
    border: 1px solid {border};
    border-radius: {rs}px;
    padding: 6px 8px;
}}

/* ── title bar ── */
QLabel#appTitle {{
    font-size: {px(c, 15)}; font-weight: 700;
    letter-spacing: 0.4px; color: {c['text']};
}}
QLabel#mazeLinuxTag {{
    font-size: {px(c, 10)}; font-weight: 600; color: {c['text_muted']};
    background: {ghost}; border: 1px solid {border};
    border-radius: {rp}px; padding: 2px 8px;
}}
QPushButton#iconBtn, QPushButton#settingsBtn {{
    background: {ghost};
    border: {bw}px solid {border};
    border-radius: {min(15, r)}px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
    font-size: {px(c, 14)}; color: {c['text_muted']}; padding: 0 0 1px 0;
}}
QPushButton#iconBtn:hover, QPushButton#settingsBtn:hover {{
    background: {hover}; color: {c['text']};
}}
QPushButton#iconBtn[role="close"]:hover {{
    background: {rgba(red, 50)}; border-color: {rgba(red, 160)}; color: {_hex(red)};
}}
QPushButton#panicBtn {{
    background: transparent;
    border: {bw}px solid {rgba(red, 90)};
    border-radius: {min(15, r)}px;
    min-height: 30px; max-height: 30px; padding: 0 12px;
    font-size: {px(c, 11)}; font-weight: 700; letter-spacing: 0.6px;
    color: {_hex(red)};
}}
QPushButton#panicBtn:hover {{ background: {rgba(red, 30)}; border-color: {_hex(red)}; }}

/* ── hero / status ── */
QLabel#statusTitle {{
    font-size: {px(c, 24)}; font-weight: 700; letter-spacing: 0.2px;
    color: {c['text']};
}}
QLabel#statusTitle[state="on"]         {{ color: {_hex(c['status_ok'])}; }}
QLabel#statusTitle[state="off"]        {{ color: {_hex(c['status_bad'])}; }}
QLabel#statusTitle[state="error"]      {{ color: {_hex(c['status_bad'])}; }}
QLabel#statusTitle[state="connecting"] {{ color: {c['text_muted']}; }}
QLabel#statusSub {{ font-size: {px(c, 12)}; color: {c['text_muted']}; }}
QLabel#statusBadge {{
    font-size: {px(c, 10)}; font-weight: 700; letter-spacing: 1px;
    color: {c['text_muted']}; background: {ghost};
    border: 1px solid {border}; border-radius: {rp}px; padding: 3px 9px;
}}
QLabel#statusBadge[state="on"] {{
    color: {_hex(acc)}; background: transparent; border-color: {rgba(acc, 70)};
}}
QLabel#statusBadge[state="connecting"] {{
    color: {_hex(c['amber_rgb'])}; background: transparent;
    border-color: {rgba(c['amber_rgb'], 90)};
}}
QLabel#statusBadge[state="error"] {{
    color: {_hex(red)}; background: transparent; border-color: {rgba(red, 70)};
}}

/* ── section headers inside panels ── */
QLabel#sectionTitle {{
    font-size: {px(c, 11)}; font-weight: 700; letter-spacing: 1.4px;
    color: {c['text_muted']};
}}
QLabel#sectionHint {{ font-size: {px(c, 11)}; color: {c['text_dim']}; }}

/* ── layer rows ── */
QFrame#layerRow {{ background: transparent; border-radius: {rs}px; }}
QFrame#layerRow:hover {{ background: {hover}; }}
QFrame#layerDivider {{ background: {rgba(c['glass_border'], c['glass_border'][3] // 2 + 6)}; }}
QLabel#layerName {{ font-size: {px(c, 14)}; font-weight: 600; color: {c['text']}; }}
QLabel#layerDesc {{ font-size: {px(c, 11)}; color: {c['text_muted']}; }}
QLabel#statePill {{
    font-size: {px(c, 10)}; font-weight: 700; letter-spacing: 0.8px;
    color: {c['text_dim']}; background: transparent;
    border: 1px solid {border}; border-radius: {rp}px; padding: 2px 8px;
}}
QLabel#statePill[state="active"] {{
    color: {_hex(acc)}; background: transparent; border-color: {rgba(acc, 60)};
}}
QLabel#statePill[state="connecting"] {{
    color: {_hex(c['amber_rgb'])}; background: transparent;
    border-color: {rgba(c['amber_rgb'], 80)};
}}
QLabel#statePill[state="error"] {{
    color: {_hex(red)}; background: transparent; border-color: {rgba(red, 70)};
}}
QPushButton#gearBtn {{
    background: transparent; border: 1px solid transparent;
    border-radius: {min(13, r)}px; color: {c['text_dim']};
    font-size: {px(c, 14)}; padding: 0 0 1px 0;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
}}
QPushButton#gearBtn:hover {{ background: {hover}; border-color: {border}; color: {c['text']}; }}

/* ── connect button ── */
QPushButton#connectBtn {{
    background: {_hex(acc)};
    border: none; border-radius: {r - 2 if r > 6 else r}px;
    color: {on_acc}; font-size: {px(c, 15)}; font-weight: 700;
    letter-spacing: 0.6px; min-height: 52px;
}}
QPushButton#connectBtn:hover {{ background: {rgba(acc, 225)}; }}
QPushButton#connectBtn:pressed {{ background: {rgba(acc, 195)}; }}
QPushButton#connectBtn:checked {{
    background: {ghost}; border: {bw}px solid {border}; color: {c['text']};
}}
QPushButton#connectBtn:checked:hover {{
    background: {rgba(red, 22)}; border-color: {rgba(red, 140)}; color: {_hex(red)};
}}
QPushButton#connectBtn:disabled {{
    background: {ghost}; border: {bw}px solid {border}; color: {c['text_muted']};
}}

/* ── tool buttons ── */
QPushButton#toolBtn {{
    background: {ghost};
    border: {bw}px solid {border};
    border-radius: {rs}px;
    color: {c['text']}; font-size: {px(c, 12)}; font-weight: 600;
    min-height: 38px; padding: 0 10px; text-align: center;
}}
QPushButton#toolBtn:hover {{ background: {hover}; }}
QPushButton#toolBtn:pressed {{ background: {press}; }}
QPushButton#toolBtn:disabled {{ color: {c['text_dim']}; background: transparent; }}
QPushButton#ghostBtn {{
    background: transparent; border: 1px solid transparent;
    border-radius: {rs}px; color: {c['text_muted']};
    font-size: {px(c, 11)}; font-weight: 600; padding: 4px 9px;
}}
QPushButton#ghostBtn:hover {{ background: {hover}; border-color: {border}; color: {c['text']}; }}
QLabel#countBadge {{
    font-size: {px(c, 10)}; font-weight: 700; color: {c['text_muted']};
    background: {ghost}; border: 1px solid {border};
    border-radius: {rp}px; padding: 1px 7px;
}}
QLabel#logPreview {{ font-size: {px(c, 11)}; color: {c['text_dim']}; }}

/* ── log ── */
QTextEdit#log {{
    background: transparent; border: none;
    color: {c['log_fg']};
    font-family: 'JetBrains Mono','Fira Code','Cascadia Code','DejaVu Sans Mono',monospace;
    font-size: {px(c, 11) if c['name'] != 'pixel' else '10px'};
    padding: 0 2px;
    selection-background-color: {rgba(acc, 90)};
}}

/* ── settings ── */
QFrame#settingsCard {{
    background: {rgba(c['glass_strong'], c['glass_strong'][3])};
    border: {bw}px solid {border};
    border-radius: {r + 2}px;
}}
QLabel#panelTitle {{ font-size: {px(c, 18)}; font-weight: 700; color: {c['text']}; }}
QLabel#settingSection {{
    font-size: {px(c, 11)}; font-weight: 700; letter-spacing: 1.4px;
    color: {_hex(acc)}; padding-top: 4px;
}}
QLabel#settingLabel {{ font-size: {px(c, 12)}; color: {c['text_muted']}; }}
QPushButton#saveBtn {{
    background: {_hex(acc)};
    border: none; border-radius: {rs}px; color: {on_acc};
    font-size: {px(c, 13)}; font-weight: 700; min-height: 40px; padding: 0 22px;
}}
QPushButton#saveBtn:hover {{ background: {rgba(acc, 225)}; }}
QPushButton#closeBtn {{
    background: {ghost}; border: {bw}px solid {border};
    border-radius: {rs}px; color: {c['text']};
    font-size: {px(c, 12)}; min-height: 40px; padding: 0 16px;
}}
QPushButton#closeBtn:hover {{ background: {hover}; }}

/* ── inputs ── */
QSpinBox, QLineEdit, QPlainTextEdit, QTextEdit#bridgeLines, QTableWidget {{
    background: {c['input_bg']};
    border: {bw}px solid {c['input_border']};
    border-radius: {rs}px;
    color: {c['text']}; padding: 5px 10px; min-height: 22px;
    selection-background-color: {rgba(acc, 110)};
}}
QSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextEdit#bridgeLines:focus {{ border-color: {rgba(acc, 200)}; }}
QSpinBox::up-button, QSpinBox::down-button {{ background: transparent; border: none; width: 16px; }}
QHeaderView::section {{
    background: transparent; color: {c['text_muted']}; border: none;
    border-bottom: 1px solid {c['input_border']}; padding: 4px 6px;
    font-size: {px(c, 11)}; font-weight: 600;
}}
QTableWidget {{ gridline-color: {c['input_border']}; padding: 2px; }}
QComboBox {{
    background: {c['input_bg']};
    border: {bw}px solid {c['input_border']};
    border-radius: {rs}px;
    color: {c['text']}; padding: 5px 10px; min-height: 22px; min-width: 110px;
}}
QComboBox:focus {{ border-color: {rgba(acc, 200)}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {rgba(c['glass_strong'], 250)};
    border: 1px solid {border}; border-radius: {rs}px;
    color: {c['text']}; padding: 4px;
    selection-background-color: {rgba(acc, 70)};
}}
QCheckBox {{ color: {c['text_muted']}; spacing: 8px; }}

/* ── tabs (settings) ── */
QTabWidget::pane {{ border: none; background: transparent; top: 0; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {c['text_muted']};
    padding: 6px 11px; margin: 0 2px 6px 0;
    border: 1px solid transparent; border-radius: {rp}px;
    font-size: {px(c, 11)}; font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {c['text']}; background: {hover}; border-color: {border};
}}
QTabBar::tab:hover:!selected {{ background: {hover}; color: {c['text']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

{_display_rules(c)}
/* ── scrollbars ── */
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scrollbar']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scrollbar']}; border-radius: 3px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ width: 0; background: none; }}
"""
