"""Check every plan, oldest first, as one history.

`check_plan_parity.py` accepts several plans and treats them as an ordered
history: a later plan's block for a file supersedes an earlier plan's. That is
what stops a file quoted whole by an early plan from "drifting" every time a
later slice edits it — which happened three times in two days, and was being
patched by re-emitting the old block from disk, making an early task show text
it never produced.

Add new plans HERE, in the order they were written. A plan missing from this
list is a plan nobody checks.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PLANS = [
    # Slice 1's plan is frozen history: its files have all been superseded by
    # the plans below, and re-listing it would only re-open blocks that the
    # later work legitimately replaced.
    "docs/superpowers/plans/2026-08-30-management-screens.md",
    "docs/superpowers/plans/2026-08-30-model-selection.md",
    "docs/superpowers/plans/2026-08-30-prompt-admin.md",
    "docs/superpowers/plans/2026-08-30-slice-5-observability.md",
    "docs/superpowers/plans/2026-08-30-slice-2-mcp.md",
    "docs/superpowers/plans/2026-08-30-slice-3-orchestrator.md",
    "docs/superpowers/plans/2026-08-30-slice-4-agents.md",
    "docs/superpowers/plans/2026-08-30-neighbour-expansion.md",
    "docs/superpowers/plans/2026-08-30-prompt-budget.md",
    "docs/superpowers/plans/2026-08-31-ui-masthead-composer-sidebar.md",
    "docs/superpowers/plans/2026-08-31-agent-builder.md",
    # Slice 6. It supersedes the backend halves of the slice-3, slice-4 and
    # agent-builder plans: `app/orchestrator/` and `app/agents/` no longer exist,
    # and rule 3 reads a later plan's block for a path as replacing an earlier
    # one's. It says nothing about `frontend/`, which is another agent's.
    "docs/superpowers/plans/2026-08-31-workflow-engine.md",
]

missing = [p for p in PLANS if not (REPO / p).exists()]
if missing:
    print("plans listed here but not on disk:")
    for path in missing:
        print(f"  {path}")
    sys.exit(2)

sys.exit(
    subprocess.run(
        [sys.executable, str(REPO / "scripts/check_plan_parity.py"), *PLANS, *sys.argv[1:]],
        cwd=REPO,
    ).returncode
)
