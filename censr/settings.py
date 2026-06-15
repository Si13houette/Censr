# -*- coding: utf-8 -*-
"""Настройки приложения: JSON рядом с профилем пользователя."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# Общие константы GUI и CLI (раньше дублировались в gui.py/cli.py и могли
# разойтись — паттерн «рассинхрон справочников» из lessons.md)
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma",
             ".mp4", ".mkv", ".webm"}
DEFAULT_SUFFIX = "_censr"


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Censr"


def app_base_dir() -> Path:
    """Корень программы: рядом с .exe (сборка) или корень репозитория (dev)."""
    if getattr(sys, "frozen", False):          # запущено из PyInstaller-сборки
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_model_dir() -> str | None:
    """Папка models/gigaam-v3-onnx рядом с программой, если модель там лежит."""
    cand = app_base_dir() / "models" / "gigaam-v3-onnx"
    return str(cand) if (cand / "v3_ctc.int8.onnx").exists() else None


@dataclass
class Settings:
    mode: str = "silence"              # silence | beep | noise
    output_dir: str = ""               # пусто — рядом с исходником
    edge_keep_pct: int = 12            # слышимость краёв слова, % — уровни GUI: 5 | 12 | 20
    extra_words: list[str] = field(default_factory=list)   # свой запрещённый список
    whitelist: list[str] = field(default_factory=list)     # никогда не глушить
    model_dir: str = ""                # пусто — скачать с HF в кэш
    use_cache: bool = True             # кэшировать транскрипт (переприменение без ASR)
    full_mute: bool = False            # максимальная очистка — глушить слово целиком, без краёв
    thorough_clean: bool = False       # тщательная очистка — несколько проходов распознавания
    write_report: bool = False         # писать <имя>.report.json (нужен кнопке «проверить»)

    @classmethod
    def load(cls) -> "Settings":
        p = _config_dir() / "settings.json"
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                defaults = cls()
                kw = {}
                for f in fields(cls):              # валидация типов: рукотворный
                    if f.name not in raw:          # settings.json не должен ломать
                        continue                   # детектор («слово» → набор букв)
                    v = raw[f.name]
                    want = type(getattr(defaults, f.name))
                    if want is list:
                        if isinstance(v, list) and all(isinstance(x, str) for x in v):
                            kw[f.name] = v
                    elif want is bool:
                        if isinstance(v, bool):
                            kw[f.name] = v
                    elif want is int:
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            kw[f.name] = int(v)      # принять и 12.0 из ручной правки JSON
                    elif isinstance(v, want):
                        kw[f.name] = v
                s = cls(**kw)
                if s.mode not in ("silence", "beep", "noise"):
                    s.mode = "silence"
                return s
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        d = _config_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "settings.json"
        tmp = p.with_suffix(".json.tmp")           # атомарно: краш посреди записи
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1),
                       encoding="utf-8")           # не должен сбрасывать словарь
        os.replace(tmp, p)
