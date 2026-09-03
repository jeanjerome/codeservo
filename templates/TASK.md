# Task

## Goal

Describe one small, observable software change.

## Acceptance criteria

Each criterion names what decides it: `{gate: <name>}` hands it to a gate the
constitution declares, `{review}` leaves it to the independent reviewer, and a
criterion naming neither is reviewed.

- [AC1] State an outcome a gate measures. {gate: unit}
- [AC2] State an outcome only a reader can judge. {review}

## Out of scope

- Explicitly name adjacent changes that must not be made.

## Constraints

- Add task-specific constraints only. Stable repository rules belong in `.codeservo/constitution.toml`.
