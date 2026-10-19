"""Local environment diagnostic, no requests to inference providers."""
import importlib.util
import shutil
import sys
from pathlib import Path
if __name__ == '__main__':
    print('Python:', sys.version.split()[0])
    for module in ['tkinter','torch','transformers','faster_whisper','PIL']:
        print(module, bool(importlib.util.find_spec(module)))
    print('FFmpeg:', bool(shutil.which('ffmpeg')))
    print('ffprobe:', bool(shutil.which('ffprobe')))
    print('Launch: python3 main.py; one-time setup: python3 setup_local.py')
