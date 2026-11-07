# Where the skills went

This pack used to hold three identical copies of the same 27 skills (`skills/`, `.claude/skills/` and `scientific_writer/.claude/skills/`). The copies were checked file by file (same 374 files, same checksums) before the two extra copies were removed.

19 research-related skills moved to `.claude/skills/<name>/` so Claude Code can discover them. Two were renamed to avoid clashing with skills of the same name from agent-research-skills.

| Old name | New folder |
|---|---|
| citation-management | `.claude/skills/sw-citation-management/` |
| literature-review | `.claude/skills/sw-literature-review/` |

Moved with the same name: generate-image, hypothesis-generation, infographics, latex-posters, markitdown, paper-2-web, parallel-web, peer-review, pptx-posters, research-grants, research-lookup, scholar-evaluation, scientific-critical-thinking, scientific-schematics, scientific-slides, scientific-writing, venue-templates.

Left in `skills/` on purpose, so they do not load: clinical-decision-support, clinical-reports, treatment-plans, market-research-reports (off topic for paper writing) and document-skills/docx, pdf, pptx, xlsx (Claude already has built-in skills with these names). Move a folder up to `.claude/skills/` if you want it.

Several of these skills call paid services and need an `OPENROUTER_API_KEY` or `PARALLEL_API_KEY`: generate-image, infographics, markitdown (some features), paper-2-web, parallel-web, research-lookup, scientific-schematics, scientific-slides. They will not work without those keys.

Note: the `scientific_writer` Python program in this folder (a separate tool, not used by Claude Code here) copied its skills from `scientific_writer/.claude/skills/`. That copy was removed, so that separate program would need its skills restored before use.
