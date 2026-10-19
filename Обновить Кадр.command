#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/opt/python@3.12/libexec/bin:/usr/local/opt/python@3.12/libexec/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
python3 scripts/update_mac.py
status=$?
if [ "$status" -ne 0 ]; then
  read -r -p 'Не удалось обновить Кадр. Сообщение выше. Нажмите Enter: ' _
fi
exit "$status"
