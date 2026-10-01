#!/bin/bash
# Собирает «Синхронизация.app» в корне проекта: двойной клик = _tools/sync_all.sh.
# Приложение ищет скрипт рядом с собой, так что папку проекта можно переносить.
cd "$(dirname "$0")/.." || exit 1
osacompile -o "Синхронизация.app" <<'AS'
try
	set base to POSIX path of ((path to me as text) & "::")
	set r to do shell script quoted form of (base & "_tools/sync_all.sh")
	display dialog r buttons {"OK"} default button 1 with title "ClaudeStudy: синхронизация"
on error e
	display dialog "Ошибка: " & e buttons {"OK"} default button 1 with icon stop
end try
AS
echo "Готово: $(pwd)/Синхронизация.app"
