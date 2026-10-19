"""Keep the user's launch command: python3 main.py, without shell activation."""
import os
import sys
from pathlib import Path


def runtime_path(base):
    return Path(base)/'.venv-local'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')


def enter_runtime():
    base=Path(__file__).resolve().parent
    runtime=runtime_path(base)
    # Compare prefixes, not resolved executable paths: venv executables may be symlinks.
    if Path(sys.prefix).resolve() != (base/'.venv-local').resolve():
        if runtime.exists():
            os.execv(str(runtime),[str(runtime),str(base/'main.py'),*sys.argv[1:]])
        print('Сначала один раз выполни в папке программы: python3 setup_local.py')
        print('После установки запускай как раньше: python3 main.py')
        raise SystemExit(1)
    try:
        import tkinter
    except ImportError:
        version=f'{sys.version_info.major}.{sys.version_info.minor}'
        print(f'В Python отсутствует Tkinter. Для Homebrew: brew install python-tk@{version}')
        print('Затем повтори: python3 setup_local.py')
        raise SystemExit(1)
    try:
        import dotenv
        import PIL
        import numpy
    except ImportError as exc:
        print(f'Не установлен модуль {exc.name}. Выполни: python3 setup_local.py')
        raise SystemExit(1)
