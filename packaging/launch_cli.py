# -*- coding: utf-8 -*-
"""Точка входа CLI-сборки (Censr-cli.exe, с консолью)."""
import sys
import traceback


def _main():
    from censr.cli import main
    return main()


try:
    sys.exit(_main())
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    try:
        input("\nОшибка. Enter — выход.")
    except Exception:
        pass
    sys.exit(1)
