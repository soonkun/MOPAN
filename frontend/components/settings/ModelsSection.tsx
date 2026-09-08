"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import Switch from "@/components/ui/Switch";
import type { AdminModel } from "@/lib/types";

/** 고급 설정 > 모델. 사용자가 고를 수 있는 모델(허가), 기본 모델, 이미지·추론 지원을
 * 관리자가 토글로 정한다. .env의 ANSWER_MODELS는 시작 목록일 뿐이고 여기서 끈 모델은
 * 사용자에게 보이지 않으며 요청이 이름을 대도 서버가 거절한다(app/llm/catalog.py). */
export default function ModelsSection() {
  const [models, setModels] = useState<AdminModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [newProvider, setNewProvider] = useState<"openai" | "local">("openai");

  const load = useCallback(async () => {
    try {
      setModels(await apiFetch<AdminModel[]>("/api/admin/models"));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function update(id: string, body: Partial<AdminModel>) {
    setBusy(id);
    setError(null);
    try {
      await apiFetch(`/api/admin/models/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function refresh() {
    setBusy("refresh");
    setError(null);
    try {
      const result = await apiFetch<{ added: string[]; local_base_url: string }>("/api/admin/models/refresh", {
        method: "POST",
      });
      setNotice(
        result.added.length
          ? `로컬 모델 ${result.added.length}개를 새로 찾았습니다: ${result.added.join(", ")} (허가는 꺼진 상태)`
          : `${result.local_base_url}에 새 모델이 없습니다.`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!newId.trim()) return;
    setBusy("add");
    setError(null);
    try {
      await apiFetch("/api/admin/models", {
        method: "POST",
        body: JSON.stringify({ id: newId.trim(), provider: newProvider }),
      });
      setNewId("");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const groups: { title: string; note: string; items: AdminModel[] }[] = models
    ? [
        {
          title: "OpenAI",
          note: "질문마다 API 비용이 든다. .env의 ANSWER_MODELS에 있는 모델은 처음부터 허가된 채 나온다.",
          items: models.filter((m) => m.provider !== "local"),
        },
        {
          title: "로컬 GPU",
          note: "이 서버의 GPU에 띄운 모델(Ollama). 비용은 없고 속도는 모델 크기를 따른다. 새로 띄운 모델은 '새로 고침'으로 찾는다.",
          items: models.filter((m) => m.provider === "local"),
        },
      ]
    : [];

  return (
    <div className="space-y-4">
      <p className="notice">
        허가를 켠 모델만 사용자의 모델 선택기에 나옵니다. 기본 모델은 모델을 고르지 않은 질문이 쓰는 모델입니다.
        이미지·추론 표시는 그 모델에 첨부 사진을 보낼지, 추론 수준 조절을 보일지를 정합니다 - 이름으로 유추한
        값이니 틀리면 여기서 고치세요.
      </p>
      <ErrorBanner message={error} />
      {notice && <p className="text-body text-on-surface-variant">{notice}</p>}
      {models === null && !error && (
        <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      )}

      {groups.map((group) => (
        <section key={group.title} className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-body font-medium text-on-surface">
              {group.title} <span className="text-caption font-normal text-on-surface-variant">{group.items.length}개</span>
            </h3>
            {group.title === "로컬 GPU" && (
              <button type="button" onClick={() => void refresh()} disabled={busy !== null} className="btn-tonal btn-compact">
                {busy === "refresh" ? "찾는 중..." : "로컬 모델 새로 고침"}
              </button>
            )}
          </div>
          <p className="text-caption text-on-surface-variant">{group.note}</p>
          {group.items.length === 0 ? (
            <p className="rounded-md bg-surface-container-low px-4 py-3 text-caption text-on-surface-variant">
              {group.title === "로컬 GPU"
                ? ".env의 LOCAL_LLM_BASE_URL이 비어 있거나 로컬 서버에 모델이 없습니다."
                : "모델이 없습니다."}
            </p>
          ) : (
            <ul className="divide-y divide-outline-variant rounded-md bg-surface-container-low">
              {group.items.map((m) => {
                const working = busy === m.id;
                // 모바일: 이름 줄 아래에 스위치 줄(소유자 지적 - 한 줄에 몰아넣으면 이름이 세로로 쪼개졌다).
                // 데스크톱(sm 이상): 이름과 스위치를 한 줄에.
                return (
                  <li key={m.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-x-3">
                    <div className="min-w-0 sm:flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-body font-medium text-on-surface">{m.label}</span>
                        {m.label !== m.id && <code className="text-caption text-on-surface-variant">{m.id}</code>}
                        {m.is_default && (
                          <span className="rounded-xs bg-primary-container px-2 py-0.5 text-caption font-medium text-on-primary-container">
                            기본
                          </span>
                        )}
                      </div>
                    </div>
                    {/* 세 토글 + 기본 라디오. 모바일은 줄바꿈으로 두 줄이 된다. */}
                    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-caption text-on-surface-variant">
                      <button
                        type="button"
                        disabled={working}
                        aria-pressed={m.enabled}
                        onClick={() => void update(m.id, { enabled: !m.enabled })}
                        className="flex items-center gap-1.5"
                        title="사용자가 이 모델을 고를 수 있는가"
                      >
                        <Switch on={m.enabled} />
                        허가
                      </button>
                      <button
                        type="button"
                        disabled={working || !m.enabled}
                        aria-pressed={m.supports_vision}
                        onClick={() => void update(m.id, { supports_vision: !m.supports_vision })}
                        className="flex items-center gap-1.5 disabled:opacity-40"
                        title="첨부 이미지를 읽을 수 있는 모델인가"
                      >
                        <Switch on={m.supports_vision} />
                        이미지
                      </button>
                      <button
                        type="button"
                        disabled={working || !m.enabled}
                        aria-pressed={m.supports_reasoning}
                        onClick={() => void update(m.id, { supports_reasoning: !m.supports_reasoning })}
                        className="flex items-center gap-1.5 disabled:opacity-40"
                        title="추론 수준(즉시/낮음/중간/깊이)을 받는 모델인가"
                      >
                        <Switch on={m.supports_reasoning} />
                        추론
                      </button>
                      <label className={`flex items-center gap-1.5 ${m.enabled ? "" : "opacity-40"}`} title="모델을 고르지 않은 질문이 쓰는 모델">
                        <input
                          type="radio"
                          name="default-model"
                          checked={m.is_default}
                          disabled={working || !m.enabled}
                          onChange={() => void update(m.id, { is_default: true })}
                        />
                        기본
                      </label>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}

      <form onSubmit={add} className="flex flex-wrap items-center gap-2 rounded-md bg-surface-container-low p-4">
        <span className="text-body font-medium text-on-surface">모델 직접 추가</span>
        <input
          value={newId}
          onChange={(e) => setNewId(e.target.value)}
          placeholder="예) gpt-4.1-mini 또는 qwen3:32b"
          className="field min-w-0 flex-1"
          aria-label="모델 이름"
        />
        <select value={newProvider} onChange={(e) => setNewProvider(e.target.value as "openai" | "local")} className="field w-32" aria-label="제공자">
          <option value="openai">OpenAI</option>
          <option value="local">로컬 GPU</option>
        </select>
        <button type="submit" disabled={busy !== null || !newId.trim()} className="btn-filled btn-compact">
          추가
        </button>
        <span className="w-full text-caption text-on-surface-variant">
          발견되지 않은 이름을 적어 넣습니다. 허가는 꺼진 채 추가되니 켜서 쓰세요. 없는 이름은 첫 질문에서 제공자 오류로 드러납니다.
        </span>
      </form>
    </div>
  );
}
