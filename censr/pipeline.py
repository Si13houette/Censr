# -*- coding: utf-8 -*-
"""Пайплайн: файл → ASR → детектор → глушение → файл + отчёт."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import cache
from .asr import SR as ASR_SR
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


def _disposition(stream) -> str | None:
    """Строка -disposition для перекодированной дорожки (default/forced),
    чтобы мультиплекс не терял флаги исходной дорожки."""
    parts = [n for n, on in (("default", stream.dis_default),
                             ("forced", stream.dis_forced)) if on]
    return "+".join(parts) if parts else None


def _build_zones(words, detector, mono, sr, zp, track):
    """Детекция мата → (зоны глушения, список CensoredWord, число матных слов)."""
    censored: list[CensoredWord] = []
    zones: list[tuple[float, float]] = []
    flags = [detector.check(w.word) for w in words]
    flagged = sum(1 for m in flags if m is not None)
    for i, (w, m) in enumerate(zip(words, flags, strict=True)):
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
            # куски; спан режется по разрывам таймкодов букв (>0.3 c — другой звук)
            # на НЕСКОЛЬКО под-зон (раньше хвост спана после разрыва молча
            # отбрасывался — мат в тянущемся слове мог остаться слышимым)
            for a0, b0 in m.spans:
                a = a0
                while a < b0:
                    k = a
                    while k + 1 < b0 and ct[imap[k + 1]] - ct[imap[k]] <= 0.3:
                        k += 1
                    b = k + 1
                    sub_s, sub_e = ct[imap[a]], ct[imap[b - 1]]
                    zs, ze = compute_zone(mono, sr, sub_s, sub_e,
                                          prev_end=max(w.start, sub_s - 0.4),
                                          next_start=min(w.end, sub_e + 0.4), params=zp)
                    zs = max(zs, 0.0)
                    if ze - zs >= 1e-3:        # пропустить выродившуюся (нулевую) под-зону
                        zones.append((zs, ze))
                        censored.append(CensoredWord(m.norm[a:b], sub_s, sub_e,
                                                     round(zs, 3), round(ze, 3), m.reason + "+sub", track))
                    a = b
        else:
            zs, ze = compute_zone(mono, sr, w.start, w.end, prev_end, next_start, params=zp)
            zones.append((max(zs, 0.0), ze))
            censored.append(CensoredWord(w.word, w.start, w.end,
                                         round(zs, 3), round(ze, 3), m.reason, track))
    return zones, censored, flagged


PASS_PAD = 2.0   # «тщательная очистка»: окно ±сек вокруг заглушенных зон


def _resample16k(mono, sr: int):
    """float32 mono native → 16 кГц для ASR (soxr, в памяти).
    None — soxr недоступен: вызывающий уходит на ffmpeg-путь (transcribe_file)."""
    if sr == ASR_SR:
        return mono
    try:
        import soxr  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    import numpy as np  # noqa: PLC0415
    return np.ascontiguousarray(soxr.resample(mono, sr, ASR_SR), dtype=np.float32)


def _mono(samples):
    return samples[:, 0] if samples.shape[1] == 1 else samples.mean(axis=1)


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

    def _pass_cb(pass_idx):
        """Прогресс прохода в своём поддиапазоне [pass/max, (pass+1)/max] —
        иначе при тщательной очистке полоска откатывалась к нулю каждый проход."""
        if max_passes <= 1:
            return on_asr
        def cb(done, total):
            if total:
                on_asr(pass_idx + min(done / total, 1.0), float(max_passes))
        return cb

    model_id = getattr(transcriber, "model_id", "?")
    sr = meta.sample_rate
    samples = decode(src, meta, idx)       # единственный нативный декод дорожки
    duration = samples.shape[0] / sr
    mono = _mono(samples)
    # сэмпловый путь (один декод): нативное моно → 16 кГц в памяти. Фоллбэк на
    # transcribe_file (второй ffmpeg-декод) — если нет soxr или метода у фейка
    can_samples = hasattr(transcriber, "transcribe_samples")
    words: list[Word] | None = cache.load_words(src, model_id, idx) if use_cache else None
    if words is None:
        mono16 = _resample16k(mono, sr) if can_samples else None
        if mono16 is not None:
            words = transcriber.transcribe_samples(mono16, progress=_pass_cb(0),
                                                   word_cb=_wcb, cancel=cancel)
        else:
            can_samples = False
            words = transcriber.transcribe_file(src, progress=_pass_cb(0), word_cb=_wcb,
                                                cancel=cancel, audio_index=idx)
        if cancel and cancel():
            raise Cancelled()
        if use_cache:
            cache.save_words(src, model_id, words, idx)
    else:
        _wcb(words)                   # живой счётчик — тем же правилом, что и при ASR
        _pass_cb(0)(1.0, 1.0)         # кэш: распознавание пропущено — сразу двигаем прогресс

    zones, censored, flagged = _build_zones(words, detector, mono, sr, zp, idx)
    muted = merge_zones(zones)             # всё, что уже заглушено (для учёта новизны)
    if zones:
        samples = apply_censor(samples, sr, muted, mode=mode)

    # тщательная очистка: ещё проходы по уже заглушенному звуку, пока находится новое.
    # Повторные проходы добивают края, но в счётчик/отчёт идут ТОЛЬКО реально новые
    # слова (не попавшие в прошлые зоны) — иначе одно слово считалось бы каждый проход.
    # Сэмпловый путь распознаёт не весь файл, а окна ±PASS_PAD c вокруг зон — это
    # доли процента длительности, проход почти бесплатен.
    p = 1
    while p < max_passes and zones:
        if cancel and cancel():
            raise Cancelled()
        if set_stage:
            set_stage("проход %d/%d" % (p + 1, max_passes))
        rew = None
        if can_samples:
            mono = _mono(samples)
            wins = merge_zones([(max(0.0, a - PASS_PAD), min(duration, b + PASS_PAD))
                                for a, b in muted], gap=0.2)
            rew, cb = [], _pass_cb(p)
            for wi, (a, b) in enumerate(wins):
                if cancel and cancel():
                    raise Cancelled()
                seg16 = _resample16k(mono[int(a * sr):int(b * sr)], sr)
                if seg16 is None:               # soxr пропал на лету — старый путь
                    rew = None
                    break
                for w in transcriber.transcribe_samples(seg16, cancel=cancel):
                    rew.append(Word(w.word, round(w.start + a, 3), round(w.end + a, 3),
                                    w.conf, [round(t + a, 3) for t in (w.char_times or [])]))
                cb(wi + 1.0, float(len(wins)))
        if rew is None:                         # фоллбэк: весь файл через temp-WAV
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "pass.wav"
                write_wav(samples, meta, tmp)
                rew = transcriber.transcribe_file(tmp, progress=_pass_cb(p), cancel=cancel)
            mono = _mono(samples)
        zones, cens2, _ = _build_zones(rew, detector, mono, sr, zp, idx)
        if not zones:
            break
        # «новое» — зона, НЕ пересекающаяся с уже заглушённым. Раньше сверяли
        # середину зоны: край повторно найденного слова мог не попасть в прошлый
        # интервал → одно слово считалось новым на каждом проходе (двоение счётчика)
        new = [c for c in cens2
               if not any(s < c.mute_to and c.mute_from < e for s, e in muted)]
        samples = apply_censor(samples, sr, merge_zones(zones), mode=mode)
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
                max_passes: int = 1, defer_encode: bool = False):
    """progress(frac, found, stage) — ход, счётчик мата и текущий этап;
    cancel() — прерывание.

    use_cache: брать/сохранять транскрипт в кэш (зависит только от аудио+модели).
    tracks: 0-based индексы аудиодорожек для обработки (None — все). Невыбранные
    дорожки и видеопоток копируются в выход без перекодирования.
    max_passes: >1 — тщательная очистка (повторные проходы по заглушенному звуку).
    defer_encode: вернуть (Report, finalize|None) — на быстром пути энкод отложен
    в finalize() (конвейер GUI гонит его параллельно с ASR следующего файла);
    None — файл уже готов. По умолчанию (False) возвращается просто Report.
    """
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise AudioError("Выходной файл совпадает с исходным: %s\n"
                         "Так исходник был бы уничтожен — укажи другую папку или суффикс." % src)
    zp = zone_params or ZoneParams()
    streams = list_audio_streams(src)
    if not streams:
        raise AudioError("В файле нет аудиодорожки: %s" % src)
    has_vid = has_video_stream(src)
    if tracks is None:
        sel = list(range(len(streams)))
    else:
        bad = [i for i in tracks if not 0 <= i < len(streams)]
        if bad:   # раньше промах молча подменялся первой дорожкой
            raise AudioError("Нет дорожк%s %s: в файле только %d (нумерация с 1)"
                             % ("и" if len(bad) == 1 else "ек",
                                ", ".join(str(i + 1) for i in bad), len(streams)))
        sel = list(tracks)
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
        rep = Report(str(src), str(dst), round(dur, 1), wt, censored, flagged, sel)

        def _finish():
            """Хвост обработки: энкод/копирование. Выделен, чтобы конвейер GUI
            мог гнать его в фоне, пока ASR жуёт следующий файл."""
            dst.parent.mkdir(parents=True, exist_ok=True)
            if censored:
                encode(s, meta, dst, src=src)    # src — донор тегов/обложки
            else:                                # мат не найден — оригинал бит-в-бит,
                shutil.copyfile(src, dst)        # без лишнего lossy-перекодирования
            last_frac[0] = 1.0
            _emit()
            return rep

        if defer_encode:
            return rep, _finish
        _finish()
        return rep

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
            if censored:
                tmpf = Path(td) / ("a%d.%s" % (stream.index, temp_ext_for(meta.codec)))
                encode(s, meta, tmpf)
                plan.append({"file": str(tmpf), "language": stream.language,
                             "title": stream.title, "disposition": _disposition(stream)})
            else:
                plan.append({"copy": stream.index})   # чистая дорожка — без перекодирования
            done[0] += 1
            last_frac[0] = done[0] / nsel * 0.95
            _emit()
            if cancel and cancel():
                raise Cancelled()
        mux_audio_tracks(src, dst, plan, copy_video=has_vid)
    last_frac[0] = 1.0
    _emit()
    rep = Report(str(src), str(dst), round(duration, 1), total_words, all_censored,
                 total_flagged, sel)
    return (rep, None) if defer_encode else rep


def recensor(src: str | Path, dst: str | Path, zones_by_track: dict,
             mode: str = "silence") -> None:
    """Перерисовать выход по уже готовому набору зон глушения (без распознавания).

    Используется экраном «проверки»: пользователь снял/добавил слова — берём
    оригинал src, применяем отредактированные зоны и перезаписываем dst.
    zones_by_track: {индекс_дорожки: [(mute_from, mute_to), ...]}.
    Видео и незатронутые дорожки копируются как есть.
    """
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise AudioError("Выходной файл совпадает с исходным: %s" % src)
    streams = list_audio_streams(src)
    if not streams:
        raise AudioError("В файле нет аудиодорожки: %s" % src)
    has_vid = has_video_stream(src)
    touched = {t for t, z in zones_by_track.items() if z}

    # dst — уже готовый хороший выход: пишем во временный файл (с тем же
    # расширением, чтобы ffmpeg понял контейнер) и подменяем атомарно — сбой
    # посреди записи не уничтожит единственную целую копию
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)

    # быстрый путь: одна аудиодорожка без видео
    if len(streams) == 1 and not has_vid:
        z = merge_zones(zones_by_track.get(0, []))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not z:                  # все слова сняты — оригинал как есть,
                shutil.copyfile(src, tmp)    # без лишнего lossy-перекодирования
            else:
                meta = streams[0].meta()
                samples = decode(src, meta, 0)
                samples = apply_censor(samples, meta.sample_rate, z, mode=mode)
                encode(samples, meta, tmp, src=src)  # src — донор тегов/обложки
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
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
            plan.append({"file": str(tmpf), "language": stream.language,
                         "title": stream.title, "disposition": _disposition(stream)})
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            mux_audio_tracks(src, tmp, plan, copy_video=has_vid)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
