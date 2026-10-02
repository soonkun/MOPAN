"""서버 작업 공간 실행기 - 사용자마다 bubblewrap 샌드박스 안의 `opencode serve` 한 프로세스.

왜 사용자마다 프로세스인가: OpenCode 서버 하나가 `?directory=`로 여러 디렉터리를 섬기지만, 그 안의
bash는 프로세스의 uid로 돈다. 사용자 A의 에이전트가 B의 파일을 읽지 못하게 하는 유일하게 단순한
장벽은 "다른 uid + 자기 디렉터리만 마운트"다. 그래서 프로세스 = 사용자 = uid.

샌드박스 레시피(이 호스트에서 실측한 조합, 2026-09-24):
- bwrap은 root로 뜬다. 비특권 user namespace는 AppArmor(apparmor_restrict_unprivileged_userns=1)가
  막고, `--unshare-user`와 `--proc`는 이 컨테이너에서 함께 쓰면 "Can't mount proc"다.
- 마운트는 root가 만들고, 마지막에 setpriv가 실제 uid를 내린다(caps 0, no_new_privs). 그래서 안에서
  /etc/shadow는 EACCES고, 쓸 수 있는 곳은 자기 홈과 자기 작업 공간뿐이다.
- 중간 디렉터리(/home, /opt)는 bwrap이 700으로 만들므로 `--perms 0755 --dir`로 먼저 만든다.
- 바인드 원본 경로는 uid 변경 전에 root가 열므로 어디든 되지만, 데이터 디렉터리 자체는 setpriv 이후
  그 uid가 써야 하니 그 uid 소유여야 한다.
- 네트워크 네임스페이스는 공유한다(LLM 프록시 127.0.0.1:8010에 닿아야 한다). 그래서 다른 사용자의
  opencode 포트도 보이지만, 프로세스마다 다른 basic auth 비밀번호가 그 문을 잠근다.
- `--clearenv`: 백엔드의 환경(DATABASE_URL, OPENAI_API_KEY)이 샌드박스로 새지 않게.

ponytail: 프로세스 상한을 넘으면 가장 오래 쉰 것을 내린다. 큐·대기열은 필요할 때.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import Settings
from app.core.logging import log_event

logger = logging.getLogger("mopan.code")

# Claude Code의 기본과 같은 결: 읽기는 자유, 고치기·실행·외부 접속은 물어본다. 화면의 "항상 허용"이
# 세션 동안 기억한다. 작업 공간 밖은 물어보지도 않고 거절 - 파일 권한이 두 번째 벽이다.
SANDBOX_RULES = """# 이 작업 환경에 대해

- 이 서버 샌드박스는 인터넷에 연결되어 있지 않다. 웹 페이지 읽기(webfetch), 패키지 새로 설치, 외부 API 호출은
  되지 않는다. 시도하지 말고, 필요하면 사용자에게 파일이나 자료를 넣어 달라고 요청한다.
