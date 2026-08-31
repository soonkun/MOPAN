// Five admin tables each wrote the same two lines - a div with
// `overflow-x-auto rounded-sm` around a `w-full text-left text-body` table -
// and the sixth (the MCP tools table nested in an expanded row) forgot the
// wrapper. This is that pair, once.
//
// The wrapper is what makes a table too wide for its column scroll INSIDE its
// own box instead of widening the page; `main` never gains a horizontal
// scrollbar and neither does the document. Measured at 390/1280/1920/2560 on
// every admin route: document.scrollWidth - clientWidth is 0.
//
// `caption` is rendered sr-only. It is the table's accessible name, which a
// screen reader announces before the first cell; a visible <h2> above the
// table does not fill that role.
export default function DataTable({
  caption,
  children,
}: {
  caption?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-sm">
      {/* break-keep is word-break: keep-all. Korean has no space between the
          syllables of a word, so the default `normal` lets a line break after
          any one of them - which means a column's min-content width is ONE
          character. Auto table layout then hands 상태 twelve pixels and renders
          비활성 as 비/활/성 stacked vertically, measured on 사용자 관리 at 390px.
          keep-all makes the word the unit, the column asks for its real width,
          and the wrapper above scrolls instead. Cells that must break a long
          unbroken token - an email, a filename - already override this locally
          with break-all or truncate. */}
      <table className="w-full break-keep text-left text-body">
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}
