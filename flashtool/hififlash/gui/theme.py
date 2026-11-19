""""Hi-Fi & elegant" (DECISIONS #9): the SAME colour tokens as the WebUI (``bridge/btbridge/webui/static/app.css``, checked by a test),
warm charcoal and ivory with a brass accent, serif display type, system sans for text. Light, dark and "follow the system"."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = {"bg": "#f5f2ea", "surface": "#fffdf8", "raised": "#ffffff", "ink": "#1d1b18", "muted": "#5f5a51", "line": "#e2dccf",
         "accent": "#7d5f25", "accent-ink": "#ffffff", "accent-soft": "#f1e6cd", "ok": "#2f6b45", "warn": "#8a5a00", "danger": "#9b2c2c",
         "focus": "#1f5fbf"}
DARK = {"bg": "#111113", "surface": "#19191d", "raised": "#202026", "ink": "#ecebe6", "muted": "#a39f95", "line": "#2c2c33",
        "accent": "#d2ac63", "accent-ink": "#17130a", "accent-soft": "#2b2416", "ok": "#7cc49a", "warn": "#e3b25a", "danger": "#ef8a8a",
        "focus": "#8fb6ff"}
SERIF = ["Iowan Old Style", "Palatino Linotype", "Palatino", "Book Antiqua", "P052", "TeX Gyre Pagella", "Georgia", "Times New Roman",
         "serif"]
SANS = ["Segoe UI", "SF Pro Text", "Helvetica Neue", "Roboto", "Noto Sans", "DejaVu Sans", "Arial", "sans-serif"]
MODES = ("system", "light", "dark")


def system_is_dark(app: QGuiApplication | None = None) -> bool:
    hints = (app or QGuiApplication.instance()).styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def tokens_for(mode: str, app: QGuiApplication | None = None) -> dict:
    if mode == "dark" or (mode not in ("light", "dark") and system_is_dark(app)):
        return DARK
    return LIGHT


def serif_font(point_size: float, weight=QFont.Weight.DemiBold) -> QFont:
    f = QFont()
    f.setFamilies(SERIF)
    f.setPointSizeF(point_size)
    f.setWeight(weight)
    return f


def palette(t: dict) -> QPalette:
    p = QPalette()
    c = {k: QColor(v) for k, v in t.items()}
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "raised"), (QPalette.ColorRole.AlternateBase, "surface"),
                      (QPalette.ColorRole.WindowText, "ink"), (QPalette.ColorRole.Text, "ink"), (QPalette.ColorRole.ButtonText, "ink"),
                      (QPalette.ColorRole.Button, "surface"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "accent-ink"), (QPalette.ColorRole.PlaceholderText, "muted"),
                      (QPalette.ColorRole.ToolTipBase, "raised"), (QPalette.ColorRole.ToolTipText, "ink"), (QPalette.ColorRole.Link, "accent")):
        p.setColor(role, c[key])
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, c["muted"])
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c["muted"])
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c["muted"])
    return p


def stylesheet(t: dict) -> str:
    return f"""
QWidget {{ color: {t['ink']}; font-size: 10.5pt; }}
QMainWindow, QDialog, #page {{ background: {t['bg']}; }}
#header {{ background: {t['surface']}; border-bottom: 1px solid {t['line']}; }}
#brand {{ color: {t['ink']}; }}
#eyebrow, .muted, QLabel[role="muted"] {{ color: {t['muted']}; }}
#eyebrow {{ letter-spacing: 2px; font-size: 8.5pt; }}
#steps {{ background: {t['surface']}; border-right: 1px solid {t['line']}; }}
#steps QLabel {{ color: {t['muted']}; padding: 6px 10px; border-radius: 10px; }}
#steps QLabel[current="true"] {{ color: {t['ink']}; background: {t['accent-soft']}; font-weight: 600; }}
#steps QLabel[done="true"] {{ color: {t['ok']}; }}
#footer {{ background: {t['surface']}; border-top: 1px solid {t['line']}; }}
QFrame[card="true"] {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 14px; }}
QLabel[state="ok"] {{ color: {t['ok']}; font-weight: 600; }}
QLabel[state="warn"] {{ color: {t['warn']}; font-weight: 600; }}
QLabel[state="error"] {{ color: {t['danger']}; font-weight: 600; }}
QFrame[panel="warn"] {{ border: 1px solid {t['warn']}; border-radius: 12px; background: {t['surface']}; }}
QFrame[panel="error"] {{ border: 1px solid {t['danger']}; border-radius: 12px; background: {t['surface']}; }}
QFrame[panel="ok"] {{ border: 1px solid {t['ok']}; border-radius: 12px; background: {t['surface']}; }}
QPushButton {{ background: {t['raised']}; border: 1px solid {t['line']}; border-radius: 999px; padding: 7px 16px; min-height: 22px; }}
QPushButton:hover {{ background: {t['accent-soft']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['surface']}; }}
QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['accent-ink']}; border-color: {t['accent']}; font-weight: 600; }}
QPushButton[primary="true"]:disabled {{ background: {t['line']}; color: {t['muted']}; border-color: {t['line']}; }}
QPushButton[danger="true"] {{ color: {t['danger']}; border-color: {t['danger']}; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QListWidget:focus, QCheckBox:focus, QRadioButton:focus, QTextBrowser:focus {{
    border: 2px solid {t['focus']}; }}
QLineEdit, QComboBox, QListWidget, QTextBrowser {{ background: {t['raised']}; border: 1px solid {t['line']}; border-radius: 10px;
    padding: 6px 8px; selection-background-color: {t['accent']}; selection-color: {t['accent-ink']}; }}
QListWidget::item {{ padding: 8px; border-radius: 8px; }}
QListWidget::item:selected {{ background: {t['accent-soft']}; color: {t['ink']}; }}
QListWidget::item:disabled {{ color: {t['muted']}; }}
QRadioButton, QCheckBox {{ spacing: 8px; padding: 3px; }}
QProgressBar {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; text-align: center; min-height: 18px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 7px; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: 12px; margin-top: 14px; padding: 12px; background: {t['surface']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {t['muted']}; }}
QToolTip {{ background: {t['raised']}; color: {t['ink']}; border: 1px solid {t['line']}; }}
"""


def apply(app, mode: str) -> dict:
    t = tokens_for(mode, app)
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t))
    return t


def _lum(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio."""
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
