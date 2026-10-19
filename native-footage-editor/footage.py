"""Pexels/Pixabay retrieval and verified downloads. Semantic selection lives in semantic.py."""

import os
import re
import json

import tempfile
import time
import subprocess
import urllib.request
from network import TLS_CONTEXT
import urllib.parse
import urllib.error
from pathlib import Path


PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PEXELS_BASE = "https://api.pexels.com/videos/search"

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
PIXABAY_BASE = "https://pixabay.com/api/videos/"

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

def _search_pexels(query: str, per_page: int, page: int = 1) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    params = urllib.parse.urlencode({
        "query": query,
        "page": page,
        "per_page": min(80, max(1, per_page)),
        "orientation": "landscape",
    })
    req = urllib.request.Request(
        f"{PEXELS_BASE}?{params}",
        headers={"Authorization": PEXELS_API_KEY, **_UA},
    )
    with urllib.request.urlopen(req, timeout=30, context=TLS_CONTEXT) as resp:
        data = json.loads(resp.read())

    results = []
    for video in data.get("videos", []):
        files = [f for f in video.get("video_files", [])
                 if f.get("link") and f.get("file_type", "video/mp4") == "video/mp4"]
        if not files:
            continue
        # берём файл ближе к HD (1280 шириной) — не тащим лишний вес 4K
        hd_files = sorted(
            [f for f in files if (f.get("width") or 0) >= 1280],
            key=lambda f: f.get("width") or 0,
        )
        best = hd_files[0] if hd_files else max(files, key=lambda f: f.get("width") or 0)
        results.append({
            "id": f"pexels_{video['id']}",
            "provider": "pexels",
            "source_url": video.get("url", ""),
            "title": re.sub(r"-?\d+/?$", "", urllib.parse.urlparse(video.get("url", "")).path.rsplit("/video/", 1)[-1]).replace("-", " "),
            "tags": "",
            "provider_rank": len(results),
            "sample_url": min(files, key=lambda f: abs((f.get("width") or 0) - 640))["link"],
            "query": query,
            "preview_image": video.get("image"),
            "video_url": best["link"],
            "width": best.get("width"),
            "height": best.get("height"),
            "duration": video.get("duration"),
        })
    return results


def _search_pixabay(query: str, per_page: int, page: int = 1) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    params = urllib.parse.urlencode({
        "key": PIXABAY_API_KEY,
        "q": query,
        "page": page,
        "order": "popular",
        "per_page": min(200, max(per_page, 3)),  # у Pixabay минимум 3
        "video_type": "film",
        "orientation": "horizontal",
        "safesearch": "true",
    })
    req = urllib.request.Request(f"{PIXABAY_BASE}?{params}", headers=_UA)
    with urllib.request.urlopen(req, timeout=30, context=TLS_CONTEXT) as resp:
        data = json.loads(resp.read())

    results = []
    for video in data.get("hits", [])[:per_page]:
        variants = video.get("videos", {})
        # предпочитаем medium (обычно ~960px) — компромисс качество/вес
        best = variants.get("medium") or variants.get("large") or variants.get("small") or variants.get("tiny")
        if not best or not best.get("url"):
            continue
        picture_id = video.get("picture_id")
        preview_image = (
            best.get("thumbnail")
            or (f"https://i.vimeocdn.com/video/{picture_id}_200x150.jpg" if picture_id else None)
        )
        results.append({
            "id": f"pixabay_{video['id']}",
            "provider": "pixabay",
            "source_url": video.get("pageURL", ""),
            "title": "",
            "tags": video.get("tags", ""),
            "provider_rank": len(results),
            "sample_url": (variants.get("small") or best).get("url") or best["url"],
            "query": query,
            "preview_image": preview_image,
            "video_url": best["url"],
            "width": best.get("width"),
            "height": best.get("height"),
            "duration": video.get("duration"),
        })
    return results


def search_footage(segment_text: str, per_page: int = 6) -> tuple[list[dict], list[str]]:
    from semantic import SemanticSelector
    from project import Project
    with tempfile.TemporaryDirectory() as directory:
        project = Project(Path(directory) / "selection.wav")
        project.set_segments_from_transcript([{"text": segment_text, "start": 0, "end": 8}])
        return SemanticSelector(project, per_page=per_page).search_segment(0)


def _download_bytes(url: str, dest_path: Path, max_bytes: int = 500 * 1024 * 1024) -> tuple[Path, str]:
    """Write atomically: a cancelled/failed transfer never looks like a cached video."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=_UA)
    temporary = None
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30, context=TLS_CONTEXT) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            size = 0
            with tempfile.NamedTemporaryFile(dir=dest_path.parent, suffix=".part", delete=False) as f:
                temporary = Path(f.name)
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes or time.monotonic() - started > 180:
                        raise RuntimeError("Превышен лимит размера или времени скачивания")
                    f.write(chunk)
        temporary.replace(dest_path)
        return dest_path, content_type
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _verify_video(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(result.stdout)
        return result.returncode == 0 and bool(data.get("streams")) and float(data["format"]["duration"]) > 0
    except Exception:
        return False


def download_footage(url: str, dest_path: Path) -> Path:
    dest_path = Path(dest_path)
    if dest_path.exists() and _verify_video(dest_path):
        return dest_path
    path, content_type = _download_bytes(url, dest_path)
    if content_type and not content_type.startswith("video/") and "octet-stream" not in content_type:
        path.unlink(missing_ok=True)
        raise RuntimeError("Сервер вернул не видео")
    if not _verify_video(path):
        path.unlink(missing_ok=True)
        raise RuntimeError("Скачанный файл не содержит исправного видео")
    return path


def download_preview(url: str, dest_path: Path) -> Path:
    path, _ = _download_bytes(url, dest_path, max_bytes=15 * 1024 * 1024)
    return path


def download_best(candidates, directory, log=print, stop=lambda: False):
    """Try eligible candidates in relevance order; a broken URL is not a scene failure."""
    for candidate in candidates:
        if stop():
            return None, None
        if not candidate.get("auto_eligible"):
            continue
        try:
            path = download_footage(candidate["video_url"], Path(directory) / f"{candidate['id']}.mp4")
            return candidate, path
        except Exception as exc:
            log(f"Не удалось скачать {candidate['id']}: {type(exc).__name__}; пробую следующий")
    return None, None
