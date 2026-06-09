# -*- coding: utf-8 -*-
"""GUI Censr — единый стиль «звуковая волна»: один индикатор-волна проходит
через все экраны (покой → прогресс → полная → прервана)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import (
    QObject, QPointF, QRectF, QRunnable, QSize, QThread, QThreadPool,
    Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QStackedLayout, QVBoxLayout, QWidget,
)

from . import native, theme
from .settings import Settings, app_base_dir, default_model_dir
from .wave import Wave

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".mp4", ".mkv", ".webm"}
SUFFIX = "_censr"
COLUMN_W = 580
HEADER_H = 56
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # не показывать окно консоли


def _collect(paths):
    files = []
    for p in paths:
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in AUDIO_EXT]
        elif p.suffix.lower() in AUDIO_EXT:
            files.append(p)
    return files


def _duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15, check=True, creationflags=NO_WINDOW)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _fmt(sec: float) -> str:
    sec = int(round(sec))
    if sec >= 3600:
        return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)
    return "%d:%02d" % (sec // 60, sec % 60)


def _fmt_dur(sec: float) -> str:
    """Человекочитаемая длительность обработки: «48 с», «2 мин 05 с», «1 ч 03 мин»."""
    sec = int(round(sec))
    if sec < 60:
        return "%d с" % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return "%d мин %02d с" % (m, s)
    h, m = divmod(m, 60)
    return "%d ч %02d мин" % (h, m)


# ======================================================================== воркеры
class _DurSignals(QObject):
    done = Signal(float)


class _DurTask(QRunnable):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = _DurSignals()

    def run(self):
        self.signals.done.emit(_duration(Path(self.path)))


class _TracksSignals(QObject):
    done = Signal(object)         # list[AudioStream]


class _TracksTask(QRunnable):
    """Асинхронно перечисляет аудиодорожки файла (для выбора в строке)."""
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = _TracksSignals()

    def run(self):
        try:
            from .audio import list_audio_streams
            streams = list_audio_streams(self.path)
        except Exception:
            streams = []
        self.signals.done.emit(streams)


class Worker(QThread):
    file_started = Signal(int)
    file_progress = Signal(int, int, int, str)   # row, pct, found, stage
    file_done = Signal(int, int, float, str)  # row, censored, elapsed, dst
    file_error = Signal(int, str)
    fatal = Signal(str)
    all_done = Signal()

    def __init__(self, files, settings):
        super().__init__()
        self.files = files
        self.s = settings
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):  # noqa: C901
        try:
            from .asr import Cancelled, Transcriber
            from .audio import AudioError
            from .audio_zone import ZoneParams
            from .detector import ProfanityDetector
            from .pipeline import censor_file

            zone_params = ZoneParams.from_edge_pct(self.s.edge_keep_pct, full=self.s.full_mute)
            tr = Transcriber(model_path=self.s.model_dir or default_model_dir())
            det = ProfanityDetector(extra_words=set(self.s.extra_words),
                                    whitelist=set(self.s.whitelist))
        except ModuleNotFoundError as e:
            self.fatal.emit(
                "Не установлен модуль «%s».\n\nВыполни в папке программы:\n"
                "    python -m pip install -r requirements.txt\n\nи перезапусти Censr." % e.name)
            self.all_done.emit()
            return
        except Exception:
            self.fatal.emit("Не удалось загрузить модель распознавания:\n\n" + traceback.format_exc())
            self.all_done.emit()
            return

        for row, (f, tracks) in enumerate(self.files):
            if self._stop:
                break
            self.file_started.emit(row)
            try:
                src = Path(f)
                out_dir = Path(self.s.output_dir) if self.s.output_dir else src.parent
                dst = out_dir / ("%s%s%s" % (src.stem, SUFFIX, src.suffix))
                last = [-1, -1, ""]

                def report(frac, found=0, stage="", row=row, last=last):
                    p = int(frac * 100)
                    if p != last[0] or found != last[1] or stage != last[2]:
                        last[0], last[1], last[2] = p, found, stage
                        self.file_progress.emit(row, p, found, stage)

                t0 = time.perf_counter()
                rep = censor_file(src, dst, tr, det, mode=self.s.mode,
                                  progress=report, cancel=lambda: self._stop,
                                  zone_params=zone_params, use_cache=self.s.use_cache,
                                  tracks=tracks,
                                  max_passes=3 if self.s.thorough_clean else 1)
                elapsed = time.perf_counter() - t0
                dst.with_suffix(".report.json").write_text(
                    json.dumps(rep.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
                self.file_done.emit(row, rep.flagged_words, elapsed, str(rep.dst))
            except Cancelled:
                break
            except AudioError as e:
                self.file_error.emit(row, str(e))
            except Exception:
                self.file_error.emit(row, traceback.format_exc())
        self.all_done.emit()


# ======================================================================== строка файла
def _chevron_icon(direction: str, color: str, size: int = 12) -> QIcon:
    """Рисованный шеврон (вправо/вниз) — не зависит от системного шрифта."""
    from PySide6.QtCore import QPointF
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6 * dpr)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = size * dpr
    if direction == "down":
        pts = [QPointF(s * 0.30, s * 0.42), QPointF(s * 0.50, s * 0.60), QPointF(s * 0.70, s * 0.42)]
    else:
        pts = [QPointF(s * 0.42, s * 0.30), QPointF(s * 0.60, s * 0.50), QPointF(s * 0.42, s * 0.70)]
    p.drawPolyline(QPolygonF(pts))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _play_icon(color: str, size: int = 13) -> QIcon:
    """Иконка воспроизведения (треугольник)."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    s = size * dpr
    p.drawPolygon(QPolygonF([QPointF(s * 0.30, s * 0.20),
                             QPointF(s * 0.30, s * 0.80),
                             QPointF(s * 0.80, s * 0.50)]))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _folder_icon(color: str, size: int = 14) -> QIcon:
    """Иконка папки (контур)."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4 * dpr)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = size * dpr
    p.drawPolygon(QPolygonF([QPointF(s * 0.16, s * 0.32), QPointF(s * 0.40, s * 0.32),
                             QPointF(s * 0.48, s * 0.42), QPointF(s * 0.84, s * 0.42),
                             QPointF(s * 0.84, s * 0.74), QPointF(s * 0.16, s * 0.74)]))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _close_icon(color: str, size: int = 12) -> QIcon:
    """Иконка ✕ (крестик) — не зависит от системного шрифта."""
    from PySide6.QtCore import QPointF
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5 * dpr)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    s = size * dpr
    p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _stop_icon(color: str, size: int = 30) -> QPixmap:
    """Иконка «стоп/прервано» (кружок с диагональю) — для экрана «Прервано»."""
    import math
    from PySide6.QtCore import QPointF
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(2.2 * dpr)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = size * dpr
    r = s * 0.36
    p.drawEllipse(QPointF(s / 2, s / 2), r, r)
    d = r * math.cos(math.radians(45))
    p.drawLine(QPointF(s / 2 - d, s / 2 - d), QPointF(s / 2 + d, s / 2 + d))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class FileRow(QFrame):
    removed = Signal(object)
    dur_ready = Signal()
    tracks_ready = Signal()

    def __init__(self, path: Path):
        super().__init__()
        self.setObjectName("fileRow")
        self.path = path
        self.state = "queued"
        self._full_name = path.name
        self.dur_sec = 0.0
        self.streams = []          # list[AudioStream]
        self.checks = []           # QCheckBox по дорожке
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QWidget()
        lay = QHBoxLayout(top)
        lay.setContentsMargins(0, 9, 0, 9)
        lay.setSpacing(12)
        self.expand_btn = QPushButton("")          # шеврон-кружок раскрытия (мультитрек)
        self.expand_btn.setObjectName("rowExpand")
        self.expand_btn.setFixedSize(20, 20)
        self.expand_btn.setIconSize(QSize(12, 12))
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.setToolTip("Выбрать аудиодорожки")
        self.expand_btn.setProperty("open", "none")
        self.expand_btn.setEnabled(False)
        self.expand_btn.hide()                     # гаттер показывается, только если в очереди есть мультитрек
        self.expand_btn.clicked.connect(self._toggle)
        lay.addWidget(self.expand_btn)
        self.num = QLabel("01")
        self.num.setObjectName("rowNum")
        self.num.setFixedWidth(24)
        lay.addWidget(self.num)
        self.name = QLabel(path.name)
        self.name.setObjectName("fileName")
        self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setToolTip(path.name)
        lay.addWidget(self.name, 1)
        self.tracks_lbl = QLabel("")
        self.tracks_lbl.setObjectName("rowTracks")
        self.tracks_lbl.hide()
        lay.addWidget(self.tracks_lbl)
        self.dur = QLabel("·····")
        self.dur.setObjectName("colDur")
        lay.addWidget(self.dur)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("rowClose")
        self.close_btn.setFixedWidth(20)
        self.close_btn.setToolTip("Убрать из очереди")
        self.close_btn.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(self.close_btn)
        outer.addWidget(top)

        self.panel = QWidget()                     # раскрывающийся список дорожек
        self.panel.setObjectName("trackPanel")
        self._panel_lay = QVBoxLayout(self.panel)
        self._panel_lay.setContentsMargins(52, 10, 20, 14)
        self._panel_lay.setSpacing(10)
        self.panel.hide()
        outer.addWidget(self.panel)

        self._task = _DurTask(str(path))
        self._task.signals.done.connect(self._set_duration)
        QThreadPool.globalInstance().start(self._task)
        self._tr_task = _TracksTask(str(path))
        self._tr_task.signals.done.connect(self._set_tracks)
        QThreadPool.globalInstance().start(self._tr_task)

    def set_number(self, n: int):
        self.num.setText("%02d" % n)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.name.width()
        if w > 10:
            fm = self.name.fontMetrics()
            self.name.setText(fm.elidedText(self._full_name, Qt.ElideMiddle, w))

    def _set_duration(self, sec):
        self.dur_sec = sec
        self.dur.setText(_fmt(sec))
        self.dur_ready.emit()

    def set_gutter(self, on: bool):
        """Показать/скрыть левый гаттер под шеврон (резервируется на всю очередь,
        только если есть мультитрек — иначе строки выровнены по левому краю)."""
        self.expand_btn.setVisible(on)

    def _set_tracks(self, streams):
        self.streams = streams or []
        if len(self.streams) <= 1:                 # одна дорожка — выбор не нужен
            self.tracks_ready.emit()
            return
        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("какие дорожки обработать:")
        hint.setObjectName("trackHint")
        hl.addWidget(hint)
        hl.addStretch(1)
        self.all_btn = QPushButton("выбрать все")
        self.all_btn.setObjectName("trackAll")
        self.all_btn.setCursor(Qt.PointingHandCursor)
        self.all_btn.clicked.connect(self._check_all)
        hl.addWidget(self.all_btn)
        self._panel_lay.addWidget(hdr)
        for s in self.streams:
            cb = QCheckBox("Дорожка %d — %s" % (s.index + 1, s.label()))
            cb.setObjectName("trackCheck")
            cb.setChecked(True)                    # по умолчанию отмечены все
            cb.toggled.connect(self._on_check)
            self.checks.append(cb)
            self._panel_lay.addWidget(cb)
        self.expand_btn.setEnabled(True)
        self._set_arrow(False)
        self._update_tracks_lbl()
        self.tracks_ready.emit()

    def _set_arrow(self, is_open: bool):
        self.expand_btn.setIcon(_chevron_icon("down" if is_open else "right",
                                              theme.ON_ACCENT if is_open else theme.ACCENT))
        self.expand_btn.setProperty("open", "true" if is_open else "false")
        self.expand_btn.style().unpolish(self.expand_btn)
        self.expand_btn.style().polish(self.expand_btn)

    def _toggle(self):
        self._expanded = not self._expanded
        self.panel.setVisible(self._expanded)
        self._set_arrow(self._expanded)

    def _check_all(self):
        for c in self.checks:
            c.setChecked(True)

    def _on_check(self, _checked=False):
        if self.checks and not any(c.isChecked() for c in self.checks):
            s = self.sender()                      # не дать снять последнюю дорожку
            if isinstance(s, QCheckBox):
                s.blockSignals(True)
                s.setChecked(True)
                s.blockSignals(False)
        self._update_tracks_lbl()

    def _update_tracks_lbl(self):
        n = len(self.streams)
        m = sum(1 for c in self.checks if c.isChecked())
        self.tracks_lbl.setText("дорожек: %d · выбрано %d" % (n, m))
        self.tracks_lbl.show()

    def selected_tracks(self):
        """0-based индексы выбранных дорожек; None — обрабатывать все."""
        if len(self.streams) <= 1:
            return None
        sel = [i for i, c in enumerate(self.checks) if c.isChecked()]
        return None if len(sel) == len(self.streams) else sel


# ======================================================================== диалоги
def _spk_icon(level: int, color: str, size: int = 16) -> QIcon:
    """Иконка динамика по уровню: 0 — перечёркнут (тишина), 1 — одна волна, 2 — две."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    pen = QPen(c)
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(c)
    s = float(size)
    # корпус-коробочка + конус
    p.drawRect(QRectF(2.0, s * 0.38, 2.6, s * 0.24))
    cone = QPolygonF([
        QPointF(4.6, s * 0.38), QPointF(8.0, s * 0.22),
        QPointF(8.0, s * 0.78), QPointF(4.6, s * 0.62),
    ])
    p.drawPolygon(cone)
    p.setBrush(Qt.NoBrush)
    if level <= 0:
        p.drawLine(QPointF(s * 0.66, s * 0.36), QPointF(s * 0.90, s * 0.64))
        p.drawLine(QPointF(s * 0.90, s * 0.36), QPointF(s * 0.66, s * 0.64))
    else:
        p.drawArc(QRectF(s * 0.50, s * 0.32, s * 0.26, s * 0.36), -55 * 16, 110 * 16)
        if level >= 2:
            p.drawArc(QRectF(s * 0.50, s * 0.20, s * 0.44, s * 0.60), -50 * 16, 100 * 16)
    p.end()
    return QIcon(pm)


