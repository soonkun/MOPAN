"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import BrandingSection from "@/components/settings/BrandingSection";
import type { RuntimeSetting, SettingsPayload } from "@/lib/types";

// The API returns a group key, not a heading. The copy belongs on the screen,
// and an unmapped group falls back to its key rather than to a blank so a
// setting added later is still visible.
const GROUP_TITLE: Record<string, string> = {
  retrieval: "검색과 답변",
  chunking: "문서 분할",
};

const GROUP_NOTE: Record<string, string> = {
  retrieval: "저장하면 다음 질문부터 바로 적용됩니다. 서버를 다시 시작할 필요는 없습니다.",
  chunking:
    "저장하면 앞으로 등록되는 문서에만 적용됩니다. 이미 색인된 문서의 청크는 바뀌지 않으며, 바꾸려면 그 문서를 다시 등록해야 합니다.",
};

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
        <input
          id={setting.key}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          inputMode="decimal"
          className="field w-32"
        />
        <button type="submit" disabled={busy} className="btn-filled btn-compact">
          {busy ? "저장 중..." : "저장"}
        </button>
        {setting.overridden && (
          <button type="button" onClick={() => void reset()} disabled={busy} className="btn-text btn-compact">
            기본값({setting.env_value})으로 되돌리기
          </button>
        )}
        <span className="text-caption text-on-surface-variant">
          허용 범위 {setting.minimum} ~ {setting.maximum}
          {setting.overridden ? ` · .env 값 ${setting.env_value}` : " · .env 값과 동일"}
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

  const groups = [...new Set(payload?.settings.map((s) => s.group) ?? [])];

  return (
    <PageShell>
      <h1 className="text-center text-headline font-medium md:text-left">고급 설정</h1>

      <BrandingSection />
      <ErrorBanner message={loadError} />

      {payload === null ? (
        !loadError && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : (
        <>
          {groups.map((group) => (
            <section key={group} className="space-y-3">
              <h2 className="text-title font-medium">{GROUP_TITLE[group] ?? group}</h2>
              {GROUP_NOTE[group] && (
                // Tone, not a rule: nothing has gone wrong, so this is a
                // .notice rather than a banner. It is the sentence an admin who
                // changes 청크 크기 and waits for the corpus to change would
                // otherwise learn the slow way.
                <p className="notice">{GROUP_NOTE[group]}</p>
              )}
              {/* Two-up from 2xl. Each card holds a label, one sentence, a
                  32-character input and a button - at 1600px a single column
                  left roughly two thirds of every card empty and made the page
                  2011px tall on a 1080px screen, which is the "빈 화면" the
                  owner was looking at. The grid spends the width the shell
                  gained instead of stretching one control across it. items-start
                  so a card with an error banner does not stretch its neighbour. */}
              <div className="grid gap-3 2xl:grid-cols-2 2xl:items-start">
                {payload.settings
                  .filter((s) => s.group === group)
                  .map((setting) => (
                    <SettingRow key={setting.key} setting={setting} onSaved={load} />
                  ))}
              </div>
            </section>
          ))}

          <section className="space-y-3">
            <h2 className="text-title font-medium">여기서 바꿀 수 없는 값</h2>
            {/* Rendered from the API, not written into this file: the reason has
                to live beside the decision in the settings store, where the next
                person who wants to make one of these editable will read it. */}
            <p className="notice">
              아래 값들은 바꾸면 이미 저장된 데이터와 어긋나므로 환경변수(.env)로만 관리합니다. 화면에서
              바꿀 수 있게 두면 코퍼스가 조용히 망가집니다.
            </p>
            <div className="grid gap-3 2xl:grid-cols-2 2xl:items-start">
              {payload.env_only.map((item) => (
                <div key={item.key} className="rounded-md bg-surface-container-low p-4">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <h3 className="text-body font-medium text-on-surface">{item.label}</h3>
                    <code className="text-caption text-on-surface-variant">{item.key}</code>
                  </div>
                  <p className="mt-1 text-caption text-on-surface-variant">{item.reason}</p>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </PageShell>
  );
}
