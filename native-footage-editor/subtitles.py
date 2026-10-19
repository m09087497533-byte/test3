"""
Импорт готовых субтитров (.vtt / .srt) как альтернатива транскрибации через API.
Полезно, когда уже есть точный тайминг (например, из стороннего инструмента
субтитрирования): реплики в таких файлах обычно порезаны под экран (2-3 секунды,
разрыв прямо на середине фразы), поэтому мы сначала склеиваем всё в один текст
с пословным таймингом (распределяя его равномерно внутри каждой реплики),
а потом режем на сцены по своим правилам (конец предложения / максимальная
длительность) — точно так же, как после обычной транскрибации.
"""

import re
from pathlib import Path

from transcribe import split_into_scenes

_TIME_RE = re.compile(
    r'(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})'
)


def _tc_to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_cues(path: Path) -> list[dict]:
    """Общий парсер VTT/SRT: возвращает [{'start':.., 'end':.., 'text': '...'}, ...]."""
    raw = Path(path).read_text(encoding="utf-8-sig")
    lines = raw.splitlines()

    cues = []
    i = 0
    while i < len(lines):
        m = _TIME_RE.search(lines[i])
        if m:
            g = m.groups()
            start = _tc_to_seconds(*g[0:4])
            end = _tc_to_seconds(*g[4:8])
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            text = " ".join(text_lines)
            text = re.sub(r"<[^>]+>", "", text)          # <i>, <b> и т.п.
            text = re.sub(r"\{[^}]*\}", "", text)          # ASS-подобные теги в некоторых .srt
            text = text.replace("&nbsp;", " ").strip()
            if text:
                cues.append({"start": start, "end": end, "text": text})
        i += 1
    return cues


def _cues_to_words(cues: list[dict]) -> list[dict]:
    """Раскладывает каждую реплику на слова с равномерно распределённым таймингом внутри неё."""
    words = []
    for cue in cues:
        cue_words = cue["text"].split()
        if not cue_words:
            continue
        duration = max(cue["end"] - cue["start"], 0.05)
        step = duration / len(cue_words)
        for i, w in enumerate(cue_words):
            words.append({
                "text": w,
                "start": round(cue["start"] + i * step, 3),
                "end": round(cue["start"] + (i + 1) * step, 3),
            })
    return words


def import_subtitle_file(path: Path) -> dict:
    """
    Импортирует .vtt/.srt как готовый транскрипт со сценами, минуя транскрибацию
    через fast-gen.ai. Формат результата такой же, как у transcribe.transcribe_audio:
    {"text": ..., "segments": [...]}.
    """
    path = Path(path)
    cues = _parse_cues(path)
    if not cues:
        raise ValueError("Не нашла ни одной реплики в файле — проверь формат (.vtt/.srt)")

    words = _cues_to_words(cues)
    full_text = " ".join(w["text"] for w in words)

    # один большой "сегмент" на весь текст с пословным таймингом —
    # split_into_scenes сама порежет его на осмысленные куски
    mega_segment = {
        "text": full_text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "speaker": None,
        "words": words,
    }
    scenes = split_into_scenes([mega_segment])
    return {"text": full_text, "segments": scenes}
