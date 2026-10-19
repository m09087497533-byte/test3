"""Pexels/Pixabay video search and bounded local import through official APIs."""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from PIL import Image, ImageOps
from .media import DATA, ASSETS, run
from .image_search import scene_query
from .network import https_handler, connection_error, open_with_retry

PROVIDERS={'pexels':('PEXELS_API_KEY','Pexels','https://www.pexels.com/license/'),
           'pixabay':('PIXABAY_API_KEY','Pixabay','https://pixabay.com/service/license-summary/')}
lock=threading.Lock()
results=OrderedDict()
SETTINGS=DATA/'stock-keys.json'
CACHE=DATA/'stock-cache'

class StockError(ValueError):
    pass


def local_keys():
    try: return json.loads(SETTINGS.read_text())
    except (OSError,ValueError): return {}


def get_key(provider):
    return os.environ.get(PROVIDERS[provider][0],'').strip() or local_keys().get(provider,'')


def key_status():
    return {name:{'configured':bool(get_key(name)), 'environment':bool(os.environ.get(meta[0],''))} for name,meta in PROVIDERS.items()}


def save_keys(updates):
    with lock:
        data=local_keys()
        for name,value in updates.items():
            if value is None: continue
            if value.strip(): data[name]=value.strip()
            else: data.pop(name,None)
        # Never put keys into project JSON, the browser store, or downloadable packages.
        temp=SETTINGS.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump(data,f)
        os.chmod(temp,0o600)
        temp.replace(SETTINGS)
    return key_status()


