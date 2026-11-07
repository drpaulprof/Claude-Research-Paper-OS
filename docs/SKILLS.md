# Skills Reference

Skills are specialized knowledge and tool packages stored in `.claude/skills/`. Claude draws on them automatically based on the task at hand. You do not need to invoke them manually.

This document covers all 8 skill packs and the 65 skills that load.

Claude Code only discovers a skill at `.claude/skills/<name>/SKILL.md`. For that reason every skill below lives in its own top-level folder, for example `.claude/skills/latex-formatting/`. The original pack folders keep their README, LICENSE and a `MOVED_SKILLS.md` note.

---

## How Skills Work

Skills are loaded when Claude's auto-trigger map (in `.claude/CLAUDE.md`) matches your request to a skill. For example, when you say "write the introduction," Claude loads the paper-writing-section skill, the humanizer skill, the prose-writing skill, and the writing quality check skill. All run in sequence as part of the writing workflow.

Some skills include Python scripts. These require Python 3.10+ (one script, `academic-paper-strategist/scripts/gap_analysis.py`, needs 3.12+) and the dependencies listed in each skill's directory. Most skills are pure-prompt skills that work without any external dependencies.

---

## Skill Pack 1: agent-research-skills (31 skills)

**Author:** [lingzhi227](https://github.com/lingzhi227)
**Location:** `.claude/skills/<skill-name>/` (pack notes in `.claude/skills/agent-research-skills/`)

This is the largest and most comprehensive pack. It covers the entire research pipeline from idea generation through submission and rebuttal.

### Research Phase Skills

| Skill | What It Does |
|---|---|
| **deep-research** | 6-phase systematic literature survey with search scripts for Semantic Scholar and arXiv |
| **literature-search** | Multi-source academic search with CrossRef, Semantic Scholar, and arXiv download scripts |
| **literature-review** | Structured literature review with thematic organization |
| **github-research** | Searches GitHub for relevant codebases and implementations |
| **novelty-assessment** | Evaluates whether your contribution is genuinely novel against existing work |
| **idea-generation** | Structured brainstorming for research ideas and hypotheses |
| **research-planning** | Produces a research plan with milestones and gap analysis |

### Writing Phase Skills

| Skill | What It Does |
|---|---|
| **paper-writing-section** | Section-specific writing tips (abstract, intro, methods, results, discussion, conclusion) |
| **related-work-writing** | Thematic organization for related work with compare-and-contrast structure |
| **survey-generation** | RAG-based subsection writing with automatic citation validation |
| **rebuttal-writing** | Templates and strategies for peer review rebuttals |
| **paper-revision** | Handles major/minor revisions based on reviewer feedback |

### Quality and Review Skills

| Skill | What It Does |
|---|---|
| **self-review** | 3-persona automated review (NeurIPS review form format) |
| **citation-management** | BibTeX validation, deduplication, auto-fix, and consistency checks |
| **latex-formatting** | LaTeX formatting fixes and pre-submission checks |
| **paper-compilation** | pdflatex + bibtex pipeline with automatic error fixing |
| **paper-assembly** | End-to-end pipeline completeness check |
| **backward-traceability** | Verifies every claim traces back to a source |

### Technical Skills

| Skill | What It Does |
|---|---|
| **math-reasoning** | Derivations, notation tables, statistical test selection |
| **symbolic-equation** | Symbolic math manipulation and equation formatting |
| **algorithm-design** | Algorithm pseudocode generation and complexity analysis |
| **figure-generation** | Publication-quality matplotlib figures |
| **table-generation** | LaTeX booktabs tables |
| **slide-generation** | Presentation slides from paper content |
| **data-analysis** | Statistical analysis and data visualization |

### Engineering Skills

| Skill | What It Does |
|---|---|
| **experiment-design** | Experimental protocol design with controls and baselines |
| **experiment-code** | Generates experiment code from protocols |
| **code-debugging** | Debugging experiment code |
| **paper-to-code** | Extracts implementable code from paper descriptions |
| **atomic-decomposition** | Breaks complex tasks into atomic subtasks |
| **excalidraw-skill** | Diagram generation for papers and presentations |

---

## Skill Pack 2: academic-research-skills (4 skills)

**Author:** [Imbad0202](https://github.com/Imbad0202)
**Location:** `.claude/skills/academic-paper/`, `academic-paper-reviewer/`, `academic-pipeline/`, `academic-deep-research/` (shared files in `.claude/skills/academic-research-skills/shared/`)
**License:** GPL-3.0

### Skills

| Skill | What It Does |
|---|---|
| **academic-paper** | Writing quality checklist with 25+ anti-AI checks. The `references/writing_quality_check.md` file is the master quality gate for all writing |
| **academic-paper-reviewer** | Multi-perspective peer review with 0-100 rubrics across multiple dimensions |
| **academic-pipeline** | Orchestrates the full research-to-paper pipeline |
| **academic-deep-research** | 13-agent research team with fact-check, Socratic and systematic review modes. Renamed from `deep-research` so it does not clash with the agent-research-skills skill of that name |

The `academic-paper/references/writing_quality_check.md` file is referenced throughout the system as the authoritative list of banned AI terms and quality standards.

---

## Skill Pack 3: creative-writing-skills (6 skills)

**Author:** [haowjy](https://github.com/haowjy)
**Location:** `.claude/skills/cw-*/`
**License:** Apache-2.0

These skills handle prose quality. Academic writing is still writing, and these skills ensure it reads naturally.

| Skill | What It Does |
|---|---|
| **cw-prose-writing** | Narrative flow, sentence rhythm, and natural prose style. Ensures varied sentence structure and smooth transitions |
| **cw-story-critique** | Critiques flow, pacing, and readability. Identifies where a section drags, jumps, or loses the reader |
| **cw-brainstorming** | Idea generation and creative exploration for framing research narratives |
| **cw-official-docs** | Technical and official documentation writing style |
| **cw-router** | Routes writing tasks to the appropriate sub-skill |
| **cw-style-skill-creator** | Creates custom writing style profiles for consistent voice |

---

## Skill Pack 4: academic-paper-skills (2 skills)

**Author:** [lishix520](https://github.com/lishix520)
**Location:** `.claude/skills/academic-paper-strategist/` and `.claude/skills/academic-paper-composer/`
**License:** MIT

| Skill | What It Does |
|---|---|
| **academic-paper-strategist** | Plans paper structure, identifies key arguments, maps section dependencies, and produces an outline with quality checkpoints at each stage |
| **academic-paper-composer** | Drafts paper content following the strategist's plan, with built-in quality gates that pause and check work at paragraph boundaries |

The strategist-then-composer workflow is useful for initial drafting. The strategist plans, then the composer executes with frequent quality checks.

---

## Skill Pack 5: claude-scientific-writer (19 skills)

**Author:** [K-Dense-AI](https://github.com/K-Dense-AI)
**Location:** `.claude/skills/<skill-name>/` (pack notes in `.claude/skills/claude-scientific-writer/`)
**License:** MIT

The pack shipped three identical copies of 27 skills. The two extra copies were removed after a file-by-file check. 19 skills load.

| Skill | What It Does | Paid key? |
|---|---|---|
| **scientific-writing** | Core scientific manuscript writing (IMRaD, reporting guidelines) | No |
| **sw-citation-management** | Google Scholar and PubMed search, DOI to BibTeX (renamed from `citation-management`) | No |
| **sw-literature-review** | Systematic reviews across PubMed, arXiv, bioRxiv and more (renamed from `literature-review`) | No |
| **peer-review** | Systematic peer review toolkit | No |
| **scholar-evaluation** | ScholarEval quality scoring | No |
| **scientific-critical-thinking** | Rigor, bias and evidence quality checks | No |
| **hypothesis-generation** | Testable hypotheses and predictions | No |
| **venue-templates** | LaTeX templates and rules for journals, conferences and grants | No |
| **research-grants** | NSF, NIH, DOE and DARPA proposals | No |
| **latex-posters** | LaTeX research posters | No |
| **pptx-posters** | Alternate poster guide with an HTML poster template | No |
| **markitdown** | Convert files to Markdown | Some features |
| **research-lookup** | Research lookup through Parallel or Perplexity | Yes |
| **parallel-web** | Web search and extraction through Parallel | Yes |
| **generate-image** | AI image generation | Yes |
| **infographics** | AI infographics | Yes |
| **scientific-schematics** | AI scientific diagrams | Yes |
| **scientific-slides** | AI slide decks | Yes |
| **paper-2-web** | Paper to website, video or poster | Yes |

Left in the pack folder on purpose, so they do not load: clinical-decision-support, clinical-reports, treatment-plans, market-research-reports, and document-skills (docx, pdf, pptx, xlsx, which clash with Claude's built-in skills of the same names).

---

## Skill Pack 6: paper-writer-skill (1 skill)

**Author:** [kgraph57](https://github.com/kgraph57)
**Location:** `.claude/skills/paper-writer-skill/`

Provides section-by-section templates and helper scripts for writing academic papers. Includes a SKILL.md with detailed instructions, reference materials in `references/`, and utility scripts in `scripts/`.

---

## Skill Pack 7: humanizer (1 skill)

**Location:** `.claude/skills/humanizer/`
**License:** MIT

The AI pattern detection and removal skill. Based on Wikipedia's "Signs of AI writing" guide maintained by WikiProject AI Cleanup.

Checks 25 pattern categories.

1. Undue emphasis on significance, legacy, and broader trends
2. Undue emphasis on notability and media coverage
3. Superficial analyses with -ing endings
4. Promotional and advertisement-like language
5. Vague attributions and weasel words
6. Outline-like "Challenges and Future Prospects" sections
7. Overused AI vocabulary (25+ specific words)
8. Copula avoidance ("serves as" instead of "is")
9. Negative parallelisms ("Not only...but...")
10. Rule of Three overuse
11. Elegant variation (synonym cycling)
12. False ranges ("from X to Y")
13. Em dash overuse
14. Boldface overuse
15. Inline-header vertical lists
16. Title case in headings
17. Emojis in professional text
18. Curly quotation marks
19. Collaborative communication artifacts
20. Knowledge-cutoff disclaimers
21. Sycophantic/servile tone
22. Filler phrases
23. Excessive hedging
24. Generic positive conclusions
25. Hyphenated word pair overuse

The skill includes a two-pass process. First, rewrite to remove patterns. Second, run the "What makes this obviously AI generated?" audit and fix remaining tells.

---

## Skill Pack 8: content-research-writer (1 skill)

**Location:** `.claude/skills/content-research-writer/`

Assists in writing content that is grounded in research. Conducts background research, adds citations, improves hooks, iterates on outlines, and provides feedback on each section. Useful as a supplement to the main paper-writing skills when you need deeper research integration during the writing phase.

---

## Skill Interaction Map

Skills do not run in isolation. Here is how they chain together during common tasks.

**Writing a section:**
```
paper-writing-section (section tips)
  → cw-prose-writing (sentence rhythm)
  → scientific-writing (conventions)
  → academic-paper / academic-paper-composer (quality gates)
  → humanizer (AI pattern check)
  → cw-story-critique (flow check)
```

**Reviewing the paper:**
```
self-review (3-persona review)
  → academic-paper-reviewer (rubrics)
  → humanizer (pattern scan)
  → citation-management (reference check)
  → backward-traceability (claim verification)
```

**Researching a topic:**
```
deep-research (systematic search)
  → literature-search (multi-source)
  → novelty-assessment (gap check)
  → literature-review (organize findings)
```

**Preparing for submission:**
```
paper-assembly (completeness)
  → latex-formatting (format fixes)
  → paper-compilation (build + verify)
  → humanizer (final pass)
  → citation-management (final check)
```
