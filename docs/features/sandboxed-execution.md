# Sandboxed Execution

*The design is in [ARCHITECTURE.md](../ARCHITECTURE.md#confinement); this is
what a run applies and why.*

A coding agent that can execute code, run builds and touch the file system gets
a profile it did not ask for and cannot negotiate. The profile says what a
process may not reach and what it may only read. Which mechanism enforces it
belongs to the host — `sandbox-exec` on macOS, Bubblewrap on Linux — and the
target repository declares its execution provider but never its confinement: a
candidate able to name the mechanism holding it would be negotiating its own
cage. The mechanism is established by applying a profile rather than inferred
from the platform name, and a host with none refuses the run.

## What each process may reach

Gates marked `baseline=false` must reference an external acceptance sensor.
CodeServo freezes the sensor and its digest before baseline verification, then
provides its path only to the gate process through `CODESERVO_SENSOR_PATH`.

Every gate runs under a controller-owned profile that makes the run directory
read-only, so a gate reads the frozen sensor it was given but cannot write
anywhere in the record it produces; its own log files are opened by the
controller before the gate starts. Each gate is confined to the tree it
measures — the source repository during the baseline, the isolated checkout
afterwards — whose Git metadata and provider directory are readable and not
writable. Reading survives that confinement: `git status`, `diff`, `log`,
`ls-files` and `rev-parse` all work, and a task gate runs on the environment it
cannot write.

What a profile cannot express is caught afterwards. The controller recomputes
every frozen sensor digest and compares the candidate's state across each
measurement phase, so a gate that changed the tree it was measuring ends the run
even when every gate exited zero — as a control failure naming the phase, not as
a failing gate.

The implementer receives a shallow checkout with no remote, so target repository
history is absent. It runs inside a controller-owned macOS sandbox profile that
denies reads and writes to source sensors, frozen sensors, run evidence, state
repository metadata, and the source repository's Git object store, denies every
write to the source repository, and leaves the candidate's Git metadata and
provider directory readable but not writable. The reviewer runs under the same
profile extended with a write denial covering the whole candidate worktree, so
its read-only nature is mechanical rather than instructed. The actuator receives
only the controller-selected gate output on the next iteration. CodeServo fails
closed where `sandbox-exec` is unavailable.

## Requirements of the host

macOS carries `sandbox-exec` and needs nothing else. Linux needs `bubblewrap`
installed and unprivileged user namespaces allowed: Ubuntu 24.04 restricts them
through AppArmor and its bubblewrap package installs no profile of its own, so
`bwrap` fails setting up its uid map until
`kernel.apparmor_restrict_unprivileged_userns` is lifted. The controller finds
that out by applying a profile, not by reading the platform name.

## What the record says

Every confined process records the mechanism that held it, the denied paths and
the read-only paths, resolved and absolute. A profile that could not be applied
is not a verdict about the candidate: neither mechanism can report one through
an exit code, since that code belongs to the measured command, so each adapter
answers separately whether the command ran under the profile at all, and one
that did not stops the run.
