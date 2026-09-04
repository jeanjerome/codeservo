# Context Engineering

The context an actuator gets is constructed, never accumulated. Each iteration
starts a fresh process: there is no session to carry rot forward, no memory
between runs, and no MCP server loading tools nobody asked for. What reaches
the model is the frozen task, the frozen constitution as the candidate is
allowed to see it, a shallow checkout with no remote and no history, and the
feedback the controller wrote.

## What the feedback carries

A failing gate is returned with its name, its command, its exit code, then —
for a gate that wrote a valid document — its summary, its findings with the
path and line each one names (the first forty, and the count of the rest) and
its metrics, then the last 120 lines of stdout and stderr. The output tail sits
behind the document because an adapter projects each failure into one line
while the full trace is what a correction reads. A gate answering with an exit
code alone is returned as it always was.

The prompt names every acceptance criterion by its id and by the control that
decides it. From the second iteration it carries one line per iteration already
spent — scope, quick gates passed, gates that failed with the summary of their
document — before the full feedback of the last one. A ratchet that broke names
the gate, the metric and both values.

Nothing is fed back on the path of an escalation, because nothing there is the
candidate's to correct.

## What each backend is run with

`--actuator` selects the agent CLI that proposes the change, and reviews it
unless `--review-actuator` names another one. It accepts `claude` and `codex`,
defaults to `$CODESERVO_ACTUATOR`, and falls back to `claude`.

Both backends run without session persistence and without MCP servers, so a run
depends on the frozen task, the frozen constitution, the controller feedback,
and the repository content. Claude Code additionally runs with user memory,
settings, hooks, skills and custom commands disabled; plugin and subagent
definitions installed on the machine stay registered, but the actuator tool set
excludes the tools that would reach them.

| | implementer | reviewer |
| --- | --- | --- |
| `claude` | `--print --safe-mode --strict-mcp-config --disable-slash-commands --no-session-persistence`, tools `Bash,Read,Write,Edit,NotebookEdit` | same, tools `Bash,Read`, structured output validated against the review schema |
| `codex` | `exec --ephemeral --ignore-user-config` | same, plus the frozen output schema |

An effort reaches Claude Code as `--effort` and Codex as
`-c model_reasoning_effort=<level>`, handed over unchanged. The record keeps
the keys the command actually carried beside what the session reported about
itself, and never fills the second from the first.

A confined process has one confinement authority and it is the controller's
profile, so an actuator under it must not sandbox itself: Codex runs with
`--sandbox danger-full-access` and Claude Code with its permission checks
bypassed. The rule was first forced by macOS, which refuses to apply a seatbelt
profile inside another one, and it holds on either host. See
[sandboxed execution](sandboxed-execution.md).
