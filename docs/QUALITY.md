# Engineering and Coding Rules

What a change to the controller must respect. The shape of the code these rules
govern is in [ARCHITECTURE.md](ARCHITECTURE.md); the measurements that check
them are in [COMMANDS.md](COMMANDS.md).

## Design

- Keep CodeServo small, explicit, and independent from its actuator. Backend-specific behavior stays in one module loaded explicitly by `actuators.load_actuator`; it exposes `run_implementer`, `run_reviewer`, and `describe_isolation`, and reads no provider cache: what a run may name is the model catalogue the package publishes, and an adapter reports tokens without pricing them.
- Prefer the Python standard library when practical. Keep tests and architecture checks deterministic.
- A closed vocabulary is a `StrEnum`: its members are the whole of it, so one
  is added in a single place, and a member is the string it serialises to. A
  string read from a declaration becomes its vocabulary by being looked up in
  it, never by comparing equal to a member. What a record holds is the value,
  so a document read back carries plain strings that compare equal to the
  members they were written from, and nothing asks whether a value is an
  instance of a vocabulary. `tests/test_vocabularies.py` walks the package and
  holds every vocabulary it declares to exactly that.
- A document a run writes is a frozen dataclass built on `domain.document`,
  declared where it is owned: the record in `controller/document.py`, the
  journal event, the observation, what a backend's actuation and review carry.
  It is constructed once by naming every field, nothing edits it afterwards,
  and `to_document` renders it as the JSON object a record, a journal line or
  a digest is taken over. Field sets the code enforces are read from those
  shapes. A document that closes over its own digest is built in two stages,
  and the types say so: `unsigned.signed()` is the only way to reach the
  signed one.
- Absence and a measured null are different statements, and a record keeps
  them apart. `UNSET` says a field has nothing to report and leaves it out of
  the document; `None` says a measurement was made and answered nothing.
  Filling an absent field with null makes the record assert what no
  measurement produced.
  `tests/test_documents.py` walks the package and holds every document to
  being frozen, built by name, and rendered as what JSON carries.
- The actuator port states its three operations as call signatures. Backend
  behaviour stays in the adapter; nothing else names a flag or a stream field.
- The confinement port states the same way what a mechanism answers: why this
  host cannot apply a profile, the command to run under one, and whether the
  command ran under it at all. A profile names no mechanism, and nothing
  outside `runtime/seatbelt.py` and `runtime/bubblewrap.py` names a rule
  syntax or an exit code either of them owns. The mechanism belongs to the
  host and is established by applying a profile, never inferred from the
  platform name; a target repository declares its execution provider and never
  its confinement.
- A test guarded on an operating system where it means a capability reports
  nothing on every other host while passing. Guard it on the capability, name
  the reason the host gave, and let the one assertion that the capability is
  missing fail rather than skip. A refusal is evidence only when the same
  profile also permits something, so a suite measuring a denial asserts a
  reading beside it.
- Use four-space indentation and type hints. Use `snake_case` for modules, functions, and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Preserve dependency direction toward the domain layer. Group imports as standard library, third party, then local.
- Name tests `test_*.py` and test methods `test_<behavior>`. Use `unittest` in `codeservo` and `pytest` in deployment-tracker.
- A parsing boundary is stated as a property, not only as cases. What arrives
  at one is whatever a file, a gate or a backend produced, so the rule is
  written over every input of a shape: a constitution is read or refused by
  name, a document reaches one of four classifications, a record reaches a
  verdict. Properties are `unittest` cases like the others and run under
  `tests/properties.py`, which derandomises the seed so a gate gives the same
  verdict twice and moves Hypothesis's database and cache out of the tree a
  gate may not write into.
- A boundary another party supplies is also searched, because a property is
  written over the shapes someone thought of and generated text is always
  text. What arrives at those three — the constitution, a run directory handed
  to `verify-run`, the provider's own output — is bytes, so `tools/fuzz.py`
  drives them as bytes under coverage guidance. An input it finds becomes a
  named case in the suite, where it stays a regression test after the search
  moves on.
- A gate adapter projects and never decides. The exit code stays the tool's, a
  document claiming to have passed beside a non-zero exit is a contradiction the
  controller refuses, and nothing may come between a tool that runs the tests
  and the streams the controller handed the gate: those bytes are what gets fed
  back to the actuator, and `tests/isolation_harness` recognises an active
  confinement through the directory of its own descriptors. A tool whose text is
  the projection's only source may be captured, and none of those runs a test.
- What crosses into the record carries what the record declares. A field typed
  `str | None` holds a string or nothing whatever the producer wrote there,
  and a number is one JSON can carry back: `json.loads` reads `NaN` and
  `json.dumps` writes it as a literal nothing else accepts. A value of another
  shape is not a measurement the record can hold, and reporting nothing is
  the honest answer.
- Treat `.codeservo/constitution.toml`, external sensors, and protected paths as
  controller-owned configuration. Never expose sensor source or run evidence to
  the actuator. Do not commit secrets.

## Commit and Pull Request Guidelines

Use `<type>: <description>` with `feat`, `fix`, `refactor`, `docs`, `test`,
`chore`, `perf`, or `ci`. Describe code behavior, not planning metadata. Do not
add AI attribution or `Co-Authored-By` trailers. Pull requests should explain
changed behavior, list verification commands, link applicable issues or
acceptance criteria, and include screenshots only for visible UI changes.
