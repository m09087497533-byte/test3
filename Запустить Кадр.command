#!/bin/bash
# Двойной щелчок: подготовить отдельную установку и открыть приложение.
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/opt/python@3.12/libexec/bin:/usr/local/opt/python@3.12/libexec/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

fail() {
  printf '\nНе удалось запустить Кадр. %s\n' "$1"
  read -r -p 'Нажмите Enter, чтобы закрыть окно: ' _
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail 'Установите Python 3.12: brew install python@3.12 или установщик с python.org.'
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail 'Установите Python 3.12: brew install python@3.12 или установщик с python.org.'
python3 scripts/migrate_install.py .
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    printf 'Устанавливаем FFmpeg через Homebrew…\n'
    brew install ffmpeg || fail 'Не удалось установить FFmpeg через Homebrew.'
  else
    fail 'Установите Homebrew с brew.sh, затем выполните brew install ffmpeg.'
  fi
fi
runtime_python="$(python3 scripts/install_runtime.py .)" || fail 'Не удалось подготовить Python. Причина указана выше.'
printf '\nОткрываем Кадр. Оставьте это окно открытым.\n'
"$runtime_python" run.py
status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  fail 'Программа остановилась. Сообщение об ошибке находится выше.'
fi
