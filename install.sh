#!/bin/bash
# 메뉴 봇을 이 Mac의 launchd 스케줄러에 등록한다.
# 평일(월~금) 10:45 중식, 17:20 석식 체크. 여러 번 실행해도 안전하다(재등록).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PYTHON="$REPO_DIR/.venv/bin/python3"
LOG="$REPO_DIR/launchd.log"

if [ ! -f "$REPO_DIR/.env" ]; then
    echo "오류: $REPO_DIR/.env 가 없습니다 (TEAMS_WEBHOOK_URL 필요)." >&2
    exit 1
fi

# 1. 가상환경 + 의존성
if [ ! -x "$PYTHON" ]; then
    echo "가상환경 생성: $REPO_DIR/.venv"
    python3 -m venv "$REPO_DIR/.venv"
fi
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -r "$REPO_DIR/requirements.txt"

# 2. plist 생성 + 등록
mkdir -p "$AGENTS_DIR"

write_plist() {  # $1=label $2=hour $3=minute
    local plist="$AGENTS_DIR/$1.plist"
    {
        cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$1</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$REPO_DIR/scraper.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>

    <key>StartCalendarInterval</key>
    <array>
EOF
        for weekday in 1 2 3 4 5; do
            cat <<EOF
        <dict>
            <key>Hour</key><integer>$2</integer>
            <key>Minute</key><integer>$3</integer>
            <key>Weekday</key><integer>$weekday</integer>
        </dict>
EOF
        done
        cat <<EOF
    </array>

    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF
    } > "$plist"

    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "등록: $1 (평일 $2:$3)"
}

write_plist com.menubot.lunch 10 45
write_plist com.menubot.dinner 17 20

echo
echo "완료. 확인: launchctl list | grep menubot"
echo "로그: $LOG"
