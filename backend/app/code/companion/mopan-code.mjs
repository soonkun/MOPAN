#!/usr/bin/env node
// MOPAN 코드 - 내 컴퓨터 연결 컴패니언.
//
// 내 PC의 폴더를 MOPAN 코드 탭의 에이전트가 읽고 쓰게 한다. 에이전트(opencode)는 이 PC에서 돌고,
// MOPAN은 화면과 모델만 맡는다. 인바운드 포트는 열지 않는다 - 이 스크립트가 MOPAN으로 나가는
// 연결(SSE) 하나를 열어 두고, 그 위로 내려오는 요청을 로컬 opencode에 대신 보낸 뒤 결과를 POST로 올린다.
//
// 보통은 코드 탭 > 내 컴퓨터 연결이 만들어 주는 한 줄 명령(setup.ps1 / setup.sh)이 이 파일을 %LOCALAPPDATA%\MOPAN(~/.mopan)에
// 받아 실행한다. 직접 할 때 - 준비: Node.js 22 이상, OpenCode(`npm i -g opencode-ai`). 이 파일은 어느 폴더에 두어도 된다.
//   node mopan-code.mjs --server https://<MOPAN 주소> --token <코드 탭에서 발급한 토큰> --dir <폴더> [--dir <폴더>...]
// 선택: --opencode <opencode 실행 파일 경로>  (PATH에 없을 때)
//
// 의존 패키지 없음.
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import fs from "node:fs";

const args = parseArgs(process.argv.slice(2));
const SERVER = (args.server || "").replace(/\/+$/, "");
const TOKEN = args.token || "";
const DIRS = (args.dir || []).map((d) => path.resolve(d));
const OPENCODE = args.opencode || "opencode";
if (!SERVER || !TOKEN || DIRS.length === 0) {
  console.error("사용법: node mopan-code.mjs --server https://<MOPAN> --token <토큰> --dir <폴더> [--dir <폴더>...] [--opencode <경로>]");
  process.exit(2);
}
for (const d of DIRS) {
  if (!fs.existsSync(d) || !fs.statSync(d).isDirectory()) {
    console.error(`폴더가 없습니다: ${d}`);
    process.exit(2);
  }
}
const major = Number(process.versions.node.split(".")[0]);
if (major < 22) {
  console.error(`Node.js 22 이상이 필요합니다(현재 ${process.versions.node}).`);
  process.exit(2);
}

const COMPANION_VERSION = "2026-09-26";
const AUTH = { Authorization: `Bearer ${TOKEN}` };
const PASSWORD = crypto.randomBytes(18).toString("base64url");
const BASIC = "Basic " + Buffer.from(`opencode:${PASSWORD}`).toString("base64");

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

async function main() {
  // 1. 모델 목록은 서버가 준다 - 하드코딩하지 않는다.
  const modelsRes = await fetch(`${SERVER}/api/code/llm/v1/models`, { headers: AUTH });
  if (!modelsRes.ok) throw new Error(`MOPAN에 연결하지 못했습니다(${modelsRes.status}). 주소와 토큰을 확인해 주세요.`);
  const models = {};
  // 창 크기는 서버가 준다(output < context가 지켜져야 자동 압축 루프에 빠지지 않는다).
  for (const m of (await modelsRes.json()).data) models[m.id] = { name: m.id, limit: { context: m.context ?? 128000, output: m.output ?? 32768 }, tool_call: true };
  const config = {
    $schema: "https://opencode.ai/config.json",
    provider: {
      mopan: {
        npm: "@ai-sdk/openai-compatible",
        name: "MOPAN",
        options: { baseURL: `${SERVER}/api/code/llm/v1`, apiKey: TOKEN },
        models,
      },
    },
    model: `mopan/${Object.keys(models)[0]}`,
    permission: { "*": "allow", edit: "ask", bash: "ask", webfetch: "ask", external_directory: "deny", doom_loop: "ask" },
    share: "disabled",
    autoupdate: false,
  };

  // 2. 로컬 opencode 서버.
  const port = await freePort();
  const child = spawn(OPENCODE, ["serve", "--port", String(port), "--hostname", "127.0.0.1"], {
    env: {
      ...process.env,
      OPENCODE_CONFIG_CONTENT: JSON.stringify(config),
      OPENCODE_SERVER_PASSWORD: PASSWORD,
      OPENCODE_DISABLE_MODELS_FETCH: "1",
      OPENCODE_DISABLE_AUTOUPDATE: "1",
    },
    stdio: ["ignore", "inherit", "inherit"],
    shell: process.platform === "win32",
  });
  child.on("exit", (code) => {
    console.error(`opencode가 종료되었습니다(코드 ${code}).`);
    process.exit(1);
  });
  process.on("SIGINT", () => {
    child.kill();
    process.exit(0);
  });
  const local = `http://127.0.0.1:${port}`;
  await waitHealthy(local);
  let version = "";
  try {
    version = (await (await fetch(`${local}/global/health`, { headers: { Authorization: BASIC } })).json()).version || "";
  } catch {}
  console.log(`opencode ${version} 준비됨 (127.0.0.1:${port}). 폴더: ${DIRS.join(", ")}`);

  // 3. 연결 유지 루프.
  for (;;) {
    try {
      await bridge(local, version);
      console.log("연결이 끊어졌습니다. 3초 후 다시 연결합니다.");
    } catch (e) {
      console.error(`연결 실패: ${e.message}. 3초 후 다시 시도합니다.`);
    }
    await sleep(3000);
  }
}

