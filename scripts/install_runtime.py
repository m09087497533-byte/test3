"""Select a supported Mac Python without deleting an existing installation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

PROBE = """import json,platform,sys,sysconfig
print(json.dumps(dict(version=list(sys.version_info[:2]),implementation=platform.python_implementation(),machine=platform.machine(),macos=platform.mac_ver()[0],free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')))))
"""


def runtime_info(python):
    try:
        result = subprocess.run([str(python), '-c', PROBE], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def compatible(info):
    if not info or info['implementation'] != 'CPython' or info.get('free_threaded'):
        return False
    return info['version'] == [3, 12] if sys.platform == 'darwin' else info['version'] >= [3, 10]


def choose_python(root):
    for name in ('.venv-py312', '.venv'):
        python = root / name / 'bin/python'
        if python.is_file() and compatible(runtime_info(python)):
            return python
    candidates = [sys.executable]
    if sys.platform == 'darwin':
        candidates = [shutil.which('python3.12'), '/opt/homebrew/opt/python@3.12/bin/python3.12',
                      '/usr/local/opt/python@3.12/bin/python3.12', sys.executable]
    python = next((Path(p) for p in candidates if p and compatible(runtime_info(p))), None)
    if python is None and sys.platform == 'darwin':
        brew = shutil.which('brew')
        if not brew:
            raise RuntimeError('Для распознавания нужен обычный CPython 3.12. Установите Python 3.12 с python.org или через Homebrew: brew install python@3.12. Старая .venv сохранена.')
        print('Устанавливаем совместимый Python 3.12 через Homebrew…', file=sys.stderr, flush=True)
        subprocess.run([brew, 'install', 'python@3.12'], check=True, stdout=sys.stderr)
        prefix = subprocess.run([brew, '--prefix', 'python@3.12'], check=True, capture_output=True, text=True).stdout.strip()
        python = Path(prefix) / 'bin/python3.12'
        if not compatible(runtime_info(python)):
            raise RuntimeError('Homebrew не предоставил совместимый CPython 3.12. Старая установка сохранена.')
    if python is None:
        raise RuntimeError('Нужен CPython 3.10+ для запуска Кадра.')
    folder = root / ('.venv-py312' if (root / '.venv').exists() else '.venv')
    if folder.exists():
        raise RuntimeError(f'{folder.name} уже существует, но её Python несовместим или повреждён. Папка сохранена; перенесите её в резервную копию и повторите запуск.')
    print(f'Создаём {folder.name}; прежние окружения и материалы сохраняются…', file=sys.stderr, flush=True)
    subprocess.run([str(python), '-m', 'venv', str(folder)], check=True, stdout=sys.stderr)
    return folder / 'bin/python'


def installed(python, imports):
    return subprocess.run([str(python), '-c', imports], capture_output=True).returncode == 0


def install(python, root, requirements):
    result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-input', '--only-binary=:all:',
                             '-r', requirements], cwd=root, capture_output=True, text=True)
    print(result.stdout, file=sys.stderr, end='')
    print(result.stderr, file=sys.stderr, end='')
    return result.returncode == 0


def prepare_runtime(root, refresh=False):
    root = Path(root).resolve()
    python = choose_python(root)
    info = runtime_info(python)
    if info:
        print(f"Python {'.'.join(map(str, info['version']))}, {info['machine']}, macOS {info['macos'] or 'не используется'}", file=sys.stderr, flush=True)
        if sys.platform == 'darwin' and info['macos'] and int(info['macos'].split('.')[0]) < 11:
            raise RuntimeError('Эта сборка локальных моделей требует macOS 11 или новее. Не обновляйте macOS без проверки совместимости своего Mac; прежняя установка сохранена.')
    if refresh or not installed(python, 'import fastapi, uvicorn, PIL, multipart'):
        if not install(python, root, 'requirements.txt'):
            raise RuntimeError('Не удалось установить основные библиотеки. Причина pip показана выше.')
    for filename, imports, label in [
        ('requirements-speech.txt', 'import faster_whisper', 'Распознавание речи'),
        ('requirements-semantic.txt', 'import fastembed', 'Смысловая модель'),
    ]:
        if refresh or not installed(python, imports):
            print(f'{label}: устанавливаем библиотеки…', file=sys.stderr, flush=True)
            if not install(python, root, filename):
                print(f'{label}: установка не завершена. Причина pip показана выше. «No matching distribution» означает отсутствие подходящего пакета для Python, macOS и архитектуры в используемом источнике pip; повторный запуск без исправления причины не поможет. SRT/VTT остаётся доступен.', file=sys.stderr, flush=True)
    return python


if __name__ == '__main__':
    try:
        print(prepare_runtime(Path(sys.argv[1] if len(sys.argv) > 1 else '.')))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Не удалось подготовить Кадр: {exc}', file=sys.stderr)
        sys.exit(1)
