#!/usr/bin/env bash
# 코드/코워크 샌드박스(uid 60000~60999, CODE_UID_BASE)의 네트워크를 잠근다 - 이 백엔드(127.0.0.1:8010)만 허용, 나머지 전부 거절.
# 이유(보안 검토 2026-09-25): 샌드박스는 네트워크 네임스페이스를 공유하므로 에이전트의 bash가 127.0.0.1의 Postgres(5432)·
# Redis(6379, 세션)·vLLM(/sleep)·Neo4j·Ollama와 내부망(10.x)에 그대로 닿았다. bwrap의 --unshare-net은 LLM 프록시에 닿을 길이
# 없어 못 쓰고, xt_owner uid 매치가 가장 짧은 답이다. 멱등 - start_local.sh가 매번 부른다. 내부망·인터넷도 막힌다(pip install
# 불가) - 필요해지면 허용 목적지를 이 파일에 추가한다.
set -euo pipefail
LO=${1:-60000}; HI=${2:-60999}; PORT=${3:-8010}
for IPT in iptables ip6tables; do
  $IPT -N MOPAN_SANDBOX 2>/dev/null || $IPT -F MOPAN_SANDBOX
  # OUTPUT → 체인 점프(한 번만)
  $IPT -C OUTPUT -m owner --uid-owner "$LO-$HI" -j MOPAN_SANDBOX 2>/dev/null || $IPT -I OUTPUT 1 -m owner --uid-owner "$LO-$HI" -j MOPAN_SANDBOX
done
# 백엔드(root)가 샌드박스 안 opencode 포트로 먼저 연결한다 - 그 응답 패킷도 uid 소유라 이 줄이 없으면 거절돼
# 코드 탭 전체가 타임아웃(실측 2026-09-25). 샌드박스가 스스로 여는 연결은 SYN이 아래에서 거절되어 여기 못 온다.
iptables  -A MOPAN_SANDBOX -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A MOPAN_SANDBOX -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables  -A MOPAN_SANDBOX -o lo -d 127.0.0.1 -p tcp --dport "$PORT" -j ACCEPT
iptables  -A MOPAN_SANDBOX -j REJECT --reject-with icmp-port-unreachable
ip6tables -A MOPAN_SANDBOX -j REJECT --reject-with icmp6-port-unreachable
echo "sandbox firewall: uid $LO-$HI → only 127.0.0.1:$PORT"
