# -*- coding: utf-8 -*-
"""Пайплайн и аудио-I/O: разметка спанов склейки, сквозное глушение,
сохранение формата и понятные ошибки ffmpeg."""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from censr import audio
from censr.asr import Word
from censr.detector import ProfanityDetector
from censr.pipeline import _norm_index_map, censor_file

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
ffmpeg = pytest.mark.skipif(not HAVE_FF, reason="нужен ffmpeg в PATH")


def _wav(path, *, dur=2.0, sr=16000, freq=440, codec=None):
    cmd = ["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
           "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={sr}", "-ac", "1"]
    if codec:
        cmd += ["-c:a", codec]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)


class _FakeTr:
    def __init__(self, words):
        self._words = words

    def transcribe_file(self, src, progress=None, word_cb=None, cancel=None, audio_index=0):
        if word_cb:
            word_cb(self._words)
        if progress:
            progress(1.0, 1.0)
        return self._words


def test_norm_index_map():
    assert _norm_index_map("хуй") == [0, 1, 2]
    assert _norm_index_map("aхуй") == [1, 2, 3]      # латинская «a» выкинута
    assert _norm_index_map("ХУЙ") == [0, 1, 2]
    assert _norm_index_map("ёж") == [0, 1]           # ё→е, длина 1:1
    assert _norm_index_map("x1!") == []


@ffmpeg
def test_censor_file_basic(tmp_path):
    src = tmp_path / "in.wav"
    _wav(src, dur=2.0)
    words = [Word("привет", 0.1, 0.4), Word("блять", 0.8, 1.2)]
    dst = tmp_path / "out.wav"
    rep = censor_file(src, dst, _FakeTr(words), ProfanityDetector(), mode="silence")
    assert dst.exists()
    assert rep.flagged_words == 1                     # живой счётчик = матных слов
    assert len(rep.censored) >= 1
    cw = rep.censored[0]
    meta = audio.probe(dst)
    mono = audio.decode(dst, meta).mean(axis=1)
    sr = meta.sample_rate
    seg = mono[int((cw.mute_from + 0.05) * sr):int((cw.mute_to - 0.05) * sr)]
    assert float(np.sqrt((seg ** 2).mean())) < 0.02   # середина реально заглушена


@ffmpeg
def test_glue_branch_uses_index_map(tmp_path):
    """Склейка с не-кириллицей: глушим по правильным таймкодам (баг-фикс #6)."""
    src = tmp_path / "in.wav"
    _wav(src, dur=13.0)
    ct = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7]   # len == len("aпоебать")
    w = Word("aпоебать", 10.0, 12.0, char_times=ct)         # латинская «a» в начале
    rep = censor_file(src, tmp_path / "o.wav", _FakeTr([w]), ProfanityDetector(), mode="silence")
    sub = [c for c in rep.censored if c.reason.endswith("+sub")]
    assert sub, "ожидалась под-зона склейки"
    assert sub[0].word == "поебать"                  # ярлык — нормализованный кусок
    assert abs(sub[0].start - 10.1) < 1e-6           # старт = таймкод «п», не «a»(10.0)


class _RepeatTr:
    """Возвращает одно и то же матное слово каждый проход (для теста тщательной очистки)."""
    model_id = "repeat"

    def __init__(self, word):
        self._w = word
        self.passes = 0

    def transcribe_file(self, src, progress=None, word_cb=None, cancel=None, audio_index=0):
        self.passes += 1
        words = [Word(self._w, 1.0, 1.3)]
        if word_cb:
            word_cb(words)
        if progress:
            progress(1.0, 1.0)
        return words


@ffmpeg
def test_thorough_clean_does_not_double_count(tmp_path):
    """Тщательная очистка: одно и то же слово на каждом проходе считается ОДИН раз
    (а не max_passes раз) и не плодит дубли в censored (баг-фикс)."""
    src = tmp_path / "in.wav"
    _wav(src, dur=4.0)
    found = [0]
    rep = censor_file(src, tmp_path / "o.wav", _RepeatTr("блять"), ProfanityDetector(),
                      mode="silence", use_cache=False, max_passes=3,
                      progress=lambda frac, f=0, stage="": found.__setitem__(0, f))
    assert rep.flagged_words == 1                  # не 3
    assert len(rep.censored) == 1                  # без дублей
    assert found[0] == 1                           # живой счётчик тоже не двоит


@ffmpeg
def test_encode_preserves_24bit(tmp_path):
    src = tmp_path / "in24.wav"
    _wav(src, dur=1.0, sr=44100, freq=300, codec="pcm_s24le")
    meta = audio.probe(src)
    assert meta.codec == "pcm_s24le"
    dst = tmp_path / "out24.wav"
    audio.encode(audio.decode(src, meta), meta, dst)
    assert audio.probe(dst).codec == "pcm_s24le"     # не деградировало до 16 бит


@ffmpeg
def test_probe_bad_file_raises_audioerror(tmp_path):
    with pytest.raises(audio.AudioError):
        audio.probe(tmp_path / "nope.wav")


@ffmpeg
def test_probe_no_audio_stream(tmp_path):
    png = tmp_path / "x.png"
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
                    "-i", "color=c=black:s=16x16:d=1", "-frames:v", "1", str(png)], check=True)
    with pytest.raises(audio.AudioError):
        audio.probe(png)
