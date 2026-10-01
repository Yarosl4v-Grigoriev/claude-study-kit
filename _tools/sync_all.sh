#!/bin/bash
# Единая точка входа: заметки + Freeform. Аргументы (--redo/--dry) уходят в оба скрипта.
cd "$(dirname "$0")/.." || exit 1
# Приложения, запущенные из Finder, получают урезанный PATH — добавляем типовые места python3
export PATH="/opt/homebrew/bin:/usr/local/bin:$(ls -d /Library/Frameworks/Python.framework/Versions/*/bin 2>/dev/null | tail -1):$PATH:/usr/bin:/bin"
python3 _tools/sync_notes.py "$@" 2>&1
python3 _tools/sync_freeform.py "${@/--redo/}" 2>&1
