#!/usr/bin/env python3
"""Check the implementation plan's code blocks against the files on disk.

The plan is the durable transcription source: a future session rebuilds this
repo from it. Four parity claims in this project have turned out to be false,
each time because a task wrote a throwaway extractor that missed a case. This
is that extractor, checked in once.

Classification rules, in the order they are applied:

1.  A block belongs to the step header above it. `Write`/`Create`/`Complete`
    means the block is the WHOLE file; `Modify`/`Append`/`Remove` means it is a
    PARTIAL snippet, checked for verbatim presence. Other verbs (Run, Commit,
    Verify) carry no file claim.
2.  A task whose `Write`/`Create` files are not all on disk has not run yet
    (Tasks 13-24). Its blocks are unverifiable - skipped, not drift.
3.  STANDING CONTROLLER RULING: a block captures its file *as of its own task*,
    and later tasks amend it. So if any LATER task mentions the same path, a
    mismatch is SUPERSEDED, not drift. This is why Task 2's whole-file
    `config.py` differing from disk is expected.

Only an unsuperseded mismatch is drift. Two things exit non-zero: drift, and a
step header whose backticked filename is bare and matches no file in the repo -
that one names nothing to compare, and silently mutes its whole task under rule 2.

Caveat worth stating out loud: rule 3 is triage, not proof. It says a mismatch
has a plausible innocent cause, not that the cause is the one it names. It
cannot tell "Task 7's snippet was rewritten by Task 23" from "Task 7's snippet
was mistranscribed". Read the superseded list; do not just count it.

Usage: python scripts/check_plan_parity.py [plan.md]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = REPO / "docs" / "superpowers" / "plans" / "2026-08-28-vertical-slice-1.md"

TASK_RE = re.compile(r"^#{2,4}\s+Task\s+(\d+)\b")
STEP_RE = re.compile(r"^-\s+\[[ x]\]\s+\*\*Step\s+[\w]+:\s*(.*)$")
FENCE_RE = re.compile(r"^(\s*)```(\w*)\s*$")
BACKTICK_RE = re.compile(r"`([^`]+)`")

WHOLE_FILE_VERBS = {"write", "create", "complete"}
PARTIAL_VERBS = {"modify", "append", "remove"}

# Fence language -> file suffixes it can plausibly be. Used to drop the bash and
# text fences that sit beside a step's real code block.
LANG_SUFFIXES = {
    "python": {".py", ".mako"},
    "py": {".py"},
    "ini": {".ini", ".cfg"},
    "toml": {".toml"},
    "yaml": {".yml", ".yaml"},
    "yml": {".yml", ".yaml"},
    "json": {".json"},
    "dockerfile": {""},
    "text": {".txt", ".gitignore", ".dockerignore", ".gitattributes", ".example", ""},
    "": {".txt", ".gitignore", ".dockerignore", ".gitattributes", ".example", ""},
    "typescript": {".ts", ".tsx"},
    "tsx": {".ts", ".tsx"},
    "ts": {".ts", ".tsx"},
    "javascript": {".js", ".mjs"},
    "js": {".js", ".mjs"},
    "css": {".css"},
    "markdown": {".md"},
    "md": {".md"},
}

ROOT_FILE_SUFFIXES = {".md", ".yml", ".yaml", ".txt", ".ini", ".toml", ".json", ".example"}

# A bare filename is a path CLAIM, but not a usable path. A step header reading
# "Write `a/b/ChunkViewer.tsx` and `StructureViewer.tsx`" yielded ONE path for TWO
# blocks, and pair()'s single-path fallback then aimed both blocks at the first
# file - so the second was never compared and the first was compared against the
# wrong block. Rejecting the bare token is what made that silent; ACCEPTING it is
# not a fix either, because `REPO / "StructureViewer.tsx"` does not exist, so rule
# 2 reads the whole task as unrun and skips all of its blocks at exit 0. That is
# worse: a tool whose only alarm is a non-zero exit went quiet for a whole task.
# So accept it here, and report it as AMBIGUOUS below. The real fix in every case
# is the plan header, which should name the path.
CODE_FILE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".sql", ".sh", ".mako"}


def looks_like_path(token: str) -> bool:
    """`backend/app/main.py`, `.gitignore` and `StructureViewer.tsx` are paths;
    `app.rag.parsers` is not."""
    if not token or any(c.isspace() for c in token) or token.endswith("/"):
        return False
    if "/" in token:
        return True
    return token.startswith(".") or Path(token).suffix in ROOT_FILE_SUFFIXES | CODE_FILE_SUFFIXES


def paths_in(text: str) -> list[str]:
    seen: list[str] = []
    for token in BACKTICK_RE.findall(text):
        token = token.strip().rstrip(".,;:")
        if looks_like_path(token) and token not in seen:
            seen.append(token)
    return seen


@dataclass
class Block:
    lang: str
    body: str
    line: int


@dataclass
class Step:
    task: int
    line: int
    header: str
    verb: str
    paths: list[str]
    prose: str
    blocks: list[Block]


def parse(plan_text: str) -> list[Step]:
    """Walk the plan, attaching each fenced block to its preceding step header."""
    steps: list[Step] = []
    task = 0
    current: Step | None = None
    fence: tuple[str, str, int] | None = None  # indent, lang, start line
    buf: list[str] = []

    for number, line in enumerate(plan_text.split("\n"), start=1):
        if fence is not None:
            indent, lang, start = fence
            closing = FENCE_RE.match(line)
            if closing and closing.group(2) == "" and len(closing.group(1)) == len(indent):
                if current is not None:
                    current.blocks.append(Block(lang, "\n".join(buf), start))
                fence, buf = None, []
            else:
                buf.append(line[len(indent) :] if line.startswith(indent) else line)
            continue

        opening = FENCE_RE.match(line)
        if opening:
            fence = (opening.group(1), opening.group(2).lower(), number + 1)
            continue

        task_match = TASK_RE.match(line)
        if task_match:
            task = int(task_match.group(1))
            current = None
            continue

        step_match = STEP_RE.match(line)
        if step_match:
            header = step_match.group(1).split("**")[0].strip()
            verb = re.sub(r"[^a-z]", "", header.split()[0].lower()) if header.split() else ""
            current = Step(task, number, header, verb, paths_in(header), "", [])
            steps.append(current)
        elif current is not None:
            current.prose += line + "\n"

    return steps


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def pair(step: Step) -> list[tuple[str, Block]] | None:
    """Match this step's paths to its code blocks, or None if it is ambiguous."""
    wanted: set[str] = set()
    for path in step.paths:
        wanted.add(Path(path).suffix)
    usable = [b for b in step.blocks if LANG_SUFFIXES.get(b.lang, {b.lang}) & wanted]

    if len(usable) == len(step.paths):
        return list(zip(step.paths, usable, strict=True))
    if len(step.paths) == 1:
        # An unknown fence language (```gitignore, ```dotenv, ```mako) filters out
        # every block and would silently check nothing. One path means every block
        # under it belongs to that path regardless of how the fence is tagged.
        return [(step.paths[0], b) for b in (usable or step.blocks)]
    return None


