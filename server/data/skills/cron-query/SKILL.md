---
name: cron-query
description: Query AIOpsOS cron job configuration details, execution history, and output
  logs. Use when the user asks about scheduled tasks, cron jobs, timed task status,
  execution results, or wants to audit what automated tasks exist in the platform.
version: 1.0.0
metadata:
  tags:
  - cron
  - scheduler
  - query
  - audit
  - operations
license: MIT
---

# Cron Query

Query cron job configuration, execution status, and output logs in AIOpsOS.

## When to Activate

Activate this skill when the user asks to:
- "How many cron jobs are there?" / "有多少定时任务？"
- "Show me all scheduled tasks" / "查看所有定时任务"
- "What's the status of task X?" / "任务X的状态怎么样？"
- "Show me the last execution output" / "查看最近执行输出"
- "Which tasks are enabled/disabled?" / "哪些任务启用了？"
- "When will task X run next?" / "任务X下次什么时候运行？"
- Any request involving listing, querying, or inspecting cron job status or results

## Available Tools

This skill provides three dedicated tools for querying cron jobs:

### `list_cron_jobs`
List all cron jobs with their basic status. Returns id, name, schedule, enabled, last_run, next_run, and a brief summary of last_output (first 200 chars). Use this as the first step for any cron query.

### `get_cron_job_detail`
Get full detail for a single cron job by name (fuzzy match) or ID. Returns all fields including the complete `last_output`. Use this when the user asks about a specific task.

### `list_cron_outputs`
List execution output files from the `data/cron_output/` directory. Optionally filter by job ID to see execution history for a specific task. Files are named `{job_id}_{timestamp}.md`.

## Workflow

### Query all tasks
1. Call `list_cron_jobs` to get an overview of all tasks
2. Present results in a table with: name, schedule, enabled status, last run, next run
3. Highlight any disabled tasks or tasks with errors

### Query a specific task
1. Call `get_cron_job_detail` with the task name or ID
2. Show: full configuration (name, schedule, prompt, skills), execution status (last_run, next_run, enabled), and last output
3. If the user wants more execution history, call `list_cron_outputs` with the job_id

### Audit task health
1. Call `list_cron_jobs` to get all tasks
2. Check for: disabled tasks, tasks where last_run is old, tasks with no next_run
3. Report findings with recommendations

## Answer Guidelines

- Use Chinese unless the user asks otherwise
- Present cron job lists as tables
- For schedules, translate cron expressions to human-readable descriptions when helpful
- If a task has never run (last_run is null), note it clearly
- If last_output contains error messages, flag them prominently
