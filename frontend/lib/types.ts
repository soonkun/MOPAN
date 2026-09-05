export interface User {
  id: string;
  email: string;
  role: "admin" | "user";
  /** 호칭. 새 대화 첫 화면과 잡담 응답이 "OO님"이라고 부르는 값. null이면
   * 부르지 않는다 - 이메일 앞부분으로 어림해 부르는 것은 추측이다. */
  nickname: string | null;
}

/** GET /api/branding - 이 배포가 화면에서 자기를 뭐라고 부르는가. null은
 * "코드의 기본값을 쓴다"는 뜻이라, 프런트가 기본 문구의 원본이다. */
export interface Branding {
  app_title: string | null;
  tagline_primary: string | null;
  tagline_secondary: string | null;
  suggested_questions: string[];
  has_custom_mascot: boolean;
}

/** GET /api/users and PATCH /api/users/{id} - the backend's AdminUserResponse.
 * Kept separate from User, which is what /api/auth/me returns to every logged-in
 * user: is_active and created_at are for the management screen only. */
export interface ManagedUser extends User {
  is_active: boolean;
  created_at: string;
}

/** CollectionResponse. It carries no document count - the management screen
 * derives one from a single GET /api/documents rather than asking per row. */
export interface Collection {
  id: string;
  name: string;
  description: string | null;
  /** WHAT THE DOCUMENTS' NUMBERING LOOKS LIKE, not what any one document is.
   * `{}` is prose. The two shapes the 문서 구조 select writes are
   * `{"strategy":"hierarchical","preset":"korean_legal"}` and
   * `{"strategy":"classification_table","preset":"korean_ip_classification"}`;
   * the backend validates it with the same `resolve` the worker calls, so a
   * shape it does not know is a 422 at save time. Whether a given document
   * actually uses that numbering is decided per document and lands in
   * `DocumentItem.structure`. */
  chunking: Record<string, unknown>;
  created_at: string;
}

/** GET /api/prompts. One entry per prompt NAME, carrying the text that is live
 * right now - the exact string the model receives as its system message on the
 * next question. Admin only. */
export interface PromptSummary {
  name: string;
  version: string;
  text: string;
  version_count: number;
  updated_at: string;
  /** What the active text costs in tokens, and the ceiling a save is refused
   * above. Counted by the server: the browser cannot count cl100k tokens, and
   * the ceiling is a backend constant a copy here would silently outlive. */
  tokens: number;
  token_limit: number;
}

/** GET /api/prompts/{name}/versions, newest first. `created_by_email` is null
 * for the version the migration seeded, which predates every account. */
export interface PromptVersion {
  id: string;
  version: string;
  text: string;
  is_active: boolean;
  created_by_email: string | null;
  created_at: string;
}

/** How dangerous a tool is, and the reason the column exists from the MCP
 * registry's first migration: Slice 3's human-approval gate reads it. A newly
 * discovered tool is `write`, never `read` - an unclassified tool must not be
 * the cheap one. */
export type McpRiskLevel = "read" | "write" | "destructive";

/** GET /api/mcp/servers/{id}'s tools, and the rows of the MCP 관리 table. */
export interface McpTool {
  id: string;
  server_id: string;
  name: string;
  description: string | null;
  input_schema: Record<string, unknown>;
  risk_level: McpRiskLevel;
  /** False on a tool an admin turned off AND on one the server stopped listing
   * - a tombstone, kept because messages.citations names it. */
  enabled: boolean;
  discovered_at: string;
}

/** GET /api/mcp/servers. Admin only. Note what is NOT here: the auth token. The
 * API accepts one on create/update and never returns it; `has_auth_token` is
 * the only thing the screen can know about it. */
