# -*- coding: utf-8 -*-
"""Геометрия зоны глушения: середина слова глушится, края остаются слышимыми.

Требование: «примерно слышно, что за слово» — первая/последняя буква.
Границы реального звучания ищутся по энергии (таймкоды CTC плавают:
старт опаздывает, конец занижен, тянущиеся слова обрезаются).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KEEP_HEAD_S = 0.045  # максимум слышимой «первой буквы» от реального начала
KEEP_TAIL_S = 0.05   # максимум слышимой «последней буквы» до реального конца
KEEP_FRAC = 0.15     # доля длительности на каждый край (но не меньше 0.03)
# (0.07/0.08/0.2 оставляли «бля» целиком у растянутых слов — пользователь слышал слово)
MUTE_MIN_S = 0.10    # минимальная длина заглушенной середины
FULL_MUTE_DUR_S = 0.13  # слова короче — целиком (края не оставить)
SCAN_STEP_S = 0.02
RMS_FLOOR = 0.004
RMS_REL = 0.15


@dataclass(frozen=True)
class ZoneParams:
    """Геометрия зоны глушения. Передаётся в compute_zone явно — не через
    глобалы модуля (чтобы GUI/CLI не «протекали» настройками друг в друга)."""
    keep_head_s: float = KEEP_HEAD_S
    keep_tail_s: float = KEEP_TAIL_S
    keep_frac: float = KEEP_FRAC
    mute_min_s: float = MUTE_MIN_S
    full_mute_dur_s: float = FULL_MUTE_DUR_S
    full: bool = False               # максимальная очистка — глушить слово целиком (без краёв)

    @classmethod
    def from_edge_pct(cls, pct, full: bool = False) -> "ZoneParams":
        """% слышимости краёв (5..25) → геометрия (как раньше делал GUI-воркер)."""
        f = max(5, min(25, int(pct))) / 100.0
        return cls(keep_head_s=f * 0.45, keep_tail_s=f * 0.50, keep_frac=f, full=full)


_DEFAULT = ZoneParams()


def _rms(seg: np.ndarray) -> float:
    return float(np.sqrt((seg ** 2).mean())) if len(seg) else 0.0


def refine_onset(mono: np.ndarray, sr: int, start: float, limit: float, ref_rms: float) -> float:
    """Реальное начало слова: от CTC-старта назад, пока энергия высокая."""
    thresh = max(RMS_FLOOR, RMS_REL * ref_rms)
    i, lim, w = int(start * sr), max(int(limit * sr), 0), max(int(SCAN_STEP_S * sr), 1)
    while i - w >= lim:
        if _rms(mono[i - w:i]) < thresh:
            break
        i -= w
    return i / sr


def refine_end(mono: np.ndarray, sr: int, end: float, limit: float, ref_rms: float) -> float:
    """Реальный конец слова: от CTC-конца вперёд, пока энергия высокая."""
    thresh = max(RMS_FLOOR, RMS_REL * ref_rms)
    i, lim, w = int(end * sr), min(int(limit * sr), len(mono)), max(int(SCAN_STEP_S * sr), 1)
    while i + w <= lim:
        if _rms(mono[i:i + w]) < thresh:
            break
        i += w
    return i / sr


def compute_zone(mono: np.ndarray, sr: int, start: float, end: float,
                 prev_end: float | None = None,
                 next_start: float | None = None,
                 params: ZoneParams = _DEFAULT) -> tuple[float, float]:
    """Зона глушения: [реальное начало + голова, реальный конец − хвост].

    prev_end/next_start — границы соседних чистых слов (пределы поиска).
    """
    ref = _rms(mono[int(start * sr):max(int(end * sr), int(start * sr) + 1)])
    on_lim = (start - 0.25) if prev_end is None else max(prev_end - 0.02, start - 0.25)
    end_lim = (end + 0.4) if next_start is None else min(end + 0.4, next_start + 0.03)
    onset = refine_onset(mono, sr, start, on_lim, ref)
    wend = max(refine_end(mono, sr, end, end_lim, ref), end)
    if params.full:                        # максимальная очистка: всё слово, без краёв
        return onset, wend
    dur = wend - onset
    if dur < params.full_mute_dur_s:
        return onset, wend
    head = min(max(params.keep_frac * dur, 0.03), params.keep_head_s)
    tail = min(max(params.keep_frac * dur, 0.03), params.keep_tail_s)
    core = dur - head - tail
    if core < params.mute_min_s:           # ужимаем края пропорционально
        k = max((dur - params.mute_min_s) / (head + tail), 0.0)
        head, tail = head * k, tail * k
    return onset + head, wend - tail
