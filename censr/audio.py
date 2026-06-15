# -*- coding: utf-8 -*-
"""Декод/энкод и глушение интервалов на оригинальной дорожке.

Геометрия зон (какую часть слова глушить) — в audio_zone.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # не показывать окно консоли
FADE_S = 0.012         # короткий косинусный фейд: без щелчков, но края чёткие
BEEP_HZ = 1000.0
BEEP_LEVEL = 0.18      # амплитуда бипа (~-15 dBFS)
NOISE_LEVEL = 0.05     # амплитуда шума-заглушки (~-26 dBFS, негромкий)


class AudioError(RuntimeError):
    """Понятная ошибка работы с ffmpeg/ffprobe (вместо голого трейсбека)."""


def _ff_path(name: str) -> str:
    """ffmpeg/ffprobe: bundled рядом с .exe (сборка), иначе из PATH."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent / "ffmpeg" / (name + ".exe")
        if exe.exists():
            return str(exe)
    return name


# Живые дочерние процессы ffmpeg/ffprobe: чтобы отмена могла снять
# непрерываемый энкод (kill_active_ff), а не ждать его завершения.
_ACTIVE: set = set()
_ACTIVE_LOCK = threading.Lock()


def kill_active_ff() -> None:
    """Убить все запущенные ffmpeg/ffprobe (вызывается при отмене из GUI).

    Прибитый процесс даст ненулевой код возврата → run_ff поднимет AudioError,
    которую вызывающая сторона трактует как отмену (если ставила флаг cancel)."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


def run_ff(cmd: list[str], *, input: bytes | memoryview | None = None, text: bool = False,
           timeout: float | None = None):
    """Запуск ffmpeg/ffprobe с человекочитаемыми ошибками.

    timeout (сек) — для коротких probe-вызовов: подвисший процесс (битый файл,
    мёртвый сетевой путь) убивается, а не висит вечно."""
    cmd = list(cmd)
    cmd[0] = _ff_path(cmd[0])           # подменяем на bundled-бинарь в сборке
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=NO_WINDOW)
    except FileNotFoundError as e:
        raise AudioError(
            "Не найден «%s» в PATH. Установи ffmpeg: "
            "https://www.gyan.dev/ffmpeg/builds/" % cmd[0]) from e
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
    try:
        try:
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.communicate()
            raise AudioError("%s: не ответил за %s с (битый файл или недоступный путь?)"
                             % (cmd[0], timeout)) from e
        except BaseException:       # Ctrl+C / MemoryError: не оставлять процесс-сироту,
            proc.kill()             # пишущий в уже мёртвые пайпы
            raise
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
    if proc.returncode != 0:
        err_s = err.decode("utf-8", "replace") if isinstance(err, (bytes, bytearray)) else (err or "")
        raise AudioError("%s: ошибка (%s)\n%s" % (cmd[0], proc.returncode,
                                                  err_s.strip()[-800:]))
    res = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if text:
        res = subprocess.CompletedProcess(
            cmd, proc.returncode,
            out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else out,
            err.decode("utf-8", "replace") if isinstance(err, (bytes, bytearray)) else err)
    return res


def ffmpeg_available() -> bool:
    """Есть ли ffmpeg (PATH или bundled) — для строки статуса в GUI."""
    import shutil
    p = _ff_path("ffmpeg")
    return bool(shutil.which(p)) or Path(p).exists()


@dataclass
class AudioMeta:
    sample_rate: int
    channels: int
    codec: str
    bit_rate: int | None


@dataclass
class AudioStream:
    """Одна аудиодорожка контейнера (для выбора, что обрабатывать)."""
    index: int                 # порядковый индекс среди АУДИО-потоков (0-based, для -map 0:a:N)
    codec: str
    sample_rate: int
    channels: int
    bit_rate: int | None
    language: str | None = None
    title: str | None = None
    dis_default: bool = False      # disposition: дорожка по умолчанию
    dis_forced: bool = False       # disposition: forced

    def meta(self) -> "AudioMeta":
        return AudioMeta(self.sample_rate, self.channels, self.codec, self.bit_rate)

    def label(self) -> str:
        ch = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(self.channels, "%dch" % self.channels)
        lang = self.language if self.language and self.language.lower() not in ("und", "unknown") else None
        parts = [p for p in (lang, self.title, self.codec, ch) if p]
        if self.bit_rate:
            parts.append("%d кбит/с" % round(self.bit_rate / 1000))
        return " · ".join(parts)


def probe(path: str | Path) -> AudioMeta:
    out = run_ff(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(path)],
        text=True, timeout=15,
    )
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise AudioError("В файле нет аудиодорожки: %s" % path)
    s = streams[0]
    br = s.get("bit_rate")
    return AudioMeta(int(s["sample_rate"]), int(s["channels"]), s["codec_name"],
                     int(br) if br else None)


# поля ffprobe для разбора аудиодорожки — единый источник для list_audio_streams
# и GUI-пробы (_ProbeTask): копии парсера расходились бы молча
STREAM_ENTRIES = ("stream=codec_name,sample_rate,channels,bit_rate"
                  ":stream_tags=language,title:stream_disposition=default,forced")


def stream_from_probe(i: int, s: dict) -> AudioStream:
    """dict из ffprobe-json (STREAM_ENTRIES) → AudioStream."""
    br = s.get("bit_rate")
    tags = s.get("tags") or {}
    dis = s.get("disposition") or {}
    return AudioStream(
        index=i, codec=s.get("codec_name", ""),
        sample_rate=int(s.get("sample_rate") or 0),
        channels=int(s.get("channels") or 0),
        bit_rate=int(br) if br else None,
        language=tags.get("language"), title=tags.get("title"),
        dis_default=bool(dis.get("default", 0)),
        dis_forced=bool(dis.get("forced", 0)))


def list_audio_streams(path: str | Path, timeout: float | None = 15) -> list[AudioStream]:
    """Все аудиодорожки контейнера по порядку (для выбора, что обрабатывать)."""
    out = run_ff(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         STREAM_ENTRIES, "-of", "json", str(path)],
        text=True, timeout=timeout,
    )
    streams = json.loads(out.stdout).get("streams") or []
    return [stream_from_probe(i, s) for i, s in enumerate(streams)]


def decode(path: str | Path, meta: AudioMeta, index: int = 0) -> np.ndarray:
    """Оригинальное качество: float32, native sample rate, все каналы. Shape (n, ch).

    index — порядковый номер аудиодорожки (0:a:index)."""
    out = run_ff(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:%d" % index,
         "-ac", str(meta.channels), "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"],
    )
    data = np.frombuffer(out.stdout, dtype=np.float32)
    if data.size == 0:                  # пустой поток — раньше молча «успешно» копировался
        raise AudioError("Пустая аудиодорожка (0 сэмплов) — битый или обрезанный файл: %s"
                         % path)
    if meta.channels < 1 or data.size % meta.channels:
        raise AudioError("Не удалось разобрать каналы (%d) дорожки: %s"
                         % (meta.channels, path))
    # своя запись-владелец: огромный bytes из ffmpeg (для 2-ч файла — гигабайты)
    # освобождается сразу, а массив становится writable — apply_censor не копирует повторно
    arr = data.reshape(-1, meta.channels).copy()
    del data, out
    return arr


_ENCODERS = {
    "mp3": ["-c:a", "libmp3lame"],
    "aac": ["-c:a", "aac"],
    "ac3": ["-c:a", "ac3"],
    "eac3": ["-c:a", "eac3"],
    "dts": ["-c:a", "flac"],                # у DTS нет вменяемого энкодера в ffmpeg
    #                                         (dca экспериментальный) — без потерь во flac
    "opus": ["-c:a", "libopus"],
    "vorbis": ["-c:a", "libvorbis"],
    "flac": ["-c:a", "flac"],
    "alac": ["-c:a", "alac"],
    "pcm_s16le": ["-c:a", "pcm_s16le"],
    "pcm_s24le": ["-c:a", "pcm_s24le"],     # без явного -c:a .wav упал бы в 16 бит
    "pcm_s32le": ["-c:a", "pcm_s32le"],
    "pcm_f32le": ["-c:a", "pcm_f32le"],
    "pcm_u8": ["-c:a", "pcm_u8"],
}


def _audio_args(meta: AudioMeta) -> list[str]:
    """Аргументы кодека/битрейта для энкода аудио в исходном формате."""
    args = list(_ENCODERS.get(meta.codec, []))  # неизвестный кодек — ffmpeg выберет по расширению
    # ac3/eac3 — честный CBR: без -b:a ffmpeg молча уронит 640k → 448k/96k
    if meta.bit_rate and meta.codec in ("mp3", "aac", "opus", "vorbis", "ac3", "eac3"):
        args += ["-b:a", str(meta.bit_rate)]
    return args


# контейнеры, умеющие обложку-attached_pic (для переноса cover art)
_COVER_OK = {".mp3", ".flac", ".m4a", ".mp4", ".m4b"}


def _pcm_view(samples: np.ndarray) -> memoryview:
    """PCM float32 как bytes-view для stdin ffmpeg — без .tobytes():
    тот делал полную копию (~2.7 ГБ лишнего пика на 2-часовом стерео)."""
    return memoryview(np.ascontiguousarray(samples, dtype=np.float32)).cast("B")


def encode(samples: np.ndarray, meta: AudioMeta, dst: str | Path,
           src: str | Path | None = None) -> None:
    """PCM → dst в исходном кодеке/битрейте.

    src — файл-донор метаданных: глобальные теги (артист/альбом/…) и
    обложка-attached_pic переносятся в выход (быстрый путь раньше молча
    терял ID3/обложку). Для временных дорожек мультитрек-пути src не нужен —
    метаданные сохранит финальный мультиплекс."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(meta.sample_rate),
           "-ac", str(meta.channels), "-i", "pipe:0"]
    extra: list[str] = []
    if src is not None:
        cmd += ["-i", str(src)]
        extra += ["-map", "0:a", "-map_metadata", "1"]
        if Path(dst).suffix.lower() in _COVER_OK:
            extra += ["-map", "1:v?", "-c:v", "copy"]   # обложка (если есть)
    run_ff(cmd + [*_audio_args(meta), *extra, str(dst)], input=_pcm_view(samples))


