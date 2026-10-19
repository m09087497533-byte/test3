"""Local CLIP ranking, local translation and cached frame sampling.
Cosine similarity is a retrieval signal, not a verified statement about a scene.
"""
import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import time
import urllib.error
from pathlib import Path
from PIL import Image
import footage
from local_models import LocalModels, CLIP_MODEL, TRANSLATION_MODEL

VERSION='local-clip-1'
STOP=set('a an the of and or but to in at on for from by with as is are was were be been '
         'has have had do does did he she it they we i you his her their our this that these those '
         'there here then when where which who whom would could should will can very just also '
         'one two said says say told thought think'.split())
RU_STOP=set('и в во на но не что он она они мы вы я это этот эта то тот та как к ко по из за '
            'у о об с со для а же бы был была были будет или его её ее их уже ещё еще всё все '
            'только когда чтобы если так от до над под после перед'.split())


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def setting_int(name,default,lower,upper):
    try:return max(lower,min(upper,int(os.getenv(name,str(default)))))
    except ValueError:return default


def safe_error(exc):
    if isinstance(exc,urllib.error.HTTPError):return f'HTTP {exc.code}'
    if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)):return 'истекло время ожидания'
    if isinstance(exc,ModuleNotFoundError):return f'Не установлен {exc.name}; запусти python3 setup_local.py'
    if isinstance(exc,(RuntimeError,ValueError,FileNotFoundError)):
        # Messages from remote URLs may contain provider credentials.
        return re.sub(r'https?://\S+','[ссылка]',str(exc))[:300] or type(exc).__name__
    return type(exc).__name__


def make_queries(current,previous='',custom=''):
    if custom.strip():
        return [custom.strip()[:100]]
    def words(text):return list(dict.fromkeys(w for w in re.findall(r'[a-z]+',text.lower()) if len(w)>2 and w not in STOP))
    primary=words(current);context=words(previous)
    if not primary:return []
    candidates=[primary[:5],list(dict.fromkeys(primary[:2]+primary[-3:])),
                list(dict.fromkeys(primary[:2]+context[:2])) if context else primary[:3]]
    return list(dict.fromkeys(' '.join(q) for q in candidates if q))


