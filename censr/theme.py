# -*- coding: utf-8 -*-
"""Тема Censr v7 «quiet tech»: почти чёрный фон, индиго-акцент, тонкие рамки
и тихая SaaS-эстетика; моноширинный шрифт остаётся только у данных
(номера, таймкоды, длительности)."""

from __future__ import annotations

BG = "#0a0a0b"          # почти чёрный
SURFACE = "#121215"     # поля ввода, чипы, тосты
HAIR = "#1c1c21"        # волосяные линии
BORDER = "#232327"      # рамки кнопок/чипов (чуть ярче волосяной)
TEXT = "#ededf0"
DIM = "#85858f"
FAINT = "#55555e"       # очень тусклый (очередь, прочерки)
ACCENT = "#9aa4f0"      # светлый индиго — текст, иконки, счётчики
ACCENT_HOVER = "#b6bdf5"
ACCENT_DOWN = "#7c87e0"
ACCENT_BG = "rgba(94, 106, 210, 0.16)"   # индиго-подложка (чипы, активный сегмент)
ACCENT_BRD = "#3a3f63"  # рамка активного чипа/бейджа
PRIMARY = "#5e6ad2"     # индиго — заливка главных кнопок
PRIMARY_HOVER = "#6d79e0"
PRIMARY_DOWN = "#4f5ac2"
PRIMARY_EDGE = "#7c87e0"  # светлая кромка primary-кнопки
ON_ACCENT = "#ffffff"
GREEN = "#4ade80"       # успех (галочки, точка «готово»)
RED = "#e5686d"
AMBER = "#fbbf24"       # предупреждение/стоп (тот же, что у бейджа «похоже?»)

MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
SANS = '"Segoe UI Variable", "Segoe UI", sans-serif'

