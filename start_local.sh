#!/bin/bash
# MOPAN 로컬 기동(Docker 없음): backend 8010, arq worker, 동봉 MCP 8100, 프론트 3000. 재실행하면 다시 띄운다.
# 외부 접속 주소(cloudflared)는 살아 있으면 유지하고, 죽었을 때만 새로 받아 ../MOPAN-접속주소.txt에 쓰고 메일로 알린다.
cd "$(dirname "$0")/backend" || exit 1
mkdir -p ../logs
# 패턴을 argv[0]에 고정 - 이 스크립트 자신의 명령줄이 pkill -f에 잡히지 않게.
pkill -f '^\.\./\.venv/bin/python -m (uvicorn|arq) ' ; sleep 2
DBURL=$(grep -E '^DATABASE_URL=' ../.env | cut -d= -f2-)
# 답변 모델(gemma4:26b)은 vLLM이 8001에서 낸다(docs/local-llm-concurrency.md). 이미 떠 있으면 그대로,
# 아니면 띄운다 - 가중치 로드 ~20초 + 그래프 캡처 ~1분이라 백엔드 기동을 막지 않는다. 백엔드는 기동 시
# /v1/models로 vLLM을 발견하며, 그때 아직 안 떠 있었으면 관리자 화면 "모델 → 새로고침"이 다시 발견한다.
../scripts/start_vllm.sh
DATABASE_URL="$DBURL" nohup ../.venv/bin/python -m uvicorn app.examples_mcp.main:app --host 127.0.0.1 --port 8100 >> ../logs/mcp-examples.log 2>&1 &
sleep 3   # 시딩이 발견까지 하려면 MCP가 먼저 떠 있어야 한다.
nohup ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 >> ../logs/backend.log 2>&1 &
nohup ../.venv/bin/python -m arq app.worker.WorkerSettings >> ../logs/worker.log 2>&1 &
sleep 8
pgrep -af '^\.\./\.venv/bin/python -m'
curl -s -m 5 -o /dev/null -w 'mcp-examples /healthz %{http_code}\n' http://127.0.0.1:8100/healthz
curl -s -m 5 -o /dev/null -w 'backend /api/models %{http_code} (401=살아있음)\n' http://127.0.0.1:8010/api/models
# 프론트(운영 빌드, 3000). 코드가 바뀌었으면 먼저: cd frontend && API_INTERNAL_URL=http://localhost:8010 npm run build
pkill -f '^next-server'; pkill -f '^npm start'; sleep 1   # 앵커 필수 - 이 스크립트 자신을 잡지 않게
(cd ../frontend && nohup npm start >> ../logs/frontend.log 2>&1 &)
sleep 5; curl -s -m 5 -o /dev/null -w 'frontend / %{http_code}\n' http://127.0.0.1:3000/

# ── 외부 접속 주소 ──────────────────────────────────────────────────────────
# quick tunnel은 켤 때마다 이름이 바뀌므로 살아 있으면 건드리지 않는다.
# 530(Cloudflare 1016/1033 = 터널 등록 만료)이나 000(응답 없음)일 때만 새로 띄운다.
ADDR_FILE=../../MOPAN-접속주소.txt
REPORT=../../mopan-report/report.py
URL=$(cat "$ADDR_FILE" 2>/dev/null)
CODE=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "${URL:-http://127.0.0.1:1}/api/health")
if [ -n "$URL" ] && [ "$CODE" != 000 ] && [ "$CODE" != 530 ]; then
    echo "외부 접속 주소 유지: $URL"
else
    pkill -f '^/usr/local/bin/cloudflared tunnel .*127\.0\.0\.1:3000'; sleep 1
    : > ../logs/cloudflared.log
    nohup /usr/local/bin/cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:3000 >> ../logs/cloudflared.log 2>&1 &
    URL=""
    for _ in $(seq 1 40); do
        sleep 2
        URL=$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' ../logs/cloudflared.log | head -1)
        [ -n "$URL" ] && break
    done
    if [ -n "$URL" ]; then
        # 주소를 받은 경우에만 쓴다 - 실패 시 빈 값으로 덮으면 살아 있던 주소까지 잃는다
        echo "$URL" > "$ADDR_FILE"; echo "새 외부 접속 주소: $URL"
        for _ in $(seq 1 15); do sleep 2; curl -sf -o /dev/null -m 10 "$URL/api/health" && break; done   # 등록 직후 잠깐 1033
        if /usr/bin/python3 "$REPORT" --new-url >> ../../mopan-report/report.log 2>&1; then
            echo "새 접속 주소 메일 발송"
        else
            echo "새 접속 주소 메일 발송 실패 (mopan-report/report.log 확인)"
        fi
    else
        echo "외부 주소를 받지 못했습니다 (logs/cloudflared.log 확인)"
    fi
fi
