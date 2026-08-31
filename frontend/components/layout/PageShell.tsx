// The one container every admin screen sits in. It exists because there were
// eight of them: `mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6` was copied
// into 분류/사용자/프롬프트/MCP/고급 설정/문서/문서 상세, and 워크플로우 had
// drifted to max-w-7xl with nothing recording why. Eight containers are eight
// chances to diverge again, which is how a wide-desktop bug ended up on every
// screen at once instead of on one.
//
// Widths, measured in headless Chrome against the running app:
//   1024px column in a 1640px `main` at 1920 left 308px of dead gutter each
//   side (53% fill); at 2560 it was 40%. max-w-7xl takes that to 1280 (the
//   value 워크플로우 already used, so its graph editor keeps the room it had),
//   and 2xl:max-w-page takes it to 1600 at >=1536 where there is width to
//   spend. Below 1280 nothing changes: max-w never binds there and the 390px
//   layout is byte-for-byte what it was.
//
// min-w-0 is deliberate. This is the child of `main`, whose overflow-y-auto
// already zeroes its own automatic minimum size (see app/(app)/layout.tsx);
// min-w-0 does the same one level down so a wide table inside a page scrolls
// in its own box rather than widening the column that holds it.
export default function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full min-w-0 max-w-7xl space-y-6 px-4 py-6 sm:px-6 2xl:max-w-page 2xl:px-8">
      {children}
    </div>
  );
}
