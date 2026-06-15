# -*- coding: utf-8 -*-
"""Точка входа: с аргументами — CLI, без — GUI. Ошибки пишутся в censr_error.log."""
import sys
import traceback


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
except KeyboardInterrupt:
    sys.exit(130)                  # Ctrl+C — не «падение», в лог не пишем
except BaseException:
    tb = traceback.format_exc()
    try:
        import datetime
        from .settings import app_base_dir
        # app_base_dir: рядом с .exe в сборке (раньше Path(__file__) указывал
        # внутрь _internal/_MEI — лог терялся)
        log = app_base_dir() / "censr_error.log"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log.open("a", encoding="utf-8") as f:   # дописываем, не затирая прошлые
            f.write("\n===== %s =====\n%s\n" % (stamp, tb))
    except Exception:
        pass
    try:
        sys.stderr.write(tb)
        sys.stderr.flush()
        if sys.stdin is not None and sys.stdin.isatty():   # не вешать .bat/планировщик
            input("\nПрограмма упала. Ошибка записана в censr_error.log. Enter — выход.")
        elif sys.platform == "win32":          # GUI без консоли: иначе отказ был бы немым
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "Censr не смог запуститься.\n\nПодробности — в файле "
                "censr_error.log рядом с программой.", "Censr", 0x10)   # MB_ICONERROR
    except Exception:
        pass  # под pythonw консоли нет — ошибка уже записана в лог
    sys.exit(1)
