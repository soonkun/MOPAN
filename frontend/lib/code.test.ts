import assert from "node:assert/strict";
import { test } from "node:test";

import { EMPTY_THREAD, applyEvent, toolSummary } from "./code.ts";

const S = "ses_1";

test("파트가 메시지보다 먼저 와도 자리표시자에 붙고, 델타는 이어 붙는다", () => {
  let st = applyEvent(EMPTY_THREAD, {
    type: "message.part.updated",
    properties: { sessionID: S, part: { id: "prt_1", messageID: "msg_1", sessionID: S, type: "text", text: "안" } },
  }, S);
  st = applyEvent(st, {
    type: "message.part.delta",
    properties: { sessionID: S, messageID: "msg_1", partID: "prt_1", field: "text", delta: "녕" },
  }, S);
  st = applyEvent(st, {
    type: "message.updated",
    properties: { sessionID: S, info: { id: "msg_1", sessionID: S, role: "assistant", time: { created: 1 } } },
  }, S);
  assert.equal(st.messages.length, 1);
  assert.equal(st.messages[0].parts[0].text, "안녕");
  assert.equal(st.messages[0].info.role, "assistant");
});

test("다른 세션의 이벤트는 같은 객체를 돌려준다", () => {
  const st = applyEvent(EMPTY_THREAD, { type: "session.idle", properties: { sessionID: "other" } }, S);
  assert.equal(st, EMPTY_THREAD);
});

test("권한 요청은 asked로 쌓이고 replied로 빠진다", () => {
  let st = applyEvent(EMPTY_THREAD, {
    type: "permission.asked",
    properties: { id: "per_1", sessionID: S, permission: "bash", patterns: ["python3 *"], metadata: { command: "python3 a.py" }, always: [] },
  }, S);
  assert.equal(st.permissions.length, 1);
  st = applyEvent(st, { type: "permission.replied", properties: { sessionID: S, requestID: "per_1", reply: "once" } }, S);
  assert.equal(st.permissions.length, 0);
});

test("도구 요약은 bash면 명령, edit면 경로", () => {
  assert.equal(toolSummary({ id: "p", messageID: "m", sessionID: S, type: "tool", tool: "bash", state: { status: "running", input: { command: "ls" } } }), "ls");
  assert.equal(toolSummary({ id: "p", messageID: "m", sessionID: S, type: "tool", tool: "edit", state: { status: "completed", input: { filePath: "/workspace/a/x.py" } } }), "/workspace/a/x.py");
});