export interface McpServer {
  id: string;
  name: string;
  base_url: string;
  auth_kind: "none" | "bearer";
  has_auth_token: boolean;
  enabled: boolean;
  created_by_email: string | null;
  created_at: string;
  updated_at: string;
  tools: McpTool[];
  /** Set when the server could not be reached. The row still exists so a
   * mistyped port can be corrected; this is what stops an empty tool list from
   * reading as "this server has no tools". */
  discovery_error: string | null;
}

/** GET /api/mcp/tools - what the composer's tool picker lists. Readable by any
 * authenticated user, and deliberately narrower than McpTool: no base_url and
 * nothing about whether the server carries a token. */
export interface McpToolOption {
  id: string;
  server_name: string;
  name: string;
  description: string | null;
  input_schema: Record<string, unknown>;
  risk_level: McpRiskLevel;
}

/** One tool call the user picked for the next message, with the arguments they
 * typed. Slice 2 is MANUAL invocation only - the model is never asked which
 * tool to call; that is Slice 3. */
export interface PendingToolCall {
  tool: McpToolOption;
  arguments: Record<string, unknown>;
}

export type DocumentStatus =
  | "uploaded"
  | "parsing"
  | "chunking"
  | "embedding"
  | "indexed"
  | "failed";

export type DocumentCharacter = "reference_dependent" | "self_contained";

/** `documents.structure`, written by the pipeline and read by the 구조 인식
 * panel. EVERY FIELD IS OPTIONAL and that is the contract, not laziness:
 *
 *  - `{}` is a document whose collection asks for prose, or one that has not
 *    been re-processed since the collection was configured. Nothing has been
 *    detected, which is not the same fact as "no structure found".
 *  - `citations` / `unresolved_examples` / `parent_edges` appear only for a
 *    document that was actually cut on its hierarchy - the pipeline skips edge
 *    building otherwise rather than record "0 of 130 resolved" about a document
 *    it never tried to resolve.
 *
 * See backend/app/rag/chunking/hierarchy.py:Detection.as_json. */
export interface DocumentStructure {
  /** What was APPLIED - the override when there is one, the detection otherwise. */
  character?: DocumentCharacter;
  /** What the content said, kept separate so the screen can show both. */
  detected?: DocumentCharacter;
  /** What a person said. Survives re-processing; null means nobody has. */
  override?: DocumentCharacter | null;
  confidence?: "high" | "ambiguous" | "none";
  /** The preset name, or "custom" for a collection that wrote its own levels. */
  scheme?: string;
  /** Level name to count. JSONB does not preserve key order, so this arrives in
   * Postgres's order rather than outermost-to-innermost. */
  levels?: Record<string, number>;
  blocks?: number;
  spine_ratio?: number;
  citation_ratio?: number;
  citations?: { found: number; resolved: number; unresolved: number };
  /** Unresolved citations verbatim - "[민법950]", "[헌법6]". The point is that
   * the reader can see WHICH law is missing from the corpus, which a count
   * cannot say. */
  unresolved_examples?: string[];
  parent_edges?: number;
}

export interface DocumentItem {
  id: string;
  collection_id: string;
  collection_name: string | null;
  filename: string;
  file_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error_message: string | null;
  uploader_email: string | null;
  chunk_count: number;
  structure: DocumentStructure;
  created_at: string;
  updated_at: string;
}

export interface Chunk {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  char_count: number;
  page: number | null;
  section: string | null;
  chunk_metadata: Record<string, unknown>;
  // Derived server-side from the chunk's embedding column; see ChunkResponse.
  embedded: boolean;
}

export interface Citation {
  index: number;
  // source_type and ref are the only two fields every citation carries - the
  // five below come from Evidence.metadata and a Slice 2/3 MCP citation has
  // none of them. See _citations_from in backend/app/chat/service.py.
  // "attachment" is a citation of a file the user attached to their own turn:
  // `filename` is set and chunk_id/document_id/page/section are all null, so
  // CitationBadge must not try to fetch a chunk for one.
  source_type: "rag" | "mcp" | "attachment";
  ref: string;
  chunk_id: string | null;
  document_id: string | null;
  filename: string | null;
  page: number | null;
  section: string | null;
  snippet: string;
  score: number | null;
}

