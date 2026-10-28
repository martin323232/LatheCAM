#!/usr/bin/env bash
# Real-GUI regression test for LatheCAM: runs FreeCAD offscreen (no X
# needed) with a harness Mod that activates the workbench and exercises
# every command through the real Gui.runCommand path.
# Exit 0 = HARNESS PASS.
set -u
FREECAD="${FREECAD_BIN:-$HOME/tools/freecad/squashfs-root/usr/bin/freecad}"
MODDIR="$HOME/.local/share/FreeCAD/v1-1/Mod"
HARNESS_SRC="$(cd "$(dirname "$0")/gui_harness" && pwd)"
LINK="$MODDIR/ZZHarness"

rm -rf "$LINK"
mkdir -p "$MODDIR"
ln -s "$HARNESS_SRC" "$LINK"
trap 'rm -rf "$LINK"' EXIT

OUT=/tmp/lathe_harness_console.log
rm -f /tmp/lathe_harness_result.txt
# QT_LOGGING_RULES: FreeCAD 1.1 deadlocks offscreen without it. Its
# NotificationArea::showInNotificationArea() holds a non-recursive
# mutex while showing the bubble; showing a widget calls
# QWidget::raise(), which the offscreen platform plugin refuses with a
# qWarning; FreeCAD's Qt message handler routes that warning into
# Console -> NotificationArea::pushNotification, which re-locks the
# same mutex on the same thread -> main thread deadlocks ~1 s after
# startup, before any QTimer ever fires (verified with gdb backtrace).
# Disabling Qt warnings keeps them out of FreeCAD's handler entirely.
QT_QPA_PLATFORM=offscreen QT_LOGGING_RULES='*.warning=false' \
    "$FREECAD" >"$OUT" 2>&1 &
PID=$!
for _ in $(seq 1 60); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
done
kill "$PID" 2>/dev/null
RES=/tmp/lathe_harness_result.txt
[ -f "$RES" ] && cat "$RES"
grep -aq "HARNESS PASS" "$RES" 2>/dev/null
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "--- console log: $OUT ---"
    tail -30 "$OUT" 2>/dev/null
fi
if [ "$RC" -eq 0 ]; then echo "GUI HARNESS: PASS"; else echo "GUI HARNESS: FAIL"; fi
[ "$RC" -eq 0 ]
