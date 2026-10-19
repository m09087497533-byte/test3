import io
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

from .media import ASSETS, JOBS, ROOT, asset_info, asset_path, probe, run
from .models import Project, Scene, total_duration
from .render import preview, render, validate_assets
from .director import automatic_project
from .reference_effects import apply_reference_effects
from .subtitles import parse_subtitles, plan_scenes, apply_reference_rhythm

app = FastAPI(title='Кадр — видеомастерская', docs_url='/api/docs')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
worker = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()
jobs: dict[str, dict] = {}
Image.MAX_IMAGE_PIXELS = 24_000_000
logger = logging.getLogger('uvicorn.error')

# A restarted process cannot resume an encoder. Finished exports remain available.
for status_file in JOBS.glob('*/status.json'):
    try:
        state = json.loads(status_file.read_text())
        if state['status'] in ('queued', 'running'):
            state.update(status='error', message='Сервер был перезапущен. Запустите экспорт ещё раз.')
            status_file.write_text(json.dumps(state, ensure_ascii=False))
        jobs[status_file.parent.name] = state
    except (OSError, ValueError, KeyError):
        continue


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    return {'ok': bool(shutil.which('ffmpeg') and shutil.which('ffprobe')), 'version': '0.20'}


from . import transcription
from . import semantic
threading.Thread(target=semantic.restore, daemon=True).start()


@app.get('/api/semantic')
def semantic_status():
    return semantic.status()


@app.post('/api/semantic/prepare')
def semantic_prepare():
    return semantic.prepare()


@app.get('/api/transcription')
def transcription_capabilities():
    return {'available': transcription.available()}


@app.post('/api/transcription', status_code=202)
def start_transcription(request: transcription.TranscriptionRequest):
    try:
        return transcription.start(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/transcription/{identifier}')
def transcription_status(identifier: str):
    try:
        return transcription.status(identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post('/api/transcription/{identifier}/cancel')
def cancel_transcription(identifier: str):
    try:
        return transcription.cancel(identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class TranscriptEditRequest(BaseModel):
    project: Project
    texts: list[str] = Field(max_length=10000)


@app.post('/api/project/transcript')
def edit_transcript(request: TranscriptEditRequest):
    if len(request.texts) != len(request.project.subtitles):
        raise HTTPException(400, 'Количество фраз изменилось. Откройте редактор заново.')
    try:
        updated = request.project.model_copy(deep=True)
        for cue, text in zip(updated.subtitles, request.texts):
            cue.text = text.strip()
        updated = Project.model_validate(updated.model_dump())
        # Recompute accents while preserving the scene layout and all assigned media.
        revised = automatic_project(updated, rebuild=False)
        for old, new in zip(updated.scenes, revised.scenes):
            for key in ('asset_id', 'offset', 'media_reason', 'media_query', 'motion', 'motion_style',
                        'template', 'picture_effect', 'document_title', 'document_caption', 'transition_out',
                        'camera_style','camera_amount','camera_beat','shot_role','transition_duration','edit_reason'):
                setattr(new, key, getattr(old, key))
        return revised
    except ValueError as exc:
        raise HTTPException(400, 'Текст каждой фразы должен содержать от 1 до 500 символов.') from exc


@app.get('/api/assets')
def assets():
    result = []
    for path in sorted(ASSETS.glob('*/meta.json')):
        result.append(json.loads(path.read_text()))
    return result


@app.post('/api/assets')
async def upload(file: UploadFile = File(...)):
    identifier = uuid.uuid4().hex
    folder = ASSETS / identifier
    folder.mkdir()
    target = folder / 'source'
    size = 0
    try:
        with target.open('wb') as dest:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > 256*1024*1024:
                    raise HTTPException(413, 'Максимальный размер файла — 256 МБ')
                dest.write(chunk)
        name = (file.filename or 'Материал')[:160]
        meta = {'id': identifier, 'name': name, 'size': size, 'has_audio': False, 'created_at':time.time()}
        try:
            with Image.open(target) as source:
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.save(folder / 'image.jpg', quality=95)
                image.thumbnail((640, 360))
                image.save(folder / 'thumb.jpg')
                meta.update(kind='image', file='image.jpg', duration=0)
            target.unlink()
        except (UnidentifiedImageError, OSError):
            # Whitelist demuxers; an uploaded playlist must not trigger URL/file reads.
            result = run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                          '-format_whitelist', 'mov,matroska,webm,mp3,wav,ogg,flac,aac,avi',
                          '-show_format', '-show_streams', '-of', 'json', str(target)], timeout=30)
            info = json.loads(result.stdout)
            duration = float(info.get('format', {}).get('duration', 0))
            if not 0 < duration <= 36000:
                raise ValueError('Не удалось определить длительность файла')
            video = next((s for s in info['streams'] if s['codec_type'] == 'video'
                          and not s.get('disposition', {}).get('attached_pic')), None)
            has_audio = any(s['codec_type'] == 'audio' for s in info['streams'])
            if not video and not has_audio:
                raise ValueError('В файле нет изображения или звука')
            meta.update(kind='video' if video else 'audio', duration=duration, has_audio=has_audio, file='source')
            if video:
                if video['width']*video['height'] > 24_000_000:
                    raise ValueError('Максимальное разрешение — 24 мегапикселя')
                run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(target),
                     '-frames:v', '1', '-vf', 'scale=640:360:force_original_aspect_ratio=decrease',
                     '-threads', '2', str(folder / 'thumb.jpg')], timeout=60)
        (folder / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False))
        return meta
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except (ValueError, subprocess.SubprocessError, OSError, Image.DecompressionBombError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, 'Не удалось прочитать файл. Поддерживаются JPEG, PNG, WebP, MP4, MOV, MP3, WAV, M4A, OGG и FLAC.') from exc
    finally:
        await file.close()


