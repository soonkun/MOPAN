/** 인터넷 조사가 무엇을 검색하고 읽었는가.
 *
 * 조사는 30~70초 걸린다. 그동안 "도구 호출 중…" 한 줄뿐이면 일하는 것과 멈춘 것이 구별되지 않는다(소유자 지적) -
 * 그래서 도는 동안에는 펼친 채로 단계가 쌓이는 것을 보이고, 답이 오면 접힌 채 답변 위에 남긴다. 꺽쇠로 여닫는다.
 *
 * 네이티브 <details>: 여닫기·키보드·스크린리더 안내를 브라우저가 한다. `open`은 처음 값일 뿐이다 - 같은 값으로
 * 다시 그려질 때 React는 속성을 건드리지 않으므로, 도는 중에 사용자가 접어 두면 다음 단계가 와도 접힌 채다. */
export default function ResearchSteps({ steps, live = false }: { steps: string[]; live?: boolean }) {
  if (steps.length === 0) return null;
  return (
    <details open={live} className="group mb-3 rounded-md bg-surface-container-low text-caption text-on-surface-variant">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md px-3 py-2 hover:bg-surface-container [&::-webkit-details-marker]:hidden">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4 shrink-0 transition-transform duration-150 group-open:rotate-90"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M9 6l6 6-6 6" />
        </svg>
        <span>
          {live ? "인터넷 조사 중" : "조사 과정"} · {steps.length}단계
        </span>
      </summary>
      <ol className="space-y-1 px-3 pb-3 pl-9">
        {steps.map((step, index) => (
          // index가 키다: 단계는 뒤에 붙기만 하고 순서가 바뀌지 않는다.
          <li key={index} className={live && index === steps.length - 1 ? "text-on-surface" : undefined}>
            {step}
          </li>
        ))}
      </ol>
    </details>
  );
}
