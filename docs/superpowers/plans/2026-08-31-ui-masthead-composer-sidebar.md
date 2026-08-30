# MOPAN — The masthead, the + menu and the sidebar icons — Implementation Plan

> **Scope:** three UI changes the product owner asked for in one sitting, and nothing else. No backend file is touched, no endpoint changes, no migration. It amends no other plan; it supersedes the composer and empty-state blocks in `docs/superpowers/plans/2026-08-30-slice-4-agents.md` and `docs/superpowers/plans/2026-08-30-slice-2-mcp.md` only in the sense every later plan does — those blocks captured those files as of their own tasks.

**What the owner said, verbatim:**

```text
대문에 표어 같은거 수정해 MOPAN의 뜻도 좀 쓰고 그 아래 예시 프롬프트는 지워. 의미없잖아.
프롬프트 창 안에 기능들은 + 안으로 넣어. 그리고 chatgpt처럼 구분을 하게.
사이드바의 문구 왼쪽에 해당 기능을 나타내는 기호 그림을 넣어줘.
```

**What ships:**
- The new-conversation screen is a masthead: the mascot, 모판 in the brand gradient, the English reading with its five initials at full contrast, a short rule, and two lines saying what the product is. The four suggestion chips and the `SUGGESTIONS` constant are gone.
- The composer's row is `+`, the textarea and 전송. Everything that used to compete along it — attach, model, agent, tool, super agent — is inside the `+` menu, in two groups separated by a rule: 이 메시지에만 and 대화 설정. The two settings a user has to read *before* sending are chips above the textarea.
- `PopoverSheet.tsx` is the anchored-menu/bottom-sheet shape `ModelPicker` and `AgentPicker` had each written; the `+` menu would have been the third copy.
- Every sidebar nav item and 로그아웃 carries an inline SVG glyph on `currentColor`. No icon dependency. Conversation history rows deliberately get none.

## Decisions

**One task, not three.** `scripts/check_plan_parity.py` rule 2 treats a task with no `Write`/`Create` step as never having run and SKIPS every block under it. Split into a masthead task, a composer task and a sidebar task, only the composer half would ever be checked — the other two would be silently unverified, which is the exact failure that script exists to prevent. This is one commit anyway.

**The gradient moves off the sentence and onto the name.** §2 allows the gradient on the wordmark, the assistant sparkle and the streaming indicator. It was on a whole line of body copy (`등록된 문서에 대해 무엇이든 물어보세요.`), which is a surface treatment wearing a wordmark's clothes. 모판 is two characters, so it also cannot wrap — the old 36px sentence wrapped to two lines at 390px and ate the fold, which is why it had to step down to 24px below `md`. The name does not, and the masthead is `text-display` at every width.

**The suggestion chips are deleted, not reworded.** They guessed at questions nobody had asked, in a product whose whole claim is that the corpus is the user's. §8 of the design language calls for "3-4 suggestion chips"; the owner's instruction outranks it, and this paragraph is the record that the departure was deliberate rather than forgotten.

**Which controls stay visible: the model and the super agent, as chips, and only when they are a question.** A menu hides state, and "which model is about to answer" and "the super agent is on" are asked before pressing 전송, not read off the trace afterwards. The trailing value on a menu row (`답변 모델 · GPT-4o mini`) answers it only once the menu is open, so it is not enough on its own. The chips sit ABOVE the textarea rather than beside the `+`, and that is a width decision: at 390px the old row spent 260 of 358 pixels on controls, and a full-width chip row squeezes nothing. The model chip appears only when the deployment offers more than one model — with one, nobody is asking. The agent chip appears only for a non-default agent. Pressing a chip opens the sheet that changes it, so the menu is not the only way in.

**The menu closes before a picker opens.** Two stacked sheets over a composer is two Escapes to get out and a scrim over a scrim. The hand-off is one state change in one owner — `sheet: null | "menu" | "model" | "agent" | "tool"` — which is also what makes "exactly one overlay at a time" structural rather than a rule someone has to remember.

