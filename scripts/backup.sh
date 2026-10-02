#!/usr/bin/env bash
# MOPAN 주간 백업 → Lustre(/NHNHOME/.../soonkun/backups/YYYYMMDD/). 컨테이너 루트 오버레이에만 있는 것들을 남긴다:
#   - Postgres `mopan` DB (pg_dump -Fc, 압축 커스텀 형식 - 사용자·문서 메타·청크·임베딩 22GB)
#   - Redis 스냅샷 (세션·큐 - 없어도 재로그인으로 복구되지만 있으면 편하다)
#   - /var/lib/mopan-code/users (코워크 작업 폴더·코드 작업 공간·opencode 세션 DB) - bin/node20은 opt/에서 다시 복사
#   - MOPAN/.env (비밀번호가 여기만 있다)
# 업로드 원본(data/uploads)·모델·소스는 이미 Lustre라 뺀다. 보존 KEEP회분. 복구 절차는 저장소 밖 문서(서버의 MOPAN-private-docs/)에 있다
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # …/soonkun
MOPAN="$ROOT/MOPAN"
DEST="$ROOT/backups/$(date +%Y%m%d)"
KEEP=${KEEP:-6}
mkdir -p "$DEST"; chmod 700 "$ROOT/backups" "$DEST"
started=$(date +%s)
echo "[$(date '+%F %T')] backup → $DEST"

# 1. Postgres (peer 인증: postgres OS 사용자로) - 병렬 없이 단일 파일, 상태 표시는 로그로
runuser -u postgres -- pg_dump -Fc --no-owner --file=/tmp/mopan.dump mopan
mv /tmp/mopan.dump "$DEST/mopan.dump"
# 2. Redis - 비밀번호는 .env에서
RD_PW=$(grep -E '^REDIS_PASSWORD=' "$MOPAN/.env" | cut -d= -f2-)
redis-cli -a "$RD_PW" --no-auth-warning --rdb "$DEST/redis.rdb" >/dev/null 2>&1 || echo "  redis snapshot skipped"
# 3. 코드/코워크 작업 데이터 + .env
tar -czf "$DEST/mopan-code-users.tgz" -C /var/lib/mopan-code users 2>/dev/null || echo "  mopan-code skipped"
cp -p "$MOPAN/.env" "$DEST/env"; chmod 600 "$DEST/env"
# 4. 검증 가능한 목록 + 보존 정리
chmod 600 "$DEST"/*
( cd "$DEST" && sha256sum * > SHA256SUMS )
ls -1d "$ROOT"/backups/2* | sort | head -n -"$KEEP" | xargs -r rm -rf
du -sh "$DEST" | awk -v s="$started" '{printf "[%s] done %s in %ds\n", strftime("%F %T"), $1, systime()-s}'
