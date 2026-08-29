import Sidebar from "@/components/layout/Sidebar";

// h-screen, not min-h-screen. The sidebar is a full-height column with a
// scrolling history region and a footer pinned under it, and `main`'s
// overflow-y-auto only scrolls when something bounds its height. Under
// min-h-screen the container grows with the page instead, so main never
// overflows and never scrolls, the sidebar stretches to the full document
// height, and on any page taller than the viewport - the documents table -
// the logout button ends up below the fold.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen">
      <Sidebar />
      {/* pt-12 md:pt-0 reserves the strip the fixed hamburger occupies. It is
          part of the layout, not of each page: without it every page's first
          element sits under the toggle at (8,8)-(39,38).
          overflow-y-auto is load-bearing twice over. It bounds the scroll, and
          it makes the computed overflow-x `auto` too, which zeroes this flex
          item's automatic minimum size - that is what stops Task 23's wide
          table from pushing the 256px sidebar off-screen. */}
      <main className="flex-1 overflow-y-auto pt-12 md:pt-0">{children}</main>
    </div>
  );
}