@app.get('/api/assets/{identifier}/thumb')
def thumbnail(identifier: str):
    try:
        meta = asset_info(identifier)
        if meta['kind'] == 'audio':
            raise ValueError('Нет миниатюры')
        return FileResponse(ASSETS / identifier / 'thumb.jpg')
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/assets/{identifier}/file')
def original(identifier: str):
    try:
        meta = asset_info(identifier)
        return FileResponse(asset_path(identifier), filename=meta['name'])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post('/api/subtitles')
async def subtitles(file: UploadFile = File(...), voice_id: str = Form(default='')):
    try:
        content = await file.read(1024*1024 + 1)
        if len(content) > 1024*1024:
            raise ValueError('Файл субтитров слишком большой: максимум 1 МБ')
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('cp1251')
        cues = parse_subtitles(text)
        duration = asset_info(voice_id)['duration'] if voice_id else 0
        scenes = plan_scenes(cues, duration)
        directed=automatic_project(Project(scenes=scenes,subtitles=cues))
        return {'subtitles': [c.model_dump() for c in cues], 'scenes': [s.model_dump() for s in directed.scenes], 'director_version':12,'effects_version':19,'photo_motion':directed.photo_motion}
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()


@app.post('/api/project/reference-rhythm')
def reference_rhythm(project: Project):
    return automatic_project(project)


@app.post('/api/project/reference-effects')
def reference_effects(project: Project):
    return apply_reference_effects(project)


class SceneRegenerateRequest(BaseModel):
    project: Project
    scene_index: int = Field(ge=0, le=599)


@app.post('/api/project/scene/regenerate')
def regenerate_scene(request: SceneRegenerateRequest):
    if request.scene_index >= len(request.project.scenes):
        raise HTTPException(400, 'Сцена не найдена')
    revised=automatic_project(request.project,rebuild=False)
    scene=revised.scenes[request.scene_index]
    scene.asset_id=None
    scene.media_reason=''
    scene.offset=0
    return scene


