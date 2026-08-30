export interface User {
  id: string;
  email: string;
  role: "admin" | "user";
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

/** GET /api/models - the admin's ANSWER_MODELS allowlist, which POST /api/chat
 * enforces. `label` falls back to the id server-side, so it is never empty. */
export interface AnswerModel {
  id: string;
  label: string;
  is_default: boolean;
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
  score: number | null;
  tokens: number;
  snippet: string;
  included: boolean;
}

export interface TraceRetrieval {
  top_n: number | null;
  candidate_limit: number | null;
  rrf_k: number | null;
  sparse_weight: number | null;
  token_budget: number | null;
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
  prompt_name: string | null;
  prompt_version: string | null;
  latency_ms: number | null;
  retrieval_ms: number | null;
  usage: Record<string, number>;
  has_trace: boolean;
  retrieval: TraceRetrieval;
  evidence: TraceEvidence[];
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
  model: string | null;
  // The caller's own 👍/👎, null until they rate this answer. It rides the
  // transcript so a reload does not lose it, and so opening a conversation is
  // one request rather than one per assistant message.
  feedback: Feedback | null;
  created_at: string;
}

/** SSE payloads from POST /api/chat. `token` is reserved for Slice 3. */
export type ChatEvent =
  // "calling_tool" is emitted only when the turn carried tool_calls, and always
  // before "searching": the MCP round trip happens first so the user sees the
  // slow, visible thing they asked for happening.
  | { type: "status"; status: "searching" | "answering" | "calling_tool" }
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
    }
  | { type: "error"; detail: string };