def write_wav(samples: np.ndarray, meta: AudioMeta, dst: str | Path) -> None:
    """Быстрый PCM-wav (для повторного распознавания уже заглушенного звука)."""
    run_ff(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(meta.sample_rate),
         "-ac", str(meta.channels), "-i", "pipe:0", "-c:a", "pcm_s16le", str(dst)],
        input=_pcm_view(samples),
    )


def has_video_stream(path: str | Path) -> bool:
    """Есть ли в файле настоящая видеодорожка (а не обложка-картинка)."""
    out = run_ff(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        text=True, timeout=15,
    )
    try:
        streams = json.loads(out.stdout).get("streams") or []
    except Exception:
        return False
    return any((s.get("disposition") or {}).get("attached_pic", 0) == 0 for s in streams)


_CONTAINER_EXT = {
    "aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac",
    "alac": "m4a", "ac3": "ac3", "eac3": "eac3",   # dts кодируется во flac → контейнер mka
    "pcm_s16le": "wav", "pcm_s24le": "wav", "pcm_s32le": "wav",
    "pcm_f32le": "wav", "pcm_u8": "wav",
}


def temp_ext_for(codec: str) -> str:
    """Расширение временного файла для зацензуренной дорожки (mka — универсально)."""
    return _CONTAINER_EXT.get(codec, "mka")


