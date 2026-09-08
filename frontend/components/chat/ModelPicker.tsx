"use client";

import PopoverSheet from "@/components/chat/PopoverSheet";
import type { AnswerModel } from "@/lib/types";

/** Which model answers the next question.
 *
 * No trigger of its own any more: the composer's + menu owns every entry point
 * into this list, so this component is the list and PopoverSheet is the box it
 * arrives in. `open` and `onClose` are what let the menu close itself before
 * this opens - two stacked sheets over a composer is one too many.
 */

// 추론 수준 4단 - 서버가 받는 reasoning_effort 값 그대로(소유자 지적: 3단으로
// 접으면 실제 단계와 화면이 다르다). "즉시"는 minimal이고, GPT-5.6 계열은
// 프로바이더가 none으로 옮긴다(그 계열은 minimal을 받지 않는다 - 실측 400).
const EFFORTS = [
  { id: "minimal", label: "즉시" },
  { id: "low", label: "낮음" },
  { id: "medium", label: "중간" },
  { id: "high", label: "깊이" },
] as const;

export default function ModelPicker({
  models,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
  reasoningEffort,
  onReasoningEffortChange,
  onEffortPicked,
}: {
  models: AnswerModel[];
  value: string;
  onChange: (id: string) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
  /** 추론 모델이 선택된 동안 그 행 밑에 나타나는 즉시/중간/깊이의 현재값. */
  reasoningEffort: string;
  onReasoningEffortChange: (
    value: "minimal" | "low" | "medium" | "high",
  ) => void;
  /** 깊이를 고른 순간 - 시트를 닫고 입력창으로 돌아가는 신호. */
  onEffortPicked: () => void;
}) {
  // Selecting and dismissing are deliberately two different events, and this was
  // found by driving it: with `onChange` closing the sheet, the first ArrowDown
  // a keyboard user pressed moved the selection AND shut the menu, so they could
  // never reach the third model. `change` fires on an arrow key, `click` does not
  // - it fires on a pointer press and on Space, which are the two gestures that
  // MEAN "this one". So change commits the choice and click is what closes.
  // Escape closes too, and keeps whatever the arrows landed on, because the
  // choice was already committed on the way past.

  return (
    <PopoverSheet
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      label="답변 모델"
    >
      {/* Radios, not buttons: arrow-key navigation inside the group, the
          checked state announced, and one tab stop for the whole list all
          come from the platform. pb-6 is the phone's home indicator. */}
      <fieldset className="border-0 p-2 pb-6 sm:pb-2">
        <legend className="px-3 py-2 text-label font-medium text-on-surface-variant">
          답변 모델
        </legend>
        {models.map((model) => (
          <div key={model.id}>
            <label className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2">
              <input
                type="radio"
                name="answer-model"
                value={model.id}
                checked={model.id === value}
                onChange={() => onChange(model.id)}
                // `detail` is the click count. A radio runs its full activation
                // behaviour for an ARROW key too - measured, the sheet closed on
                // the first ArrowDown - so `click` on its own cannot tell
                // browsing from choosing. A pointer press reports detail >= 1;
                // every keyboard-synthesised click reports 0.
                onClick={(event) => {
                  // 추론 모델은 클릭해도 닫지 않는다: 고르는 순간 바로 밑에
                  // 즉시/중간/깊이가 나타나고, 그것까지 골라야 이 흐름이 끝난다
                  // (데스크톱에서 시트가 즉시 닫혀 깊이를 정할 틈이 없던 실사고).
                  if (event.detail > 0 && !model.reasoning) onClose();
                }}
                // The keyboard half: Space and Enter mean "this one", arrows
                // mean "show me the next one".
                //
                // keyDOWN, not keyup, and this was measured too. A button is
                // activated by Enter on keydown, so opening the sheet with Enter
                // moved focus onto this radio in time for the SAME press's keyup
                // to land here and close it again - the sheet flickered open and
                // shut on one keystroke. A keydown belongs to whatever had focus
                // when the key went down, which is the distinction that fixes it.
                onKeyDown={(event) => {
                  // 같은 규칙의 키보드 절반: 추론 모델은 Enter/Space로 골라도
                  // 열린 채 깊이 선택으로 이어진다.
                  if (
                    (event.key === " " || event.key === "Enter") &&
                    !model.reasoning
                  )
                    onClose();
                }}
                className="sr-only"
              />
              <span
                aria-hidden="true"
                className="h-4 w-4 shrink-0 text-primary"
              >
                {model.id === value && (
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="m5 13 4 4L19 7" />
                  </svg>
                )}
              </span>
              <span className="min-w-0 flex-1 truncate text-body">
                {model.label}
              </span>
              {model.provider === "local" && (
                <span className="shrink-0 rounded-full bg-surface-container-high px-2 py-0.5 text-caption text-on-surface-variant">
                  로컬 GPU
                </span>
              )}
              {model.is_default && (
                <span className="shrink-0 text-caption text-on-surface-variant">
                  기본
                </span>
              )}
            </label>
            {/* 고른 모델이 추론 계열이면 그 행 바로 아래, 체크 표시 열에 맞춰
              들여쓴 자리에서 깊이를 정한다 - 목록 끝에 두면 어느 모델의 설정인지
              읽히지 않았다(소유자 지적). 누르면 켜진 채 남는다: 이 시트는 모델을
              '클릭'해야 닫히고, 깊이 조절은 그 전에 눈으로 확인할 상태다. */}
            {model.reasoning && model.id === value && (
              <div
                role="group"
                aria-label={`${model.label} 추론 수준`}
                className="mb-1 ml-10 mr-3 mt-0.5 flex gap-1 rounded-full bg-surface-container-high p-1"
              >
                {EFFORTS.map((effort) => (
                  <button
                    key={effort.id}
                    type="button"
                    aria-pressed={reasoningEffort === effort.id}
                    onClick={() => {
                      onReasoningEffortChange(effort.id);
                      // 깊이가 이 흐름의 마지막 선택이다: 고르면 닫히고 입력창으로.
                      onEffortPicked();
                    }}
                    className={`flex-1 whitespace-nowrap rounded-full px-1 py-1.5 text-center text-caption transition-colors duration-150 ${
                      reasoningEffort === effort.id
                        ? "bg-surface font-medium text-on-surface shadow-sm"
                        : "text-on-surface-variant hover:text-on-surface"
                    }`}
                  >
                    {effort.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </fieldset>
    </PopoverSheet>
  );
}
