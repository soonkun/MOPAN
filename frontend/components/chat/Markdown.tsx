"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import CitationBadge from "@/components/chat/CitationBadge";
import type { Citation } from "@/lib/types";

// Slice 1 shipped no markdown renderer at all, and a security reviewer praised
// that the XSS surface had been "designed away rather than configured away".
// This file puts it back, so the configuration IS the security argument:
//
//   - NO `rehype-raw` and NO dangerouslySetInnerHTML anywhere. Without
//     rehype-raw, react-markdown rewrites every `raw` hast node into a TEXT
//     node before rendering (react-markdown/lib/index.js:355), so
//     `<img src=x onerror=alert(1)>` in an answer is characters on screen, not
//     an element in the DOM.
//   - Link hrefs go through react-markdown's `defaultUrlTransform`, whose
//     protocol allowlist is /^(https?|ircs?|mailto|xmpp)$/i - a `javascript:`
//     href is emptied before it ever reaches the anchor.
//   - The `[n]` -> badge pass runs on the hast TEXT nodes below, never as a
//     regex over rendered HTML, and it resolves each marker against the
//     message's own citations array before emitting anything.
const MARKER = /\[(\d{1,2})\]/g;

/** The subset of hast this file touches. Declared locally rather than imported
 * from @types/hast, which is only here as react-markdown's transitive dep. */
type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/** The security-load-bearing half of MessageBubble's old renderContent, moved
 * onto the markdown tree and otherwise unchanged in behaviour:
 *
 *   - a marker whose number is not in `byIndex` is skipped WITHOUT advancing the
 *     cursor, so a forged "[9]" in an answer survives as literal text rather
 *     than becoming a badge pointing at nothing;
 *   - the badge is built from the resolved Citation object, so there is no path
 *     from attacker-chosen text to a link target.
 *
 * `inCode` is the part markdown adds: a text node under <code> or <pre> is left
 * alone, so `[1]` inside inline code or a fenced block stays visible source. */
function splitMarkers(value: string, byIndex: Map<number, Citation>): HastNode[] {
  const nodes: HastNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  MARKER.lastIndex = 0;

  while ((match = MARKER.exec(value)) !== null) {
    if (!byIndex.has(Number(match[1]))) continue;
    if (match.index > cursor) nodes.push({ type: "text", value: value.slice(cursor, match.index) });
    nodes.push({
      type: "element",
      tagName: "citation",
      properties: { dataIndex: match[1] },
      children: [],
    });
    cursor = match.index + match[0].length;
  }
  if (nodes.length === 0) return [{ type: "text", value }];
  if (cursor < value.length) nodes.push({ type: "text", value: value.slice(cursor) });
  return nodes;
}

function citationMarkers(citations: Citation[]) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));

  function walk(node: HastNode, inCode: boolean): void {
    if (!node.children) return;
    const next: HastNode[] = [];
    for (const child of node.children) {
      if (child.type === "element") {
        // `pre` as well as `code`: a fenced block is <pre><code>, and a `pre`
        // with no `code` inside it is still preformatted source.
        walk(child, inCode || child.tagName === "code" || child.tagName === "pre");
        next.push(child);
      } else if (child.type === "text" && !inCode && typeof child.value === "string") {
        next.push(...splitMarkers(child.value, byIndex));
      } else {
        // `raw` nodes land here. They are rewritten to text by react-markdown
        // AFTER every rehype plugin has run, so a `[1]` inside a would-be HTML
        // tag is never linkified - the safe direction.
        next.push(child);
      }
    }
    node.children = next;
  }

  return () => (tree: HastNode) => walk(tree, false);
}

export default function Markdown({
  content,
  citations,
}: {
  content: string;
  citations: Citation[];
}) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  // Cast: `citation` is not an HTML tag name, and hast-util-to-jsx-runtime's
  // Components type is keyed on JSX.IntrinsicElements. The tag is produced only
  // by the plugin above, so nothing else can reach this component.
  const components = {
    citation({ node }: { node?: HastNode }) {
      const citation = byIndex.get(Number((node?.properties as { dataIndex?: string })?.dataIndex));
      return citation ? <CitationBadge citation={citation} /> : null;
    },
    // rel on every link: these hrefs come out of a model answer, so an answer
    // must not be able to hand a target window a reference back to this one.
    a({ href, children }: { href?: string; children?: React.ReactNode }) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow">
          {children}
        </a>
      );
    },
    // The one wrapper markdown cannot express: a GFM table wider than the 768px
    // reading column has to scroll inside itself, or it scrolls the page (§9.8).
    table({ children }: { children?: React.ReactNode }) {
      return (
        <div className="my-3 overflow-x-auto">
          <table>{children}</table>
        </div>
      );
    },
  } as Components;

  return (
    <div className="markdown text-body-lg text-on-surface">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[citationMarkers(citations)]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
