# 43. The search index carries each resource's labels in their rendered form

- **Status:** accepted
- **Date:** 2026-11-23
- **Issues:** [gain#2488](https://github.com/iossifovlab/gain/issues/2488) (the
  `resource_labels` table and the JSON download), with
  [gain#2489](https://github.com/iossifovlab/gain/issues/2489),
  [gain#2490](https://github.com/iossifovlab/gain/issues/2490) and
  [gain#2491](https://github.com/iossifovlab/gain/issues/2491)

## Context

The root `index.html` of a GRR will offer a **resource list**: a CSV or JSON
file of the resources that the current view lists. Each record holds the labels
of its resource. JSON keeps a list value as an array, and CSV writes the labels
as one JSON cell.

The page is static, so the file is built in the browser from data that the page
already holds. The **search index** (`.CONTENTS.sqlite3.gz`) is the only data
of the page that holds labels. Its FTS5 table has one column per label key.
`collect_index_info` joins the alternatives of a list value with spaces, so FTS5
sees one term for each alternative (gain#1225).

That join loses the shape of the value. On the NYGC GRR (2,866 resources, 34
label keys), `modalities` holds the list `RNA, ATAC` and is stored as
`"RNA ATAC"`. `brain_region` holds the single value `basal ganglia` on 832
resources and is stored as `"basal ganglia"`. From the FTS5 column, the page
cannot tell the two cases apart.

## Decision

The index build adds a plain table to the search index:

```sql
CREATE TABLE resource_labels (full_id TEXT PRIMARY KEY, labels TEXT)
```

`labels` is the JSON object of the resource's labels in their rendered form.
The rendered form is the one that the **Label** entry of `GLOSSARY.md` names,
the form in which a label is compared. A scalar value is a JSON string, and a
list value is a JSON array of strings. The FTS5 table `contents` does not
change.

## Why here, and not in another place

- **The FTS5 columns as they are.** No build change, but the list is lost. A
  reader of the file cannot tell `basal ganglia` from two values.
- **The HTML rows of the root page.** The page holds version and size only in
  its rows, so labels could go there too. The NYGC root page is already 970 KB
  of rows, and gain#1711 cut the weight of the page on purpose. The search index
  is fetched after the first paint, so it adds no weight to the first view.
- **An `UNINDEXED` JSON column in `contents`.** FTS5 allows it, but a column of
  the FTS5 table can be named in a search term. A plain table keeps the JSON out
  of the search, and the search code needs no change.
- **One request per resource for `genomic_resource.yaml`.** On NYGC that is
  2,866 requests for one download.

## Consequences

- A reader that knows only `contents`, such as `grr_browse` or an older root
  page, ignores the new table.
- The index build skips the rebuild when the `contents_md5` that the index
  records matches `.CONTENTS` (`_contents_db_is_current` in `cli_manifest.py`).
  An unchanged GRR would therefore keep an index without the new table after a
  gain upgrade. So the index also records a schema version in
  `contents_metadata`, and an index with an older version or no version is not
  current. The first `grr_manage` run after the upgrade then rebuilds the index
  and writes the root page that reads it.
- The search index is now more than a search structure. The **Search index**
  entry of the glossary says that it also holds the labels.
- The labels are stored twice: once space-joined for search and once as JSON
  for the resource list. On NYGC, the JSON of all labels is about 950 KB, and
  about 28 KB when gzipped on its own (measured from the live search index). The
  search index is 258 KB gzipped today. The issue that builds the table measures
  the real growth of the published file.
