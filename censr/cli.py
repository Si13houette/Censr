# -*- coding: utf-8 -*-
"""CLI: python -m censr.cli файл_или_папка [...] [-o выход] [--beep]

Использует те же настройки, что и GUI (%APPDATA%\\Censr\\settings.json):
свой словарь/белый список, режим, слышимость краёв, папка вывода, путь к модели.
Флаги командной строки имеют приоритет над settings.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .settings import AUDIO_EXT, DEFAULT_SUFFIX


def _parse_tracks(spec: str | None) -> list[int] | None:
    """'all'/пусто → None (все дорожки). '1,3' → [0,2] (1-based на вход, 0-based наружу).

    Мусор и 0 — ошибка (ValueError), а не молчаливое «обработать все дорожки»."""
    if not spec or spec.strip().lower() == "all":
        return None
    out: list[int] = []
    for p in spec.split(","):
        p = p.strip()
        if not p.isdigit() or int(p) < 1:
            raise ValueError("--track: «%s» — нужен номер дорожки от 1 (или all)" % p)
        out.append(int(p) - 1)
    return out or None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):     # перенаправленный вывод на Windows — cp1251:
        try:                                    # имя файла с эмодзи роняло print весь батч
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="censr", description="Удаление мата из аудио")
    ap.add_argument("inputs", nargs="+", help="файлы или папки")
    ap.add_argument("-o", "--out-dir", default=None, help="выходная папка (по умолчанию из настроек / рядом)")
    ap.add_argument("--suffix", default=DEFAULT_SUFFIX)
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--beep", action="store_true", help="бип вместо тишины")
    grp.add_argument("--noise", action="store_true", help="негромкий шум вместо тишины")
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
    try:
        tracks = _parse_tracks(args.track)
    except ValueError as e:
        ap.error(str(e))                      # код возврата 2, как принято у argparse

    rc = 0
    files: list[Path] = []
    for inp in args.inputs:
        p = Path(inp)
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*"))
                      if f.suffix.lower() in AUDIO_EXT
                      # не подбирать собственные выходы прошлых запусков
                      and not (args.suffix and f.stem.endswith(args.suffix))]
        elif p.exists():
            files.append(p)
        else:
            print(f"не найдено: {p}", file=sys.stderr)
            rc = 1                              # опечатка в пути не должна давать код 0
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
    except Exception as e:
        print("Не удалось загрузить модель распознавания: %s\n"
              "Проверь папку модели (models/gigaam-v3-onnx) или --model-path." % e,
              file=sys.stderr)
        return 1
    det = ProfanityDetector(extra_words=set(s.extra_words), whitelist=set(s.whitelist))

    used: set[str] = set()                     # выходы этого запуска: a/x.mp3 и b/x.mp3
    for f in files:                            # при общем -o не должны затирать друг друга
        out_dir = Path(out_root) if out_root else f.parent
        dst = out_dir / f"{f.stem}{args.suffix}{f.suffix}"
        base, k = dst, 2
        # дедуп по РЕАЛЬНОМУ пути: один и тот же файл, поданный двумя путями
        # (абс./отн.), иначе дал бы два «разных» dst в одну точку — второй затёр бы первый
        while os.path.normcase(str(dst.resolve())) in used:
            dst = base.with_name(f"{base.stem} ({k}){base.suffix}")
            k += 1
        used.add(os.path.normcase(str(dst.resolve())))
        if dst.resolve() == f.resolve():       # --suffix "" без -o: не дать затереть исходник
            print(f"{f.name}: выход совпадает с исходником — пропущено "
                  f"(укажи -o или непустой --suffix)", file=sys.stderr)
            rc = 1
            continue
        try:
            rep = censor_file(f, dst, tr, det, mode=mode, zone_params=zone_params,
                              use_cache=use_cache, tracks=tracks, max_passes=max_passes)
        except (AudioError, OSError) as e:
            print(f"{f.name}: ошибка — {e}", file=sys.stderr)
            rc = 1
            continue
        if s.write_report:                     # как в GUI — отчёт пишется по настройке
            try:                               # сбой записи отчёта НЕ роняет успешную цензуру
                dst.with_suffix(".report.json").write_text(
                    json.dumps(rep.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError as e:
                print(f"{f.name}: предупреждение — отчёт не записан ({e})", file=sys.stderr)
        print(f"{f.name}: {rep.flagged_words} матных слов заглушено -> {dst}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
