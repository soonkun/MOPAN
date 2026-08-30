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
  created_at: string;
}

/** SSE payloads from POST /api/chat. `token` is reserved for Slice 3. */
export type ChatEvent =
  | { type: "status"; status: "searching" | "answering" }
  | { type: "token"; text: string }
  | { type: "citations"; citations: Citation[] }
  | {
      type: "done";
      conversation_id: string;
      content: string;
      citations: Citation[];
      model: string | null;
    }
  | { type: "error"; detail: string };
