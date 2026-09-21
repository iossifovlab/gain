# web_e2e — Playwright suite for the deployed web stack

End-to-end tests that drive the GAIn web UI in a browser against a
running `web_api` + `web_ui` stack. `tests/` holds the specs, `pages/`
the page objects, `fixtures/` the files the specs upload.

## How it runs

- **CI:** the `gain-web-e2e` Jenkins job (`Jenkinsfile.e2e`, job DSL in
  `jenkins-jobs/e2e.groovy`) brings the stack up from
  `web_infra/compose-jenkins.yaml` and runs the `e2e-tests` service,
  which is *built from this directory* — the specs are baked into the
  image. Editing a spec and re-running `docker compose run e2e-tests`
  without `docker compose build e2e-tests` runs the previous spec
  (gain#1353 found this the hard way). With `CI=1` the suite targets
  `http://frontend`, the compose network's UI service.
- **Locally**, against a stack serving the UI on `localhost:4200`:

  ```bash
  npm ci
  npx playwright test              # or: npx playwright test tests/pipeline
  ```

The root `Jenkinsfile` triggers `gain-web-e2e` on every branch but does
not wait for it, so a red e2e run does not fail the parent build — read
the job itself.

## Text the specs pin

A `toContainText` / `toHaveText` / `getByText` literal that repeats a
message the **backend** composes is checked without the stack by
`web_api/web_annotation/tests/test_e2e_backend_pins.py`, in the ordinary
`web_api` suite: rewording a registered message fails it naming the spec
that pins the old text, and a sentence-shaped pin it cannot render fails
it until registered with a producer or allow-listed with its origin. The
module docstring says how.

One habit keeps that cheap: pin the refusal clause, not the echo of your
own input (an `AnnotatorInfo` repr or a temp path is the test's input
coming back; see the comment in `tests/pipeline/validation.spec.ts`).
A pin on Markdown the page renders (the annotator modal,
`tests/single-annotation/annotator-modal.spec.ts`) is registered whole
and compared the way `toHaveText` compares, whitespace collapsed on both
sides.

## Counts the specs pin

The suite runs against the node-local grr-sync mirror of the live
`iossifovlab/grr`, so a few assertions pin a *count* that belongs to that
GRR's inventory, not to the UI: how many resources a search matches, how
many `genome` resources fill a selector, how many `*clinical*` pipelines a
dropdown offers. Those literals are kept on purpose and move the day the
GRR does. Each goes through `expectInventoryCountText` /
`expectInventoryOptionCount` in `utils.ts`, whose failure starts with
`GRR inventory drift` and names what to count against the mirror; the
helpers' docstring says which failures are drift and which stay UI
failures, and `tests/inventory-pin.spec.ts` pins that contract. A new
inventory pin goes through the same helpers. The unfiltered resource
total is deliberately *not* pinned: it moves whenever a resource of any
type lands.
