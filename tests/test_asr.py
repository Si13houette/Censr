# -*- coding: utf-8 -*-
"""ASR-нарезка чанков: короткий хвост в конце файла не должен теряться (#7)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from censr.asr import SR, Transcriber


class _Rec:
    tokens: list = []
    timestamps: list = []
    logprobs = None


class _FakeModel:
    def __init__(self):
        self.calls = []

    def recognize(self, seg, sample_rate):
        self.calls.append(len(seg))
        return _Rec()


def _tr(model):
    tr = Transcriber.__new__(Transcriber)   # без загрузки onnx
    tr._model = model
    return tr


def _sig(n):
    """Сигнал заметно громче VAD-порога (нули — тишина, её гейт пропускает)."""
    return np.full(n, 0.05, dtype=np.float32)


def _sig(n):
    """Сигнал заметно громче VAD-порога (нули — тишина, её гейт пропускает)."""
    return np.full(n, 0.05, dtype=np.float32)


def test_short_final_tail_is_recognized_and_padded():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(_sig(2000))        # 0.125 c < 0.25 c
    assert model.calls == [SR // 4]          # распознан, дополнен тишиной до 0.25 c


def test_tiny_tail_below_floor_is_skipped():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(_sig(500))         # ~31 мс < 50 мс
    assert model.calls == []                 # слишком короткий — пропущен


def test_normal_length_not_padded():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(_sig(SR))          # 1 c
    assert model.calls == [SR]               # ровно столько, без дополнения


def test_vad_skips_silent_chunk():
    model = _FakeModel()
    tr = _tr(model)
    done = []
    tr.transcribe_samples(np.zeros(SR * 3, dtype=np.float32),
                          progress=lambda d, t: done.append((d, t)))
    assert model.calls == []                 # тишина — модель не звали
    assert done and done[-1][0] == done[-1][1]   # прогресс дошёл до конца


def test_vad_can_be_disabled():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(np.zeros(SR, dtype=np.float32), vad=False)
    assert model.calls == [SR]               # с vad=False тишина распознаётся


def test_vad_keeps_quiet_speech():
    """Сигнал на уровне −40 дБ (тихая речь) не должен отсекаться гейтом."""
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(np.full(SR, 0.01, dtype=np.float32))
    assert model.calls == [SR]
