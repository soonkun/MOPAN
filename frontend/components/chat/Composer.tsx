"use client";

import { useEffect, useRef, useState } from "react";
import AgentPicker, { DEFAULT_AGENT_LABEL } from "@/components/chat/AgentPicker";
import AttachmentChip from "@/components/chat/AttachmentChip";
import ModelPicker from "@/components/chat/ModelPicker";
import PopoverSheet from "@/components/chat/PopoverSheet";
import ToolPicker from "@/components/chat/ToolPicker";
import type {
  AgentOption,
  AnswerModel,
  Attachment,
  McpToolOption,
  PendingToolCall,
} from "@/lib/types";

/** One file the user has chosen. It exists on screen from the moment it is
 * picked, before POST /api/attachments has answered, so that a refusal can be
 * rendered on the chip it belongs to rather than as a page-level banner. */
export type PendingAttachment = {
  /** Local, and NOT the attachment id: a chip exists before the server has
   * given it one, and an upload that is refused never gets one at all. */
  key: string;
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  /** A blob: URL made from the File, so an image thumbnail appears immediately
   * and without a second round trip. Revoked by ChatWindow. */
  previewUrl: string | null;
  status: "uploading" | "ready" | "error";
  /** The server's row, once POST /api/attachments has answered. It is what the
   * send carries (its id) and what the sent user turn renders from. */
  attachment: Attachment | null;
  error: string | null;
};

// backend/app/documents/validation.py: ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS.
export const ATTACHMENT_EXTENSIONS = [
  "pdf",
  "docx",
  "txt",
  "md",
  "html",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "gif",
];
const ACCEPT = ATTACHMENT_EXTENSIONS.map((ext) => `.${ext}`).join(",");

// 8 rows of body-lg (26px) plus the textarea's own 8px padding top and bottom.
const MAX_HEIGHT = 8 * 26 + 16;

/** Exactly one overlay is open at a time, and which one is a single value.
 *
 * Two states would let the + menu and a picker be open together, which is the
 * bug the hand-off was written to avoid: two stacked sheets over a composer,
 * two Escapes to get out, and a scrim over a scrim. */
type Sheet = null | "menu" | "model" | "agent" | "tool";

// The four-point spark. Plain currentColor, NOT the brand gradient: §2 reserves
// that for the wordmark, the assistant sparkle and the streaming indicator.
const SPARK = (
  <>
    <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
    <path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />
  </>
);
const SHIELD = (
  <>
    <path d="M12 3 4 7v5c0 4.4 3.2 8.2 8 9 4.8-.8 8-4.6 8-9V7l-8-4Z" />
    <path d="m9 12 2 2 4-4" />
  </>
);
// The processor die. The outer rectangle is load-bearing: without it the pins
// alone read as a snowflake, and in a menu row directly under the spark above
// the two glyphs were telling each other apart by nothing.
const CHIP = (
  <>
    <rect x="5" y="5" width="14" height="14" rx="2.5" />
    <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
    <path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3" />
  </>
);
const WRENCH = (
  <>
    <path d="M14.5 5.5a3.5 3.5 0 0 0 4.6 4.6l-8.9 8.9a2.2 2.2 0 0 1-3.1-3.1z" />
    <path d="M5 5l3 3" />
  </>
);
const CLIP = <path d="M17 8.5 9.4 16a2.5 2.5 0 0 0 3.6 3.6l7.1-7.1a4.5 4.5 0 0 0-6.4-6.4l-7 7a6.5 6.5 0 0 0 9.2 9.2l5.6-5.6" />;

