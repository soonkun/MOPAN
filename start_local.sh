#!/bin/bash
# MOPAN 로컬 기동(Docker 없음): backend 8010, arq worker, 동봉 MCP 8100. 재실행하면 셋을 다시 띄운다.
cd "$(dirname "$0")/backend" || exit 1
mkdir -p ../logs
# 패턴을 argv[0]에 고정 - 이 스크립트 자신의 명령줄이 pkill -f에 잡히지 않게.
pkill -f '^\.\./\.venv/bin/python -m (uvicorn|arq) ' ; sleep 2
DBURL=$(grep -E '^DATABASE_URL=' ../.env | cut -d= -f2-)
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
