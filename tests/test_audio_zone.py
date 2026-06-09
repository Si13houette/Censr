# -*- coding: utf-8 -*-
"""Геометрия зоны и ZoneParams (передаются явно, без глобалов модуля)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from censr.audio_zone import KEEP_HEAD_S, KEEP_TAIL_S, ZoneParams, compute_zone


def test_zone_params_from_edge_pct():
    p = ZoneParams.from_edge_pct(10)
    assert abs(p.keep_frac - 0.10) < 1e-9
    assert abs(p.keep_head_s - 0.045) < 1e-9
    assert abs(p.keep_tail_s - 0.050) < 1e-9


def test_zone_params_clamped():
    assert ZoneParams.from_edge_pct(2).keep_frac == 0.05      # снизу 5%
    assert ZoneParams.from_edge_pct(99).keep_frac == 0.25     # сверху 25%


def test_zone_params_default_matches_constants():
    p = ZoneParams()
    assert p.keep_head_s == KEEP_HEAD_S and p.keep_tail_s == KEEP_TAIL_S


def test_compute_zone_keeps_edges_on_long_word():
    sr = 16000
    mono = np.ones(sr * 2, dtype=np.float32) * 0.5    # ровная энергия
    zs, ze = compute_zone(mono, sr, 0.5, 1.0)
    assert zs > 0.5 - 0.25 and ze < 1.0 + 0.4         # внутри пределов поиска
    assert zs < ze                                    # есть что глушить


def test_compute_zone_params_widen_edges():
    sr = 16000
    mono = np.ones(sr * 2, dtype=np.float32) * 0.5
    narrow = compute_zone(mono, sr, 0.5, 1.0, params=ZoneParams.from_edge_pct(5))
    wide = compute_zone(mono, sr, 0.5, 1.0, params=ZoneParams.from_edge_pct(25))
    # больше % краёв -> позже старт глушения (слышно больше начала слова)
    assert wide[0] >= narrow[0]
