"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import BrandingSection from "@/components/settings/BrandingSection";
import ModelsSection from "@/components/settings/ModelsSection";
import type { RuntimeSetting, SettingsPayload } from "@/lib/types";

/** 카테고리 하나 = 왼쪽(모바일은 위쪽) 탐색의 한 항목. 설정은 앞으로도 늘어나므로
 * 한 화면에 전부 펼치지 않고 고른 카테고리만 오른쪽에 그린다(소유자 요구).
 * API의 group 키가 곧 카테고리 id다. 여기 없는 group은 키 그대로 뒤에 붙어 보이므로
 * 나중에 추가된 설정이 숨지 않는다. */
const CATEGORIES: { id: string; title: string; summary: string; note?: string }[] = [
  { id: "branding", title: "화면 브랜딩", summary: "제목 · 첫 화면 문구 · 마스코트" },
  { id: "models", title: "모델", summary: "허가 · 기본 · 로컬 GPU" },
  {
    id: "retrieval",
    title: "검색과 답변",
    summary: "근거 수 · 후보 · 재시도",
    note: "저장하면 다음 질문부터 바로 적용됩니다. 서버를 다시 시작할 필요는 없습니다.",
  },
  {
    id: "intent",
    title: "의도 분류",
    summary: "잡담/검색 판정 모델 · 켬/끔",
    note:
      "질문을 문서에서 찾기 전에 '검색할 질문인가, 인사·잡담·시스템 질문인가'를 값싼 모델 한 번으로 가립니다. " +
      "이미지가 첨부되면 그 사실도 함께 알려 그림 자체에 대한 요청은 검색 없이 답합니다. " +
      "판정 기준(프롬프트)은 프롬프트 관리의 intent_agent에서 고칩니다 - 반드시 chat 또는 search 한 단어로 답하게 두어야 하며, 그 외 출력은 전부 검색으로 처리됩니다.",
  },
  {
    id: "documents",
    title: "문서 관리",
    summary: "점검 필요 기준",
    note: "문서 탐색기의 표시 규칙입니다. 검색에는 영향이 없습니다.",
  },
  {
    id: "chunking",
    title: "문서 분할",
    summary: "청크 크기 · 겹침 · 병합",
    note:
      "저장하면 앞으로 등록되는 문서에만 적용됩니다. 이미 색인된 문서의 청크는 바뀌지 않으며, 바꾸려면 그 문서를 다시 등록해야 합니다.",
  },
];

// 화면에서 바꿀 수 없는 값. 별도 탭으로 두자 모바일 가로 스크롤 밖으로 밀려 "사라진"
// 것처럼 보였다(소유자 지적). 중요한 안내라 숨기지 않고, 설정 카테고리마다 하단에
// 접힌 채로 항상 보이게 둔다 - 펼치면 이유까지 읽는다.
const ENV_ONLY_NOTE =
  "아래 값들은 바꾸면 이미 저장된 데이터와 어긋나므로 환경변수(.env)로만 관리합니다. 화면에서 바꿀 수 있게 두면 코퍼스가 조용히 망가집니다.";

function showValue(setting: RuntimeSetting, value: RuntimeSetting["value"]): string {
  if (setting.kind === "bool") return value ? "켬" : "끔";
  if (setting.kind === "str") return value === "" ? "기본값" : String(value);
  return String(value);
}

