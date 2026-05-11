---
# Specialist Profile Template
#
# Place specialist profiles in config/specialists/<id>.md
# Files starting with _ are ignored by the loader.
#
# Required frontmatter fields:
#   id:    unique identifier (matches workspace routing_rules)
#   name:  human-readable role name
#   tier:  provider tier for this specialist (nano / standard / deep)
#
# Optional frontmatter fields:
#   tools:       list of tool identifiers this specialist may use
#   data_sources: list of data sources this specialist can query
#   scope:       one-line boundary statement ("what I do / don't do")

id: example
name: Example Specialist
tier: standard
tools: []
data_sources: []
scope: "Answers questions about examples"
---

# Role Definition

Write the specialist's role definition below the frontmatter.
This entire section becomes the system prompt sent to the LLM
when the specialist is invoked.

Guidelines for writing specialist profiles:

1. **Be explicit about role boundaries.** What this specialist
   handles and what it defers to other specialists.

2. **Include domain knowledge** the LLM needs — formulas,
   thresholds, regulations, procedures. The LLM has general
   knowledge but your specialist needs specific operational context.

3. **Define output format.** Should the specialist respond with
   bullet points? A structured analysis? A recommendation with
   tradeoffs? Be specific.

4. **Reference data sources.** If the specialist should check
   an API, read a ledger, or query a database, say so.

5. **Keep it provider-agnostic.** Don't reference Claude, GPT,
   or any specific model. Any LLM should be able to execute
   this profile.

6. **Scope limits.** Include a "What I do NOT do" section so
   the specialist doesn't overstep into another specialist's domain.
