# -*- coding: utf-8 -*-
"""Тема Censr v6 «тихий терминал»: тёмный фон, мятный акцент, моноширинные
данные по колонкам, волосяные разделители вместо карточек."""

from __future__ import annotations

BG = "#131519"          # тёмный, чуть тёплый
SURFACE = "#1b1e23"     # поля ввода, диалоги
HAIR = "#1e2127"        # волосяные линии
TEXT = "#e7ecf2"
DIM = "#5e6b7a"
FAINT = "#3a4149"       # очень тусклый (очередь, прочерки)
ACCENT = "#5ad19b"      # мятный
ACCENT_HOVER = "#6fe0ad"
ACCENT_DOWN = "#46b986"
ACCENT_BG = "#173026"   # мятная подложка (чипы, активный сегмент)
ON_ACCENT = "#04140a"
RED = "#e5686d"

MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
SANS = '"Segoe UI Variable", "Segoe UI", sans-serif'

QSS = f"""
* {{
    font-family: {SANS};
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}

#wordmark {{ font-family: {MONO}; font-size: 14px; letter-spacing: 1px; color: {TEXT}; }}
#topLink {{
    background: transparent;
    border: none;
    color: {DIM};
    font-family: {MONO};
    font-size: 12px;
    padding: 6px 8px;
    border-radius: 6px;
}}
#topLink:hover {{ color: {ACCENT}; }}

#heroHint {{ color: {DIM}; font-size: 11px; font-family: {MONO}; }}
#pvTitle {{ font-size: 22px; font-weight: 300; color: {TEXT}; }}
#pvLine {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#ghostBtn {{
    background: transparent;
    border: 1px solid {HAIR};
    border-radius: 10px;
    color: {TEXT};
    font-family: {MONO};
    font-size: 12px;
    padding: 12px 18px;
}}
#ghostBtn:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}

QPushButton {{
    background: transparent;
    border: 1px solid {HAIR};
    border-radius: 8px;
    padding: 9px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {DIM}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {SURFACE}; }}

QPushButton#primary {{
    background: {ACCENT};
    color: {ON_ACCENT};
    border: none;
    font-family: {MONO};
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 1px;
    padding: 13px 24px;
    border-radius: 9px;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton#primary:disabled {{ background: {SURFACE}; color: {DIM}; }}

QScrollArea {{ border: none; background: transparent; }}
#fileList {{ background: transparent; }}

#fileRow {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {HAIR};
}}
#rowNum {{ font-family: {MONO}; font-size: 11px; font-weight: 700; color: {ACCENT}; }}
#fileName {{ font-family: {MONO}; font-size: 12px; color: {TEXT}; }}
#colDur {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#rowClose {{
    background: transparent;
    border: none;
    color: {FAINT};
    font-size: 14px;
    padding: 0 2px;
}}
#rowClose:hover {{ color: {RED}; }}
#rowExpand {{
    border: none;
    border-radius: 10px;
    background: transparent;
}}
#rowExpand[open="false"] {{ background: {ACCENT_BG}; }}
#rowExpand[open="false"]:hover {{ background: #1d3a2d; }}
#rowExpand[open="true"] {{ background: {ACCENT}; }}
#rowExpand[open="true"]:hover {{ background: {ACCENT_HOVER}; }}
#rowTracks {{ font-family: {MONO}; font-size: 10px; color: {DIM}; }}
#trackPanel {{ background: transparent; }}
#trackHint {{ font-size: 11px; color: {DIM}; }}
#trackAll {{
    background: transparent;
    border: none;
    color: {ACCENT};
    font-size: 11px;
    padding: 0;
}}
#trackAll:hover {{ color: {ACCENT_HOVER}; }}
#trackCheck {{ font-size: 12px; color: {TEXT}; spacing: 10px; padding: 0; }}
#trackCheck:disabled {{ color: {DIM}; }}
#trackCheck::indicator {{
    width: 16px; height: 16px;
    border-radius: 5px;
    border: 1px solid {DIM};
    background: {SURFACE};
}}
#trackCheck::indicator:hover {{ border-color: {ACCENT}; }}
#trackCheck::indicator:checked {{
    border: 1px solid {ACCENT};
    background: {ACCENT};
    image: url(__CHECK_ICON__);
}}
#trackCheck::indicator:checked:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
#linkAdd {{
    background: transparent;
    border: none;
    color: {ACCENT};
    font-family: {MONO};
    font-size: 12px;
    font-weight: 700;
    padding: 4px 6px;
}}
#linkAdd:hover {{ color: {ACCENT_HOVER}; }}
#linkDim {{
    background: transparent;
    border: none;
    color: {FAINT};
    font-family: {MONO};
    font-size: 11px;
    padding: 4px 6px;
}}
#linkDim:hover {{ color: {DIM}; }}

QProgressBar {{
    background: {HAIR};
    border: none;
    border-radius: 1px;
    max-height: 2px;
}}
QProgressBar::chunk {{ background: {ACCENT}; }}

QComboBox, QLineEdit, QSpinBox, QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid {HAIR};
    border-radius: 8px;
    padding: 8px 10px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {HAIR};
    border-radius: 8px;
    selection-background-color: #173026;
}}

QSlider::groove:horizontal {{ background: {HAIR}; height: 4px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 16px; height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HOVER}; }}

#footerNote {{ color: {DIM}; font-size: 11px; font-family: {MONO}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {HAIR}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {HAIR};
    padding: 6px;
}}
QMessageBox {{ background: {BG}; }}

#dlgOk {{
    background: {ACCENT};
    color: {ON_ACCENT};
    border: none;
    border-radius: 8px;
    font-family: {MONO};
    font-weight: 700;
    font-size: 11px;
    padding: 8px 20px;
}}
#dlgOk:hover {{ background: {ACCENT_HOVER}; }}
#dlgOk:pressed {{ background: {ACCENT_DOWN}; }}
#dlgCancel {{
    background: transparent;
    border: 1px solid {HAIR};
    border-radius: 8px;
    color: {DIM};
    font-family: {MONO};
    font-size: 11px;
    padding: 8px 16px;
}}
#dlgCancel:hover {{ border-color: {DIM}; color: {TEXT}; }}
#dlgFrame {{ background: {BG}; border: 1px solid {HAIR}; border-radius: 12px; }}
#dlgBar {{ background: transparent; }}
#dlgClose {{
    background: transparent;
    border: none;
    color: {DIM};
    font-family: {MONO};
    font-size: 13px;
    border-radius: 7px;
}}
#dlgClose:hover {{ background: #c0392b; color: #fff; }}
#dlgTitle {{ font-family: {MONO}; font-size: 15px; color: {TEXT}; }}
#dlgSub {{ font-family: {MONO}; font-size: 10px; color: {DIM}; }}
#dlgKey {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#aboutTag {{ font-family: {SANS}; font-size: 17px; font-weight: 300; color: {TEXT}; }}
#aboutChk {{ font-family: {MONO}; font-size: 13px; font-weight: 700; color: {ACCENT}; }}
#aboutItem {{ font-family: {MONO}; font-size: 12px; color: {TEXT}; }}
#aboutVer {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#aboutLink {{ font-family: {MONO}; font-size: 13px; color: {ACCENT}; }}
#secTitle {{ font-family: {MONO}; font-size: 12px; color: {TEXT}; }}
#secCount {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#doneOpen {{ background: transparent; border: none; border-radius: 7px; }}
#doneOpen:hover {{ background: rgba(90, 209, 155, 0.22); }}
#doneOpen:pressed {{ background: rgba(90, 209, 155, 0.36); }}
#rcptHdr {{ font-family: {MONO}; font-size: 9px; color: {FAINT}; }}
#rcptHead {{ font-family: {MONO}; font-size: 13px; color: {ACCENT}; letter-spacing: 2px; }}
#rcptDash {{ background: transparent; border: none; border-top: 1px dashed {FAINT}; }}
#rcptName {{ font-family: {MONO}; font-size: 12px; color: {TEXT}; }}
#rcptStat {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#rcptKvK {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#rcptKvV {{ font-family: {MONO}; font-size: 11px; color: {TEXT}; }}
#reviewBtn {{ background: {ACCENT_BG}; color: {ACCENT}; border: none; border-radius: 11px; font-size: 11px; padding: 4px 11px; }}
#reviewBtn:hover {{ background: #1d3a2d; }}
#reviewBtn:pressed {{ background: {ACCENT_DOWN}; color: {ON_ACCENT}; }}
#rsRootBadge {{ background: {ACCENT_BG}; color: {ACCENT}; border-radius: 9px; font-size: 9px; padding: 2px 0; }}
#rsRiskBadge {{ background: #3a2e12; color: #e0a13a; border-radius: 9px; font-size: 9px; padding: 2px 0; }}
#timeInput {{ font-family: {MONO}; font-size: 13px; }}
#cancelTitle {{ font-family: {MONO}; font-size: 16px; color: #e0a13a; letter-spacing: 1px; }}
QFrame#dlgSep {{ background: {HAIR}; max-height: 1px; min-height: 1px; border: none; }}
#modeChip {{
    background: transparent;
    border: 1px solid {HAIR};
    border-radius: 8px;
    color: {DIM};
    font-family: {MONO};
    font-size: 12px;
    padding: 8px 14px;
    text-align: left;
}}
#modeChip:hover {{ border-color: {DIM}; }}
#modeChip:checked {{ background: {ACCENT_BG}; border-color: {ACCENT_BG}; color: {ACCENT}; }}
#pathLabel {{ font-family: {MONO}; font-size: 10px; color: {DIM}; padding: 2px 2px 0 0; }}
"""


def _check_icon_path() -> str:
    """Рисует тёмную галочку (для отмеченного мятного чекбокса) и возвращает путь.
    Путь с прямыми слэшами — так его понимает Qt StyleSheet."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    from .settings import _config_dir
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "check.png"
    s = 32                                  # ×2 для чёткости на HiDPI
    img = QImage(s, s, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    pt = QPainter(img)
    pt.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(ON_ACCENT))           # тёмная галочка на мятном фоне
    pen.setWidthF(3.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    pt.setPen(pen)
    pt.drawPolyline([QPointF(7, 17), QPointF(13.5, 23), QPointF(25, 9)])
    pt.end()
    img.save(str(p), "PNG")
    return str(p).replace("\\", "/")


def apply(app) -> None:
    app.setStyle("Fusion")
    try:
        qss = QSS.replace("__CHECK_ICON__", _check_icon_path())
    except Exception:
        qss = QSS.replace("__CHECK_ICON__", "")   # без иконки чекбокс всё равно мятный
    app.setStyleSheet(qss)
