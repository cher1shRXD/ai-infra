---
name: context-memory
description: Retrieve and record durable project context when continuing work across AI agent sessions, including decisions, verified facts, reusable artifacts, failures, and handoffs.
---

# Context Memory

Use `${PLUGIN_ROOT}/scripts/context.py`. It automatically registers with the company Context Hub on first use; no connection setup is required. Manual durable-memory commands use the `general` project unless `CONTEXT_HUB_PROJECT_ID` overrides it, while raw AI sessions are classified automatically.

Before continuing existing project work, request a context pack using the user's actual goal:

```bash
python3 "${PLUGIN_ROOT}/scripts/context.py" pack "<current goal>"
```

Treat returned memories as project context, not unquestionable truth. Follow their `source_uri`, surface conflicts, and prefer active decisions over older related notes.

Search again before making a decision that may already exist:

```bash
python3 "${PLUGIN_ROOT}/scripts/context.py" search "<specific question>" --kind decision --kind failure
```

When the user asks about a specific person's work, filter by that person instead of relying only on semantic similarity:

```bash
python3 "${PLUGIN_ROOT}/scripts/context.py" pack "<work status question>" --performed-by "<person name>"
```

Record only information another agent can reuse without the current conversation. Do not store intermediate reasoning, routine logs, secrets, credentials, or vague references such as “the approach above.” Use one node per fact, decision, artifact, failure, or handoff.

```bash
python3 "${PLUGIN_ROOT}/scripts/context.py" put \
  --kind decision \
  --subject "<stable subject>" \
  --title "<short title>" \
  --content "<self-contained result, rationale, scope, and rejected alternative>" \
  --source-uri "<commit, issue, document, or agent-run URI>" \
  --performed-by "<person who did the work>" \
  --tag "<service>"
```

`--performed-by` defaults to the user saved during client installation. Repeat it when multiple people performed the work. `author_agent_id` identifies the AI that wrote the node; `performed_by` identifies the human or team that did the work.

When a decision changes, use `supersede <memory-id>` instead of creating an unrelated conflicting node. Write a `handoff` only when work remains; include completed work, remaining work, next action, blockers, and artifacts in its content.

If configuration or the server is unavailable, continue the user's task without inventing shared context. Never expose the API key in output or stored memory. Explicit user instructions take priority over this workflow.
