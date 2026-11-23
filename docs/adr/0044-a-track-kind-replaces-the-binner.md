# 44. A track kind replaces the binner

- **Status:** accepted
- **Date:** 2026-11-23

## Context

A run definition of `binning_tool` has a `binners:` list. Each item of the
list is an **entry** of the kind `position_score_binner` or
`fragment_score_binner`. The `binning_tracks_annotator` takes the same list.
The `/tracks` table of the output file has the columns `binner_type`,
`binner_index` and `binner_def` (`tracks_layout` 2). The code of
`gain.binning` uses the word "binner" for the kind that reads an entry, for
example `PositionScoreBinner` and `resolve_binners`.

A user writes a list of entries and gets a list of tracks. The word "binner"
names the code, not the thing that the user asks for.

## Decision

The word **track kind** replaces "binner" in the config, in the file and in
the code:

- The run definition and the annotator take a `tracks:` list. The kinds are
  `position_score_tracks` and `fragment_score_tracks`. The annotator type is
  `tracks_annotator`.
- The `/tracks` columns are `entry_type`, `entry_index` and `entry_def`, with
  `tracks_layout` 3. The values of `entry_type` stay `position_score` and
  `fragment_score`.
- The code names follow the config: `TrackKind`, `PositionScoreTracks`,
  `FragmentScoreTracks`, `EntryDef`, `discover_track_kinds`, `resolve_tracks`,
  and the entry-point group `gain.binning.track_kinds`.

The old spellings are refused with a message that names the new spelling:
`binners:`, `position_score_binner`, `fragment_score_binner`,
`binning_tracks_annotator`, and a file with `tracks_layout` 2. The code keeps
no aliases.

## Why

The config, the file and the code then use the same words. A reader who sees
`position_score_tracks:` in a config finds `PositionScoreTracks` in the code
and `entry_type` in the file.

We rejected two alternatives:

- A rename of the config words only. The file and the code then use a second
  vocabulary for the same things.
- Aliases for the old spellings. A search of GitHub found no caller of the old
  Python names outside gain. A one-line message fixes each old config.

A refusal of `tracks_layout` 2 does not make a re-run slow. The name of a
cached chunk does not contain a kind name or a column name. So a re-run reuses
the chunks of the old run.

## Consequences

- A file written before the rename does not load. Run `binning_tool` again.
- A plugin kind registers in the group `gain.binning.track_kinds`. After a
  plugin outside gain uses the group, a change of its name breaks that plugin.
- ADRs 0025 and 0035 to 0041 keep the old words, because each ADR records a
  decision at its date.
