# Where the skills went

The 4 skills of this pack were moved to `.claude/skills/` so that Claude Code can discover them.

| Old place | New place |
|---|---|
| `academic-research-skills/academic-paper/` | `.claude/skills/academic-paper/` |
| `academic-research-skills/academic-paper-reviewer/` | `.claude/skills/academic-paper-reviewer/` |
| `academic-research-skills/academic-pipeline/` | `.claude/skills/academic-pipeline/` |
| `academic-research-skills/deep-research/` | `.claude/skills/academic-deep-research/` (renamed, because `deep-research` is already used by agent-research-skills) |

The `shared/` folder stays here. The skills now point to it as `.claude/skills/academic-research-skills/shared/`.