**Focus has exactly one owner, `Composer.closeSheet`.** `dialog.close()` fires its `close` event on a queued task, not synchronously, so a picker that returned focus to the anchor on its own would run AFTER the next sheet's `showModal()` and pull the caret straight back off it. `closingRef` in `PopoverSheet.tsx` and `ToolPicker.tsx` is the other half: a close the owner caused by flipping `open` must not report itself back and cancel the sheet being opened. Measured: 답변 모델, 에이전트 and 도구 사용 each open with focus inside their own dialog, and Escape from each returns focus to `추가`.

**No `role="menu"`.** Menu semantics promise arrow-key roving. This is a `<dialog>` whose keyboard model is Tab, Enter and Escape, which is what the two pickers already were. Promising the arrows and not implementing them is worse than not promising. The two groups are `role="group"` with `aria-labelledby` pointing at their headings, which is the grouping the owner asked to be visible, said out loud for AT.

**The + button keeps its `onMouseDown` preventDefault, and the file input keeps both re-focus handlers.** Opening a modal menu necessarily moves focus off the textarea — that is what a menu is. What was measured and is preserved is the rest of the path: the press itself does not blur, 파일 첨부 still reaches the OS file chooser, and `change`/`cancel` on the input still put the caret back in the textarea. The three-way IME guard on the textarea is untouched.

**Conversation history rows get no icon.** They are titles, not functions. One identical glyph repeated down twenty rows distinguishes nothing, and it would spend 32px of a 224px row on decoration in the one place where the text already truncates.

**Icons are inline SVG, `currentColor`, `aria-hidden`.** Exactly what `ThemeToggle.tsx` already does. A dozen paths do not justify a package. The agent glyph is the same shield-check the composer's agent control draws, because they mean the same thing and a user should not have to learn two.

## Global Constraints

- Tokens only, per `docs/superpowers/specs/2026-08-30-design-language.md`. A raw hex or a Tailwind default-palette class in a component is a defect, and `tailwind.config.ts` REPLACES those scales so they emit nothing.
- No `box-shadow` outside menus and dialogs.
- Korean UI text, correct spacing and orthography.
- No horizontal page scroll at 390px, and no clipped Korean.
- No new dependency.

---

### Task 1: The masthead, the + menu and the sidebar icons

**Files:**
- Write: `frontend/components/chat/PopoverSheet.tsx`
- Modify: `frontend/components/chat/ModelPicker.tsx`, `frontend/components/chat/AgentPicker.tsx`, `frontend/components/chat/ToolPicker.tsx`, `frontend/components/chat/Composer.tsx`, `frontend/components/chat/ChatWindow.tsx`, `frontend/components/layout/Sidebar.tsx`

**Interfaces:**
- Produces: `PopoverSheet`, and a `ModelPicker` / `AgentPicker` / `ToolPicker` that are controlled lists with no trigger of their own.
- Consumed by: `Composer.tsx`, which owns the single `sheet` state and every entry point into all three.

- [ ] **Step 1: Write `frontend/components/chat/PopoverSheet.tsx`** — the anchored menu that is a bottom sheet on a phone, extracted the third time it was written