class ChipToggle(QWidget):
    """Вариант 3: два чипа с иконками (взаимоисключающие)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.b_sil = QPushButton("  тишина", self)
        self.b_beep = QPushButton("  бип", self)
        self.b_noise = QPushButton("  шум", self)
        self._btns = (self.b_sil, self.b_beep, self.b_noise)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        for i, b in enumerate(self._btns):
            b.setObjectName("modeChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(16, 16))
            b.toggled.connect(self._refresh)
            self._grp.addButton(b, i)
            lay.addWidget(b)
        self.b_sil.setChecked(True)
        self._refresh()

    def _refresh(self):
        self.b_sil.setIcon(_spk_icon(0, theme.ACCENT if self.b_sil.isChecked() else theme.DIM))
        self.b_beep.setIcon(_spk_icon(2, theme.ACCENT if self.b_beep.isChecked() else theme.DIM))
        self.b_noise.setIcon(_spk_icon(1, theme.ACCENT if self.b_noise.isChecked() else theme.DIM))

    def value(self) -> int:
        for i, b in enumerate(self._btns):
            if b.isChecked():
                return i
        return 0

    def setValue(self, i: int):
        self._btns[i if 0 <= i < len(self._btns) else 0].setChecked(True)
        self._refresh()


class EdgeChips(QWidget):
    """Слышимость краёв, вариант 9: три чипа с иконками громкости."""

    _LEVELS = [("мин", 0, 5), ("средне", 1, 12), ("больше", 2, 20)]

    def __init__(self, pct, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        self._btns = []
        for label, lvl, p in self._LEVELS:
            b = QPushButton("  " + label, self)
            b.setObjectName("modeChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(16, 16))
            b._pct = p
            b._lvl = lvl
            b.toggled.connect(self._refresh)
            self._grp.addButton(b)
            lay.addWidget(b)
            self._btns.append(b)
        nearest = min(self._btns, key=lambda b: abs(b._pct - int(pct)))
        nearest.setChecked(True)
        self._refresh()

    def _refresh(self):
        for b in self._btns:
            b.setIcon(_spk_icon(b._lvl, theme.ACCENT if b.isChecked() else theme.DIM))

    def value(self) -> int:
        for b in self._btns:
            if b.isChecked():
                return b._pct
        return 12


class PathChooser(QWidget):
    """Путь, вариант 5: чипы «рядом с файлом / своя папка» + строка пути."""

    def __init__(self, out_dir, parent=None):
        super().__init__(parent)
        self._dir = out_dir or ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        chips.addStretch(1)
        self.b_near = QPushButton("рядом с файлом", self)
        self.b_own = QPushButton("своя папка", self)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        for b in (self.b_near, self.b_own):
            b.setObjectName("modeChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self._grp.addButton(b)
            chips.addWidget(b)
        v.addLayout(chips)

        self.path_lbl = QLabel(self)               # путь — просто подпись, не кликабельна
        self.path_lbl.setObjectName("pathLabel")
        self.path_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.path_lbl.setTextInteractionFlags(Qt.NoTextInteraction)
        v.addWidget(self.path_lbl)

        self.b_near.clicked.connect(self._refresh)
        self.b_own.clicked.connect(self._use_own)
        (self.b_own if self._dir else self.b_near).setChecked(True)
        self._refresh()

    def _refresh(self):
        own = self.b_own.isChecked()
        self.path_lbl.setVisible(own)             # путь виден только для «своя папка»
        if own:
            self.path_lbl.setText(self._dir or "папка не выбрана")

    def _use_own(self):
        self._pick()                              # клик по «своя папка» — всегда выбор папки
        if not self._dir:                         # отмена и папки нет — вернуться к «рядом»
            self.b_near.setChecked(True)
        self._refresh()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Выходная папка", self._dir or "")
        if d:
            self._dir = d
            self.b_own.setChecked(True)

    def value(self) -> str:
        return self._dir if self.b_own.isChecked() else ""


def _sep():
    f = QFrame()
    f.setObjectName("dlgSep")
    return f


def _kv_row(key, value_widget):
    r = QHBoxLayout()
    k = QLabel(key)
    k.setObjectName("dlgKey")
    r.addWidget(k)
    r.addStretch(1)
    r.addWidget(value_widget)
    return r


class FramelessDialog(QDialog):
    """Безрамочный диалог: своя шапка (заголовок + ✕), перетаскивание за шапку."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)   # чтобы окно не было «pythonw» в таскбаре
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setModal(True)
        self._bar_h = 42
        self._drag = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QWidget()
        frame.setObjectName("dlgFrame")
        outer.addWidget(frame)
        col = QVBoxLayout(frame)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("dlgBar")
        bar.setFixedHeight(self._bar_h)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 8, 0)
        tt = QLabel(title)
        tt.setObjectName("dlgTitle")
        bl.addWidget(tt)
        bl.addStretch(1)
        x = QPushButton()
        x.setObjectName("dlgClose")
        x.setFixedSize(30, 26)
        x.setIcon(_close_icon("#cdd6e0"))          # нарисованный ✕ (не зависит от шрифта)
        x.setIconSize(QSize(12, 12))
        x.setCursor(Qt.PointingHandCursor)
        x.setToolTip("Закрыть")
        x.clicked.connect(self.reject)
        bl.addWidget(x)
        col.addWidget(bar)
        cw = QWidget()
        self.content = QVBoxLayout(cw)
        self.content.setContentsMargins(18, 6, 18, 16)
        self.content.setSpacing(12)
        col.addWidget(cw, 1)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() <= self._bar_h \
                and not isinstance(self.childAt(e.position().toPoint()), QPushButton):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    def add_buttons(self, ok_text="сохранить", cancel_text="отмена", with_stretch=True):
        """Кнопки внизу диалога: справа отмена + сохранить (в стиле приложения).
        with_stretch=False — если растяжка/низ уже выстроены вызывающим кодом."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        if cancel_text:
            c = QPushButton(cancel_text)
            c.setObjectName("dlgCancel")
            c.setCursor(Qt.PointingHandCursor)
            c.clicked.connect(self.reject)
            row.addWidget(c)
        o = QPushButton(ok_text)
        o.setObjectName("dlgOk")
        o.setCursor(Qt.PointingHandCursor)
        o.clicked.connect(self.accept)
        row.addWidget(o)
        if with_stretch:
            self.content.addStretch(1)
        self.content.addLayout(row)


class SettingsDialog(FramelessDialog):
    """S9 · крупные значения справа."""

    def __init__(self, s: Settings, parent=None):
        super().__init__("настройки", parent)
        self.setMinimumWidth(460)
        root = self.content

        self.mode = ChipToggle()
        self.mode.setValue({"silence": 0, "beep": 1, "noise": 2}.get(s.mode, 0))
        root.addLayout(_kv_row("режим глушения", self.mode))
        root.addWidget(_sep())

        self.path = PathChooser(s.output_dir)
        root.addLayout(_kv_row("выходная папка", self.path))
        root.addWidget(_sep())

        self.edge = EdgeChips(int(s.edge_keep_pct))
        root.addLayout(_kv_row("слышимость краёв", self.edge))
        root.addWidget(_sep())

        self.full = QCheckBox("  максимальная очистка — глушить слово целиком")
        self.full.setObjectName("trackCheck")
        self.full.setChecked(bool(s.full_mute))
        self.full.setToolTip("Без слышимых краёв: повторная обработка не находит остатков")
        root.addWidget(self.full)

        self.thorough = QCheckBox("  тщательная очистка — несколько проходов (медленнее)")
        self.thorough.setObjectName("trackCheck")
        self.thorough.setChecked(bool(s.thorough_clean))
        self.thorough.setToolTip("Повторно распознаёт уже заглушенный звук и добивает пропуски")
        root.addWidget(self.thorough)
        self.add_buttons()

    def apply_to(self, s: Settings):
        s.mode = ("silence", "beep", "noise")[self.mode.value()]
        s.output_dir = self.path.value()
        s.edge_keep_pct = self.edge.value()
        s.full_mute = self.full.isChecked()
        s.thorough_clean = self.thorough.isChecked()


class _AccordionSection(QWidget):
    """Секция-аккордеон: шапка с кружком-шевроном (как в очереди файлов) + тело."""

    def __init__(self, title, body, count=0, expanded=False, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        header.mousePressEvent = lambda e: self.toggle()
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(11)
        self.btn = QPushButton()
        self.btn.setObjectName("rowExpand")
        self.btn.setFixedSize(20, 20)
        self.btn.setIconSize(QSize(12, 12))
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.toggle)
        self.title = QLabel(title)
        self.title.setObjectName("secTitle")
        self.count = QLabel(str(count))
        self.count.setObjectName("secCount")
        h.addWidget(self.btn)
        h.addWidget(self.title)
        h.addStretch(1)
        h.addWidget(self.count)
        v.addWidget(header)
        self.body = QWidget()                       # обёртка: поле выровнено под текст заголовка
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(31, 0, 0, 14)         # 31 = кружок(20) + зазор(11)
        bl.setSpacing(0)
        bl.addWidget(body)
        v.addWidget(self.body)
        self._expanded = expanded
        self.body.setVisible(expanded)
        self._set_arrow(expanded)

    def set_count(self, n):
        self.count.setText(str(n))

    def _set_arrow(self, op):
        self.btn.setIcon(_chevron_icon("down" if op else "right",
                                       theme.ON_ACCENT if op else theme.ACCENT))
        self.btn.setProperty("open", "true" if op else "false")
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)

    def toggle(self, *_):
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self._set_arrow(self._expanded)
        w = self.window()
        if w:
            w.adjustSize()


class DictionariesDialog(FramelessDialog):
    """Аккордеон: глушить / исключения / встроенные (read-only)."""

    def __init__(self, s: Settings, parent=None):
        super().__init__("словари", parent)
        self.setMinimumWidth(460)
        root = self.content
        root.setSpacing(0)

        self.extra = QPlainTextEdit("\n".join(s.extra_words))
        self.extra.setPlaceholderText("дополнительно глушить…")
        self.extra.setFixedHeight(120)
        self.white = QPlainTextEdit("\n".join(s.whitelist))
        self.white.setPlaceholderText("никогда не глушить…")
        self.white.setFixedHeight(120)

        from .detector import BUILTIN_ROOTS
        builtin = QPlainTextEdit("\n".join(BUILTIN_ROOTS))
        builtin.setReadOnly(True)
        builtin.setFixedHeight(120)

        self._sec_extra = _AccordionSection("＋ глушить", self.extra, len(s.extra_words), expanded=True)
        self._sec_white = _AccordionSection("－ исключения", self.white, len(s.whitelist))
        sec_builtin = _AccordionSection("★ встроенные", builtin, len(BUILTIN_ROOTS))
        self.extra.textChanged.connect(lambda: self._sec_extra.set_count(
            sum(1 for ln in self.extra.toPlainText().splitlines() if ln.strip())))
        self.white.textChanged.connect(lambda: self._sec_white.set_count(
            sum(1 for ln in self.white.toPlainText().splitlines() if ln.strip())))

        root.addWidget(self._sec_extra)
        root.addWidget(_sep())
        root.addWidget(self._sec_white)
        root.addWidget(_sep())
        root.addWidget(sec_builtin)

        root.addStretch(1)                         # растяжка ВЫШЕ подсказки — прижимает низ
        hint = QLabel("по одному слову на строку · морфология учитывается автоматически")
        hint.setObjectName("dlgSub")
        hint.setContentsMargins(31, 0, 0, 0)       # под одну линию с заголовками и полями
        root.addWidget(hint)
        root.addSpacing(16)                        # воздух между подсказкой и кнопками
        self.add_buttons(with_stretch=False)

    def apply_to(self, s: Settings):
        s.extra_words = [w.strip() for w in self.extra.toPlainText().splitlines() if w.strip()]
        s.whitelist = [w.strip() for w in self.white.toPlainText().splitlines() if w.strip()]


class AboutDialog(FramelessDialog):
    """Вариант N · маркетинговый чеклист «что умеет»."""

    _ITEMS = [
        "распознаёт русскую речь с таймкодами слов",
        "точно находит мат, не трогая обычные слова",
        "работает офлайн, ничего не загружает в сеть",
        "глушит аккуратно, сохраняя качество дорожки",
    ]

    def __init__(self, parent=None):
        super().__init__("о программе", parent)
        self.setMinimumWidth(420)
        root = self.content

        wm = QLabel('censr<span style="color:%s;">_</span>' % theme.ACCENT)
        wm.setObjectName("wordmark")
        wm.setTextFormat(Qt.RichText)
        root.addWidget(wm)

        tag = QLabel("что умеет")
        tag.setObjectName("aboutTag")
        root.addWidget(tag)

        listw = QWidget()
        lv = QVBoxLayout(listw)
        lv.setContentsMargins(0, 4, 0, 0)
        lv.setSpacing(10)
        for it in self._ITEMS:
            row = QHBoxLayout()
            row.setSpacing(10)
            chk = QLabel("✓")
            chk.setObjectName("aboutChk")
            txt = QLabel(it)
            txt.setObjectName("aboutItem")
            txt.setWordWrap(True)
            row.addWidget(chk, 0, Qt.AlignTop)
            row.addWidget(txt, 1)
            lv.addLayout(row)
        root.addWidget(listw)

        root.addWidget(_sep())
        ltag = QLabel("поддержать и следить")
        ltag.setObjectName("aboutTag")
        root.addWidget(ltag)

        links = QWidget()
        ll = QVBoxLayout(links)
        ll.setContentsMargins(0, 4, 0, 0)
        ll.setSpacing(12)
        for title, desc, url in (
            ("Телеграм-канал · @cens3r", "анонсы обновлений, советы и обратная связь",
             "https://t.me/cens3r"),
            ("Поддержать проект", "Censr бесплатный — если пригодился, можно угостить автора кофе",
             "https://www.donationalerts.com/r/si13ouette"),
        ):
            block = QVBoxLayout()
            block.setSpacing(2)
            a = QLabel('<a href="%s" style="color:%s; text-decoration:none;">%s ↗</a>'
                       % (url, theme.ACCENT, title))
            a.setObjectName("aboutLink")
            a.setTextFormat(Qt.RichText)
            a.setOpenExternalLinks(True)
            a.setCursor(Qt.PointingHandCursor)
            d = QLabel(desc)
            d.setObjectName("aboutVer")
            d.setWordWrap(True)
            block.addWidget(a)
            block.addWidget(d)
            ll.addLayout(block)
        root.addWidget(links)

        root.addWidget(_sep())
        ver = QLabel("версия 1.0 · движок GigaAM-v3 · ONNX")
        ver.setObjectName("aboutVer")
        root.addWidget(ver)

        self.add_buttons(ok_text="закрыть", cancel_text=None)


class ManualAddDialog(FramelessDialog):
    """Своё окно «добавить вручную»: начало/конец в формате мм:сс (или ч:мм:сс)."""

    def __init__(self, parent=None):
        super().__init__("добавить вручную", parent)
        self.setMinimumWidth(360)
        self.result_range = None
        root = self.content
        root.setSpacing(8)

        rowf = QHBoxLayout()
        rowf.setSpacing(10)
        self._fields = {}
        for key, label, ph in (("s", "начало", "00:12.0"), ("e", "конец", "00:12.6")):
            col = QVBoxLayout()
            col.setSpacing(5)
            lab = QLabel(label)
            lab.setObjectName("dlgSub")
            le = QLineEdit()
            le.setObjectName("timeInput")
            le.setPlaceholderText(ph)
            col.addWidget(lab)
            col.addWidget(le)
            rowf.addLayout(col)
            self._fields[key] = le
        root.addLayout(rowf)

        hint = QLabel("формат мм:сс.д (или ч:мм:сс) · заглушит этот отрезок")
        hint.setObjectName("dlgSub")
        root.addWidget(hint)
        self.add_buttons(ok_text="добавить", cancel_text="отмена")

    @staticmethod
    def _parse(t):
        parts = [float(p) for p in t.strip().replace(",", ".").split(":")]
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    def accept(self):
        try:
            s = self._parse(self._fields["s"].text())
            e = self._parse(self._fields["e"].text())
            if e <= s or s < 0:
                raise ValueError
        except Exception:
            QMessageBox.warning(self, "Censr",
                                "Не понял время. Формат: мм:сс, напр. 00:12.0 – 00:12.6")
            return
        self.result_range = (s, e)
        super().accept()


class ReviewDialog(FramelessDialog):
    """Проверка найденного: снять/добавить, прослушать фрагмент, применить
    (перерисовать выходной файл без повторного распознавания)."""

    def __init__(self, src, dst, censored, mode, parent=None):
        super().__init__("проверка", parent)
        self.setMinimumWidth(560)
        self.src = src
        self.dst = dst
        self.mode = mode
        self.applied_count = len(censored)
        self._checks = []                  # [(QCheckBox, item)]
        self._sfx = None
        self._tmp = None
        self._play_n = 0
        root = self.content
        root.setSpacing(6)

        head = QLabel("Найдено мата: %d" % len(censored))
        head.setObjectName("secTitle")
        root.addWidget(head)
        sub = QLabel("сними лишнее, добавь пропущенное, прослушай сомнительное — затем «применить»")
        sub.setObjectName("dlgSub")
        sub.setWordWrap(True)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(340)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(0, 6, 0, 0)
        self._lay.setSpacing(0)
        for it in censored:
            self._lay.addWidget(self._make_row(it))
        self._lay.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        link = QLabel('<a href="#" style="color:%s; text-decoration:none;">+ добавить вручную</a>'
                      % theme.ACCENT)
        link.setObjectName("aboutLink")
        link.setTextFormat(Qt.RichText)
        link.setCursor(Qt.PointingHandCursor)
        link.linkActivated.connect(lambda *_: self._add_manual())
        root.addWidget(link)

        self.add_buttons(ok_text="применить", cancel_text="назад")

    def _make_row(self, it):
        row = QWidget()
        row.setObjectName("rcptRow")
        row.setFixedHeight(36)
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(10)
        cb = QCheckBox()
        cb.setObjectName("trackCheck")
        cb.setChecked(True)
        self._checks.append((cb, it))
        w = QLabel(it.get("word", ""))
        w.setObjectName("rcptName")
        fuzzy = "fuzzy" in (it.get("reason") or "")
        badge = QLabel("похоже?" if fuzzy else "корень")
        badge.setObjectName("rsRiskBadge" if fuzzy else "rsRootBadge")
        badge.setFixedWidth(60)
        badge.setAlignment(Qt.AlignCenter)
        tc = QLabel(_fmt(it.get("start", 0)))
        tc.setObjectName("rcptStat")
        tc.setFixedWidth(56)
        tc.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        play = QPushButton()
        play.setObjectName("doneOpen")
        play.setFixedSize(26, 24)
        play.setIcon(_play_icon(theme.ACCENT))
        play.setIconSize(QSize(13, 13))
        play.setCursor(Qt.PointingHandCursor)
        play.setToolTip("Прослушать фрагмент оригинала")
        play.clicked.connect(lambda _=False, x=it: self._play(x))
        h.addWidget(cb)
        h.addWidget(w, 1)
        h.addWidget(badge)
        h.addWidget(tc)
        h.addWidget(play)
        return row

    def _add_manual(self):
        dlg = ManualAddDialog(self)
        if not dlg.exec() or not dlg.result_range:
            return
        s, e = dlg.result_range
        it = {"word": "(вручную)", "start": s, "end": e, "mute_from": s, "mute_to": e,
              "reason": "manual", "track": 0}
        self._lay.insertWidget(self._lay.count() - 1, self._make_row(it))

    def _play(self, it):
        try:
            from PySide6.QtMultimedia import QSoundEffect
            from .audio import _run_ff
            import tempfile
            if self._sfx is None:
                self._sfx = QSoundEffect()     # один объект на диалог (не плодим на каждый клик)
            self._sfx.stop()                   # освободить прошлый фрагмент
            if self._tmp is None:
                self._tmp = tempfile.mkdtemp()
            self._play_n += 1                  # своё имя файла — обходим кэш источника QSoundEffect
            wav = str(Path(self._tmp) / ("frag%d.wav" % self._play_n))
            s = max(0.0, float(it.get("start", 0)) - 0.4)
            e = float(it.get("end", 0)) + 0.4
            _run_ff(["ffmpeg", "-v", "quiet", "-y", "-ss", str(s), "-to", str(e),
                     "-i", str(self.src), "-ac", "1", "-ar", "22050",
                     "-c:a", "pcm_s16le", wav])
            self._sfx.setSource(QUrl.fromLocalFile(wav))
            self._sfx.setVolume(0.9)
            self._sfx.play()
        except Exception:
            pass   # без аудио-бэкенда просто молчим

    def done(self, r):
        """Закрытие диалога (ok/отмена/✕): остановить звук и убрать временные фрагменты."""
        if self._sfx is not None:
            self._sfx.stop()
        if self._tmp:
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        super().done(r)

    def accept(self):
        zbt = {}
        n = 0
        for cb, it in self._checks:
            if cb.isChecked():
                t = int(it.get("track", 0) or 0)
                zbt.setdefault(t, []).append((float(it["mute_from"]), float(it["mute_to"])))
                n += 1
        try:
            from .pipeline import recensor
            recensor(self.src, self.dst, zbt, mode=self.mode)
        except Exception as e:
            QMessageBox.warning(self, "Censr", "Не удалось применить:\n%s" % e)
            return
        self.applied_count = n
        super().accept()


# ======================================================================== главное окно
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Censr")
        self.resize(760, 600)
        self.setAcceptDrops(True)
        self.settings = Settings.load()
        self.worker = None
        self.rows: list[FileRow] = []
        self.taskbar = None
        self._completed = 0
        self._total = 0
        self._found_total = 0
        self._cancelled = False
        self._out_dirs = []
        self._errors = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(24, 0, 24, 0)
        body.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(COLUMN_W)
        column.setMinimumWidth(min(COLUMN_W, 420))
        self.col = QVBoxLayout(column)
        self.col.setContentsMargins(0, 8, 0, 18)
        self.col.setSpacing(14)
        body.addWidget(column, 8)
        body.addStretch(1)
        root.addLayout(body, 1)

        self._build_header(central)
        self._build_stack()
        self._build_bottom()

    # — шапка-оверлей
    def _build_header(self, central):
        self.header = QWidget(central)
        self.header.setFixedHeight(HEADER_H)
        top = QHBoxLayout(self.header)
        top.setContentsMargins(28, 0, 28, 0)
        top.setSpacing(4)
        self.mark = QLabel()
        self.mark.setObjectName("wordmark")
        self.mark.setTextFormat(Qt.RichText)
        top.addWidget(self.mark, 0, Qt.AlignVCenter)
        top.addStretch(1)
        self._caret_on = True
        self._render_mark()
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._toggle_caret)
        self._blink.start(560)
        for text, slot in (("словарь", self._open_dicts), ("настройки", self._open_settings),
                           ("о программе", self._about)):
            b = QPushButton(text)
            b.setObjectName("topLink")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            top.addWidget(b, 0, Qt.AlignVCenter)
        self.header.raise_()

    # — три экрана
    def _build_stack(self):
        self.stack = QStackedLayout()

        # 0 · главный: волна-покой + подсказка
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(8, 0, 8, 0)
        ev.addStretch(1)
        self.empty_wave = Wave(height=104, interactive=True)
        self.empty_wave.set_idle()
        self.empty_wave.clicked.connect(self._pick_files)
        ev.addWidget(self.empty_wave)
        ev.addSpacing(18)
        eh = QLabel("нажми на волну или перетащи аудио/видео в окно")
        eh.setObjectName("heroHint")
        eh.setAlignment(Qt.AlignHCenter)
        ev.addWidget(eh)
        ev.addStretch(1)
        self.stack.addWidget(empty)

        # 1 · очередь: волна-покой сверху + список
        listing = QWidget()
        lv = QVBoxLayout(listing)
        lv.setContentsMargins(0, HEADER_H - 8, 0, 0)
        lv.setSpacing(12)
        self.queue_wave = Wave(n=30, height=56)
        self.queue_wave.set_idle(glow=False)
        lv.addWidget(self.queue_wave)
        self.queue_sub = QLabel("")
        self.queue_sub.setObjectName("footerNote")
        lv.addWidget(self.queue_sub)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        inner.setObjectName("fileList")
        self.list_lay = QVBoxLayout(inner)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        scroll.setWidget(inner)
        lv.addWidget(scroll, 1)
        self.stack.addWidget(listing)

        # 2 · обработка / готово / прервано: волна-индикатор + детали
        proc = QWidget()
        pv = QVBoxLayout(proc)
        pv.setContentsMargins(8, 0, 8, 0)
        pv.addStretch(1)
        self.proc_wave = Wave(height=100)
        pv.addWidget(self.proc_wave)
        pv.addSpacing(18)
        self.pv_title = QLabel("Обработка")
        self.pv_title.setObjectName("pvTitle")
        self.pv_title.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_title)
        pv.addSpacing(6)
        self.pv_l1 = QLabel("")
        self.pv_l1.setObjectName("pvLine")
        self.pv_l1.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_l1)
        self.pv_l2 = QLabel("")
        self.pv_l2.setObjectName("pvLine")
        self.pv_l2.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_l2)
        pv.addStretch(1)
        self.stack.addWidget(proc)

        # 3 · готово: «квитанция» — моноширинная сводка с пунктиром
        donep = QWidget()
        dvp = QVBoxLayout(donep)
        dvp.setContentsMargins(0, HEADER_H - 8, 0, 18)   # очистить шапку-оверлей; центрируют растяжки в cl
        center = QHBoxLayout()
        center.addStretch(1)
        col = QWidget()
        col.setFixedWidth(500)                      # «квитанция» (шире — под кнопку «проверить»)
        col.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addStretch(1)                            # центрируем «квитанцию» по вертикали

        self.done_title = QLabel("CENSR · ГОТОВО")
        self.done_title.setObjectName("rcptHead")
        self.done_title.setAlignment(Qt.AlignHCenter)
        cl.addWidget(self.done_title)
        cl.addSpacing(12)

        d1 = QFrame()
        d1.setObjectName("rcptDash")
        d1.setFixedHeight(1)
        cl.addWidget(d1)
        self._d1 = d1
        cl.addSpacing(6)

        hdr = QWidget()                            # шапка колонок: файл · мата · длит
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(0, 0, 0, 4)
        hh.setSpacing(8)
        kf = QLabel("файл")
        kf.setObjectName("rcptHdr")
        ks = QLabel("мата · длит")
        ks.setObjectName("rcptHdr")
        ks.setFixedWidth(86)
        ks.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sp1 = QWidget()
        sp1.setFixedWidth(26)
        sp2 = QWidget()
        sp2.setFixedWidth(26)
        sp3 = QWidget()
        sp3.setFixedWidth(84)                      # под кнопку «проверить»
        hh.addWidget(kf, 1)
        hh.addWidget(ks)
        hh.addWidget(sp1)
        hh.addWidget(sp2)
        hh.addWidget(sp3)
        cl.addWidget(hdr)
        self._hdr = hdr

        self.done_scroll = QScrollArea()
        self.done_scroll.setWidgetResizable(True)
        self.done_scroll.setFrameShape(QScrollArea.NoFrame)
        self.done_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.done_scroll.setMaximumHeight(360)     # ~10 строк, дальше — скролл
        self.done_scroll.setStyleSheet("background: transparent; border: none;")
        self.done_scroll.viewport().setStyleSheet("background: transparent;")
        dl_inner = QWidget()
        dl_inner.setStyleSheet("background: transparent;")
        self.done_list = QVBoxLayout(dl_inner)
        self.done_list.setContentsMargins(0, 0, 0, 0)
        self.done_list.setSpacing(0)
        self.done_list.addStretch(1)
        self.done_scroll.setWidget(dl_inner)
        cl.addWidget(self.done_scroll)

        self.cancel_box = QWidget()                 # экран «Прервано» с 0 готовых файлов
        cbl = QVBoxLayout(self.cancel_box)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(8)
        cbl.addStretch(1)
        cico = QLabel()
        cico.setPixmap(_stop_icon("#e0a13a", 32))
        cico.setAlignment(Qt.AlignHCenter)
        cbl.addWidget(cico)
        ctit = QLabel("Прервано")
        ctit.setObjectName("cancelTitle")
        ctit.setAlignment(Qt.AlignHCenter)
        cbl.addWidget(ctit)
        self.cancel_sub = QLabel("")
        self.cancel_sub.setObjectName("dlgSub")
        self.cancel_sub.setAlignment(Qt.AlignHCenter)
        cbl.addWidget(self.cancel_sub)
        cbl.addStretch(1)
        self.cancel_box.hide()
        cl.addWidget(self.cancel_box)

        cl.addSpacing(16)                           # воздух между списком и итогами (одной группой)
        d2 = QFrame()
        d2.setObjectName("rcptDash")
        d2.setFixedHeight(1)
        cl.addWidget(d2)
        self._d2 = d2
        cl.addSpacing(8)

        self._kv_rows = []
        for key, attr in (("итого заглушено мата", "tot_mata"),
                          ("время обработки", "tot_time"),
                          ("сохранено", "tot_saved")):
            kvw = QWidget()
            kvh = QHBoxLayout(kvw)
            kvh.setContentsMargins(0, 2, 0, 2)
            kvh.setSpacing(8)
            k = QLabel(key)
            k.setObjectName("rcptKvK")
            v = QLabel("")
            v.setObjectName("rcptKvV")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            kvh.addWidget(k)
            kvh.addStretch(1)
            kvh.addWidget(v)
            setattr(self, attr, v)
            cl.addWidget(kvw)
            self._kv_rows.append(kvw)
        cl.addStretch(1)                            # нижняя растяжка — «квитанция» по центру

        center.addWidget(col)
        center.addStretch(1)
        dvp.addLayout(center, 1)                     # колонка на всю высоту экрана
        self.stack.addWidget(donep)

        host = QWidget()
        host.setLayout(self.stack)
        self.col.addWidget(host, 1)

    # — низ
    def _build_bottom(self):
        self.bottom = QWidget()
        bb = QVBoxLayout(self.bottom)
        bb.setContentsMargins(0, 0, 0, 0)
        bb.setSpacing(12)
        foot = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("footerNote")
        foot.addWidget(self.note)
        foot.addStretch(1)
        self.add_link = QPushButton("+ добавить файлы")
        self.add_link.setObjectName("linkAdd")
        self.add_link.setCursor(Qt.PointingHandCursor)
        self.add_link.clicked.connect(self._pick_files)
        foot.addWidget(self.add_link)
        self.clear_btn = QPushButton("очистить всё")
        self.clear_btn.setObjectName("linkDim")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear)
        foot.addWidget(self.clear_btn)
        bb.addLayout(foot)
        act = QHBoxLayout()
        act.setSpacing(8)
        self.open_btn = QPushButton("открыть папку")
        self.open_btn.setObjectName("ghostBtn")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_output)
        self.open_btn.hide()
        act.addWidget(self.open_btn)
        self.start_btn = QPushButton("НАЧАТЬ ОБРАБОТКУ")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start_stop)
        act.addWidget(self.start_btn, 1)
        bb.addLayout(act)
        self.bottom.hide()
        self.col.addWidget(self.bottom)

    # — словомарка
    def _render_mark(self):
        color = theme.ACCENT if self._caret_on else "transparent"
        self.mark.setText('censr<span style="color:%s">_</span>' % color)

    def _toggle_caret(self):
        self._caret_on = not self._caret_on
        self._render_mark()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "header"):
            self.header.setGeometry(0, 0, self.centralWidget().width(), HEADER_H)

    # — drag&drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self._add_files(_collect([Path(u.toLocalFile()) for u in e.mimeData().urls()]))

    # — файлы
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Аудио и видео", "",
            "Аудио и видео (" + " ".join("*%s" % e for e in sorted(AUDIO_EXT)) + ");;Все файлы (*)")
        self._add_files([Path(f) for f in files])

    def _add_files(self, files):
        if self.worker is not None:
            return
        known = {str(r.path) for r in self.rows}
        for f in files:
            if str(f) in known:
                continue
            row = FileRow(f)
            row.removed.connect(self._remove_row)
            row.dur_ready.connect(self._update_note)
            row.tracks_ready.connect(self._refresh_gutters)
            self.rows.append(row)
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self._sync()

    def _refresh_gutters(self):
        """Гаттер под шеврон резервируется на всю очередь, только если есть мультитрек —
        тогда номера строк выровнены; иначе строки прижаты к левому краю колонки."""
        any_multi = any(len(r.streams) > 1 for r in self.rows)
        for r in self.rows:
            r.set_gutter(any_multi)

    def _remove_row(self, row):
        if self.worker is not None:
            return
        if row in self.rows:
            self.rows.remove(row)
            row.deleteLater()
        self._sync()

    def _clear(self):
        if self.worker is not None:
            return
        for r in self.rows:
            r.deleteLater()
        self.rows = []
        self._sync()

    def _sync(self):
        has = bool(self.rows)
        self.stack.setCurrentIndex(1 if has else 0)
        self.bottom.setVisible(has)
        for i, r in enumerate(self.rows, 1):
            r.set_number(i)
        if self.worker is None:
            self.start_btn.setText("НАЧАТЬ ОБРАБОТКУ")
        self._refresh_gutters()
        self._update_note()

    def _update_note(self):
        if not self.rows:
            self.note.setText("")
            return
        n = len(self.rows)
        word = "файл" if n == 1 else "файла" if n < 5 else "файлов"
        self.queue_sub.setText("%d %s в очереди" % (n, word))
        total = sum(r.dur_sec for r in self.rows)
        self.note.setText("общая длительность %s" % _fmt(total) if total else "")

    # — действия шапки
    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_to(self.settings)
            self.settings.save()

    def _open_dicts(self):
        dlg = DictionariesDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_to(self.settings)
            self.settings.save()

    def _about(self):
        AboutDialog(self).exec()

    def _open_output(self):
        opened = 0
        for d in self._out_dirs:
            if d and os.path.isdir(d):
                os.startfile(d)  # noqa: S606
                opened += 1
                if opened >= 4:        # не плодить десятки окон проводника
                    break

    # — обработка
    def _start_stop(self):
        if self.worker is not None:
            self._cancelled = True
            self.worker.stop()
            self.start_btn.setEnabled(False)
            self.start_btn.setText("останавливаю…")
            return
        if not self.rows:
            QMessageBox.information(self, "Censr", "Добавь файлы для обработки.")
            return
        self._cancelled = False
        self.stack.setCurrentIndex(2)
        self.note.setText("")
        self.clear_btn.hide()
        self.add_link.hide()
        self.open_btn.hide()
        self.start_btn.setText("ОСТАНОВИТЬ")
        self.pv_title.setText("Обработка")
        self.proc_wave.randomize()                 # своя форма волны на каждый запуск
        self.proc_wave.set_progress(0.0)
        self._total = len(self.rows)
        self._completed = 0
        self._found_total = 0
        self._errors = []
        self._done_files = []
        self._t_start = time.perf_counter()        # отсчёт времени с нажатия кнопки
        self.done_scroll.hide()                    # очистить список готовых от прошлого прогона
        while self.done_list.count() > 1:
            it = self.done_list.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        if self.settings.output_dir:
            self._out_dirs = [self.settings.output_dir]
        else:
            dirs = []
            for r in self.rows:                  # «рядом с файлом»: все папки очереди
                d = str(r.path.parent)
                if d not in dirs:
                    dirs.append(d)
            self._out_dirs = dirs
        if self.taskbar is None:
            self.taskbar = native.TaskbarProgress(self)
        self.taskbar.set(0)
        self.worker = Worker([(str(r.path), r.selected_tracks()) for r in self.rows], self.settings)
        self.worker.file_started.connect(self._on_started)
        self.worker.file_progress.connect(self._on_progress)
        self.worker.file_done.connect(self._on_done)
        self.worker.file_error.connect(self._on_error)
        self.worker.fatal.connect(lambda m: QMessageBox.critical(self, "Censr — ошибка запуска", m))
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _overall(self, cur_pct):
        return int((self._completed * 100 + cur_pct) / max(self._total, 1))

    def _on_started(self, row):
        self.pv_l1.setText("%s · 0%%" % self.rows[row].path.name)
        self.pv_l2.setText("файл %d из %d · найдено мата: %d" % (row + 1, self._total, self._found_total))
        self.proc_wave.set_progress(self._completed / max(self._total, 1))

    def _on_progress(self, row, pct, found, stage=""):
        self.proc_wave.set_progress(self._overall(pct) / 100.0)
        line = "%s · %d%%" % (self.rows[row].path.name, pct)
        if stage:
            line += " · " + stage
        self.pv_l1.setText(line)
        self.pv_l2.setText("файл %d из %d · найдено мата: %d" % (row + 1, self._total, self._found_total + found))
        if self.taskbar:
            self.taskbar.set(self._overall(pct))

    def _on_done(self, row, count, elapsed, dst):
        self._completed += 1
        self._found_total += count
        self._done_files.append({"name": Path(dst).name, "dst": dst,
                                 "count": count, "dur": self.rows[row].dur_sec})
        if self.taskbar:
            self.taskbar.set(self._overall(0))

    def _done_row(self, info):
        row = QWidget()
        row.setObjectName("rcptRow")
        row.setFixedHeight(34)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        name = info["name"]
        nm = QLabel(name if len(name) <= 24 else name[:21] + "…")
        nm.setObjectName("rcptName")
        nm.setToolTip(name)
        st = QLabel("%d · %s" % (info["count"], _fmt(info["dur"])))
        st.setObjectName("rcptStat")
        st.setFixedWidth(86)
        st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        play = QPushButton()
        play.setObjectName("doneOpen")
        play.setFixedSize(26, 24)
        play.setIcon(_play_icon(theme.ACCENT))
        play.setIconSize(QSize(13, 13))
        play.setCursor(Qt.PointingHandCursor)
        play.setToolTip("Воспроизвести файл")
        play.clicked.connect(lambda _=False, p=info["dst"]: self._play(p))
        folder = QPushButton()
        folder.setObjectName("doneOpen")
        folder.setFixedSize(26, 24)
        folder.setIcon(_folder_icon(theme.ACCENT))
        folder.setIconSize(QSize(14, 14))
        folder.setCursor(Qt.PointingHandCursor)
        folder.setToolTip("Показать в папке")
        folder.clicked.connect(lambda _=False, p=info["dst"]: self._reveal(p))
        review = QPushButton("проверить")
        review.setObjectName("reviewBtn")
        review.setFixedWidth(84)
        review.setCursor(Qt.PointingHandCursor)
        review.setToolTip("Проверить и поправить найденное")
        review.clicked.connect(lambda _=False, i=info: self._open_review(i))
        h.addWidget(nm, 1)
        h.addWidget(st)
        h.addWidget(play)
        h.addWidget(folder)
        h.addWidget(review)
        return row

    def _play(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reveal(self, path):
        """Открыть папку с выделением файла (Проводник /select)."""
        p = os.path.normpath(path)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", p],
                                 creationflags=0x08000000)      # без окна консоли
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def _fill_done_list(self):
        while self.done_list.count() > 1:          # очистить, кроме хвостовой растяжки
            it = self.done_list.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for info in self._done_files:
            self.done_list.insertWidget(self.done_list.count() - 1, self._done_row(info))
        # высота списка = под содержимое (все строки видны), дальше — скролл с потолка 360
        n = len(self._done_files)
        if n:
            self.done_scroll.setFixedHeight(min(360, n * 34 + 2))

    def _open_review(self, info):
        import json
        rp = Path(info["dst"]).with_suffix(".report.json")
        try:
            data = json.loads(Path(rp).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Censr", "Не удалось открыть отчёт проверки:\n%s" % e)
            return
        dlg = ReviewDialog(data.get("src", ""), info["dst"], data.get("censored", []),
                           self.settings.mode, self)
        if dlg.exec():
            info["count"] = dlg.applied_count
            self._fill_done_list()

    def _on_error(self, row, err):
        self.rows[row].state = "error"
        self._completed += 1
        self._errors.append((self.rows[row].path.name, err))
        if self.taskbar:
            self.taskbar.error()

    def _on_all_done(self):
        w = self.worker
        self.worker = None
        if w is not None:
            w.wait()              # дождаться полного выхода из run(): иначе QThread
            w.deleteLater()       # может быть уничтожен «на ходу» → краш на 100%
        errors = sum(1 for r in self.rows if r.state == "error")
        done = self._completed - errors
        took = _fmt_dur(time.perf_counter() - getattr(self, "_t_start", time.perf_counter()))
        self.stack.setCurrentIndex(3)                   # страница «Готово» (квитанция)
        if self._cancelled:
            self.done_title.setText("CENSR · ПРЕРВАНО")
        elif errors:
            self.done_title.setText("CENSR · ГОТОВО · ОШИБОК %d" % errors)
        else:
            self.done_title.setText("CENSR · ГОТОВО")
        self.tot_mata.setText(str(self._found_total))
        self.tot_time.setText(took)
        if self.settings.output_dir:
            self.tot_saved.setText(Path(self.settings.output_dir).name or self.settings.output_dir)
        else:
            self.tot_saved.setText("рядом с исходниками")
        # «Прервано» без единого готового файла — центрированное сообщение вместо пустой квитанции
        cancel_only = self._cancelled and not self._done_files
        self.cancel_box.setVisible(cancel_only)
        for wdg in (self.done_title, self._d1, self._hdr, self._d2):
            wdg.setVisible(not cancel_only)
        for kvw in self._kv_rows:
            kvw.setVisible(not cancel_only)
        if cancel_only:
            self.cancel_sub.setText("обработано %d из %d · файлы не изменены" % (done, self._total))
        self._fill_done_list()                     # строки квитанции + кнопки
        self.done_scroll.setVisible(bool(self._done_files))
        self.start_btn.setEnabled(True)
        self.start_btn.setText("ОБРАБОТАТЬ ЕЩЁ")
        try:
            self.start_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.start_btn.clicked.connect(self._reset)
        if done:
            self.open_btn.show()
        if self.taskbar:
            self.taskbar.clear()
        if self._errors:
            detail = "\n\n".join("%s:\n%s" % (n, e[-400:].strip()) for n, e in self._errors[:5])
            if len(self._errors) > 5:
                detail += "\n\n…ещё %d" % (len(self._errors) - 5)
            QMessageBox.warning(self, "Censr — ошибки при обработке", detail)

    def _reset(self):
        for r in self.rows:
            r.deleteLater()
        self.rows = []
        self.open_btn.hide()
        self.clear_btn.show()
        self.add_link.show()
        try:
            self.start_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.start_btn.clicked.connect(self._start_stop)
        self.start_btn.setText("НАЧАТЬ ОБРАБОТКУ")
        self._sync()

    def closeEvent(self, e):
        """Аккуратно останавливаем воркер и фоновые задачи, чтобы Qt не падал
        с «QThread: Destroyed while thread is still running»."""
        if self.worker is not None and self.worker.isRunning():
            self._cancelled = True
            self.worker.stop()
            if not self.worker.wait(15000):      # дать доделать текущий файл
                self.worker.terminate()
                self.worker.wait(2000)
        QThreadPool.globalInstance().waitForDone(2000)   # _DurTask'и длительности
        if self.taskbar is not None:
            self.taskbar.close()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    theme.apply(app)
    ico = app_base_dir() / "censr.ico"          # иконка окна/таскбара (вместо дефолтной pythonw)
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    w = MainWindow()
    w.show()
    native.apply_window_effects(w)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
