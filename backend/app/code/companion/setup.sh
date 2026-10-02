#!/usr/bin/env bash
# MOPAN 코드 - 내 컴퓨터 연결(macOS/Linux). 코드 탭의 "내 컴퓨터 연결"이 만들어 준 한 줄 명령이 이 파일을 받아 실행한다.
#   MOPAN_SERVER='https://...' MOPAN_TOKEN='...' bash -c "$(curl -fsSL https://.../api/code/setup.sh)"
# 하는 일: 폴더 고르기(MOPAN_DIR를 주면 생략) → Node.js 확인 → OpenCode 없으면 설치 → 컴패니언을 ~/.mopan 에 내려받기 → 다음부터 실행할 connect.sh 만들기 → 연결.
set -euo pipefail
: "${MOPAN_SERVER:?코드 탭 > 내 컴퓨터 연결에서 만든 명령을 그대로 붙여 넣어 주세요}"
: "${MOPAN_TOKEN:?토큰이 없습니다 - 코드 탭에서 만든 명령을 그대로 붙여 넣어 주세요}"
# 브라우저는 PC 폴더의 전체 경로를 알 수 없어서 폴더는 여기서 고른다: macOS는 Finder 창, Linux는 zenity 창, 없으면 입력.
if [ -z "${MOPAN_DIR:-}" ]; then
  if [ "$(uname)" = Darwin ]; then
    MOPAN_DIR=$(osascript -e 'POSIX path of (choose folder with prompt "MOPAN 에이전트가 읽고 쓸 폴더를 고르세요")' 2>/dev/null || true)
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null 2>&1; then
    MOPAN_DIR=$(zenity --file-selection --directory --title "MOPAN 에이전트가 읽고 쓸 폴더" 2>/dev/null || true)
  else
    read -rp "연결할 폴더 경로: " MOPAN_DIR </dev/tty
  fi
  [ -n "$MOPAN_DIR" ] || { echo "폴더를 고르지 않아 멈췄습니다. 같은 명령을 다시 실행하면 됩니다."; exit 1; }
fi
[ -d "$MOPAN_DIR" ] || { echo "폴더가 없습니다: $MOPAN_DIR"; exit 1; }
DIR=$(cd "$MOPAN_DIR" && pwd)
BASE="$HOME/.mopan"; mkdir -p "$BASE"

# 1. Node.js 22+
if ! command -v node >/dev/null 2>&1; then
  echo; echo "Node.js가 없습니다. https://nodejs.org 에서 LTS(22 이상)를 설치한 뒤(macOS: brew install node) 이 명령을 다시 실행해 주세요."; exit 1
fi
MAJOR=$(node -v | sed 's/^v//' | cut -d. -f1)
[ "$MAJOR" -ge 22 ] || { echo "Node.js $(node -v)는 오래되었습니다. 22 이상으로 올려 주세요(https://nodejs.org)."; exit 1; }

# 2. OpenCode - 전역 설치가 권한 문제로 막히면 ~/.mopan/npm 에 설치해 경로를 직접 넘긴다
OPENCODE=$(command -v opencode || true)
if [ -z "$OPENCODE" ] && [ -x "$BASE/npm/bin/opencode" ]; then OPENCODE="$BASE/npm/bin/opencode"; fi
if [ -z "$OPENCODE" ]; then
  echo "OpenCode를 설치합니다(npm install -g opencode-ai) - 1~2분 걸립니다 ..."
  if npm install -g opencode-ai >/dev/null 2>&1; then
    OPENCODE=$(command -v opencode || echo "$(npm prefix -g)/bin/opencode")
  else
    npm install -g --prefix "$BASE/npm" opencode-ai
    OPENCODE="$BASE/npm/bin/opencode"
  fi
  [ -x "$OPENCODE" ] || { echo "OpenCode 설치에 실패했습니다. 'npm install -g opencode-ai'를 직접 실행한 뒤 다시 시도해 주세요."; exit 1; }
fi

# 3. 컴패니언 내려받기(항상 최신으로 덮어쓴다)
MJS="$BASE/mopan-code.mjs"
curl -fsSL -H "Authorization: Bearer $MOPAN_TOKEN" "$MOPAN_SERVER/api/code/companion" -o "$MJS"

# 4. 다음부터 실행할 스크립트
cat > "$BASE/connect.sh" <<EOS
#!/usr/bin/env bash
exec node "$MJS" --server "$MOPAN_SERVER" --token "$MOPAN_TOKEN" --dir "$DIR" --opencode "$OPENCODE"
EOS
chmod 700 "$BASE/connect.sh" "$MJS" 2>/dev/null || true

echo; echo "준비 끝. 다음부터는 이렇게 실행하면 바로 연결됩니다:  $BASE/connect.sh"
echo "연결하는 폴더: $DIR   (이 터미널을 닫으면 연결이 끊어집니다)"; echo
exec node "$MJS" --server "$MOPAN_SERVER" --token "$MOPAN_TOKEN" --dir "$DIR" --opencode "$OPENCODE"
