# Skills and lead skills — differences and rules

## Locations

| Directory | UI field | Purpose |
|-----------|----------|---------|
| `skills/` | **Skill** | Default prompt library for agents. |
| `lead_skills/` | **Lead skill** | Alternative “lead” prompts; **exclusive** with Skill. |

Files are **`*.md`**. The catalog is built from **direct children** of each folder (non-recursive listing for top-level entries; see `discover_catalogs` in `dashboard/server.py`).

## Mutual exclusion (UI)

- If **Lead skill** is set, the **Skill** dropdown is **disabled** and the lead file wins.
- If both are empty, the backend injects a **short default** system message: generic auditor role + JSON output expectation (`_build_system_prompt` in `agent_runner.py`).

## How they are loaded

1. The user selects a **name** that matches a file stem (e.g. `generic-security-rules` → `generic-security-rules.md`).
2. `_read_skill_file` resolves `skills/<name>.md` or **`lead_skills/<name>.md`** depending on which field is active.
3. The **entire file text** becomes the **system prompt** for that agent run—no automatic templating beyond what you write in markdown.

## Semantic difference

There is **no technical difference** in the engine: only the **directory** and **UI field** change. Conventionally:

- **`skills/`** — reusable audit playbooks (rules, checklists, output shape).
- **`lead_skills/`** — “lead auditor” or domain-specific **primary** instructions when you want a clearly separated set of prompts (e.g. DeFi lending vs DEX) without mixing filenames under `skills/`.

You can use either folder for any content; keep naming consistent for your team.

## JSON output

Agents are expected to return structured **`AgentOutput`** JSON. Your skill files should **explicitly** require valid JSON matching the schema (vulnerabilities, ideas, tool usage flags, etc.). If the model struggles, disable strict `response_format` for that provider (`supports_response_format: false` in `conf.yaml`) and rely on instruction + parsing.

## Tips

- Keep one responsibility per file; reference external docs in prose rather than duplicating huge blobs.
- Version or name files by protocol type (`skills/defi-amm.md`) for clarity.
- Do not commit secrets inside skill files; use `conf.yaml` + env vars for API keys.

## See also

- [`agent-audit.md`](agent-audit.md)
- [`../readme.md`](../readme.md) — quick start and `conf.yaml` overview
