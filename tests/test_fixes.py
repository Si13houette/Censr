# -*- coding: utf-8 -*-
"""Регрессионные тесты на фиксы аудита 2026-06-09: guard'ы dst==src,
валидация дорожек, recensor без зон, разрезка спана по гэпам,
строгий --track, валидация settings, prune кэша, плюрализация."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from censr import settings as settings_mod
from censr.asr import Word
from censr.audio import AudioError
from censr.cli import _parse_tracks
from censr.detector import ProfanityDetector
from censr.pipeline import censor_file, recensor
from censr.settings import Settings

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
ffmpeg = pytest.mark.skipif(not HAVE_FF, reason="нужен ffmpeg в PATH")


def _wav(path, *, dur=2.0, sr=16000, freq=440):
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={sr}",
                    "-ac", "1", str(path)], check=True)


class _FakeTr:
    def __init__(self, words):
        self._words = words

    def transcribe_file(self, src, progress=None, word_cb=None, cancel=None, audio_index=0):
        if word_cb:
            word_cb(self._words)
        if progress:
            progress(1.0, 1.0)
        return self._words


# ---------------------------------------------------------------- dst == src

@ffmpeg
def test_censor_file_refuses_dst_equals_src(tmp_path):
    """--suffix "" уничтожал исходник: теперь понятная ошибка ДО любой записи."""
    src = tmp_path / "in.wav"
    _wav(src)
    before = src.read_bytes()
    with pytest.raises(AudioError):
        censor_file(src, src, _FakeTr([]), ProfanityDetector())
    assert src.read_bytes() == before              # исходник не тронут


@ffmpeg
def test_recensor_refuses_dst_equals_src(tmp_path):
    src = tmp_path / "in.wav"
    _wav(src)
    with pytest.raises(AudioError):
        recensor(src, src, {0: [(0.1, 0.2)]})


# ---------------------------------------------------------------- дорожки

@ffmpeg
def test_censor_file_invalid_track_raises(tmp_path):
    """Промах по номеру дорожки — ошибка, а не молчаливая подмена первой."""
    src = tmp_path / "in.wav"
    _wav(src)
    with pytest.raises(AudioError):
        censor_file(src, tmp_path / "o.wav", _FakeTr([]), ProfanityDetector(), tracks=[5])


def test_parse_tracks_strict():
    assert _parse_tracks(None) is None
    assert _parse_tracks("all") is None
    assert _parse_tracks("1,3") == [0, 2]
    for bad in ("0", "x", "1-3", "1,0", "1,x", "-2"):
        with pytest.raises(ValueError):
            _parse_tracks(bad)


# ---------------------------------------------------------------- recensor

@ffmpeg
def test_recensor_no_zones_copies_original(tmp_path):
    """Все слова сняты → выход бит-в-бит равен оригиналу (без lossy-перекодирования)."""
    src = tmp_path / "in.wav"
    _wav(src)
    dst = tmp_path / "out.wav"
    recensor(src, dst, {0: []})
    assert dst.read_bytes() == src.read_bytes()


@ffmpeg
def test_clean_file_copied_bit_exact(tmp_path):
    """Мат не найден → выход бит-в-бит равен оригиналу (раньше чистый файл
    полностью пере-энкодился: минуты впустую + lossy-деградация)."""
    src = tmp_path / "in.wav"
    _wav(src)
    dst = tmp_path / "out.wav"
    rep = censor_file(src, dst, _FakeTr([Word("привет", 0.1, 0.5)]),
                      ProfanityDetector(), use_cache=False)
    assert rep.flagged_words == 0
    assert dst.read_bytes() == src.read_bytes()


# ------------------------------------------------- разрезка спана по гэпам

@ffmpeg
def test_span_gap_splits_into_subzones_not_drops_tail(tmp_path):
    """Разрыв таймкодов >0.3 c внутри матного спана: раньше хвост спана молча
    отбрасывался (мат оставался слышимым), теперь — отдельная под-зона."""
    src = tmp_path / "in.wav"
    _wav(src, dur=14.0)
    #            п    о    е    |гап 0.8с|  б    а    т    ь
    ct = [10.0, 10.1, 10.2, 11.0, 11.1, 11.2, 11.3]
    w = Word("поебать", 10.0, 11.5, char_times=ct)
    rep = censor_file(src, tmp_path / "o.wav", _FakeTr([w]), ProfanityDetector(),
                      mode="silence")
    subs = [c for c in rep.censored if c.reason.endswith("+sub")]
    assert len(subs) >= 2, "ожидались под-зоны по обе стороны гэпа"
    starts = sorted(c.start for c in subs)
    assert starts[0] < 10.5 and starts[-1] >= 11.0   # хвост после гэпа не потерян


# ---------------------------------------------------------------- settings

def test_settings_load_validates_types(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "extra_words": "слово",          # строка вместо списка — детектор получил бы буквы
        "whitelist": ["ок"],
        "edge_keep_pct": "12",           # строка вместо int
        "mode": "explode",               # неизвестный режим
        "use_cache": 1,                  # int вместо bool
    }), encoding="utf-8")
    s = Settings.load()
    assert s.extra_words == []           # мусор отброшен
    assert s.whitelist == ["ок"]         # валидное принято
    assert isinstance(s.edge_keep_pct, int)
    assert s.mode == "silence"
    assert s.use_cache is True


def test_settings_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "_config_dir", lambda: tmp_path)
    s = Settings(mode="beep", extra_words=["дурак"], edge_keep_pct=20)
    s.save()
    s2 = Settings.load()
    assert (s2.mode, s2.extra_words, s2.edge_keep_pct) == ("beep", ["дурак"], 20)
    assert not list(tmp_path.glob("*.tmp"))          # временный файл подчищен


def test_settings_default_edge_matches_gui_levels():
    """Дефолт слышимости краёв должен быть одним из уровней GUI {5, 12, 20} —
    иначе «сохранить» без изменений молча менял значение."""
    assert Settings().edge_keep_pct in (5, 12, 20)


# ---------------------------------------------------------------- cache

def test_cache_prune_by_age(tmp_path, monkeypatch):
    from censr import cache as cache_mod
    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path)
    (tmp_path / "old.json").write_text("{}", encoding="utf-8")
    cache_mod.prune(max_age_days=0)                  # всё старше «сейчас» — удалить
    assert not list(tmp_path.glob("*.json"))


def test_cache_save_words_atomic_no_tmp_left(tmp_path, monkeypatch):
    from censr import cache as cache_mod
    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path / "cache")
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 100)
    cache_mod.save_words(src, "m", [Word("привет", 0.0, 0.5)])
    assert cache_mod.load_words(src, "m")[0].word == "привет"
    assert not list((tmp_path / "cache").glob("*.tmp"))


# ---------------------------------------------------------------- gui-хелперы

def test_plural_russian():
    gui = pytest.importorskip("censr.gui", reason="нужен PySide6")
    p = gui._plural
    assert p(1, "файл", "файла", "файлов") == "файл"
    assert p(2, "файл", "файла", "файлов") == "файла"
    assert p(5, "файл", "файла", "файлов") == "файлов"
    assert p(11, "файл", "файла", "файлов") == "файлов"
    assert p(21, "файл", "файла", "файлов") == "файл"
    assert p(22, "файл", "файла", "файлов") == "файла"
    assert p(111, "файл", "файла", "файлов") == "файлов"


def test_manual_add_parse_strict():
    gui = pytest.importorskip("censr.gui", reason="нужен PySide6")
    parse = gui.ManualAddDialog._parse
    assert parse("1:23") == 83.0
    assert parse("1:23.5") == 83.5
    assert parse("1:02:03") == 3723.0
    for bad in ("1:-30", "0:99", "1:2:3:4", "abc", ""):
        assert parse(bad) is None, bad


def test_model_id_for_matches_transcriber_format():
    """GUI строит ключ кэша без загрузки модели — формат должен совпадать."""
    from censr.asr import model_id_for
    assert model_id_for(None) == "gigaam-v3-ctc|int8|hub"
    assert model_id_for("models/x") == "gigaam-v3-ctc|int8|models/x"


def test_phrase_context_finds_neighbors():
    """Контекст фразы в «проверке»: соседние слова вокруг таймкода."""
    gui = pytest.importorskip("censr.gui", reason="нужен PySide6")
    ws = [Word("ну", 1.0, 1.2), Word("ты", 1.3, 1.5), Word("блять", 1.6, 2.0),
          Word("даёшь", 2.1, 2.4), Word("сказал", 2.5, 2.9)]
    before, target, after = gui._phrase_context(ws, 1.6, 2.0)
    assert target == "блять"
    assert before == ["ну", "ты"]
    assert after == ["даёшь", "сказал"]
    assert gui._phrase_context(ws, 50.0, 50.5) is None      # далеко — нет контекста
    assert gui._phrase_context([], 1.0, 2.0) is None


def test_fmt_eta_rounding():
    gui = pytest.importorskip("censr.gui", reason="нужен PySide6")
    assert gui._fmt_eta(42) == "~40 с"
    assert gui._fmt_eta(300) == "~5 мин"
    assert "ч" in gui._fmt_eta(2 * 3600)
