"""Local faster-whisper transcription; original audio never leaves this computer."""
import os
import re
from pathlib import Path

_SENTENCE_END_RE = re.compile(r"[.!?…]+$")


def _split_segment_into_scenes(seg: dict, max_duration: float, min_duration: float) -> list[dict]:
    """
    Режет один (возможно длинный) сегмент на куски поменьше — по концам предложений,
    с принудительным разрезом, если предложение всё равно длиннее max_duration.
    """
    words = seg.get("words") or []
    if not words or any(w.get("start") is None or w.get("end") is None for w in words):
        # нет пословных таймкодов — разбить точно не можем, оставляем как есть
        return [seg]

    scenes = []
    current = []

    def flush():
        if not current:
            return
        text = " ".join(w["text"] for w in current).strip()
        if text:
            scenes.append({
                "text": text,
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "speaker": seg.get("speaker"),
                "words": list(current),
            })
        current.clear()

    for w in words:
        current.append(w)
        duration_so_far = current[-1]["end"] - current[0]["start"]
        is_sentence_end = bool(_SENTENCE_END_RE.search(w["text"]))
        if is_sentence_end and duration_so_far >= min_duration:
            flush()
        elif duration_so_far >= max_duration:
            flush()
    flush()

    return scenes if scenes else [seg]


def split_into_scenes(segments: list[dict], max_duration: float = 8.0, min_duration: float = 2.5) -> list[dict]:
    """
    Распознавание иногда отдаёт один сегмент на целый абзац без пауз.
    Режем такие сегменты на сцены поменьше, чтобы на каждую можно было
    подобрать свой футаж, а не один на весь текст.
    """
    scenes = []
    for seg in segments:
        scenes.extend(_split_segment_into_scenes(seg, max_duration, min_duration))
    return scenes


def transcribe_audio(audio_path: Path, language_codes=None, progress_cb=None) -> dict:
    from faster_whisper import WhisperModel
    from local_models import CACHE
    report=progress_cb or (lambda message:None)
    report('Локальное распознавание: загружаю модель; при первом запуске скачиваются веса...')
    size=os.getenv('WHISPER_MODEL','base')
    options=dict(device='cpu',compute_type='int8',cpu_threads=min(4,os.cpu_count() or 2),download_root=CACHE)
    try:
        model=WhisperModel(size,local_files_only=True,**options)
    except (OSError,ValueError):
        model=WhisperModel(size,**options)
    language=language_codes[0] if language_codes else os.getenv('WHISPER_LANGUAGE','ru') or None
    stream,info=model.transcribe(str(audio_path),language=language,word_timestamps=True,vad_filter=True,beam_size=3)
    segments=[]
    try:
        for segment in stream:
            words=[{'text':w.word.strip(),'start':w.start,'end':w.end} for w in (segment.words or []) if w.word.strip()]
            text=segment.text.strip()
            if text and segment.end>segment.start:
                segments.append({'text':text,'start':segment.start,'end':segment.end,'words':words,'speaker':None})
            report(f'Распознано {segment.end:.0f} из {info.duration:.0f} секунд')
    finally:
        del model
    scenes=split_into_scenes(segments)
    report(f'Готово: {len(scenes)} сцен. Распознавание выполнено на компьютере.')
    return {'text':' '.join(s['text'] for s in segments),'segments':scenes}
