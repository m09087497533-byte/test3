"""Cached 540p montage playback using the same renderer as the final export."""
import hashlib
import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .media import DATA
from .models import total_duration
from .render import render
from .render_diagnostics import failure_report,save_failure
import logging
logger=logging.getLogger('uvicorn.error')

ROOT=DATA/'playbacks'
ROOT.mkdir(exist_ok=True)


def fingerprint(project):
    data=project.model_dump(exclude={'name','version','resolution','auto_media_mode','auto_media_after_subtitles'})
    for scene in data['scenes']:scene.pop('media_reason',None)
    return hashlib.sha256(('renderer-18:'+json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'))).encode()).hexdigest()


class PlaybackBusy(ValueError):pass


class PlaybackJobs:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.root.mkdir(exist_ok=True)
        self.lock=threading.Lock();self.pool=ThreadPoolExecutor(max_workers=1)
        self.states={}
        for file in self.root.glob('*/status.json'):
            try:
                state=json.loads(file.read_text())
                if state['status'] in ('queued','running'):
                    state.update(status='error',message='Сборка просмотра прервана перезапуском. Подготовьте просмотр заново.')
                    file.write_text(json.dumps(state,ensure_ascii=False))
                self.states[file.parent.name]=state
            except (OSError,ValueError,KeyError):continue

    def _write(self,identifier,state):
        folder=self.root/identifier
        temp=folder/'status.tmp';temp.write_text(json.dumps(state,ensure_ascii=False));temp.replace(folder/'status.json')

    def start(self,project):
        digest=fingerprint(project)
        with self.lock:
            for identifier,state in self.states.items():
                if state['fingerprint']==digest and (state['status'] in ('queued','running') or
                        state['status']=='done' and (self.root/identifier/'film.mp4').is_file()):
                    return dict(state)
            if any(s['status'] in ('queued','running') for s in self.states.values()):
                raise PlaybackBusy('Уже готовится другой просмотр. Дождитесь завершения и обновите его.')
            identifier=uuid.uuid4().hex;(self.root/identifier).mkdir()
            (self.root/identifier/'project.json').write_text(project.model_dump_json(indent=2))
            state={'id':identifier,'status':'queued','progress':0,'message':'Подготовка просмотра · 540p',
                   'fingerprint':digest,'duration':total_duration(project),'created':time.time()}
            self.states[identifier]=state;self._write(identifier,state)
        self.pool.submit(self._run,identifier,project)
        return dict(state)

    def _run(self,identifier,project):
        def progress(percent,message):
            with self.lock:
                state=self.states[identifier]
                state.update(status='running',progress=percent,message=message)
                self._write(identifier,state)
        try:
            render(project,self.root/identifier,progress,draft=True)
            with self.lock:
                state=self.states[identifier];state.update(status='done',progress=100,message='Просмотр готов · 540p')
                self._write(identifier,state)
                # Bound cache size without touching exports or pending previews.
                done=sorted(((key,value) for key,value in self.states.items() if value['status']=='done'),
                            key=lambda pair:pair[1].get('created',0),reverse=True)
                for key,_ in done[3:]:
                    shutil.rmtree(self.root/key,ignore_errors=True);del self.states[key]
        except Exception as exc:
            with self.lock:
                state=self.states[identifier]
                report=failure_report(exc,state['message'],state['progress'])
                state.update(status='error',message=report['message'],diagnostic=report)
                try:
                    text=save_failure(self.root/identifier,report)
                    self._write(identifier,state)
                except OSError:
                    text=f"{report['message']}\nЭтап: {report['stage']}\n{report['detail']}"
                    state['diagnostic_save_error']='Не удалось записать отчёт на диск. Его можно скопировать из интерфейса.'
            logger.error('Playback failed:\n%s',text)

    def get(self,identifier):
        with self.lock:
            if identifier not in self.states:raise KeyError(identifier)
            return dict(self.states[identifier])