/** POST /api/attachments, and the `attachments` array on a user MessageResponse.
 * `has_text` is whether the parser got anything out of a document; the text
 * itself is never sent to the browser. */
export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "image" | "document";
  has_text: boolean;
  created_at: string;
}

/** One node of a workflow graph, exactly as `backend/app/workflow/graph.py`
 * writes it back out (`WorkflowGraph.to_raw`).
 *
 * `x`/`y` ride ALONG on the node rather than in a parallel layout blob, because
 * the backend stores them and a person arranged them: reopening the canvas has
 * to show the same picture. They are the one part the executor reads nothing
 * from, which is exactly why they belong here and cannot drift out of step.
 *
 * `tool` is the flat namespace `GET /api/tools` publishes: `rag`,
 * `mcp:서버/도구`, `workflow:이름`. `collections` is RAG only, and EMPTY MEANS
 * THE WHOLE ALLOWED CATALOGUE - which the canvas says out loud rather than
 * drawing as an empty list. */
export interface GraphNode {
  id: string;
  kind: "input" | "tool" | "branch" | "answer";
  label?: string;
  x: number;
  y: number;
  tool?: string;
  collections?: string[];
  arguments?: Record<string, unknown>;
  condition?: GraphCondition | null;
}

/** A branch condition. JSON, not a string grammar - see
 * `backend/app/workflow/expr.py`, which parses the reference by hand and has no
 * `eval` in it. `llm` is in the schema and is REFUSED at save. */
export interface GraphCondition {
  kind: "compare" | "exists" | "empty" | "and" | "or" | "not" | "llm";
  left?: unknown;
  op?: "==" | "!=" | ">" | ">=" | "<" | "<=";
  right?: unknown;
  of?: unknown;
}

/** An edge ORDERS execution and carries data - the thing `PlanStep.depends_on`
 * deliberately did not do. `when` is set only on an edge leaving a `branch`, and
 * the backend refuses a branch edge that has none. */
