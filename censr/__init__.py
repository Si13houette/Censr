# -*- coding: utf-8 -*-
"""Censr.

Защита для запуска без консоли (pythonw / собранный GUI-exe): там
sys.stdout и sys.stderr равны None. Сторонние библиотеки (tqdm внутри
huggingface_hub при скачивании модели) пишут в stdout и падают с
AttributeError: 'NoneType' object has no attribute 'write'. Глушим их
прогресс-бары и подставляем безопасный «пустой» поток.

Делается на уровне пакета, чтобы сработать до импорта asr/huggingface_hub
во всех точках входа (GUI, CLI, frozen).
"""
import os as _os
import sys as _sys

# Прогресс-бары и телеметрия HuggingFace — выключить (нет консоли, не нужны)
_os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
_os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


class _NullStream:
    """Заглушка для stdout/stderr, когда их нет (pythonw)."""

    def write(self, *_a, **_k):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False


if _sys.stdout is None:
    _sys.stdout = _NullStream()
if _sys.stderr is None:
    _sys.stderr = _NullStream()
