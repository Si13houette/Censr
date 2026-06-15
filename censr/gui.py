# -*- coding: utf-8 -*-
"""GUI Censr — единый стиль «звуковая волна»: один индикатор-волна проходит
через все экраны (покой → прогресс → полная → прервана)."""

from __future__ import annotations

import copy
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import (
    QObject, QPointF, QRectF, QRunnable, QSize, QThread, QThreadPool,
    Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QFontMetricsF, QIcon, QImage,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QPolygonF, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QStackedLayout, QVBoxLayout, QWidget,
)

from . import __version__, native, theme
from .settings import AUDIO_EXT, DEFAULT_SUFFIX, Settings, app_base_dir, default_model_dir
from .wave import Wave

COLUMN_W = 580
HEADER_H = 56


def _collect(paths):
    files = []
    for p in paths:
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in AUDIO_EXT]
        elif p.suffix.lower() in AUDIO_EXT:
            files.append(p)
    return files


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русская плюрализация: 1 файл, 2 файла, 5 файлов, 21 файл, 12 файлов."""
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


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


def _fmt_eta(sec: float) -> str:
    """Оценка остатка: грубое округление, чтобы цифра не дёргалась."""
    if sec < 75:
        return "%d с" % (max(1, round(sec / 10)) * 10)   # округление до десятков секунд
    m = round(sec / 60)
    if m < 60:
        return "%d мин" % m
    return "%d ч %02d мин" % (m // 60, m % 60)


def _phrase_context(words, start: float, end: float, n: int = 3, max_chars: int = 56):
    """Соседние слова вокруг [start, end] из транскрипта — контекст для «проверки».

    words — list[Word] из кэша транскрипта. Возвращает (before, target, after)
    списками строк/строкой, или None, если слово не нашлось рядом (±1 c)."""
    if not words:
        return None
    best, best_d = None, 1.0
    for i, w in enumerate(words):
        if w.start <= start <= w.end:      # таймкод внутри слова — точное попадание
            d = 0.0
        else:
            d = min(abs(w.start - start), abs(w.end - start))
        if d < best_d or (d == best_d and best is not None
                          and abs(w.start - start) < abs(words[best].start - start)):
            best, best_d = i, d
    if best is None:
        return None
    tw = words[best]
    # соседи только из той же фразы: пауза >4 c — другая сцена, склейка вводит в заблуждение
    before = [w.word for w in words[max(0, best - n):best] if tw.start - w.end <= 4.0]
    after = [w.word for w in words[best + 1:best + 1 + n] if w.start - tw.end <= 4.0]
    while before and len(" ".join(before)) > max_chars // 2:
        before.pop(0)
    while after and len(" ".join(after)) > max_chars // 2:
        after.pop()
    return before, tw.word, after


# ======================================================================== воркеры
class _ProbeSignals(QObject):
    dur = Signal(float)
    streams = Signal(object)      # list[AudioStream]


class _ProbeTask(QRunnable):
    """Один ffprobe на файл: длительность + список аудиодорожек."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = _ProbeSignals()

    def run(self):
        dur = 0.0
        streams = []
        try:
            # разбор дорожек — общий с audio.list_audio_streams (своя копия парсера
            # разошлась бы молча); добавлен только format=duration, чтобы остаться
            # в одном вызове ffprobe на файл
            from .audio import STREAM_ENTRIES, run_ff, stream_from_probe
            out = run_ff(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration:" + STREAM_ENTRIES,
                 "-select_streams", "a", "-of", "json", self.path],
                text=True, timeout=15)
            data = json.loads(out.stdout)
            try:
                dur = float((data.get("format") or {}).get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            streams = [stream_from_probe(i, s)
                       for i, s in enumerate(data.get("streams") or [])]
        except Exception:
            pass
        self.signals.dur.emit(dur)
        self.signals.streams.emit(streams)


class _CollectSignals(QObject):
    done = Signal(object)         # list[str]


class _CollectTask(QRunnable):
    """Рекурсивный обход брошенных папок — вне main thread (сетевые диски и т.п.)."""

    def __init__(self, paths):
        super().__init__()
        self.paths = [str(p) for p in paths]
        self.signals = _CollectSignals()

    def run(self):
        try:
            files = _collect([Path(p) for p in self.paths])
        except Exception:
            files = []
        self.signals.done.emit([str(f) for f in files])


class _FragSignals(QObject):
    done = Signal(int, str)       # номер запроса, путь к wav ("" — ошибка)


class _FragTask(QRunnable):
    """Вырезает фрагмент во временный wav для прослушивания в «проверке».

    zone=(from,to) → фрагмент глушится так же, как в выходе («после»);
    None → оригинал как есть («до»)."""

    def __init__(self, n: int, src: str, start: float, end: float, wav: str,
                 zone=None, mode: str = "silence"):
        super().__init__()
        self.n, self.src, self.start, self.end, self.wav = n, src, start, end, wav
        self.zone, self.mode = zone, mode
        self.signals = _FragSignals()

    def run(self):
        try:
            from .audio import run_ff
            sr = 22050
            if self.zone is None:                  # «до»: прямой быстрый вырез
                run_ff(["ffmpeg", "-v", "error", "-y", "-ss", str(self.start),
                        "-to", str(self.end), "-i", self.src, "-ac", "1", "-ar", str(sr),
                        "-c:a", "pcm_s16le", self.wav], timeout=30)
            else:                                  # «после»: вырез → глушение зоны → wav
                import numpy as np

                from .audio import apply_censor
                out = run_ff(["ffmpeg", "-v", "error", "-ss", str(self.start),
                              "-to", str(self.end), "-i", self.src, "-ac", "1", "-ar", str(sr),
                              "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"], timeout=30)
                samp = np.frombuffer(out.stdout, dtype=np.float32).copy()
                a = max(0.0, self.zone[0] - self.start)      # зона в координатах фрагмента
                b = max(a, self.zone[1] - self.start)
                samp = apply_censor(samp, sr, [(a, b)], mode=self.mode)
                run_ff(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr),
                        "-ac", "1", "-i", "pipe:0", "-c:a", "pcm_s16le", self.wav],
                       input=np.ascontiguousarray(samp, dtype=np.float32).tobytes(), timeout=30)
            self.signals.done.emit(self.n, self.wav)
        except Exception:
            self.signals.done.emit(self.n, "")


class _RecensorSignals(QObject):
    done = Signal(str)            # "" — успех, иначе текст ошибки


class _RecensorTask(QRunnable):
    """Перерисовка выходного файла по правленым зонам — вне main thread."""

    def __init__(self, src: str, dst: str, zones_by_track: dict, mode: str):
        super().__init__()
        self.src, self.dst, self.zbt, self.mode = src, dst, zones_by_track, mode
        self.signals = _RecensorSignals()

    def run(self):
        try:
            from .pipeline import recensor
            recensor(self.src, self.dst, self.zbt, mode=self.mode)
            self.signals.done.emit("")
        except Exception as e:
            self.signals.done.emit(str(e) or repr(e))


_TR_LOCK = threading.Lock()
_TRANSCRIBERS: dict[str, object] = {}     # model_id → Transcriber (живёт между пачками)


def _get_transcriber(model_dir: str):
    """Transcriber с кэшем: модель грузится один раз за сессию, а не на каждую
    пачку (1–3 с). Ключ — идентификатор модели, смена model_dir создаёт новый."""
    from .asr import Transcriber, model_id_for
    path = model_dir or default_model_dir()
    key = model_id_for(path)
    with _TR_LOCK:
        tr = _TRANSCRIBERS.get(key)
    if tr is None:
        tr = Transcriber(model_path=path)
        with _TR_LOCK:
            _TRANSCRIBERS.setdefault(key, tr)
    return tr


