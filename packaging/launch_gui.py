# -*- coding: utf-8 -*-
"""Точка входа GUI-сборки (Censr.exe, без консоли).

Импортируем censr.gui напрямую: в замороженной сборке нет модуля
censr.__main__, поэтому runpy-подход не работает. Ошибки пишем в
censr_error.log рядом с .exe — консоли у оконного приложения нет.
"""
import sys
import traceback
from pathlib import Path


def _main():
    from censr.gui import main
    return main()


try:
    sys.exit(_main())
except SystemExit:
    raise
except BaseException:
    tb = traceback.format_exc()
    try:
        import datetime
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        log = base / "censr_error.log"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:
            f.write("\n===== %s =====\n%s\n" % (stamp, tb))
    except Exception:
        pass
    sys.exit(1)
