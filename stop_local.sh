#!/bin/bash
# MOPAN 로컬 종료: backend 8010, arq worker, MCP 8100, 프론트 3000. start_local.sh의 pkill 패턴과 동일.
# cloudflared 터널은 건드리지 않는다(죽이면 접속주소가 바뀜).
pkill -f '^\.\./\.venv/bin/python -m (uvicorn|arq) '
pkill -f '^next-server'; pkill -f '^npm start'
sleep 2
pgrep -af '^\.\./\.venv/bin/python -m|^next-server|^npm start' && echo '아직 남은 프로세스 있음' || echo 'MOPAN 종료됨'
