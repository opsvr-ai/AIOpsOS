# Built-in Agents & Tools — Design Spec

**Date:** 2026-05-01
**Status:** Approved

## Summary

Add `is_builtin` flag to Agent and Tool models. Mark seed items as built-in. Redesign Agent page and Tool marketplace UI with modern SaaS style.

## Design Decisions

| Decision | Choice |
|----------|--------|
| Visual style | Modern SaaS |
| Agent page layout | Stats overview + card/table toggle |
| Tool marketplace layout | Same pattern as agent page |
| Builtin restrictions | No delete, no deactivate, limited edit |

## Backend

### Model changes
- Agent: add `is_builtin = Column(Boolean, default=False, nullable=False)`
- Tool: add `is_builtin = Column(Boolean, default=False, nullable=False)`

### Seed data
- All agents in `_auto_seed_agents()` set `is_builtin=True`
- All builtin tools set `is_builtin=True`

### API protection
- DELETE agent/tool: 403 if `is_builtin=True`
- PATCH is_active=False: 403 if `is_builtin=True`
- PATCH agent name/system_prompt: 403 if builtin + not admin
- PATCH tool name/description/config: 403 if `is_builtin=True`

### Schema
- AgentOut, ToolItem: add `is_builtin: bool`

## Frontend

### AgentsPage
- Stats bar (total/active/inactive/builtin)
- Card/table view toggle
- Builtin badge (gold Tag)
- Hide delete/deactivate for builtin agents
- Gradient avatar based on name hash

### ToolsPage
- Same stats bar + view toggle
- Builtin badge on cards
- Hide delete/deactivate/edit for builtin tools
- Keep existing type tabs
