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


def test_short_final_tail_is_recognized_and_padded():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(np.zeros(2000, dtype=np.float32))   # 0.125 c < 0.25 c
    assert model.calls == [SR // 4]          # распознан, дополнен тишиной до 0.25 c


def test_tiny_tail_below_floor_is_skipped():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(np.zeros(500, dtype=np.float32))    # ~31 мс < 50 мс
    assert model.calls == []                 # слишком короткий — пропущен


def test_normal_length_not_padded():
    model = _FakeModel()
    tr = _tr(model)
    tr.transcribe_samples(np.zeros(SR, dtype=np.float32))     # 1 c
    assert model.calls == [SR]               # ровно столько, без дополнения
