"""Sanitized render failure reports: keep FFmpeg's reason, not private paths."""
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from .media import ROOT,DATA


@lru_cache(maxsize=1)
def capabilities():
    result={'ffmpeg':bool(shutil.which('ffmpeg')),'ffprobe':bool(shutil.which('ffprobe')),
            'ass':False,'xfade':False,'libx264':False,'aac':False,'qtrle':False,'version':'unknown'}
    if not result['ffmpeg']:return result
    try:
        filters=subprocess.run(['ffmpeg','-hide_banner','-filters'],capture_output=True,text=True,timeout=15,check=True).stdout
        encoders=subprocess.run(['ffmpeg','-hide_banner','-encoders'],capture_output=True,text=True,timeout=15,check=True).stdout
        for name in ('ass','xfade'):result[name]=bool(re.search(r'\s'+name+r'\s',filters))
        for name in ('libx264','aac','qtrle'):result[name]=bool(re.search(r'\s'+name+r'\s',encoders))
        result['version']=subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,timeout=15,check=True).stdout.splitlines()[0][:250]
    except (OSError,subprocess.SubprocessError,IndexError):pass
    return result


def redact(value):
    if isinstance(value,bytes):value=value.decode('utf-8',errors='replace')
    text=str(value or '')
    replacements={str(ROOT):'<программа>',str(DATA):'<данные>',str(Path.home()):'<пользователь>'}
    for original,replacement in sorted(replacements.items(),key=lambda pair:len(pair[0]),reverse=True):
        if len(original)>3:text=text.replace(original,replacement)
    text=re.sub(r'https?://[^\s\"\'<>]+','<URL>',text)
    # Logs may contain a path outside the configured data directory, e.g. removable media.
    text=re.sub(r'/Users/[^/\s]+','<пользователь>',text)
    return text[-10000:]


def failure_report(exc,stage,progress):
    raw=getattr(exc,'stderr',None) or getattr(exc,'output',None) or str(exc)
    detail=redact(raw);lower=detail.lower();features=capabilities()
    if isinstance(exc,FileNotFoundError):message='Не найден файл или инструмент FFmpeg. Проверьте установку и материалы.'
    elif 'ждёт фото или футаж' in lower or 'не найдено подходящее фото или видео' in lower:
        message='Для одной или нескольких сцен нет фото или футажа. Повторите автоматический подбор после восстановления доступа к источникам.'
    elif isinstance(exc,subprocess.TimeoutExpired):message='FFmpeg превысил время ожидания. Попробуйте более короткую сцену или другой футаж.'
    elif 'unknown encoder' in lower and 'libx264' in lower:message='В установленном FFmpeg нет кодировщика libx264, необходимого для H.264.'
    elif 'no such filter' in lower and 'ass' in lower:message='В FFmpeg нет фильтра субтитров ass (libass). Обновление программы использует резервный способ.'
    elif 'no such filter' in lower and 'xfade' in lower:message='В FFmpeg нет фильтра xfade для переходов. Выберите прямые склейки или обновите FFmpeg.'
    elif 'no space left' in lower:message='На диске не хватает места для сборки видео.'
    elif 'permission denied' in lower:message='Нет доступа к одному из файлов или папке сборки.'
    elif 'no such file' in lower or 'не найден' in lower:message='Один из файлов монтажа недоступен. Проверьте материал сцены.'
    elif 'broken pipe' in lower:message='FFmpeg остановился при сборке анимации. Причина указана в отчёте.'
    else:message='Сборка остановилась. Откройте «Причина ошибки» и пришлите отчёт.'
    return {'message':message,'stage':stage,'progress':progress,'error_type':type(exc).__name__,
            'detail':detail or type(exc).__name__,'capabilities':features}


def save_failure(folder,report):
    (folder/'diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    features=report['capabilities']
    lines=['Кадр: отчёт об ошибке сборки',report['message'],f"Этап: {report['stage']} ({report['progress']}%)",
           'Тип: '+report['error_type'],features['version'],
           'Возможности: '+', '.join(f'{key}={value}' for key,value in features.items() if key!='version'),
           '',report['detail']]
    text='\n'.join(lines)
    (folder/'diagnostic.txt').write_text(text,encoding='utf-8')
    return text