async function bridge(local, version) {
  const q = new URLSearchParams({ dirs: DIRS.join("|"), host: os.hostname(), version: `${version}+c${COMPANION_VERSION}` });
  // POST로 연다: Cloudflare 터널은 GET 스트림 응답을 연결이 끝날 때까지 붙잡아 두지만 POST 스트림은 즉시 흘린다(실측 2026-09-26).
  const res = await fetch(`${SERVER}/api/code/bridge/stream?${q}`, {
    method: "POST",
    headers: { ...AUTH, Accept: "text/event-stream", "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) throw new Error(`bridge ${res.status}: ${(await res.text()).slice(0, 200)}`);
  console.log(`MOPAN에 연결됨: ${SERVER}`);
  const inflight = new Map(); // request id -> AbortController
  const decoder = new TextDecoder();
  let buf = "";
  for await (const chunk of res.body) {
    buf += decoder.decode(chunk, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (msg.type === "hello") continue;
      if (msg.cancel) {
        inflight.get(msg.id)?.abort();
        continue;
      }
      handle(local, msg, inflight).catch((e) => console.error(`요청 처리 실패 ${msg.id}: ${e.message}`));
    }
  }
}

async function handle(local, req, inflight) {
  const ctrl = new AbortController();
  inflight.set(req.id, ctrl);
  const qs = new URLSearchParams(req.query || {}).toString();
  const headers = { ...(req.headers || {}), Authorization: BASIC };
  delete headers["content-length"];
  let res;
  try {
    res = await fetch(`${local}${req.path}${qs ? "?" + qs : ""}`, {
      method: req.method,
      headers,
      body: req.body ? Buffer.from(req.body, "base64") : undefined,
      signal: ctrl.signal,
    });
  } catch (e) {
    inflight.delete(req.id);
    await reply(req.id, { status: 502, headers: { "content-type": "text/plain" }, chunk: Buffer.from(String(e.message)).toString("base64"), end: true });
    return;
  }
  const contentType = res.headers.get("content-type") || "";
  const respHeaders = { "content-type": contentType };
  try {
    if (!contentType.includes("text/event-stream")) {
      const body = Buffer.from(await res.arrayBuffer());
      await reply(req.id, { status: res.status, headers: respHeaders, chunk: body.toString("base64"), end: true });
      return;
    }
    // 끝없는 스트림: 첫 프레임에 상태, 이어서 50ms 단위로 모아 올린다. 순서가 중요하니 POST는 직렬.
    if (!(await reply(req.id, { status: res.status, headers: respHeaders, end: false }))) {
      ctrl.abort();
      return;
    }
    let pending = [];
    let timer = null;
    let chain = Promise.resolve();
    const flush = () => {
      timer = null;
      if (pending.length === 0) return;
      const data = Buffer.concat(pending).toString("base64");
      pending = [];
      chain = chain.then(async () => {
        if (!(await reply(req.id, { chunk: data, end: false }))) ctrl.abort();
      });
    };
    for await (const chunk of res.body) {
      pending.push(Buffer.from(chunk));
      if (!timer) timer = setTimeout(flush, 50);
    }
    if (timer) clearTimeout(timer);
    flush();
    await chain;
    await reply(req.id, { end: true });
  } catch (e) {
    if (!ctrl.signal.aborted) await reply(req.id, { end: true }).catch(() => {});
  } finally {
    inflight.delete(req.id);
  }
}

/** 응답 프레임을 올린다. 서버가 그 요청을 더 기다리지 않으면 false. */
async function reply(id, frame) {
  const r = await fetch(`${SERVER}/api/code/bridge/reply/${id}`, {
    method: "POST",
    headers: { ...AUTH, "content-type": "application/json" },
    body: JSON.stringify(frame),
  });
  if (!r.ok) return false;
  return (await r.json()).accepted !== false;
}

async function waitHealthy(local) {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${local}/global/health`, { headers: { Authorization: BASIC } });
      if (r.ok) return;
    } catch {}
    await sleep(400);
  }
  throw new Error("로컬 opencode 서버가 뜨지 않았습니다. `opencode --version`으로 설치를 확인해 주세요.");
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
    s.on("error", reject);
  });
}

function parseArgs(argv) {
  const out = { dir: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const val = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "";
    if (key === "dir") out.dir.push(val);
    else out[key] = val;
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
