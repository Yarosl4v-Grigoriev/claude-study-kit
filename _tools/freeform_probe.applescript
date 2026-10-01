-- Диагностика Freeform: печатает дерево интерфейса окна (нужен доступ «Универсальный доступ»).
tell application "Freeform" to activate
delay 1.5
tell application "System Events"
  tell process "Freeform"
    return entire contents of window 1
  end tell
end tell
