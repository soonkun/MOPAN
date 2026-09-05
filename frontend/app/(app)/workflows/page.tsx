"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Workflow } from "@/lib/types";

/** 워크플로우 목록. 편집은 /workflows/[id]의 전체 화면 편집기가 맡는다 -
 * 목록과 캔버스를 한 화면에 우겨넣던 첫 판이 "캔버스는 쥐똥만한데 도구가 방을
 * 채운" 화면을 만들었다. 이 화면의 일은 고르는 것 하나다. */

/** 전체, not 없음. An empty list is unrestricted, and this is the summary an
 * admin reads down the list without opening anything. */
function summary(workflow: Workflow): string {
  const collections =
    workflow.collections.length === 0 ? "전체 분류" : `분류 ${workflow.collections.length}개`;
  const tools = workflow.tools.length === 0 ? "전체 도구" : `도구 ${workflow.tools.length}개`;
  const nodes = workflow.graph?.nodes?.length ?? 0;
  return `${collections} · ${tools} · 노드 ${nodes}개`;
}

export default function WorkflowsPage() {
  // null is "not loaded yet", not an empty list - the distinction every admin
  // screen here draws so the empty state never flashes.
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 삭제는 여기서 한다(소유자 지정): 편집기는 만드는 곳이고, 목록이 지우는
  // 곳이다. ConfirmDialog가 실패를 자기 안에 그리므로 별도 오류 상태가 없다.
  const [deleteTarget, setDeleteTarget] = useState<Workflow | null>(null);

  useEffect(() => {
    apiFetch<Workflow[]>("/api/workflows")
      .then(setWorkflows)
      .catch((err) => setLoadError(errorMessage(err)));
  }, []);

  return (
    <PageShell>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex-1 text-center text-headline font-medium md:flex-none md:text-left">워크플로우</h1>
        <Link href="/workflows/new" className="btn-filled">
          새 워크플로우
        </Link>
      </div>
      <ErrorBanner message={loadError} />

      {workflows === null ? (
        !loadError && <p className="text-body text-on-surface-variant">불러오는 중...</p>
      ) : workflows.length === 0 ? (
        <div className="rounded-md bg-surface-container-low p-6">
          <p className="text-body text-on-surface">
            저장된 워크플로우가 없습니다. 하나도 만들지 않으면 채팅은 지금까지와 똑같이 동작합니다.
          </p>
          <p className="mt-2 text-caption text-on-surface-variant">
            워크플로우는 저장된 절차입니다. 그래프의 간선이 실행 순서를 정하고, 놓은 분류와 도구는
            권한 경계입니다. 아무것도 놓지 않으면 전체를 허용한다는 뜻입니다.
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {workflows.map((workflow) => (
            <li key={workflow.id} className="relative">
              <Link
                href={`/workflows/${workflow.id}`}
                className="block h-full rounded-md bg-surface-container-low p-4 transition-colors duration-150 hover:bg-surface-container"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 truncate text-title font-medium text-on-surface">
                    {workflow.name}
                  </span>
                  {!workflow.enabled && (
                    <span className="shrink-0 rounded-full bg-surface-container-high px-2 py-0.5 text-caption text-on-surface-variant">
                      중지됨
                    </span>
                  )}
                </div>
                {workflow.description && (
                  <p className="mt-1 line-clamp-2 text-body text-on-surface-variant">
                    {workflow.description}
                  </p>
                )}
                <p className="mt-2 text-caption text-on-surface-variant">{summary(workflow)}</p>
              </Link>
              {/* 카드 밖이 아니라 안 오른쪽 아래 - Link의 자식이면 클릭이 이동을
                  같이 태우므로, li를 relative로 두고 카드 위에 겹친다. */}
              <button
                type="button"
                onClick={() => setDeleteTarget(workflow)}
                aria-label={`${workflow.name} 삭제`}
                className="icon-btn absolute bottom-2 right-2 h-8 w-8 text-on-surface-variant hover:bg-error-container hover:text-on-error-container"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 7h16M10 11v6M14 11v6" />
                  <path d="M6 7l1 13a1 1 0 0 0 1 .9h8a1 1 0 0 0 1-.9l1-13" />
                  <path d="M9 7V4h6v3" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="워크플로우 삭제"
          message={`"${deleteTarget.name}" 워크플로우를 삭제합니다. 이 워크플로우로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/workflows/${deleteTarget.id}`, { method: "DELETE" });
            setWorkflows((prev) => (prev ?? []).filter((w) => w.id !== deleteTarget.id));
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}

      {workflows !== null && workflows.length > 0 && (
        <p className="text-caption text-on-surface-variant">
          워크플로우는 저장된 절차입니다. 간선이 실행 순서를 정하고, 놓은 분류와 도구는 권한
          경계라서 목록 밖의 도구를 지정한 그래프는 저장 시점에 통째로 거부됩니다. 아무것도 놓지
          않으면 전체를 허용한다는 뜻입니다.
        </p>
      )}
    </PageShell>
  );
}
