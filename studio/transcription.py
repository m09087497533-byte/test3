"""Local speech recognition in a cancellable child process; no cloud audio upload."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from .media import DATA, ROOT, asset_info, asset_path
from .limits import MAX_PROJECT_SECONDS, MAX_SUBTITLES, DURATION_LIMIT_MESSAGE
from .models import Project, Subtitle
from .subtitles import plan_scenes
from .director import automatic_project

TASKS = DATA / 'transcriptions'
TASKS.mkdir(parents=True, exist_ok=True)
_lock = threading.RLock()
_processes = {}


class TranscriptionRequest(BaseModel):
    voice_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    model: Literal['tiny', 'base', 'small', 'medium'] = 'small'
    language: Literal['auto', 'ru', 'en', 'uk', 'de', 'fr', 'es', 'it', 'pt'] = 'ru'


def available():
    return importlib.util.find_spec('faster_whisper') is not None


def task_folder(identifier):
    if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Задание не найдено')
    folder = TASKS / identifier
    if not (folder / 'status.json').is_file():
        raise ValueError('Задание не найдено')
    return folder


def write_state(folder, state):
    temp = folder / 'status.tmp'
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    temp.replace(folder / 'status.json')


def status(identifier):
    with _lock:
        folder = task_folder(identifier)
        state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        if state['status'] in ('queued', 'running') and (folder / 'worker.json').is_file():
            state.update(json.loads((folder / 'worker.json').read_text(encoding='utf-8')))
        if state['status'] in ('queued', 'running') and identifier not in _processes:
            state.update(status='interrupted', message='Сервер перезапущен. Запустите распознавание ещё раз.')
            write_state(folder, state)
        return state


def cancel(identifier):
    with _lock:
        folder = task_folder(identifier)
        state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        if state['status'] not in ('queued', 'running'):
            return state
        process = _processes.get(identifier)
        if process is not None:
            process.terminate()
        state.update(status='cancelled', message='Распознавание остановлено')
        write_state(folder, state)
        return state


def start(request):
    meta = asset_info(request.voice_id)
    if not meta.get('has_audio'):
        raise ValueError('В файле нет звуковой дорожки')
    if meta['duration'] > MAX_PROJECT_SECONDS:
        raise ValueError(DURATION_LIMIT_MESSAGE)
    if not available():
        raise ValueError('Распознавание не установлено. Запустите «Обновить Кадр.command» или установите requirements-ai.txt.')
    with _lock:
        if _processes:
            raise ValueError('Распознавание уже идёт. Дождитесь завершения или остановите его.')
        identifier = uuid.uuid4().hex
        folder = TASKS / identifier
        folder.mkdir()
        state = dict(id=identifier, voice_id=request.voice_id, status='queued', progress=0,
                     message='Готовим модель. При первом запуске она скачивается; это может занять несколько минут.')
        write_state(folder, state)
        env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY='1')
        # Arguments contain only validated IDs/options, never arbitrary commands or paths from a client.
        with (folder / 'worker.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'studio.transcription', identifier,
                                        request.model, request.language], cwd=ROOT, env=env,
                                       stdout=log, stderr=log)
        _processes[identifier] = process
        threading.Thread(target=_watch, args=(identifier, process), daemon=True).start()
        return state


def _watch(identifier, process):
    process.wait()
    with _lock:
        folder = task_folder(identifier)
        state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        if state['status'] in ('queued', 'running'):
            if (folder / 'worker.json').is_file():
                state.update(json.loads((folder / 'worker.json').read_text(encoding='utf-8')))
            if state['status'] in ('queued', 'running'):
                state.update(status='error', message='Распознавание прервано. Возможно, не хватило памяти; попробуйте модель «Быстрая».')
            write_state(folder, state)
        _processes.pop(identifier, None)


def error_message(exc):
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if any(s in name + ' ' + message for s in ('connect', 'timeout', 'network', 'download', 'huggingface', 'localentrynotfound', 'certificate', 'proxy', '403 forbidden')):
        return 'Не удалось загрузить модель. Проверьте интернет и доступ к huggingface.co, затем повторите. Уже скачанная модель работает без сети.'
    if any(s in message for s in ('memory', 'alloc')):
        return 'Недостаточно памяти. Выберите модель «Быстрая» или «Базовая».'
    if isinstance(exc, ValueError) and any('а' <= c.lower() <= 'я' or c.lower() == 'ё' for c in str(exc)):
        return str(exc)[:400]
    return 'Не удалось распознать речь. Попробуйте другую модель или загрузите SRT/VTT.'


def recognize(path, model_name, language, duration, update, model_factory=None):
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    model = model_factory(model_name, device='cpu', compute_type='int8', cpu_threads=min(4, os.cpu_count() or 2),
                          download_root=str(DATA / 'models' / 'whisper'))
    update(1, 'Распознаём речь…')
    segments, info = model.transcribe(str(path), language=None if language == 'auto' else language,
                                      vad_filter=True, beam_size=5, word_timestamps=True)
    cues = []
    for segment in segments:
        # Word timestamps let long segments split without inventing evenly spaced timing.
        words = list(segment.words or [])
        groups = []
        if words:
            group = []
            for word in words:
                group.append(word)
                text = ''.join(w.word for w in group).strip()
                if len(text) >= 160 or word.end - group[0].start >= 6:
                    groups.append((group[0].start, word.end, text)); group = []
            if group:
                groups.append((group[0].start, group[-1].end, ''.join(w.word for w in group).strip()))
        else:
            groups = [(segment.start, segment.end, segment.text.strip())]
        for a, b, text in groups:
            a, b = max(0, a), min(duration, b)
            if text and b > a:
                if len(text) > 500:
                    raise ValueError('Модель вернула слишком длинную фразу. Попробуйте другую модель или SRT/VTT.')
                cues.append(Subtitle(start=a, end=b, text=text))
                if len(cues) > MAX_SUBTITLES:
                    raise ValueError('Слишком много фраз. Разделите озвучку на части.')
        update(min(98, round(segment.end / duration * 98)), 'Распознаём речь…')
    if not cues:
        raise ValueError('Речь не найдена. Проверьте озвучку или выберите другой язык.')
    directed = automatic_project(Project(scenes=plan_scenes(cues, duration), subtitles=cues))
    return dict(subtitles=[c.model_dump() for c in cues], scenes=[s.model_dump() for s in directed.scenes],
                director_version=directed.director_version, effects_version=directed.effects_version, photo_motion=directed.photo_motion, language=info.language)


def main():
    identifier, model_name, language = sys.argv[1:]
    folder = task_folder(identifier)
    state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
    def write_worker():
        temp = folder / 'worker.tmp'
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temp.replace(folder / 'worker.json')
    def update(progress, message):
        state.update(status='running', progress=progress, message=message)
        write_worker()
    try:
        result = recognize(asset_path(state['voice_id']), model_name, language,
                           asset_info(state['voice_id'])['duration'], update)
        state.update(status='done', progress=100, message='Речь распознана', result=result)
    except Exception as exc:
        state.update(status='error', message=error_message(exc))
    write_worker()


if __name__ == '__main__':
    main()
