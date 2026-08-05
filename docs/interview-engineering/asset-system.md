# Asset System

The asset system discovers flashcard files from curriculum source repositories
and records metadata in the generated manifest. OpenStudy does not parse or
import the flashcard content, but the production Docker image packages the
small set of referenced `.apkg` and `.db` files and syncs them into the
OpenStudy course-file root so learners can download them from the Files pane.

## Asset Types

The validator currently accepts:

- `flashcard-deck` with `format: apkg`
- `flashcard-database` with `format: db`

Discovered file types:

- `.apkg`: Anki package files
- `.db`: flashcard database files

## Current Assets

The generated manifest currently includes six assets:

| ID | Source | Format | Storage path |
| --- | --- | --- | --- |
| `icc-anki-coding` | `interactive-coding-challenges/anki_cards/Coding.apkg` | `apkg` | `ICC/flashcards/Coding.apkg` |
| `ciu-flashcards-standard` | `computer-science-flash-cards/cards-jwasham.db` | `db` | `CIU/flashcards/cards-jwasham.db` |
| `ciu-flashcards-cards-jwasham-extreme` | `computer-science-flash-cards/cards-jwasham-extreme.db` | `db` | `CIU/flashcards/cards-jwasham-extreme.db` |
| `sdp-flashcards-oo-design` | `system-design-primer/resources/flash_cards/OO Design.apkg` | `apkg` | `SDP/flashcards/OO Design.apkg` |
| `sdp-flashcards-system-design` | `system-design-primer/resources/flash_cards/System Design.apkg` | `apkg` | `SDP/flashcards/System Design.apkg` |
| `sdp-flashcards-system-design-exercises` | `system-design-primer/resources/flash_cards/System Design Exercises.apkg` | `apkg` | `SDP/flashcards/System Design Exercises.apkg` |

`computer-science-flash-cards/cards-empty.db` is intentionally excluded because
it is not useful as a learner asset.

## Manifest Shape

```yaml
- id: icc-anki-coding
  title: Interactive Coding Challenges Coding Anki Deck
  kind: flashcard-deck
  format: apkg
  course_code: ICC
  source:
    repo: interactive-coding-challenges
    path: anki_cards/Coding.apkg
  storage_path: ICC/flashcards/Coding.apkg
  usage:
    openstudy_readable: false
    import_into_anki: true
    review_cadence: weekly
```

## Metadata Semantics

### `source`

Points to the local source repository and relative path. This preserves
attribution and lets future automation find the original asset.

### `storage_path`

Represents the logical curriculum asset path in the manifest. At runtime,
OpenStudy does not use this value as the browser path. The startup sync copies
packaged flashcard files into:

```text
STUDY_ROOT/<user-id>/interview-engineering/resources/flashcards/
```

With the default deployment this resolves to:

```text
/opt/courses/<user-id>/interview-engineering/resources/flashcards/
```

That location is what the dashboard Files pane and MCP `list_course_files`
tool browse.

### `usage`

Describes how a learner should use the asset:

- `openstudy_readable`: whether OpenStudy can read it directly. Current `.apkg`
  and `.db` assets are `false`.
- `import_into_anki`: whether the learner should import it into Anki.
- `review_cadence`: optional suggested review rhythm.

## Anki Workflow

For `.apkg` decks:

1. Download the file from the dashboard Files pane under
   `interview-engineering/resources/flashcards/`.
2. Import it into Anki.
3. Keep OpenStudy tasks and module progress as the planning layer.
4. Use Anki for spaced repetition reviews.

For `.db` files:

1. Treat them as reference card databases.
2. Do not expect OpenStudy to parse them.
3. Convert or import them with an external workflow only if needed.

## Why Metadata-Only

Parsing Anki packages or upstream card databases would add complexity and
couple OpenStudy to formats it does not currently own. Metadata-only assets
still give the dashboard enough information to:

- display available flashcard resources later
- track review workflow metadata
- preserve source paths
- seed asset records without binary parsing

## Validation

The validator checks:

- asset ID format
- required fields
- accepted `kind` and `format`
- source repo reference
- source path existence when possible
- basic usage metadata

The seed script warns if an asset source file is missing. Production images
should include the six whitelisted flashcard assets, while still excluding full
source repositories.

## Runtime Visibility

Packaging files into the image is not enough for the dashboard. The Files pane,
download endpoint, and MCP file tools all read from `STUDY_ROOT`, not directly
from `/app/curriculum/sources`.

On backend startup, `app.services.packaged_assets` runs when
`OPENSTUDY_PACKAGED_CURRICULUM=1` and copies supported manifest assets from:

```text
PACKAGED_CURRICULUM_ASSETS_ROOT=/app/curriculum/sources
```

to:

```text
/opt/courses/<user-id>/interview-engineering/resources/flashcards/
```

The sync is idempotent. It copies only manifest assets with `format: apkg` or
`format: db`, and it does not expose the full source repositories.

## Future Improvements

- Add a first-class `curriculum_assets` table.
- Add dashboard UI for asset review status.
- Add optional Anki import instructions per asset.
- Add checksums for binary asset tracking.
