"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ResearchProject, User } from "@/lib/types";

/** 딥 리서치 방 목록. 방은 지침·예산·범위·모델을 묶는 단위이고(원본 새싹이 CR-62),
 * 만들기는 관리자, 실행은 모든 사용자다. */
export default function ResearchPage() {
  const [projects, setProjects] = useState<ResearchProject[] | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<ResearchProject[]>("/api/research/projects").then(setProjects).catch((err) => setLoadError(errorMessage(err)));
    apiFetch<User>("/api/auth/me").then(setUser).catch(() => setUser(null));
  }, []);
  const isAdmin = user?.role === "admin";

  return (
    <PageShell>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex-1 text-center text-headline font-medium md:flex-none md:text-left">딥 리서치</h1>
        {isAdmin && (
          <Link href="/research/new" className="btn-filled">
            새 방
          </Link>
        )}
      </div>
      <p className="notice">
        질문 하나를 여러 갈래의 검색 질의로 나눠 등록된 문서를 읽고, 빈 곳을 한 번 더 찾은 뒤, 실제로 인용한 근거만
        출처로 붙인 보고서를 만듭니다. 몇 분이 걸리며 화면을 떠나도 계속 진행됩니다.
      </p>
      <ErrorBanner message={loadError} />
      {projects === null ? (
        !loadError && <p className="text-body text-on-surface-variant">불러오는 중...</p>
      ) : projects.length === 0 ? (
        <div className="rounded-md bg-surface-container-low p-6">
          <p className="text-body text-on-surface">아직 방이 없습니다.</p>
          <p className="mt-2 text-caption text-on-surface-variant">
            예: “상표 출원 전 검토” 방에는 유사상품·류 구분 관점의 지침을, “규정 개정 영향 분석” 방에는 조항 인용을
            요구하는 지침을 둡니다. {isAdmin ? "오른쪽 위 ‘새 방’으로 만듭니다." : "관리자가 방을 만들면 여기에 보입니다."}
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => (
            <li key={p.id}>
              <Link
                href={`/research/${p.id}`}
                className="block h-full rounded-md bg-surface-container-low p-4 transition-colors duration-150 hover:bg-surface-container"
              >
                <h2 className="text-title font-medium text-on-surface">{p.name}</h2>
                {p.description && <p className="mt-1 text-body text-on-surface-variant">{p.description}</p>}
                <p className="mt-3 text-caption text-on-surface-variant">
                  실행 {p.run_count}회
                  {p.last_run_at && ` · 마지막 ${new Date(p.last_run_at).toLocaleDateString()}`}
                  {p.model && ` · ${p.model}`}
                  {p.collection_ids.length > 0 ? ` · 분류 ${p.collection_ids.length}개` : " · 전체 분류"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}