def main(argv: list[str]) -> int:
    # The plan is UTF-8; a Windows console is often cp949. Do not die on an em-dash.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Several plans are read as ONE ordered history, oldest first. Every rule
    # below keys on `step.task`, an integer, so giving each plan a task-number
    # range above the previous one is all "a later plan supersedes an earlier
    # one" needs - rules 2 and 3 then work across plans with no change.
    #
    # Without this a file quoted whole by an early plan drifts the moment a
    # later slice edits it, even though a later plan describes it correctly.
    # The workaround was re-emitting the old block from disk, which makes that
    # task's block show text the task never produced. Passing every plan in
    # order says what is actually true instead.
    plan_paths = [Path(a) for a in argv[1:]] or [DEFAULT_PLAN]
    steps: list[Step] = []
    offset = 0
    for plan_path in plan_paths:
        plan_steps = parse(plan_path.read_text(encoding="utf-8"))
        if not plan_steps:
            continue
        steps.extend(replace(s, task=s.task + offset) for s in plan_steps)
        offset = max(s.task for s in steps) + 1

    # Rule 2: a task is unrun until every file it claims to create exists.
    created: dict[int, list[str]] = {}
    for step in steps:
        if step.verb in WHOLE_FILE_VERBS:
            created.setdefault(step.task, []).extend(step.paths)
    missing: dict[int, list[str]] = {
        task: [p for p in paths if not (REPO / p).exists()] for task, paths in created.items()
    }
    unrun = {task for task, paths in created.items() if not paths or missing[task]}
    unrun |= {s.task for s in steps} - set(created)

    # Rule 3: the last task that mentions a path anywhere outside a code fence.
    last_mention: dict[str, int] = {}
    for step in steps:
        for path in paths_in(step.header + "\n" + step.prose):
            last_mention[path] = max(last_mention.get(path, 0), step.task)

    drift: list[str] = []
    superseded: list[str] = []
    skipped: list[str] = []
    ambiguous: list[str] = []
    unresolvable: list[str] = []
    excusable: set[tuple[str, int]] = set()
    checked = 0

    for step in steps:
        whole = step.verb in WHOLE_FILE_VERBS
        if not (whole or step.verb in PARTIAL_VERBS) or not step.paths:
            continue
        where = f"Task {step.task} (plan:{step.line}) {step.header[:60]}"

        # A bare `StructureViewer.tsx` in a header names no file the checker can
        # open. Left to rule 2 it silently mutes the whole task, so say it out
        # loud instead. Bare tokens that DO resolve (README.md, docker-compose.yml,
        # .gitignore) are real repo-root paths and are not affected.
        bare = [
            p
            for p in step.paths
            if "/" not in p and Path(p).suffix in CODE_FILE_SUFFIXES and not (REPO / p).exists()
        ]
        if bare:
            ambiguous.append(f"{where} - bare filename, not a repo path: {', '.join(bare)}")
            unresolvable.append(f"{where} - {', '.join(bare)}")
            continue

        if step.task in unrun:
            absent = missing.get(step.task) or ["no Write/Create step"]
            # Spell out the truncation: Task 20 is missing 14 files, and a bare
            # absent[:3] printed as if those three were the whole list.
            shown = ", ".join(absent[:3])
            if len(absent) > 3:
                shown += f", ... (+{len(absent) - 3} more)"
            skipped.append(f"{where} - task has not run yet (missing: {shown})")
            continue
        if not step.blocks:
            skipped.append(f"{where} - no code block (empty file or prose-only step)")
            continue

        pairs = pair(step)
        if pairs is None:
            ambiguous.append(f"{where} - {len(step.paths)} paths vs {len(step.blocks)} blocks")
            continue

        for path, block in pairs:
            target = REPO / path
            if not target.exists():
                skipped.append(f"{where} - {path} does not exist yet")
                continue
            checked += 1
            if last_mention.get(path, 0) > step.task:
                excusable.add((path, step.task))
            disk = norm(target.read_text(encoding="utf-8"))
            body = norm(block.body)
            if (disk == body) if whole else (body in disk):
                continue
            kind = "whole-file" if whole else "snippet"
            entry = f"{path} ({kind}, plan:{block.line}) <- Task {step.task}"
            if last_mention.get(path, 0) > step.task:
                superseded.append(f"{entry}, amended by Task {last_mention[path]}")
            else:
                drift.append(f"{entry} - no later task amends it")

    def section(title: str, rows: list[str]) -> None:
        print(f"\n{title} ({len(rows)})")
        for row in rows:
            print(f"  {row}")

    claiming = sum(1 for s in steps if s.paths and s.verb in WHOLE_FILE_VERBS | PARTIAL_VERBS)
    print("plan: " + ", ".join(str(x) for x in plan_paths))
    print(f"steps with a file claim: {claiming}")
    print(f"blocks compared against disk: {checked}")
    # Rule 3's ceiling, stated in the output rather than only in the docstring:
    # these blocks would be excused as SUPERSEDED if they drifted, so a mutation
    # in any of them exits 0. Read them by hand; the tool cannot.
    by_path: dict[str, list[int]] = {}
    for path, task in sorted(excusable):
        by_path.setdefault(path, []).append(task)
    section(
        "RULE-3 EXCUSABLE (a later task amends these; drift here would NOT be caught)",
        [f"{p} <- Task {', '.join(map(str, t))}" for p, t in sorted(by_path.items())],
    )
    section("SUPERSEDED (mismatch with a later amending task - expected)", superseded)
    section("AMBIGUOUS (could not pair paths to blocks - check by hand)", ambiguous)
    section("SKIPPED", skipped)
    section("DRIFT", drift)
    print()
    if unresolvable:
        # The one AMBIGUOUS case that is never innocent, so the one that gets the
        # exit code. The rest of that bucket is "I could not tell which block goes
        # with which path"; this is "this header names a file that does not exist
        # anywhere in the repo", which is always a plan defect and would otherwise
        # mute an entire task at exit 0.
        print(f"FAIL: {len(unresolvable)} step header(s) name a bare filename with no repo path.")
        return 1
    if drift:
        print(f"FAIL: {len(drift)} block(s) drifted from disk with no later task to explain it.")
        return 1
    print("OK: no unexplained drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