- 작업 폴더 안의 파일 읽기·쓰기·수정과 명령 실행(python, node, 셸 등 이미 설치된 것)은 된다.
- 사용자에게는 한국어로 답한다. 결과 파일은 작업 폴더에 남기고 파일 이름을 알려 준다.
"""

PERMISSION = {
    "*": "allow",
    "edit": "ask",
    "bash": "ask",
    # 서버 샌드박스는 방화벽으로 인터넷이 막혀 있다(scripts/sandbox_firewall.sh). ask면 코워크가 자동 승인해
    # 시도하고 실패만 쌓인다(실사고 2026-09-27: "너는 뭘 할 수 있어" → opencode.ai 읽기 실패 두 번). deny.
    "webfetch": "deny",
    "external_directory": "deny",
    # 같은 도구를 같은 입력으로 되풀이하면 멈춰서 묻는다. 실측(gemma4:26b): hello.py를 142번 읽는 루프 -
    # "*": allow가 OpenCode 기본값(ask)을 덮어 버린 탓이었다. 토큰이 새는 길을 사람이 끊을 수 있어야 한다.
    "doom_loop": "ask",
}
HEALTH_WAIT_SECONDS = 40.0


@dataclass
class Proc:
    user_id: uuid.UUID
    uid: int
    port: int
    password: str
    llm_token: str
    process: asyncio.subprocess.Process
    last_used: float = field(default_factory=time.monotonic)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth("opencode", self.password)

    @property
    def alive(self) -> bool:
        return self.process.returncode is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.procs: dict[uuid.UUID, Proc] = {}
        # LLM 프록시(app/code/llm_proxy.py)가 Bearer 토큰으로 사용자를 찾는 표. 프로세스가 죽으면 지운다.
        self.tokens: dict[str, uuid.UUID] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        # 스트리밍(/event)은 끝이 없으므로 읽기 타임아웃은 없다. 연결만 제한.
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))

    # --- 경로 ----------------------------------------------------------------------
    def user_dir(self, uid: int) -> Path:
        return self.settings.code_data_dir / "users" / str(uid)

    def ws_dir(self, uid: int) -> Path:
        return self.user_dir(uid) / "ws"

    def cowork_dir(self, uid: int) -> Path:
        """코워크 작업 폴더들의 부모. 작업(대화)마다 폴더 하나.

        마지막 사용 후 CODE_COWORK_RETENTION_DAYS가 지나면 통째로 지운다(router.purge_cowork_tasks)."""
        return self.user_dir(uid) / "cowork"

    def available(self) -> tuple[bool, str]:
        if not self.settings.code_opencode_bin.is_file():
            return False, f"OpenCode 실행 파일이 없습니다: {self.settings.code_opencode_bin}"
        if shutil.which("bwrap") is None:
            return False, "bubblewrap(bwrap)이 설치되어 있지 않습니다."
        if shutil.which("setpriv") is None:
            return False, "setpriv가 없습니다."
        return True, ""

    def ensure_dirs(self, uid: int) -> Path:
        """작업 공간 루트를 만들고 그 uid에게 준다. 반환: ws 디렉터리."""
        home, ws = self.user_dir(uid) / "home", self.ws_dir(uid)
        self.user_dir(uid).mkdir(parents=True, exist_ok=True)
        for d in (home, ws, self.cowork_dir(uid)):
            d.mkdir(exist_ok=True)
            os.chown(d, uid, uid)
        return ws

    # --- 수명 ----------------------------------------------------------------------
    async def ensure(
        self, user_id: uuid.UUID, uid: int, models: dict[str, dict], default_model: str | None
    ) -> Proc:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            proc = self.procs.get(user_id)
            if proc is not None and proc.alive:
                proc.last_used = time.monotonic()
                return proc
            if proc is not None:
                self._forget(proc)
            if len(self.procs) >= self.settings.code_max_processes:
                idle = min(self.procs.values(), key=lambda p: p.last_used)
                await self.stop(idle.user_id)
            return await self._spawn(user_id, uid, models, default_model)

    def _config(self, llm_token: str, models: dict[str, dict], default_model: str | None) -> dict:
        return {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "mopan": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "MOPAN",
                    "options": {
                        "baseURL": f"{self.settings.code_backend_url}/api/code/llm/v1",
                        "apiKey": llm_token,
                    },
                    "models": models,
                }
            },
            **({"model": f"mopan/{default_model}"} if default_model else {}),
            "permission": PERMISSION,
            "share": "disabled",
            "autoupdate": False,
        }

    def _bwrap(self, uid: int, home: Path, ws: Path, password: str, port: int) -> list[str]:
        s = self.settings
        ro = lambda src, dst=None: ["--ro-bind", src, dst or src]  # noqa: E731
        setenv = lambda k, v: ["--setenv", k, v]  # noqa: E731
        cmd = ["bwrap", *ro("/usr"), *ro("/lib"), *ro("/lib64"), *ro("/bin"), *ro("/sbin"), *ro("/etc")]
        # 실제 uid로 내려가면 원래 못 읽지만, 마운트 목록에서부터 없애 둔다.
        cmd += [*ro("/dev/null", "/etc/shadow"), *ro("/dev/null", "/etc/gshadow")]
        cmd += ["--perms", "0755", "--dir", "/opt", *ro(str(s.code_opencode_bin.parent), "/opt/opencode")]
        if s.code_node_dir.is_dir():
            cmd += ro(str(s.code_node_dir), "/opt/node")
        cmd += ["--perms", "0755", "--dir", "/home", "--bind", str(home), "/home/code"]
        cmd += ["--bind", str(ws), "/workspace", "--bind", str(self.cowork_dir(uid)), "/cowork"]
        cmd += ["--proc", "/proc", "--dev", "/dev"]
        cmd += ["--perms", "1777", "--tmpfs", "/tmp", "--perms", "1777", "--tmpfs", "/run"]
        cmd += ["--unshare-pid", "--unshare-ipc", "--unshare-uts", "--new-session", "--die-with-parent"]
        cmd += ["--clearenv"]
        cmd += setenv("HOME", "/home/code") + setenv("PATH", "/opt/node/bin:/usr/local/bin:/usr/bin:/bin")
        cmd += setenv("LANG", "C.UTF-8") + setenv("OPENCODE_CONFIG", "/home/code/mopan.json")
        cmd += setenv("OPENCODE_SERVER_PASSWORD", password)
        # models.dev 카탈로그·자동 갱신을 끈다: 프로바이더는 우리 것 하나뿐이고 인터넷에 기대지 않는다.
        cmd += setenv("OPENCODE_DISABLE_MODELS_FETCH", "1") + setenv("OPENCODE_DISABLE_AUTOUPDATE", "1")
        cmd += ["--chdir", "/workspace", "--"]
        cmd += ["/usr/bin/setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "--inh-caps=-all"]
        cmd += ["--no-new-privs", f"/opt/opencode/{s.code_opencode_bin.name}", "serve"]
        cmd += ["--port", str(port), "--hostname", "127.0.0.1"]
        return cmd

    async def _spawn(
        self, user_id: uuid.UUID, uid: int, models: dict[str, dict], default_model: str | None
    ) -> Proc:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        ws = self.ensure_dirs(uid)
        home = self.user_dir(uid) / "home"
        password, llm_token, port = secrets.token_urlsafe(24), secrets.token_urlsafe(32), _free_port()
        # 전역 규칙(OpenCode가 ~/.config/opencode/AGENTS.md를 읽는다): 이 샌드박스의 한계를 모델이 미리 알게.
        rules = home / ".config" / "opencode" / "AGENTS.md"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text(SANDBOX_RULES, encoding="utf-8")
        os.chown(rules, uid, uid)
        os.chown(rules.parent, uid, uid)
        cfg = home / "mopan.json"
        cfg.write_text(
            json.dumps(self._config(llm_token, models, default_model), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.chown(cfg, uid, uid)
        log = open(self.user_dir(uid) / "opencode.log", "ab")  # noqa: ASYNC230 - 자식이 물려받는 fd
        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *self._bwrap(uid, home, ws, password, port),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
        finally:
            log.close()
        proc = Proc(
            user_id=user_id, uid=uid, port=port, password=password, llm_token=llm_token, process=process
        )
        self.tokens[llm_token] = user_id
        deadline = time.monotonic() + HEALTH_WAIT_SECONDS
        while time.monotonic() < deadline:
            if not proc.alive:
                self.tokens.pop(llm_token, None)
                log_path = self.user_dir(uid) / "opencode.log"
                code = process.returncode
                raise RuntimeError(f"opencode 프로세스가 바로 종료했습니다(코드 {code}). {log_path} 확인")
            try:
                r = await self.http.get(f"{proc.base_url}/global/health", auth=proc.auth, timeout=3.0)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.4)
        else:
            await self._kill(proc)
            self.tokens.pop(llm_token, None)
            raise RuntimeError("opencode 서버가 시간 안에 뜨지 않았습니다.")
        self.procs[user_id] = proc
        log_event(
            logger,
            "code_runtime_spawned",
            user_id=str(user_id),
            uid=uid,
            port=port,
            ms=int((time.perf_counter() - started) * 1000),
        )
        return proc

    def touch(self, user_id: uuid.UUID) -> None:
        proc = self.procs.get(user_id)
        if proc:
            proc.last_used = time.monotonic()

    def _forget(self, proc: Proc) -> None:
        self.procs.pop(proc.user_id, None)
        self.tokens.pop(proc.llm_token, None)

    async def _kill(self, proc: Proc) -> None:
        if not proc.alive:
            return
        proc.process.terminate()
        try:
            await asyncio.wait_for(proc.process.wait(), 5.0)
        except TimeoutError:
            # bwrap을 SIGKILL하면 pid 네임스페이스의 init이 죽어 안의 것도 전부 죽는다.
            proc.process.kill()
            await proc.process.wait()

    async def stop(self, user_id: uuid.UUID) -> None:
        proc = self.procs.get(user_id)
        if proc is None:
            return
        self._forget(proc)
        await self._kill(proc)
        log_event(logger, "code_runtime_stopped", user_id=str(user_id), uid=proc.uid)

    async def reap_forever(self) -> None:
        """유휴 프로세스 회수. 60초마다, code_idle_minutes 넘게 안 쓴 것을 내린다."""
        while True:
            await asyncio.sleep(60)
            limit = self.settings.code_idle_minutes * 60
            for proc in list(self.procs.values()):
                if not proc.alive or time.monotonic() - proc.last_used > limit:
                    await self.stop(proc.user_id)

    async def aclose(self) -> None:
        for user_id in list(self.procs):
            await self.stop(user_id)
        await self.http.aclose()
