"use client";

import { useEffect, useRef, useState } from "react";
import { CHIP, CLIP, Glyph, MAX_HEIGHT, MIC, MenuRow, StateChip } from "@/components/chat/Composer";
import ModelPicker from "@/components/chat/ModelPicker";
import PopoverSheet from "@/components/chat/PopoverSheet";
import Switch from "@/components/ui/Switch";
import { useSpeechInput } from "@/lib/useSpeechInput";
import type { AnswerModel } from "@/lib/types";

/** 코드 탭 입력창 - 채팅 입력창과 같은 껍데기·같은 자리. 왼쪽 +(첨부·설정), 그 옆 상태 칩(모델·자동 승인),
 * 오른쪽 마이크와 ↑. 채팅과 다른 것은 메뉴의 내용뿐이다: 첨부는 작업 공간에 올라가고, 설정은 코딩 모델·자동
 * 승인·파일 보기. 두 화면의 입력창이 같은 부품(Composer.tsx의 export)으로 그려져 따로 늙지 않는다. */

const FOLDER = <path d="M3 8a2 2 0 0 1 2-2h3.2l1.8 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />;
const FILES = (
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
    <path d="M14 3v5h5" />
  </>
);
const CHECK = (
  <>
    <path d="M12 3 4 7v5c0 4.4 3.4 8.3 8 9.5 4.6-1.2 8-5.1 8-9.5V7Z" />
    <path d="m9 12 2 2 4-4" />
  </>
);

type Sheet = null | "menu" | "model";

