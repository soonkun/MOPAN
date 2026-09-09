import type { CallableTool, McpToolOption, WorkflowOption } from "@/lib/types";

/** What `@` in the composer is made of, with no JSX in it.
 *
 * Here rather than in MentionMenu.tsx for one reason: this is the part with
 * rules in it - where a token starts, which rows exist, what a query matches -
 * and `node --test --experimental-strip-types` can import a .ts file and cannot
 * import a component. The menu next door is markup; this is the logic, and
 * lib/mention.test.ts is what holds it to it.
 */

/** One row of the menu. The three kinds are flattened into one list
 * deliberately - a menu grouped by transport would be asking the user to know
 * what MCP is. */
export type MentionEntry = {
  /** Unique per row, for React and for the `aria-activedescendant` id. */
  key: string;
  kind: "rag" | "mcp" | "workflow";
  /** What the filter matches and what the row shows. */
  name: string;
  description: string | null;
  riskLevel: string;
  /** The namespace this row names, verbatim, exactly as a graph node writes it.
   * Shown on the row so the thing a person picks in chat and the thing they drop
   * on the canvas are visibly the same thing. */
  ref: string;
  /** RAG rows only: which collection this row would scope the search to. */
  collectionId?: string;
  /** RAG 폴더 행: 하위 폴더 포함 범위. collectionId와 함께 온다. */
  folderId?: string;
  folderLabel?: string;
  /** Workflow rows only: the id POST /api/chat takes. */
  workflowId?: string;
  /** MCP rows only: 서버 이름. 행이 도구가 아니라 서버 단위인 이유는 + 메뉴와
   * 같다 - "서버를 연결하면 그 안의 기능은 다 쓰는 것"(소유자 원칙). 고르면
   * 이번 질문의 자동 사용 후보에 이 서버가 토글과 무관하게 들어간다. */
  serverName?: string;
};

/** The `@…` token the caret is sitting in, or null.
 *
 * The `@` has to follow the start of the text or whitespace, so an email address
 * typed into a question never opens the menu. The token ends AT THE CARET, so
 * moving back into a sentence that already contains an `@` does not reopen it,
 * and it cannot contain whitespace or a second `@`.
 *
 * `start` is where the `@` itself is, which is what a pick removes from.
 */
export function mentionAt(
  value: string,
  caret: number | null,
): { start: number; query: string } | null {
  if (caret === null) return null;
  const match = /(^|\s)@([^\s@]*)$/.exec(value.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2] };
}

/** `GET /api/tools` into rows this composer can actually act on.
 *
 * The `rag` entry is skipped: retrieval runs on every question, so it is not a
 * thing to "call". Only MCP servers and workflows are offered.
 *
 * A workflow or an MCP row whose id this client does not hold is DROPPED rather
 * than shown: `/api/tools` and the two id-carrying lists are fetched separately,
 * so a row can exist in one and not yet the other, and a row that cannot be
 * acted on is worse than a row that is not there. Those ids are what
 * POST /api/chat takes.
 */
export function mentionEntries(
  callables: CallableTool[],
  workflows: WorkflowOption[],
  tools: McpToolOption[],
): MentionEntry[] {
  const entries: MentionEntry[] = [];
  for (const callable of callables) {
    if (callable.kind === "rag") {
      // 문서 검색은 @로 부르는 대상이 아니다 - 모든 질문이 기본으로 타는 전역 단계다(소유자 결정
      // 2026-09-09). 분류·폴더 범위는 문서 화면과 + 메뉴가 맡는다. 여기서는 도구(MCP 서버)와
      // 워크플로우만 보인다.
      continue;
    } else if (callable.kind === "mcp") {
      // 서버 단위 한 줄로 접는다. 도구별 행("생활정보/current_weather" 셋)은
      // 사용자에게 서버 내부 구조를 외우라는 요구였고, + 메뉴에서 이미 기각된
      // 모양이다. 같은 서버의 두 번째 도구부터는 행을 만들지 않는다.
      const server = callable.ref.replace(/^mcp:/, "").split("/")[0];
      if (entries.some((e) => e.kind === "mcp" && e.serverName === server)) continue;
      if (!tools.some((t) => t.server_name === server)) continue;
      entries.push({
        key: `mcp:${server}`,
        kind: "mcp",
        name: server,
        description: "이번 질문에 이 서버의 도구를 적극 사용합니다.",
        riskLevel: callable.risk_level,
        ref: `mcp:${server}`,
        serverName: server,
      });
    } else {
      const workflow = workflows.find((w) => `workflow:${w.name}` === callable.ref);
      if (!workflow) continue;
      entries.push({
        key: callable.ref,
        kind: "workflow",
        name: callable.name,
        description: callable.description,
        riskLevel: callable.risk_level,
        ref: callable.ref,
        workflowId: workflow.id,
      });
    }
  }
  return entries;
}

/** Case-insensitive substring on the NAME, which is what the user typed after
 * the `@`. Not on the description: a two-character query would then match half
 * the list through prose nobody was reading. */
export function filterEntries(entries: MentionEntry[], query: string): MentionEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.filter((entry) => entry.name.toLowerCase().includes(needle));
}
