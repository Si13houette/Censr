# -*- coding: utf-8 -*-
"""Кэш ASR-транскрипта.

Распознавание занимает ~90% времени. Транскрипт зависит только от аудио и
модели (НЕ от словаря/режима/краёв), поэтому его можно сохранить и переприменять
другие настройки глушения мгновенно, без повторного прогона модели.

Ключ кэша = версия + абсолютный путь + размер + mtime + id модели.
Любая ошибка чтения/записи кэша молча игнорируется (кэш — необязательная
оптимизация, не источник истины).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .asr import Word
from .settings import _config_dir

CACHE_VERSION = 1


def _cache_dir() -> Path:
    return _config_dir() / "cache"


def _key(src: Path, model_id: str, track: int) -> str:
    st = src.stat()
    raw = "%d|%s|%d|%d|%s|a%d" % (CACHE_VERSION, src.resolve(), st.st_size,
                                  st.st_mtime_ns, model_id, track)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _path_for(src: Path, model_id: str, track: int) -> Path:
    return _cache_dir() / (_key(src, model_id, track) + ".json")


def load_words(src: str | Path, model_id: str, track: int = 0) -> list[Word] | None:
    """Транскрипт из кэша или None, если его нет/он повреждён/файл изменился."""
    try:
        p = _path_for(Path(src), model_id, track)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Word(w["word"], w["start"], w["end"],
                     w.get("conf", 1.0), w.get("char_times")) for w in data["words"]]
    except Exception:
        return None


def save_words(src: str | Path, model_id: str, words: list[Word], track: int = 0) -> None:
    """Сохранить транскрипт в кэш (ошибки игнорируются)."""
    try:
        p = _path_for(Path(src), model_id, track)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"words": [{"word": w.word, "start": w.start, "end": w.end,
                           "conf": w.conf, "char_times": w.char_times} for w in words]}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def clear() -> int:
    """Удалить весь кэш. Возвращает число удалённых файлов."""
    n = 0
    try:
        for f in _cache_dir().glob("*.json"):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n