export interface GraphEdge {
  from: string;
  to: string;
  when?: "true" | "false";
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** GET /api/workflows - the admin screen's row. Admin only, because the two
 * lists ARE the boundary and enumerating a boundary tells somebody what to try.
 *
 * `graph` is the ACTIVE version's, carried on the row so opening the canvas is
 * one request: splitting it into a second endpoint would guarantee a screen
 * showing one workflow's boxes over another's name at least once. */
export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  /** A name from the prompt store, never the text: the store owns versioning
   * and attribution, and a workflow carrying its own copy would fork it out. */
  prompt_name: string;
  /** Null means the deployment's own ANSWER_MODEL. */
  answer_model: string | null;
  enabled: boolean;
  /** EMPTY MEANS UNRESTRICTED, for both lists. The screen prints 전체 허용
   * rather than 없음 beside an empty selection - that is the one place this
   * rule could mislead an admin, so it is the one place it is spelled out. */
  collections: { id: string; name: string }[];
  tools: { id: string; server_name: string; name: string; risk_level: McpRiskLevel }[];
  /** Null when no version is active, which makes a workflow uncallable rather
   * than broken. */
  active_version: number | null;
  graph: WorkflowGraph | null;
  created_by_email: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/workflows/{id}/versions, newest first - the 되돌리기 list. Every
 * save is a version, and rolling back ACTIVATES an existing one rather than
 * copying it forward, so the history stays a history rather than growing a
 * duplicate every rollback. */
export interface WorkflowVersion {
  id: string;
  version: number;
  is_active: boolean;
  graph: WorkflowGraph;
  note: string | null;
  created_by_email: string | null;
  created_at: string;
}

/** GET /api/tools - ONE list, because RAG, MCP and workflows are one Tool
 * interface. It is what `@` opens in the composer and what the canvas offers on
 * a node.
 *
 * `ref` is what a graph node writes in its `tool` field and what a chip carries,
 * verbatim. `collections` is populated for the `rag` entry only: they are this
 * deployment's collections, so the composer can offer one search per collection
 * and the canvas can scope a search node. */
export interface CallableTool {
  kind: "rag" | "mcp" | "workflow";
  ref: string;
  name: string;
  description: string | null;
  risk_level: McpRiskLevel;
  collections: { id: string; name: string }[];
}

/** GET /api/workflows/selectable - what the composer's `@` menu and its picker
 * list. ENABLED workflows that have a graph, readable by any authenticated user,
 * and deliberately carrying neither boundary list: the boundary is not an
 * inventory to publish. */
export interface WorkflowOption {
  id: string;
  name: string;
  description: string | null;
  answer_model: string | null;
  /** How many nodes are in the graph it would run - the one number that says
   * this is a procedure rather than a prompt swap, without naming what it
   * reaches. */
  node_count: number;
}

/** GET /api/models - the admin's ANSWER_MODELS allowlist, which POST /api/chat
 * enforces. `label` falls back to the id server-side, so it is never empty. */
export interface AnswerModel {
  id: string;
  label: string;
  is_default: boolean;
  /** 추론 수준(reasoning_effort)을 받는 모델인가. 조절 UI를 그릴지의 근거. */
  reasoning: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** PUT /api/messages/{id}/feedback, and the `feedback` field of a
 * MessageResponse. Always the CALLER's own rating - a conversation has exactly
 * one reader - so there is no user field. */
export interface Feedback {
  rating: "up" | "down";
  comment: string | null;
  updated_at: string;
}

/** One retrieved item in GET /api/messages/{id}/trace.
 *
 * `included: false` is the field the whole trace screen exists for: the item was
 * retrieved and then dropped because the answer's token budget ran out before
 * it, so the model never saw it. A null rank means the item was absent from that
 * ranking entirely, which is a fact worth showing rather than a zero. */
/** One chunk that neighbour expansion folded into an evidence item's content.
 *
 * `offset` is -1 for the chunk before the cited one and +1 for the one after.
 * The item's own chunk_id/page/section still name the PRIMARY chunk - an
 * expanded item is one citation, not several - so this is the only thing on the
 * trace screen that says the text shown to the model was wider than that chunk.
 * `reason` is why it was merged: "proviso" (the next chunk opened with 다만 /
 * 그러나 / …), "dangling" (this chunk opened referring to text it did not
 * contain) or "blanket". */
export interface TraceNeighbor {
  chunk_id: string;
  chunk_index: number;
  offset: number;
  page: number | null;
  reason: string;
  tokens: number;
}

export interface TraceEvidence {
  index: number;
  source_type: "rag" | "mcp" | "attachment";
  ref: string;
  chunk_id: string | null;
  document_id: string | null;
  filename: string | null;
  page: number | null;
  section: string | null;
  vector_rank: number | null;
  keyword_rank: number | null;
  rrf_score: number | null;
  rerank_score: number | null;
  /** Empty when expansion is off, and on every answer written before it existed. */
  neighbors: TraceNeighbor[];
  score: number | null;
  tokens: number;
  snippet: string;
  included: boolean;
}

/** One NODE of a workflow graph as it actually ran, in
 * GET /api/messages/{id}/trace and in the `step` SSE frames the chat streams.
 * ONE SHAPE whoever authored the graph - a person on the canvas or 슈퍼 에이전트
 * per question - because two trace shapes would make "which am I looking at"
 * unanswerable on screen.
 *
 * `state` is the field worth reading: `done`, `failed` (recorded, and the run
 * carried on), `skipped` (the human declined it, or a branch did not select
 * it), `timeout` (the run's wall clock ran out first), or `running` while it is
 * in flight. */
export interface PlanStep {
  id: string;
  /** The four node kinds. `rag` appears on traces written before Slice 6, where
   * a search step was its own kind rather than a `tool` node naming `rag`. */
  kind: "input" | "tool" | "branch" | "answer" | "rag";
  label: string;
  state: "running" | "done" | "failed" | "skipped" | "timeout";
  query?: string | null;
  collections?: string[];
  tool?: string | null;
  risk_level?: string | null;
  arguments?: Record<string, unknown> | null;
  depends_on?: string[];
  /** How deep this node ran. 0 is the graph the request named; 1 and above are
   * nodes inside a workflow another workflow called - the one thing a flat step
   * list could not otherwise show. */
  depth?: number;
  evidence_count: number;
  ms: number;
  /** The Korean sentence that goes with a non-`done` state. It is `detail` on
   * the SSE frame and `error` in the stored trace; both are optional here. */
  detail?: string | null;
  error?: string | null;
}

/** The plan behind an answer, or the record that there was not one. Null for
 * every answer from the direct RAG path, which is still the default.
 *
 * `refused` is set when the planner produced something the executor would not
 * run - a tool it invented, a collection outside the question's scope, a ceiling
 * exceeded. The answer then came from the direct path, and this is the sentence
 * that says why. */
export interface TracePlan {
  /** 사람 or 슈퍼 에이전트 - who authored this graph. The ONLY field that differs
   * between the two, which is why they share one trace rather than two. Null on
   * every trace written before Slice 6. */
  author: string | null;
  /** Which workflow ran, and which version of it. Both null when 슈퍼 에이전트
   * ran without one selected. */
  workflow_name: string | null;
  workflow_version: number | null;
  steps: PlanStep[];
  step_count: number;
  tool_step_count: number;
  timed_out: boolean;
  elapsed_ms: number;
  fell_back_to_direct_rag: boolean;
  refused: string | null;
  budget_seconds: number | null;
  max_steps: number | null;
  max_nodes: number | null;
  max_tool_calls: number | null;
  max_depth: number | null;
  approval_risk_level: string | null;
}

/** The `approval_required` SSE frame. A plan paused on a tool whose risk level
 * is at or above ORCHESTRATOR_APPROVAL_RISK_LEVEL; nothing has been answered and
 * the tool has NOT been called.
 *
 * The token is opaque, single-use and owner-checked, and it is answered with a
 * SECOND request to POST /api/chat/approve rather than on this stream - SSE is
 * one-way, and a generator held open across the pause would die with the
 * connection at exactly the moment a user is most likely to walk away. */
export interface ApprovalRequest {
  approval_token: string;
  expires_in: number;
  conversation_id: string;
  step: {
    id: string;
    label: string;
    server: string;
    tool: string;
    risk_level: McpRiskLevel;
    arguments: Record<string, unknown>;
  };
}

export interface TraceRetrieval {
  top_n: number | null;
  candidate_limit: number | null;
  rrf_k: number | null;
  sparse_weight: number | null;
  token_budget: number | null;
  /** The system prompt's own cost and the allowance it is charged against.
   * null on a trace written while the budget still bounded the whole request -
   * which is not the same fact as 0. */
  prompt_tokens: number | null;
  mandatory_allowance: number | null;
  /** "off" | "targeted" | "blanket", or null on a trace written before neighbour
   * expansion existed - which is not the same fact as "off". */
  neighbor_expansion: string | null;
  evidence_count: number;
  included_count: number;
}

/** GET /api/messages/{id}/trace. Owner-scoped: someone else's is a 404, never a
 * 403. `has_trace` is false for an answer written before the trace column
 * existed - the columns above it are still real. */
export interface MessageTrace {
  message_id: string;
  conversation_id: string;
  created_at: string;
  model: string | null;
  workflow_name: string | null;
  workflow_version: number | null;
  prompt_name: string | null;
  prompt_version: string | null;
  latency_ms: number | null;
  retrieval_ms: number | null;
  usage: Record<string, number>;
  has_trace: boolean;
  retrieval: TraceRetrieval;
  evidence: TraceEvidence[];
  /** Null on the direct RAG path, which is still the default. `messages.trace`
   * is JSONB, so Slice 3 needed no migration to add this. */
  plan: TracePlan | null;
}

/** One row of GET /api/settings. `env_value` is what removing the override would
 * restore, which is what makes 기본값으로 되돌리기 a promise the screen keeps. */
export interface RuntimeSetting {
  key: string;
  label: string;
  help: string;
  group: string;
  kind: "int" | "float";
  minimum: number;
  maximum: number;
  value: number;
  env_value: number;
  overridden: boolean;
}

/** A value deliberately NOT editable at runtime, with the reason. Served by the
 * API so the reason lives beside the decision in the settings store. */
export interface EnvOnlySetting {
  key: string;
  label: string;
  reason: string;
}

export interface SettingsPayload {
  settings: RuntimeSetting[];
  env_only: EnvOnlySetting[];
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  // Populated on user turns only, and only for a turn that carried files.
  attachments: Attachment[];
  // The model that produced this answer, as the provider resolved it
  // ("gpt-4o-2024-08-06"). Null on every user turn, and on assistant turns
  // written before the model became a per-question choice.
  /** 어느 저장 프롬프트가 답했는가. "smalltalk_agent"면 검색 없이 답한
   * 대화형 응답이라 근거-없음 경고를 붙이지 않는다. 과거 답변은 null. */
  prompt_name?: string | null;
  model: string | null;
  // WHICH WORKFLOW ANSWERED, and which version of it. Null on every user turn,
  // on every answer written before workflows existed, and on every answer given
  // without one - all three render the same way, because they are the same
  // fact: the app answering as it always did.
  workflow_name: string | null;
  workflow_version?: number | null;
  // The caller's own 👍/👎, null until they rate this answer. It rides the
  // transcript so a reload does not lose it, and so opening a conversation is
  // one request rather than one per assistant message.
  feedback: Feedback | null;
  created_at: string;
}

/** SSE payloads from POST /api/chat and POST /api/chat/approve. `token` is
 * still reserved - nothing emits it. */
export type ChatEvent =
  // "calling_tool" is emitted only when the turn carried tool_calls, and always
  // before "searching": the MCP round trip happens first so the user sees the
  // slow, visible thing they asked for happening. "planning" is Slice 3's, and
  // "searching" then appears only when the plan produced no evidence and the
  // direct RAG path answered instead.
  | { type: "status"; status: "searching" | "answering" | "calling_tool" | "planning" }
  // One per plan step, twice: `running` when it starts, and its final state when
  // it ends. This is the "문서 검색 → 진단 → 결과 종합" the requirement asked for.
  | ({ type: "step" } & PlanStep)
  // TERMINAL, like `done` and `error`: the plan stopped, nothing was answered,
  // and the client replies with POST /api/chat/approve.
  | ({ type: "approval_required" } & ApprovalRequest)
  | { type: "token"; text: string }
  | { type: "citations"; citations: Citation[] }
  | {
      type: "done";
      conversation_id: string;
      // The real assistant row id, so the just-streamed answer can be rated and
      // traced without a reload. The client used to fabricate one from a
      // timestamp, which pointed at nothing.
      message_id: string;
      content: string;
      citations: Citation[];
      model: string | null;
      /** 어느 저장 프롬프트가 답했는가 - "smalltalk_agent"면 근거-없음 경고를
       * 붙이지 않는다. 과거 서버의 프레임에는 없을 수 있다. */
      prompt_name?: string | null;
      // So the answer on screen can say what produced it without a reload,
      // exactly as `model` does. Null when no workflow was named.
      workflow_name: string | null;
      workflow_version?: number | null;
    }
  | { type: "error"; detail: string };