@app.post('/api/preview')
def scene_preview(scene: Scene):
    try:
        if scene.asset_id and asset_info(scene.asset_id)['kind'] == 'audio':
            raise ValueError('Аудиофайл нельзя использовать как изображение')
        out = io.BytesIO()
        preview(scene).save(out, format='JPEG', quality=90)
        return Response(out.getvalue(), media_type='image/jpeg')
    except (ValueError, subprocess.SubprocessError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/project/validate')
def validate(project: Project):
    try:
        validate_assets(project)
        return project.model_dump()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def perform_job(identifier: str, project: Project):
    folder = JOBS / identifier

    def update(percent, message, status='running'):
        with job_lock:
            state = {'id': identifier, 'status': status, 'progress': percent, 'message': message}
            jobs[identifier] = state
            temp = folder / 'status.tmp'
            temp.write_text(json.dumps(state, ensure_ascii=False))
            temp.replace(folder / 'status.json')
    try:
        render(project, folder, update)
        update(100, 'Ролик готов', 'done')
    except Exception as exc:
        logger.exception('Render failed: %s', identifier)
        if isinstance(exc, subprocess.CalledProcessError):
            logger.error('Encoder stderr: %s', exc.stderr[-4000:])
        update(0, 'Не удалось собрать ролик. Проверьте материалы; подробности — в журнале сервера.', 'error')


@app.post('/api/render', status_code=202)
def start_render(project: Project):
    try:
        validate_assets(project)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with job_lock:
        if any(j['status'] in ('queued', 'running') for j in jobs.values()):
            raise HTTPException(409, 'Сначала дождитесь завершения текущего экспорта')
        identifier = uuid.uuid4().hex
        folder = JOBS / identifier
        folder.mkdir()
        (folder / 'project.json').write_text(project.model_dump_json(indent=2))
        state = {'id': identifier, 'status': 'queued', 'progress': 0, 'message': 'Подготовка экспорта'}
        jobs[identifier] = state
        (folder / 'status.json').write_text(json.dumps(state, ensure_ascii=False))
    worker.submit(perform_job, identifier, project)
    return state


@app.get('/api/jobs/{identifier}')
def job_status(identifier: str):
    with job_lock:
        if identifier not in jobs:
            raise HTTPException(404, 'Экспорт не найден')
        return dict(jobs[identifier])


@app.get('/api/jobs/{identifier}/video')
def video(identifier: str):
    state = job_status(identifier)
    if state['status'] != 'done':
        raise HTTPException(409, 'Видео ещё не готово')
    return FileResponse(JOBS / identifier / 'film.mp4', media_type='video/mp4', filename='film.mp4')


@app.get('/api/demo')
def demo():
    return Project(name='История, которая оживает', sound_mode='auto', sounds_per_minute=4, transition='mixed', transition_sounds=False, animate_titles=True, scenes=[
        Scene(title='У каждой истории\nесть свой голос', caption='Ваша озвучка. Ваш ритм. Ваш фильм.', duration=4),
        Scene(template='archive', title='Прошлое становится ближе', caption='АРХИВ • ГЛАВА 01', duration=4),
        Scene(template='statement', title='СИЛА СЛОВА', caption='Один акцент меняет всё', duration=3),
        Scene(template='quote', title='История начинается\nс одного слова', caption='Загрузите свою озвучку и SRT, чтобы создать собственный монтаж.', duration=5)
    ]).model_dump()


@app.get('/api/project/new')
def new_project():
    return Project(name='Новая история', library_since=time.time(), scenes=[Scene(duration=4, motion=False)],
                   director_version=12, transition='cut', photo_motion='static',
                   sound_mode='auto', sounds_per_minute=4, sfx_volume=.28,
                   animate_titles=True, show_subtitles=False, auto_music=True, semantic_media=True,effects_version=19).model_dump()


from .image_search import scene_query, search_images, import_image
from .soundplan import sound_plan
from .network import connection_error


class SearchRequest(BaseModel):
    project: Project
    scene_index: int = Field(ge=0, le=599)
    query: str = Field(default='', max_length=200)


class ImportImageRequest(BaseModel):
    token: str = Field(pattern=r'^[a-f0-9]{32}$')


@app.post('/api/images/search')
def image_search(request: SearchRequest):
    if request.scene_index >= len(request.project.scenes):
        raise HTTPException(400,'Сцена не найдена')
    try:
        context=({'query':request.query.strip(),'concept':'Ваш запрос','basis':''} if request.query.strip()
                 else scene_query(request.project,request.scene_index))
        return {**context,'results':[] if context.get('text_only') else search_images(context['query'])}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502,connection_error(exc,'Wikimedia Commons')) from None


@app.post('/api/images/import')
def image_import(request: ImportImageRequest):
    try:
        return import_image(request.token)
    except (ValueError,Image.DecompressionBombError,UnidentifiedImageError) as exc:
        raise HTTPException(400,str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502,connection_error(exc,'Wikimedia Commons')) from None


@app.post('/api/sound-plan')
def planned_sound(project: Project):
    events=sound_plan(project)
    return {'events':events,'count':len(events),'duration':total_duration(project)}


@app.get('/api/jobs/{identifier}/sources')
def download_sources(identifier: str):
    # Reuse the job access and completion checks from the MP4 endpoint.
    video(identifier)
    path=JOBS/identifier/'sources.txt'
    if not path.is_file():
        raise HTTPException(404,'Для старого экспорта список источников отсутствует')
    return FileResponse(path,media_type='text/plain; charset=utf-8',filename='sources.txt')


