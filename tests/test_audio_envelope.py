# -*- coding: utf-8 -*-
"""Огибающая глушения: переходы у зоны должны быть плавными (без щелчков),
в том числе когда фейд обрезается у самого конца файла."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from censr.audio import (BEEP_HZ, BEEP_LEVEL, FADE_S, _envelope, apply_censor,
                         merge_zones)


def test_zone_is_fully_muted():
    sr = 1000
    n = 40
    env = _envelope(n, sr, [(0.010, 0.020)])
    assert np.allclose(env[10:20], 0.0)


def test_fadein_starts_at_zero_no_click():
    """Полный фейд-ин после зоны идёт 0→1 (у зоны — тишина, без скачка)."""
    sr = 1000  # fade = int(FADE_S*sr) = 12 сэмплов
    fade = int(FADE_S * sr)
    n = 60
    i1 = 30
    env = _envelope(n, sr, [(0.015, i1 / sr)])
    region = env[i1:i1 + fade]
    assert region[0] < 0.05, f"щелчок: фейд-ин стартует с {region[0]:.3f}, а не с ~0"
    assert np.all(np.diff(region) >= -1e-6), "фейд-ин должен монотонно расти 0→1"
    assert region[-1] > 0.95


def test_truncated_fadein_at_eof_no_click():
    """Зона у конца файла: фейд-ин обрезан, но всё равно стартует с ~0 (баг-фикс #5)."""
    sr = 1000
    fade = int(FADE_S * sr)
    n = 20
    i1 = 15  # хвоста (n-i1=5) меньше, чем fade=12 -> обрезка
    assert n - i1 < fade
    env = _envelope(n, sr, [(0.005, i1 / sr)])
    assert env[i1 - 1] == 0.0                       # последний сэмпл зоны — тишина
    assert env[i1] < 0.05, f"щелчок у конца файла: env[{i1}]={env[i1]:.3f}"
    assert np.all(np.diff(env[i1:]) >= -1e-6)       # дальше только рост


def test_merge_zones_combines_close_intervals():
    assert merge_zones([(0.0, 1.0), (1.02, 2.0)], gap=0.05) == [(0.0, 2.0)]
    assert merge_zones([(0.0, 1.0), (3.0, 4.0)], gap=0.05) == [(0.0, 1.0), (3.0, 4.0)]
    assert merge_zones([]) == []


def _reference(samples, sr, zones, mode):
    """Старый полнофайловый вариант — эталон для зонной apply_censor."""
    n = samples.shape[0]
    env = _envelope(n, sr, merge_zones(zones, gap=max(0.05, 2 * FADE_S)), FADE_S)
    out = samples.astype(np.float32).copy()
    out *= env[:, None]
    if mode == "beep":
        # фаза в float64 — как в боевом коде (float32 вырождается после 2^24 сэмплов)
        t = np.arange(n, dtype=np.float64) / sr
        beep = (BEEP_LEVEL * np.sin(2 * np.pi * BEEP_HZ * t)).astype(np.float32)
        out += (beep * (1.0 - env))[:, None]
    return out


@pytest.mark.parametrize("mode", ["silence", "beep"])
def test_apply_censor_zonal_matches_fullfile_reference(mode):
    """Зонная apply_censor бит-в-бит совпадает с полнофайловым эталоном
    (несколько зон: у начала, в середине, крошечная, у самого конца файла)."""
    sr = 16000
    n = sr * 5
    rng = np.random.default_rng(0)
    samp = (rng.standard_normal((n, 2)) * 0.3).astype(np.float32)
    zones = [(0.005, 0.05), (1.0, 1.3), (2.4, 2.42), (4.97, 4.999)]
    got = apply_censor(samp.copy(), sr, zones, mode=mode)
    assert np.array_equal(got, _reference(samp, sr, zones, mode))


def test_beep_alive_beyond_float32_precision():
    """Бип не вырождается в DC-тишину за пределами 2^24-го сэмпла (баг-фикс:
    float32-фаза становилась константной — на 2-часовом фильме бип молчал)."""
    sr = 48000
    n = 2 ** 24 + sr                                # зона за порогом точности float32
    samp = np.zeros(n, dtype=np.float32)            # 1-D mono — экономим память
    zs = (2 ** 24 + sr // 4) / sr
    out = apply_censor(samp, sr, [(zs, zs + 0.2)], mode="beep")
    seg = out[int((zs + 0.05) * sr):int((zs + 0.15) * sr)]
    rms = float(np.sqrt((seg.astype(np.float64) ** 2).mean()))
    assert rms > BEEP_LEVEL * 0.5, f"бип мёртв: rms={rms:.4f}"
    assert seg.min() < -BEEP_LEVEL * 0.9 and seg.max() > BEEP_LEVEL * 0.9


def test_apply_censor_accepts_1d_mono():
    sr = 16000
    mono = (np.random.default_rng(1).standard_normal(sr) * 0.3).astype(np.float32)
    out = apply_censor(mono.copy(), sr, [(0.4, 0.5)], mode="silence")
    assert out.shape == (sr,)                       # 1-D вход → 1-D выход (без краша)
    mid = out[int(0.43 * sr):int(0.47 * sr)]
    assert float(np.sqrt((mid ** 2).mean())) < 1e-6  # середина заглушена