def mux_audio_tracks(src: str | Path, dst: str | Path, plan: list[dict],
                     copy_video: bool = True) -> None:
    """Собрать выходной файл из исходного видео и набора аудиодорожек.

    plan — по одному элементу на выходную аудиодорожку, в нужном порядке:
      {"copy": i}                  — скопировать i-ю исходную аудиодорожку как есть
      {"file": path, "language": l, "title": t, "disposition": "default+forced"}
                                   — взять аудио из готового (зацензуренного) файла
    Видео, субтитры, вложения (шрифты mkv) и копируемые дорожки идут без
    перекодирования (-c copy) — раньше сабы и вложения молча выбрасывались."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    for item in plan:
        if "file" in item:
            cmd += ["-i", str(item["file"])]
    maps: list[str] = ["-map", "0:v?"] if copy_video else []
    meta_args: list[str] = []
    in_idx = 1
    for out_a, item in enumerate(plan):
        if "copy" in item:
            maps += ["-map", "0:a:%d" % item["copy"]]
        else:
            maps += ["-map", "%d:a:0" % in_idx]
            in_idx += 1
            if item.get("language"):
                meta_args += ["-metadata:s:a:%d" % out_a, "language=%s" % item["language"]]
            if item.get("title"):
                meta_args += ["-metadata:s:a:%d" % out_a, "title=%s" % item["title"]]
            if item.get("disposition"):
                meta_args += ["-disposition:a:%d" % out_a, item["disposition"]]
    maps += ["-map", "0:s?", "-map", "0:t?"]    # субтитры и вложения — копией
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() in (".mp4", ".m4a", ".m4v", ".mov"):
        meta_args += ["-strict", "-2"]          # flac-в-mp4 (наш dts→flac) — experimental
    run_ff(cmd + maps + ["-c", "copy", "-map_metadata", "0"] + meta_args + [str(dst)])


def merge_zones(zones: list[tuple[float, float]], gap: float = 0.05) -> list[tuple[float, float]]:
    if not zones:
        return []
    zones = sorted(zones)
    merged = [list(zones[0])]
    for s, e in zones[1:]:
        if s <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _envelope(n: int, sr: int, zones: list[tuple[float, float]], fade_s: float = FADE_S) -> np.ndarray:
    """Огибающая громкости: 1 — оригинал, 0 — заглушено, косинусные переходы."""
    env = np.ones(n, dtype=np.float32)
    fade = max(int(fade_s * sr), 1)
    ramp = (1 + np.cos(np.linspace(0, np.pi, fade, dtype=np.float32))) / 2  # 1 → 0
    for zs, ze in zones:
        i0, i1 = max(int(zs * sr), 0), min(int(ze * sr), n)
        if i1 <= i0:
            continue
        env[i0:i1] = 0.0
        f0 = min(fade, i0)                       # фейд-аут перед зоной
        if f0 > 0:
            env[i0 - f0:i0] = np.minimum(env[i0 - f0:i0], ramp[-f0:])
        f1 = min(fade, n - i1)                   # фейд-ин после зоны
        if f1 > 0:                               # ramp[-f1:][::-1]: старт у зоны = 0 (без щелчка
            env[i1:i1 + f1] = np.minimum(env[i1:i1 + f1], ramp[-f1:][::-1])  # даже при обрезке у конца файла)
    return env


def apply_censor(samples: np.ndarray, sr: int, zones: list[tuple[float, float]],
                 mode: str = "silence", fade_s: float = FADE_S) -> np.ndarray:
    """Глушит зоны (sec) в samples (n, ch). mode: silence | beep | noise.

    Работает по зонам, а не по всему файлу: огибающая, бип и шум считаются только
    в окрестности заглушаемых интервалов (плюс поля фейда). Для 2-часового файла
    с парой секунд мата это O(длины зон), а не O(всего сигнала) — без гигабайтных
    временных массивов и прохода по всему звуку (раньше бип/шум генерились на весь
    файл). Тишина и бип бит-в-бит совпадают со старым полнофайловым вариантом.

    Возвращает float32 той же формы: тот же массив, если он уже writable float32,
    иначе ровно одна копия."""
    out = np.ascontiguousarray(samples, dtype=np.float32)   # тот же массив, если уже float32
    if not out.flags.writeable:                # decode() мог дать read-only (np.frombuffer)
        out = out.copy()                       # одна копия только когда иначе нельзя
    mono_in = out.ndim == 1                     # допускаем 1-D вход (вернём 1-D)
    if mono_in:
        out = out[:, None]
    n = out.shape[0]
    fade = max(int(fade_s * sr), 1)
    ramp = (1 + np.cos(np.linspace(0, np.pi, fade, dtype=np.float32))) / 2   # 1 → 0
    rng = np.random.default_rng() if mode == "noise" else None
    k = max(int(sr * 0.0005), 1)               # лёгкий low-pass для мягкого шума
    # слияние гарантирует, что окна соседних зон (±fade) не перекрываются,
    # поэтому каждую зону можно обработать независимо
    for zs, ze in merge_zones(zones, gap=max(0.05, 2 * fade_s)):
        i0, i1 = max(int(zs * sr), 0), min(int(ze * sr), n)
        if i1 <= i0:
            continue
        w0, w1 = max(i0 - fade, 0), min(i1 + fade, n)
        env = np.ones(w1 - w0, dtype=np.float32)
        env[i0 - w0:i1 - w0] = 0.0
        f0 = i0 - w0                           # фейд-аут перед зоной (1 → 0)
        if f0 > 0:
            env[:f0] = ramp[-f0:]
        f1 = w1 - i1                           # фейд-ин после зоны (0 → 1, без щелчка у EOF)
        if f1 > 0:
            env[i1 - w0:w1 - w0] = ramp[-f1:][::-1]
        seg = out[w0:w1]
        seg *= env[:, None]
        if mode == "beep":
            # фаза строго в float64: float32 теряет целые уже после 2^24-го сэмпла
            # (≈6 мин при 48 кГц) — бип на длинных файлах вырождался в тишину
            t = np.arange(w0, w1, dtype=np.float64) / sr      # абсолютное время — фаза непрерывна
            beep = (BEEP_LEVEL * np.sin(2 * np.pi * BEEP_HZ * t)).astype(np.float32)
            seg += (beep * (1.0 - env))[:, None]              # кроссфейд бипа той же огибающей
        elif mode == "noise":
            noise = rng.standard_normal(w1 - w0).astype(np.float32)
            if 1 < k <= w1 - w0:    # окно короче ядра (доли мс) — convolve вернул бы массив длины k
                noise = np.convolve(noise, np.ones(k, dtype=np.float32) / k, mode="same")
            noise /= (float(noise.std()) + 1e-9)
            seg += (NOISE_LEVEL * noise * (1.0 - env))[:, None]   # шум только в заглушенных зонах
    return out[:, 0] if mono_in else out