from typing import Literal
from .stock_video import key_status, save_keys, footage_query, search_videos, import_video, StockError


class StockSearchRequest(SearchRequest):
    provider: Literal['pexels','pixabay'] = 'pexels'


class StockKeysRequest(BaseModel):
    pexels: str | None = Field(default=None,max_length=512)
    pixabay: str | None = Field(default=None,max_length=512)


@app.get('/api/stock/status')
def stock_status():
    return key_status()


@app.post('/api/stock/keys')
def stock_keys(request: StockKeysRequest):
    return save_keys(request.model_dump())


@app.post('/api/videos/search')
def footage_search(request: StockSearchRequest):
    if request.scene_index>=len(request.project.scenes):raise HTTPException(400,'Сцена не найдена')
    try:
        context=({'query':request.query.strip(),'concept':'Ваш запрос','basis':''} if request.query.strip()
                 else footage_query(request.project,request.scene_index))
        return {**context,'results':search_videos(request.provider,context['query'])}
    except StockError:
        raise
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    except OSError:
        raise HTTPException(502,'Не удалось выполнить поиск видео. Проверьте доступ к сервису.') from None


@app.post('/api/videos/import')
def footage_import(request: ImportImageRequest):
    try:return import_video(request.token)
    except StockError:raise
    except OSError as exc:
        raise HTTPException(502,connection_error(exc,'Загрузка футажа')) from None
    except (ValueError,subprocess.SubprocessError):
        raise HTTPException(502,'Не удалось прочитать видео. Попробуйте другой футаж.') from None


@app.exception_handler(StockError)
def stock_error_handler(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':str(exc)})


@app.get('/api/network/check')
def check_network():
    from .network import diagnostics
    return {'checks':diagnostics()}


from .auto_media import ArrangementJobs
arrangement_jobs=ArrangementJobs()


class AutoMediaRequest(BaseModel):
    project: Project
    mode: Literal['mixed','photos','videos'] = 'mixed'
    replace_existing: bool = False
    scene_indices: list[int] | None = Field(default=None, max_length=600)
    excluded_asset_ids: list[str] = Field(default_factory=list, max_length=600)


@app.post('/api/auto-media',status_code=202)
def start_auto_media(request: AutoMediaRequest):
    if request.scene_indices is not None and any(i >= len(request.project.scenes) for i in request.scene_indices):
        raise HTTPException(400, 'Сцена не найдена')
    try:return arrangement_jobs.start(request.project,request.mode,request.replace_existing,request.scene_indices,request.excluded_asset_ids)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@app.get('/api/auto-media/{identifier}')
def get_auto_media(identifier: str):
    try:return arrangement_jobs.get(identifier)
    except KeyError:raise HTTPException(404,'Подбор не найден') from None


@app.post('/api/auto-media/{identifier}/cancel')
def cancel_auto_media(identifier: str):
    try:return arrangement_jobs.cancel(identifier)
    except KeyError:raise HTTPException(404,'Подбор не найден') from None


from .playback import PlaybackJobs,PlaybackBusy
playback_jobs=PlaybackJobs()


@app.post('/api/playback',status_code=202)
def start_playback(project: Project):
    try:
        validate_assets(project)
        return playback_jobs.start(project)
    except PlaybackBusy as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@app.get('/api/playback/{identifier}')
def get_playback(identifier: str):
    try:return playback_jobs.get(identifier)
    except KeyError:raise HTTPException(404,'Просмотр не найден. Подготовьте его заново.') from None


@app.get('/api/playback/{identifier}/video')
def playback_video(identifier: str):
    state=get_playback(identifier)
    if state['status']!='done':raise HTTPException(409,'Просмотр ещё не готов')
    path=playback_jobs.root/identifier/'film.mp4'
    if not path.is_file():raise HTTPException(404,'Просмотр удалён из кэша. Подготовьте его заново.')
    return FileResponse(path,media_type='video/mp4',filename='preview.mp4',content_disposition_type='inline')


@app.get('/api/playback/{identifier}/diagnostic')
def playback_diagnostic(identifier: str):
    get_playback(identifier)
    path=playback_jobs.root/identifier/'diagnostic.txt'
    if not path.is_file():raise HTTPException(404,'Для этого задания отчёт отсутствует. Повторите подготовку просмотра.')
    return FileResponse(path,media_type='text/plain; charset=utf-8',filename='kadr-playback-error.txt')
