#!/usr/bin/env python
"""Cheap-endpoint-under-contention load harness (iossifovlab/gain#164).

Validates the async read-view migration (#163): under ``K`` concurrent
slow-build ``POST /api/single_allele/annotate`` requests (each triggering a GRR
pipeline build with the injected ``GPFWA_BUILD_DELAY_SECONDS`` delay), a cheap
sync endpoint -- ``GET /api/version`` -- must stay responsive. The cheap view
shares the single daphne ``thread_sensitive`` sync-view thread with the slow
builds; on the pre-#163 *sync* code that thread is parked inside
``future.result()`` while a build runs, so cheap-endpoint latency balloons. On
the async code the build is awaited OFF that thread, so the cheap endpoint
stays flat. The win is responsiveness-under-contention, not single-request
speed.

What it does
------------
1. Sanity-pings ``GET /api/version`` so a misconfigured server fails fast.
2. Concurrently:
   * fires ``K`` ``POST /api/single_allele/annotate`` requests against a COLD
     pipeline (each pays the injected build delay), and
   * samples ``GET /api/version`` latency every ``--sample-interval`` seconds.
3. Reports the cheap endpoint's p50/p95/p99 + timeout rate as JSON.

Cold pipeline
-------------
"Cold" means the first build of the target pipeline has not happened yet. The
cheapest reliable way to guarantee cold is to start a *fresh* server process per
run (each baseline run below starts its own daphne). Re-running against an
already-warm server still measures the *steady-state* cheap-endpoint latency,
but only the first run sees the build contention -- so prefer one fresh server
per measurement (see the docs runbook).

Dependencies
------------
Uses ``aiohttp`` (already in the gain venv). No new dependency added.

Usage
-----
    python -m web_annotation.loadtest.cheap_endpoint_slo \
        --base-url http://127.0.0.1:21011 \
        --pipeline-id t4c8/t4c8_pipeline \
        --concurrency 16 --timeout 30 --sample-interval 0.1

Parameterized: ``--concurrency`` (K), ``--timeout`` (per-request, also the SLO
breach threshold), ``--sample-interval``, ``--pipeline-id``, and an optional
``--label`` echoed into the JSON. The injected build delay is set on the SERVER
via ``GPFWA_BUILD_DELAY_SECONDS`` and echoed here via ``--delay`` for the
record.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

VERSION_PATH = "/api/version"
ANNOTATE_PATH = "/api/single_allele/annotate"
LOGIN_PATH = "/api/login"


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


@dataclass
class CheapSamples:
    """Latency samples for the cheap ``GET /api/version`` endpoint."""

    latencies_ms: list[float] = field(default_factory=list)
    timeouts: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return len(self.latencies_ms) + self.timeouts + self.errors

    def summary(self) -> dict[str, Any]:
        ok = sorted(self.latencies_ms)
        total = self.total
        return {
            "samples": total,
            "ok": len(ok),
            "timeouts": self.timeouts,
            "errors": self.errors,
            "timeout_rate": (self.timeouts / total) if total else 0.0,
            "p50_ms": round(_percentile(ok, 50), 2) if ok else None,
            "p95_ms": round(_percentile(ok, 95), 2) if ok else None,
            "p99_ms": round(_percentile(ok, 99), 2) if ok else None,
            "max_ms": round(max(ok), 2) if ok else None,
            "min_ms": round(min(ok), 2) if ok else None,
        }


async def _login(
    session: aiohttp.ClientSession,
    base_url: str,
    email: str,
    password: str,
    timeout: float,
) -> None:
    """Obtain a session cookie via POST /api/login.

    Logging in as an *unlimited* user (``set_unlimited``) is what removes the
    anonymous ``UserRateThrottle`` (10/min) and the single-allele quota from the
    measurement, so every one of the ``K`` requests actually reaches the build
    wait instead of short-circuiting with 429/quota. The cookie is stored on the
    session's cookie jar and carried by every later request.
    """
    url = f"{base_url}{LOGIN_PATH}"
    async with session.post(
        url, json={"email": email, "password": password},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise RuntimeError(
                f"login POST {url} returned {resp.status}: {body[:200]}")


async def _sanity_check(
    session: aiohttp.ClientSession, base_url: str, timeout: float,
) -> None:
    url = f"{base_url}{VERSION_PATH}"
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(
                f"sanity GET {url} returned {resp.status}: {body[:200]}")


async def _sample_cheap_endpoint(
    session: aiohttp.ClientSession,
    base_url: str,
    samples: CheapSamples,
    stop: asyncio.Event,
    *,
    timeout: float,
    interval: float,
) -> None:
    """Sample GET /api/version until ``stop`` is set."""
    url = f"{base_url}{VERSION_PATH}"
    while not stop.is_set():
        start = time.monotonic()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                await resp.read()
                elapsed_ms = (time.monotonic() - start) * 1000.0
                if resp.status == 200:
                    samples.latencies_ms.append(elapsed_ms)
                else:
                    samples.errors += 1
        except TimeoutError:
            samples.timeouts += 1
        except aiohttp.ClientError:
            samples.errors += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


def _csrf_header(session: aiohttp.ClientSession) -> dict[str, str]:
    """Build the ``X-CSRFToken`` header from the session's csrftoken cookie.

    DRF SessionAuthentication enforces CSRF on authenticated session POSTs, so
    a logged-in annotate must echo the ``csrftoken`` cookie (set by /api/login)
    in the ``X-CSRFToken`` header. Anonymous (cookieless) requests skip CSRF, so
    the header is simply absent then.
    """
    for cookie in session.cookie_jar:
        if cookie.key == "csrftoken":
            return {"X-CSRFToken": cookie.value}
    return {}


async def _fire_annotate(
    session: aiohttp.ClientSession,
    base_url: str,
    pipeline_id: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Fire one POST annotate; return status + elapsed (build contention)."""
    url = f"{base_url}{ANNOTATE_PATH}"
    payload = {
        "annotatable": {"chrom": "chr1", "pos": "3"},
        "pipeline_id": pipeline_id,
    }
    headers = _csrf_header(session)
    start = time.monotonic()
    try:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            await resp.read()
            return {
                "status": resp.status,
                "elapsed_ms": round((time.monotonic() - start) * 1000.0, 2),
            }
    except TimeoutError:
        return {"status": "timeout", "elapsed_ms": round(timeout * 1000.0, 2)}
    except aiohttp.ClientError as exc:
        return {"status": f"error:{type(exc).__name__}", "elapsed_ms": None}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run the harness and return the result record."""
    base_url = args.base_url.rstrip("/")
    samples = CheapSamples()
    stop = asyncio.Event()

    connector = aiohttp.TCPConnector(limit=0)  # no client-side cap
    jar = aiohttp.CookieJar(unsafe=True)  # accept cookies for 127.0.0.1
    async with aiohttp.ClientSession(
        connector=connector, cookie_jar=jar,
    ) as session:
        await _sanity_check(session, base_url, args.timeout)
        if args.email:
            await _login(
                session, base_url, args.email, args.password, args.timeout)

        sampler = asyncio.ensure_future(
            _sample_cheap_endpoint(
                session, base_url, samples, stop,
                timeout=args.timeout, interval=args.sample_interval,
            ),
        )
        # Give the sampler a moment to record a cheap-baseline before the burst.
        await asyncio.sleep(args.warmup)

        wall_start = time.monotonic()
        annotate_results = await asyncio.gather(*[
            _fire_annotate(
                session, base_url, args.pipeline_id, timeout=args.timeout,
            )
            for _ in range(args.concurrency)
        ])
        wall_elapsed = time.monotonic() - wall_start

        # Keep sampling briefly after the burst to capture any tail.
        await asyncio.sleep(args.cooldown)
        stop.set()
        await sampler

    annotate_statuses = [r["status"] for r in annotate_results]
    return {
        "label": args.label,
        "params": {
            "base_url": base_url,
            "pipeline_id": args.pipeline_id,
            "concurrency_K": args.concurrency,
            "injected_build_delay_seconds": args.delay,
            "request_timeout_seconds": args.timeout,
            "sample_interval_seconds": args.sample_interval,
        },
        "annotate": {
            "count": len(annotate_results),
            "ok_200": sum(1 for s in annotate_statuses if s == 200),
            "statuses": annotate_statuses,
            "wall_seconds": round(wall_elapsed, 2),
        },
        "cheap_endpoint": samples.summary(),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the harness CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:21011",
        help="Base URL of the running server.")
    parser.add_argument(
        "--pipeline-id", default="t4c8/t4c8_pipeline",
        help="Global GRR pipeline id to build (cold).")
    parser.add_argument(
        "--concurrency", "-K", type=int, default=16,
        help="Number of concurrent slow-build annotate requests.")
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Per-request timeout (s); also the SLO breach threshold.")
    parser.add_argument(
        "--sample-interval", type=float, default=0.1,
        help="Seconds between cheap-endpoint samples.")
    parser.add_argument(
        "--warmup", type=float, default=0.3,
        help="Seconds to sample the cheap endpoint before the burst.")
    parser.add_argument(
        "--cooldown", type=float, default=0.5,
        help="Seconds to keep sampling after the burst completes.")
    parser.add_argument(
        "--delay", type=float, default=None,
        help="Echo the server's GPFWA_BUILD_DELAY_SECONDS into the record.")
    parser.add_argument(
        "--label", default=None,
        help="Free-form label (e.g. 'async' / 'sync-baseline') for the record.")
    parser.add_argument(
        "--email", default=None,
        help="Log in as this user (an unlimited user bypasses throttle+quota).")
    parser.add_argument(
        "--password", default="secret",
        help="Password for --email login.")
    parser.add_argument(
        "--compact", action="store_true",
        help="Emit single-line JSON (for line-based aggregation).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the harness, and print the JSON result record."""
    parser = build_parser()
    args = parser.parse_args(argv)
    result = asyncio.run(run(args))
    if args.compact:
        json.dump(result, sys.stdout, separators=(",", ":"))
    else:
        json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
