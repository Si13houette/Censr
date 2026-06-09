# -*- coding: utf-8 -*-
"""Настройки приложения: JSON рядом с профилем пользователя."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    edge_keep_pct: int = 10            # слышимость краёв слова, % (5..25)
    extra_words: list[str] = field(default_factory=list)   # свой запрещённый список
    whitelist: list[str] = field(default_factory=list)     # никогда не глушить
    model_dir: str = ""                # пусто — скачать с HF в кэш
    use_cache: bool = True             # кэшировать транскрипт (переприменение без ASR)
    full_mute: bool = False            # максимальная очистка — глушить слово целиком, без краёв
    thorough_clean: bool = False       # тщательная очистка — несколько проходов распознавания

    @classmethod
    def load(cls) -> "Settings":
        p = _config_dir() / "settings.json"
        if p.exists():
            try:
                return cls(**{k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                              if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        d = _config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
