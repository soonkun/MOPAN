"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { apiFetch, downloadDocument, errorMessage } from "@/lib/api";
import ChunkViewer from "@/components/documents/ChunkViewer";
import { TERMINAL } from "@/components/documents/DocumentTable";
import StructurePanel from "@/components/documents/StructurePanel";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Chunk, DocumentItem, User } from "@/lib/types";

// Next 15 made `params` a Promise. A client component cannot await, so it
// unwraps with React 19's `use()`. A synchronous signature is a build error.
export default function DocumentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  // Named `doc`, not `document`: shadowing the global is harmless here today, but
  // the list page's poll gate reads `document.hidden`, and that pattern copied
  // into a file where `document` is a DocumentItem reads the wrong thing in
  // silence.
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  // 문서 전체의 청크 수(제목의 괄호 숫자). chunks.length가 아니다 - 목록은
  // 이제 한 장(100개)씩 내려온다: 만행짜리 표(실측 19,994청크)를 한 번에
  // 그리다 브라우저가 죽은 실사고.
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  // 유사도 검색: 입력값(query)과 실제 적용된 질의(activeQuery)를 가른다 -
  // 지우기·재검색이 각각 무엇을 되돌리는지가 이 구분에서 나온다.
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const chunksRef = useRef<Chunk[]>([]);
  chunksRef.current = chunks;
  const stateRef = useRef({ total: 0, activeQuery: "", loadingMore: false });
  stateRef.current = { total, activeQuery, loadingMore };
  // Empty is not the same as not-loaded. Without this the list renders
  // "아직 청크가 없습니다." for the length of the fetch - a false statement - and
  // the (0) in the heading then jumps to its real value.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  // Only to decide whether the 구조 인식 controls are on screen at all. null is
  // "not loaded yet", not "not an admin" - the same distinction the list page
  // and the 분류 screen draw. The endpoint answers a non-admin with 403
  // regardless.
  const [user, setUser] = useState<User | null>(null);
  // Read by the poll below without making the interval depend on `doc`, which
  // would tear down and rebuild it on every refetch.
  const docRef = useRef<DocumentItem | null>(null);

  const load = useCallback(async () => {
    // The 원문 구조 pane and its GET /api/documents/{id}/structure are gone. It
    // re-parsed the whole file on every request - about 35 seconds of pdfplumber
    // on the 854-page document in this corpus - to fill a pane nobody read.
    try {
      const [item, page] = await Promise.all([
        apiFetch<DocumentItem>(`/api/documents/${id}`),
        apiFetch<{ total: number; items: Chunk[] }>(`/api/documents/${id}/chunks?limit=100`),
      ]);
      docRef.current = item;
      setDoc(item);
      // 색인 중 폴링이 이 함수를 다시 부르면 1페이지로 돌아간다 - 처리 중엔
      // 목록 자체가 바뀌고 있으므로 이어붙일 기준이 없다.
      setChunks(page.items);
      setTotal(page.total);
      setActiveQuery("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      // finally, not a tail of the try: a 404 on the document otherwise leaves
      // the list saying 불러오는 중... forever, under a banner explaining why.
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
    apiFetch<User>("/api/auth/me")
      .then(setUser)
      .catch(() => undefined);
  }, [load]);

  // 다시 처리 puts the document back to 대기 중, so without this the panel would
  // sit on a disabled button and a stale verdict until somebody reloaded the
  // page by hand. Same gate as the list page's poll - only while something is
  // actually processing, and never while the tab is hidden.
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.hidden) return;
      if (docRef.current === null || TERMINAL.has(docRef.current.status)) return;
      void load();
    }, 3000);
    return () => clearInterval(interval);
  }, [load]);

  // 스크롤이 끝에 닿으면 다음 100개. IntersectionObserver 하나로, 검색 모드
  // (유사도 상위 N)는 이어붙일 "다음"이 없으므로 쉰다.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(async (entries) => {
      const { total, activeQuery, loadingMore } = stateRef.current;
      const loaded = chunksRef.current.length;
      if (!entries[0].isIntersecting || loadingMore || activeQuery || loaded >= total) return;
      setLoadingMore(true);
      try {
        const page = await apiFetch<{ total: number; items: Chunk[] }>(
          `/api/documents/${id}/chunks?limit=100&offset=${loaded}`,
        );
        setChunks((prev) => [...prev, ...page.items]);
        setTotal(page.total);
      } catch {
        // 다음 장 실패는 조용히 - 스크롤을 다시 올리면 재시도된다.
      } finally {
        setLoadingMore(false);
      }
    });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [id, loading]);

  async function search() {
    const trimmed = query.trim();
    if (!trimmed) {
      await resetList();
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const page = await apiFetch<{ total: number; items: Chunk[] }>(
        `/api/documents/${id}/chunks?q=${encodeURIComponent(trimmed)}&limit=50`,
      );
      setChunks(page.items);
      setTotal(page.total);
      setActiveQuery(trimmed);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSearching(false);
    }
  }

  async function resetList() {
    setQuery("");
    setActiveQuery("");
    setSearching(true);
    try {
      const page = await apiFetch<{ total: number; items: Chunk[] }>(
        `/api/documents/${id}/chunks?limit=100`,
      );
      setChunks(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSearching(false);
    }
  }

  async function download() {
    if (doc === null) return;
    setDownloading(true);
    try {
      await downloadDocument(doc.id, doc.filename);
      setError(null);
    } catch (err) {
      // Where 원본 파일을 더 이상 찾을 수 없습니다. lands when the row outlived
      // its file - the backend answers a Korean 404 and this is what shows it.
      setError(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <PageShell>
      <div className="flex flex-wrap items-center gap-2">
        {/* 목록으로 - 워크플로우 편집기의 ‹와 같은 규칙. 모바일에서는 떠 있는
            햄버거 옆에 서도록 pl-12가 자리를 비켜 준다. */}
        <Link
          href="/documents"
          aria-label="문서 목록으로"
          className="icon-btn ml-12 h-9 w-9 shrink-0 md:ml-0"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <h1 className="min-w-0 flex-1 break-all text-headline font-medium">{doc?.filename ?? "문서"}</h1>
        {doc && (
          // The accessible name carries the filename: "다운로드" alone would be
          // the same name this control has on every other document. 모바일은
          // 아이콘만, 데스크톱은 글자까지(헤더 버튼 공통 규칙).
          <button
            type="button"
            onClick={download}
            disabled={downloading}
            aria-label={`${doc.filename} 원본 파일 다운로드`}
            className="btn-tonal btn-compact shrink-0 gap-1.5 px-2.5 sm:px-4"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3v12" />
              <path d="m7 10 5 5 5-5" />
              <path d="M5 21h14" />
            </svg>
            <span className="hidden sm:inline">
              {downloading ? "내려받는 중..." : "원본 다운로드"}
            </span>
          </button>
        )}
      </div>
      {doc?.error_message && <ErrorBanner message={doc.error_message} />}
      <ErrorBanner message={error} />

      {/* Above the chunk list, because it is what explains the chunk list: a
          document judged 참조형 was cut on its own numbering and every chunk
          below carries its governing clause. */}
      {doc !== null && (
        <StructurePanel doc={doc} isAdmin={user?.role === "admin"} onReprocessed={load} />
      )}

      {/* One pane, and one line per row. This screen answers "did the chunking
          come out sensibly", which is a question about boundaries, not about
          content - so a row shows where it starts and stops there, and opens to
          the full text and the per-chunk numbers only when asked. */}
      {/* Guarded on the document, not on the banner. A failed DOWNLOAD fills the
          same banner as a failed load, and guarding on `error` made all 1950
          rows vanish the moment one did - the chunk list has nothing to do with
          whether the stored file is still there. A failed LOAD leaves `doc`
          null, which is what should hide it. */}
      {(loading || doc !== null) && (
        <section className="rounded-md bg-surface-container-low p-4">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-title font-medium text-on-surface">
              청크 목록{!loading && ` (${total.toLocaleString()})`}
            </h2>
            {/* 유사도 검색: 질문이 검색을 탈 때와 같은 임베딩 공간에서 이
                문서 안의 청크를 순위 매긴다 - 정확일치가 아니라, 표현이
                달라도 "이 내용이 어느 청크에 있나"에 답한다. */}
            <form
              className="flex min-w-0 flex-1 items-center gap-2 sm:max-w-md"
              onSubmit={(event) => {
                event.preventDefault();
                void search();
              }}
            >
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="내용으로 청크 찾기 (유사한 순)"
                aria-label="청크 내용 검색"
                className="field h-9 min-w-0 flex-1 text-body"
              />
              <button
                type="submit"
                disabled={searching}
                className="btn-tonal btn-compact shrink-0"
              >
                {searching ? "찾는 중..." : "검색"}
              </button>
              {activeQuery && (
                <button
                  type="button"
                  onClick={() => void resetList()}
                  className="btn-tonal btn-compact shrink-0"
                >
                  지우기
                </button>
              )}
            </form>
          </div>
          {activeQuery && !searching && (
            <p className="mb-3 text-caption text-on-surface-variant">
              &quot;{activeQuery}&quot;와 유사한 순 상위 {chunks.length}개입니다. 번호는 문서
              안의 원래 위치입니다.
            </p>
          )}
          {loading ? (
            <p className="text-body text-on-surface-variant">불러오는 중...</p>
          ) : (
            <>
              <ChunkViewer chunks={chunks} />
              {/* 무한 스크롤의 파수꾼: 화면에 들어오면 다음 100개를 청한다. */}
              {!activeQuery && chunks.length < total && (
                <div ref={sentinelRef} className="py-3 text-center text-caption text-on-surface-variant">
                  {loadingMore ? "다음 청크 불러오는 중..." : `${chunks.length.toLocaleString()} / ${total.toLocaleString()}`}
                </div>
              )}
            </>
          )}
        </section>
      )}
    </PageShell>
  );
}
