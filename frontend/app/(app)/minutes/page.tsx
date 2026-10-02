"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";

// must = 지침 위반, should = 지침의 권장을 벗어남, check = 사람이 확인할 것(녹취록에 없는 숫자 등)
type Issue = { level: "must" | "should" | "check"; line: string; message: string };
type Transcription = { id: string; status: "queued" | "running" | "done" | "failed"; text?: string; error?: string };
type Draft = { id: string; status: "running" | "done" | "failed"; note?: string; text?: string; issues?: Issue[]; revisions?: number; error?: string };

// 올리는 길(Next 프록시 → 터널)이 한 요청에 100MB까지 받는다. next.config.js middlewareClientMaxBodySize와 같이 움직인다.
const MAX_UPLOAD_MB = 95;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function save(blob: Blob, name: string) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

/** 음성 전사·회의록 - 회의 녹음을 받아 적고, 작성 지침대로 회의 결과보고서 초안을 써서 한글(hwpx)로 받는다. 원본: 새싹이.
 * 받아 적기는 영상 내용 추출과 같은 서비스(Whisper), 초안은 답변 모델이 쓰고 코드가 지침으로 재어 고쳐 쓰게 한다(backend app/minutes).
 * 둘 다 오래 걸려 짧은 GET을 되풀이해 읽는다. 한글 문서는 화면의 글 그대로 만든다 - 고친 것이 그대로 나간다. */
