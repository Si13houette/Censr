# -*- coding: utf-8 -*-
"""Пайплайн: файл → ASR → детектор → глушение → файл + отчёт."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import tempfile

from . import cache
from .asr import Cancelled, Transcriber, Word
from .audio import (AudioError, apply_censor, decode, encode, has_video_stream,
                    list_audio_streams, merge_zones, mux_audio_tracks, temp_ext_for,
                    write_wav)
from .audio_zone import ZoneParams, compute_zone
from .detector import ProfanityDetector


@dataclass
class CensoredWord:
    word: str
    start: float
    end: float
    mute_from: float
    mute_to: float
    reason: str
    track: int = 0              # индекс аудиодорожки (0:a:N)


@dataclass
class Report:
    src: str
    dst: str
    duration: float
    words_total: int
    censored: list[CensoredWord]
    flagged_words: int          # матных слов (≈ живой счётчик); зон может быть больше
    tracks: list[int] | None = None   # какие дорожки обработаны (0-based)

    def to_dict(self) -> dict:
        return {**asdict(self), "censored_count": len(self.censored)}


def _norm_index_map(word: str) -> list[int]:
    """Для каждой буквы нормализованной формы — её индекс в ИСХОДНОМ слове.

    _normalize выкидывает всё не из [а-я], а char_times/word выровнены по
    исходному слову, поэтому спаны (индексы в norm) надо переводить обратно.
    """
    low = word.lower().replace("ё", "е")
    return [i for i, ch in enumerate(low) if "а" <= ch <= "я"]


def _covered(zones, t: float) -> bool:
    """Точка t (sec) уже попадает в одну из заглушённых зон?"""
    return any(s <= t <= e for s, e in zones)


def _build_zones(words, detector, mono, sr, zp, track):
    """Детекция мата → (зоны глушения, список CensoredWord, число матных слов)."""
    censored: list[CensoredWord] = []
    zones: list[tuple[float, float]] = []
    flags = [detector.check(w.word) for w in words]
    flagged = sum(1 for m in flags if m is not None)
    for i, (w, m) in enumerate(zip(words, flags)):
        if m is None:
            continue
        # сосед-мат не ограничивает зону: зоны всё равно сольются
        prev_end = words[i - 1].end if i > 0 and flags[i - 1] is None else None
        next_start = words[i + 1].start if i + 1 < len(words) and flags[i + 1] is None else None
        dur = w.end - w.start
        ct = w.char_times or []
        imap = _norm_index_map(w.word)         # norm-индекс → индекс в w.word/ct
        can_sub = (dur > 1.0 and m.spans and len(ct) == len(w.word)
                   and len(imap) == len(m.norm))
        if can_sub:
            # CTC-склейка (мат прилип к другой речи/языку): глушим только матные
            # куски; спан обрезается по разрыву таймкодов букв (>0.3 c — другой звук)
            for a, b in m.spans:
                k = a
                while k + 1 < b and ct[imap[k + 1]] - ct[imap[k]] <= 0.3:
                    k += 1
                b = k + 1
                sub_s, sub_e = ct[imap[a]], ct[imap[b - 1]]
                zs, ze = compute_zone(mono, sr, sub_s, sub_e,
                                      prev_end=max(w.start, sub_s - 0.4),
                                      next_start=min(w.end, sub_e + 0.4), params=zp)
                zones.append((max(zs, 0.0), ze))
                censored.append(CensoredWord(m.norm[a:b], sub_s, sub_e,
                                             round(zs, 3), round(ze, 3), m.reason + "+sub", track))
        else:
            zs, ze = compute_zone(mono, sr, w.start, w.end, prev_end, next_start, params=zp)
            zones.append((max(zs, 0.0), ze))
            censored.append(CensoredWord(w.word, w.start, w.end,
                                         round(zs, 3), round(ze, 3), m.reason, track))
    return zones, censored, flagged


def _censor_track(src, stream, transcriber, detector, zp, mode, use_cache,
                  on_asr, add_found, cancel, max_passes=1, set_stage=None):
    """Обработать одну аудиодорожку: ASR(кэш) → детекция → глушение.
    max_passes>1 — «тщательная очистка»: повторное распознавание уже заглушенного
    звука, чтобы добить слова, пропущенные на первом проходе.
    set_stage(text) — сообщить текущий этап («проход 2/3») для интерфейса.
    Возвращает (samples, meta, censored, flagged, words_total, duration)."""
    if max_passes > 1 and set_stage:
        set_stage("проход 1/%d" % max_passes)
    idx = stream.index
    meta = stream.meta()
    def _wcb(chunk):                    # живой счётчик мата по чанкам (все проходы)
        add_found(sum(1 for w in chunk if detector.check(w.word) is not None))

    model_id = getattr(transcriber, "model_id", "?")
    words: list[Word] | None = cache.load_words(src, model_id, idx) if use_cache else None
    if words is None:
        words = transcriber.transcribe_file(src, progress=on_asr, word_cb=_wcb,
                                            cancel=cancel, audio_index=idx)
        if cancel and cancel():
            raise Cancelled()
        if use_cache:
            cache.save_words(src, model_id, words, idx)
    else:
        add_found(sum(1 for w in words if detector.check(w.word) is not None))
        on_asr(1.0, 1.0)              # кэш: распознавание пропущено — сразу двигаем прогресс

    samples = decode(src, meta, idx)
    duration = samples.shape[0] / meta.sample_rate
    mono = samples.mean(axis=1)
    zones, censored, flagged = _build_zones(words, detector, mono, meta.sample_rate, zp, idx)
    muted = merge_zones(zones)             # всё, что уже заглушено (для учёта новизны)
    if zones:
        samples = apply_censor(samples, meta.sample_rate, muted, mode=mode)

    # тщательная очистка: ещё проходы по уже заглушенному звуку, пока находится новое.
    # Повторные проходы добивают края, но в счётчик/отчёт идут ТОЛЬКО реально новые
    # слова (не попавшие в прошлые зоны) — иначе одно слово считалось бы каждый проход.
    p = 1
    while p < max_passes and zones:
        if cancel and cancel():
            raise Cancelled()
        if set_stage:
            set_stage("проход %d/%d" % (p + 1, max_passes))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "pass.wav"
            write_wav(samples, meta, tmp)
            rew = transcriber.transcribe_file(tmp, progress=on_asr, cancel=cancel)
        mono = samples.mean(axis=1)
        zones, cens2, _ = _build_zones(rew, detector, mono, meta.sample_rate, zp, idx)
        if not zones:
            break
        new = [c for c in cens2 if not _covered(muted, (c.mute_from + c.mute_to) / 2)]
        samples = apply_censor(samples, meta.sample_rate, merge_zones(zones), mode=mode)
        muted = merge_zones([*muted, *zones])
        censored += new
        flagged += len(new)
        add_found(len(new))     # живой счётчик растёт только на реально новые слова
        p += 1

    return samples, meta, censored, flagged, len(words), duration


def censor_file(src: str | Path, dst: str | Path, transcriber: Transcriber,
                detector: ProfanityDetector, mode: str = "silence",
                progress=None, cancel=None, zone_params: ZoneParams | None = None,
                use_cache: bool = True, tracks: list[int] | None = None,
                max_passes: int = 1) -> Report:
    """progress(frac, found) — ход и счётчик мата; cancel() — прерывание.

    use_cache: брать/сохранять транскрипт в кэш (зависит только от аудио+модели).
    tracks: 0-based индексы аудиодорожек для обработки (None — все). Невыбранные
    дорожки и видеопоток копируются в выход без перекодирования.
    max_passes: >1 — тщательная очистка (повторные проходы по заглушенному звуку).
    """
    src, dst = Path(src), Path(dst)
    zp = zone_params or ZoneParams()
    streams = list_audio_streams(src)
    if not streams:
        raise AudioError("В файле нет аудиодорожки: %s" % src)
    has_vid = has_video_stream(src)
    sel = list(range(len(streams))) if tracks is None else [i for i in tracks if 0 <= i < len(streams)]
    if not sel:
        sel = [0]
    sel_set = set(sel)
    nsel = len(sel)

    found = [0]
    last_frac = [0.0]
    stage = [""]

    def _emit():
        if progress:
            progress(last_frac[0], found[0], stage[0])

    def _add_found(d):
        found[0] += d
        _emit()

    def _set_stage(t):
        stage[0] = t
        _emit()

    # быстрый путь: одна аудиодорожка без видео — как раньше (decode→censor→encode)
    if len(streams) == 1 and not has_vid:
        def _asr(done, total):
            if total:
                last_frac[0] = min(done / total, 1.0) * 0.9
            _emit()
        s, meta, censored, flagged, wt, dur = _censor_track(
            src, streams[0], transcriber, detector, zp, mode, use_cache, _asr, _add_found,
            cancel, max_passes=max_passes, set_stage=_set_stage)
        last_frac[0] = 0.96
        _emit()
        if cancel and cancel():
            raise Cancelled()
        dst.parent.mkdir(parents=True, exist_ok=True)
        encode(s, meta, dst)
        last_frac[0] = 1.0
        _emit()
        return Report(str(src), str(dst), round(dur, 1), wt, censored, flagged, sel)

    # мультидорожечный / видео путь: цикл по дорожкам + мультиплекс
    plan: list[dict] = []
    all_censored: list[CensoredWord] = []
    total_flagged = total_words = 0
    duration = 0.0
    done = [0]
    with tempfile.TemporaryDirectory() as td:
        for stream in streams:
            if stream.index not in sel_set:
                plan.append({"copy": stream.index})       # не выбрана — копируем как есть
                continue
            base = done[0]

            def _asr(d, total, base=base):
                if total:
                    last_frac[0] = (base + min(d / total, 1.0) * 0.9) / nsel
                _emit()

            s, meta, censored, flagged, wt, dur = _censor_track(
                src, stream, transcriber, detector, zp, mode, use_cache, _asr, _add_found,
                cancel, max_passes=max_passes, set_stage=_set_stage)
            duration = max(duration, dur)
            total_flagged += flagged
            total_words += wt
            all_censored += censored
            tmpf = Path(td) / ("a%d.%s" % (stream.index, temp_ext_for(meta.codec)))
            encode(s, meta, tmpf)
            plan.append({"file": str(tmpf), "language": stream.language})
            done[0] += 1
            last_frac[0] = done[0] / nsel * 0.95
            _emit()
            if cancel and cancel():
                raise Cancelled()
        mux_audio_tracks(src, dst, plan, copy_video=has_vid)
    last_frac[0] = 1.0
    _emit()
    return Report(str(src), str(dst), round(duration, 1), total_words, all_censored,
                  total_flagged, sel)


def recensor(src: str | Path, dst: str | Path, zones_by_track: dict,
             mode: str = "silence") -> None:
    """Перерисовать выход по уже готовому набору зон глушения (без распознавания).

    Используется экраном «проверки»: пользователь снял/добавил слова — берём
    оригинал src, применяем отредактированные зоны и перезаписываем dst.
    zones_by_track: {индекс_дорожки: [(mute_from, mute_to), ...]}.
    Видео и незатронутые дорожки копируются как есть.
    """
    src, dst = Path(src), Path(dst)
    streams = list_audio_streams(src)
    if not streams:
        raise AudioError("В файле нет аудиодорожки: %s" % src)
    has_vid = has_video_stream(src)
    touched = {t for t, z in zones_by_track.items() if z}

    # быстрый путь: одна аудиодорожка без видео
    if len(streams) == 1 and not has_vid:
        meta = streams[0].meta()
        samples = decode(src, meta, 0)
        z = merge_zones(zones_by_track.get(0, []))
        if z:
            samples = apply_censor(samples, meta.sample_rate, z, mode=mode)
        dst.parent.mkdir(parents=True, exist_ok=True)
        encode(samples, meta, dst)
        return

    # видео / мультидорожки: пересобрать с мультиплексом
    plan: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        for stream in streams:
            z = merge_zones(zones_by_track.get(stream.index, []))
            if stream.index not in touched or not z:
                plan.append({"copy": stream.index})       # дорожка не правилась — копия
                continue
            meta = stream.meta()
            samples = decode(src, meta, stream.index)
            samples = apply_censor(samples, meta.sample_rate, z, mode=mode)
            tmpf = Path(td) / ("a%d.%s" % (stream.index, temp_ext_for(meta.codec)))
            encode(samples, meta, tmpf)
            plan.append({"file": str(tmpf), "language": stream.language})
        dst.parent.mkdir(parents=True, exist_ok=True)
        mux_audio_tracks(src, dst, plan, copy_video=has_vid)
