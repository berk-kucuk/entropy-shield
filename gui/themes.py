from __future__ import annotations

DARK: dict[str, str] = {
    "name":          "dark",
    "bg":            "#080b10",
    "frame_bg":      "rgba(11,15,22,0.98)",
    "card_bg":       "rgba(14,19,28,0.93)",
    "card_bg_h":     "rgba(20,26,38,0.97)",
    "border":        "rgba(255,255,255,0.055)",
    "border_soft":   "rgba(255,255,255,0.028)",
    "border_hover":  "rgba(98,168,255,0.52)",
    "border_active": "rgba(58,204,114,0.62)",
    "border_error":  "rgba(240,80,80,0.62)",
    "text":          "#e6eaf2",
    "text_muted":    "#6e7e92",
    "text_dim":      "#38475a",
    "green":         "#3acc72",
    "blue":          "#62a8ff",
    "yellow":        "#dda04a",
    "red":           "#ef5050",
    "log_fg":        "#a8b8c8",
    "log_bg":        "rgba(6,9,14,0.92)",
    "scrollbar":     "rgba(255,255,255,0.07)",
    "input_bg":      "rgba(255,255,255,0.033)",
    "input_border":  "rgba(255,255,255,0.07)",
    "tab_active":    "#62a8ff",
    "title_btn":     "rgba(255,255,255,0.035)",
    "close_hover":   "#ef5050",
    "min_hover":     "#dda04a",
    "section_bg":    "rgba(255,255,255,0.018)",
}

LIGHT: dict[str, str] = {
    "name":          "light",
    "bg":            "#edf1f7",
    "frame_bg":      "rgba(244,248,255,0.98)",
    "card_bg":       "rgba(255,255,255,0.90)",
    "card_bg_h":     "rgba(255,255,255,0.99)",
    "border":        "rgba(20,36,60,0.09)",
    "border_soft":   "rgba(20,36,60,0.045)",
    "border_hover":  "rgba(0,85,238,0.45)",
    "border_active": "rgba(26,127,55,0.55)",
    "border_error":  "rgba(204,34,34,0.55)",
    "text":          "#16223a",
    "text_muted":    "#4a5f7a",
    "text_dim":      "#8896aa",
    "green":         "#1a7f37",
    "blue":          "#0055ee",
    "yellow":        "#a05a00",
    "red":           "#cc2222",
    "log_fg":        "#1e3450",
    "log_bg":        "rgba(255,255,255,0.78)",
    "scrollbar":     "rgba(20,36,60,0.12)",
    "input_bg":      "rgba(255,255,255,0.88)",
    "input_border":  "rgba(20,36,60,0.10)",
    "tab_active":    "#0055ee",
    "title_btn":     "rgba(20,36,60,0.04)",
    "close_hover":   "#cc2222",
    "min_hover":     "#a05a00",
    "section_bg":    "rgba(0,0,0,0.018)",
}

_current: dict[str, str] = DARK


def current() -> dict[str, str]:
    return _current


def set_theme(name: str) -> None:
    global _current
    _current = DARK if name == "dark" else LIGHT


