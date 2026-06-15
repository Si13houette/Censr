# -*- coding: utf-8 -*-
"""ASR-обёртка: GigaAM-v3 CTC (onnx) → слова с таймкодами."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000
CHUNK_SEC = 29.0  # GigaAM-CTC рассчитана на сегменты до ~30 с


class Cancelled(Exception):
    """Обработка прервана пользователем."""


@dataclass
class Word:
    word: str
    start: float
    end: float
    conf: float = 1.0                 # exp(mean logprob): низкая = модель не уверена
    char_times: list | None = None    # таймкод каждой буквы (для разбора склеек)


def tokens_to_words(tokens: list[str], timestamps: list[float],
                    logprobs: list[float] | None = None) -> list[Word]:
    """Собирает слова из посимвольных CTC-токенов по пробелам."""
    import math
    words: list[Word] = []
    cur, start, end, lps, cts = "", 0.0, 0.0, [], []
    have_lp = logprobs is not None       # нет logprobs → уверенность неизвестна (None, не 1.0)
    if logprobs is None:
        logprobs = [0.0] * len(tokens)
    for tok, ts, lp in zip(tokens, timestamps, logprobs, strict=False):
        clean = tok.replace("▁", " ").strip()
        if clean == "":
            if cur:
                words.append(Word(cur, start, end, round(math.exp(sum(lps) / len(lps)), 3) if have_lp else None, cts))
                cur, lps, cts = "", [], []
            continue
        if cur == "":
            start = ts
        cur += clean
        cts += [ts] * len(clean)
        end = ts
        lps.append(lp)
    if cur:
        words.append(Word(cur, start, end, round(math.exp(sum(lps) / len(lps)), 3) if have_lp else None, cts))
    return words


VAD_RMS = 1e-3   # порог «нет сигнала»: max RMS 0.2-с окна ниже (≈ −60 дБFS)


def _is_silent(seg: np.ndarray, win_s: float = 0.2, thr: float = VAD_RMS) -> bool:
    """Дешёвый VAD-гейт: True — в чанке нет окна громче порога (тишина/фон),
    модель можно не звать. Порог консервативный: шёпот заметно громче −60 дБ."""
    if not len(seg):
        return True
    win = max(int(win_s * SR), 1)
    k = len(seg) // win
    m = 0.0
    if k:
        rms = np.sqrt((seg[: k * win].reshape(k, win) ** 2).mean(axis=1))
        m = float(rms.max())
    tail = seg[k * win:]
    if len(tail):
        m = max(m, float(np.sqrt((tail ** 2).mean())))
    return m < thr


def _quiet_cut(seg: np.ndarray, search_s: float = 4.0, win_s: float = 0.2) -> int:
    """Индекс разреза: середина самого тихого окна в последних search_s секундах."""
    n = len(seg)
    search = min(int(search_s * SR), n // 2)
    win = max(int(win_s * SR), 1)
    tail = seg[n - search:]
    k = len(tail) // win
    if k < 2:
        return n
    rms = np.sqrt((tail[: k * win].reshape(k, win) ** 2).mean(axis=1))
    j = int(rms.argmin())
    return n - search + j * win + win // 2


def model_id_for(model_path: str | None = None, model: str = "gigaam-v3-ctc",
                 quantization: str | None = "int8") -> str:
    """Идентификатор модели для ключа кэша — БЕЗ загрузки самой модели.

    Нужен GUI, чтобы читать кэш транскрипта (контекст фраз в «проверке»),
    не поднимая onnx-runtime."""
    return "%s|%s|%s" % (model, quantization, model_path or "hub")


class Transcriber:
    def __init__(self, model_path: str | None = None, model: str = "gigaam-v3-ctc",
                 quantization: str | None = "int8"):
        import onnx_asr  # noqa: PLC0415
        self._model = onnx_asr.load_model(model, path=model_path, quantization=quantization).with_timestamps()
        # идентификатор модели для ключа кэша транскрипта (см. cache.py)
        self.model_id = model_id_for(model_path, model, quantization)

    def transcribe_samples(self, samples: np.ndarray, progress=None, word_cb=None,
                           cancel=None, vad: bool = True) -> list[Word]:
        """16 кГц mono float32 → слова. progress(done_sec, total_sec) — колбэк.
        word_cb(chunk_words) вызывается после каждого чанка (для счёта мата вживую).
        cancel() → True прерывает обработку (проверяется перед каждым чанком).
        vad=True — чанки без сигнала (см. _is_silent) модели не отдаются.

        Длинный файл режется не по жёсткой сетке, а в самом тихом месте
        последних секунд чанка — чтобы не разрезать слово на границе.
        """
        max_chunk = int(CHUNK_SEC * SR)
        words: list[Word] = []
        total = len(samples) / SR
        i = 0
        while i < len(samples):
            if cancel and cancel():
                raise Cancelled()
            seg = samples[i : i + max_chunk]
            is_last = i + max_chunk >= len(samples)
            if not is_last:
                cut = _quiet_cut(seg)         # тихая точка в хвосте чанка
                seg = seg[:cut]
            if vad and _is_silent(seg):       # нет сигнала — чанк не распознаём
                i += len(seg) if len(seg) else max_chunk
                if progress:
                    progress(min(i / SR, total), total)
                continue
            # обычный чанк — от 0.25 c; последний короткий хвост (>=50 мс) дополняем
            # тишиной до 0.25 c, чтобы не терять речь в самом конце файла
            if len(seg) >= SR // 4 or (is_last and len(seg) >= SR // 20):
                rec = seg
                if len(rec) < SR // 4:
                    rec = np.concatenate([rec, np.zeros(SR // 4 - len(rec), dtype=rec.dtype)])
                res = self._model.recognize(np.ascontiguousarray(rec), sample_rate=SR)
                off, seg_dur = i / SR, len(seg) / SR
                chunk_words = []
                for w in tokens_to_words(res.tokens or [], res.timestamps or [],
                                         getattr(res, "logprobs", None)):
                    if len(rec) > len(seg) and w.start > seg_dur:
                        continue             # слово целиком в дополненной тишине
                    cw = Word(w.word, round(w.start + off, 3), round(w.end + off, 3),
                              w.conf, [round(t + off, 3) for t in (w.char_times or [])])
                    chunk_words.append(cw)
                words.extend(chunk_words)
                if word_cb:
                    word_cb(chunk_words)
            i += len(seg) if len(seg) else max_chunk
            if progress:
                progress(min(i / SR, total), total)
        return words

    def transcribe_file(self, path: str | Path, progress=None, word_cb=None, cancel=None,
                        audio_index: int = 0) -> list[Word]:
        from .audio import run_ff  # понятная ошибка, если нет ffmpeg
        # f32le сразу в пайп: без временного wav на диске (запись+чтение ~230 МБ
        # на 2-часовом файле) и без int16-квантования по дороге к модели
        out = run_ff(["ffmpeg", "-v", "error", "-i", str(path),
                      "-map", "0:a:%d" % audio_index, "-ac", "1", "-ar", str(SR),
                      "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"])
        samples = np.frombuffer(out.stdout, dtype=np.float32).copy()   # своя копия —
        del out                                                        # отпустить bytes ffmpeg
        return self.transcribe_samples(samples, progress=progress, word_cb=word_cb, cancel=cancel)
