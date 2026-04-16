---
name: smart-contract-yaml-pattern-audit
description: >-
  Audits smart contract source against vulnerability patterns supplied as YAML.
  Use when the user provides contract code and a YAML pattern list (e.g. id,
  name, severity, description, false_positives) and expects a structured JSON
  report of matches and rationale.
---

# Smart contract audit against YAML vulnerability patterns

## Inputs you will receive

1. **Smart contract(s)** — Solidity (or mixed) source: full files, snippets, or a single concatenated bundle. Note the compiler version and framework (Hardhat, Foundry, etc.) if stated.
2. **Patterns** — YAML, typically a list of objects. Common fields (adapt if the file uses different keys):
   - `id` — pattern identifier
   - `name` — short title
   - `severity` — e.g. Critical / High / Medium / Low / Informational
   - `description` — what constitutes the vulnerability and what to look for in code
   - `false_positives` — mitigations or situations that should **not** be reported   - Optional: `cross_cluster_risk`, tags, references, code hints

Treat the YAML as the **single source of truth** for what to check; do not invent extra vulnerability types unless the user asks.

## What you must do

1. **Parse the YAML** mentally: for each pattern, extract the checks implied by `description` and the exclusions from `false_positives`.
2. **Walk the contract code** (logic, modifiers, external calls, access control, asset flows, cross-chain / compose / paymaster / ERC quirks as relevant).
3. For **each pattern**, decide:
   - **Match** — the contract exhibits the risky behavior and `false_positives` does not clearly apply.
   - **No match** — behavior absent or adequately mitigated (cite mitigations when relevant).
   - **Uncertain** — insufficient code or context; say what is missing.
4. Prefer **precision over volume**: only report findings where the pattern text reasonably applies. When in doubt after applying `false_positives`, mark uncertain rather than forcing a finding.

## Output: JSON only for the findings block

Return a **single JSON object** (valid JSON, no markdown fences required unless the host asks). Schema:

```json
{
  "summary": {
    "patterns_checked": <number>,
    "findings_count": <number>,
    "contracts_reviewed": ["<file or label>", "..."]
  },
  "findings": [
    {
      "pattern_id": "<from YAML id>",
      "pattern_name": "<from YAML name>",
      "severity": "<from YAML severity>",
      "status": "match",
      "title": "<short human title>",
      "description": "<why this applies to the contract>",
      "evidence": [
        {
          "location": "<contract: function or line hint>",
          "snippet": "<relevant code excerpt, abbreviated if long>"
        }
      ],
      "recommendation": "<concrete fix or verification step>",
      "false_positive_check": "<brief note on why false_positives do not apply, or N/A>"
    }
  ],
  "non_matches": [
    {
      "pattern_id": "<id>",
      "pattern_name": "<name>",
      "note": "<one line: absent or mitigated>"
    }
  ],
  "uncertain": [
    {
      "pattern_id": "<id>",
      "pattern_name": "<name>",
      "reason": "<what is missing or ambiguous>"
    }
  ]
}
```

### Rules

- **`findings`**: include only entries with `"status": "match"`. Omit the array or use `[]` if there are no matches.
- **`non_matches`**: optional but useful when the user wants full coverage; cap length if there are hundreds of patterns (then summarize counts and list only matches + uncertain).
- **`uncertain`**: use when you cannot verify without tests, deployment config, or off-chain components.
- Preserve **exact** `pattern_id` and severity from YAML where present.

## Quality bar

- Tie each finding to **specific functions, state variables, or control flow** in the supplied code.
- Respect **`false_positives`**: if the contract clearly implements those mitigations, do not emit a finding for that pattern.
- Do not hallucinate file names; use `"unknown"` or the label the user gave if paths are missing.
