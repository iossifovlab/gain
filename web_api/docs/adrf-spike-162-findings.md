# adrf async-vehicle spike — findings (iossifovlab/gain#162)

**Status: GO.** `adrf` (async-DRF) cleanly hosts an `async def` DRF handler
against our real `WebAnnotationAuthentication` + `UserRateThrottle` stack, runs
the blocking session I/O off the event loop, and preserves DRF exception
mapping. Proceed with the real read-view conversion in #163.

**Scope of this GO (do not overclaim).** This spike proved that adrf offloads
the sync auth/throttle stack OFF the event loop and preserves DRF exception
mapping; it did NOT itself `await` a long-running GRR pipeline build off-loop —
that end-to-end behavior (the actual fix for the daphne event-loop stall) is
validated in #163, not here.

## Why this spike exists

Under daphne every sync DRF view runs on one shared `thread_sensitive`
worker thread. Read endpoints park it for seconds on GRR pipeline builds
(`future.result()`), which caused the `gain-web-e2e` #746 flake. The fix
(#163+) is to make read views `async` and `await` the build off-thread — but
plain DRF 3.17.1 ships **zero** async support (`async def` handlers do not work
in `rest_framework.views.APIView`). This spike de-risks `adrf` as the vehicle
before investing in the conversion.

## Dependency

`adrf==0.1.13` added to `web_api/pyproject.toml` and `uv.lock` (pulls one
transitive dep, `async-property==0.2.2`). Installs and builds via the standard
`uv add adrf` workflow; no native deps.

## How adrf works (evidence from its installed source)

`adrf/views.py::APIView.dispatch` branches on `view_is_async` and, for an async
view, runs `async_dispatch` (`adrf/views.py:50-82`):

```python
async def async_dispatch(self, request, *args, **kwargs):
    ...
    try:
        await sync_to_async(self.initial)(request, *args, **kwargs)   # <-- key
        handler = getattr(self, request.method.lower(), ...)
        if iscoroutinefunction(handler):
            response = await handler(request, *args, **kwargs)
        else:
            response = await sync_to_async(handler)(request, *args, **kwargs)
    except Exception as exc:
        response = self.handle_exception(exc)
    self.response = self.finalize_response(...)
```

Two load-bearing facts:

1. **`initial()` is wrapped in `sync_to_async`.** DRF's `initial()`
   (`rest_framework/views.py:405`) runs `perform_authentication` →
   `request.user` → `_authenticate` → our **sync**
   `WebAnnotationAuthentication.authenticate` (session read + `session.save()`),
   plus `check_permissions` and `check_throttles`. Because the whole `initial()`
   call is `await sync_to_async(self.initial)(...)`, **all of that blocking I/O
   runs in a thread-pool worker, not on the event-loop thread.** No override of
   `perform_authentication` is required — the fallback contemplated in #162 is
   unnecessary.

2. **DRF exception mapping is preserved.** Both `initial()` and the async
   handler run inside `try/except Exception → handle_exception`, identical to
   DRF's sync path. A `ValidationError`/`NotFound` raised inside an `async def`
   handler is therefore rendered by DRF's normal machinery (400/404), not as a
   500.

adrf's `check_throttles` (`adrf/views.py:235`) splits throttles into sync/async
buckets; our sync `UserRateThrottle` goes through `check_sync_throttles`, called
from within the `sync_to_async(self.initial)` worker — so throttling is off-loop
too.

## Probe + proof

Throwaway probe (delete with #163):

- `web_annotation/spike_adrf_probe.py` — an `adrf.views.APIView` at
  `/api/_spike/adrf-probe`, using instrumented subclasses of the **real**
  `WebAnnotationAuthentication` and `UserRateThrottle` that record the thread
  ident of each phase into `PROBE_RECORD`. The probe also raises
  `ValidationError`/`NotFound` on `?raise=validation|notfound`.
- `web_annotation/tests/test_spike_adrf_probe.py` — async tests
  (`@pytest.mark.asyncio` + Django `AsyncClient`) under the existing
  `django_db(transaction=True)` setup.

Note: the probe's `ProbeAuthentication` forces an **unconditional**
`request.session.save()` purely for instrumentation (to record the save-thread
on both the authenticated and anonymous paths). The real
`WebAnnotationAuthentication.authenticate` only saves the session when
`session_key is None` — the probe is not representative of that save cadence.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| **1. Real auth/throttle works through adrf** | PASS | `test_probe_anonymous_returns_200` (200, `authenticated=False`) and `test_probe_authenticated_returns_200` (200, `authenticated=True` via `aforce_login`) |
| **2. Auth/session I/O + throttle run OFF the event loop** | PASS | `test_probe_auth_and_throttle_run_off_event_loop`: the async handler body runs on `threading.get_ident()` (the loop thread); `auth_thread`, `session_save_thread` (a real `session.save()`), and `throttle_thread` are all non-`None` and `!=` the loop thread |
| **3. DRF exception mapping survives async handler** | PASS | `test_probe_validation_error_maps_to_400` (400 + reason text "spike validation boom"), `test_probe_not_found_maps_to_404` (404) — neither 500 |

Verification (commands from `gain/CLAUDE.md`, run in `web_api/`):

- `pytest web_annotation/tests/test_spike_adrf_probe.py` → **5 passed**
- full suite `pytest -n 5 web_annotation/tests` → **532 passed** (no regression)
- `ruff check` (touched files) → clean
- `mypy --config-file mypy.ini` (touched files) → clean (added a
  `[mypy-adrf.*] ignore_missing_imports` stanza — adrf ships no `py.typed`)
- `pylint --rcfile=pylintrc` (touched files) → 10.00/10

## Recommendation for #163: **GO**

adrf is a viable vehicle. The conversion can:

- Subclass `adrf.views.APIView` for read views and make the handler `async def`.
- Keep `authentication_classes = [WebAnnotationAuthentication]` and
  `throttle_classes = [UserRateThrottle]` **unchanged** — adrf offloads them
  off-loop for free via `sync_to_async(self.initial)`. No `perform_authentication`
  override / `sync_to_async(lambda: request.user)` fallback is needed.
- Rely on DRF's existing exception mapping (`AnnotationBaseView.get_pipeline`'s
  `ValidationError`/`NotFound` keep rendering as 400/404 from an async handler).
- `await` the GRR pipeline build off-thread so the event-loop thread is never
  blocked on `future.result()`.

### Caveats to carry into #163

- adrf 0.1.13 has no type stubs; the `[mypy-adrf.*]` ignore stanza must stay.
- `AnnotationBaseView` mixes sync helpers (`async_to_sync(channel_layer...)`,
  blocking GRR access). Calling `async_to_sync` from inside an async handler is
  disallowed; channel notifications and any remaining blocking calls inside the
  async path must themselves be wrapped in `sync_to_async` / awaited. This is
  the real work of #163 — the spike only proves the vehicle.
- Mixed sync+async views in one `APIView` subclass: adrf decides per-view via
  `view_is_async` (true iff *all* handlers are coroutines). Read views being
  converted should have **only** async handlers, or be split out.