QSS = f"""
* {{
    font-family: {SANS};
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}

#wordmark {{ font-family: {MONO}; font-size: 14px; font-weight: 700; letter-spacing: 1px; color: {TEXT}; }}
#topLink {{
    background: transparent;
    border: none;
    color: {DIM};
    font-size: 12px;
    padding: 6px 8px;
    border-radius: 6px;
}}
#topLink:hover {{ color: {TEXT}; }}

#heroHint {{ color: {DIM}; font-size: 12px; }}
#pvTitle {{ font-size: 22px; font-weight: 600; color: {TEXT}; }}
#ghostBtn {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: #b9b9c4;
    font-size: 12px;
    padding: 12px 18px;
}}
#ghostBtn:hover {{ border-color: {ACCENT_BRD}; color: {TEXT}; }}

QPushButton {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 9px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {DIM}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {SURFACE}; }}

QPushButton#primary {{
    background: {PRIMARY};
    color: {ON_ACCENT};
    border: 1px solid {PRIMARY_EDGE};
    font-weight: 600;
    font-size: 13px;
    letter-spacing: 1px;
    padding: 13px 24px;
    border-radius: 10px;
}}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary:pressed {{ background: {PRIMARY_DOWN}; }}
QPushButton#primary:disabled {{ background: {SURFACE}; color: {DIM}; border-color: {SURFACE}; }}

QScrollArea {{ border: none; background: transparent; }}
#fileList {{ background: transparent; }}

#fileRow {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {HAIR};
}}
#rowNum {{ font-family: {MONO}; font-size: 11px; font-weight: 700; color: {FAINT}; }}
#fileName {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
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
#rowExpand[open="false"]:hover {{ background: rgba(94, 106, 210, 0.28); }}
#rowExpand[open="true"] {{ background: {PRIMARY}; }}
#rowExpand[open="true"]:hover {{ background: {PRIMARY_HOVER}; }}
#rowTracks {{ font-family: {MONO}; font-size: 11px; color: {ACCENT}; }}
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
    border: 1px solid #33333b;
    background: {SURFACE};
}}
#trackCheck::indicator:hover {{ border-color: {ACCENT}; }}
#trackCheck::indicator:checked {{
    border: 1px solid {PRIMARY_EDGE};
    background: {PRIMARY};
    image: url(__CHECK_ICON__);
}}
#trackCheck::indicator:checked:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
#linkAdd {{
    background: transparent;
    border: none;
    color: {ACCENT};
    font-size: 12px;
    font-weight: 600;
    padding: 4px 6px;
}}
#linkAdd:hover {{ color: {ACCENT_HOVER}; }}
#linkDim {{
    background: transparent;
    border: none;
    color: {FAINT};
    font-size: 11px;
    padding: 4px 6px;
}}
#linkDim:hover {{ color: {DIM}; }}

QLineEdit, QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 8px 10px;
    color: {TEXT};
    selection-background-color: {PRIMARY};
    selection-color: {ON_ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT_BRD}; }}

#footerNote {{ color: {DIM}; font-size: 11px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 6px;
}}
QMessageBox {{ background: {BG}; }}

#dlgOk {{
    background: {PRIMARY};
    color: {ON_ACCENT};
    border: 1px solid {PRIMARY_EDGE};
    border-radius: 9px;
    font-weight: 600;
    font-size: 12px;
    padding: 8px 20px;
}}
#dlgOk:hover {{ background: {PRIMARY_HOVER}; }}
#dlgOk:pressed {{ background: {PRIMARY_DOWN}; }}
#dlgCancel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 9px;
    color: {DIM};
    font-size: 12px;
    padding: 8px 16px;
}}
#dlgCancel:hover {{ border-color: {DIM}; color: {TEXT}; }}
#dlgFrame {{ background: #0f0f12; border: 1px solid #2a2a31; border-radius: 16px; }}
#dlgBar {{ background: transparent; }}
#dlgClose {{
    background: transparent;
    border: none;
    color: {DIM};
    font-size: 13px;
    border-radius: 7px;
}}
#dlgClose:hover {{ background: #c0392b; color: #fff; }}
#dlgTitle {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
#dlgSub {{ font-size: 11px; color: {DIM}; }}
#dlgKey {{ font-size: 12px; color: {DIM}; }}
#aboutTag {{ font-size: 11px; font-weight: 600; letter-spacing: 2px; color: {FAINT}; }}
#aboutChk {{ font-size: 13px; font-weight: 700; color: {ACCENT}; }}
#aboutItem {{ font-size: 13px; color: {TEXT}; }}
#aboutVer {{ font-size: 11px; color: {DIM}; }}
#aboutLink {{ font-size: 13px; font-weight: 600; color: {ACCENT}; }}
#secTitle {{ font-size: 14px; font-weight: 700; color: {TEXT}; }}
#secCount {{ font-size: 11px; color: {DIM}; }}
#doneOpen {{ background: transparent; border: 1px solid {BORDER}; border-radius: 7px; }}
#doneOpen:hover {{ border-color: {ACCENT_BRD}; background: {ACCENT_BG}; }}
#doneOpen:pressed {{ background: rgba(94, 106, 210, 0.32); }}
#rcptHdr {{ font-size: 9px; letter-spacing: 1px; color: {FAINT}; }}
#rcptHead {{ font-size: 13px; font-weight: 700; color: {ACCENT}; letter-spacing: 2px; }}
#rcptDash {{ background: transparent; border: none; border-top: 1px solid {HAIR}; }}
#rcptName {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
#rcptStat {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}
#rcptCount {{ font-family: {MONO}; font-size: 12px; font-weight: 700; color: {ACCENT}; }}
#rcptCountZero {{ font-family: {MONO}; font-size: 12px; color: {FAINT}; }}
#okMark {{ font-size: 13px; font-weight: 700; color: {GREEN}; }}
#heroSub {{ font-size: 13px; color: {DIM}; }}
#rcptKvK {{ font-size: 11px; color: {DIM}; }}
#rcptKvV {{ font-family: {MONO}; font-size: 11px; color: {TEXT}; }}
#reviewBtn {{ background: #1a1a20; color: {ACCENT}; border: 1px solid {ACCENT_BRD}; border-radius: 7px; font-size: 11px; font-weight: 600; padding: 4px 4px; }}
#reviewBtn:hover {{ background: {ACCENT_BG}; }}
#reviewBtn:pressed {{ background: {PRIMARY_DOWN}; color: {ON_ACCENT}; }}
#rsRootBadge {{ background: #1a1a20; color: {ACCENT}; border: 1px solid {ACCENT_BRD}; border-radius: 7px; font-size: 9px; padding: 2px 0; }}
#rsRiskBadge {{ background: #1f1a10; color: {AMBER}; border: 1px solid #4a3a12; border-radius: 7px; font-size: 9px; padding: 2px 0; }}
#timeInput {{ font-family: {MONO}; font-size: 13px; }}
#cancelTitle {{ font-size: 16px; font-weight: 600; color: {AMBER}; letter-spacing: 1px; }}
QFrame#dlgSep {{ background: {HAIR}; max-height: 1px; min-height: 1px; border: none; }}
#modeChip {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 9px;
    color: #a3a3b0;
    font-size: 12px;
    padding: 8px 14px;
    text-align: left;
}}
#modeChip:hover {{ border-color: {DIM}; }}
#modeChip:checked {{ background: {ACCENT_BG}; border-color: {ACCENT_BRD}; color: #c7cdf8; }}
#modeChip:disabled {{ color: {FAINT}; border-color: {SURFACE}; background: transparent; }}
#pathLabel {{ font-size: 10px; color: {DIM}; padding: 2px 2px 0 0; }}

#emptyTitle {{ font-size: 22px; font-weight: 700; color: #f8f8fa; }}
#statusLine {{ font-size: 11px; color: {FAINT}; }}
#linkPick {{
    background: transparent;
    border: none;
    color: #c7cdf8;
    font-size: 15px;
    font-weight: 600;
    text-decoration: underline;
    padding: 4px 10px;
}}
#linkPick:hover {{ color: {ACCENT_HOVER}; }}
#linkPick:pressed {{ color: {ACCENT_DOWN}; }}

QPushButton#stopBtn {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    color: #b9b9c4;
    font-weight: 600;
    font-size: 13px;
    letter-spacing: 1px;
    padding: 12px 24px;
    border-radius: 10px;
}}
QPushButton#stopBtn:hover {{ border-color: {AMBER}; color: {AMBER}; }}
QPushButton#stopBtn:disabled {{ border-color: {SURFACE}; color: {DIM}; }}

#dropOverlay {{ background: rgba(10, 10, 11, 0.95); border: 1px dashed {PRIMARY_EDGE}; border-radius: 16px; }}
#dropHint {{ font-size: 13px; color: {ACCENT}; background: transparent; border: none; }}
#toast {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {TEXT};
    font-size: 12px;
    padding: 9px 16px;
}}

#procSum {{ font-size: 12px; color: {DIM}; }}
#procNameDim {{ font-size: 12px; color: {FAINT}; }}
#procR {{ font-family: {MONO}; font-size: 11px; color: {DIM}; }}

#reviewBtnDim {{ background: transparent; color: {DIM}; border: 1px solid {BORDER}; border-radius: 7px; font-size: 11px; padding: 4px 4px; }}
#reviewBtnDim:hover {{ border-color: {DIM}; }}
#ctxLine {{ font-size: 12px; color: {DIM}; }}
#secWarn {{ font-size: 11px; font-weight: 600; letter-spacing: 1px; color: {AMBER}; }}
#secDim {{ font-size: 11px; font-weight: 600; letter-spacing: 1px; color: {FAINT}; }}
#errText {{ font-size: 11px; color: {RED}; }}
"""


