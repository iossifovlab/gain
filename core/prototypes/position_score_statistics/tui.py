#!/usr/bin/env python3
"""PROTOTYPE shell -- throwaway.  Drive the segment-statistics model by hand.

    python core/prototypes/position_score_statistics/tui.py
    python core/prototypes/position_score_statistics/tui.py --dump

Stdlib only, no gain imports, so it runs in a fresh worktree with no venv.
The logic lives in ``segment_stats``; this file is the disposable shell.
"""

from __future__ import annotations

import sys
import termios
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from segment_stats import (
    SEG_BIN_LABELS,
    FinalStats,
    Kind,
    RegionStats,
    Row,
    disagreements,
    run_oracle,
    run_scan,
    sweep_region_sizes,
    totals,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
OFF = "\x1b[0m"


def rows(spec: str, chrom: str = "1", value: float = 0.5) -> list[Row]:
    """``"10-20 30-40"`` -> rows.  ``"10-20@0.9"`` sets the value."""
    out = []
    for token in spec.split():
        span, _, val = token.partition("@")
        begin, _, end = span.partition("-")
        out.append(Row(
            chrom, int(begin), int(end or begin),
            float(val) if val else value))
    return out


# Each scenario is (title, what it is meant to expose, rows, kind, region size)
SCENARIOS: list[tuple[str, str, list[Row], Kind, int]] = [
    (
        "disjoint rows",
        "baseline -- nothing spans a boundary",
        rows("5-9@0.1 20-24@0.3 40-44@0.7"), "position", 10,
    ),
    (
        "row split by a boundary",
        "one row cut in half; covered must not double-count",
        rows("8-14@0.2"), "position", 10,
    ),
    (
        "adjacent rows, one segment",
        "begin == prev_end + 1 is legal and is ONE segment, not three",
        rows("5-9 10-14 15-19"), "position", 10,
    ),
    (
        "segment across three chunks",
        "the middle chunk is covered end to end (one_run) -- the case a "
        "naive head/tail merge gets wrong",
        rows("5-9 10-14 15-19 20-24 25-29 30-34"), "position", 10,
    ),
    (
        "whole contig one segment",
        "head and tail are the same run in EVERY chunk",
        rows("1-40"), "position", 10,
    ),
    (
        "two segments, one gap",
        "the gap decides where a chunk's open runs close",
        rows("1-14 25-40"), "position", 10,
    ),
    (
        "overlapping position rows",
        "REFUSED by the scan -- so a union fixture cannot be written on a "
        "position score (triage finding on gain#772)",
        rows("5-15 12-20"), "position", 10,
    ),
    (
        "fragments: overlap and nesting",
        "legal for a fragment score, and union semantics genuinely bites -- "
        "8-12 is nested inside 5-25",
        rows("5-25 8-12 14-20 30-34"), "fragment", 10,
    ),
    (
        "fragment across a boundary",
        "ONE fragment, weighed 1 per region that fetches it -- watch the "
        "value histogram against the oracle",
        rows("8-14@0.2"), "fragment", 10,
    ),
    (
        "two contigs",
        "adjacent extents on different contigs must never stitch",
        [*rows("5-19", chrom="1"), *rows("1-15", chrom="2")], "position", 10,
    ),
    (
        "single-position rows",
        "every row its own segment; the length histogram's first bin",
        rows("3 7 11 15 19 23"), "position", 10,
    ),
]


class App:
    """All the mutable state the shell owns."""

    def __init__(self) -> None:
        self.scenario = 0
        self.rows: list[Row] = []
        self.kind: Kind = "position"
        self.region_size = 10
        self.grouping = "left"
        self.detail = True
        self.sweep: list[tuple[int, list[str]]] | None = None
        self.load(0)

    def load(self, index: int) -> None:
        title, _why, scenario_rows, kind, size = SCENARIOS[index]
        self.scenario = index
        self.rows = list(scenario_rows)
        self.kind = kind
        self.region_size = size
        self.sweep = None
        self.title = title


def fmt_hist(hist: tuple[int, ...], labels: tuple[str, ...] | None) -> str:
    parts = []
    for index, count in enumerate(hist):
        if count == 0:
            continue
        label = labels[index] if labels else f"b{index}"
        parts.append(f"{label}:{count}")
    return " ".join(parts) if parts else f"{DIM}(empty){OFF}"


def fmt_region(region: RegionStats) -> str:
    extent = "whole contig" if region.start is None \
        else f"{region.start:>4}-{region.end:<4}"
    flags = []
    if region.one_run:
        flags.append(f"{YELLOW}one-run{OFF}")
    elif region.head_len or region.tail_len:
        flags.append(f"{DIM}open{OFF}")
    return (
        f"  {region.chrom}:{extent} "
        f"cov={region.covered:<4} closed={region.closed_count:<3} "
        f"head={region.head_len:<4} tail={region.tail_len:<4} "
        f"{' '.join(flags)}"
    )


def fmt_final(stats: FinalStats) -> list[str]:
    return [
        f"    covered   {BOLD}{stats.covered}{OFF}",
        f"    segments  {BOLD}{stats.segments}{OFF}",
        f"    lengths   {fmt_hist(stats.seg_hist, SEG_BIN_LABELS)}",
        f"    values    {fmt_hist(stats.value_hist, None)}"
        + (f"  {DIM}out-of-range:{stats.value_out_of_range}{OFF}"
           if stats.value_out_of_range else ""),
    ]


def render(app: App) -> str:
    _title, why, _r, _k, _s = SCENARIOS[app.scenario]
    size_label = "0 (unbounded)" if app.region_size == 0 else str(
        app.region_size)
    out = [
        f"{BOLD}POSITION-SCORE STATISTICS{OFF} "
        f"{DIM}-- coverage + segment lengths riding the histogram scan{OFF}",
        f"  scenario {app.scenario + 1}: {BOLD}{app.title}{OFF}",
        f"  {DIM}{why}{OFF}",
        "",
        f"  kind={CYAN}{app.kind}{OFF}  region_size={CYAN}{size_label}{OFF}"
        f"  fold={CYAN}{app.grouping}{OFF}",
        "",
        f"{BOLD}ROWS{OFF} {DIM}(chrom begin-end @value){OFF}",
    ]
    line = "  "
    for row in app.rows:
        line += f"{row.chrom}:{row.begin}-{row.end}@{row.value:g}  "
        if len(line) > 70:
            out.append(line)
            line = "  "
    out.extend((line, ""))

    regions, got, problems, refused = run_scan(
        app.rows, app.kind, app.region_size, app.grouping)
    oracle, oracle_refused = run_oracle(app.rows, app.kind)

    if refused or oracle_refused:
        out += [
            f"{RED}{BOLD}SCAN REFUSED THE RESOURCE{OFF}",
            f"  {RED}{refused or oracle_refused}{OFF}",
            "",
            f"  {DIM}Nothing is computed: the validator aborts the whole "
            f"statistics build.{OFF}",
        ]
        return "\n".join(out) + "\n\n" + keys()

    if app.detail:
        out.append(f"{BOLD}PER-REGION{OFF} {DIM}(what one scan task "
                   f"returns){OFF}")
        for region in regions[:12]:
            out.append(fmt_region(region))
        if len(regions) > 12:
            out.append(f"  {DIM}... {len(regions) - 12} more{OFF}")
        out.append("")

    got_total = totals(got)
    want_total = totals(oracle)
    bad = disagreements(got_total, want_total)

    out.append(f"{BOLD}MERGED{OFF} {DIM}(chunked, then stitched){OFF}")
    out += fmt_final(got_total)
    out.append("")
    out.append(f"{BOLD}ORACLE{OFF} {DIM}(one unbounded pass){OFF}")
    out += fmt_final(want_total)
    out.append("")

    if problems:
        for problem in problems:
            out.append(f"  {RED}merge refused: {problem}{OFF}")
    if bad:
        out.append(f"  {RED}{BOLD}MISMATCH{OFF}")
        for item in bad:
            out.append(f"    {RED}{item}{OFF}")
    elif not problems:
        out.append(f"  {GREEN}{BOLD}MATCH{OFF} {DIM}chunked == unchunked{OFF}")

    if len(got) > 1:
        out.append("")
        out.append(f"{BOLD}PER CONTIG{OFF}")
        for chrom in sorted(got):
            stats = got[chrom]
            out.append(
                f"  {chrom}: covered={stats.covered} "
                f"segments={stats.segments} "
                f"lengths={fmt_hist(stats.seg_hist, SEG_BIN_LABELS)}")

    if app.sweep is not None:
        out.append("")
        failures = [(size, bad) for size, bad in app.sweep if bad]
        if failures:
            out.append(f"  {RED}{BOLD}SWEEP: {len(failures)}/"
                       f"{len(app.sweep)} region sizes disagree{OFF}")
            for size, items in failures[:6]:
                out.append(f"    {RED}size {size}: {items[0]}{OFF}")
        else:
            out.append(f"  {GREEN}{BOLD}SWEEP: all {len(app.sweep)} region "
                       f"sizes 1..40 agree with the unchunked scan{OFF}")

    return "\n".join(out) + "\n\n" + keys()


def keys() -> str:
    return (
        f"{DIM}scenario{OFF} {BOLD}1-9,0{OFF} {DIM}or{OFF} {BOLD}n/p{OFF}   "
        f"{DIM}kind{OFF} {BOLD}k{OFF}   "
        f"{DIM}region size{OFF} {BOLD}+/-{OFF}   "
        f"{DIM}fold order{OFF} {BOLD}g{OFF}   "
        f"{DIM}sweep sizes{OFF} {BOLD}s{OFF}\n"
        f"{DIM}add row{OFF} {BOLD}a{OFF}   "
        f"{DIM}drop last{OFF} {BOLD}x{OFF}   "
        f"{DIM}per-region detail{OFF} {BOLD}v{OFF}   "
        f"{DIM}quit{OFF} {BOLD}q{OFF}"
    )


def read_key() -> str:
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def prompt(message: str) -> str:
    print(f"\n{message}", end="", flush=True)
    return sys.stdin.readline().strip()


def main() -> None:
    app = App()

    if "--dump" in sys.argv:
        for index in range(len(SCENARIOS)):
            app.load(index)
            app.sweep = sweep_region_sizes(app.rows, app.kind, app.grouping)
            print(render(app).rsplit("\n\n", 1)[0])
            print("=" * 72)
        return

    if not sys.stdin.isatty():
        print("not a terminal -- run me from a shell, or use --dump")
        return

    while True:
        print("\x1b[2J\x1b[H" + render(app), flush=True)
        key = read_key()
        if key in ("q", "\x03"):
            print()
            return
        if key in "1234567890":
            index = (int(key) - 1) % 10
            if index < len(SCENARIOS):
                app.load(index)
        elif key == "n":
            app.load((app.scenario + 1) % len(SCENARIOS))
        elif key == "p":
            app.load((app.scenario - 1) % len(SCENARIOS))
        elif key == "k":
            app.kind = "fragment" if app.kind == "position" else "position"
            app.sweep = None
        elif key in ("+", "="):
            app.region_size += 1
            app.sweep = None
        elif key in ("-", "_"):
            app.region_size = max(0, app.region_size - 1)
            app.sweep = None
        elif key == "g":
            order = ["left", "pairwise", "reverse"]
            app.grouping = order[(order.index(app.grouping) + 1) % 3]
            app.sweep = None
        elif key == "v":
            app.detail = not app.detail
        elif key == "s":
            app.sweep = sweep_region_sizes(app.rows, app.kind, app.grouping)
        elif key == "x":
            app.rows = app.rows[:-1]
            app.sweep = None
        elif key == "a":
            spec = prompt("row(s), e.g. '30-40@0.7' or '2:5-9' : ")
            chrom = "1"
            if ":" in spec:
                chrom, _, spec = spec.partition(":")
            try:
                app.rows = sorted(
                    app.rows + rows(spec, chrom=chrom),
                    key=lambda r: (r.chrom, r.begin))
                app.sweep = None
            except ValueError:
                pass


if __name__ == "__main__":
    main()
