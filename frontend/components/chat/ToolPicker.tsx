"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { McpToolOption, PendingToolCall } from "@/lib/types";

/** Picking ONE MCP tool to run before the next question is answered.
 *
 * A native <dialog> opened with showModal(), for the same reason ConfirmDialog
 * and ModelPicker use one: the focus trap, Escape, the inert background and
 * top-layer stacking are all the platform's, and none of it has to be written
 * here. Unlike the two pickers this really is a modal - it has a form in it, it
 * is centred rather than anchored, and it keeps the scrim on desktop - which is
 * why it does NOT use PopoverSheet.
 *
 * Controlled like the other two: the composer's + menu is the only way in, and
 * the menu closes before this opens.
 *
 * The argument fields are generated from the tool's own `input_schema`, which is
 * discovered at runtime and therefore cannot be a compile-time form. Only
 * top-level properties are rendered: that covers what MCP servers actually
 * declare, and a nested object would need a schema-form library to do properly.
 * A tool whose arguments this cannot express is still callable - the server is
 * the one that validates them, and it answers a bad set with an error that
 * becomes evidence saying the call failed.
 */

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

type Property = { type?: string; description?: string; title?: string };

function propertiesOf(schema: Record<string, unknown>): [string, Property][] {
  const properties = schema?.properties;
  if (!properties || typeof properties !== "object") return [];
  return Object.entries(properties as Record<string, Property>);
}

function requiredOf(schema: Record<string, unknown>): string[] {
  const required = schema?.required;
  return Array.isArray(required) ? required.filter((r): r is string => typeof r === "string") : [];
}

