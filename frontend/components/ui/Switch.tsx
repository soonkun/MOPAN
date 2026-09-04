/** 토글 스위치의 생김새만 - 상태와 접근성은 부모 버튼(aria-pressed)이 진다.
 *
 * 자리마다 스위치를 다시 그리면 트랙 폭·썸 위치가 미묘하게 어긋나므로 생김새를
 * 한 곳에 둔다. + 메뉴의 MCP 서버 행과 계정 창의 테마 스위치가 같은 것을 쓴다. */
export default function Switch({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-150 ${
        on ? "bg-primary" : "bg-surface-container-highest"
      }`}
    >
      <span
        className={`absolute h-3.5 w-3.5 rounded-full shadow-sm transition-transform duration-150 ${
          on ? "translate-x-[1.125rem] bg-on-primary" : "translate-x-[0.1875rem] bg-surface"
        }`}
      />
    </span>
  );
}
