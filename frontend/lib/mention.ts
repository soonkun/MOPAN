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
  /** Workflow rows only: the id POST /api/chat takes. */
  workflowId?: string;
  /** MCP rows only: the id POST /api/chat takes. */
  toolId?: string;
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
 * The `rag` entry arrives as ONE row carrying the deployment's collections, and
 * is expanded here into one row per collection: "부를 수 있는 것" for retrieval is
 * a corpus, not the search function. A deployment with no collections still gets
 * the single unscoped row, because searching everything is a real thing to ask
 * for.
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
      if (callable.collections.length === 0) {
        entries.push({
          key: "rag",
          kind: "rag",
          name: callable.name,
          description: callable.description,
          riskLevel: callable.risk_level,
          ref: callable.ref,
        });
        continue;
      }
      for (const collection of callable.collections) {
        entries.push({
          key: `rag:${collection.id}`,
          kind: "rag",
          name: collection.name,
          description: "이 분류 안에서만 근거를 찾습니다.",
          riskLevel: callable.risk_level,
          ref: callable.ref,
          collectionId: collection.id,
        });
      }
    } else if (callable.kind === "mcp") {
      const tool = tools.find((t) => `mcp:${t.server_name}/${t.name}` === callable.ref);
      if (!tool) continue;
      entries.push({
        key: callable.ref,
        kind: "mcp",
        name: callable.name,
        description: callable.description,
        riskLevel: callable.risk_level,
        ref: callable.ref,
        toolId: tool.id,
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