export default function ToolPicker({
  tools,
  onSelect,
  open,
  onClose,
  initialToolId,
}: {
  tools: McpToolOption[];
  onSelect: (call: PendingToolCall) => void;
  open: boolean;
  /** Which tool to open ON. Set when `@` picked one by name: the row the user
   * chose is already the answer to "which tool", and re-asking it in the dialog
   * would make the `@` gesture a slower route to the same list. Null - the +
   * menu's 도구 사용 row - leaves the previous selection alone, which is what
   * lets somebody adjust the arguments of the tool they just used. */
  initialToolId?: string | null;
  /** A dismissal, a 취소 or a committed 추가. Focus return belongs to the
   * composer's `closeSheet`, which is the one owner of it - see PopoverSheet. */
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  // Same reasoning as PopoverSheet's: a close the OWNER asked for by flipping
  // `open` must not fire onClose back at it.
  const closingRef = useRef(false);
  const [selectedId, setSelectedId] = useState("");
  // Keyed by tool id AND property name, so switching tools in the same dialog
  // does not carry a value across into a field that happens to share a name.
  const [values, setValues] = useState<Record<string, string>>({});

  const selected = useMemo(() => tools.find((t) => t.id === selectedId), [tools, selectedId]);
  const byServer = useMemo(() => {
    const groups = new Map<string, McpToolOption[]>();
    for (const tool of tools) {
      const list = groups.get(tool.server_name) ?? [];
      list.push(tool);
      groups.set(tool.server_name, list);
    }
    return [...groups.entries()];
  }, [tools]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || tools.length === 0) return;
    if (!open) {
      if (dialog.open) {
        closingRef.current = true;
        dialog.close();
      }
      return;
    }
    if (dialog.open) return;
    setSelectedId((current) => initialToolId || current || tools[0].id);
    dialog.showModal();
  }, [open, tools, initialToolId]);

  // Nothing to pick from: an admin has registered no server, or every tool is
  // disabled. The + menu drops its 도구 사용 row for the same reason.
  if (tools.length === 0) return null;

  function commit() {
    if (!selected || missing.length > 0) return;
    const args: Record<string, unknown> = {};
    for (const [name, property] of propertiesOf(selected.input_schema)) {
      const raw = values[`${selected.id}:${name}`];
      if (property.type === "boolean") {
        if (raw === "true") args[name] = true;
        continue;
      }
      // An untouched optional field is OMITTED rather than sent as "": a server
      // that declares a default gets to use it, and an empty string is a value.
      if (raw === undefined || raw === "") continue;
      if (property.type === "number" || property.type === "integer") {
        const parsed = Number(raw);
        args[name] = Number.isFinite(parsed) ? parsed : raw;
        continue;
      }
      args[name] = raw;
    }
    onSelect({ tool: selected, arguments: args });
    onClose();
  }

  const required = selected ? requiredOf(selected.input_schema) : [];
  // The dialog cannot be a <form>. It is rendered inside the composer's own
  // form, HTML forbids nested forms, and the browser resolves that by dropping
  // the inner one - so a `type="submit"` button in here submitted the COMPOSER,
  // and 추가 sent the message with no tool attached and no error anywhere.
  // Found by driving it, not by reading it. Without a form there is no
  // `required` validation either, so this is what replaces it.
  const missing = selected
    ? required.filter((name) => !(values[`${selected.id}:${name}`] ?? "").trim())
    : [];

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="tool-picker-title"
      onClose={() => {
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        onClose();
      }}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      {/* A div, not a form - see `missing` above. Enter inside a field commits
          the tool, which is the one thing a form would otherwise have given
          for free, and preventDefault is what stops that Enter from reaching
          the composer form as an implicit submit. */}
      <div
        onKeyDown={(event) => {
          if (event.key !== "Enter" || !(event.target instanceof HTMLInputElement)) return;
          event.preventDefault();
          commit();
        }}
        className="space-y-4 p-6"
      >
        <h2 id="tool-picker-title" className="text-title font-medium">
          도구 사용
        </h2>

        <div>
          <label htmlFor="tool-picker-select" className="text-label font-medium text-on-surface-variant">
            도구
          </label>
          <select
            id="tool-picker-select"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="field mt-1 w-full"
          >
            {byServer.map(([server, group]) => (
              <optgroup key={server} label={server}>
                {group.map((tool) => (
                  <option key={tool.id} value={tool.id}>
                    {tool.name} · {RISK_LABEL[tool.risk_level] ?? tool.risk_level}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        {selected && (
          <>
            {selected.description && (
              <p className="text-body text-on-surface-variant">{selected.description}</p>
            )}
            {propertiesOf(selected.input_schema).length === 0 ? (
              <p className="text-caption text-on-surface-variant">입력값이 필요 없는 도구입니다.</p>
            ) : (
              propertiesOf(selected.input_schema).map(([name, property]) => {
                const key = `${selected.id}:${name}`;
                const id = `tool-arg-${name}`;
                if (property.type === "boolean") {
                  return (
                    <label key={key} className="flex items-center gap-2 text-body">
                      <input
                        id={id}
                        type="checkbox"
                        checked={values[key] === "true"}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [key]: String(e.target.checked) }))
                        }
                        className="h-4 w-4 accent-primary"
                      />
                      {property.title ?? name}
                    </label>
                  );
                }
                return (
                  <div key={key}>
                    <label htmlFor={id} className="text-label font-medium text-on-surface-variant">
                      {property.title ?? name}
                      {required.includes(name) && <span className="text-error"> *</span>}
                    </label>
                    <input
                      id={id}
                      type={property.type === "number" || property.type === "integer" ? "number" : "text"}
                      value={values[key] ?? ""}
                      onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      aria-required={required.includes(name) || undefined}
                      className="field mt-1 w-full"
                    />
                    {property.description && (
                      <p className="mt-1 text-caption text-on-surface-variant">{property.description}</p>
                    )}
                  </div>
                );
              })
            )}
            {/* Said out loud, on the screen where the call is made: the result
                is third-party text this system treats as reference data, never
                as an instruction. */}
            <p className="rounded-sm bg-surface-container-high p-3 text-caption text-on-surface-variant">
              도구가 돌려준 결과는 외부에서 온 참고 자료로만 쓰이며, 그 안의 지시는 따르지
              않습니다.
            </p>
          </>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-text">
            취소
          </button>
          <button
            type="button"
            onClick={commit}
            disabled={missing.length > 0}
            className="btn-filled"
          >
            추가
          </button>
        </div>
      </div>
    </dialog>
  );
}