def _check_icon_path() -> str:
    """Рисует белую галочку (для отмеченного индиго-чекбокса) и возвращает путь.
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
    pen = QPen(QColor(ON_ACCENT))           # белая галочка на индиго-фоне
    pen.setWidthF(3.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    pt.setPen(pen)
    pt.drawPolyline([QPointF(7, 17), QPointF(13.5, 23), QPointF(25, 9)])
    pt.end()
    img.save(str(p), "PNG")
    return str(p).replace("\\", "/")


def _load_fonts() -> None:
    """Подхватить вшитые шрифты (fonts/*.ttf|otf рядом с программой), если они есть.
    Без них семейства MONO/SANS молча падают на системные (Consolas/Segoe UI)."""
    try:
        from PySide6.QtGui import QFontDatabase

        from .settings import app_base_dir
        d = app_base_dir() / "fonts"
        if not d.is_dir():
            return
        for f in sorted(d.glob("*.[ot]tf")):
            QFontDatabase.addApplicationFont(str(f))
    except Exception:
        pass


def apply(app) -> None:
    app.setStyle("Fusion")
    _load_fonts()                  # вшитые шрифты (если поставлены) — до setStyleSheet
    try:
        qss = QSS.replace("__CHECK_ICON__", _check_icon_path())
    except Exception:
        qss = QSS.replace("__CHECK_ICON__", "")   # без иконки чекбокс всё равно индиго
    app.setStyleSheet(qss)