export default function CodeComposer({
  value,
  onChange,
  onSubmit,
  sending,
  onStop,
  models,
  model,
  onModelChange,
  autoApprove,
  onAutoApproveChange,
  canAttach,
  canAttachFolder = false,
  onFiles,
  onOpenFiles,
  disabled,
  placeholder,
  statusText,
  showModelChip = true,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  sending: boolean;
  onStop: () => void;
  models: AnswerModel[];
  model: string;
  onModelChange: (id: string) => void;
  autoApprove: boolean;
  onAutoApproveChange: (on: boolean) => void;
  /** 서버 작업 공간만 - 내 컴퍼터 폴더에는 파일 탐색기로 직접 넣는다. */
  canAttach: boolean;
  /** 코워크만: 폴더를 통째로 넣는다(상대 경로 유지). 코드는 폴더면 작업 공간으로 - 내 컴퓨터 연결. */
  canAttachFolder?: boolean;
  onFiles: (files: FileList) => void;
  onOpenFiles: () => void;
  disabled?: boolean;
  placeholder: string;
  /** 지금 도는 도구·올리는 중 같은 한 줄. 비면 안 그린다. */
  statusText?: string | null;
  /** 간편 모드는 칩을 숨긴다(+ 메뉴에는 남는다) - 모델 이름은 라이트 사용자에게 소음이다. */
  showModelChip?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const dirRef = useRef<HTMLInputElement>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const composingRef = useRef(false);
  const currentModel = models.find((m) => m.id === model) ?? models[0];
  const speech = useSpeechInput((heard) => onChange(value ? `${value.replace(/\s+$/, "")} ${heard}` : heard));

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);

  const closeSheet = () => setSheet(null);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!sending) onSubmit();
      }}
      className="rounded-xl bg-surface-container p-2 outline-primary transition-colors duration-150 focus-within:outline focus-within:outline-2"
    >
      <div className="flex flex-col gap-1.5">
        <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => (e.target.files && onFiles(e.target.files), (e.target.value = ""))} />
        <input ref={dirRef} type="file" multiple className="hidden" {...({ webkitdirectory: "" } as Record<string, string>)} onChange={(e) => (e.target.files && onFiles(e.target.files), (e.target.value = ""))} />

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onCompositionStart={() => (composingRef.current = true)}
          onCompositionEnd={() => (composingRef.current = false)}
          onKeyDown={(e) => {
            // 한글 후보 확정의 Enter는 IME의 것 - 채팅 입력창과 같은 세 겹 확인.
            const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean };
            const composing = composingRef.current || native.isComposing === true || native.keyCode === 229;
            if (e.key !== "Enter" || e.shiftKey || composing) return;
            e.preventDefault();
            if (!sending) onSubmit();
          }}
          placeholder={speech.listening ? "듣는 중입니다… 마이크를 다시 누르면 받아적은 내용이 들어갑니다." : placeholder}
          aria-label="에이전트에게 시킬 일"
          style={{ maxHeight: MAX_HEIGHT }}
          className="w-full resize-none bg-transparent px-2 py-2 text-body-lg text-on-surface placeholder:text-on-surface-variant focus:outline-none disabled:opacity-60"
        />

        <div className="flex items-center gap-1.5">
          <button
            ref={plusRef}
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => setSheet("menu")}
            aria-haspopup="dialog"
            aria-expanded={sheet === "menu"}
            aria-label="추가"
            className="icon-btn shrink-0"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>

          <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
            {showModelChip && currentModel && (
              <StateChip icon={CHIP} label={currentModel.label} onClick={() => setSheet("model")} ariaLabel={`코딩 모델: ${currentModel.label}`} title="누르면 바꿉니다." />
            )}
            {autoApprove && (
              <StateChip icon={CHECK} label="자동 승인" active onClick={() => onAutoApproveChange(false)} ariaLabel="자동 승인 켜짐" title="누르면 끕니다. 파일 수정·명령 실행을 다시 하나씩 묻습니다." />
            )}
            {statusText && (
              <span className="min-w-0 truncate text-caption text-on-surface-variant" aria-live="polite">
                {statusText}
              </span>
            )}
          </div>

          {speech.supported && (
            <button
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={speech.toggle}
              aria-pressed={speech.listening}
              aria-label={speech.listening ? "듣기 끝내고 입력" : "음성으로 입력"}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors duration-150 ${
                speech.listening ? "mic-listening bg-error text-on-error" : "bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest"
              }`}
            >
              <Glyph className="h-5 w-5">{MIC}</Glyph>
            </button>
          )}

          {sending ? (
            <button
              key="stop"
              type="button"
              onClick={onStop}
              aria-label="작업 중단"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-container-high text-on-surface transition-colors duration-150 hover:bg-surface-container-highest"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                <rect x="7" y="7" width="10" height="10" rx="1.5" />
              </svg>
            </button>
          ) : (
            <button
              key="send"
              type="submit"
              aria-label="전송"
              disabled={disabled || !value.trim()}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors duration-150 ${
                value.trim() && !disabled ? "bg-primary text-on-primary" : "bg-surface-container-high text-on-surface-variant"
              }`}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <PopoverSheet open={sheet === "menu"} onClose={closeSheet} anchorRef={plusRef} label="추가">
        <div className="p-2 pb-6 sm:pb-2">
          {canAttach && (
            <div role="group" aria-labelledby="code-menu-message">
              <p id="code-menu-message" className="px-3 py-2 text-label font-medium text-on-surface-variant">
                작업 공간에 넣기
              </p>
              <MenuRow icon={CLIP} label="파일 첨부" onClick={() => (closeSheet(), fileRef.current?.click())} />
              {/* 폴더 넣기는 코워크만(소유자): 코드에서 폴더 단위면 그 폴더가 작업 공간이다 - 내 컴퓨터 연결. */}
              {canAttachFolder && (
                <MenuRow icon={FOLDER} label="폴더 넣기" onClick={() => (closeSheet(), dirRef.current?.click())} title="폴더 안 파일이 상대 경로 그대로 작업 공간에 들어갑니다. 아이폰 사파리는 폴더 선택을 지원하지 않아 zip으로 넣어 주세요." />
              )}
            </div>
          )}
          <div role="group" aria-labelledby="code-menu-settings" className={canAttach ? "mt-1 border-t border-outline-variant pt-1" : ""}>
            <p id="code-menu-settings" className="px-3 py-2 text-label font-medium text-on-surface-variant">
              작업 설정
            </p>
            {currentModel && <MenuRow icon={CHIP} label="코딩 모델" value={currentModel.label} onClick={() => setSheet("model")} />}
            <MenuRow
              icon={CHECK}
              label="자동 승인"
              trailing={<Switch on={autoApprove} />}
              pressed={autoApprove}
              onClick={() => onAutoApproveChange(!autoApprove)}
              title="켜면 파일 수정·명령 실행을 묻지 않고 진행합니다."
            />
            <MenuRow icon={FILES} label="파일 보기" onClick={() => (closeSheet(), onOpenFiles())} title="작업 공간의 파일을 보고 받습니다." />
          </div>
        </div>
      </PopoverSheet>

      <ModelPicker
        models={models}
        value={model}
        onChange={onModelChange}
        open={sheet === "model"}
        onClose={closeSheet}
        anchorRef={plusRef}
        reasoningEffort="medium"
        onReasoningEffortChange={() => {}}
        onEffortPicked={closeSheet}
      />
    </form>
  );
}
