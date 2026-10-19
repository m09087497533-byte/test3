"""Carry local media and private stock keys into a neighboring Mac installation."""
import shutil
import sys
from pathlib import Path


def migrate(current):
    current=Path(current).resolve()
    data=current/'.data'
    assets=data/'assets'
    has_assets=assets.is_dir() and any(assets.glob('*/meta.json'))
    has_keys=(data/'stock-keys.json').is_file()
    if has_assets and has_keys:return False
    siblings=[]
    for folder in current.parent.iterdir():
        if folder==current or not folder.is_dir() or folder.is_symlink():continue
        source=folder/'.data'
        if not (folder/'Запустить Кадр.command').is_file():continue
        if not (source/'assets').is_dir() and not (source/'stock-keys.json').is_file():continue
        siblings.append(source)
    if not siblings:return False
    previous=max(siblings,key=lambda source:max((p.stat().st_mtime for p in (source/'assets',source/'stock-keys.json') if p.exists()),default=0))
    old_assets=previous/'assets'
    copied=False
    if not has_assets and old_assets.is_dir() and not old_assets.is_symlink() and not any(p.is_symlink() for p in old_assets.rglob('*')):
        shutil.copytree(old_assets,assets,dirs_exist_ok=True)
        copied=True
    key=previous/'stock-keys.json'
    if key.is_file() and not key.is_symlink() and not (data/'stock-keys.json').exists():
        data.mkdir(exist_ok=True)
        shutil.copy2(key,data/'stock-keys.json')
        (data/'stock-keys.json').chmod(0o600)
        copied=True
    if copied:print('Материалы и настройки источников перенесены из предыдущей папки Кадра.')
    return copied


if __name__=='__main__':
    try:migrate(sys.argv[1] if len(sys.argv)>1 else '.')
    except OSError as exc:print('Не удалось перенести старые материалы:',exc)
