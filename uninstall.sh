#!/bin/bash
# 메뉴 봇을 이 Mac의 launchd 스케줄러에서 해제한다. (코드/.venv/로그는 건드리지 않음)
set -uo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"

for label in com.menubot.lunch com.menubot.dinner; do
    plist="$AGENTS_DIR/$label.plist"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    if [ -f "$plist" ]; then
        rm "$plist"
        echo "해제: $label"
    else
        echo "없음: $label (이미 해제됨)"
    fi
done
