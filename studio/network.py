"""Verified HTTPS using system roots plus certifi; redact URL-bearing errors."""
import socket
import ssl
import urllib.error
import urllib.request
import time
from functools import lru_cache
import certifi


@lru_cache(maxsize=1)
def tls_context():
    context=ssl.create_default_context()
    # Python distributions on macOS may not load the system certificate roots.
    # Add maintained public roots without disabling verification or removing local roots.
    context.load_verify_locations(cafile=certifi.where())
    return context


def https_handler():
    return urllib.request.HTTPSHandler(context=tls_context())


def open_with_retry(opener,request,timeout):
    """Honor short rate-limit windows without retrying authentication failures."""
    for attempt in range(3):
        try:return opener.open(request,timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code!=429 or attempt==2:raise
            try:delay=float(exc.headers.get('Retry-After',0))
            except (TypeError,ValueError):delay=0
            time.sleep(min(12,max(1,delay or 2**attempt)))


def connection_error(exc,service='Сервис'):
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(exc,urllib.error.HTTPError):
        code=exc.code
        if code==401:return f'{service}: API-ключ не принят (HTTP 401). Проверьте ключ этого сервиса.'
        if code==403:return f'{service}: сервер запретил доступ (HTTP 403). Причиной могут быть права API-ключа или ограничения сети.'
        if code==429:return f'{service}: превышен лимит запросов (HTTP 429). Повторите позже.'
        if code==400:return f'{service}: запрос отклонён (HTTP 400). Проверьте API-ключ и поисковую фразу.'
        return f'{service}: сервер ответил HTTP {code}. Повторите позже.'
    description=str(reason).lower()
    if isinstance(reason,ssl.SSLCertVerificationError) or 'certificate_verify_failed' in description:
        return f'{service}: Python не доверяет сертификату HTTPS (SSL). Обновите зависимости программы; если ошибка остаётся, проверьте сертификаты используемого прокси.'
    if 'tunnel connection failed' in description:
        return f'{service}: соединение отклонено прокси. Проверьте настройки доступа прокси/VPN; это не ошибка поисковой фразы.'
    if isinstance(reason,socket.gaierror):
        return f'{service}: не удалось найти адрес сервера (DNS). Проверьте интернет и настройки DNS.'
    if isinstance(reason,(TimeoutError,socket.timeout)):
        return f'{service}: сервер не ответил вовремя. Проверьте подключение и повторите поиск.'
    if isinstance(reason,ssl.SSLError):
        return f'{service}: не удалось установить защищённое соединение TLS. Проверьте настройки сети и прокси.'
    if isinstance(reason,ConnectionRefusedError):
        return f'{service}: соединение отклонено. Проверьте сетевой доступ к сервису.'
    return f'{service}: соединение прервано или недоступно. Нажмите «Проверить подключение» для проверки каждого сервиса.'


def diagnostics():
    from concurrent.futures import ThreadPoolExecutor
    targets=[('Wikimedia Commons','https://commons.wikimedia.org/w/api.php?action=query&meta=siteinfo&format=json'),
             ('Pexels','https://api.pexels.com/videos/search?query=forest&per_page=1'),
             ('Pixabay','https://pixabay.com/api/videos/?q=forest&per_page=3')]
    def check(target):
        name,url=target
        # Public connectivity probe: API keys are deliberately not included.
        req=urllib.request.Request(url,headers={'User-Agent':'KadrStudio/0.5.1'})
        try:
            with urllib.request.build_opener(https_handler()).open(req,timeout=8) as response:
                response.read(256)
                return {'service':name,'ok':True,'message':'HTTPS работает. Это проверка соединения, не API-ключа.'}
        except urllib.error.HTTPError as exc:
            if exc.code in (400,401):
                return {'service':name,'ok':True,'message':f'HTTPS работает (ответ {exc.code} без ключа). Проверьте API-ключ поиском в приложении.'}
            return {'service':name,'ok':False,'message':connection_error(exc,name)}
        except OSError as exc:
            return {'service':name,'ok':False,'message':connection_error(exc,name)}
    with ThreadPoolExecutor(max_workers=3) as pool:return list(pool.map(check,targets))
