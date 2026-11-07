# Detailed Setup Guide

This guide walks through installation, configuration, and first use of Claude Research Paper OS.

---

## Prerequisites

### Required

- **Claude Code** -- The CLI tool from Anthropic. Install it following the [official docs](https://docs.anthropic.com/en/docs/claude-code/overview).

### Optional (but recommended)

- **Docker** -- For LaTeX compilation inside a container. Install from [docker.com](https://www.docker.com/). If you do not have Docker, you can use a local LaTeX installation instead.

- **Python 3.10+** -- Some skills include Python scripts for citation validation, literature search, and formatting checks. The scripts use Python 3.10 syntax (for example `str | None` type hints and `match` statements), so Python 3.9 or older will not run them. One script, `academic-paper-strategist/scripts/gap_analysis.py`, needs Python 3.12+. Install from [python.org](https://www.python.org/). Check your version with `python3 --version`.

- **LaTeX distribution** -- If not using Docker. TeX Live (Linux/Mac) or MiKTeX (Windows).

---

## Installation

### Step 1: Clone the repo

```bash
git clone https://github.com/ARAVINDAN20/Claude-Research-Paper-OS.git
cd Claude-Research-Paper-OS
```

### Step 2: Copy .claude/ into your project

```bash
# If starting a new paper
mkdir my-paper && cd my-paper
cp -r ../Claude-Research-Paper-OS/.claude/ .

# If you already have a paper project
cd /path/to/your/paper
cp -r /path/to/Claude-Research-Paper-OS/.claude/ .
```

### Step 3: Set up a template (for new papers)

```bash
# IEEE journal paper
cp ../Claude-Research-Paper-OS/templates/ieee-journal/* .

# Conference paper
cp ../Claude-Research-Paper-OS/templates/conference-paper/* .

# Or use your own main.tex and references.bib
```

### Step 4: Configure for your paper

Open `.claude/CLAUDE.md` and edit the configuration block near the top.

```yaml
title: "Your Actual Paper Title"
authors: "Your Name, Co-Author Name"
target_venue: "IEEE Access"
format: "IEEEtran"
compile_command: >
  docker run --rm -v "$(pwd)":/data blang/latex:ubuntu bash -c
  "cd /data && pdflatex -interaction=nonstopmode main.tex &&
  bibtex main && pdflatex -interaction=nonstopmode main.tex &&
  pdflatex -interaction=nonstopmode main.tex"
```

If you are not using Docker, change the compile command to your local setup.

```yaml
compile_command: >
  pdflatex -interaction=nonstopmode main.tex &&
  bibtex main &&
  pdflatex -interaction=nonstopmode main.tex &&
  pdflatex -interaction=nonstopmode main.tex
```

### Step 5: Python dependencies (optional)

The scripts the 6 agents use need only Python 3.10+ and its standard library. Nothing extra has to be installed for them.

- `citation-management/scripts/validate_citations.py`
- `deep-research/scripts/search_semantic_scholar.py`
- `deep-research/scripts/search_arxiv.py`
- `literature-search/scripts/search_crossref.py`
- `literature-search/scripts/download_arxiv_source.py`
- `latex-formatting/scripts/latex_checker.py` and `clean_latex.py`
- `paper-assembly/scripts/assembly_checker.py`
- `paper-compilation/scripts/compile_paper.py`

Some other skills need extra packages, only if you use them. For example `figure-generation` needs `matplotlib`, `data-analysis` needs `numpy`, `scipy` and `pandas`, some PDF scripts need `pypdf` or `PyMuPDF`, and `sw-citation-management/scripts/extract_metadata.py` needs `requests`. Check the `import` lines of a script before running it.

The search scripts need internet access to api.semanticscholar.org, api.crossref.org and export.arxiv.org. Downloading arXiv source files (`download_arxiv_source.py`) also needs arxiv.org.

### Step 5b: Optional API keys

- **Semantic Scholar** (free, optional) -- pass `--api-key` to the search script for higher rate limits.
- **OpenRouter or Parallel** (paid) -- needed only by these claude-scientific-writer skills: research-lookup, parallel-web, generate-image, infographics, scientific-schematics, scientific-slides, paper-2-web and some markitdown features. Set `OPENROUTER_API_KEY` or `PARALLEL_API_KEY` only if you want to pay for them.

### Step 6: Verify the setup

```bash
cd /path/to/your/paper
claude
```

In Claude Code, say:

```
List all available agents and skills
```

Claude should show you the 6 agents and list the 65 skills. Start `claude` from the project's top folder, because all script paths are relative to it.

---

## Project Structure After Setup

Your paper project should look like this.

```
your-paper/
├── main.tex              <- Your paper
├── references.bib        <- Bibliography
├── figures/              <- Figures (optional)
├── WRITING_RULES.md      <- Writing constraints (optional)
├── .claude/
│   ├── CLAUDE.md         <- Master config (edit this)
│   ├── agents/
│   │   ├── paper-writer.md
│   │   ├── paper-reviewer.md
│   │   ├── citation-checker.md
│   │   ├── humanizer-check.md
│   │   ├── latex-compiler.md
│   │   └── deep-researcher.md
│   └── skills/
│       ├── <skill-name>/SKILL.md     <- 65 skill folders (latex-formatting/, humanizer/, ...)
│       ├── agent-research-skills/    <- pack README, LICENSE, MOVED_SKILLS.md
│       ├── academic-research-skills/ <- pack README, LICENSE, shared/
│       ├── creative-writing-skills/  <- pack README, LICENSE
│       ├── academic-paper-skills/    <- pack README, LICENSE, examples
│       └── claude-scientific-writer/ <- pack README, LICENSE, unused skills
```

---

## Customizing Writing Rules

The system comes with default writing rules in `.claude/CLAUDE.md`. You can add a `WRITING_RULES.md` file in your project root for paper-specific rules. The paper-writer agent reads both files.

A template is provided at `templates/WRITING_RULES.md`. Copy it to your project root and edit it.

```bash
cp /path/to/Claude-Research-Paper-OS/templates/WRITING_RULES.md .
```

---

## Compilation Options

### Docker (recommended)

The default compile command uses the `blang/latex:ubuntu` Docker image. Pull it once.

```bash
docker pull blang/latex:ubuntu
```

Then compilation works automatically through the latex-compiler agent.

### Local TeX Live

If you prefer a local installation.

```bash
# Ubuntu/Debian
sudo apt install texlive-full

# macOS
brew install --cask mactex

# Then update compile_command in .claude/CLAUDE.md
```

### Overleaf

If you use Overleaf, you can still use this system for writing and reviewing. Just copy the output from Claude into Overleaf. The agents work on the text content regardless of how you compile it.

---

## Troubleshooting

### Claude does not see the agents

Make sure `.claude/` is in your project root (the directory where you run `claude`). Claude Code looks for `.claude/CLAUDE.md` in the current working directory.

### Python scripts fail

Make sure you started `claude` from the project's top folder, because the script paths start with `.claude/skills/`. The core scripts need only the Python standard library. If a different script fails with `ModuleNotFoundError`, install only the package it names.

### Docker compilation fails

Make sure Docker is running and you have pulled the image.

```bash
docker pull blang/latex:ubuntu
docker run --rm -v "$(pwd)":/data blang/latex:ubuntu pdflatex --version
```

### Skills directory is too large

If you want a lighter setup, you can remove skill folders you do not need. At minimum, keep these.

- The agent-research skills the agents call: `citation-management/`, `deep-research/`, `literature-search/`, `latex-formatting/`, `paper-assembly/`, `paper-writing-section/`
- `humanizer/` -- AI pattern removal
- `academic-paper/` -- Writing quality check (`references/writing_quality_check.md`)

### Claude does not auto-trigger skills

Verify that `.claude/CLAUDE.md` exists and contains the Skill Auto-Trigger Map section. Claude reads this file at session start. If you renamed it or moved it, Claude will not find it.
