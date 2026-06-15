# -*- coding: utf-8 -*-
"""Точка входа для PyInstaller-сборки — эквивалент `python -m censr`.

Зеркалит censr/__main__.py, но с АБСОЛЮТНЫМИ импортами: PyInstaller
анализирует верхнеуровневый скрипт, а относительные импорты («from .gui»)
работают только при запуске модулем (`python -m censr`). Поведение то же:
без аргументов — GUI, с аргументами — CLI; фатальный сбой пишется в
censr_error.log рядом с программой и (без консоли) показывается MessageBox.
"""
import sys
import traceback


def _run():
    if len(sys.argv) > 1:
        from censr.cli import main
    else:
        from censr.gui import main
    return main()


try:
    sys.exit(_run())
except SystemExit:
    raise
except KeyboardInterrupt:
    sys.exit(130)
except BaseException:
    tb = traceback.format_exc()
    try:
        import datetime
        from censr.settings import app_base_dir
        log = app_base_dir() / "censr_error.log"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:
            f.write("\n===== %s =====\n%s\n" % (stamp, tb))
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "Censr не смог запуститься.\n\nПодробности — в файле "
                "censr_error.log рядом с программой.", "Censr", 0x10)
    except Exception:
        pass
    sys.exit(1)
