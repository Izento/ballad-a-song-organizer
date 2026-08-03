# Ballad: A Song Organizer

Ballad is a review-first Windows music-library organizer. It analyzes a folder
without changing files, presents filename and tag repairs for review, and
applies only the actions the user selects.

## What it does

- Normalizes filenames to `Artist - Title (feat. Guest).ext`.
- Audits and repairs tags to match approved filenames.
- Identifies tracks with missing or conflicting metadata through optional
  AcoustID lookup.
- Enriches verified recordings with MusicBrainz artist credits, release,
  date, genre, credits, identifiers, and missing front artwork.
- Finds duplicate candidates without deleting files.
- Journals applied changes and supports guarded undo.

## Run from source

Requires Windows, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run ballad
```

Running `ballad` without a command opens the GUI. Power-user commands use
explicit subcommands:

```powershell
uv run ballad rename --folder "D:\Music\Library"
uv run ballad audit --folder "D:\Music\Library"
uv run ballad tags --folder "D:\Music\Library"
uv run ballad enrich --folder "D:\Music\Library"
uv run ballad enrich --folder "D:\Music\Library" --apply
uv run ballad dedup --folder "D:\Music\Library"
uv run ballad auto-detect --folder "D:\Music\Library"
uv run ballad undo
```

## Enrich metadata

The GUI's **Organize library** action asks for confirmation, identifies songs,
writes all verified metadata, embeds missing front cover art, and renames the
same verified files. Ambiguous recordings and unsupported files remain
unchanged and appear in **Skipped / errors**; duplicate findings remain
read-only. The tag row's context menu can show the MusicBrainz and
identification evidence used for a proposal.

From the command line, `ballad enrich` previews proposals. Add `--apply` to
write verified changes; add `--no-cover-art` to skip cover-art downloads.

Ballad first uses an embedded MusicBrainz recording ID when present. Otherwise,
with optional AcoustID enabled, it fingerprints audio locally and sends only
the fingerprint plus duration to AcoustID. AcoustID supplies source-recording
evidence; [MusicBrainz](https://musicbrainz.org/) supplies canonical recording
and release metadata. The [Cover Art Archive](https://coverartarchive.org/)
is queried only for the selected MusicBrainz release.

Local version identity is preserved: instrumentals, a cappellas, remixes, VIP
versions, edits, and extensions are not overwritten by a source-song match.
For local derivatives, Ballad writes only safe song-level credits and does not
assign the source release IDs or artwork.

## Optional online identification

AcoustID lookup requires both `fpcalc.exe` and an `ACOUSTID_API_KEY`. Built
Ballad packages include `fpcalc.exe` automatically, so users only need to
provide a key for online identification. The app works normally without
either one. When enabled, `fpcalc` processes audio locally; the app sends only
the resulting fingerprint and duration to AcoustID to request identity
metadata.

For local development, copy `.env.example` to `.env` and set the key there.
Installed builds also look for `.env` under
`%LOCALAPPDATA%\SongOrganizer` or beside the executable.

The optional fingerprint-based duplicate check also uses `fpcalc` and does not
require an AcoustID key. Direct source runs use `fpcalc` from the environment;
the release build prepares and bundles it automatically. Ballad enables
fingerprint duplicate evidence by default whenever `fpcalc` is available; turn
off the checkbox for a faster metadata/hash-only duplicate scan.

### Get your own AcoustID key

1. Create or sign in to an [AcoustID](https://acoustid.org/) account and
   register an application to obtain a lookup client key. See the
   [AcoustID web-service documentation](https://acoustid.org/documentation/webservice)
   for its API-key and usage rules.
2. In Ballad's folder, copy `.env.example` to `.env`.
3. Open `.env` in a text editor and set your own key:

   ```text
   ACOUSTID_API_KEY=your_key_here
   ```

4. Restart Ballad. The header will show `Online identification: ready`.

AcoustID lookup is optional; Ballad still analyzes and repairs
filenames/tags without a key.

The GUI has a separate **Use AcoustID identification during enrichment**
control. Its fingerprint checkbox controls optional duplicate-check evidence.

## Build

Build a keyless one-folder Windows package with:

```powershell
.\build.ps1
```

The public package is written to `release\public\Ballad`. On the first build,
the script downloads the pinned official Chromaprint archive into the ignored
`build\dependencies` cache and verifies its SHA-256 before packaging
`fpcalc.exe`. Later builds reuse the verified cache.

The package includes `fpcalc.exe`, `.env.example`, `LICENSE`,
`THIRD_PARTY_NOTICES.txt`, and `LGPL-2.1.txt`. It never includes a private
`.env`. The pinned version and checksum are recorded in `chromaprint.json`;
update them together only after reviewing a new official Chromaprint release.

## Safety

- Analysis is read-only until selected changes are confirmed.
- Enrichment writes tag updates to a same-filesystem temporary copy, verifies
  its tags and artwork, then atomically replaces the original.
- Tag updates retain compact metadata/artwork snapshots for guarded undo rather
  than full duplicate audio-file backups.
- Every reviewed plan is digest-validated before mutation. Coordinated tag and
  rename actions run as a per-song transaction, and a failed tag write blocks
  that song's rename without stopping unrelated songs.
- Applied filename and tag changes are journaled for recovery and verified,
  atomic undo.
- Duplicate findings are read-only; no normal operation permanently deletes
  files.
- CLI rename and tag changes require `--apply`; `ballad undo` restores the
  latest recoverable journaled batch.

## Development checks

```powershell
uv sync --extra test
uv run pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run python tools/check_structure.py
```

## Code organization

- `src/ballad` is the public package and entry-point namespace.
- `renamer/domain` contains immutable metadata, identity, artwork, and issue
  contracts.
- `renamer/filename_parser`, `renamer/filename_builder`, and
  `renamer/track_identity` own filename parsing, construction, and
  identity policy; `renamer/media` and `renamer/online` own container
  adapters and provider policy.
- `renamer/planners` performs read-only analysis; `renamer/transactions`
  validates, applies, journals, and restores reviewed changes.
- `cli` and `gui` are thin interfaces over those application services. The
  GUI composes focused session, view, controller, dialog, and widget modules;
  workers and presentation models do not depend on Tk widgets.

Application state, cache files, build outputs, virtual environments, API keys,
and private release archives are excluded from Git.

## License

Ballad is licensed under the MIT License. See `LICENSE`.
