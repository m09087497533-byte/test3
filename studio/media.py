import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('STUDIO_DATA_DIR', ROOT / '.data')).resolve()
ASSETS = DATA / 'assets'
JOBS = DATA / 'jobs'
for directory in (ASSETS, JOBS):
    directory.mkdir(parents=True, exist_ok=True)


def run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout)


def probe(path: Path) -> dict:
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                           '-of', 'json', str(path)], timeout=30).stdout)


def asset_info(asset_id: str) -> dict:
    # IDs, rather than client supplied paths, are used for all filesystem access.
    if len(asset_id) != 32 or any(c not in '0123456789abcdef' for c in asset_id):
        raise ValueError('Некорректный идентификатор файла')
    path = ASSETS / asset_id / 'meta.json'
    if not path.is_file():
        raise ValueError('Материал не найден. Загрузите его повторно.')
    return json.loads(path.read_text())


def asset_path(asset_id: str) -> Path:
    return ASSETS / asset_id / asset_info(asset_id)['file']
