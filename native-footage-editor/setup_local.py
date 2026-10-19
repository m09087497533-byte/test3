"""One-time setup. No global pip installs and no alteration of the user's python3."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parent


def find_python():
    candidates=[]
    # Prefer a stable supported interpreter for the local ML dependencies.
    for prefix in ['/opt/homebrew','/usr/local']:
        candidates.append(str(Path(prefix)/'opt/python@3.12/bin/python3.12'))
    candidates.extend([shutil.which('python3.12'),shutil.which('python3.11'),sys.executable])
    for executable in dict.fromkeys(x for x in candidates if x):
        if not Path(executable).exists():continue
        test=subprocess.run([executable,'-c',
            'import sys,json; import tkinter; print(json.dumps(list(sys.version_info[:2])))'],
            capture_output=True,text=True)
        if test.returncode!=0:continue
        try:
            if tuple(json.loads(test.stdout.strip())) in {(3,11),(3,12)}:return executable
        except ValueError:pass
    return None


def main():
    python=find_python()
    if python is None:
        print('Нужен Python 3.12 с Tkinter для локальных моделей.')
        if sys.platform=='darwin':
            print('Один раз выполни: brew install python@3.12 python-tk@3.12 ffmpeg')
        else:
            print('Установи Python 3.12, соответствующий tkinter и FFmpeg средствами своей ОС.')
        print('Затем снова: python3 setup_local.py')
        return 1
    environment=BASE/'.venv-local'
    executable=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if environment.exists():
        if not executable.exists():
            print('Окружение .venv-local повреждено. Переименуй эту папку и повтори установку.')
            return 1
        probe=subprocess.run([str(executable),'-c','import tkinter; import sys; assert (3,11)<=sys.version_info[:2]<=(3,12)'],capture_output=True)
        if probe.returncode:
            print('Старое .venv-local несовместимо. Переименуй только .venv-local и повтори установку.')
            return 1
    else:
        subprocess.run([python,'-m','venv',str(environment)],check=True)
    subprocess.run([str(executable),'-m','pip','install','-r',str(BASE/'requirements.txt')],check=True)
    subprocess.run([str(executable),'-c','import tkinter, dotenv, PIL, numpy, torch, transformers, faster_whisper'],check=True)
    if not (BASE/'.env').exists():
        shutil.copyfile(BASE/'.env.example',BASE/'.env')
        print('Создан .env: добавь ключ Pexels и/или Pixabay.')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        print('Не найден FFmpeg/ffprobe в PATH. На Mac: brew install ffmpeg')
    print('Готово. Запуск из этой папки: python3 main.py')
    print('Активация окружения и Start.command не нужны. Модели скачаются при первом использовании.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print('Установка не завершена. Смотри сообщение выше; после исправления повтори python3 setup_local.py.')
        raise SystemExit(exc.returncode)
