# The stale-index `wrote` flag is threaded by the CLI, not tracked by the protocol

`CommandResult.wrote` (`core/gain/genomic_resources/cli.py`) records whether
a resource-scoped run changed anything on disk. The three `resource-*`
entry points read it to log the INFO stale-index note: "run `grr_manage
repo-index` before publishing". The flag is set by hand in the CLI layer.
The manifest pass reports whether it saved a manifest. `_run_stats_core`
combines that with whether any statistics tasks were collected. And
`_regenerate_resource_pages` combines the info-page writes. GAIn does not
move this bookkeeping into the read-write protocol as a dirty flag that
`save_manifest`, `open_raw_file(mode="wt")` and the state writes would set.

## Why this is out of scope

The proposal (gain#783, deferred from gain#760 / PR #766) came from a real
hazard. A new write site in a resource-scoped command that forgets to set
`wrote` silently stops the note from firing for that kind of write, and no
test can list "all writes" to catch it. A protocol-level flag looked like
the single seam that would make `wrote` a reported fact.

It cannot be that seam. The biggest writer on this path is the statistics
build. Its tasks, including `_store_stats_hash`, receive the protocol as a
task argument. With `-j > 1`, `TaskGraphCli.create_executor` runs them on
`ProcessPoolTaskExecutor` or `DaskExecutor`, which pickle the protocol into
worker processes. A dirty flag set there never reaches the parent's
protocol object. So the stats path would still need the CLI-side reasoning
it has today: "tasks were collected, so files were written". The hazard
would be smaller but not gone. In exchange, every read-write protocol would
gain new state and surface for the sake of one advisory log line.

The flag is advisory. A missed note means an operator might publish without
running `repo-index`. It never corrupts a resource and never makes a run
report false success. The CLI-layer threading is good enough at that
stakes level. The invariant is written into the `CommandResult.wrote`
docstring instead: any new write site on a resource-scoped path must set
`wrote`.

## Prior requests

- iossifovlab/gain#783 -- "Track the stale-index 'wrote' fact at the
  protocol write seam instead of hand-threading it through the CLI"
