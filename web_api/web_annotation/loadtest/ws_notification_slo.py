#!/usr/bin/env python
"""WS notification-responsiveness load harness (iossifovlab/gain#170).

Measures WebSocket push latency (a cheap sentinel notification's emit->receipt)
while a SUSTAINED stream of K in-flight slow-build annotate requests churns the
event loop. The notifications consumer is a SYNC WebsocketConsumer, so it does
not itself block the loop; the surface under test is the async-view + group_send
path. If an async view leaks blocking work onto the loop, the sampled WS latency
spikes; if the loop stays clean, it stays flat.

Clocking: CLIENT-SIDE round-trip stamping. The harness records ``t_send``
when it POSTs ``GET /api/_loadtest/ping?seq=N`` and ``t_recv`` when the
matching
``loadtest_ping:N`` frame arrives on the WS -- both in THIS process, so no
cross-process clock comparison. The ping route is test-only and gated under
``settings_e2e`` (see web_annotation/loadtest/ping_view.py).

Phases: a BASELINE window (no load) establishes idle push latency, then the
sustained build load runs for ``--duration`` while sampling continues
(UNDER-LOAD), then a short recovery. The report splits ws latency into
baseline/under_load so the doc shows the idle->load delta.

Uses ``aiohttp`` (already in the gain venv). No new dependency.

Usage:
    python -m web_annotation.loadtest.ws_notification_slo \\
        --base-url http://127.0.0.1:21011 \\
        --ws-url ws://127.0.0.1:21011/ws/notifications/ \\
        --concurrency 16 --duration 10 --ping-interval 0.1 \\
        --delay 0.4 --label async-K16 --email loadtest@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

VERSION_PATH = "/api/version"
ANNOTATE_PATH = "/api/single_allele/annotate"
LOGIN_PATH = "/api/login"
PING_PATH = "/api/_loadtest/ping"


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _summary(latencies_ms: list[float]) -> dict[str, Any]:
    """p50/p95/p99/max/min summary of a latency list (ms)."""
    ok = sorted(latencies_ms)
    return {
        "count": len(ok),
        "p50_ms": round(_percentile(ok, 50), 2) if ok else None,
        "p95_ms": round(_percentile(ok, 95), 2) if ok else None,
        "p99_ms": round(_percentile(ok, 99), 2) if ok else None,
        "max_ms": round(max(ok), 2) if ok else None,
        "min_ms": round(min(ok), 2) if ok else None,
    }


@dataclass
class WsSamples:
    """WS ping round-trip latencies, tagged baseline vs under-load."""

    baseline_ms: list[float] = field(default_factory=list)
    under_load_ms: list[float] = field(default_factory=list)
    missed: int = 0


async def _login(
    session: aiohttp.ClientSession, base_url: str, email: str,
    password: str, timeout: float,
) -> None:
    """Obtain a session cookie (unlimited user bypasses throttle+quota)."""
    async with session.post(
        f"{base_url}{LOGIN_PATH}", json={"email": email, "password": password},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"login returned {resp.status}: {body[:200]}")


async def _sanity_check(
    session: aiohttp.ClientSession, base_url: str, timeout: float,
) -> None:
    """Sanity-ping the server to fail fast on misconfiguration."""
    async with session.get(
        f"{base_url}{VERSION_PATH}",
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"sanity returned {resp.status}: {body[:200]}")


def _csrf_header(session: aiohttp.ClientSession) -> dict[str, str]:
    """Build the X-CSRFToken header from the session's csrftoken cookie."""
    for cookie in session.cookie_jar:
        if cookie.key == "csrftoken":
            return {"X-CSRFToken": cookie.value}
    return {}


