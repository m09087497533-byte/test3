#!/bin/bash
# Двойной щелчок: подготовить отдельную установку и открыть приложение.
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

fail() {
  printf '\nНе удалось запустить Кадр. %s\n' "$1"
  read -r -p 'Нажмите Enter, чтобы закрыть окно: ' _
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail 'Установите Python 3.10+ через Homebrew: brew install python'
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail 'Нужен Python 3.10+. Установите его: brew install python'
python3 scripts/migrate_install.py .
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    printf 'Устанавливаем FFmpeg через Homebrew…\n'
    brew install ffmpeg || fail 'Не удалось установить FFmpeg через Homebrew.'
  else
    fail 'Установите Homebrew с brew.sh, затем выполните brew install ffmpeg.'
  fi
fi
if [ ! -x .venv/bin/python ]; then
  printf 'Готовим эту папку Кадра…\n'
  python3 -m venv .venv || fail 'Не удалось создать окружение Python.'
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, PIL, multipart' >/dev/null 2>&1; then
  printf 'Устанавливаем библиотеки Кадра…\n'
  .venv/bin/python -m pip install -r requirements.txt || fail 'Не удалось установить библиотеки. Проверьте подключение к интернету.'
fi
if ! .venv/bin/python -c 'import faster_whisper, fastembed' >/dev/null 2>&1; then
  printf 'Устанавливаем распознавание речи и смысловую модель…\n'
  if ! .venv/bin/python -m pip install -r requirements-ai.txt; then
    printf 'Дополнительные модели не установлены. Можно работать с SRT/VTT; повторите запуск при доступном интернете.\n'
  fi
fi
printf '\nОткрываем Кадр. Оставьте это окно открытым.\n'
.venv/bin/python run.py
status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  fail 'Программа остановилась. Сообщение об ошибке находится выше.'
fi
