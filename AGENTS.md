# key-cli development rules

## Responsibilities

key-cli is an independent repository. It owns the `key shell`, `key ipc`, `key record`,
`key audio`, `key clipboard` and `key doctor` commands, their backend/process identity
behavior, packaging, and the machine-facing JSON protocol. Clavis consumes these public
interfaces; key-cli tests must not depend on `../clavis` or `../keytop`.

## Test policy

**Do not add tests automatically just because code was changed.** Fixing a bug does not
automatically require a regression test. Add a test only for stable CLI behavior, a public
JSON contract, backend input/output behavior, process identity/state semantics, or a
packaging artifact. Before adding one, explain why lint/build cannot cover it, why the
chosen test layer is appropriate, and why it does not freeze implementation details.

Allowed tests include public CLI parsing and entrypoint behavior, JSON response contracts,
clipboard MIME/backend behavior, recording/audio state transitions, process identity,
packaging and service artifacts. Tests must exercise input → backend → output behavior.

Forbidden tests use `grep`, `sed`, `awk`, regular expressions or source-text matching to
assert function names, file layout, class/module layout or implementation shape. Do not
create `test_*_architecture.sh`, `test_*_feature.sh` or `test_*_implementation.sh` tests.

## Protocol changes

When machine-facing JSON changes, update `docs/protocol.md` and the relevant contract
tests together. Preserve `schemaVersion`, `command`, `ok`, `error`, exit-code semantics,
and the documented record/audio/clipboard fields unless a deliberate protocol change is
being reviewed.

## Developer workflow

Install the declared development tools in a virtual environment:

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
python -m compileall src
python -m pytest
python -m build --wheel
scripts/check.sh
```

`ruff format` is the formatter; do not add Black, isort or flake8. Do not add mypy,
pyright, coverage thresholds or a large pre-commit framework in routine cleanup.
