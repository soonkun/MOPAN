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
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
