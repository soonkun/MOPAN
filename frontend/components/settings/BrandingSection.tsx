"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Branding } from "@/lib/types";

/** 화면 브랜딩 - 고급 설정 맨 위의 한 카드.

MOPAN은 가져다 자기 것으로 만드는 바탕 시스템이라, 사이드바 제목·새 대화
첫 화면의 문구·추천 질문·마스코트가 관리자의 값이어야 한다. 빈 칸은 "기본값
(코드의 문구)"이라는 뜻이고, 그 사실을 자리표시자가 그대로 말한다.

추천 질문은 textarea 한 줄에 하나다. 행 추가 버튼과 드래그 정렬을 들일
만큼의 목록이 아니고(상한 6개), 줄 단위 편집은 관리자가 이미 아는 조작이다. */

export default function BrandingSection() {
  const [branding, setBranding] = useState<Branding | null>(null);
  const [title, setTitle] = useState("");
  const [taglinePrimary, setTaglinePrimary] = useState("");
  const [taglineSecondary, setTaglineSecondary] = useState("");
  const [questions, setQuestions] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [mascotBusy, setMascotBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  function seed(value: Branding) {
    setBranding(value);
    setTitle(value.app_title ?? "");
    setTaglinePrimary(value.tagline_primary ?? "");
    setTaglineSecondary(value.tagline_secondary ?? "");
    setQuestions(value.suggested_questions.join("\n"));
  }

  useEffect(() => {
    apiFetch<Branding>("/api/branding")
      .then(seed)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await apiFetch<Branding>("/api/branding", {
        method: "PUT",
        body: JSON.stringify({
          app_title: title,
          tagline_primary: taglinePrimary,
          tagline_secondary: taglineSecondary,
          suggested_questions: questions.split("\n"),
        }),
      });
      seed(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function uploadMascot(file: File) {
    setMascotBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      // apiFetch가 아니라 fetch: JSON Content-Type을 강제로 붙이면 멀티파트
      // 경계가 사라진다.
      const response = await fetch("/api/branding/mascot", { method: "POST", body });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail ?? "마스코트 업로드에 실패했습니다.");
      }
      const refreshed = await apiFetch<Branding>("/api/branding");
      seed(refreshed);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setMascotBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function resetMascot() {
    setMascotBusy(true);
    setError(null);
    try {
      await apiFetch("/api/branding/mascot", { method: "DELETE" });
      const refreshed = await apiFetch<Branding>("/api/branding");
      seed(refreshed);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setMascotBusy(false);
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-title font-medium">화면 브랜딩</h2>
      <p className="notice">
        사이드바 제목과 새 대화 첫 화면의 문구·추천 질문·마스코트입니다. 비워 두면 MOPAN
        기본값으로 그려집니다.
      </p>
      <form onSubmit={save} className="rounded-md bg-surface-container-low p-4 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="branding-title" className="text-label font-medium text-on-surface-variant">
              제목
            </label>
            <p className="mt-0.5 text-caption text-on-surface-variant">
              사이드바와 새 대화 화면의 이름입니다.
            </p>
            <input
              id="branding-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={60}
              placeholder="MOPAN"
              className="field mt-2 w-full"
            />
          </div>
          <div>
            <span className="text-label font-medium text-on-surface-variant">마스코트</span>
            <p className="mt-0.5 text-caption text-on-surface-variant">
              PNG·JPEG·WebP, 2MB 이하. 새 대화 화면의 그림입니다.
            </p>
            <div className="mt-2 flex items-center gap-3">
              {/* 지금 그려지는 그 그림 - 업로드본이 없으면 기본. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={branding?.has_custom_mascot ? "/api/branding/mascot" : "/mascot.png"}
                alt="현재 마스코트"
                className="h-14 w-14 rounded-md bg-surface-container object-contain"
              />
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void uploadMascot(file);
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={mascotBusy}
                className="btn-tonal btn-compact"
              >
                {mascotBusy ? "처리 중..." : "이미지 올리기"}
              </button>
              {branding?.has_custom_mascot && (
                <button
                  type="button"
                  onClick={() => void resetMascot()}
                  disabled={mascotBusy}
                  className="btn-text btn-compact"
                >
                  기본으로
                </button>
              )}
            </div>
          </div>
          <div>
            <label
              htmlFor="branding-tagline1"
              className="text-label font-medium text-on-surface-variant"
            >
              첫 화면 문구
            </label>
            <input
              id="branding-tagline1"
              value={taglinePrimary}
              onChange={(event) => setTaglinePrimary(event.target.value)}
              maxLength={200}
              placeholder="한 판에서 길러 어느 논에나 옮겨 심습니다."
              className="field mt-2 w-full"
            />
          </div>
          <div>
            <label
              htmlFor="branding-tagline2"
              className="text-label font-medium text-on-surface-variant"
            >
              보조 문구
            </label>
            <input
              id="branding-tagline2"
              value={taglineSecondary}
              onChange={(event) => setTaglineSecondary(event.target.value)}
              maxLength={300}
              placeholder="RAG · MCP · LLM · 워크플로우를 직접 등록하고 조합하는 베이스 시스템입니다."
              className="field mt-2 w-full"
            />
          </div>
          <div className="sm:col-span-2">
            <label
              htmlFor="branding-questions"
              className="text-label font-medium text-on-surface-variant"
            >
              추천 질문 (한 줄에 하나, 최대 6개)
            </label>
            <p className="mt-0.5 text-caption text-on-surface-variant">
              새 대화 화면에 칩으로 뜨고, 누르면 입력창에 채워집니다. 이 배포의 문서가 실제로
              답할 수 있는 질문을 적어 주세요.
            </p>
            <textarea
              id="branding-questions"
              value={questions}
              onChange={(event) => setQuestions(event.target.value)}
              rows={4}
              placeholder={"출원전 공개를 했는데 공지예외주장이 가능한가요?\n어플 이름을 상표로 등록하려면 몇 류로 출원하나요?"}
              className="field mt-2 h-auto w-full py-2"
            />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button type="submit" disabled={busy} className="btn-filled btn-compact">
            {busy ? "저장 중..." : "저장"}
          </button>
          {saved && <span className="text-caption text-primary">저장됐습니다.</span>}
        </div>
        <ErrorBanner message={error} />
      </form>
    </section>
  );
}
