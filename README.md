# huje.registry

**The single source of truth for the whole Nostr package family.**

One file — [`packages.yaml`](./packages.yaml) — is the authoritative record of every
package we ship across npm, PyPI, and the Hermes plugin ecosystem: its canonical
name, its rename target, where its source lives, whether it is published, how many
tests it has, and which install command (if any) we are allowed to advertise.

Nothing else re-derives these facts. Landing pages, README badge tables, catalog
pages, and CI all **fetch this file** and render from it. If a fact about a package
is wrong on a website, you fix it *here* and rebuild the site — you never edit the
fact in two places.

## Why a registry

The family spans three ecosystems with overlapping names (npm `nostr-*`, PyPI
`nostr*`/`sense-*`/`social-*`, a Hermes plugin) and is mid-rename (e.g. PyPI
`nostrkey` → `nostr-key`). Without one source of truth, every surface drifts: a site
advertises `pip install` for a package that is only a metadata shell, a badge points
at a repo that no longer exists, a renamed package shows two different names on two
pages. The registry makes drift impossible to hide and cheap to fix.

## The honesty contract

`status` is the spine, and the validator enforces that every other field tells the
truth about it:

| status | meaning | rules enforced |
|--------|---------|----------------|
| `published` | live on its registry | version set, `advertise_install: true`, `install` non-null, npm **or** pypi url non-null |
| `code` | code-complete, not yet published | `advertise_install: false`, `repo.path` exists on disk |
| `shell` | metadata only | `advertise_install: false`, `install: null`, npm/pypi url null |
| `planned` | not built | `advertise_install: false`, `install: null`, npm/pypi url null |
| `orphan` | on a registry but no source repo on disk | `repo.path: null`, `advertise_install: false` |

Plus: `advertise_install: true` is only ever allowed on a `published` package. This is
what stops a site from ever advertising an install command that would not work.

## Fields

Each package entry carries:

- `id` — stable slug, **never renamed** (the join key everything else uses)
- `ecosystem` — `npm` | `pypi` | `both` | `plugin`
- `current_name` / `target_name` — name today vs. post-rename canonical name
- `old_names` — previously published names to redirect / alias
- `family` — `nostr` | `sense` | `social` | `core` | `enclave` | `orchestrator`
- `pillar` — `identity` | `finance` | `time` | `relationships` | `alignment` | `orchestrator` | `tool` | `perception`
- `version`, `status`, `advertise_install`, `test_count`
- `repo` — `{ path, slug }`
- `badges` — `{ ci, version, license }` shields.io URLs (null where missing)
- `install` — install command string, or null
- `urls` — `{ npm, pypi, clawhub, source, docs }` (nullable)
- `tagline`, `description`

The valid values live in the `enums:` block at the top of `packages.yaml`, and the
file is stamped `version: 1`.

## Validate

Read-only; exits non-zero on any failure. Run it before every commit and in CI:

```bash
pip install pyyaml          # once
python3 scripts/validate_registry.py
# or point at an explicit file:
python3 scripts/validate_registry.py path/to/packages.yaml
```

It checks: required fields present, enum validity, `id` global uniqueness,
`current_name`/`target_name` uniqueness **within each ecosystem** (npm↔pypi
collisions are allowed), URL well-formedness, `repo.slug` shape (`owner/name`), and
the full honesty contract above. Missing license badges are reported as
**warnings** (they never fail the build) so an unlicensed package stays visible
instead of rotting silently.

## Workflow

1. Edit `packages.yaml`.
2. `python3 scripts/validate_registry.py` → must print `PASS`.
3. Commit.
4. Rebuild the sites / regenerate badge tables — they read from here.
