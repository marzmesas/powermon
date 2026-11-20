# Contributing

## Running the tests

```sh
python3 -m unittest discover -s tests        # daemon, ~270 tests, no dependencies
cd macos && xcodegen generate && \
  xcodebuild -project PowermonMenuBar.xcodeproj -scheme Powermon test
```

CI runs both on every push and pull request, on Python 3.10 and 3.13.

## The two constraints worth knowing before you start

**Standard library only, in the daemon.** `powermon.py`, `pwr` and the tests
import nothing that needs installing. The reference box runs the system
`/usr/bin/python3` so a broken conda environment cannot take monitoring down
with it, and the install instructions promise no `pip install`. If something
genuinely needs a dependency, it belongs behind an optional import that
degrades cleanly when it is absent — as the NVML reader does.

**Python 3.10 is the floor.** The reference box runs 3.10, which has no
`tomllib`, so `config.toml` is read by a bundled subset parser on that version
and by `tomllib` on newer ones. Any new config key must parse under both, which
in practice means no arrays and no nested tables — that is why
`trusted_proxies` is a comma-separated string rather than a TOML list. CI runs
3.10 specifically to keep that path honest.

## What the tests are for

The daemon reports numbers people use to decide what their electricity costs,
so the tests that matter most are the ones pinning honesty rather than
behaviour:

- **Zero and unknown are different.** A sensor that cannot be read must produce
  `None`, never `0`. A zero is a claim about the world; an unknown is not.
  Anything that turns one into the other is a bug even when the arithmetic
  works.
- **A failure must not make the numbers smaller.** Missing data reduces
  coverage, never consumption. A silently cheaper month is worse than a visible
  gap.
- **Recorded history is immutable.** Changing configuration changes what
  happens next, not what a closed day already cost.

If you change any of those, expect the tests to argue with you.

## Style

Match the surrounding code. Comments explain *why* — the tricky constant, the
trap avoided, the reason the obvious approach is wrong — rather than restating
what the line does. Commit messages say what was wrong and what the change does
about it; the body is the place for the reasoning that would otherwise be lost.
