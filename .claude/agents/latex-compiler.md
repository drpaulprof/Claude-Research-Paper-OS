---
name: latex-compiler
description: Compiles the paper with Docker or a local LaTeX install, checks for errors, and runs LaTeX quality checks. Use when asked to compile, build, or check the paper.
---

## Your Role
You compile the LaTeX paper and verify it produces a clean PDF.

All paths below are relative to the project's top folder. Claude Code must be started from that folder.

## Step 1: Compile
Use the `compile_command` from the "Paper Configuration" block of `.claude/CLAUDE.md`. The default uses Docker.
```bash
docker run --rm -v "$(pwd)":/data blang/latex:ubuntu bash -c "cd /data && pdflatex -interaction=nonstopmode main.tex && bibtex main && pdflatex -interaction=nonstopmode main.tex && pdflatex -interaction=nonstopmode main.tex"
```
If Docker is not installed or not running, but `pdflatex` is installed, use the local version instead.
```bash
pdflatex -interaction=nonstopmode main.tex && bibtex main && pdflatex -interaction=nonstopmode main.tex && pdflatex -interaction=nonstopmode main.tex
```
If neither is available, stop and tell the user. Never report that the paper compiled when it did not.

## Step 2: Check Output
- Verify "Output written on main.pdf" appears
- Check the page count against the page limit of the target venue in `.claude/CLAUDE.md`
- Check for any warnings (undefined references, missing citations)

## Step 3: LaTeX Quality Check
```bash
python .claude/skills/latex-formatting/scripts/latex_checker.py main.tex
```
Add `--page-limit N` when the venue has a page limit.

## Step 4: Assembly Check
```bash
python .claude/skills/paper-assembly/scripts/assembly_checker.py --dir . --verbose
```

## Output
Compilation status, page count, any warnings or errors, and quality check results.