function SettingRow({
  setting,
  onSaved,
}: {
  setting: RuntimeSetting;
  onSaved: () => Promise<void>;
}) {
  // Uncontrolled by the server once the admin has typed: a background reload
  // must not overwrite what is under the cursor. Re-seeded when the saved value
  // changes, which is what makes 되돌리기 update the box.
  const [draft, setDraft] = useState(String(setting.value));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(String(setting.value));
  }, [setting.value]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // The SERVER is what refuses a bad value, so the input is not clamped and
      // the button is never disabled on one: the Korean 400 renders under the
      // field. A disabled button would hide the guard instead of exercising it.
      await apiFetch(`/api/settings/${setting.key}`, {
        method: "PUT",
        body: JSON.stringify({ value: draft }),
      });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/settings/${setting.key}`, { method: "DELETE" });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const numeric = setting.kind === "int" || setting.kind === "float";

  return (
    <form onSubmit={save} className="rounded-md bg-surface-container-low p-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="text-body font-medium text-on-surface">{setting.label}</h3>
        <code className="text-caption text-on-surface-variant">{setting.key}</code>
        {setting.overridden && (
          <span className="rounded-xs bg-primary-container px-2 py-0.5 text-caption font-medium text-on-primary-container">
            변경됨
          </span>
        )}
      </div>
      <p className="mt-1 text-caption text-on-surface-variant">{setting.help}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={setting.key} className="sr-only">
          {setting.label}
        </label>
        {/* 숫자는 입력칸, 모델·켬끔은 select - 자유 입력할 이유가 없는 값이다. */}
        {setting.kind === "bool" ? (
          <select id={setting.key} value={draft} onChange={(e) => setDraft(e.target.value)} className="field w-32">
            <option value="true">켬</option>
            <option value="false">끔</option>
          </select>
        ) : setting.kind === "str" ? (
          <select
            id={setting.key}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="field w-full max-w-xs"
          >
            {(setting.choices ?? [String(setting.value)]).map((choice) => (
              <option key={choice} value={choice}>
                {choice === "" ? "기본값 (질문 다시 쓰기 모델과 같음)" : choice}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={setting.key}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            inputMode="decimal"
            className="field w-32"
          />
        )}
        <button type="submit" disabled={busy} className="btn-filled btn-compact">
          {busy ? "저장 중..." : "저장"}
        </button>
        {setting.overridden && (
          <button type="button" onClick={() => void reset()} disabled={busy} className="btn-text btn-compact">
            기본값({showValue(setting, setting.env_value)})으로 되돌리기
          </button>
        )}
        <span className="text-caption text-on-surface-variant">
          {numeric && `허용 범위 ${setting.minimum} ~ ${setting.maximum} · `}
          {setting.overridden ? `.env 값 ${showValue(setting, setting.env_value)}` : ".env 값과 동일"}
        </span>
      </div>
      <div className="mt-2">
        <ErrorBanner message={error} />
      </div>
    </form>
  );
}

export default function SettingsPage() {
  // null is "not loaded yet", not "empty". GET /api/settings answers a non-admin
  // with 403 관리자 권한이 필요합니다., which lands in loadError, so this page
  // needs no role branch of its own - the same shape as 프롬프트 관리.
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [active, setActive] = useState<string>("branding");

  const load = useCallback(async () => {
    try {
      setPayload(await apiFetch<SettingsPayload>("/api/settings"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 고른 카테고리는 URL 해시에 남긴다 - 새로 고쳐도, 링크로 건너와도 같은 자리.
  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash) setActive(hash);
  }, []);
  function choose(id: string) {
    setActive(id);
    window.history.replaceState(null, "", `#${id}`);
  }

  // API가 아는 group 중 CATEGORIES에 없는 것은 키 그대로 뒤에 붙인다.
  const apiGroups = [...new Set(payload?.settings.map((s) => s.group) ?? [])];
  const categories: { id: string; title: string; summary: string; note?: string }[] = [
    ...CATEGORIES,
    ...apiGroups
      .filter((g) => !CATEGORIES.some((c) => c.id === g))
      .map((g) => ({ id: g, title: g, summary: "" })),
  ];
  const current = categories.find((c) => c.id === active) ?? categories[0];
  const rows = payload?.settings.filter((s) => s.group === current.id) ?? [];
  // 성격이 맞는 탭 밑에만 접혀 보인다 - 아홉 개가 탭마다 반복되던 것(소유자 지적).
  const envRows = payload?.env_only.filter((e) => e.group === current.id) ?? [];

  return (
    <PageShell>
      <h1 className="text-center text-headline font-medium md:text-left">고급 설정</h1>
      <ErrorBanner message={loadError} />

      {/* 데스크톱: 왼쪽 세로 목록 + 오른쪽 내용. 모바일: 위쪽 가로 스크롤 칩 + 아래 내용.
          같은 버튼 목록이 두 배치를 다 맡는다 - 컴포넌트를 둘로 두면 곧 어긋난다. */}
      <div className="md:grid md:grid-cols-[220px_minmax(0,1fr)] md:items-start md:gap-6">
        <nav
          aria-label="설정 카테고리"
          className="mb-4 flex flex-wrap gap-2 md:sticky md:top-6 md:mb-0 md:flex-col"
        >
          {categories.map((c) => {
            const selected = c.id === current.id;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => choose(c.id)}
                aria-current={selected ? "page" : undefined}
                className={`shrink-0 rounded-md px-3 py-2 text-left transition-colors duration-150 md:w-full ${
                  selected
                    ? "bg-primary-container text-on-primary-container"
                    : "text-on-surface-variant hover:bg-surface-container-high"
                }`}
              >
                <span className="block text-body font-medium">{c.title}</span>
                {c.summary && <span className="hidden text-caption md:block">{c.summary}</span>}
              </button>
            );
          })}
        </nav>

        {/* 제목은 탭이 맡는다 - 같은 글자를 세 번 보이던 것(탭·제목·섹션 제목)을 걷어냈다. */}
        <section className="min-w-0 space-y-3" aria-label={current.title}>
          {current.note && <p className="notice">{current.note}</p>}

          {current.id === "branding" ? (
            <BrandingSection />
          ) : current.id === "models" ? (
            <ModelsSection />
          ) : payload === null ? (
            !loadError && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
          ) : (
            <>
              {/* Two-up from 2xl: a card holds a label, one sentence, one control
                  and a button, and one column left two thirds of every card empty
                  on a wide screen. items-start so an error banner does not stretch
                  the neighbour. */}
              <div className="grid gap-3 2xl:grid-cols-2 2xl:items-start">
                {rows.map((setting) => (
                  <SettingRow key={setting.key} setting={setting} onSaved={load} />
                ))}
              </div>
              {current.id === "intent" && (
                <p className="text-body text-on-surface-variant">
                  판정 프롬프트 편집:{" "}
                  <Link href="/prompts" className="text-primary underline">
                    프롬프트 관리 → intent_agent
                  </Link>
                </p>
              )}
              {rows.length === 0 && (
                <p className="py-8 text-center text-body text-on-surface-variant">이 카테고리에는 설정이 없습니다.</p>
              )}
              {envRows.length > 0 && (
              <details className="group rounded-md bg-surface-container-low">
                {/* 기본 ▶ 마커를 숨기고 두 줄로: 제목 / "펼쳐서 이유 보기 ▾". 한 줄에 붙이면
                    모바일에서 안내 문구가 어색하게 꺾였다(소유자 지적). 세모는 열리면 뒤집힌다. */}
                <summary className="cursor-pointer list-none px-4 py-3 [&::-webkit-details-marker]:hidden">
                  <span className="block text-body font-medium text-on-surface">
                    여기서 바꿀 수 없는 값 {envRows.length}개 (환경변수 전용)
                  </span>
                  <span className="mt-1 flex items-center gap-1 text-caption text-on-surface-variant">
                    <span className="group-open:hidden">펼쳐서 이유 보기</span>
                    <span className="hidden group-open:inline">접기</span>
                    <svg
                      aria-hidden="true"
                      viewBox="0 0 24 24"
                      className="h-4 w-4 transition-transform duration-150 group-open:rotate-180"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="m6 9 6 6 6-6" />
                    </svg>
                  </span>
                </summary>
                <div className="space-y-3 px-4 pb-4">
                  <p className="notice">{ENV_ONLY_NOTE}</p>
                  <div className="grid gap-3 2xl:grid-cols-2 2xl:items-start">
                    {envRows.map((item) => (
                      <div key={item.key} className="rounded-md bg-surface p-4">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <h3 className="text-body font-medium text-on-surface">{item.label}</h3>
                          <code className="text-caption text-on-surface-variant">{item.key}</code>
                        </div>
                        <p className="mt-1 text-caption text-on-surface-variant">{item.reason}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </details>
              )}
            </>
          )}
        </section>
      </div>
    </PageShell>
  );
}
