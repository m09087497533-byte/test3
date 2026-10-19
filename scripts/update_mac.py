"""Copy a downloaded update into an existing Mac installation, preserving media."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

CODE_DIRS={'studio','static','scripts','tests','docs'}
CODE_FILES={'AGENTS.md','run.py','README.md','requirements.txt','requirements-dev.txt','requirements-ui.txt','requirements-ai.txt','requirements-speech.txt','requirements-semantic.txt','constraints.txt','Обновить Кадр.command','Запустить Кадр.command'}


def update_files(source,target):
    source=Path(source).resolve();target=Path(target).resolve()
    if not (target/'run.py').is_file() or not any((target/name/'bin/python').is_file() for name in ('.venv','.venv-py312')):
        raise ValueError('Выберите старую папку Кадра, где лежат run.py и установленная .venv или .venv-py312.')
    if source==target:return
    for name in CODE_DIRS:
        if (target/name).is_symlink():raise ValueError('Папки программы не должны быть символическими ссылками.')
    selected=[]
    for path in source.rglob('*'):
        rel=path.relative_to(source)
        if not path.is_file() or path.is_symlink() or any(p.startswith('.') or p=='__pycache__' for p in rel.parts) or path.suffix=='.pyc':continue
        if rel.parts[0] in CODE_DIRS or (len(rel.parts)==1 and rel.name in CODE_FILES):selected.append((path,rel))
    backup=target/'.data'/'code-backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    for path,rel in selected:
        dest=target/rel
        if any(p.is_symlink() for p in [dest,*dest.parents] if p!=target and target in p.parents):
            raise ValueError('В пути обновления обнаружена символическая ссылка.')
    for path,rel in selected:
        dest=target/rel
        if dest.is_file():
            old=backup/rel;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,old)
        dest.parent.mkdir(parents=True,exist_ok=True)
        temp=dest.with_name(dest.name+'.update-'+uuid.uuid4().hex[:8]);shutil.copy2(path,temp);temp.replace(dest)


def wait_for_stopped_server(port=8000):
    for _ in range(3):
        probe=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            probe.bind(('127.0.0.1',port))
            return
        except OSError:
            print(f'Порт {port} занят. Остановите старый Кадр Control+C в его окне Терминала.',flush=True)
            input('После остановки нажмите Enter здесь. Файлы пока не менялись: ')
        finally:probe.close()
    raise ValueError('Порт всё ещё занят. Остановите предыдущий экземпляр программы и повторите обновление.')


def main():
    parser=argparse.ArgumentParser(description='Обновить установленный Кадр, сохранив материалы и ключи.')
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    print('Сначала остановите старый Кадр: Control+C в его Терминале.',flush=True)
    target=args.target
    if target is None:
        if sys.platform!='darwin':parser.error('Укажите --target с папкой установленной программы.')
        result=subprocess.run(['osascript','-e','POSIX path of (choose folder with prompt "Выберите СТАРУЮ папку Кадра: в ней лежат run.py, .venv и ваши материалы. Сначала остановите программу Control+C.")'],capture_output=True,text=True)
        if result.returncode:return
        target=Path(result.stdout.strip())
    target=target.resolve()
    try:
        wait_for_stopped_server()
        update_files(Path(__file__).resolve().parent.parent,target)
    except (OSError,ValueError) as exc:parser.exit(1,str(exc)+'\n')
    print('Код обновлён. Материалы и API-ключи сохранены. Обновляем зависимости…',flush=True)
    from install_runtime import prepare_runtime
    try:
        python=prepare_runtime(target, refresh=True)
    except (RuntimeError,OSError,subprocess.SubprocessError) as exc:
        parser.exit(1,str(exc)+'\n')
    print('Запускаем Кадр. Оставьте это окно открытым.',flush=True)
    subprocess.run([str(python),'run.py'],cwd=target,check=True)


if __name__=='__main__':main()