def media_url(url,provider):
    parsed=urllib.parse.urlsplit(url)
    hosts={'pexels':{'videos.pexels.com','images.pexels.com'},'pixabay':{'cdn.pixabay.com'}}[provider]
    if parsed.scheme!='https' or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None,443):
        raise StockError('Сервис вернул неподдерживаемый адрес медиафайла.')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,provider):self.provider=provider
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        media_url(newurl,self.provider)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def api_response(provider,query,kind='video'):
    key=get_key(provider)
    if not key:raise StockError(f'Подключите {PROVIDERS[provider][1]}: добавьте API-ключ в настройках поиска футажей.')
    CACHE.mkdir(exist_ok=True)
    digest=hashlib.sha256((provider+'\0'+kind+'\0'+query+'\0'+key).encode()).hexdigest()
    cache=CACHE/(digest+'.json')
    try:
        cached=json.loads(cache.read_text())
        if time.time()-cached['saved']<86400:return cached['data']
    except (OSError,ValueError,KeyError):pass
    headers={'User-Agent':'KadrStudio/0.5','Accept':'application/json'}
    if provider=='pexels':
        path='videos/search' if kind=='video' else 'v1/search'
        url='https://api.pexels.com/'+path+'?'+urllib.parse.urlencode({'query':query,'per_page':12,'orientation':'landscape'})
        headers['Authorization']=key
    else:
        path='api/videos/' if kind=='video' else 'api/'
        params={'key':key,'q':query,'per_page':12,'safesearch':'true'}
        if kind=='video':params['video_type']='film'
        else:params['image_type']='photo';params['orientation']='horizontal'
        url='https://pixabay.com/'+path+'?'+urllib.parse.urlencode(params)
    try:
        req=urllib.request.Request(url,headers=headers)
        with open_with_retry(urllib.request.build_opener(NoRedirect(),https_handler()),req,25) as response:
            raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise StockError('Слишком большой ответ сервиса.')
        data=json.loads(raw)
    except StockError:
        raise
    except OSError as exc:
        raise StockError(connection_error(exc,PROVIDERS[provider][1])) from None
    except ValueError:
        raise StockError('Сервис вернул нечитаемый ответ. Повторите поиск позже.') from None
    temp=cache.with_name(cache.stem+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps({'saved':time.time(),'data':data}))
    temp.replace(cache)
    # Bound the local API cache, retaining the most recently used responses.
    for old in sorted(CACHE.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[128:]:old.unlink(missing_ok=True)
    return data


def footage_query(project,index):
    context=scene_query(project,index)
    # Photos of a manuscript and useful moving footage need different query wording.
    mapping={'Book of Enoch manuscript':'Enoch manuscript','Ethiopian illuminated manuscript':'Ethiopian manuscript',
             'Greek biblical manuscript':'ancient manuscript','illuminated manuscript parchment':'old book pages',
             'angel illuminated manuscript':'church painting','Last Judgment painting':'church fresco',
             'solitary person mountain path':'person hiking mountains','candle old book':'candle book'}
    return {**context,'query':mapping.get(context['query'],context['query'])}


def search_videos(provider,query):
    data=api_response(provider,query)
    found=[]
    for video in data.get('videos' if provider=='pexels' else 'hits',[]):
        files=(video.get('video_files',[]) if provider=='pexels' else list(video.get('videos',{}).values()))
        choices=[]
        for f in files:
            url=f.get('link') or f.get('url','')
            if f.get('file_type','video/mp4')!='video/mp4' or not f.get('width') or not f.get('height'):continue
            if f['width']<f['height'] or f['width']<640:continue
            try:media_url(url,provider)
            except StockError:continue
            choices.append({**f,'download':url})
        if not choices:continue
        # Prefer HD/Full HD, avoiding huge 4K downloads when smaller versions exist.
        fitting=[f for f in choices if f['width']<=1920]
        chosen=max(fitting,key=lambda f:f['width']) if fitting else min(choices,key=lambda f:f['width'])
        small=min(choices,key=lambda f:f['width'])
        source=video.get('url' if provider=='pexels' else 'pageURL','')
        host=urllib.parse.urlsplit(source).hostname
        if not source.startswith('https://') or host not in ({'www.pexels.com','pexels.com'} if provider=='pexels' else {'pixabay.com','www.pixabay.com'}):continue
        user=video.get('user',{})
        author=user.get('name','') if isinstance(user,dict) else str(user)
        thumb=video.get('image','') if provider=='pexels' else chosen.get('thumbnail','')
        try:media_url(thumb,provider)
        except StockError:thumb=''
        slug=urllib.parse.unquote(urllib.parse.urlsplit(source).path.strip('/').split('/')[-1])
        description=str(video.get('tags') or re.sub(r'-?\d+$','',slug).replace('-',' ')).strip()
        item={'token':uuid.uuid4().hex,'provider':provider,'title':description[:160] or 'Видео', 'description':description[:800],
              'url':chosen['download'],'preview_url':small['download'],'thumbnail':thumb,
              'source_url':source,'author':author[:300],'license':PROVIDERS[provider][1]+' License',
              'license_url':PROVIDERS[provider][2],'width':chosen['width'],'height':chosen['height'],
              'duration':float(video.get('duration',0)),'query':query}
        with lock:
            results[item['token']]=(time.monotonic(),item)
            while len(results)>256:results.popitem(last=False)
        found.append(item)
    return found


def search_photos(provider,query):
    data=api_response(provider,query,'photo')
    found=[]
    for photo in data.get('photos' if provider=='pexels' else 'hits',[]):
        if provider=='pexels':
            url=photo.get('src',{}).get('large','')
            source=photo.get('url','')
            title=str(photo.get('alt','')).strip()
            author=str(photo.get('photographer',''))
            width=photo.get('width',0);height=photo.get('height',0)
        else:
            url=photo.get('webformatURL','')
            source=photo.get('pageURL','')
            title=str(photo.get('tags','')).strip()
            author=str(photo.get('user',''))
            width=photo.get('webformatWidth',0);height=photo.get('webformatHeight',0)
        if not title or not width or not height or width<640 or width<height:continue
        try:media_url(url,provider)
        except StockError:continue
        host=urllib.parse.urlsplit(source).hostname
        if not source.startswith('https://') or host not in ({'www.pexels.com','pexels.com'} if provider=='pexels' else {'pixabay.com','www.pixabay.com'}):continue
        item={'token':uuid.uuid4().hex,'provider':provider,'title':title[:160],
              'description':title[:800],'url':url,'source_url':source,'author':author[:300],
              'license':PROVIDERS[provider][1]+' License','license_url':PROVIDERS[provider][2],
              'width':width,'height':height,'query':query,'kind':'image'}
        with lock:
            results[item['token']]=(time.monotonic(),item)
            while len(results)>256:results.popitem(last=False)
        found.append(item)
    return found


def download_media(url,provider,target,max_bytes=256*1024*1024):
    req=urllib.request.Request(media_url(url,provider),headers={'User-Agent':'KadrStudio/0.5'})
    # This request never receives an API credential, including across redirects.
    with open_with_retry(urllib.request.build_opener(MediaRedirect(provider),https_handler()),req,30) as response:
        media_url(response.geturl(),provider)
        size=0; started=time.monotonic()
        with target.open('wb') as out:
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>max_bytes:raise StockError('Материал слишком большой. Выберите другой.')
                if time.monotonic()-started>180:raise StockError('Загрузка заняла слишком много времени. Повторите позже.')
                out.write(chunk)


def import_photo(token):
    with lock:entry=results.get(token)
    if not entry or time.monotonic()-entry[0]>3600:raise StockError('Результат устарел. Выполните поиск повторно.')
    item=entry[1]
    if item.get('kind')!='image':raise StockError('Это не фотография.')
    identifier=uuid.uuid4().hex;folder=ASSETS/identifier;folder.mkdir()
    try:
        original=folder/'source'
        download_media(item['url'],item['provider'],original,16*1024*1024)
        with Image.open(original) as loaded:
            if loaded.width*loaded.height>24_000_000:raise StockError('Слишком большое изображение.')
            loaded.load();picture=ImageOps.exif_transpose(loaded).convert('RGB')
        picture.save(folder/'image.jpg',quality=94)
        picture.thumbnail((640,360));picture.save(folder/'thumb.jpg')
        original.unlink()
        meta={'id':identifier,'name':item['title'],'kind':'image','file':'image.jpg','duration':0,
              'created_at':time.time(),'size':(folder/'image.jpg').stat().st_size,'has_audio':False,
              'provenance':{k:item.get(k,'') for k in ('source_url','author','license','license_url','query','provider','description')}}
        (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False))
        return meta
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise


