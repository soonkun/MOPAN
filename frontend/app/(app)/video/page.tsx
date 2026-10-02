"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";

type Failed = { id: string; title: string; error: string };
type Job = {
  id: string;
  url: string;
  status: "queued" | "running" | "done" | "failed" | "interrupted";
  title: string;
  total: number;
  done: number;
  failed: Failed[];
  note?: string;
  summary_chars?: number;
  full?: boolean;
  thumbnail?: boolean;
  error?: string;
  finished?: string;
};
type Jobs = { gpu: string; idle_minutes: number; jobs: Job[] };

function stateText(job: Job): string {
  switch (job.status) {
    case "queued":
      return "앞 작업이 끝나기를 기다리는 중";
    case "running":
      return (job.total ? `${job.total}편 중 ${job.done}편 정리함` : "영상 목록을 가져오는 중") + (job.note ? ` · ${job.note}` : "");
    case "done":
      return `${job.done}편 정리 완료${job.summary_chars ? ` · 요약 ${job.summary_chars}자 내외` : ""}${job.full ? " · 전체 내용 포함" : ""}${job.thumbnail === false ? " · 썸네일 없음" : ""} · ${job.finished ?? ""}`;
    case "failed":
      return `실패: ${job.error ?? ""}`;
    default:
      return "서버가 다시 시작되어 중단됐습니다. 같은 주소로 다시 시작하면 끝난 영상은 건너뜁니다.";
  }
}

/** 영상 내용 추출 - 유튜브 영상·재생목록·채널(또는 주소 묶음)을 영상별 제목·썸네일·링크·요약·분야·검색어·구간으로
 * 정리해 Word/PDF/Markdown으로 받는다. 일은 별도 서비스(soonkun/youtube_transcipt)가 하고 백엔드 app/video가 넘긴다.
 * 작업은 넣은 사람에게만 보인다. 진행은 짧은 GET을 되풀이해 읽는다 - 수십 분 걸리는 작업이라 스트림을 열어 두지 않는다. */