def build_qss(c: dict[str, str]) -> str:
    is_dark = c["name"] == "dark"

    if is_dark:
        card_grad_stop0 = "rgba(22,28,42,0.96)"
        card_grad_hover_stop0 = "rgba(28,36,54,0.98)"
        connect_normal_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(22,28,42,0.97),stop:1 rgba(12,16,24,0.93))"
        )
        connect_hover_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(65,125,255,0.20),stop:1 rgba(35,75,200,0.08))"
        )
        connect_checked_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(42,185,95,0.25),stop:1 rgba(22,125,60,0.12))"
        )
    else:
        card_grad_stop0 = "rgba(255,255,255,0.95)"
        card_grad_hover_stop0 = "rgba(255,255,255,0.99)"
        connect_normal_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(240,245,255,0.95),stop:1 rgba(228,238,252,0.88))"
        )
        connect_hover_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(0,85,238,0.15),stop:1 rgba(0,55,180,0.06))"
        )
        connect_checked_grad = (
            "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(26,127,55,0.20),stop:1 rgba(10,90,40,0.08))"
        )

    return f"""
* {{
    font-family: 'Inter', 'SF Pro Display', 'Segoe UI', sans-serif;
    font-size: 12px;
    outline: none;
}}

QMainWindow {{
    background-color: {c['bg']};
}}

QWidget#outerBg {{
    background-color: {c['bg']};
}}

QWidget {{
    color: {c['text']};
}}

/* ── app header ── */
QWidget#appHeader {{
    background: transparent;
    border-bottom: 1px solid {c['border_soft']};
}}
QLabel#appTitle {{
    font-size: 14px;
    font-weight: 700;
    color: {c['text']};
    letter-spacing: 5px;
    background: transparent;
}}
QLabel#logoLbl {{
    background: transparent;
    border: none;
}}
QPushButton#settingsBtn {{
    background: {c['title_btn']};
    border: 1px solid {c['border']};
    border-radius: 14px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
    color: {c['text_dim']};
    font-size: 13px;
    padding: 0;
}}
QPushButton#settingsBtn:hover {{
    background: rgba(98,168,255,0.10);
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#quitBtn {{
    background: {c['title_btn']};
    border: 1px solid {c['border']};
    border-radius: 14px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
    color: {c['text_dim']};
    font-size: 13px;
    padding: 0;
}}
QPushButton#quitBtn:hover {{
    background: rgba(239,80,80,0.12);
    border-color: {c['close_hover']};
    color: {c['close_hover']};
}}

/* ── status labels ── */
QLabel#statusTitle {{
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 5px;
    background: transparent;
    color: {c['text_muted']};
}}
QLabel#statusSub {{
    font-size: 10px;
    color: {c['text_dim']};
    background: transparent;
    letter-spacing: 2px;
}}

/* ── service cards ── */
QFrame#serviceCard {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_grad_stop0},stop:1 {c['card_bg']});
    border: 1px solid {c['border']};
    border-radius: 16px;
}}
QFrame#serviceCard:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_grad_hover_stop0},stop:1 {c['card_bg_h']});
    border-color: {c['border_hover']};
}}
QFrame#serviceCard[status="active"] {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_grad_hover_stop0},stop:1 {c['card_bg_h']});
}}
QFrame#serviceCard[status="connecting"] {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {card_grad_hover_stop0},stop:1 {c['card_bg_h']});
}}
QLabel#cardTitle {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    background: transparent;
}}
QLabel#cardDesc {{
    font-size: 10px;
    color: {c['text_dim']};
    background: transparent;
}}
QLabel#cardDot {{
    font-size: 7px;
    color: {c['text_dim']};
    background: transparent;
}}
QLabel#cardDot[state="active"]     {{ color: {c['green']};  }}
QLabel#cardDot[state="connecting"] {{ color: {c['yellow']}; }}
QLabel#cardDot[state="error"]      {{ color: {c['red']};    }}
QLabel#cardStatus {{
    font-size: 9px;
    color: {c['text_dim']};
    letter-spacing: 1px;
    background: transparent;
}}
QLabel#cardStatus[state="active"]     {{ color: {c['green']};  }}
QLabel#cardStatus[state="connecting"] {{ color: {c['yellow']}; }}
QLabel#cardStatus[state="error"]      {{ color: {c['red']};    }}
QPushButton#gearBtn {{
    background: transparent;
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
    color: {c['text_dim']};
    font-size: 10px;
    padding: 0;
}}
QPushButton#gearBtn:hover {{
    background: rgba(98,168,255,0.06);
    color: {c['text_muted']};
    border-color: {c['border_hover']};
}}

/* ── log label ── */
QLabel#logLabel {{
    font-size: 8px;
    font-weight: 700;
    color: {c['text_dim']};
    letter-spacing: 3px;
    background: transparent;
}}

/* ── connect button ── */
QPushButton#connectBtn {{
    background: {connect_normal_grad};
    border: 1.5px solid {c['border']};
    border-radius: 14px;
    font-size: 11px;
    font-weight: 700;
    color: {c['text_muted']};
    letter-spacing: 7px;
    min-height: 54px;
}}
QPushButton#connectBtn:hover:!checked {{
    background: {connect_hover_grad};
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#connectBtn:checked {{
    background: {connect_checked_grad};
    border: 1.5px solid {c['border_active']};
    color: {c['green']};
}}
QPushButton#connectBtn:disabled {{
    background: {c['card_bg']};
    border-color: {c['border_soft']};
    color: {c['text_dim']};
}}

/* ── privacy browser buttons ── */
QPushButton#torBrowserBtn {{
    background: {c['card_bg']};
    border: 1.5px solid {c['border']};
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    color: {c['text_dim']};
    letter-spacing: 3px;
    min-height: 34px;
    padding: 0 14px;
}}
QPushButton#torBrowserBtn:enabled:hover {{
    background: rgba(98,168,255,0.10);
    border-color: {c['blue']};
    color: {c['blue']};
}}
QPushButton#torBrowserBtn:disabled {{
    opacity: 0.35;
}}
QPushButton#i2pBrowserBtn {{
    background: {c['card_bg']};
    border: 1.5px solid {c['border']};
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    color: {c['text_dim']};
    letter-spacing: 3px;
    min-height: 34px;
    padding: 0 14px;
}}
QPushButton#i2pBrowserBtn:enabled:hover {{
    background: rgba(58,204,114,0.10);
    border-color: {c['green']};
    color: {c['green']};
}}
QPushButton#i2pBrowserBtn:disabled {{
    opacity: 0.35;
}}

/* ── log ── */
QTextEdit#log {{
    background-color: {c['log_bg']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    color: {c['log_fg']};
    font-size: 10px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    padding: 8px 10px;
    selection-background-color: {c['blue']};
}}

/* ── settings panel ── */
QFrame#settingsCard {{
    background-color: {c['frame_bg']};
    border: 1px solid {c['border']};
    border-radius: 18px;
}}
QLabel#panelTitle {{
    font-size: 12px;
    font-weight: 700;
    color: {c['text']};
    letter-spacing: 4px;
}}
QLabel#settingSection {{
    font-size: 8px;
    color: {c['text_dim']};
    letter-spacing: 3px;
    font-weight: 700;
}}
QLabel#settingLabel {{
    font-size: 11px;
    color: {c['text_muted']};
}}
QPushButton#saveBtn {{
    background-color: {c['green']};
    border: none;
    border-radius: 10px;
    color: #ffffff;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    min-height: 36px;
    padding: 0 20px;
}}
QPushButton#saveBtn:hover {{
    border: 1px solid {c['green']};
}}
QPushButton#closeBtn {{
    background: transparent;
    border: 1px solid {c['border']};
    border-radius: 10px;
    color: {c['text_muted']};
    font-size: 11px;
    min-height: 36px;
    padding: 0 16px;
}}
QPushButton#closeBtn:hover {{
    border-color: {c['red']};
    color: {c['red']};
}}

/* ── inputs ── */
QSpinBox, QLineEdit {{
    background: {c['input_bg']};
    border: 1px solid {c['input_border']};
    border-radius: 9px;
    color: {c['text']};
    padding: 5px 10px;
    min-height: 28px;
    selection-background-color: {c['blue']};
}}
QSpinBox:focus, QLineEdit:focus {{ border-color: {c['blue']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent; border: none; width: 16px;
}}

/* ── tabs ── */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 12px;
    background-color: {c['card_bg']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {c['text_dim']};
    padding: 7px 14px;
    border: none;
    font-size: 9px;
    letter-spacing: 2px;
    font-weight: 700;
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
