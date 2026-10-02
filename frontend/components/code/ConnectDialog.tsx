"use client";

import { useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import type { CodeStatus } from "@/lib/code";
import ErrorBanner from "@/components/ui/ErrorBanner";

type Os = "windows" | "unix";

/** 내 컴퓨터 연결 - 한 줄 명령을 붙여 넣고, 뜨는 창에서 폴더를 고르면 끝.
 *
 * 브라우저는 PC의 프로그램을 실행하지 못하므로 "연결" 버튼 하나로는 안 되고, 그 다음으로 짧은 길이 이것이다:
 * 토큰이 든 한 줄 명령 → PowerShell/터미널에 붙여 넣기. 그 한 줄이 받아 오는 setup 스크립트
 * (backend/app/code/companion/setup.ps1·setup.sh)가 폴더 고르기 창(탐색기/Finder), Node.js 확인, OpenCode 설치, 컴패니언 내려받기, 다음부터
 * 두 번 클릭할 connect 파일 만들기, 실행을 전부 한다. 사용자가 직접 할 일은 Node.js 설치(처음 한 번)뿐이다.
 * 폴더를 이 화면에서 고르지 않는 이유: 브라우저의 폴더 선택(showDirectoryPicker, webkitdirectory)은 이름만 주고
 * C:\\Dev 같은 전체 경로는 주지 않는다. 경로 붙여 넣기 칸은 사용자가 번거로워해서 없앴다.
 * 실사고: 컴패니언 파일을 어디에 둬야 하는지 설명이 없어 다른 폴더에서 실행 → MODULE_NOT_FOUND.
 *
 * 인바운드 포트를 열지 않는다. PC의 스크립트가 이 서버로 나가는 연결 하나를 열어 두고, 그 위로 에이전트
 * 명령이 오간다(app/code/bridge.py). 토큰은 이 계정에 묶이고 새로 발급하면 이전 것은 즉시 죽는다. */
export default function ConnectDialog({ open, onClose, status }: { open: boolean; onClose: () => void; status: CodeStatus | null }) {
  const [os, setOs] = useState<Os>("windows");
  const [command, setCommand] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (typeof navigator !== "undefined" && !/Windows/i.test(navigator.userAgent)) setOs("unix");
  }, []);
  if (!open) return null;
  const origin = typeof window !== "undefined" ? window.location.origin : "https://<MOPAN 주소>";

  // 셸 인용: 작은따옴표 안에서는 작은따옴표만 위험하다(PowerShell은 '' 로, bash는 '\'' 로).
  const q = (s: string) => (os === "windows" ? `'${s.replace(/'/g, "''")}'` : `'${s.replace(/'/g, `'\\''`)}'`);

  async function build() {
    setError(null);
    setBusy(true);
    try {
      const r = await apiFetch<{ token: string }>("/api/code/bridge/token", { method: "POST", body: "{}" });
      setCommand(
        os === "windows"
          ? `$env:MOPAN_SERVER=${q(origin)}; $env:MOPAN_TOKEN=${q(r.token)}; irm ${origin}/api/code/setup.ps1 | iex`
          : `MOPAN_SERVER=${q(origin)} MOPAN_TOKEN=${q(r.token)} bash -c "$(curl -fsSL ${origin}/api/code/setup.sh)"`,
      );
      setCopied(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setError("복사하지 못했습니다. 명령을 직접 선택해 복사해 주세요.");
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-scrim p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="connect-title" className="max-h-full w-full max-w-xl overflow-y-auto rounded-lg bg-surface p-6 shadow-dialog" onClick={(e) => e.stopPropagation()}>
        <h2 id="connect-title" className="text-title font-medium text-on-surface">
          내 컴퓨터 연결
        </h2>
        <p className="mt-2 text-body text-on-surface-variant">
          내 PC의 폴더를 에이전트가 읽고 씁니다. 에이전트는 내 PC에서 돌고, 이 화면은 조종만 합니다. 밖으로 나가는 연결 하나만 쓰고
          포트는 열지 않습니다.
        </p>
        {status?.bridge.connected && (
          <div className="mt-3 rounded-md bg-primary-container p-3 text-caption text-on-primary-container">
            연결됨 · {status.bridge.host} · {status.bridge.dirs.join(", ")}
          </div>
        )}

        <div className="mt-4 flex gap-1 rounded-full bg-surface-container p-1 text-caption" role="tablist" aria-label="운영체제">
          {(["windows", "unix"] as Os[]).map((o) => (
            <button
              key={o}
              type="button"
              role="tab"
              aria-selected={os === o}
              onClick={() => {
                setOs(o);
                setCommand(null);
              }}
              className={`flex-1 rounded-full px-3 py-1.5 transition-colors ${os === o ? "bg-surface text-on-surface shadow-sm" : "text-on-surface-variant"}`}
            >
              {o === "windows" ? "Windows" : "macOS · Linux"}
            </button>
          ))}
        </div>

        <div className="mt-4 text-body text-on-surface">
          <span className="font-medium">1. 명령 한 줄 붙여 넣기</span>
          <p className="mt-1 text-caption text-on-surface-variant">
            {os === "windows" ? "시작 메뉴에서 PowerShell을 열고" : "터미널을 열고"} 붙여 넣은 뒤 Enter. 이 계정 전용 토큰이 들어 있으니 남에게 보내지
            마세요. 다시 만들면 이전 토큰은 무효가 됩니다.
          </p>
          {command ? (
            <div className="mt-2">
              <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-surface-container-high px-3 py-2 text-caption">{command}</pre>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" className="btn-filled btn-compact" onClick={copy}>
                  {copied ? "복사됨" : "명령 복사"}
                </button>
                <button type="button" className="btn-tonal btn-compact" onClick={build} disabled={busy}>
                  다시 만들기
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="btn-filled btn-compact mt-2" onClick={build} disabled={busy}>
              {busy ? "만드는 중…" : "연결 명령 만들기"}
            </button>
          )}
          <span className="mt-3 block font-medium">2. 뜨는 창에서 연결할 폴더 고르기</span>
          <p className="mt-1 text-caption text-on-surface-variant">
            내 PC에 {os === "windows" ? "탐색기 폴더 선택 창" : "Finder 폴더 선택 창"}이 뜹니다. 브라우저는 PC 폴더의 전체 경로를 알 수 없어서 폴더는
            PC에서 직접 고릅니다. 고르면 Node.js 확인, OpenCode 설치, 컴패니언 내려받기, 실행이 이어집니다.
          </p>
        </div>

        <ul className="mt-4 space-y-1 text-caption text-on-surface-variant">
          <li>
            처음 한 번은 Node.js 22 이상이 필요합니다. 없으면 명령이 설치 페이지(nodejs.org)를 열어 주고, 설치 뒤 새 창에서 같은 명령을 다시
            붙여 넣으면 됩니다. OpenCode는 자동으로 설치됩니다.
          </li>
          <li>
            다음부터는 {os === "windows" ? "%LOCALAPPDATA%\\MOPAN\\connect.cmd 를 두 번 클릭" : "~/.mopan/connect.sh 를 실행"}하면 바로
            연결됩니다. 그 창을 닫으면 연결이 끊어집니다. 다른 폴더를 연결하려면 같은 명령을 다시 붙여 넣고 다른 폴더를 고르면 됩니다.
          </li>
          <li>
            직접 하려면:{" "}
            <a href="/api/code/companion" download="mopan-code.mjs" className="text-primary underline">
              mopan-code.mjs
            </a>
            (어느 폴더에 두어도 됩니다)를 받아 <code>node mopan-code.mjs --server {origin} --token &lt;토큰&gt; --dir &lt;폴더&gt;</code>.
          </li>
        </ul>
        <ErrorBanner message={error} />
        <div className="mt-4 flex justify-end">
          <button type="button" className="btn-filled" onClick={onClose}>
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