export default function MinutesPage() {
  const [file, setFile] = useState<File | null>(null);
  const [transcript, setTranscript] = useState("");
  const [pages, setPages] = useState<1 | 2>(1);
  const [date, setDate] = useState(today);
  const [department, setDepartment] = useState("");
  const [report, setReport] = useState("");
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [revisions, setRevisions] = useState(0);
  const [working, setWorking] = useState<string | null>(null); // 지금 하는 일(진행 문구). null이면 쉬는 중
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  async function run(task: () => Promise<void>) {
    setError(null);
    try {
      await task();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setWorking(null);
    }
  }

  const transcribe = () =>
    run(async () => {
      if (!file) return;
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024)
        throw new Error(`파일이 ${MAX_UPLOAD_MB}MB를 넘습니다. m4a·mp3처럼 압축된 형식으로 바꾸거나 나눠서 올려 주세요.`);
      setWorking("음성 파일을 올리는 중");
      let job = await apiFetch<Transcription>(`/api/minutes/transcriptions?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      while (alive.current && (job.status === "queued" || job.status === "running")) {
        setWorking(job.status === "queued" ? "앞 작업이 끝나기를 기다리는 중" : "받아 적는 중 (1시간 녹음에 2~3분)");
        await sleep(3000);
        job = await apiFetch<Transcription>(`/api/minutes/transcriptions/${job.id}`);
      }
      if (job.status === "failed") throw new Error(job.error ?? "받아 적지 못했습니다.");
      setTranscript(job.text ?? "");
    });

  const write = () =>
    run(async () => {
      setWorking("초안을 쓰는 중");
      let job = await apiFetch<Draft>("/api/minutes/drafts", { method: "POST", body: JSON.stringify({ transcript, pages }) });
      while (alive.current && job.status === "running") {
        setWorking(job.note || "초안을 쓰는 중");
        await sleep(2000);
        job = await apiFetch<Draft>(`/api/minutes/drafts/${job.id}`);
      }
      if (job.status === "failed") throw new Error(job.error ?? "초안을 쓰지 못했습니다.");
      setReport(job.text ?? "");
      setIssues(job.issues ?? []);
      setRevisions(job.revisions ?? 0);
    });

  const body = () => JSON.stringify({ text: report, transcript, pages, date: date ? `${date.replaceAll("-", ".")}.` : "", department });

  const check = () =>
    run(async () => {
      setWorking("지침으로 점검하는 중");
      setIssues((await apiFetch<{ issues: Issue[] }>("/api/minutes/check", { method: "POST", body: body() })).issues);
    });

  const download = () =>
    run(async () => {
      setWorking("한글 문서를 만드는 중");
      const response = await fetch("/api/minutes/hwpx", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: body() });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "한글 문서를 만들지 못했습니다.");
      save(await response.blob(), `${report.trim().split("\n")[0].slice(0, 60) || "회의 결과보고"}.hwpx`);
    });

  const busy = working !== null;
  const must = issues?.filter((i) => i.level === "must") ?? [];
  const notes = issues?.filter((i) => i.level !== "must") ?? [];

  return (
    <PageShell>
      <PageHeader title="음성 전사·회의록" />
      <p className="max-w-measure break-keep text-body text-on-surface-variant">
        회의 녹음 파일을 올리면 받아 적고, 받아 적은 글로 회의 결과보고서(개요·주요내용·향후계획) 초안을 써서 한글(hwpx) 문서로 냅니다. 초안은 작성
        지침으로 점검해 고쳐 쓴 결과이며, 남은 점검 결과가 아래에 표시됩니다. 받아 적은 글을 이미 갖고 있으면 2번 칸에 바로 붙여 넣으세요.
      </p>
      <ErrorBanner message={error} />
      {working && (
        <p role="status" className="text-body font-medium text-primary">
          {working}…
        </p>
      )}

      <section className="max-w-measure space-y-3">
        <h2 className="text-title font-medium text-on-surface">1. 음성 받아 적기</h2>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            aria-label="음성 파일"
            accept="audio/*,video/mp4,video/webm,.m4a,.mp3,.wav,.ogg,.flac,.aac,.wma"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-body text-on-surface"
          />
          <button type="button" disabled={busy || !file} onClick={() => void transcribe()} className="btn-filled">
            받아 적기
          </button>
        </div>
        <p className="text-caption text-on-surface-variant">
          m4a·mp3·wav 등, {MAX_UPLOAD_MB}MB까지. 음성 파일은 받아 적은 뒤 서버에서 지웁니다.
        </p>
      </section>

      <section className="max-w-measure space-y-3">
        <h2 className="text-title font-medium text-on-surface">2. 받아 적은 글</h2>
        <textarea
          aria-label="받아 적은 글"
          rows={10}
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          placeholder="받아 적은 글이 여기에 들어옵니다. 잘못 들린 이름·숫자는 여기서 고친 뒤 초안을 쓰면 보고서가 정확해집니다."
          className="field h-auto w-full resize-y py-2"
        />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex items-center gap-2 text-body text-on-surface">
            분량
            <select value={pages} onChange={(e) => setPages(Number(e.target.value) as 1 | 2)} className="field w-24">
              <option value={1}>1쪽</option>
              <option value={2}>2쪽</option>
            </select>
          </label>
          <button type="button" disabled={busy || transcript.trim().length < 20} onClick={() => void write()} className="btn-filled">
            보고서 초안 쓰기
          </button>
          <button
            type="button"
            disabled={!transcript.trim()}
            onClick={() => save(new Blob([transcript], { type: "text/plain;charset=utf-8" }), "받아 적은 글.txt")}
            className="btn-tonal"
          >
            글(txt) 받기
          </button>
        </div>
      </section>

      <section className="max-w-measure space-y-3">
        <h2 className="text-title font-medium text-on-surface">3. 회의 결과보고서</h2>
        <textarea
          aria-label="회의 결과보고서"
          rows={18}
          value={report}
          onChange={(e) => setReport(e.target.value)}
          placeholder={"초안이 여기에 들어옵니다. 고친 글 그대로 한글 문서가 됩니다.\n\n회의 제목\n[개요]\n○ …\n[주요내용]\n○ …\n - …\n  * …\n[향후계획]\n○ … (10.15.)"}
          className="field h-auto w-full resize-y py-2 font-mono"
        />
        {issues !== null && (
          <div className="rounded-md bg-surface-container-low px-4 py-3">
            <p className="text-body font-medium text-on-surface">
              지침 점검: {issues.length === 0 ? "걸린 곳이 없습니다." : `고칠 곳 ${must.length}건 · 확인 권장 ${notes.length}건`}
              {revisions > 0 && <span className="font-normal text-on-surface-variant"> (초안을 {revisions}번 고쳐 쓴 결과)</span>}
            </p>
            {issues.length > 0 && (
              <ul className="mt-2 space-y-1.5 text-caption">
                {[...must, ...notes].map((issue, index) => (
                  <li key={index} className="break-words">
                    <span className={issue.level === "must" ? "font-medium text-error" : "text-on-surface-variant"}>
                      {issue.level === "must" ? "고칠 곳" : "확인"}
                    </span>{" "}
                    <span className="text-on-surface">{issue.message}</span>
                    {issue.line && <span className="block pl-4 text-on-surface-variant">{issue.line}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex items-center gap-2 text-body text-on-surface">
            작성일
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="field w-40" />
          </label>
          <label className="flex items-center gap-2 text-body text-on-surface">
            부서
            <input value={department} maxLength={40} onChange={(e) => setDepartment(e.target.value)} placeholder="예) 기술융합과" className="field w-40" />
          </label>
          <button type="button" disabled={busy || !report.trim()} onClick={() => void download()} className="btn-filled">
            한글(hwpx) 받기
          </button>
          <button type="button" disabled={busy || !report.trim()} onClick={() => void check()} className="btn-tonal">
            지침 다시 점검
          </button>
        </div>
      </section>
    </PageShell>
  );
}
