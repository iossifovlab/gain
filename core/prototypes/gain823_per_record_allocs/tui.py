"""PROTOTYPE SHELL (gain#823) -- drive the per-record allocation variants.

Throwaway.  The logic under test lives in ``variants.py``; this is only a
terminal in front of it, so that the three removals can be switched on and off
by hand and the effect watched, rather than trusted from a table in an issue.

Run:  uv run python core/prototypes/gain823_per_record_allocs/tui.py

Keys are listed at the bottom of every frame.
"""
from __future__ import annotations

import os
import sys
import termios
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gain.genomic_resources.genomic_scores import (
    build_score_from_resource,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

import variants as V

GRR_DIR = os.environ.get(
    "PROTO823_GRR", "/Users/lubo/Work/seq-pipeline/grr_bench")
RESOURCE = os.environ.get("PROTO823_RESOURCE", "phastCons100way-bw")

# The issue's region first: chr21:10,000,000-11,000,000, 487,856 records.
REGIONS = [
    ("chr21", 10_000_000, 11_000_000),
    ("chr21", 10_000_000, 10_100_000),
    ("chr21", 10_000_000, 15_000_000),
]
PASS_CHOICES = [3, 7, 11]

B, D, R, INV = "\x1b[1m", "\x1b[2m", "\x1b[0m", "\x1b[7m"


class State:
    def __init__(self) -> None:
        self.cfg = V.ALL_ON
        self.region_i = 0
        self.passes_i = 1
        self.timings: list[V.Timing] = []
        self.verdict: V.Verdict | None = None
        self.verdict_of: str = ""
        self.note = "press [m] to measure, [v] to verify"

    @property
    def region(self) -> tuple[str, int, int]:
        return REGIONS[self.region_i]

    @property
    def passes(self) -> int:
        return PASS_CHOICES[self.passes_i]


def factories(score, region, cfgs, *, native: bool):
    chrom, begin, end = region
    out = [
        (cfg.label, (lambda c=cfg: V.segment_scores(
            score, chrom, begin, end, None, c)))
        for cfg in cfgs
    ]
    if native:
        out.append(("native pyBigWig.intervals()",
                    lambda: V.native_segments(score, chrom, begin, end)))
    return out


def render(state: State, score) -> None:
    print("\x1b[2J\x1b[H", end="")
    chrom, begin, end = state.region
    print(f"{B}gain#823 -- per-record allocations on "
          f"fetch_region_segment_scores{R}")
    print(f"{D}Do the two 'nobody reads it' allocations pay, individually "
          f"and together, on current master?{R}\n")

    print(f"  {B}resource{R}  {RESOURCE}  {D}({type(score).__name__} / "
          f"{type(score.table).__name__}){R}")
    print(f"  {B}region{R}    {chrom}:{begin:,}-{end:,}"
          f"   {B}passes{R} {state.passes}  {D}(interleaved, median){R}\n")

    print(f"  {B}candidate{R}")
    for key, flag, name, where in [
        ("1", state.cfg.raw_parser, "raw-parser",
         "table_bigwig.py  -- drop the _fetch 3-tuple"),
        ("2", state.cfg.inline_span, "inline-span",
         "genomic_scores.py -- drop _record_to_begin_end's tuple"),
        ("3", state.cfg.inline_extract, "inline-extract",
         "genomic_scores.py -- bypass get_score_values_from_record"),
    ]:
        mark = f"{INV} ON  {R}" if flag else f"{D} off {R}"
        print(f"    [{key}] {mark} {name:<15} {D}{where}{R}")
    print()

    if state.timings:
        base = next((t for t in state.timings if t.label ==
                     V.ALL_OFF.label), None)
        nat = next((t for t in state.timings if t.label.startswith("native")),
                   None)
        print(f"  {B}{'variant':<38}{'us/rec':>9}{'vs today':>10}"
              f"{'vs native':>11}{'records':>10}{R}")
        for t in state.timings:
            vs_base = (f"{base.us_per_rec / t.us_per_rec:.2f}x"
                       if base and t.us_per_rec else "-")
            vs_nat = (f"{t.us_per_rec / nat.us_per_rec:.2f}x"
                      if nat and nat.us_per_rec else "-")
            hot = B if t.label == state.cfg.label else ""
            print(f"  {hot}{t.label:<38}{t.us_per_rec:>9.3f}{vs_base:>10}"
                  f"{vs_nat:>11}{t.records:>10,}{R}")
        print()

    if state.verdict is not None:
        v = state.verdict
        if v.identical:
            print(f"  {B}identity{R}  {v.compared:,} segments element-wise "
                  f"IDENTICAL to today  {D}({state.verdict_of}){R}")
        else:
            print(f"  {B}identity{R}  {INV} DIFFERS {R} {v.first_diff}  "
                  f"{D}({state.verdict_of}){R}")
        print()

    print(f"  {D}{state.note}{R}\n")
    print(f"{D}[1/2/3]{R} toggle removal   {D}[m]{R} measure candidate   "
          f"{D}[a]{R} measure all 8   {D}[v]{R} verify identical")
    print(f"{D}[r]{R} region   {D}[p]{R} passes   {D}[0]{R} all off   "
          f"{D}[9]{R} all on   {D}[q]{R} quit")


def measure(state: State, score, cfgs: list[V.Config]) -> None:
    state.note = "measuring... (each pass drains the whole region)"
    render(state, score)
    cfgs = [c for c in cfgs if V.applicable(score, c)]
    fs = factories(score, state.region, cfgs, native=V.is_bigwig(score))
    state.timings = V.measure(fs, state.passes)
    state.note = f"measured {state.passes} interleaved passes"


def verify(state: State, score) -> None:
    state.note = "verifying element-wise..."
    render(state, score)
    chrom, begin, end = state.region
    ref = V.segment_scores(score, chrom, begin, end, None, V.ALL_OFF)
    cand = V.segment_scores(score, chrom, begin, end, None, state.cfg)
    state.verdict = V.verify_identical(ref, cand)
    state.verdict_of = state.cfg.label
    state.note = "verified"


def read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def auto(score, passes: int) -> None:
    """Non-interactive: measure all eight, verify each, print, exit.

    The same variants the TUI drives, run once without a keyboard -- for a
    run left unattended, and for pasting the answer onto the issue.
    """
    chrom, begin, end = REGIONS[0]
    cfgs = [c for c in V.ALL_CONFIGS if V.applicable(score, c)]
    native = V.is_bigwig(score)
    print(f"{RESOURCE}  {chrom}:{begin:,}-{end:,}   "
          f"{passes} interleaved passes\n")
    print("warming page cache...")
    V.time_one_pass(
        lambda: score.fetch_region_segment_scores(chrom, begin, end))

    timings = V.measure(
        factories(score, REGIONS[0], cfgs, native=native), passes)
    base = timings[0]
    nat = timings[-1] if native else None
    print(f"\n{'variant':<38}{'us/rec':>9}{'vs today':>10}{'vs native':>11}")
    for t in timings:
        vs_base = f"{base.us_per_rec / t.us_per_rec:.2f}x" if t.us_per_rec \
            else "-"
        vs_nat = f"{t.us_per_rec / nat.us_per_rec:.2f}x" \
            if nat and nat.us_per_rec else "-"
        print(f"{t.label:<38}{t.us_per_rec:>9.3f}{vs_base:>10}{vs_nat:>11}")
    print(f"\n{base.records:,} records\n")

    for cfg in cfgs[1:]:
        v = V.verify_identical(
            V.segment_scores(score, chrom, begin, end, None, V.ALL_OFF),
            V.segment_scores(score, chrom, begin, end, None, cfg))
        status = f"IDENTICAL ({v.compared:,})" if v.identical \
            else f"DIFFERS: {v.first_diff}"
        print(f"{cfg.label:<38}{status}")


def main() -> None:
    repo = build_genomic_resource_repository({
        "id": "proto823", "type": "directory", "directory": GRR_DIR,
    })
    score = build_score_from_resource(repo.get_resource(RESOURCE))

    if "--auto" in sys.argv:
        passes = 7
        if "--passes" in sys.argv:
            passes = int(sys.argv[sys.argv.index("--passes") + 1])
        with score.open() as opened:
            auto(opened, passes)
        return

    state = State()
    with score.open() as opened:
        # Warm the page cache once, so the first measurement is not the
        # only one paying for cold I/O.
        print("warming page cache over the region...")
        chrom, begin, end = state.region
        V.time_one_pass(lambda: opened.fetch_region_segment_scores(
            chrom, begin, end))

        while True:
            render(state, opened)
            key = read_key().lower()
            if key in ("q", "\x03"):
                print("\x1b[2J\x1b[H", end="")
                return
            if key == "1":
                state.cfg = state.cfg._replace(
                    raw_parser=not state.cfg.raw_parser)
                state.verdict = None
            elif key == "2":
                state.cfg = state.cfg._replace(
                    inline_span=not state.cfg.inline_span)
                state.verdict = None
            elif key == "3":
                state.cfg = state.cfg._replace(
                    inline_extract=not state.cfg.inline_extract)
                state.verdict = None
            elif key == "0":
                state.cfg, state.verdict = V.ALL_OFF, None
            elif key == "9":
                state.cfg, state.verdict = V.ALL_ON, None
            elif key == "r":
                state.region_i = (state.region_i + 1) % len(REGIONS)
                state.timings, state.verdict = [], None
                state.note = "region changed -- measure again"
            elif key == "p":
                state.passes_i = (state.passes_i + 1) % len(PASS_CHOICES)
            elif key == "m":
                measure(state, opened, [V.ALL_OFF, state.cfg])
            elif key == "a":
                measure(state, opened, V.ALL_CONFIGS)
            elif key == "v":
                verify(state, opened)


if __name__ == "__main__":
    main()