```tsx
"use client";

import { useEffect, useRef } from "react";

/** The anchored menu that is a bottom sheet on a phone.
 *
 * ModelPicker and AgentPicker each wrote this, and the composer's + menu would
 * have been the third copy - which is the moment those files' own comment named
 * for extracting it ("If a third picker appears, that is the moment to extract
 * one"). Everything that was identical lives here; what differs is the list,
 * which is the children.
 *
 * ONE native <dialog>, two placements. showModal() is what buys the focus trap,
 * Escape, an inert background and top-layer stacking - the same reasoning
 * ConfirmDialog.tsx gives - and none of it has to be written here. The composer
 * is pinned to the bottom of the viewport, so a menu that opened DOWNWARD from
 * its anchor would open off-screen; on desktop it is anchored above, and on a
 * phone it is a bottom sheet, which is where the thumb is.
 *
 * CONTROLLED, unlike the two pickers it replaces. The composer's + menu hands
 * off to a picker - close the menu, open the model list - and that hand-off is
 * one state change in one owner rather than two components reaching into each
 * other. It is also why `closingRef` exists: a close driven by the `open` prop
 * going false is the owner already knowing, and firing `onClose` back at it
 * would immediately cancel the sheet it was opening instead. Only a dismissal
 * the USER caused - Escape, the backdrop - is reported.
 *
 * Focus return is deliberately NOT here. There is exactly one owner of it, the
 * composer's `closeSheet`, because the hand-off case has to skip it: a sheet
 * that focused the anchor on its way out would pull focus straight back off the
 * picker it just opened. Two places doing it is how that bug returns.
 */

// Must equal `sm:w-72` below; the anchoring maths needs the number.
const MENU_WIDTH = 288;
const EDGE = 8;

export default function PopoverSheet({
  open,
  onClose,
  anchorRef,
  label,
  children,
}: {
  open: boolean;
  /** A dismissal the user caused. A close the owner asked for by flipping
   * `open` does not call this - see closingRef. */
  onClose: () => void;
  /** What the sheet hangs off on desktop. */
  anchorRef: React.RefObject<HTMLElement | null>;
  label: string;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closingRef = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) {
      if (dialog.open) {
        closingRef.current = true;
        dialog.close();
      }
      return;
    }
    if (dialog.open) return;
    const anchor = anchorRef.current;
    if (anchor && window.matchMedia("(min-width: 640px)").matches) {
      const rect = anchor.getBoundingClientRect();
      // Left-aligned to the anchor and clamped to the viewport, so the menu
      // cannot hang off either edge on a narrow desktop window. Left, not
      // right: the anchor is the composer's + button, which is the LEFTMOST
      // control in the row, and a right-aligned box would open across it.
      const left = Math.min(
        Math.max(EDGE, rect.left),
        window.innerWidth - MENU_WIDTH - EDGE,
      );
      dialog.style.left = `${left}px`;
      dialog.style.right = "auto";
      dialog.style.top = "auto";
      dialog.style.bottom = `${window.innerHeight - rect.top + EDGE}px`;
    } else {
      // Back to the class-driven bottom sheet. Without this a resize from
      // desktop to phone width would keep the anchored coordinates.
      dialog.style.cssText = "";
    }
    dialog.showModal();
  }, [open, anchorRef]);

  return (
    <dialog
      ref={dialogRef}
      aria-label={label}
      onClose={() => {
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        onClose();
      }}
      // A transparent desktop backdrop still fills the viewport, so this is
      // what closes the menu on an outside click. `=== dialog` because every
      // click inside a child bubbles to the dialog too.
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current.close();
      }}
      // Mobile: a bottom sheet pinned to the bottom edge, full width, rounded
      // on top only. Desktop: 288px anchored above the trigger by the effect,
      // and no scrim - it is a menu, not a modal, whatever showModal() calls it.
      className="fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-72 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
    >
      {children}
    </dialog>
  );
}
```

- [ ] **Step 2: Modify `frontend/components/chat/ModelPicker.tsx`** — the trigger goes, the list stays

```tsx
export default function ModelPicker({
  models,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
}: {
  models: AnswerModel[];
  value: string;
  onChange: (id: string) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
```

- [ ] **Step 3: Modify `frontend/components/chat/AgentPicker.tsx`** — same, and the `agents.length === 0` guard moves to the menu row that would have opened it

```tsx
export default function AgentPicker({
  agents,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
}: {
  agents: AgentOption[];
  value: string | null;
  onChange: (id: string | null) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const rows: (AgentOption | null)[] = [null, ...agents];
```

- [ ] **Step 4: Modify `frontend/components/chat/ToolPicker.tsx`** — controlled, but NOT through PopoverSheet: it is centred, it has a form in it, and it keeps the scrim on desktop

