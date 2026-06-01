#!/usr/bin/env python3
"""README example linter.

Catches the post-v2.0.8 footgun where README code blocks still say
`python3 muninn.py ...`. After v2.0.8, deps live in `.venv/`, so users
following those examples hit `ModuleNotFoundError: No module named
'gungnir'` (the Pi24 user did exactly this on 2026-06-01, surfaced in
the WDGoWars Discord).

What this checks:

- Every fenced ```bash code block in README.md is scanned line by line.
- Any line that invokes `python3 muninn.py` (or `python muninn.py`)
  directly is flagged UNLESS one of these escape hatches applies:
    1. The line is inside an "Option B - clone with git" or similar
       manual-install block, AND the block also references
       `python3 -m venv .venv` or `.venv/bin/`. Those are intentional
       teaching examples.
    2. The line is preceded by a comment containing `# direct invocation`
       (explicit author override for cases where we WANT to show the
       non-venv form).

Otherwise, flag with the line number and a suggested rewrite.

Run: python scripts/check_readme_examples.py [README.md]
Exit codes: 0 clean, 1 issues found.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path


CODE_FENCE = re.compile(r"^```(\w+)?\s*$")
PROBLEM = re.compile(r"^\s*(python3?)\s+muninn\.py\b")
VENV_FORM = re.compile(r"(\.venv/bin/python|python3?\s+-m\s+venv)")
OVERRIDE_MARKER = "# direct invocation"


def lint_readme(path: Path) -> list[tuple[int, str, str]]:
    """Return a list of (line_number, line, why) findings."""
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[tuple[int, str, str]] = []
    in_block = False
    block_lang = ""
    block_start = 0
    block_lines: list[tuple[int, str]] = []

    def flush_block():
        if not block_lines:
            return
        # If any line in the block references a venv form, treat the
        # block as teaching-the-venv-flow — `python3 muninn.py` inside
        # such a block is intentional (showing the system-Python form
        # the user would hit, with the venv answer nearby).
        block_text = "\n".join(l for _, l in block_lines)
        teaching = bool(VENV_FORM.search(block_text))
        prev_override = False
        for lineno, line in block_lines:
            if OVERRIDE_MARKER in line:
                prev_override = True
                continue
            if PROBLEM.match(line):
                if teaching or prev_override:
                    prev_override = False
                    continue
                findings.append((
                    lineno, line,
                    "uses system python3 directly; either prefix with "
                    "`.venv/bin/python` / `./run.sh`, or move the line "
                    "into a teaching block that explains `python3 -m "
                    f"venv .venv`, or annotate with `{OVERRIDE_MARKER}`",
                ))
            prev_override = False

    for i, line in enumerate(lines, start=1):
        m = CODE_FENCE.match(line)
        if m:
            if in_block:
                # closing fence
                flush_block()
                in_block = False
                block_lines = []
            else:
                # opening fence
                in_block = True
                block_lang = (m.group(1) or "").lower()
                block_start = i
                block_lines = []
            continue
        if in_block and block_lang in ("bash", "sh", "shell", ""):
            block_lines.append((i, line))
    # If file ends mid-block, still flush
    if in_block:
        flush_block()
    return findings


def main(argv: list[str]) -> int:
    readme = Path(argv[1]) if len(argv) > 1 else Path("README.md")
    if not readme.exists():
        print(f"{readme}: not found", file=sys.stderr)
        return 2
    findings = lint_readme(readme)
    if not findings:
        print(f"{readme}: clean ({len(readme.read_text().splitlines())} lines)")
        return 0
    print(f"{readme}: {len(findings)} issue(s) found", file=sys.stderr)
    for lineno, line, why in findings:
        print(f"  {readme}:{lineno}: {line.strip()}", file=sys.stderr)
        print(f"    {why}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