export default function VideoExtractPage() {
  const [data, setData] = useState<Jobs | null>(null);
  const [url, setUrl] = useState("");
  const [force, setForce] = useState(false);
  const [chars, setChars] = useState(500);
  const [full, setFull] = useState(false);
  const [thumbnail, setThumbnail] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<Jobs>("/api/video/jobs"));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load]);

  async function submit(target: string, again: boolean, length: number, whole: boolean, thumb: boolean) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/video/jobs", { method: "POST", body: JSON.stringify({ url: target, force: again, summary_chars: length, full: whole, thumbnail: thumb }) });
      await load();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function showPreview(id: string) {
    if (preview[id] !== undefined) return;
    const response = await fetch(`/api/video/jobs/${id}/report.md`, { credentials: "include" });
    const text = response.ok ? await response.text() : "내용을 불러오지 못했습니다.";
    setPreview((prev) => ({ ...prev, [id]: text }));
  }

  return (
    <PageShell>
      <PageHeader title="영상 내용 추출" />
      <p className="max-w-measure break-keep text-body text-on-surface-variant">
        유튜브 영상 주소를 넣으면 그 영상 하나를, 재생목록이나 채널 주소를 넣으면 그 안의 모든 영상을 제목·썸네일·링크·요약·분야·검색어·구간
        링크로 정리해 Word 문서로 만듭니다. 주소 여러 개를 쉼표나 줄바꿈으로 나눠 넣으면 한 문서로 묶습니다. 영상이 많으면
        수십 분 걸리며, 화면을 닫아도 작업은 계속됩니다.
      </p>
      <form
        className="max-w-measure space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await submit(url, force, chars, full, thumbnail)) setUrl("");
        }}
      >
        <label htmlFor="video-url" className="block text-label font-medium text-on-surface">
          유튜브 주소
        </label>
        <textarea
          id="video-url"
          required
          rows={3}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="예) https://www.youtube.com/watch?v=…, https://www.youtube.com/playlist?list=…"
          className="field h-auto w-full resize-y py-2"
        />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex items-center gap-2 text-body text-on-surface">
            요약
            <input
              type="number"
              required
              min={100}
              max={1500}
              step={50}
              value={chars}
              onChange={(e) => setChars(Number(e.target.value))}
              aria-label="요약 길이(자)"
              className="field w-24"
            />
            자 내외
          </label>
          <button type="submit" disabled={busy || !url.trim()} className="btn-filled">
            정리 시작
          </button>
          <label className="flex items-center gap-2 text-body text-on-surface">
            <input type="checkbox" checked={thumbnail} onChange={(e) => setThumbnail(e.target.checked)} />
            썸네일 포함
          </label>
          <label className="flex items-center gap-2 text-body text-on-surface">
            <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
            전체 내용 추출 (요약 뒤에 영상 전체 내용을 시간대별로 붙임)
          </label>
          <label className="flex items-center gap-2 text-body text-on-surface">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            전에 정리한 영상도 처음부터 다시 만들기
          </label>
        </div>
      </form>
      <ErrorBanner message={error} />
      {data && (
        <p className="text-caption text-on-surface-variant">
          GPU 요약 모델: {data.gpu} · 작업이 끝나고 {data.idle_minutes}분 동안 쓰지 않으면 휴지 상태로 돌아갑니다.
        </p>
      )}
      {data === null ? (
        !error && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : data.jobs.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">아직 작업이 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {data.jobs.map((job) => {
            const urls = job.url.split(", ");
            const failed = job.failed ?? [];
            const bad = job.status === "failed" || job.status === "interrupted";
            return (
              <li key={job.id} className="rounded-md bg-surface-container-low px-4 py-3">
                <p className="break-words text-body font-medium text-on-surface">{job.title || "(제목을 가져오는 중)"}</p>
                <p className="break-all text-caption text-on-surface-variant">
                  {urls[0]}
                  {urls.length > 1 && ` 외 ${urls.length - 1}개 주소`}
                </p>
                {job.status === "running" && job.total > 0 && (
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={job.total}
                    aria-valuenow={job.done + failed.length}
                    className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-container-highest"
                  >
                    <div
                      className="h-full rounded-full bg-primary transition-[width] duration-300"
                      style={{ width: `${((job.done + failed.length) / job.total) * 100}%` }}
                    />
                  </div>
                )}
                <p className={`mt-2 break-keep text-body ${bad ? "text-error" : "text-on-surface"}`}>{stateText(job)}</p>
                {job.status === "done" && (
                  <>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <a href={`/api/video/jobs/${job.id}/report.docx`} className="btn-tonal btn-compact">
                        Word 문서 받기
                      </a>
                      <a href={`/api/video/jobs/${job.id}/report.pdf`} className="btn-tonal btn-compact">
                        PDF 받기
                      </a>
                      <a href={`/api/video/jobs/${job.id}/report.md`} className="btn-tonal btn-compact">
                        Markdown 받기
                      </a>
                    </div>
                    <details className="mt-2" onToggle={(e) => e.currentTarget.open && void showPreview(job.id)}>
                      <summary className="cursor-pointer text-caption text-on-surface-variant">내용 보기</summary>
                      <pre className="mt-2 max-h-[30rem] overflow-auto whitespace-pre-wrap break-words rounded-sm bg-surface-container p-3 text-caption text-on-surface">
                        {preview[job.id] ?? "불러오는 중..."}
                      </pre>
                    </details>
                  </>
                )}
                {failed.length > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-caption text-error">정리하지 못한 영상 {failed.length}편</summary>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-caption text-on-surface-variant">
                      {failed.map((f) => (
                        <li key={f.id} className="break-words">
                          {f.title || f.id} - {f.error}
                        </li>
                      ))}
                    </ul>
                    {job.status === "done" && (
                      // 끝난 영상은 그 서비스에 저장돼 있어, 같은 주소·같은 길이로 다시 넣으면 실패한 것만 다시 돈다.
                      // 길이 설정이 생기기 전 작업(summary_chars 없음)은 500자였다.
                      <button type="button" disabled={busy} onClick={() => void submit(job.url, false, job.summary_chars ?? 500, !!job.full, job.thumbnail ?? true)} className="btn-text btn-compact mt-1">
                        실패한 영상만 다시 시도
                      </button>
                    )}
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </PageShell>
  );
}
