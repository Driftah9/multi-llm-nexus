# Project Agents

Roles and specialist configurations for this project.

## Primary Orchestrator

- **Name:** [NEXUS_AGENT_NAME]
- **Model:** [PRIMARY_PROVIDER_MODEL]
- **Role:** Chief of Staff — coordinates project work, routes tasks to specialists

## Specialists (Optional)

Define task-specific agents below. Each has a focused prompt and preferred model tier.

### Example: Research Specialist

```yaml
name: Research
model_tier: standard
prompt: |
  You are researching [topic] for this project.
  Focus on: [what to research]
  Output format: [how to present findings]
```

### Example: Code Review

```yaml
name: Code Reviewer
model_tier: deep
prompt: |
  Review [type] code for this project.
  Check: [what to check]
  Standards: [what standards apply]
```

## Adding More

To add a specialist:
1. Define the role above
2. Link it in your project communication: `@bot [specialist-name] [task]`
3. The orchestrator will dispatch to the right model tier
