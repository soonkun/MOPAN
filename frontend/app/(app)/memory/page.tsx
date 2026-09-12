"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage } from "@/lib/api";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";

type MemoryItem = {
  id: string;
  content: string;
  source_conversation_id: string | null;
  created_at: string;
};

/** 대화를 넘어 남는 내 기억(백엔드 app/chat/user_memory.py). 계정에 묶여 본인만 보고,
 * 한 줄씩 또는 전부 지운다. 새로 붙는 건 매 턴 뒤 백그라운드라 여기서 만들지는 않는다 -
 * 기억은 대화에서 나온다. */
export default function MemoryPage() {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [maxItems, setMaxItems] = useState(50);
  const [error, setError] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: MemoryItem[]; max_items: number }>("/api/memory");
      setItems(data.items);
      setMaxItems(data.max_items);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(id: string) {
    try {
      await apiFetch(`/api/memory/${id}`, { method: "DELETE" });
      setItems((prev) => prev?.filter((m) => m.id !== id) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function clearAll() {
    setClearing(false);
    try {
      await apiFetch("/api/memory", { method: "DELETE" });
      setItems([]);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <PageShell>
      <PageHeader
        title="내 기억"
        subtitle={items && `${items.length}개 · 최대 ${maxItems}개`}
        actions={
          items && items.length > 0 ? (
            <button type="button" onClick={() => setClearing(true)} className="btn-tonal btn-compact">
              전부 지우기
            </button>
          ) : undefined
        }
      />
      <p className="max-w-[40rem] break-keep text-body text-on-surface-variant">
        대화에서 드러난 나에 관한 사실(하는 일, 진행 중인 프로젝트, 답을 받는 방식의 선호)이 한 줄씩
        남고, 새 대화를 열어도 답변 모델이 이것을 봅니다. 내 계정에만 묶여 있고 다른 사용자는 볼 수
        없습니다. 틀렸거나 남기고 싶지 않은 줄은 여기서 지우면 다음 답변부터 반영됩니다. {maxItems}개를
        넘으면 오래된 것부터 자동으로 지워지고, 출처 대화를 지워도 기억은 남습니다.
      </p>
      <ErrorBanner message={error} />
      {items === null ? (
        <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : items.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">
          아직 기억이 없습니다. 대화를 나누면 나에 관한 사실이 여기 쌓입니다.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-outline-variant rounded-md bg-surface-container-low">
          {items.map((m) => (
            <li key={m.id} className="flex items-start gap-3 px-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="break-keep text-body text-on-surface">{m.content}</p>
                <p className="mt-1 text-caption text-on-surface-variant">
                  {new Date(m.created_at).toLocaleDateString("ko-KR")}
                  {" · "}
                  {m.source_conversation_id ? (
                    <Link href={`/chat/${m.source_conversation_id}`} className="text-primary underline">
                      출처 대화
                    </Link>
                  ) : (
                    "출처 대화 삭제됨"
                  )}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void remove(m.id)}
                aria-label="이 기억 지우기"
                className="btn-tonal btn-compact shrink-0"
              >
                지우기
              </button>
            </li>
          ))}
        </ul>
      )}
      {clearing && (
        <ConfirmDialog
          title="기억 전부 지우기"
          message="나에 관해 기억된 내용을 모두 지웁니다. 되돌릴 수 없고, 이후 대화에서 다시 쌓입니다."
          confirmLabel="전부 지우기"
          onConfirm={clearAll}
          onClose={() => setClearing(false)}
        />
      )}
    </PageShell>
  );
}