async def _fire_annotate(
    session: aiohttp.ClientSession, base_url: str, pipeline_id: str,
    timeout: float,
) -> str:
    """Fire one POST annotate; return its status (or error tag)."""
    payload = {
        "annotatable": {"chrom": "chr1", "pos": "3"},
        "pipeline_id": pipeline_id,
    }
    try:
        async with session.post(
            f"{base_url}{ANNOTATE_PATH}", json=payload,
            headers=_csrf_header(session),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            await resp.read()
            return str(resp.status)
    except TimeoutError:
        return "timeout"
    except aiohttp.ClientError as exc:
        return f"error:{type(exc).__name__}"


async def _sustain_load(
    session: aiohttp.ClientSession, base_url: str, pipeline_id: str,
    *, concurrency: int, stop: asyncio.Event, timeout: float,
    statuses: list[str],
) -> None:
    """Keep ``concurrency`` annotate requests in flight until ``stop``."""
    inflight: set[asyncio.Future[str]] = set()
    while not stop.is_set():
        while len(inflight) < concurrency:
            inflight.add(asyncio.ensure_future(
                _fire_annotate(session, base_url, pipeline_id, timeout)))
        done, inflight = await asyncio.wait(
            inflight, timeout=0.05, return_when=asyncio.FIRST_COMPLETED)
        statuses.extend(fut.result() for fut in done)
    statuses.extend(
        fut for fut in
        await asyncio.gather(*inflight, return_exceptions=True)
        if isinstance(fut, str)
    )


async def _ws_receiver(
    ws: aiohttp.ClientWebSocketResponse, pending: dict[int, float],
    samples: WsSamples, load_active: asyncio.Event, stop: asyncio.Event,
) -> None:
    """Match ``loadtest_ping:N`` frames to their send time; record latency."""
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except ValueError:
            continue
        message = data.get("message", "")
        if not isinstance(message, str) or not message.startswith(
                "loadtest_ping:"):
            continue
        seq = int(message.split(":", 1)[1])
        sent = pending.pop(seq, None)
        if sent is None:
            continue
        latency_ms = (time.monotonic() - sent) * 1000.0
        if load_active.is_set():
            samples.under_load_ms.append(latency_ms)
        else:
            samples.baseline_ms.append(latency_ms)
        if stop.is_set() and not pending:
            return


async def _ws_pinger(
    session: aiohttp.ClientSession, base_url: str, pending: dict[int, float],
    *, interval: float, stop: asyncio.Event, timeout: float,
    samples: WsSamples,
) -> None:
    """POST the ping endpoint every ``interval`` s, stamping send time."""
    seq = 0
    while not stop.is_set():
        pending[seq] = time.monotonic()
        try:
            async with session.get(
                f"{base_url}{PING_PATH}", params={"seq": str(seq)},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                await resp.read()
        except (TimeoutError, aiohttp.ClientError):
            if pending.pop(seq, None) is not None:
                samples.missed += 1
        seq += 1
        await asyncio.sleep(interval)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run baseline -> sustained-load -> recovery; return the result record."""
    base_url = args.base_url.rstrip("/")
    samples = WsSamples()
    pending: dict[int, float] = {}
    statuses: list[str] = []
    stop_all = asyncio.Event()
    stop_load = asyncio.Event()
    load_active = asyncio.Event()

    jar = aiohttp.CookieJar(unsafe=True)
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(
            connector=connector, cookie_jar=jar) as session:
        await _sanity_check(session, base_url, args.timeout)
        if args.email:
            await _login(
                session, base_url, args.email, args.password, args.timeout)

        async with session.ws_connect(args.ws_url) as ws:
            receiver = asyncio.ensure_future(_ws_receiver(
                ws, pending, samples, load_active, stop_all))
            pinger = asyncio.ensure_future(_ws_pinger(
                session, base_url, pending, interval=args.ping_interval,
                stop=stop_all, timeout=args.timeout, samples=samples))

            # BASELINE: sample the idle loop before any load.
            await asyncio.sleep(args.baseline)

            # UNDER LOAD: sustain K in-flight builds for --duration.
            load_active.set()
            loader = asyncio.ensure_future(_sustain_load(
                session, base_url, args.pipeline_id,
                concurrency=args.concurrency, stop=stop_load,
                timeout=args.timeout, statuses=statuses))
            await asyncio.sleep(args.duration)
            stop_load.set()
            await loader
            load_active.clear()

            # RECOVERY: brief tail, then drain.
            await asyncio.sleep(args.recovery)
            stop_all.set()
            await pinger
            try:
                await asyncio.wait_for(receiver, timeout=args.timeout)
            except TimeoutError:
                receiver.cancel()
                samples.missed += len(pending)
            await ws.close()

    return {
        "label": args.label,
        "params": {
            "base_url": base_url,
            "ws_url": args.ws_url,
            "pipeline_id": args.pipeline_id,
            "concurrency_K": args.concurrency,
            "duration_seconds": args.duration,
            "ping_interval_seconds": args.ping_interval,
            "injected_build_delay_seconds": args.delay,
        },
        "annotate": {
            "fired": len(statuses),
            "ok_200": sum(1 for s in statuses if s == "200"),
            "non_200": sorted({s for s in statuses if s != "200"}),
        },
        "ws": {
            "missed_pings": samples.missed,
            "baseline": _summary(samples.baseline_ms),
            "under_load": _summary(samples.under_load_ms),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the harness CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:21011")
    parser.add_argument(
        "--ws-url", default="ws://127.0.0.1:21011/ws/notifications/")
    parser.add_argument("--pipeline-id", default="t4c8/t4c8_pipeline")
    parser.add_argument("--concurrency", "-K", type=int, default=16)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--baseline", type=float, default=1.0)
    parser.add_argument("--recovery", type=float, default=1.0)
    parser.add_argument("--ping-interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default="secret")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the harness, print the JSON result record."""
    args = build_parser().parse_args(argv)
    result = asyncio.run(run(args))
    if args.compact:
        json.dump(result, sys.stdout, separators=(",", ":"))
    else:
        json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