```tsx
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || tools.length === 0) return;
    if (!open) {
      if (dialog.open) {
        closingRef.current = true;
        dialog.close();
      }
      return;
    }
    if (dialog.open) return;
    setSelectedId((current) => current || tools[0].id);
    dialog.showModal();
  }, [open, tools]);
```

- [ ] **Step 5: Modify `frontend/components/chat/Composer.tsx`** — one `sheet` value, one owner of focus return

```tsx
/** Exactly one overlay is open at a time, and which one is a single value.
 *
 * Two states would let the + menu and a picker be open together, which is the
 * bug the hand-off was written to avoid: two stacked sheets over a composer,
 * two Escapes to get out, and a scrim over a scrim. */
type Sheet = null | "menu" | "model" | "agent" | "tool";
```

- [ ] **Step 6: Modify `frontend/components/chat/Composer.tsx`** — the focus rule, written down where it is enforced

```tsx
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
```

- [ ] **Step 7: Modify `frontend/components/chat/Composer.tsx`** — the two groups and the rule between them

```tsx
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
```

- [ ] **Step 8: Modify `frontend/components/chat/Composer.tsx`** — the state that must be readable without opening the menu

```tsx
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
```

- [ ] **Step 9: Modify `frontend/components/chat/ChatWindow.tsx`** — the masthead, and the gradient back on the name

```tsx
              <h1 className="text-display font-medium">
                <span className="text-gradient-brand">모판</span>
                {/* The Latin half is deliberately outside the gradient: across
                    ASCII glyphs at this size it reads as a rendering fault, and
                    this parenthetical is here to be legible, not decorative. */}
                <span className="text-on-surface">(MOPAN)</span>
              </h1>
```

- [ ] **Step 10: Modify `frontend/components/chat/ChatWindow.tsx`** — what the name means, in Korean and in English, and nothing else

```tsx
              <p className="max-w-[32rem] break-keep text-body-lg text-on-surface">
                한 판에서 길러 어느 논에나 옮겨 심습니다.
              </p>
```

- [ ] **Step 11: Modify `frontend/components/layout/Sidebar.tsx`** — a glyph per function, keyed by route

```tsx
function NavIcon({ name }: { name: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {NAV_ICON[name]}
    </svg>
  );
}
```

- [ ] **Step 12: Modify `frontend/components/layout/Sidebar.tsx`** — the row, and why 280px still holds

```tsx
        // gap-3 (12px) beside a 20px icon inside px-4 leaves 224 - 32 = 192px
        // for the label in a 280px sidebar. The longest is MCP 서버 관리 at
        // ~84px, so nothing here comes near truncating; no `truncate` class,
        // because a label that cannot overflow does not need one and one that
        // could should be shortened instead.
        className={`flex items-center gap-3 rounded-full px-4 py-2 text-label transition-colors duration-150 ${
          active
            ? "bg-primary-container font-medium text-on-primary-container"
            : "text-on-surface-variant hover:bg-surface-container-high"
        }`}
      >
        <NavIcon name={link.href} />
        {link.label}
      </Link>
```

- [ ] **Step 13: Modify `scripts/check_all_plans.py`** — this plan joins the history

```python
    "docs/superpowers/plans/2026-08-30-prompt-budget.md",
    "docs/superpowers/plans/2026-08-31-ui-masthead-composer-sidebar.md",
]
```

- [ ] **Step 14: Verify** — typecheck, build, tests, parity, the token greps, and drive it

```text
cd frontend && npx tsc --noEmit          -> 0 errors
cd frontend && npm run build             -> 13/13 pages
cd frontend && npm test                  -> 6 pass, 0 fail
python scripts/check_all_plans.py        -> exit 0, DRIFT (0)
raw hex outside globals.css/tailwind.config.ts   -> 0
Tailwind default-palette classes in components   -> 0
shadow- classes that are not shadow-menu/dialog  -> 0
```

## Screenshots

`docs/screenshots/` — the empty state light and dark at 1280x900 and 390x844, the `+` menu open on both, the model picker, the state chips, the sidebar, and the mobile drawer. They are checked in as documentation of this commit, PNG, each well under 400KB.