class _PreloadTask(QRunnable):
    """Фоновая предзагрузка модели при старте — первый «НАЧАТЬ» без паузы.
    Запускается только если модель уже на диске (не качает 250 МБ молча)."""

    def __init__(self, model_dir: str):
        super().__init__()
        self._md = model_dir

    def run(self):
        try:
            _get_transcriber(self._md)
        except Exception:
            pass                          # не вышло — загрузится как обычно, с ошибкой в UI
        try:
            from .detector import prewarm
            prewarm()                     # словарь pymorphy3 — тоже заранее, в фоне
        except Exception:
            pass


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
        try:
            from .audio import kill_active_ff
            kill_active_ff()              # снять живой ffmpeg/ffprobe — отмена мгновенная
        except Exception:
            pass

    def run(self):  # noqa: C901
        try:
            from .asr import Cancelled, Transcriber
            from .audio import AudioError
            from .audio_zone import ZoneParams
            from .detector import ProfanityDetector
            from .pipeline import censor_file

            zone_params = ZoneParams.from_edge_pct(self.s.edge_keep_pct, full=self.s.full_mute)
            tr = _get_transcriber(self.s.model_dir)
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

        def _report_done(row, rep, t0):
            """Отчёт + сигнал готовности файла (общий для обоих путей конвейера)."""
            if self.s.write_report:        # отчёт — по настройке (по умолчанию выключен)
                try:
                    Path(rep.dst).with_suffix(".report.json").write_text(
                        json.dumps({**rep.to_dict(), "mode": self.s.mode},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError as e:       # сам файл обработан — не выдавать его за провал
                    self.file_error.emit(row, "Файл обработан и сохранён:\n%s\n\nНо отчёт для "
                                              "«проверить» записать не удалось: %s" % (rep.dst, e))
                    return
            self.file_done.emit(row, rep.flagged_words, time.perf_counter() - t0, str(rep.dst))

        def _settle(item):
            """Дождаться фонового энкода файла N и отрапортовать его."""
            row_, rep_, fut_, t0_ = item
            try:
                fut_.result()
            except (Cancelled, AudioError) as e:
                if self._stop or isinstance(e, Cancelled):
                    raise Cancelled() from e   # отмена: ffmpeg прибит — не ошибка файла
                self.file_error.emit(row_, str(e))
                return
            except Exception:
                self.file_error.emit(row_, traceback.format_exc())
                return
            _report_done(row_, rep_, t0_)

        enc_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="censr-enc")
        pending = None                     # (row, rep, future, t0) — энкод в фоне
        used: set[str] = set()             # выходы этого прогона: a/x.mp3 и b/x.mp3 при
        try:                               # общей папке не должны затирать друг друга
            for row, (f, tracks) in enumerate(self.files):
                if self._stop:
                    break
                self.file_started.emit(row)
                try:
                    src = Path(f)
                    out_dir = Path(self.s.output_dir) if self.s.output_dir else src.parent
                    dst = out_dir / ("%s%s%s" % (src.stem, DEFAULT_SUFFIX, src.suffix))
                    base, k = dst, 2
                    while os.path.normcase(str(dst)) in used:
                        dst = base.with_name("%s (%d)%s" % (base.stem, k, base.suffix))
                        k += 1
                    used.add(os.path.normcase(str(dst)))
                    last = [-1, -1, ""]

                    def report(frac, found=0, stage="", row=row, last=last):
                        p = int(frac * 100)
                        if p != last[0] or found != last[1] or stage != last[2]:
                            last[0], last[1], last[2] = p, found, stage
                            self.file_progress.emit(row, p, found, stage)

                    t0 = time.perf_counter()
                    rep, fin = censor_file(src, dst, tr, det, mode=self.s.mode,
                                           progress=report, cancel=lambda: self._stop,
                                           zone_params=zone_params, use_cache=self.s.use_cache,
                                           tracks=tracks,
                                           max_passes=3 if self.s.thorough_clean else 1,
                                           defer_encode=True)
                    if pending is not None:    # файл N−1 докодировался, пока шёл ASR N;
                        cur, pending = pending, None   # снять ссылку ДО settle — при отмене
                        _settle(cur)                   # хвост ниже не сеттлит его повторно
                    if fin is None:            # мультитрек/видео — уже готов
                        _report_done(row, rep, t0)
                    else:                      # энкод — в фон, ASR следующего не ждёт его
                        pending = (row, rep, enc_pool.submit(fin), t0)
                except Cancelled:
                    break
                except AudioError as e:
                    if self._stop:
                        break                 # ffmpeg прибит отменой — это не ошибка файла
                    self.file_error.emit(row, str(e))
                except Exception:
                    self.file_error.emit(row, traceback.format_exc())
            if pending is not None:            # хвост конвейера
                try:
                    _settle(pending)
                except Cancelled:
                    pass
        finally:
            enc_pool.shutdown(wait=True)       # пул и его поток не оставить висящими
            self.all_done.emit()               # UI всегда выходит из «обработки», даже при сбое


# ======================================================================== строка файла
def _chevron_icon(direction: str, color: str, size: int = 12) -> QIcon:
    """Рисованный шеврон (вправо/вниз) — не зависит от системного шрифта."""
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


def _stopsq_icon(color: str, size: int = 13) -> QIcon:
    """Иконка «стоп» (квадрат) — состояние «играет» у кнопки прослушивания."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    s = size * dpr
    p.drawRoundedRect(QRectF(s * 0.26, s * 0.26, s * 0.48, s * 0.48), 2 * dpr, 2 * dpr)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def _stop_icon(color: str, size: int = 30) -> QPixmap:
    """Иконка «стоп/прервано» (кружок с диагональю) — для экрана «Прервано»."""
    import math
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


def _check_icon(color: str, size: int = 13) -> QPixmap:
    """Чистая галочка (две линии со скруглёнными концами) — маркер «готово»."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.8 * dpr)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = size * dpr
    p.drawPolyline(QPolygonF([QPointF(s * 0.18, s * 0.52),
                              QPointF(s * 0.42, s * 0.74),
                              QPointF(s * 0.82, s * 0.28)]))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class FileRow(QFrame):
    removed = Signal(object)
    dur_ready = Signal()
    tracks_ready = Signal()
    toggled = Signal()                 # раскрытие/сворачивание мультитрек-строки

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
        self.close_btn.setFixedSize(28, 26)        # зона клика крупнее самого ✕
        self.close_btn.setCursor(Qt.PointingHandCursor)
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

        self._task = _ProbeTask(str(path))
        self._task.signals.dur.connect(self._set_duration)
        self._task.signals.streams.connect(self._set_tracks)
        QThreadPool.globalInstance().start(self._task)

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
        self.toggled.emit()            # пересчитать высоту списка под раскрытую строку

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


_GRAIN_PX = None


def _grain_tile(size: int = 140, alpha: int = 14) -> QPixmap:
    """Тайл плёночного зерна: белый шум малой непрозрачности (генерится один раз)."""
    global _GRAIN_PX
    if _GRAIN_PX is not None:
        return _GRAIN_PX
    import numpy as np                          # векторно: раньше был питон-цикл на ~20k пикселей
    rng = np.random.default_rng(7)              # фиксированное зерно — кадр стабилен
    a = np.where(rng.random((size, size)) < 0.5,
                 rng.integers(0, alpha + 1, (size, size)), 0).astype(np.uint8)
    buf = np.empty((size, size, 4), np.uint8)   # порядок Format_ARGB32 на little-endian: B,G,R,A
    buf[..., 0] = 255; buf[..., 1] = 255; buf[..., 2] = 255
    buf[..., 3] = a
    img = QImage(buf.tobytes(), size, size, QImage.Format_ARGB32).copy()  # copy: владеть данными
    _GRAIN_PX = QPixmap.fromImage(img)
    return _GRAIN_PX


class EqWordmark(QWidget):
    """Эквалайзер-типографика (стиль 30f): символы прыгают по бейзлайну, как
    столбики эквалайзера, и медленно «дышат». Главный экран — «censr»,
    очередь и «Готово» — цифры-счётчики (set_text)."""

    clicked = Signal()

    _OFFS = (-44, 10, -70, -6, -52)            # паттерн смещений (макет 30f)
    _PHASE = (0.0, 1.3, 2.6, 3.9, 5.2)         # фазы дыхания
    _REF_H = 420.0                             # высота сцены макета — для масштаба

    def __init__(self, text: str = "censr", parent=None, height: int = 320,
                 font_k: float = 0.478, offs_k: float = 1.0,
                 interactive: bool = False):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._text = text
        self._font_k = font_k                  # кегль = font_k · height
        self._offs_k = offs_k                  # масштаб паттерна смещений
        self._interactive = interactive
        if interactive:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Выбрать файлы (Ctrl+O)")
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)                  # 20 fps — дыханию хватает

    def set_text(self, text: str):
        if text != self._text:
            self._text = text
            self.update()

    def _tick(self):
        self._t += 0.045
        self.update()

    def mousePressEvent(self, e):
        if self._interactive and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        k = h / self._REF_H                    # масштаб сцены от макета
        offs = [self._OFFS[i % len(self._OFFS)] * k * self._offs_k
                for i in range(len(self._text))]
        f = QFont()
        f.setFamilies(["JetBrains Mono", "Cascadia Code", "Consolas",
                       "DejaVu Sans Mono"])   # как wordmark в теме + запасной для CI
        f.setStyleHint(QFont.Monospace)
        f.setBold(True)
        px = int(h * self._font_k)
        f.setPixelSize(px)
        f.setLetterSpacing(QFont.AbsoluteSpacing, -px * 0.035)
        fm = QFontMetricsF(f)
        adv = [fm.horizontalAdvance(ch) for ch in self._text]
        total = sum(adv)
        maxw = w * 0.94
        if total > maxw and total > 0:         # не вылезать за ширину виджета (узкое окно)
            px = max(int(px * maxw / total), 1)
            f.setPixelSize(px)
            f.setLetterSpacing(QFont.AbsoluteSpacing, -px * 0.035)
            fm = QFontMetricsF(f)
            adv = [fm.horizontalAdvance(ch) for ch in self._text]
            total = sum(adv)
        x = (w - total) / 2.0
        base = h * 0.74
        amp = 9.0 * k                          # амплитуда дыхания
        # ореол за самым «подпрыгнувшим» символом — до текста, светит из-под него
        gi = min(range(len(offs)), key=lambda i: offs[i])
        gx = x + sum(adv[:gi]) + adv[gi] / 2.0
        gy = base + offs[gi] - fm.capHeight() / 2.0
        glow = QRadialGradient(gx, gy, max(150 * k, px * 0.9))
        c = QColor(theme.PRIMARY)
        c.setAlpha(48)                         # ореол приглушён (ближе к плоской теме)
        glow.setColorAt(0.0, c)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(glow))
        # у низких счётчиков круг ореола упирается в кромки виджета — растворяем
        # срез плавным переходом в фон сверху и снизу
        bg, tr = QColor(theme.BG), QColor(theme.BG)
        tr.setAlpha(0)
        fade = 26.0
        for y0, y1 in ((0.0, fade), (float(h), h - fade)):
            lg = QLinearGradient(0, y0, 0, y1)
            lg.setColorAt(0.0, bg)
            lg.setColorAt(1.0, tr)
            p.fillRect(QRectF(0, min(y0, y1), w, fade), QBrush(lg))
        # символы: вертикальный градиент, «подпрыгнувший» ярче
        for i, ch in enumerate(self._text):
            dy = offs[i] + amp * math.sin(self._t + self._PHASE[i % len(self._PHASE)])
            path = QPainterPath()
            path.addText(x, base + dy, f, ch)
            r = path.boundingRect()
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            if i == gi:
                g.setColorAt(0.0, QColor("#b6bdf5"))
                g.setColorAt(1.0, QColor(theme.PRIMARY))
            else:
                g.setColorAt(0.0, QColor(theme.ACCENT))
                g.setColorAt(1.0, QColor("#3d4480"))
            p.fillPath(path, QBrush(g))
            x += adv[i]
        p.end()


class StatusDot(QWidget):
    """Точка-статус файла (как в Linear/Slack): зелёная — готов, индиго с
    пульсирующим ореолом — в работе, тусклая — ждёт, красная — ошибка."""

    _COLORS = {"done": theme.GREEN, "run": theme.ACCENT,
               "error": theme.RED, "wait": "#3a3a42"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._state = "wait"
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        if state == "run":
            self._timer.start(50)              # пульс — только у активного файла
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._t += 0.18
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c = QColor(self._COLORS[self._state])
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self._state == "run":               # дышащий ореол
            k = (math.sin(self._t) + 1) / 2    # 0..1
            halo = QColor(c)
            halo.setAlpha(int(28 + 36 * (1 - k)))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            r = 5.0 + 3.0 * k
            p.drawEllipse(QPointF(cx, cy), r, r)
        elif self._state == "error":           # статичный тревожный отсвет
            halo = QColor(c)
            halo.setAlpha(40)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(cx, cy), 6.0, 6.0)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        p.end()


