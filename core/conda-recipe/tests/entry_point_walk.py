"""Walk every console script and entry point an installed gain-core declares.

Run with the Python of the environment gain-core is installed in; no
arguments, nothing else on ``sys.path``. Everything walked is discovered
from the installed distribution's metadata, so a script or plugin added
to ``core/pyproject.toml`` is covered without touching this file; only
the ``gain.`` group prefix is known here. A run dependency missing from
the environment surfaces as an ImportError on the entry point that
needs it.

Two callers share this file: the recipe's ``tests:`` section runs it in
rattler-build's test phase against the just-built package, and
``gain-release``'s post-publish smoke runs it against ``gain-core`` as
installed from anaconda.org with the documented channel line.
"""
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

# Keep the per-item lines in order in a captured (non-tty) log.
sys.stdout.reconfigure(line_buffering=True)

entry_points = list(distribution("gain-core").entry_points)
failures = []

# Console scripts are looked up next to this interpreter, not on PATH:
# the environment need not be activated, and what runs is the script
# this environment installed rather than whichever one PATH finds.
bin_dir = Path(sys.executable).resolve().parent
scripts = [e for e in entry_points if e.group == "console_scripts"]
for ep in scripts:
    script = bin_dir / ep.name
    if not script.exists():
        print(f"console script {ep.name} --help: FAILED")
        failures.append(f"{ep.name}: not installed at {script}")
        continue
    result = subprocess.run(
        [str(script), "--help"], capture_output=True, text=True,
        check=False,
    )
    status = "ok" if result.returncode == 0 else "FAILED"
    print(f"console script {ep.name} --help: {status}")
    if result.returncode != 0:
        failures.append(
            f"{ep.name} --help exited {result.returncode}:\n"
            f"{result.stderr}")

plugins = [e for e in entry_points if e.group.startswith("gain.")]
for ep in plugins:
    try:
        ep.load()
        print(f"entry point {ep.group}:{ep.name}: ok")
    # Any exception from a plugin's import is a finding to report, not
    # a reason to stop walking the rest of the surface.
    except Exception as exc:  # ruff: ignore[blind-except]
        print(f"entry point {ep.group}:{ep.name}: FAILED")
        failures.append(f"{ep.group}:{ep.name}: {exc!r}")

groups = sorted({e.group for e in plugins})
print(
    f"walked {len(scripts)} console scripts and "
    f"{len(plugins)} entry points in {len(groups)} groups: "
    f"{', '.join(groups)}")
assert scripts, "no console_scripts found in gain-core metadata"
assert plugins, "no gain.* entry points found in gain-core metadata"
if failures:
    print("\n\n".join(failures))
    sys.exit(1)
