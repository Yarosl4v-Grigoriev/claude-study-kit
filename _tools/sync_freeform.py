#!/usr/bin/env python3
"""Freeform → PDF в <предмет>/Конспект_с_пада/ (только доски с меткой «<Предмет>-<тип>» в названии).
Экспорт делается через интерфейс Freeform (нужен «Универсальный доступ» для запускающего приложения).
Уже выгруженные доски (по названию) пропускаются. Реестр: .source/sync_state.json, ключ "freeform".

Запуск: python3 _tools/sync_freeform.py [--dry]
"""
import json, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_notes as sn

DOWNLOADS = Path.home() / "Downloads"

AS_HELPERS = '''
on goGallery()
  tell application "System Events" to tell process "Freeform"
    set frontmost to true
    delay 1
    repeat 3 times
      set btn to missing value
      set els to entire contents of window 1
      repeat with i from 1 to count of els
        set e to item i of els
        try
          if (role of e) is "AXButton" and ((description of e) as string) is "Назад" then
            set btn to e
            exit repeat
          end if
        end try
      end repeat
      if btn is missing value then exit repeat
      perform action "AXPress" of btn
      delay 1.2
    end repeat
  end tell
end goGallery
'''


def osa_file(script, *args):
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.rstrip("\n")


def ensure_freeform():
    subprocess.run(["open", "-a", "Freeform"], check=True)
    subprocess.run(["osascript", "-e", 'tell application "Freeform" to activate'], capture_output=True)
    for _ in range(40):
        out = subprocess.run(["osascript", "-e",
                              'tell application "System Events" to tell process "Freeform" to count windows'],
                             capture_output=True, text=True).stdout.strip()
        if out.isdigit() and int(out) > 0:
            time.sleep(1)
            return
        subprocess.run(["osascript", "-e", 'tell application "Freeform" to activate'], capture_output=True)
        time.sleep(0.5)
    raise RuntimeError("Freeform не показал окно")


def list_boards():
    for attempt in range(3):
        try:
            return _list_boards()
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(2)


def _list_boards():
    ensure_freeform()
    out = osa_file(AS_HELPERS + '''
goGallery()
delay 1
tell application "System Events" to tell process "Freeform"
  set res to ""
  set seenBtn to false
  set els to entire contents of window 1
      repeat with i from 1 to count of els
        set e to item i of els
    try
      set r to role of e
      if r is "AXButton" then
        set s to size of e
        if (item 1 of s) > 150 and (item 2 of s) > 150 then set seenBtn to true
      else if r is "AXStaticText" and seenBtn then
        set v to (value of e) as string
        if v is not "" and v is not " " then
          set res to res & v & linefeed
          set seenBtn to false
        end if
      end if
    end try
  end repeat
  return res
end tell''')
    return [l for l in out.splitlines() if l.strip()]


def export_board(name, target):
    """Открыть доску, экспортировать PDF в Загрузки и перенести в target."""
    pre = DOWNLOADS / f"{name}.pdf"
    backup = None
    if pre.exists():  # чужой файл с тем же именем — временно убрать
        backup = pre.with_name(f"{name}.pdf.bak-{int(time.time())}")
        pre.rename(backup)
    try:
        osa_file(AS_HELPERS + '''
on run argv
  set nm to item 1 of argv
  goGallery()
  tell application "System Events" to tell process "Freeform"
    set el to entire contents of window 1
    set lastBtn to missing value
    set found to false
    repeat with i from 1 to count of el
      set e to item i of el
      try
        set r to role of e
        if r is "AXButton" then
          set s to size of e
          if (item 1 of s) > 150 and (item 2 of s) > 150 then set lastBtn to e
        else if r is "AXStaticText" and lastBtn is not missing value then
          if ((value of e) as string) is nm then
            perform action "AXPress" of lastBtn
            set found to true
            exit repeat
          end if
        end if
      end try
    end repeat
    if not found then error "доска не найдена: " & nm
    -- ждём открытия доски (появляется кнопка «Назад»)
    repeat 40 times
      delay 0.5
      set hasBack to false
      set els to entire contents of window 1
      repeat with i from 1 to count of els
        set e to item i of els
        try
          if (role of e) is "AXButton" and ((description of e) as string) is "Назад" then
            set hasBack to true
            exit repeat
          end if
        end try
      end repeat
      if hasBack then exit repeat
    end repeat
    delay 1.5
    set p to position of window 1
    set sz to size of window 1
    click at {(item 1 of p) + (item 1 of sz) / 2, (item 2 of p) + (item 2 of sz) / 2}
    delay 1
    click menu item "Экспортировать доску как PDF…" of menu 1 of menu bar item "Файл" of menu bar 1
    repeat 40 times
      delay 0.5
      if (exists window "Экспортировать как PDF") then exit repeat
    end repeat
    set w to window "Экспортировать как PDF"
    delay 1
    set o to outline 1 of scroll area 1 of splitter group 1 of w
    repeat with rw in rows of o
      try
        set v to (value of static text 1 of UI element 1 of rw) as string
        if v is in {"Загрузки", "Downloads"} then
          select rw
          exit repeat
        end if
      end try
    end repeat
    delay 1
    key code 36
    repeat 60 times
      delay 0.5
      if not (exists window "Экспортировать как PDF") then exit repeat
    end repeat
  end tell
end run''', name)
        for _ in range(60):
            if pre.exists() and time.time() - pre.stat().st_mtime < 120:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"PDF не появился в Загрузках: {name}")
        time.sleep(1)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pre), str(target))
    finally:
        if backup and backup.exists() and not pre.exists():
            backup.rename(pre)
        try:
            osa_file(AS_HELPERS + "goGallery()")
        except Exception:
            pass


def main():
    dry = "--dry" in sys.argv
    state = json.loads(sn.STATE.read_text()) if sn.STATE.exists() else {}
    fstate = state.setdefault("freeform", {})
    done, bad = [], []
    for name in list_boards():
        lab = sn.parse_label(name.replace("\xa0", " "))
        if not lab:
            continue
        if name in fstate:
            continue
        subj, kind = lab
        date = sn.note_date({"name": name, "created": datetime.now().strftime("%Y-%m-%d")})
        folder = sn.ROOT / subj / "Конспект_с_пада"
        base = f"{kind} {subj} {date}"
        target, k = folder / f"{base}.pdf", 2
        while target.exists():
            target = folder / f"{base} ({k}).pdf"
            k += 1
        if dry:
            done.append(f"[dry] {name} → {target.relative_to(sn.ROOT)}")
            continue
        try:
            export_board(name, target)
        except Exception as e:
            bad.append(f"{name}: {e}")
            continue
        fstate[name] = {"file": str(target.relative_to(sn.ROOT)),
                        "exported": datetime.now().isoformat(timespec="seconds")}
        sn.STATE.parent.mkdir(exist_ok=True)
        sn.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        done.append(str(target.relative_to(sn.ROOT)))
    print(f"Freeform: новых выгружено {len(done)}")
    for x in done: print("  +", x)
    for x in bad: print("  !", x)


if __name__ == "__main__":
    main()
