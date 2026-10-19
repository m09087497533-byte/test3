"""Read-only checks shared by the gallery, retry, playback and export."""
from functools import lru_cache
import subprocess
from PIL import Image
from .media import asset_info,asset_path,probe
from .preview_selection import has_visual
from .visual_fallback import assigned_problem


@lru_cache(maxsize=512)
def _file_problem(path,kind,size,modified):
    # Stat in the caller invalidates the cache if a file is replaced/deleted.
    if not size:return 'Файл материала пуст. Загрузите его заново или повторите подбор.'
    try:
        if kind=='image':
            with Image.open(path) as image:image.verify()
        else:
            streams=probe(path).get('streams',[])
            stream_type='video' if kind=='video' else 'audio'
            if not any(s.get('codec_type')==stream_type for s in streams):
                return 'В файле нет нужной дорожки. Выберите другой материал.'
    except (OSError,ValueError,subprocess.SubprocessError):
        return 'Файл материала не читается. Загрузите его заново или повторите подбор.'
    return ''


def file_problem(path,kind):
    try:
        stat=path.stat()
        return _file_problem(str(path),kind,stat.st_size,stat.st_mtime_ns)
    except OSError:
        return 'Файл материала отсутствует. Загрузите его заново или повторите подбор.'


def material_problem(identifier, *, audio=False):
    try:
        meta=asset_info(identifier)
        if audio:
            if not meta.get('has_audio'):return meta,'Выбранный файл не содержит звуковую дорожку.'
        elif meta.get('kind') not in ('image','video'):
            return meta,'Для сцены нужно изображение или видео.'
        path=asset_path(identifier)
        return meta,file_problem(path,'audio' if audio else meta['kind'])
    except (OSError,ValueError,KeyError,TypeError):
        return {},'Файл материала отсутствует. Загрузите его заново или повторите подбор.'


def project_readiness(project):
    rows=[];materials={}
    for index,scene in enumerate(project.scenes):
        reason=''
        if scene.asset_id:
            if scene.asset_id not in materials:materials[scene.asset_id]=material_problem(scene.asset_id)
            meta,reason=materials[scene.asset_id]
            if not reason and meta['kind']=='video' and scene.offset>=meta.get('duration',0):
                reason='Начало фрагмента выходит за пределы видео. Измените начало или замените кадр.'
            if not reason:
                try:reason=assigned_problem(project,index,meta)
                except ValueError:reason='Не удалось подтвердить тему автоматического кадра. Повторите подбор.'
        elif not has_visual(scene):reason='Для сцены ещё нужно фото или видео.'
        rows.append(dict(scene_index=index,ready=not reason,reason=reason))
    audio=[]
    for label,identifier in (('Озвучка',project.voice_id),('Музыка',project.music_id)):
        if identifier:
            _,reason=material_problem(identifier,audio=True)
            if reason:audio.append(label+': '+reason)
    count=sum(row['ready'] for row in rows)
    return dict(scenes=rows,ready_count=count,total=len(rows),audio_issues=audio,can_render=count==len(rows) and not audio)


def readiness_message(report):
    problems=[f"Сцена {r['scene_index']+1}: {r['reason']}" for r in report['scenes'] if not r['ready']]
    remainder=max(0,len(problems)-6)
    return '\n'.join(problems[:6]+([f'Ещё {remainder} сцен: откройте «Просмотреть кадры» → «Без кадра».'] if remainder else [])+report['audio_issues'])


def require_ready(project):
    report=project_readiness(project)
    if not report['can_render']:raise ValueError(readiness_message(report))
    return report