class GrainOverlay(QWidget):
    """Плёночное зерно на всё окно (пустой экран): тайл шума, прозрачен для мыши."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(0.55)
        p.drawTiledPixmap(self.rect(), _grain_tile())
        p.end()


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

    def set_value(self, i: int):
        self._btns[i if 0 <= i < len(self._btns) else 0].setChecked(True)
        self._refresh()


class EdgeChips(QWidget):
    """Слышимость краёв, вариант 9: три чипа с иконками громкости."""

    _LEVELS = [("мин", 0, 5, "слышна только первая буква — максимально глухо"),
               ("средне", 1, 12, "короткие края («б…ть»), середина в тишине"),
               ("больше", 2, 20, "края заметнее — мягкая цензура")]

    def __init__(self, pct, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        self._btns = []
        for label, lvl, p, tip in self._LEVELS:
            b = QPushButton("  " + label, self)
            b.setObjectName("modeChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tip)
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
        self.cancel_btn = None
        if cancel_text:
            c = QPushButton(cancel_text)
            c.setObjectName("dlgCancel")
            c.setCursor(Qt.PointingHandCursor)
            c.clicked.connect(self.reject)
            row.addWidget(c)
            self.cancel_btn = c
        o = QPushButton(ok_text)
        o.setObjectName("dlgOk")
        o.setCursor(Qt.PointingHandCursor)
        o.clicked.connect(self.accept)
        row.addWidget(o)
        self.ok_btn = o
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
        self.mode.set_value({"silence": 0, "beep": 1, "noise": 2}.get(s.mode, 0))
        root.addLayout(_kv_row("режим глушения", self.mode))
        root.addWidget(_sep())

        self.path = PathChooser(s.output_dir)
        root.addLayout(_kv_row("выходная папка", self.path))
        root.addWidget(_sep())

        self.edge = EdgeChips(int(s.edge_keep_pct))
        root.addLayout(_kv_row("слышимость краёв", self.edge))
        cap = QLabel("какая часть краёв слова остаётся слышна — помогает понять, что было заглушено")
        cap.setObjectName("dlgSub")
        cap.setWordWrap(True)
        root.addWidget(cap)
        root.addWidget(_sep())

        self.full = QCheckBox("  максимальная очистка — глушить слово целиком")
        self.full.setObjectName("trackCheck")
        self.full.setChecked(bool(s.full_mute))
        self.full.setToolTip("Без слышимых краёв: повторная обработка не находит остатков")
        # «целиком» обнуляет края — чипы выше не действуют, и это видно
        self.full.toggled.connect(lambda on: self.edge.setEnabled(not on))
        self.edge.setEnabled(not s.full_mute)
        root.addWidget(self.full)

        self.thorough = QCheckBox("  тщательная очистка — несколько проходов (медленнее)")
        self.thorough.setObjectName("trackCheck")
        self.thorough.setChecked(bool(s.thorough_clean))
        self.thorough.setToolTip("Повторно распознаёт уже заглушенный звук и добивает пропуски")
        root.addWidget(self.thorough)

        self.report = QCheckBox("  файл отчёта — список заглушенного рядом с результатом")
        self.report.setObjectName("trackCheck")
        self.report.setChecked(bool(s.write_report))
        self.report.setToolTip("Пишет <имя>.report.json — без него кнопка «проверить» недоступна")
        root.addWidget(self.report)
        self.add_buttons()

    def apply_to(self, s: Settings):
        s.mode = ("silence", "beep", "noise")[self.mode.value()]
        s.output_dir = self.path.value()
        s.edge_keep_pct = self.edge.value()
        s.full_mute = self.full.isChecked()
        s.thorough_clean = self.thorough.isChecked()
        s.write_report = self.report.isChecked()


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


class BuiltinRootsDialog(FramelessDialog):
    """Всплывающее окно со встроенным списком корней мата (только просмотр)."""

    def __init__(self, parent=None):
        super().__init__("встроенные корни", parent)
        self.setMinimumWidth(360)
        from .detector import BUILTIN_ROOTS
        root = self.content
        sub = QLabel("ловятся автоматически — с учётом морфологии, приставок и опечаток")
        sub.setObjectName("dlgSub")
        sub.setWordWrap(True)
        root.addWidget(sub)
        view = QPlainTextEdit("\n".join(BUILTIN_ROOTS))
        view.setReadOnly(True)
        view.setFixedHeight(220)
        root.addWidget(view)
        self.add_buttons(ok_text="закрыть", cancel_text="")


class DictionariesDialog(FramelessDialog):
    """Две колонки: глушить | исключения; встроенные — во всплывающем окне."""

    def __init__(self, s: Settings, parent=None):
        super().__init__("словари", parent)
        self.setMinimumWidth(560)
        root = self.content
        SP = 12                                     # единый шаг сетки — один и тот же отступ везде
        root.setSpacing(SP)

        from .detector import BUILTIN_ROOTS

        self.extra = QPlainTextEdit("\n".join(s.extra_words))
        self.extra.setPlaceholderText("дополнительно глушить…")
        self.extra.setFixedHeight(96)
        self.white = QPlainTextEdit("\n".join(s.whitelist))
        self.white.setPlaceholderText("никогда не глушить…")
        self.white.setFixedHeight(96)
        self._det = None                            # кэш детектора для тестера (см. _tester_detector)
        self._det_key = None

        def _head(title, color, count=None):        # одинаковая шапка: цветной заголовок + счётчик
            row = QHBoxLayout()
            lab = QLabel(title)
            lab.setStyleSheet("color: %s; font-size: 13px; font-weight: 700;" % color)
            row.addWidget(lab)
            row.addStretch(1)
            cnt = None
            if count is not None:
                cnt = QLabel(str(count))
                cnt.setObjectName("secCount")
                row.addWidget(cnt)
            return row, cnt

        def _block(title, color, field, n):
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(SP)
            head, cnt = _head(title, color, n)
            box.addLayout(head)
            box.addWidget(field)
            return box, cnt

        left = QVBoxLayout()                        # слева — оба списка стопкой
        left.setSpacing(SP)
        be, self._cnt_extra = _block("＋ глушить", theme.ACCENT, self.extra, len(s.extra_words))
        bw, self._cnt_white = _block("－ исключения", theme.AMBER, self.white, len(s.whitelist))
        left.addLayout(be)
        left.addLayout(bw)

        right = QVBoxLayout()                        # справа — тестер слова (та же сетка)
        right.setSpacing(SP)
        thead, _ = _head("проверить слово", theme.TEXT)
        right.addLayout(thead)
        self._test_in = QLineEdit()
        self._test_in.setPlaceholderText("введи слово…")
        self._test_in.textChanged.connect(self._test_word)
        right.addWidget(self._test_in)
        self._test_out = QLabel("введи слово — покажу, заглушится ли оно")
        self._test_out.setWordWrap(True)
        self._test_out.setAlignment(Qt.AlignTop)
        self._test_out.setMinimumHeight(74)
        self._test_out.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)  # до низа колонки
        self._test_out.setStyleSheet("background:#121215; border:1px solid #232327; "
                                     "border-radius:9px; padding:10px 12px; color:%s;" % theme.DIM)
        right.addWidget(self._test_out)

        cols = QHBoxLayout()
        cols.setSpacing(SP)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        root.addLayout(cols)
        self.extra.textChanged.connect(lambda: self._cnt_extra.setText(str(
            sum(1 for ln in self.extra.toPlainText().splitlines() if ln.strip()))))
        self.white.textChanged.connect(lambda: self._cnt_white.setText(str(
            sum(1 for ln in self.white.toPlainText().splitlines() if ln.strip()))))

        blrow = QHBoxLayout()                        # ссылка на встроенные — по центру
        blrow.addStretch(1)
        bl = QLabel('<a href="#builtin" style="color:%s; text-decoration:none;">'
                    '<b>★ встроенные корни · %d</b></a>' % (theme.ACCENT_HOVER, len(BUILTIN_ROOTS)))
        bl.setTextFormat(Qt.RichText)
        bl.setToolTip("Показать встроенный список корней мата")
        bl.linkActivated.connect(self._open_builtin)
        blrow.addWidget(bl)
        blrow.addStretch(1)
        root.addLayout(blrow)

        hrow = QHBoxLayout()
        hint = QLabel("по одному слову на строку · морфология учитывается автоматически")
        hint.setObjectName("dlgSub")
        hrow.addWidget(hint)
        hrow.addStretch(1)
        io = QLabel('<a href="#imp" style="color:%(a)s; text-decoration:none;">импорт…</a>'
                    '<span style="color:%(d)s"> · </span>'
                    '<a href="#exp" style="color:%(a)s; text-decoration:none;">экспорт…</a>'
                    % {"a": theme.ACCENT, "d": theme.FAINT})
        io.setObjectName("dlgSub")
        io.setTextFormat(Qt.RichText)
        io.setToolTip("Списки «глушить» и «исключения» — в txt-файл и обратно")
        io.linkActivated.connect(self._import_export)
        hrow.addWidget(io)
        root.addLayout(hrow)
        self.add_buttons(with_stretch=False)

    def _open_builtin(self, *_):
        BuiltinRootsDialog(self).exec()

    def _tester_detector(self):
        """Детектор по ТЕКУЩИМ спискам (пересобирается только при их изменении)."""
        extra = {w.strip() for w in self.extra.toPlainText().splitlines() if w.strip()}
        white = {w.strip() for w in self.white.toPlainText().splitlines() if w.strip()}
        key = (frozenset(extra), frozenset(white))
        if self._det is None or self._det_key != key:
            from .detector import ProfanityDetector
            self._det = ProfanityDetector(extra_words=extra, whitelist=white)
            self._det_key = key
        return self._det

    def _test_word(self):
        word = self._test_in.text().strip()
        base = ("background:#121215; border:1px solid %s; border-radius:9px; "
                "padding:10px 12px; color:%s;")
        if not word:
            self._test_out.setStyleSheet(base % ("#232327", theme.DIM))
            self._test_out.setText("введи слово — покажу, заглушится ли оно")
            return
        try:
            m = self._tester_detector().check(word)
        except Exception:
            self._test_out.setStyleSheet(base % ("#232327", theme.DIM))
            self._test_out.setText("проверка недоступна (нет модулей детектора)")
            return
        if m is not None:
            if m.pattern == "extra_words":
                why = "из вашего списка «глушить»"
            elif m.reason == "fuzzy":
                why = "похоже на мат (опечатка)"
            else:
                why = "встроенный корень мата"
            self._test_out.setStyleSheet(base % ("#3a3f63", theme.ACCENT))
            self._test_out.setText("будет заглушено\n%s" % why)
        else:
            norm = re.sub(r"[^а-я]", "", word.lower().replace("ё", "е"))
            whites = {re.sub(r"[^а-я]", "", w.lower().replace("ё", "е"))
                      for w in self.white.toPlainText().splitlines() if w.strip()}
            why = "в исключениях" if norm and norm in whites else "слово чистое"
            self._test_out.setStyleSheet(base % ("#2f5d3f", theme.GREEN))
            self._test_out.setText("оставим как есть\n%s" % why)

    def _import_export(self, href):
        """Импорт/экспорт пользовательских списков в обычный txt (обмен словарями)."""
        if href == "#imp":
            f, _ = QFileDialog.getOpenFileName(self, "Импорт словаря", "",
                                               "Текст (*.txt);;Все файлы (*)")
            if not f:
                return
            try:
                lines = [ln.strip() for ln in
                         Path(f).read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
            except Exception as e:
                QMessageBox.warning(self, "Censr", "Не удалось прочитать файл словаря:\n%s" % e)
                return
            # понимаем и плоский список, и наш экспорт с секциями «# глушить/# исключения».
            # Накапливаем и пишем одним setPlainText на поле: построчная перезапись
            # документа давала O(n²) и фриз GUI на словарях в тысячи строк
            target = self.extra
            have = {self.extra: set(self.extra.toPlainText().splitlines()),
                    self.white: set(self.white.toPlainText().splitlines())}
            added = {self.extra: [], self.white: []}
            for ln in lines:
                if ln.startswith("#"):
                    target = self.white if "исключ" in ln.lower() else self.extra
                    continue
                if ln not in have[target]:
                    have[target].add(ln)            # дедуп и внутри импортируемого файла
                    added[target].append(ln)
            for fld, new in added.items():
                if new:
                    cur = fld.toPlainText().rstrip()
                    fld.setPlainText((cur + "\n" if cur else "") + "\n".join(new))
            if not any(added.values()):        # тихий импорт сбивал с толку
                QMessageBox.information(self, "Censr", "Новых слов в файле не найдено.")
        else:
            f, _ = QFileDialog.getSaveFileName(self, "Экспорт словаря", "censr-словарь.txt",
                                               "Текст (*.txt)")
            if not f:
                return
            try:
                body = "# глушить\n%s\n\n# исключения\n%s\n" % (
                    self.extra.toPlainText().strip(), self.white.toPlainText().strip())
                Path(f).write_text(body, encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(self, "Censr", "Не удалось записать файл:\n%s" % e)

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
        ver = QLabel("версия %s · движок GigaAM-v3 · ONNX" % __version__)
        ver.setObjectName("aboutVer")
        root.addWidget(ver)

        self.add_buttons(ok_text="закрыть", cancel_text=None)


class ManualAddDialog(FramelessDialog):
    """Своё окно «добавить вручную»: начало/конец в формате мм:сс (или ч:мм:сс)."""

    def __init__(self, parent=None, max_dur=None):
        super().__init__("добавить вручную", parent)
        self.setMinimumWidth(360)
        self.result_range = None
        self._max_dur = max_dur            # длительность файла: конец зоны не должен выходить за неё
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
        self._err = QLabel("")                      # ошибка ввода — на месте, без модалки
        self._err.setObjectName("errText")
        self._err.hide()
        root.addWidget(self._err)
        self.add_buttons(ok_text="добавить", cancel_text="отмена")

    @staticmethod
    def _parse(t):
        """Только «мм:сс», «мм:сс.д» или «ч:мм:сс[.д]» (минуты/секунды 0..59).
        Всё остальное (минус, лишние сегменты, мусор) → None."""
        parts = t.strip().replace(",", ".").split(":")
        if len(parts) == 2:
            h_s, (m_s, s_s) = "0", parts
        elif len(parts) == 3:
            h_s, m_s, s_s = parts
        else:
            return None
        if not (re.fullmatch(r"\d{1,3}", h_s) and re.fullmatch(r"\d{1,2}", m_s)
                and re.fullmatch(r"\d{1,2}(\.\d+)?", s_s)):
            return None
        h, m, s = int(h_s), int(m_s), float(s_s)
        if m > 59 or s >= 60:
            return None
        return h * 3600 + m * 60 + s

    def accept(self):
        s = self._parse(self._fields["s"].text())
        e = self._parse(self._fields["e"].text())
        if s is None or e is None or e <= s:
            self._err.setText("не понял время — формат мм:сс, напр. 00:12.0 – 00:12.6"
                              if (s is None or e is None) else
                              "конец должен быть позже начала")
            self._err.show()
            return
        if self._max_dur and s >= self._max_dur:     # всё начало уже за концом файла
            self._err.setText("за пределами файла (длительность %s)" % _fmt(self._max_dur))
            self._err.show()
            return
        if self._max_dur and e > self._max_dur:       # хвост за концом — подрезать к концу
            e = self._max_dur
        self.result_range = (s, e)
        super().accept()


class _TimelineBar(QWidget):
    """Дорожка времени с метками найденного мата: позиция метки = start/длительность.
    Клик по метке → picked(i) (индекс в порядке self._checks). Цвет: янтарь —
    сомнительное, зелёный — ручное, акцент — точный мат; снятое галочкой — тускло."""

    picked = Signal(int)
    _PAD = 6

    def __init__(self, duration, items, checks, parent=None):
        super().__init__(parent)
        self._dur = max(float(duration or 0.0), 0.1)
        self._items = items                # list[dict], выровнен с checks
        self._checks = checks              # list[QCheckBox] — состояние «оставить»
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("клик по метке — перейти к слову")

    def add_mark(self, it, cb):
        self._items.append(it)
        self._checks.append(cb)
        cb.toggled.connect(self.update)
        self.update()

    def _x(self, t, w):
        span = max(w - 2 * self._PAD, 1)
        return self._PAD + min(max(t, 0.0), self._dur) / self._dur * span

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, cy = self.width(), int(self.height() / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.HAIR))                          # сама дорожка
        p.drawRoundedRect(self._PAD, cy - 1, w - 2 * self._PAD, 3, 1.5, 1.5)
        for it, cb in zip(self._items, self._checks):
            reason = it.get("reason") or ""
            if not cb.isChecked():
                col = QColor(theme.FAINT)                       # снято — не заглушим
            elif "fuzzy" in reason:
                col = QColor(theme.AMBER)                       # сомнительное
            elif reason == "manual":
                col = QColor(theme.GREEN)                       # ручное
            else:
                col = QColor(theme.ACCENT)                      # точный мат
            x = int(self._x(float(it.get("start", 0.0)), w))
            p.setBrush(col)
            p.drawRoundedRect(x - 1, cy - 9, 3, 18, 1.5, 1.5)
        p.end()

    def mousePressEvent(self, e):
        if not self._items:
            return
        x, w = e.position().x(), self.width()
        best, bd = -1, 1e9
        for i, it in enumerate(self._items):
            d = abs(self._x(float(it.get("start", 0.0)), w) - x)
            if d < bd:
                bd, best = d, i
        if best >= 0 and bd <= 14:         # клик рядом с меткой (±14px)
            self.picked.emit(best)


class ReviewDialog(FramelessDialog):
    """Проверка найденного: снять/добавить, прослушать фрагмент, применить
    (перерисовать выходной файл без повторного распознавания)."""

    def __init__(self, src, dst, report, report_path, mode, parent=None,
                 words_by_track=None):
        super().__init__("проверка", parent)
        self.setMinimumWidth(560)
        self.src = src
        self.dst = dst
        self.report = report               # весь dict отчёта (для перезаписи report.json)
        self.report_path = report_path
        self.mode = mode
        self.tracks = list(report.get("tracks") or [0])   # обработанные дорожки (для ручных зон)
        self._words = words_by_track or {}  # транскрипт из кэша: контекст фраз
        # старты слов по дорожкам — для bisect-окна в _context_html (см. там)
        self._starts = {t: [w.start for w in ws] for t, ws in self._words.items()}
        censored = report.get("censored") or []
        self.applied_count = len(censored)
        self._checks = []                  # [(QCheckBox, item)]
        self._rows = []                    # строки, выровнены с _checks (переход с таймлайна)
        self._sfx = None
        self._tmp = None
        self._play_n = 0
        self._play_btn = None              # кнопка ▶, которая сейчас «играет»
        self._busy = False                 # идёт «применить» — диалог не закрывать
        self._kept = []
        self._tasks = []                   # живые QRunnable (не дать GC убить signals)
        root = self.content
        root.setSpacing(6)

        hrow = QHBoxLayout()
        head = QLabel("Найдено мата: %d" % len(censored))
        head.setObjectName("secTitle")
        hrow.addWidget(head)
        hrow.addStretch(1)
        if censored:
            sel = QLabel('<a href="#off" style="color:%(a)s; text-decoration:none;">снять все</a>'
                         '<span style="color:%(d)s"> · </span>'
                         '<a href="#on" style="color:%(a)s; text-decoration:none;">отметить все</a>'
                         % {"a": theme.ACCENT, "d": theme.FAINT})
            sel.setObjectName("dlgSub")
            sel.setTextFormat(Qt.RichText)
            sel.linkActivated.connect(self._check_rows)
            hrow.addWidget(sel)
        root.addLayout(hrow)
        sub = QLabel("сними лишнее, добавь пропущенное, прослушай сомнительное — затем «применить»")
        sub.setObjectName("dlgSub")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self._ab_mode = "до"               # что слушать по ▶: оригинал или результат
        abrow = QHBoxLayout()
        abrow.setSpacing(8)
        abrow.setContentsMargins(0, 6, 0, 0)
        abl = QLabel("слушать")
        abl.setObjectName("dlgSub")
        abrow.addWidget(abl)
        self._ab_grp = QButtonGroup(self)
        self._ab_grp.setExclusive(True)
        for key in ("до", "после"):
            b = QPushButton(key)
            b.setObjectName("modeChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("ab", key)
            self._ab_grp.addButton(b)
            abrow.addWidget(b)
            if key == "до":
                b.setChecked(True)
        self._ab_grp.buttonClicked.connect(self._on_ab)
        abrow.addStretch(1)
        root.addLayout(abrow)

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
        self._empty_lbl = None
        if censored:
            # сомнительные («похоже?») — наверх: именно их и надо проверять
            fuzzy = [it for it in censored if "fuzzy" in (it.get("reason") or "")]
            exact = [it for it in censored if "fuzzy" not in (it.get("reason") or "")]
            if fuzzy and exact:
                self._lay.addWidget(self._sec_label(
                    "сначала проверь сомнительные · %d" % len(fuzzy), "secWarn"))
                for it in fuzzy:
                    self._lay.addWidget(self._make_row(it))
                self._lay.addWidget(self._sec_label(
                    "точные совпадения · %d" % len(exact), "secDim"))
                for it in exact:
                    self._lay.addWidget(self._make_row(it))
            else:
                for it in censored:
                    self._lay.addWidget(self._make_row(it))
        else:
            ph = QLabel("ничего не найдено — можно добавить пропущенный момент вручную")
            ph.setObjectName("dlgSub")
            ph.setAlignment(Qt.AlignCenter)
            ph.setWordWrap(True)
            ph.setContentsMargins(0, 28, 0, 28)
            self._empty_lbl = ph
            self._lay.addWidget(ph)
        self._lay.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        self._scroll = scroll
        self._timeline = None
        dur = float(self.report.get("duration") or 0.0)
        if self._checks and dur > 0:       # таймлайн позиций мата — над списком (вид «дорожка»)
            items = [it for _, it in self._checks]
            cbs = [cb for cb, _ in self._checks]
            tl = _TimelineBar(dur, items, cbs)
            tl.picked.connect(self._on_pick_mark)
            for cb in cbs:
                cb.toggled.connect(tl.update)   # снял галочку — метка гаснет
            self._timeline = tl
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 6, 0, 2)
            wl.setSpacing(3)
            cap = QLabel("позиции мата на дорожке · клик ведёт к слову")
            cap.setObjectName("dlgSub")
            wl.addWidget(cap)
            wl.addWidget(tl)
            trow = QHBoxLayout()
            t0 = QLabel("0:00")
            t0.setObjectName("dlgSub")
            tN = QLabel(_fmt(dur))
            tN.setObjectName("dlgSub")
            trow.addWidget(t0)
            trow.addStretch(1)
            trow.addWidget(tN)
            wl.addLayout(trow)
            root.insertWidget(root.indexOf(scroll), wrap)

        link = QLabel('<a href="#" style="color:%s; text-decoration:none;">+ добавить вручную</a>'
                      % theme.ACCENT)
        link.setObjectName("aboutLink")
        link.setTextFormat(Qt.RichText)
        link.setCursor(Qt.PointingHandCursor)
        link.linkActivated.connect(lambda *_: self._add_manual())
        root.addWidget(link)

        self.add_buttons(ok_text="применить", cancel_text="назад")

    @staticmethod
    def _sec_label(text, style):
        lbl = QLabel(text)
        lbl.setObjectName(style)
        lbl.setContentsMargins(2, 10, 2, 4)
        return lbl

    def _check_rows(self, href):
        on = href == "#on"
        for cb, _ in self._checks:
            cb.setChecked(on)

    def _context_html(self, it):
        """«…и тут он █████ сказал…» — соседние слова из кэша транскрипта."""
        reason = it.get("reason") or ""
        if reason == "manual" or not self._words:
            return None
        track = int(it.get("track", 0) or 0)
        words = self._words.get(track)
        if not words:
            return None
        start, end = float(it.get("start", 0)), float(it.get("end", 0))
        # окно ±10 c по отсортированным таймкодам: целью может стать только слово
        # в пределах 1 c (best_d в _phrase_context); полный проход по 2-часовому
        # транскрипту на каждую строку заметно тормозил открытие диалога
        starts = self._starts[track]
        lo, hi = bisect_left(starts, start - 10.0), bisect_right(starts, start + 10.0)
        ctx = _phrase_context(words[max(0, lo - 4):hi + 4], start, end)
        if not ctx or not (ctx[0] or ctx[2]):
            return None
        before, target, after = ctx
        if "fuzzy" in reason:                # сомнительное — показать как есть, судить юзеру
            mid = '<span style="color:%s">%s</span>' % (theme.AMBER, target)
        else:                                # точный мат — вымарка, читать его не нужно
            mid = '<span style="color:%s">%s</span>' % (theme.ACCENT,
                                                        "█" * min(len(target), 6))
        parts = ["…"] + before + [mid] + after + ["…"]
        return " ".join(parts)

    def _make_row(self, it):
        row = QWidget()
        row.setObjectName("rcptRow")
        v = QVBoxLayout(row)
        v.setContentsMargins(2, 6, 2, 6)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        cb = QCheckBox()
        cb.setObjectName("trackCheck")
        cb.setChecked(True)
        self._checks.append((cb, it))
        w = QLabel(it.get("word", ""))
        w.setObjectName("rcptName")
        reason = it.get("reason") or ""
        fuzzy = "fuzzy" in reason
        badge = QLabel("вручную" if reason == "manual" else ("похоже?" if fuzzy else "корень"))
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
        play.clicked.connect(lambda _=False, x=it, b=play: self._play(x, b))
        h.addWidget(cb)
        h.addWidget(w, 1)
        h.addWidget(badge)
        h.addWidget(tc)
        h.addWidget(play)
        v.addLayout(h)
        ctx = self._context_html(it)
        if ctx:
            cl = QLabel(ctx)
            cl.setObjectName("ctxLine")
            cl.setTextFormat(Qt.RichText)
            cl.setContentsMargins(26, 0, 0, 0)   # под чекбокс — на одной оси со словом
            v.addWidget(cl)
        self._rows.append(row)             # индекс совпадает с _checks и метками таймлайна
        return row

    def _on_pick_mark(self, i):
        """Клик по метке таймлайна → прокрутить к строке и кратко подсветить её."""
        if not (0 <= i < len(self._rows)):
            return
        row = self._rows[i]
        try:
            self._scroll.ensureWidgetVisible(row, 0, 40)
            row.setStyleSheet("#rcptRow{background:%s; border-radius:6px;}" % theme.SURFACE)
        except RuntimeError:
            return
        QTimer.singleShot(700, lambda r=row: self._unflash(r))

    def _unflash(self, row):
        try:
            row.setStyleSheet("")
        except RuntimeError:
            pass                           # строка/диалог уже удалены

    def _add_manual(self):
        dlg = ManualAddDialog(self, max_dur=self.report.get("duration"))
        ok = dlg.exec() and dlg.result_range
        dlg.deleteLater()              # диалог парентован — без этого копится до закрытия окна
        if not ok:
            return
        s, e = dlg.result_range
        it = {"word": "(вручную)", "start": s, "end": e, "mute_from": s, "mute_to": e,
              "reason": "manual", "tracks": list(self.tracks)}
        if self._empty_lbl is not None:
            self._empty_lbl.hide()
        row = self._make_row(it)
        self._lay.insertWidget(self._lay.count() - 1, row)
        if self._timeline is not None:     # отметить ручную зону и на таймлайне
            self._timeline.add_mark(it, self._checks[-1][0])

    def _reset_play_btn(self):
        if self._play_btn is not None:
            try:
                self._play_btn.setIcon(_play_icon(theme.ACCENT))
            except RuntimeError:
                pass                           # кнопка могла быть удалена
            self._play_btn = None

    def _on_sfx_state(self):
        if self._sfx is not None and not self._sfx.isPlaying():
            self._reset_play_btn()             # фрагмент дозвучал — вернуть ▶

    def _on_ab(self, btn):
        """Переключатель «слушать до/после»: задаёт, что играет ▶ у строк."""
        self._ab_mode = btn.property("ab") or "до"
        if self._sfx is not None:              # сменили режим — оборвать текущий фрагмент
            self._sfx.stop()
        self._reset_play_btn()
        self._play_n += 1                      # инвалидировать летящие вырезы прошлого режима

    def _play(self, it, btn=None):
        try:
            from PySide6.QtMultimedia import QSoundEffect
            import tempfile
            if btn is not None and btn is self._play_btn:
                if self._sfx is not None:      # повторный клик по играющей строке — стоп
                    self._sfx.stop()
                self._reset_play_btn()
                return
            if self._sfx is None:
                self._sfx = QSoundEffect()     # один объект на диалог (не плодим на каждый клик)
                self._sfx.playingChanged.connect(self._on_sfx_state)
            self._sfx.stop()                   # освободить прошлый фрагмент
            self._reset_play_btn()
            if self._tmp is None:
                self._tmp = tempfile.mkdtemp()
            self._play_n += 1                  # своё имя файла — обходим кэш источника QSoundEffect
            wav = str(Path(self._tmp) / ("frag%d.wav" % self._play_n))
            s = max(0.0, float(it.get("start", 0)) - 2.0)   # ±2 с контекста вокруг слова
            e = float(it.get("end", 0)) + 2.0
            zone = None
            if self._ab_mode == "после":           # «после»: глушим зону строки, как в выходе
                zone = (float(it.get("mute_from", it.get("start", 0.0))),
                        float(it.get("mute_to", it.get("end", 0.0))))
            task = _FragTask(self._play_n, str(self.src), s, e, wav, zone=zone, mode=self.mode)
            task.signals.done.connect(lambda n, w, b=btn: self._on_frag_ready(n, w, b))
            self._tasks.append(task)
            QThreadPool.globalInstance().start(task)
        except Exception:
            pass   # без аудио-бэкенда просто молчим

    def _on_frag_ready(self, n, wav, btn=None):
        if self._tmp is None or n != self._play_n or not wav or self._sfx is None:
            return                             # диалог закрыт / другой фрагмент / ошибка вырезания
        try:
            self._sfx.setSource(QUrl.fromLocalFile(wav))
            self._sfx.setVolume(0.9)
            self._sfx.play()
            if btn is not None:                # состояние «играет»: ▶ → ■
                btn.setIcon(_stopsq_icon(theme.ACCENT))
                self._play_btn = btn
        except Exception:
            pass

    def done(self, r):
        """Закрытие диалога (ok/отмена/✕): остановить звук и убрать временные фрагменты."""
        if self._busy:
            return                             # идёт «применить» — закрывать нельзя
        self._play_n += 1                      # инвалидировать летящие _FragTask: их wav
        if self._sfx is not None:              # сейчас удалится — звучать после закрытия нечему
            self._sfx.stop()
        tmp, self._tmp = self._tmp, None       # снять ссылку ДО rmtree: летящий _on_frag_ready
        if tmp:                                # увидит None и не тронет удаляемый файл.
            import shutil                      # фоновый _FragTask мог не дописать wav, но
            shutil.rmtree(tmp, ignore_errors=True)   # гонка безвредна (мелкий temp) — глушить
            #                                          её глобальным kill нельзя: бьёт чужой ffprobe
        super().done(r)

    def accept(self):
        if self._busy:
            return
        zbt = {}
        kept = []
        for cb, it in self._checks:
            if not cb.isChecked():
                continue
            kept.append(it)
            mf = float(it.get("mute_from", it.get("start", 0.0)))
            mt = float(it.get("mute_to", it.get("end", 0.0)))
            tracks = it.get("tracks") or [int(it.get("track", 0) or 0)]
            for t in tracks:
                zbt.setdefault(int(t), []).append((mf, mt))
        self._busy = True
        self._kept = kept
        self.ok_btn.setEnabled(False)
        self.ok_btn.setText("применяю…")
        if self.cancel_btn is not None:
            self.cancel_btn.setEnabled(False)
        task = _RecensorTask(str(self.src), str(self.dst), zbt, self.mode)
        task.signals.done.connect(self._on_recensor_done)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _on_recensor_done(self, err):
        self._busy = False
        if err:
            self.ok_btn.setEnabled(True)
            self.ok_btn.setText("применить")
            if self.cancel_btn is not None:
                self.cancel_btn.setEnabled(True)
            QMessageBox.warning(self, "Censr", "Не удалось применить:\n%s" % err)
            return
        kept = self._kept
        self.applied_count = len(kept)
        self.report["censored"] = kept           # повторное «проверить» увидит правки
        self.report["censored_count"] = len(kept)
        self.report["flagged_words"] = len(kept)
        self.report["mode"] = self.mode
        try:
            Path(self.report_path).write_text(
                json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass   # отчёт не критичен — сам файл уже перерисован
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
        self._fatal = False
        self._out_dirs = []
        self._errors = []
        self._done_files = []
        self._weights = []                 # длительности файлов — веса общего прогресса
        self._collect_tasks = []           # живые _CollectTask (не дать GC убить signals)

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

        # зерно пустого экрана — на всё окно, под оверлеями
        self.grain_ov = GrainOverlay(central)

        # оверлей «отпусти здесь» при перетаскивании
        self.drop_overlay = QFrame(central)
        self.drop_overlay.setObjectName("dropOverlay")
        dol = QVBoxLayout(self.drop_overlay)
        dh = QLabel("отпусти файлы — добавлю в очередь")
        dh.setObjectName("dropHint")
        dh.setAlignment(Qt.AlignCenter)
        dol.addWidget(dh)
        self.drop_overlay.hide()

        # тост для некритичных сообщений (вместо модальных окон)
        self._toast_lbl = QLabel("", central)
        self._toast_lbl.setObjectName("toast")
        self._toast_lbl.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast_lbl.hide)

        # горячие клавиши (подсказки — в тултипах кнопок)
        QShortcut(QKeySequence.Open, self, activated=self._kb_open)
        QShortcut(QKeySequence(Qt.Key_Return), self, activated=self._kb_start)
        QShortcut(QKeySequence(Qt.Key_Enter), self, activated=self._kb_start)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._kb_stop)

        # тёплый старт: модель уже на диске — грузим её в фоне, пока пользователь
        # перетаскивает файлы; первый «НАЧАТЬ» стартует без паузы на загрузку
        if self.settings.model_dir or default_model_dir():
            QThreadPool.globalInstance().start(_PreloadTask(self.settings.model_dir))

    def _kb_open(self):
        if self.worker is None:
            self._pick_files()

    def _kb_start(self):
        if self.worker is None and self.rows and self.bottom.isVisible() \
                and self.start_btn.isEnabled() and self.stack.currentIndex() == 1:
            self._start_stop()

    def _kb_stop(self):
        if self.worker is not None and self.start_btn.isEnabled():
            self._start_stop()                 # во время работы кнопка = «остановить»

    def _toast(self, text, ms=2800):
        self._toast_lbl.setText(text)
        self._toast_lbl.adjustSize()
        c = self.centralWidget()
        self._toast_lbl.move((c.width() - self._toast_lbl.width()) // 2,
                             c.height() - self._toast_lbl.height() - 96)
        self._toast_lbl.raise_()
        self._toast_lbl.show()
        self._toast_timer.start(ms)

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
        self.mark.hide()                   # пустой экран — без дубля имени в шапке
        self._top_btns = {}
        for text, slot in (("словарь", self._open_dicts), ("настройки", self._open_settings),
                           ("о программе", self._about)):
            b = QPushButton(text)
            b.setObjectName("topLink")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            top.addWidget(b, 0, Qt.AlignVCenter)
            self._top_btns[text] = b
        self.header.raise_()

    # — три экрана
    def _build_stack(self):
        self.stack = QStackedLayout()

        # 0 · главный (30f·F): эквалайзер-wordmark + тихая ссылка + статус среды
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(8, 0, 8, 0)
        ev.addStretch(3)
        self.empty_mark = EqWordmark("censr", height=300, font_k=0.62, interactive=True)
        self.empty_mark.clicked.connect(self._pick_files)
        ev.addWidget(self.empty_mark)
        GRID_W = 340                               # единая сетка: подсказка = строка форматов

        def _centered(wdg):                        # фикс-ширина + центрирование через стретчи —
            r = QHBoxLayout()                      # перенос текста тогда считается корректно
            r.addStretch(1)
            r.addWidget(wdg)
            r.addStretch(1)
            return r

        ev.addStretch(5)                           # все надписи — единым блоком ниже, у низа
        hint = QLabel("нажми на логотип или перетащи на него файлы")
        hint.setAlignment(Qt.AlignHCenter)         # без кнопки: цель — сам логотип
        hint.setWordWrap(True)
        hint.setFixedWidth(GRID_W)
        hint.setStyleSheet("color: %s; font-size: 13px;" % theme.ACCENT)
        ev.addLayout(_centered(hint))
        ev.addSpacing(6)                           # три нижние строки — плотно друг к другу
        extra_n = max(len(AUDIO_EXT) - 5, 0)
        fmts = QLabel("mp3 · mp4 · mkv · flac · wav · +%d" % extra_n)
        fmts.setObjectName("statusLine")
        fmts.setAlignment(Qt.AlignHCenter)
        fmts.setWordWrap(True)
        fmts.setFixedWidth(GRID_W)
        ev.addLayout(_centered(fmts))
        ev.addSpacing(6)
        self._env_status = QLabel("")              # модель/ffmpeg/версия (заполняется после старта)
        self._env_status.setObjectName("statusLine")
        self._env_status.setAlignment(Qt.AlignHCenter)
        ev.addWidget(self._env_status)
        ev.addSpacing(16)                          # такой же отступ снизу
        self.stack.addWidget(empty)
        QTimer.singleShot(60, self._fill_env_status)   # не задерживать первый кадр

        # 1 · очередь (D3): цифра-счётчик + тонкие строки + приглашение добавить
        listing = QWidget()
        lv = QVBoxLayout(listing)
        lv.setContentsMargins(0, HEADER_H - 8, 0, 0)
        lv.setSpacing(8)
        self.queue_hero = EqWordmark("0", height=128, font_k=0.66, offs_k=1.1)
        lv.addWidget(self.queue_hero)
        self.queue_sub = QLabel("")
        self.queue_sub.setObjectName("heroSub")
        self.queue_sub.setAlignment(Qt.AlignHCenter)
        self.queue_sub.setTextFormat(Qt.RichText)
        self.queue_sub.setToolTip("Куда сохраняются результаты — изменить в настройках")
        self.queue_sub.linkActivated.connect(lambda *_: self._open_settings())
        lv.addWidget(self.queue_sub)
        lv.addSpacing(8)
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
        self._queue_scroll = scroll        # высоту задаём по контенту (_resize_queue_list)
        self._queue_inner = inner
        lv.addWidget(scroll)
        lv.addStretch(1)                   # список растёт вниз в свободное место под ним
        self.stack.addWidget(listing)

        # 2 · обработка: волна-индикатор + сводка + список файлов со статусами
        proc = QWidget()
        pv = QVBoxLayout(proc)
        pv.setContentsMargins(8, HEADER_H + 4, 8, 0)   # контент выше — почти под шапку
        self.proc_wave = Wave(height=100)
        pv.addWidget(self.proc_wave)
        pv.addSpacing(14)
        self.pv_title = QLabel("Обработка")
        self.pv_title.setObjectName("pvTitle")
        self.pv_title.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_title)
        pv.addSpacing(4)
        self.pv_sum = QLabel("")
        self.pv_sum.setObjectName("procSum")
        self.pv_sum.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_sum)
        pv.addSpacing(5)
        self.pv_pct = QLabel("")                   # «общий ход N%» — под «прошло», тем же шрифтом
        self.pv_pct.setObjectName("procSum")
        self.pv_pct.setAlignment(Qt.AlignHCenter)
        pv.addWidget(self.pv_pct)
        pv.addSpacing(12)
        self._proc_scroll = QScrollArea()
        self._proc_scroll.setWidgetResizable(True)
        self._proc_scroll.setFrameShape(QScrollArea.NoFrame)
        self._proc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._proc_scroll.setStyleSheet("background: transparent; border: none;")
        self._proc_scroll.viewport().setStyleSheet("background: transparent;")
        pin = QWidget()
        pin.setStyleSheet("background: transparent;")
        pin_h = QHBoxLayout(pin)
        pin_h.setContentsMargins(0, 0, 0, 0)
        host = QWidget()                            # список обработки — во всю ширину (как волна)
        host.setStyleSheet("background: transparent;")
        self._proc_lay = QVBoxLayout(host)
        self._proc_lay.setContentsMargins(0, 0, 0, 0)
        self._proc_lay.setSpacing(0)
        pin_h.addWidget(host)
        self._proc_scroll.setWidget(pin)
        pv.addWidget(self._proc_scroll)
        self._proc_rows = []               # (виджет, левая метка, правая метка, бар-заливка)
        pv.addStretch(1)
        self.stack.addWidget(proc)

        # 3 · готово (D3): цифра-итог в стиле главного экрана + тонкие строки
        donep = QWidget()
        dvp = QVBoxLayout(donep)
        dvp.setContentsMargins(0, HEADER_H - 8, 0, 18)   # очистить шапку-оверлей; центрируют растяжки в cl
        center = QHBoxLayout()
        center.addStretch(1)
        col = QWidget()
        col.setFixedWidth(500)                      # колонка (шире — под кнопку «проверить»)
        col.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addStretch(1)                            # центрируем колонку по вертикали

        self.done_hero = EqWordmark("0", height=150, font_k=0.66, offs_k=1.2)
        cl.addWidget(self.done_hero)
        self.done_sub = QLabel("")                  # «слова заглушено · N файлов · 38 с · ×54»
        self.done_sub.setObjectName("heroSub")
        self.done_sub.setAlignment(Qt.AlignHCenter)
        self.done_sub.setTextFormat(Qt.RichText)
        cl.addWidget(self.done_sub)
        cl.addSpacing(18)                            # «сохранено …» переехало в нижний бар (слева)

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
        cico.setPixmap(_stop_icon(theme.AMBER, 32))
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
        cl.addStretch(1)                            # нижняя растяжка — колонка по центру

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
        self.done_saved = QLabel("")                # «сохранено … ↗» — слева, на экране «Готово»
        self.done_saved.setObjectName("footerNote")
        self.done_saved.setTextFormat(Qt.RichText)
        self.done_saved.setToolTip("Открыть папку с результатами")
        self.done_saved.linkActivated.connect(lambda *_: self._open_output())
        self.done_saved.hide()
        foot.addWidget(self.done_saved)
        self.note = QLabel("")
        self.note.setObjectName("footerNote")
        foot.addWidget(self.note)
        foot.addStretch(1)
        self._drag_hint = QLabel('перетащи ещё файлы в окно — или <a href="#pick" '
                                 'style="color:%s; text-decoration:none;">выбери вручную</a>'
                                 % theme.ACCENT)
        self._drag_hint.setObjectName("statusLine")
        self._drag_hint.setTextFormat(Qt.RichText)
        self._drag_hint.linkActivated.connect(lambda *_: self._pick_files())
        foot.addWidget(self._drag_hint)            # по центру между «Enter — начать» и «очистить всё»
        foot.addStretch(1)
        self.add_link = QPushButton("+ добавить файлы")
        self.add_link.setObjectName("linkAdd")
        self.add_link.setCursor(Qt.PointingHandCursor)
        self.add_link.setToolTip("Ctrl+O")
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
        self.start_btn.setToolTip("Enter — начать · Esc — остановить")
        self.start_btn.clicked.connect(self._start_stop)
        act.addWidget(self.start_btn, 1)
        bb.addLayout(act)
        self.bottom.hide()
        self.col.addWidget(self.bottom)

    def _fill_env_status(self):
        """Строка статуса на пустом экране: модель · ffmpeg · версия.
        Закрывает молчание первого запуска (модель ~250 МБ качается без индикации)."""
        try:
            from .audio import ffmpeg_available
            parts = []
            if self.settings.model_dir or default_model_dir():
                parts.append("модель на месте")
            else:
                parts.append("модель скачается при первом запуске (~250 МБ)")
            if ffmpeg_available():
                parts.append("ffmpeg найден")
            else:
                parts.append('<span style="color:%s">ffmpeg не найден — установи и добавь в PATH</span>'
                             % theme.RED)
            parts.append("v%s" % __version__)
            self._env_status.setTextFormat(Qt.RichText)
            self._env_status.setText(" · ".join(parts))
        except Exception:
            self._env_status.setText("v%s" % __version__)

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
        if hasattr(self, "grain_ov"):
            c = self.centralWidget()
            self.grain_ov.setGeometry(0, 0, c.width(), c.height())
        if hasattr(self, "drop_overlay"):
            c = self.centralWidget()
            self.drop_overlay.setGeometry(14, 14, c.width() - 28, c.height() - 28)

    # — drag&drop
    def dragEnterEvent(self, e):
        if self.worker is not None:        # во время обработки очередь заморожена
            e.ignore()
            self._toast("очередь заморожена до конца обработки")
            return
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop_overlay.raise_()     # состояние «отпусти здесь»
            self.drop_overlay.show()

    def dragLeaveEvent(self, e):
        self.drop_overlay.hide()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self.drop_overlay.hide()
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not any(p.is_dir() for p in paths):
            self._add_files(_collect(paths))     # только файлы — мгновенно, без потока
            return
        task = _CollectTask(paths)               # папки обходим вне main thread
        task.signals.done.connect(lambda files, t=task: self._on_collected(t, files))
        self._collect_tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _on_collected(self, task, files):
        if task in self._collect_tasks:
            self._collect_tasks.remove(task)
        self._add_files([Path(f) for f in files])

    # — файлы
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Аудио и видео", "",
            "Аудио и видео (" + " ".join("*%s" % e for e in sorted(AUDIO_EXT)) + ");;Все файлы (*)")
        self._add_files([Path(f) for f in files])

    def _add_files(self, files):
        if self.worker is not None:
            return

        def norm(p):
            try:
                return os.path.normcase(str(Path(p).resolve()))
            except OSError:
                return os.path.normcase(str(p))

        known = {norm(r.path) for r in self.rows}
        skipped = 0
        for f in files:
            f = Path(f)
            key = norm(f)
            if key in known:               # дубликат — и в очереди, и внутри одного вызова
                skipped += 1
                continue
            known.add(key)
            row = FileRow(f)
            row.removed.connect(self._remove_row)
            row.dur_ready.connect(self._update_note)
            row.tracks_ready.connect(self._refresh_gutters)
            row.toggled.connect(self._resize_queue_list)
            self.rows.append(row)
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self._sync()
        if skipped:
            self._toast("%d %s — уже в очереди, пропущено"
                        % (skipped, _plural(skipped, "файл", "файла", "файлов")))

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
        self.done_saved.hide()             # ссылка «сохранено» — только на «Готово»
        self.mark.setVisible(has)          # на пустом экране имя несёт сам wordmark-герой
        self.add_link.setVisible(False)    # в очереди роль «добавить» несёт приглашение в футере
        self._drag_hint.setVisible(has)    # приглашение видно только в очереди
        for i, r in enumerate(self.rows, 1):
            r.set_number(i)
        if self.worker is None:
            # вернуть кнопке роль «старт»: после экрана «Готово» она подключена к _reset,
            # и добавление файлов оттуда без переподключения стирало бы новую очередь
            try:
                self.start_btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.start_btn.clicked.connect(self._start_stop)
            self.start_btn.setText("НАЧАТЬ ОБРАБОТКУ")
        self._refresh_gutters()
        self._update_note()

    def _dest_name(self) -> str:
        if self.settings.output_dir:
            return Path(self.settings.output_dir).name or self.settings.output_dir
        return "рядом с исходниками"

    def _update_note(self):
        if not self.rows:
            self.note.setText("")
            return
        n = len(self.rows)
        total = sum(r.dur_sec for r in self.rows)
        self.queue_hero.set_text(str(n))
        count_line = "%s в очереди" % _plural(n, "файл", "файла", "файлов")
        dur = ("длительность %s · " % _fmt(total)) if total else ""
        # счётчик — отдельной строкой по центру под цифрой; ниже: подписанная
        # длительность и папка вывода (клик ведёт в настройки)
        self.queue_sub.setText('<div>%s</div>'
                               '<div style="margin-top:7px">%sвыход: <a href="#out" '
                               'style="color:%s; text-decoration:underline;">%s</a></div>'
                               % (count_line, dur, theme.ACCENT, self._dest_name()))
        self.note.setText("Enter — начать")
        self._resize_queue_list()

    def _resize_queue_list(self):
        """Высота списка очереди — по числу строк (потолок 360), чтобы при немногих
        файлах не зияла пустота; sizeHint учитывает раскрытые мультитрек-строки."""
        sc = getattr(self, "_queue_scroll", None)
        inner = getattr(self, "_queue_inner", None)
        if sc is None or inner is None:
            return
        sc.setFixedHeight(min(360, max(inner.sizeHint().height(), 1)))

    # — действия шапки
    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_to(self.settings)
            self.settings.save()
            self._update_note()                # «→ куда сохранять» в подвале очереди
        dlg.deleteLater()                      # диалоги парентованы к окну — без deleteLater
        #                                        каждый показ копился бы в памяти до выхода

    def _open_dicts(self):
        dlg = DictionariesDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_to(self.settings)
            self.settings.save()
        dlg.deleteLater()

    def _about(self):
        dlg = AboutDialog(self)
        dlg.exec()
        dlg.deleteLater()

    def _open_output(self):
        opened = 0
        for d in self._out_dirs:
            if d and os.path.isdir(d):
                if sys.platform == "win32":
                    os.startfile(d)  # noqa: S606
                else:                  # os.startfile есть только на Windows
                    QDesktopServices.openUrl(QUrl.fromLocalFile(d))
                opened += 1
                if opened >= 4:        # не плодить десятки окон проводника
                    break

    # — обработка
    def _start_stop(self):
        if self.worker is not None:
            if time.perf_counter() - getattr(self, "_t_start", 0.0) < 0.25:
                return                         # отбить дабл-клик по «НАЧАТЬ», но не осознанный стоп
            self._cancelled = True
            self.worker.stop()
            self.start_btn.setEnabled(False)
            self.start_btn.setText("останавливаю…")
            return
        if not self.rows:
            QMessageBox.information(self, "Censr", "Добавь файлы для обработки.")
            return
        self._cancelled = False
        self._fatal = False
        self.stack.setCurrentIndex(2)
        native.keep_awake(True)                    # не дать ПК уснуть во время обработки
        tip = " · можно свернуть — прогресс в панели задач" if native.IS_WIN else ""
        self.note.setText("Esc — остановить" + tip)    # на экране обработки Esc = стоп
        self.clear_btn.hide()
        self.clear_btn.setEnabled(False)           # на время обработки очередь заморожена
        self._drag_hint.hide()                     # во время обработки файлы не добавить
        self.add_link.hide()
        self.add_link.setEnabled(False)
        self.done_saved.hide()                     # ссылка «сохранено» гаснет на обработке
        self.open_btn.hide()
        for k in ("словарь", "настройки"):          # настройки снапшотятся — менять их сейчас нечестно
            self._top_btns[k].setEnabled(False)
        self._blink.stop()                         # каретка не отвлекает от волны прогресса
        self._caret_on = True
        self._render_mark()
        self._style_start(stop_mode=True)          # «стоп» — янтарный, не зелёный
        self.start_btn.setText("ОСТАНОВИТЬ")
        self.pv_title.setText("Обработка")
        self.pv_sum.setText("готовлюсь…")
        self.pv_pct.setText("общий ход 0%")
        self.proc_wave.randomize()                 # своя форма волны на каждый запуск
        self.proc_wave.set_progress(0.0)
        self._total = len(self.rows)
        self._completed = 0
        self._found_total = 0
        self._errors = []
        self._done_files = []
        for r in self.rows:
            r.state = "queued"                 # стейл «error» прошлого прогона не должен попасть в счёт
        self._proc_build()                         # список файлов со статусами
        self._weights = [max(r.dur_sec, 1.0) for r in self.rows]   # общий прогресс — по длительности
        self._t_start = time.perf_counter()        # отсчёт времени с нажатия кнопки
        self.done_scroll.hide()                    # очистить список готовых от прошлого прогона
        self._clear_done_list()
        snap = copy.deepcopy(self.settings)        # снапшот: правки настроек не влияют на текущий прогон
        if snap.output_dir:
            self._out_dirs = [snap.output_dir]
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
        self.worker = Worker([(str(r.path), r.selected_tracks()) for r in self.rows], snap)
        self.worker.file_started.connect(self._on_started)
        self.worker.file_progress.connect(self._on_progress)
        self.worker.file_done.connect(self._on_done)
        self.worker.file_error.connect(self._on_error)
        self.worker.fatal.connect(self._on_fatal)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _style_start(self, stop_mode: bool):
        """Кнопка старт/стоп: зелёная primary ↔ янтарный контур (противоположные
        действия не должны выглядеть одинаково)."""
        self.start_btn.setObjectName("stopBtn" if stop_mode else "primary")
        st = self.start_btn.style()
        st.unpolish(self.start_btn)
        st.polish(self.start_btn)

    def _on_fatal(self, msg):
        self._fatal = True
        QMessageBox.critical(self, "Censr — ошибка запуска", msg)

    def _overall(self, row, pct):
        """Общий прогресс 0..100: файлы взвешены по длительности (а не поровну)."""
        w = self._weights
        if not w or not 0 <= row < len(w):
            return int((self._completed * 100 + pct) / max(self._total, 1))
        total = sum(w)
        return int((sum(w[:row]) + w[row] * pct / 100.0) * 100.0 / total)

    # — список файлов на экране обработки
    def _proc_build(self):
        while self._proc_lay.count():
            it = self._proc_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._proc_rows = []
        for _ in self.rows:
            roww = QWidget()
            v = QVBoxLayout(roww)
            v.setContentsMargins(0, 5, 0, 5)
            v.setSpacing(4)
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            dot = StatusDot()
            left = QLabel()
            left.setObjectName("procNameDim")
            left.setTextFormat(Qt.RichText)
            right = QLabel()
            right.setObjectName("procR")
            right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(dot)
            h.addWidget(left, 1)
            h.addWidget(right)
            v.addLayout(h)
            bar = QFrame()
            bar.setFixedHeight(2)
            bar.setStyleSheet("background:%s; border:none; border-radius:1px;" % theme.HAIR)
            fill = QFrame(bar)
            fill.setGeometry(0, 0, 0, 2)
            fill.setStyleSheet("background:%s; border:none; border-radius:1px;" % theme.ACCENT)
            bar._pct = 0                            # заливка — доля ширины bar (bar тянется на строку)
            bar.resizeEvent = lambda e, b=bar, fl=fill: fl.setFixedWidth(int(b.width() * b._pct / 100))
            bar.hide()
            v.addWidget(bar)
            self._proc_lay.addWidget(roww)
            self._proc_rows.append((roww, dot, left, right, bar, fill))
        n = len(self.rows)
        # +10, а не +4: у активной строки снизу появляется полоса прогресса
        # (2px + 4px отступа) — без резерва контент на пару px выше вьюпорта и
        # вылезал паразитный вертикальный скролл при одном-нескольких файлах
        self._proc_scroll.setFixedHeight(min(340, max(n, 1) * 29 + 10))
        for i in range(n):
            self._proc_set(i, "wait")

    def _proc_set(self, i, state, pct=0, stage="", right=""):
        if not 0 <= i < len(self._proc_rows):
            return
        roww, dot, left, rightl, bar, fill = self._proc_rows[i]
        name = left.fontMetrics().elidedText(self.rows[i].path.name, Qt.ElideMiddle, 250)
        a, d, f = theme.ACCENT, theme.DIM, theme.FAINT
        dot.set_state(state if state in ("run", "done", "error") else "wait")
        if state == "run":
            extra = ' <span style="color:%s">· %s</span>' % (d, stage) if stage else ""
            left.setText('<span style="color:%s">%s</span>%s' % (theme.TEXT, name, extra))
            rightl.setText('<span style="color:%s">%d%%</span>' % (a, pct))
            bar.show()
            bar._pct = pct                          # ширину bar задаёт layout (вся строка)
            fill.setFixedWidth(int(bar.width() * pct / 100))
            self._proc_scroll.ensureWidgetVisible(roww, 0, 24)
        elif state == "done":
            left.setText('<span style="color:%s">%s</span>' % (d, name))
            rightl.setText('<span style="color:%s">✓</span> <span style="color:%s">%s</span>'
                           % (theme.GREEN, d, right))
            bar.hide()
        elif state == "error":
            left.setText('<span style="color:%s">%s</span>' % (d, name))
            rightl.setText('<span style="color:%s">ошибка</span>' % theme.RED)
            bar.hide()
        else:
            left.setText('<span style="color:%s">%s</span>' % (f, name))
            rightl.setText('<span style="color:%s">ждёт</span>' % f)
            bar.hide()

    def _update_sum(self, overall, found_live=0):
        elapsed = time.perf_counter() - getattr(self, "_t_start", time.perf_counter())
        self.pv_pct.setText("общий ход %d%%" % overall)
        parts = ["прошло %s" % _fmt(elapsed)]      # время идёт всегда, с первой секунды
        if overall >= 3 and elapsed > 5:           # ETA — только когда оценка устойчива
            parts.append("осталось ≈ %s" % _fmt_eta(elapsed * (100 - overall) / max(overall, 1)))
        parts.append("найдено мата: %d" % (self._found_total + found_live))
        self.pv_sum.setText(" · ".join(parts))

    def _on_started(self, row):
        self._proc_set(row, "run", 0)
        self._update_sum(self._overall(row, 0))
        self.proc_wave.set_progress(self._overall(row, 0) / 100.0)

    def _on_progress(self, row, pct, found, stage=""):
        overall = self._overall(row, pct)
        self.proc_wave.set_progress(overall / 100.0)
        self._proc_set(row, "run", pct, stage)
        self._update_sum(overall, found)
        if self.taskbar:
            self.taskbar.set(overall)

    def _on_done(self, row, count, elapsed, dst):
        self._completed += 1
        self._found_total += count
        self._proc_set(row, "done",
                       right="%d %s · %s" % (count, _plural(count, "слово", "слова", "слов"),
                                             _fmt(elapsed)))
        self._update_sum(self._overall(row, 100))
        dur = self.rows[row].dur_sec if 0 <= row < len(self.rows) else 0.0
        self._done_files.append({"name": Path(dst).name, "dst": dst,
                                 "count": count, "dur": dur})
        if self.taskbar:
            self.taskbar.set(self._overall(row, 100))

    def _done_row(self, info):
        row = QWidget()
        row.setObjectName("rcptRow")
        row.setFixedHeight(34)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        ok = QLabel()
        ok.setObjectName("okMark")
        ok.setFixedWidth(14)
        ok.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        ok.setPixmap(_check_icon(theme.GREEN, 13))
        name = info["name"]
        nm = QLabel()
        nm.setObjectName("rcptName")
        # бюджет имени: 500 (колонка) − 14 (✓) − 44 (счёт) − 26·2 (кнопки)
        # − 84 (проверить) − 5·8 (зазоры) = 266; чуть меньше — без переполнения
        nm.setText(nm.fontMetrics().elidedText(name, Qt.ElideRight, 250))
        nm.setToolTip(name)
        st = QLabel(str(info["count"]))
        st.setObjectName("rcptCount" if info["count"] else "rcptCountZero")
        st.setFixedWidth(44)
        st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        st.setToolTip("Найдено мата · длительность %s" % _fmt(info["dur"]))
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
        has_report = Path(info["dst"]).with_suffix(".report.json").exists()
        # 0 найдено или отчёт выключен в настройках — кнопка приглушена
        review.setObjectName("reviewBtn" if info["count"] and has_report else "reviewBtnDim")
        review.setFixedWidth(84)
        if has_report:
            review.setCursor(Qt.PointingHandCursor)
            review.setToolTip("Проверить и поправить найденное" if info["count"]
                              else "Ничего не найдено — можно добавить пропущенное вручную")
            review.clicked.connect(lambda _=False, i=info: self._open_review(i))
        else:
            review.setEnabled(False)
            review.setToolTip("Файл отчёта выключен в настройках — включи его, "
                              "чтобы проверять и править найденное")
        h.addWidget(ok)
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
                from .audio import NO_WINDOW
                subprocess.Popen(["explorer", "/select,", p], creationflags=NO_WINDOW)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def _clear_done_list(self):
        while self.done_list.count() > 1:          # очистить, кроме хвостовой растяжки
            it = self.done_list.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _fill_done_list(self):
        self._clear_done_list()
        for info in self._done_files:
            self.done_list.insertWidget(self.done_list.count() - 1, self._done_row(info))
        # высота списка = под содержимое (все строки видны), дальше — скролл с потолка 360
        n = len(self._done_files)
        if n:
            self.done_scroll.setFixedHeight(min(360, n * 34 + 2))

    def _open_review(self, info):
        rp = Path(info["dst"]).with_suffix(".report.json")
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Censr", "Не удалось открыть отчёт проверки:\n%s" % e)
            return
        mode = data.get("mode") or self.settings.mode   # режим — из отчёта, не из текущих настроек
        wbt = {}
        try:
            # транскрипт из кэша — контекст фраз («…и тут он █████ сказал…»);
            # model_id_for не поднимает onnx — только строит ключ кэша
            from . import cache
            from .asr import model_id_for
            mid = model_id_for(self.settings.model_dir or default_model_dir())
            for t in data.get("tracks") or [0]:
                w = cache.load_words(data.get("src", ""), mid, int(t))
                if w:
                    wbt[int(t)] = w
        except Exception:
            wbt = {}                                    # без кэша — просто без контекста
        dlg = ReviewDialog(data.get("src", ""), info["dst"], data, str(rp), mode, self,
                           words_by_track=wbt)
        if dlg.exec():
            info["count"] = dlg.applied_count
            # итог квитанции — вслед за правками (строка файла уже показывает новое число)
            self._found_total = sum(i["count"] for i in self._done_files)
            self._set_done_summary()
            self._fill_done_list()
        dlg.deleteLater()

    def _on_error(self, row, err):
        if 0 <= row < len(self.rows):
            self.rows[row].state = "error"
            name = self.rows[row].path.name
        else:
            name = "?"
        self._completed += 1
        self._errors.append((name, err))
        self._proc_set(row, "error")
        self._update_sum(self._overall(row, 100))
        if self.taskbar:
            self.taskbar.error()

    def _set_done_summary(self):
        """Герой-цифра и подписи экрана «Готово» (и пересборка после правок)."""
        ctx = getattr(self, "_sum_ctx", {}) or {}
        n = self._found_total
        self.done_hero.set_text(str(n))
        files = len(self._done_files)
        parts = ["%s заглушено" % _plural(n, "слово", "слова", "слов"),
                 "%d %s" % (files, _plural(files, "файл", "файла", "файлов")),
                 _fmt_dur(ctx.get("elapsed", 0))]
        spd = ctx.get("total_dur", 0) / max(ctx.get("elapsed", 0), 1e-6)
        if spd >= 2:
            parts.append("×%d" % round(spd))
        line = " · ".join(parts)
        if self._cancelled:
            line += ' · <span style="color:%s">прервано</span>' % theme.AMBER
        if ctx.get("errors"):
            line += ' · <span style="color:%s">%s %d</span>' % (
                theme.RED, _plural(ctx["errors"], "ошибка", "ошибки", "ошибок"),
                ctx["errors"])
        self.done_sub.setText(line)
        where = ('в «%s»' % self._dest_name()) if self.settings.output_dir \
            else self._dest_name()             # «рядом с исходниками»
        self.done_saved.setText('<a href="#out" style="color:%s; text-decoration:none;">'
                                'сохранено %s <span style="color:%s;">(перейти)</span></a>'
                                % (theme.ACCENT, where, theme.FAINT))

    def _on_all_done(self):
        native.keep_awake(False)                   # обработка завершена/прервана — снять анти-сон
        w = self.worker
        self.worker = None
        if w is not None:
            w.wait()              # дождаться полного выхода из run(): иначе QThread
            w.deleteLater()       # может быть уничтожен «на ходу» → краш на 100%
        for k in ("словарь", "настройки"):
            self._top_btns[k].setEnabled(True)
        self.add_link.setEnabled(True)
        self.add_link.setVisible(True)             # на «Готово» — быстрый путь к новой пачке
        self._drag_hint.setVisible(False)          # на «Готово» вместо приглашения — «+ добавить»
        self.clear_btn.setEnabled(True)
        self._style_start(stop_mode=False)         # вернуть зелёную primary
        self._blink.start(560)                     # каретка снова мигает
        if self._fatal:
            # модель не загрузилась — вернуть экран очереди, а не квитанцию «ГОТОВО»
            self.clear_btn.show()
            self.add_link.show()
            self.start_btn.setEnabled(True)
            if self.taskbar:
                self.taskbar.clear()
            self._sync()                       # роль и текст кнопки вернёт _sync
            return
        errors = sum(1 for r in self.rows if r.state == "error")
        done = self._completed - errors
        elapsed = time.perf_counter() - getattr(self, "_t_start", time.perf_counter())
        # контекст сводки сохраняем: после правок в «проверить» она пересобирается
        self._sum_ctx = {"errors": errors, "elapsed": elapsed,
                         "total_dur": sum(self._weights) if self._weights else 0.0}
        self.stack.setCurrentIndex(3)                   # страница «Готово» (квитанция)
        self.note.setText("")
        self._set_done_summary()
        # «Прервано» без единого готового файла — центрированное сообщение вместо пустой квитанции
        cancel_only = self._cancelled and not self._done_files
        self.cancel_box.setVisible(cancel_only)
        for wdg in (self.done_hero, self.done_sub, self.done_saved):
            wdg.setVisible(not cancel_only)
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
        if self.worker is not None:
            return                             # страховка: «сброс» не должен сработать при живом воркере
        for r in self.rows:
            r.deleteLater()
        self.rows = []
        self.open_btn.hide()
        self.clear_btn.show()
        self.clear_btn.setEnabled(True)
        self.add_link.show()
        self.add_link.setEnabled(True)
        self._sync()                           # роль и текст кнопки вернёт _sync

    def closeEvent(self, e):
        """Аккуратно останавливаем воркер и фоновые задачи, чтобы Qt не падал
        с «QThread: Destroyed while thread is still running»."""
        if self.worker is not None and self.worker.isRunning():
            self._cancelled = True
            self.worker.stop()                   # ставит флаг и прибивает живой ffmpeg/ffprobe
            if not self.worker.wait(15000):      # последний рубеж — практически недостижим:
                self.worker.terminate()          # kill_active_ff() снимает энкод за миллисекунды
                self.worker.wait(2000)
        try:
            from .audio import kill_active_ff
            kill_active_ff()                     # снять ffprobe/ffmpeg фоновых задач пула —
        except Exception:                        # зависший probe не должен переживать выход
            pass
        QThreadPool.globalInstance().waitForDone(2000)   # _ProbeTask'и и прочие фоновые задачи
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
