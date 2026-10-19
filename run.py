"""Run the local studio and open its UI after the server is ready."""
import argparse
import errno
import socket
import threading
import time
import urllib.request
import webbrowser


def reserve_port(port):
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',port));listener.listen(2048)
        return listener
    except BaseException:
        listener.close();raise


def main():
    parser = argparse.ArgumentParser(description='Кадр — локальная видеомастерская')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Порт должен быть от 1 до 65535.')
    import shutil
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        parser.exit(1, 'Нужен FFmpeg. Инструкции установки — в README.md.\n')
    try:
        import uvicorn
    except ImportError:
        parser.exit(1, 'Сначала установи зависимости: python3 -m pip install -r requirements.txt\n')
    try:listener=reserve_port(args.port)
    except OSError as exc:
        if exc.errno==errno.EADDRINUSE:
            parser.exit(1,f'Порт {args.port} уже занят. Вероятно, Кадр запущен в другом окне Терминала.\nОстановите старую программу Control+C в том окне, затем повторите запуск.\n')
        parser.exit(1,f'Не удалось занять порт {args.port}: {exc}\n')
    url = f'http://127.0.0.1:{args.port}' 
    if not args.no_browser:
        def open_when_ready():
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url+'/api/health', timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(.1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server=uvicorn.Server(uvicorn.Config('studio.app:app',host='127.0.0.1',port=args.port))
        server.run(sockets=[listener])
    finally:listener.close()


if __name__ == '__main__':
    main()
