# -*- coding: utf-8 -*-
"""CLI: python -m censr.cli файл_или_папка [...] [-o выход] [--beep]

Использует те же настройки, что и GUI (%APPDATA%\\Censr\\settings.json):
свой словарь/белый список, режим, слышимость краёв, папка вывода, путь к модели.
Флаги командной строки имеют приоритет над settings.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".mp4", ".mkv", ".webm"}


def _parse_tracks(spec: str | None) -> list[int] | None:
    """'all'/пусто → None (все дорожки). '1,3' → [0,2] (1-based на вход, 0-based наружу)."""
    if not spec or spec.strip().lower() == "all":
        return None
    out = [int(p) - 1 for p in spec.split(",") if p.strip().isdigit() and int(p) >= 1]
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser(prog="censr", description="Удаление мата из аудио")
    ap.add_argument("inputs", nargs="+", help="файлы или папки")
    ap.add_argument("-o", "--out-dir", default=None, help="выходная папка (по умолчанию из настроек / рядом)")
    ap.add_argument("--suffix", default="_censr")
    ap.add_argument("--beep", action="store_true", help="бип вместо тишины")
    ap.add_argument("--noise", action="store_true", help="негромкий шум вместо тишины")
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--no-cache", action="store_true",
                    help="не использовать кэш транскрипта (всегда заново распознавать)")
    ap.add_argument("--track", default="all",
                    help="какие аудиодорожки обрабатывать: all или список через запятую, "
                         "нумерация с 1 (например 1,3). Невыбранные копируются без цензуры.")
    ap.add_argument("--full", action="store_true",
                    help="максимальная очистка: глушить слово целиком, без слышимых краёв")
    ap.add_argument("--thorough", action="store_true",
                    help="тщательная очистка: несколько проходов распознавания (медленнее)")
    args = ap.parse_args()
    tracks = _parse_tracks(args.track)

    files: list[Path] = []
    for inp in args.inputs:
        p = Path(inp)
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in AUDIO_EXT]
        elif p.exists():
            files.append(p)
        else:
            print(f"не найдено: {p}", file=sys.stderr)
    if not files:
        print("нет входных файлов", file=sys.stderr)
        return 1

    from .asr import Transcriber
    from .audio import AudioError
    from .audio_zone import ZoneParams
    from .detector import ProfanityDetector
    from .pipeline import censor_file
    from .settings import Settings, default_model_dir

    s = Settings.load()
    mode = "beep" if args.beep else ("noise" if args.noise else s.mode)   # флаги имеют приоритет
    out_root = args.out_dir or s.output_dir            # -o имеет приоритет
    model_path = args.model_path or s.model_dir or default_model_dir()
    zone_params = ZoneParams.from_edge_pct(s.edge_keep_pct, full=s.full_mute or args.full)
    use_cache = s.use_cache and not args.no_cache       # --no-cache отключает кэш
    max_passes = 3 if (s.thorough_clean or args.thorough) else 1

    try:
        tr = Transcriber(model_path=model_path)
    except ModuleNotFoundError as e:
        print(f"Не установлен модуль «{e.name}». Выполни: pip install -r requirements.txt", file=sys.stderr)
        return 1
    det = ProfanityDetector(extra_words=set(s.extra_words), whitelist=set(s.whitelist))

    rc = 0
    for f in files:
        out_dir = Path(out_root) if out_root else f.parent
        dst = out_dir / f"{f.stem}{args.suffix}{f.suffix}"
        try:
            rep = censor_file(f, dst, tr, det, mode=mode, zone_params=zone_params,
                              use_cache=use_cache, tracks=tracks, max_passes=max_passes)
        except AudioError as e:
            print(f"{f.name}: ошибка — {e}", file=sys.stderr)
            rc = 1
            continue
        dst.with_suffix(".report.json").write_text(
            json.dumps(rep.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{f.name}: {rep.flagged_words} матных слов заглушено -> {dst}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
