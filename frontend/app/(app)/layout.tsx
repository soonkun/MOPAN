import Sidebar from "@/components/layout/Sidebar";

// .app-shell is 100dvh with a 100vh fallback, not min-h-screen. The sidebar is a full-height column with a
// scrolling history region and a footer pinned under it, and `main`'s
// overflow-y-auto only scrolls when something bounds its height. Under
// min-h-screen the container grows with the page instead, so main never
// overflows and never scrolls, the sidebar stretches to the full document
// height, and on any page taller than the viewport - the documents table -
// the logout button ends up below the fold.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell flex">
      <Sidebar />
      {/* pt-12가 예약하던 상단 띠는 소유자 지적으로 없앴다("햄버거 마크만 떠
          있으면 되는 거 아닌가?" - 모바일 화면 3rem이 흰 띠로 죽어 있었다).
          햄버거는 반투명+블러의 떠 있는 버튼이고, 내용이 그 밑으로 흐른다 -
          겹치는 것은 좌상단 모서리의 몇 글자뿐이고 스크롤하면 지나간다.
          overflow-y-auto is load-bearing twice over. It bounds the scroll, and
          it makes the computed overflow-x `auto` too, which zeroes this flex
          item's automatic minimum size - that is what stops Task 23's wide
          table from pushing the 280px sidebar off-screen.
          id="app-main" is the handle the Sidebar's drawer uses to set `inert`
          on this element while it is open. It is not set as a JSX prop here
          because that state lives in the Sidebar client component and this
          layout is a server component; the id keeps the coupling greppable
          instead of leaving a bare document.querySelector("main"). */}
      {/* `relative`가 이중 스크롤바의 근본 수정이다. sr-only(position:absolute)
          요소 - DataTable의 caption, 폼의 숨김 label - 는 positioned 조상이
          없으면 컨테이닝 블록이 뷰포트(html)가 되어 main의 overflow 클리핑을
          탈출하고, 자기 정적 위치(스크롤 내용 한가운데, 실측 /prompts 1448px ·
          /settings 1737px)까지 문서 자체를 늘린다. 그러면 창 스크롤바가 main의
          스크롤바와 이중으로 뜨고 셸 아래가 흰 띠가 된다. main이 positioned가
          되면 그 요소들은 main 안에 앵커되어 문서를 늘릴 수 없다. */}
      <main id="app-main" className="relative flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