def rank_candidates(candidates,recent=(),used=(),duration=8):
    recent,used=set(recent),set(used)
    def key(c):
        score=c.get('score',0)
        # Diversity applies only within a two-point cosine*100 band.
        return (bool(c.get('auto_eligible')),int(score//2),c['id'] not in recent,
                c['id'] not in used,score,(c.get('duration') or 0)>=duration,
                min(c.get('width') or 0,1920),-c.get('provider_rank',999),c['id'])
    return sorted(candidates,key=key,reverse=True)


def set_score(candidate, similarities, evidence, threshold):
    if not similarities or not all(math.isfinite(float(x)) for x in similarities):
        raise ValueError('Некорректная оценка локальной модели')
    # A single good frame should not hide two unrelated frames.
    value=.7*(sum(similarities)/len(similarities))+.3*min(similarities)
    candidate.update(score=round(100*value,2),similarity=value,evidence=evidence,
                     auto_eligible=evidence=='frames' and len(similarities)>=3 and value>=threshold,
                     observed='',match='local_similarity',contradiction=False)
    candidate['match_label']='По смысловому сходству' if candidate['auto_eligible'] else 'Нужна проверка'
    candidate['reason']=(f'Локальное сравнение {len(similarities)} кадров' if evidence=='frames' else 'Локальное сравнение превью')+f': сходство {value:.3f}. Это оценка модели, не гарантия совпадения.'


class SemanticSelector:
    def __init__(self,project,per_page=None,models=None):
        self.project=project
        self.cache_dir=project.project_dir/'selection_cache_local'
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        self.per_page=per_page or setting_int('SEARCH_PER_PROVIDER',6,3,20)
        self.preview_limit=setting_int('LOCAL_PREVIEW_LIMIT',12,2,24)
        self.frame_limit=setting_int('LOCAL_FRAME_LIMIT',4,1,8)
        try:self.minimum=float(os.getenv('LOCAL_MIN_SIMILARITY','0.22'))
        except ValueError:self.minimum=.22
        if not math.isfinite(self.minimum):self.minimum=.22
        self.minimum=max(.05,min(.8,self.minimum))
        self.models=models or LocalModels()
        self.stop=lambda:False
        self.log=lambda message:None

    def check_stop(self):
        if self.stop():raise InterruptedError('Подбор остановлен')

    def cached(self,kind,payload,factory,ttl=None):
        path=self.cache_dir/f'{kind}_{digest([VERSION,CLIP_MODEL,TRANSLATION_MODEL,payload])}.json'
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if ttl is None or time.time()-data['time']<ttl:return data['value']
        except (OSError,ValueError,KeyError,TypeError):pass
        self.check_stop()
        value=factory()
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.cache_dir,delete=False) as file:
            json.dump({'time':time.time(),'value':value},file,ensure_ascii=False)
            temporary=Path(file.name)
        temporary.replace(path)
        return value

    def translate(self,text):
        return self.cached('translation',text,lambda:self.models.translate(text))

    def topic(self):
        text=' '.join(s['text'] for s in self.project.segments)
        fingerprint=digest(text)
        stored=self.project.story_context
        if stored.get('engine')==VERSION and stored.get('fingerprint')==fingerprint:return stored['summary']
        from collections import Counter
        words=[w for w in re.findall(r'[а-яёa-z]+',text.lower()) if len(w)>3 and w not in RU_STOP and w not in STOP]
        # A transparent keyword topic, not an invented story summary.
        summary=self.translate(' '.join(w for w,_ in Counter(words).most_common(10))) if words else ''
        self.project.story_context={'engine':VERSION,'fingerprint':fingerprint,'summary':summary}
        self.project.save()
        return summary

    def scene(self,index,topic):
        segments=self.project.segments
        context={'current':segments[index]['text'],
                 'previous':' '.join(s['text'] for s in segments[max(0,index-2):index]),
                 'next':' '.join(s['text'] for s in segments[index+1:index+2]),'topic':topic}
        english={name:self.translate(context[name]) for name in ('current','previous','next')}
        custom=self.translate(segments[index].get('custom_query',''))
        # Current narration dominates. A short referring sentence benefits from previous context.
        return {'scene':english['current'],'context':context,'english':english,'topic':topic,
                'queries':make_queries(english['current'],english['previous'],custom)}

    def scene_vector(self,scene):
        import numpy as np
        texts=[scene['english']['current'],scene['english']['previous'],scene['english']['next'],scene['topic']]
        vectors=np.asarray(self.cached('text_vectors',texts,lambda:self.models.text_vectors(texts).tolist()),dtype='float32')
        weights=np.array([.8,.12,.03,.05],dtype='float32')
        for i,text in enumerate(texts):
            if not text.strip():weights[i]=0
        weights/=weights.sum()
        combined=(vectors*weights[:,None]).sum(axis=0)
        combined/=max(float(np.linalg.norm(combined)),1e-12)
        return combined

    def search_query(self,query,page,warnings):
        values=[]
        for provider,key,function in [('pexels',footage.PEXELS_API_KEY,footage._search_pexels),
                                      ('pixabay',footage.PIXABAY_API_KEY,footage._search_pixabay)]:
            self.check_stop()
            if not key:continue
            try:
                found=self.cached('search',[provider,query,page,self.per_page],
                    lambda:function(query,self.per_page,page=page),ttl=86400)
                values.extend(dict(c) for c in found)
            except InterruptedError:raise
            except Exception as exc:warnings.append(provider+': '+safe_error(exc))
        return values

    def preview(self,candidate):
        path=self.cache_dir/f"preview_{candidate['id']}.jpg"
        if path.exists():return path
        source=self.cache_dir/f"thumb_{candidate['id']}.download"
        try:
            footage.download_preview(candidate['preview_image'],source)
            with Image.open(source) as image:
                image=image.convert('RGB');image.thumbnail((640,360));image.save(path,'JPEG',quality=85)
        finally:source.unlink(missing_ok=True)
        return path

    def frames(self,candidate,duration):
        span=max(.1,min(float(candidate.get('duration') or duration),duration))
        times=[max(0,span*f-.04) for f in (.1,.5,.9)]
        paths=[self.cache_dir/f"frame_{digest([candidate['id'],t])}.jpg" for t in times]
        if all(p.exists() for p in paths):return paths
        video=self.cache_dir/f"sample_{candidate['id']}.mp4"
        try:
            footage.download_footage(candidate.get('sample_url') or candidate['video_url'],video)
            for timestamp,path in zip(times,paths):
                self.check_stop()
                frame=subprocess.run(['ffmpeg','-v','error','-ss',str(timestamp),'-i',str(video),
                    '-frames:v','1','-vf','scale=640:360:force_original_aspect_ratio=decrease',
                    '-f','image2pipe','-vcodec','mjpeg','pipe:1'],capture_output=True,check=True,timeout=25)
                with Image.open(io.BytesIO(frame.stdout)) as image:image.convert('RGB').save(path,'JPEG',quality=85)
            return paths
        finally:video.unlink(missing_ok=True)

    def similarities(self,paths,vector):
        import numpy as np
        embeddings=[]
        for path in paths:
            payload=hashlib.sha256(path.read_bytes()).hexdigest()
            def encode():
                with Image.open(path) as image:return self.models.image_vectors([image.convert('RGB')])[0].tolist()
            embeddings.append(self.cached('image_vector',payload,encode))
        return (np.asarray(embeddings,dtype='float32')@vector).tolist()

    def evaluate(self,candidates,scene,vector,duration,warnings):
        import numpy as np
        # Real titles/tags only; a query is not evidence of candidate content.
        labels=[(c.get('title','')+' '+c.get('tags','')).strip() for c in candidates]
        if any(labels):
            raw=np.asarray(self.cached('metadata',labels,lambda:self.models.text_vectors(labels).tolist()))@vector
        else:raw=np.zeros(len(candidates))
        for c,score in zip(candidates,raw):
            c.update(score=float(score)*100,evidence='metadata',auto_eligible=False,
                     match_label='Нужна проверка',reason='Кадры ещё не сравнивались')
        ordered=sorted(candidates,key=lambda c:(-c['score'],c.get('provider_rank',999),digest(c['id'])))
        for c in ordered[:self.preview_limit]:
            self.check_stop()
            try:
                self.log('Сравниваю превью '+c['id'])
                set_score(c,self.similarities([self.preview(c)],vector),'preview',self.minimum)
            except InterruptedError:raise
            except Exception as exc:warnings.append('Превью '+c['id']+': '+safe_error(exc))
        # Text-text and image-text cosine are not on the same scale.
        # Prefer checked previews; fill remaining slots with missing-preview candidates.
        shortlist=sorted(ordered,key=lambda c:(c.get('evidence')=='preview',c.get('score',0)),reverse=True)
        for c in shortlist[:self.frame_limit]:
            self.check_stop()
            try:
                self.log('Сравниваю три кадра '+c['id'])
                paths=self.frames(c,duration)
                set_score(c,self.similarities(paths,vector),'frames',self.minimum)
            except InterruptedError:raise
            except Exception as exc:
                c['auto_eligible']=False
                warnings.append('Кадры '+c['id']+': '+safe_error(exc))
        return ordered

    def search_segment(self,index,more=False,stop=lambda:False,log=lambda message:None):
        if not footage.PEXELS_API_KEY and not footage.PIXABAY_API_KEY:
            raise RuntimeError('Добавь PEXELS_API_KEY или PIXABAY_API_KEY в .env')
        self.stop,self.log=stop,log;self.models.log=log
        self.check_stop()
        seg=self.project.segments[index]
        topic=self.topic()
        scene=self.scene(index,topic)
        if not scene['queries']:raise ValueError('Не удалось составить запрос; введи свой запрос под текстом сцены')
        vector=self.scene_vector(scene)
        # Invalidate the old remote-model page counter when switching engines.
        page=(int(seg.get('search_page',1)) if seg.get('selection_engine')==VERSION else 1)+(1 if more else 0)
        seg.update(scene_plan=scene,search_page=page,selection_engine=VERSION)
        self.project.save()
        start=0 if index==0 else seg['start']
        end=self.project.segments[index+1]['start'] if index+1<len(self.project.segments) else seg['end']
        duration=max(.1,end-start)
        merged={};warnings=[]
        queries=scene['queries']
        for round_queries in (queries[:2],queries[2:]):
            added=[]
            for query in round_queries:
                self.log('Ищу: '+query)
                for c in self.search_query(query,page,warnings):
                    if c['id'] in merged:
                        merged[c['id']]['matched_queries']=list(dict.fromkeys(merged[c['id']]['matched_queries']+[query]))
                    else:
                        c['matched_queries']=[query];merged[c['id']]=c;added.append(c)
            if added:self.evaluate(added,scene,vector,duration,warnings)
            if any(c.get('auto_eligible') for c in merged.values()):break
        recent=[s.get('_selected_candidate_id') for s in self.project.segments[max(0,index-5):index]]
        used=[s.get('_selected_candidate_id') for j,s in enumerate(self.project.segments) if j!=index and s.get('footage_path')]
        ranked=rank_candidates(list(merged.values()),recent,used,duration)
        seg.update(candidates=ranked,selection_warnings=list(dict.fromkeys(warnings)),
                   selection_status='ready' if any(c.get('auto_eligible') for c in ranked) else 'review')
        self.project.save()
        return ranked,seg['selection_warnings']
