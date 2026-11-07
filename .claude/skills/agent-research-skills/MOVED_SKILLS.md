# Where the skills went

The 31 skills of this pack were moved from `agent-research-skills/skills/<name>/` to `.claude/skills/<name>/` so that Claude Code can discover them (it only looks one folder deep).

Script paths in the skill files were changed from `~/.claude/skills/<name>/` to `.claude/skills/<name>/`, so start `claude` from the project's top folder.

The original README, CHANGELOG, design notes, `commands/research.md` and `install.sh` stay here for reference. `install.sh` checks `~/.claude/skills/` (a global install) and is not needed for this project.
