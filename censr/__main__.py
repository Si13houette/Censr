# -*- coding: utf-8 -*-
"""Точка входа: с аргументами — CLI, без — GUI. Ошибки пишутся в censr_error.log."""
import sys
import traceback
from pathlib import Path


def _run():
    if len(sys.argv) > 1:
        from .cli import main
    else:
        from .gui import main
    return main()


try:
    sys.exit(_run())
except SystemExit:
    raise
except BaseException:
    tb = traceback.format_exc()
    try:
        import datetime
        log = Path(__file__).resolve().parent.parent / "censr_error.log"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:   # дописываем, не затирая прошлые
            f.write("\n===== %s =====\n%s\n" % (stamp, tb))
    except Exception:
        pass
    try:
        sys.stderr.write(tb)
        sys.stderr.flush()
        input("\nПрограмма упала. Ошибка записана в censr_error.log. Enter — выход.")
    except Exception:
        pass  # под pythonw консоли нет — ошибка уже записана в лог
    sys.exit(1)