function Glyph({ children, className = "h-5 w-5 shrink-0" }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

/** One row of the + menu.
 *
 * `value` is the trailing label - the current model, the current agent, 켬/끔 -
 * and it is the reason a menu can hold a setting at all: a control that hides
 * what it is set to is worse than no control. The chips outside the menu carry
 * the same values for the state that has to be readable WITHOUT opening it. */
function MenuRow({
  icon,
  label,
  value,
  onClick,
  pressed,
  disabled,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  value?: string;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      disabled={disabled}
      title={title}
      className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left text-body text-on-surface transition-colors duration-150 hover:bg-surface-container-high disabled:cursor-default disabled:opacity-60 sm:py-2"
    >
      <Glyph className="h-5 w-5 shrink-0 text-on-surface-variant">{icon}</Glyph>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {value && (
        <span className="max-w-[8rem] shrink-0 truncate text-caption text-on-surface-variant">
          {value}
        </span>
      )}
    </button>
  );
}

/** A state chip in the composer, above the textarea.
 *
 * Not decoration: it is the answer to "which model is about to answer this" and
 * "is the super agent on", and both are questions asked BEFORE sending. Pressing
 * one opens the sheet that changes it, so the menu is not the only way in. */
function StateChip({
  icon,
  label,
  onClick,
  active,
  disabled,
  ariaLabel,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  ariaLabel: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      // A pointer press that moves focus off the textarea dismisses the phone
      // keyboard under the user; the same rule every control in here keeps.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-caption transition-colors duration-150 disabled:cursor-default ${
        active
          ? "bg-primary-container text-on-primary-container"
          : "bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest"
      }`}
    >
      <Glyph className="h-3.5 w-3.5 shrink-0">{icon}</Glyph>
      <span aria-hidden="true" className="truncate">
        {label}
      </span>
    </button>
  );
}

export default function Composer({
  value,
  onChange,
  onSubmit,
  onFiles,
  attachments,
  onRemove,
  sending,
  onStop,
  textareaRef,
  models,
  model,
  onModelChange,
  tools,
  toolCall,
  onToolSelect,
  onToolRemove,
  orchestrator,
  onOrchestratorChange,
  agents,
  agentId,
  onAgentChange,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onFiles: (files: File[]) => void;
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
  sending: boolean;
  onStop: () => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  models: AnswerModel[];
  model: string;
  onModelChange: (id: string) => void;
  /** GET /api/mcp/tools. Empty for a deployment with no MCP server registered,
   * which is what drops the 도구 사용 row from the menu rather than opening
   * nothing. */
  tools: McpToolOption[];
  /** ONE pending call. Slice 2 is manual invocation; a plan that runs several
   * steps is Slice 3, and the backend already accepts a list. */
  toolCall: PendingToolCall | null;
  onToolSelect: (call: PendingToolCall) => void;
  onToolRemove: () => void;
  /** Slice 3's Super Agent, chosen per question the way the model is. A toggle
   * rather than a third picker: there are two modes, and the direct RAG path is
   * the default until the orchestrator measures better on the eval set. */
  orchestrator: boolean;
  onOrchestratorChange: (value: boolean) => void;
  /** GET /api/agents/selectable. Empty for a deployment with no agent
   * configured, which drops the row rather than offering a single 기본 row that
   * is not a choice. */
  agents: AgentOption[];
  /** null is the DEFAULT AGENT - this app exactly as it behaved before agents
   * existed. It is a real selection, not "nothing chosen yet". */
  agentId: string | null;
  onAgentChange: (id: string | null) => void;
}) {
  // The agent's own setting, which the server ORs with the per-question toggle.
  const forcedOrchestrator = agents.some((a) => a.id === agentId && a.orchestrator);
  const currentModel = models.find((m) => m.id === model) ?? models[0];
  const currentAgent = agents.find((a) => a.id === agentId) ?? null;
  const superOn = orchestrator || forcedOrchestrator;

  const fileRef = useRef<HTMLInputElement>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  // Chrome fires keydown(Enter) with isComposing=true while a Hangul syllable
  // is still being composed, but not every engine does; this ref is the second
  // half of the same guard, set from the composition events themselves.
  const composingRef = useRef(false);

  // Auto-grow, 1 to 8 rows. height:auto first, or scrollHeight only ever
  // reports the height it already has and the box can never shrink again after
  // the user deletes a line.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value, textareaRef]);

  /** Put the caret back in the textarea after the file sheet closes.
   *
   * Retaining focus (see the + button's onMouseDown) is what keeps the
   * keyboard up on the way IN. It does not bring it back on the way OUT: iOS
   * hides the keyboard for the duration of a system sheet regardless of who
   * holds focus. Re-focusing here runs inside the gesture the pick completed,
   * which is the one place the platform allows it.
   *
   * Guarded on the element still being there - a stream can unmount the
   * composer between opening the sheet and closing it. */
  function restoreKeyboard() {
    textareaRef.current?.focus();
  }

  // `cancel` on a file input - dismissing the sheet without choosing anything -
  // is attached natively because React's InputHTMLAttributes does not declare
  // onCancel, so the JSX prop is a type error rather than a listener. Without
  // this, backing out of the picker leaves a focused composer and no keyboard,
  // which is the same complaint as tapping + in the first place.
  useEffect(() => {
    const input = fileRef.current;
    if (!input) return;
    const onCancel = () => textareaRef.current?.focus();
    input.addEventListener("cancel", onCancel);
    return () => input.removeEventListener("cancel", onCancel);
  }, [textareaRef]);

  function take(list: FileList | null) {
    if (!list?.length) return;
    onFiles(Array.from(list));
  }

  /** The ONE owner of focus return, for every sheet in this component.
   *
   * The + button is where focus lands, not the menu row that was pressed: by
   * the time a picker closes, the row that opened it has been unmounted with
   * the menu, and focusing a detached element silently drops the caret on
   * <body>. It is also why this is not inside PopoverSheet - the hand-off below
   * closes the menu WITHOUT calling this, so that the picker it is opening in
   * the same tick keeps the focus its showModal() just took. */
  function closeSheet() {
    setSheet(null);
    plusRef.current?.focus();
  }

  return (
    // §8: one surface-container block at --radius-xl, no border at rest, a 2px
    // primary outline on focus-within, thumbnails in a row above the textarea
    // and inside the same block.
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="rounded-xl bg-surface-container p-2 outline-primary transition-colors duration-150 focus-within:outline focus-within:outline-2"
    >
      {toolCall && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {/* Same row and same shape as an attachment chip: both are "something
              extra this message carries", and the user removes either the same
              way. Its own component would be 30 lines to render two spans. */}
          <span className="inline-flex max-w-full items-center gap-2 rounded-md bg-surface-container-high px-3 py-1.5 text-label">
            <span className="truncate">
              {toolCall.tool.server_name} · {toolCall.tool.name}
            </span>
            {Object.keys(toolCall.arguments).length > 0 && (
              <span className="truncate text-caption text-on-surface-variant">
                {Object.entries(toolCall.arguments)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")}
              </span>
            )}
            <button
              type="button"
              onClick={onToolRemove}
              aria-label={`${toolCall.tool.name} 도구 제거`}
              className="shrink-0 text-on-surface-variant"
            >
              ✕
            </button>
          </span>
        </div>
      )}

      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {attachments.map((a) => (
            <AttachmentChip
              key={a.key}
              filename={a.filename}
              sizeBytes={a.sizeBytes}
              kind={a.kind}
              src={a.previewUrl}
              status={a.status === "uploading" ? "uploading" : "ready"}
              error={a.error}
              onRemove={() => onRemove(a.key)}
            />
          ))}
        </div>
      )}

      {/* The settings that survive the send, said out loud where the user is
          still typing. A menu hides state, and "which model is about to answer"
          and "the super agent is on" are answers wanted BEFORE pressing 전송,
          not read off the trace afterwards.
          Above the textarea rather than beside the + button, and that is a
          width decision: at 390px the old row spent 260 of 358 pixels on
          controls, and moving them here is what buys the textarea back. A chip
          row is full-width and squeezes nothing.
          The model chip appears only when there is more than one model - with
          one, "which model" is not a question anyone is asking. */}
      {(models.length > 1 || currentAgent || superOn) && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {models.length > 1 && currentModel && (
            <StateChip
              icon={CHIP}
              label={currentModel.label}
              onClick={() => setSheet("model")}
              ariaLabel={`답변 모델: ${currentModel.label}`}
            />
          )}
          {currentAgent && (
            <StateChip
              icon={SHIELD}
              label={currentAgent.name}
              active
              onClick={() => setSheet("agent")}
              ariaLabel={`에이전트: ${currentAgent.name}`}
            />
          )}
          {superOn && (
            <StateChip
              icon={SPARK}
              label="슈퍼 에이전트"
              active
              // An agent that carries the orchestrator turns it on server-side,
              // and there is deliberately no way to turn it off for one - that
              // is the agent's configuration, not a per-question default. So
              // the chip is shown ON and DISABLED rather than left clickable: a
              // control that silently ignores a click is a bug report.
              disabled={forcedOrchestrator}
              onClick={() => onOrchestratorChange(false)}
              ariaLabel="슈퍼 에이전트 켜짐"
              title={
                forcedOrchestrator
                  ? "선택한 에이전트가 항상 슈퍼 에이전트로 답변합니다."
                  : "누르면 끕니다."
              }
            />
          )}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* Everything that used to compete along this row lives behind this one
            button now, grouped the way ChatGPT groups its own: what applies to
            THIS message, a divider, then what persists across messages. */}
        <button
          ref={plusRef}
          type="button"
          // Tapping + must not close the keyboard. Reaching for it is the user
          // continuing the same action - they are still composing - and a
          // keyboard that drops costs them the tap to bring it back plus the
          // scroll jump when the viewport resizes twice.
          //
          // A pointer press moves focus by DEFAULT, which blurs the textarea
          // and dismisses the keyboard with it. preventDefault on mousedown
          // suppresses only that focus shift; the click still fires, which is
          // why this is the long-standing pattern for editor toolbar buttons.
          // mousedown rather than pointerdown/touchstart: cancelling a touch
          // sequence that early can also swallow the click on some browsers,
          // and iOS synthesises mousedown before click, so this covers both.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setSheet("menu")}
          aria-haspopup="dialog"
          aria-expanded={sheet === "menu"}
          aria-label="추가"
          className="icon-btn"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          // Cleared after every pick, or choosing the SAME file twice in a row
          // fires no change event and the second attachment never appears.
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
            restoreKeyboard();
          }}
        />

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            // A Korean user pressing Enter to CONFIRM a Hangul candidate must
            // not send the message - that Enter belongs to the IME. Three
            // checks because no single one is portable: `isComposing` is the
            // standard, keyCode 229 is what the engines that predate it report,
            // and the ref covers an engine that fires compositionend late.
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || composingRef.current) {
              return;
            }
            // Shift+Enter is handled by the early return above: it falls
            // through to the textarea's own newline insertion.
            e.preventDefault();
            onSubmit();
          }}
          onPaste={(e) => {
            // ONLY when the clipboard actually carries a file. Pasting text has
            // to keep working, so an empty `files` list is left entirely alone -
            // no preventDefault, no interception.
            if (e.clipboardData.files.length === 0) return;
            e.preventDefault();
            take(e.clipboardData.files);
          }}
          placeholder="질문을 입력하세요"
          // A placeholder is not an accessible name: it is dropped the moment
          // the field has text, and some screen readers never announce it.
          aria-label="질문"
          style={{ maxHeight: MAX_HEIGHT }}
          className="min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-body-lg text-on-surface placeholder:text-on-surface-variant focus:outline-none"
        />

        {/* The two `key`s are load-bearing, and this was measured. Without them
            React reuses ONE <button> DOM node across the branch and only
            rewrites its `type`. A click's activation behaviour reads `type`
            AFTER the listeners have run, so pressing 중지 went: click ->
            onStop -> abort -> the rejection's setState flushes -> the very same
            node is now type="submit" -> the browser submits the form -> the
            question that 중지 had just restored to the composer was sent
            straight back out. Observed as a second `sending` render with no
            second click, and the 중지 button never going away. Distinct keys
            make React replace the node instead, and a detached button has no
            form owner to submit. */}
        {sending ? (
          // The AbortController ChatWindow already held for unmount, surfaced.
          <button
            key="stop"
            type="button"
            onClick={onStop}
            aria-label="답변 생성 중지"
            className="h-10 shrink-0 rounded-full bg-surface-container-high px-5 text-label font-medium text-on-surface transition-colors duration-150 hover:bg-surface-container-highest"
          >
            중지
          </button>
        ) : (
          // Filled when there is something to send, tonal when there is not -
          // the button says whether the composer is ready without a word.
          <button
            key="send"
            type="submit"
            className={`h-10 shrink-0 rounded-full px-5 text-label font-medium transition-colors duration-150 ${
              value.trim()
                ? "bg-primary text-on-primary"
                : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            전송
          </button>
        )}
      </div>

      {/* The + menu. Two groups with a rule between them, which is the whole of
          what the owner asked for: 이 메시지에만 is spent on send, 대화 설정
          survives it. role="group" rather than role="menu"/"menuitem" on
          purpose - menu semantics promise arrow-key roving, and this is a
          <dialog> whose keyboard model is Tab, Enter and Escape. Promising the
          arrows and not implementing them is worse than not promising. */}
      <PopoverSheet
        open={sheet === "menu"}
        onClose={closeSheet}
        anchorRef={plusRef}
        label="추가"
      >
        {/* pb-6 is the phone's home indicator. */}
        <div className="p-2 pb-6 sm:pb-2">
          <div role="group" aria-labelledby="composer-menu-message">
            <p
              id="composer-menu-message"
              className="px-3 py-2 text-label font-medium text-on-surface-variant"
            >
              이 메시지에만
            </p>
            <MenuRow
              icon={CLIP}
              label="파일 첨부"
              onClick={() => {
                // Close first, then open the OS file chooser: two overlays over
                // the composer at once is exactly what this menu replaced.
                closeSheet();
                fileRef.current?.click();
              }}
            />
            {tools.length > 0 && (
              <MenuRow icon={WRENCH} label="도구 사용" onClick={() => setSheet("tool")} />
            )}
          </div>

          <div
            role="group"
            aria-labelledby="composer-menu-settings"
            className="mt-1 border-t border-outline-variant pt-1"
          >
            <p
              id="composer-menu-settings"
              className="px-3 py-2 text-label font-medium text-on-surface-variant"
            >
              대화 설정
            </p>
            {models.length > 1 && currentModel && (
              <MenuRow
                icon={CHIP}
                label="답변 모델"
                value={currentModel.label}
                onClick={() => setSheet("model")}
              />
            )}
            {agents.length > 0 && (
              <MenuRow
                icon={SHIELD}
                label="에이전트"
                value={currentAgent?.name ?? DEFAULT_AGENT_LABEL}
                onClick={() => setSheet("agent")}
              />
            )}
            <MenuRow
              icon={SPARK}
              label="슈퍼 에이전트"
              value={superOn ? "켬" : "끔"}
              pressed={superOn}
              // Stays open: the row IS the state readout, so the user has to be
              // able to see it flip. Closing on toggle would hide the only
              // feedback the action has.
              onClick={() => onOrchestratorChange(!orchestrator)}
              disabled={forcedOrchestrator}
              title={
                forcedOrchestrator
                  ? "선택한 에이전트가 항상 슈퍼 에이전트로 답변합니다."
                  : "질문에 맞춰 여러 단계의 검색과 도구 호출을 계획해서 실행합니다."
              }
            />
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
      />

      <AgentPicker
        agents={agents}
        value={agentId}
        onChange={onAgentChange}
        open={sheet === "agent"}
        onClose={closeSheet}
        anchorRef={plusRef}
      />

      <ToolPicker tools={tools} onSelect={onToolSelect} open={sheet === "tool"} onClose={closeSheet} />
    </form>
  );
}
