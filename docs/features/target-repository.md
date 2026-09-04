# Preparing a Target Repository

The target repository owns the envelope a change has to stay inside, and it
owns it in one committed file. `TASK.md` supplies the temporary delta. Both are
control inputs: they are versioned before the actuation they constrain, and
`.codeservo/**` is a protected path, so the actuator measured against them
cannot rewrite them.

## The constitution

```bash
cd /path/to/project
codeservo init
```

Then edit `.codeservo/constitution.toml`. It is the repository's stable executable constitution.

```toml
version = 1

[scope]
protected = [".codeservo/**", ".github/workflows/**"]
max_changed_files = 20
max_diff_lines = 800

[[gate]]
name = "lint"
phase = "quick"
command = "make lint"
timeout_seconds = 120
baseline = true

[[gate]]
name = "unit"
phase = "quick"
command = "make test-unit"
timeout_seconds = 300
baseline = true

[[gate]]
name = "full"
phase = "full"
command = "make check"
timeout_seconds = 900
baseline = true

[[gate]]
name = "acceptance"
phase = "quick"
command = "pytest -q \"$CODESERVO_SENSOR_PATH\""
timeout_seconds = 300
baseline = false
sensor = "my-project/health-contract"

[review]
blocking_severities = ["blocker", "major"]
```

Commit the constitution. CodeServo refuses to start from a dirty source repository, freezes the constitution at run start, and treats changes under protected paths as control errors.

What a gate may declare beyond a command and a timeout is in
[feedback sensors](feedback-sensors.md) — the document it writes about what it
measured, and the report formats read without an adapter — and in
[ratchets](ratchets.md).

## Measuring through a locked environment

A constitution may declare an execution provider. A gate then names a task
instead of a shell command, and the controller builds the command line, always
naming the manifest of the tree that gate measures. Two providers answer the
same port: `pixi`, whose lockfile is `pixi.lock`, and `mise`, whose lockfile is
`mise.lock`. The lockfile lies beside the manifest and is not configurable.

```toml
[execution]
provider = "pixi"           # or "mise", with manifest = "mise.toml"
manifest = "pyproject.toml"
environment = "default"

[[gate]]
name = "unit"
phase = "quick"
task = "test"
timeout_seconds = 300
baseline = true
```

Before the baseline the controller freezes the manifest and lockfile digests
and the inventory the lockfile resolves to; a lockfile that disagrees with its
manifest ends the run before any checkout exists. Where the tools are installed
depends on the provider. Pixi keeps them in the tree it measures, so the
environment is installed into the candidate after the isolated checkout is
created and before the first actuation — never into the source repository,
whose environment must already be there for a baseline task gate. mise keeps
them outside every tree, under `<state-dir>/providers/mise/`, so the controller
installs them once, before the baseline, and both trees measure through the
controller's directory; the operator's own mise installation, configuration and
trust store are never read. mise declares no named environments, so
`environment` can only be `default` there.

Every gate process runs under variables that forbid the provider to resolve or
install: `PIXI_OFFLINE`, `PIXI_NO_INSTALL` and `PIXI_FROZEN` for pixi;
`MISE_OFFLINE`, `MISE_LOCKED` and the four auto-install settings for mise, with
the one manifest read by name and the search for configuration stopped above
it. A measurement can neither resolve nor install, whichever provider it runs
through.

A constitution that declares no provider keeps shell gates unchanged.

## Writing one task

```markdown