def import_video(token):
    with lock:entry=results.get(token)
    if not entry or time.monotonic()-entry[0]>3600:raise StockError('Результат устарел. Выполните поиск повторно.')
    item=entry[1]; identifier=uuid.uuid4().hex; folder=ASSETS/identifier; folder.mkdir()
    try:
        source=folder/'source'
        download_media(item['url'],item['provider'],source)
        info=json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov',
                             '-show_format','-show_streams','-of','json',str(source)],timeout=30).stdout)
        stream=next((s for s in info['streams'] if s['codec_type']=='video'),None)
        duration=float(info.get('format',{}).get('duration',0))
        if not stream or not 0<duration<=36000 or stream['width']*stream['height']>24_000_000:
            raise StockError('Не удалось прочитать футаж или его размер слишком велик.')
        run(['ffmpeg','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov','-i',str(source),
             '-frames:v','1','-vf','scale=640:360:force_original_aspect_ratio=decrease','-threads','2',str(folder/'thumb.jpg')],timeout=60)
        meta={'id':identifier,'name':item['title']+'.mp4','kind':'video','file':'source','duration':duration,'created_at':time.time(),
              'size':source.stat().st_size,'has_audio':any(s['codec_type']=='audio' for s in info['streams']),
              'provenance':{k:item.get(k,'') for k in ('source_url','author','license','license_url','query','provider','description')}}
        (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False))
        return meta
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise
