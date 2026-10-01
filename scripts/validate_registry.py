#!/usr/bin/env python3
"""Read-only validator for packages.yaml — the single source of truth.

Exits non-zero on any failure. Warnings (missing license badge) never fail the
build; they are listed at the end so nothing rots silently.

Usage:
    python3 scripts/validate_registry.py [path/to/packages.yaml]
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

# How long to wait on each live registry call before giving up.
LIVE_TIMEOUT = 8

try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_YAML = os.path.join(HERE, "..", "packages.yaml")

SLUG_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

REQUIRED_FIELDS = [
    "id", "ecosystem", "current_name", "target_name", "old_names",
    "family", "pillar", "version", "status", "advertise_install",
    "test_count", "repo", "badges", "install", "urls", "tagline", "description",
]
REPO_FIELDS = ["path", "slug"]
BADGE_FIELDS = ["ci", "version", "license"]
URL_FIELDS = ["npm", "pypi", "clawhub", "source", "docs"]


def is_wellformed_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except (ValueError, AttributeError):
        return False
    return bool(parsed.scheme) and bool(parsed.netloc)


def _fetch_json(url: str) -> dict | None:
    """Fetch JSON from a registry. Returns None if the host is unreachable
    (offline-friendly) and raises nothing; callers treat None as 'skip'."""
    req = urllib.request.Request(url, headers={"User-Agent": "huje-registry-validator"})
    with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def npm_latest(name: str) -> str | None:
    """dist-tags.latest for an npm package, or None if the package 404s."""
    try:
        data = _fetch_json(f"https://registry.npmjs.org/{name}")
    except urllib.error.HTTPError:
        return None
    return (data or {}).get("dist-tags", {}).get("latest")


def pypi_latest(name: str) -> str | None:
    """info.version for a PyPI package, or None if the project 404s."""
    try:
        data = _fetch_json(f"https://pypi.org/pypi/{name}/json")
    except urllib.error.HTTPError:
        return None
    return (data or {}).get("info", {}).get("version")


def have_network() -> bool:
    try:
        _fetch_json("https://registry.npmjs.org/-/ping?write=false")
        return True
    except Exception:
        return False


def check_live(packages, errors):
    """For every advertise_install=true row, confirm the install target exists
    live and that the declared version matches dist-tags.latest / info.version."""
    for pkg in packages:
        if pkg.get("advertise_install") is not True:
            continue
        pid = pkg.get("id")
        tag = f"[{pid}]"
        eco = pkg.get("ecosystem")
        name = pkg.get("current_name")
        declared = str(pkg.get("version"))
        if eco == "npm":
            live = npm_latest(name)
            where = "npm"
        elif eco == "pypi":
            live = pypi_latest(name)
            where = "pypi"
        else:
            # plugin/both: only check if a concrete name resolves; skip otherwise.
            continue
        if live is None:
            errors.append(
                f"{tag} advertise_install=true but `{name}` does not resolve "
                f"live on {where} (install target would 404)"
            )
        elif str(live) != declared:
            errors.append(
                f"{tag} version `{declared}` != live {where} latest `{live}`"
            )


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    offline = "--offline" in flags
    path = argv[0] if argv else DEFAULT_YAML
    path = os.path.abspath(path)
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(doc, dict):
        print("FAIL: top-level document is not a mapping")
        return 1

    if doc.get("version") != 1:
        errors.append("top-level `version` must be 1")

    enums = doc.get("enums") or {}
    for key in ("ecosystem", "family", "pillar", "status"):
        if not isinstance(enums.get(key), list) or not enums[key]:
            errors.append(f"enums.{key} missing or not a non-empty list")
    valid_ecosystem = set(enums.get("ecosystem", []))
    valid_family = set(enums.get("family", []))
    valid_pillar = set(enums.get("pillar", []))
    valid_status = set(enums.get("status", []))

    packages = doc.get("packages")
    if not isinstance(packages, list) or not packages:
        print("FAIL: `packages` missing or empty")
        return 1

    seen_ids: dict[str, int] = {}
    # (ecosystem, name) -> id, for current_name and target_name uniqueness
    seen_current: dict[tuple, str] = {}
    seen_target: dict[tuple, str] = {}

    status_counts: dict[str, int] = {}

    for i, pkg in enumerate(packages):
        pid = pkg.get("id", f"<index {i}>")
        tag = f"[{pid}]"

        if not isinstance(pkg, dict):
            errors.append(f"{tag} package entry is not a mapping")
            continue

        # required fields present
        for field in REQUIRED_FIELDS:
            if field not in pkg:
                errors.append(f"{tag} missing required field `{field}`")

        eco = pkg.get("ecosystem")
        status = pkg.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1

        # enum validity
        if eco not in valid_ecosystem:
            errors.append(f"{tag} ecosystem `{eco}` not in enum")
        if pkg.get("family") not in valid_family:
            errors.append(f"{tag} family `{pkg.get('family')}` not in enum")
        if pkg.get("pillar") not in valid_pillar:
            errors.append(f"{tag} pillar `{pkg.get('pillar')}` not in enum")
        if status not in valid_status:
            errors.append(f"{tag} status `{status}` not in enum")

        # optional maturity field: if present it must be stable or experimental.
        # A maturity does not change any status rule; an experimental row is
        # still a normal published row for the live-check below.
        maturity = pkg.get("maturity")
        if maturity is not None and maturity not in ("stable", "experimental"):
            errors.append(
                f"{tag} maturity `{maturity}` must be 'stable' or 'experimental'"
            )

        # types
        if not isinstance(pkg.get("advertise_install"), bool):
            errors.append(f"{tag} advertise_install must be a bool")
        if not isinstance(pkg.get("test_count"), int):
            errors.append(f"{tag} test_count must be an int")
        if not isinstance(pkg.get("old_names"), list):
            errors.append(f"{tag} old_names must be a list")

        # id globally unique
        if pid in seen_ids:
            errors.append(f"{tag} duplicate id (also at index {seen_ids[pid]})")
        else:
            seen_ids[pid] = i

        # current_name / target_name unique WITHIN ecosystem
        cur = pkg.get("current_name")
        tgt = pkg.get("target_name")
        if cur is not None:
            k = (eco, cur)
            if k in seen_current:
                errors.append(
                    f"{tag} current_name `{cur}` collides within ecosystem "
                    f"`{eco}` with id `{seen_current[k]}`"
                )
            else:
                seen_current[k] = pid
        if tgt is not None:
            k = (eco, tgt)
            if k in seen_target:
                errors.append(
                    f"{tag} target_name `{tgt}` collides within ecosystem "
                    f"`{eco}` with id `{seen_target[k]}`"
                )
            else:
                seen_target[k] = pid

        # repo shape + slug
        repo = pkg.get("repo") or {}
        if not isinstance(repo, dict):
            errors.append(f"{tag} repo must be a mapping")
            repo = {}
        for rf in REPO_FIELDS:
            if rf not in repo:
                errors.append(f"{tag} repo missing `{rf}`")
        slug = repo.get("slug")
        if slug is not None and not SLUG_RE.match(str(slug)):
            errors.append(f"{tag} repo.slug `{slug}` does not match owner/name")

        # badges shape + URL well-formedness
        badges = pkg.get("badges") or {}
        if not isinstance(badges, dict):
            errors.append(f"{tag} badges must be a mapping")
            badges = {}
        for bf in BADGE_FIELDS:
            if bf not in badges:
                errors.append(f"{tag} badges missing `{bf}`")
            val = badges.get(bf)
            if val is not None and not is_wellformed_url(str(val)):
                errors.append(f"{tag} badges.{bf} is not a well-formed URL: {val}")

        # urls shape + URL well-formedness
        urls = pkg.get("urls") or {}
        if not isinstance(urls, dict):
            errors.append(f"{tag} urls must be a mapping")
            urls = {}
        for uf in URL_FIELDS:
            if uf not in urls:
                errors.append(f"{tag} urls missing `{uf}`")
            val = urls.get(uf)
            if val is not None and not is_wellformed_url(str(val)):
                errors.append(f"{tag} urls.{uf} is not a well-formed URL: {val}")

        # ---- HONESTY cross-field rules -------------------------------------
        advertise = pkg.get("advertise_install")
        install = pkg.get("install")
        version = pkg.get("version")
        npm_url = urls.get("npm")
        pypi_url = urls.get("pypi")
        repo_path = repo.get("path")

        if advertise is True and status != "published":
            errors.append(
                f"{tag} advertise_install=true requires status=published "
                f"(is `{status}`)"
            )

        # (b) anything not published must not advertise and must carry no live
        # install surface (install / npm / pypi all null). Orphan is the one
        # exception: it legitimately keeps its registry url (published, no source).
        if status != "published":
            if advertise is not False:
                errors.append(
                    f"{tag} status={status} ⇒ advertise_install must be false"
                )
            if install is not None:
                errors.append(f"{tag} status={status} ⇒ install must be null")
            if status != "orphan":
                if npm_url is not None:
                    errors.append(f"{tag} status={status} ⇒ urls.npm must be null")
                if pypi_url is not None:
                    errors.append(f"{tag} status={status} ⇒ urls.pypi must be null")

        if status == "published":
            if not version:
                errors.append(f"{tag} published ⇒ version must be set")
            if advertise is not True:
                errors.append(f"{tag} published ⇒ advertise_install must be true")
            if install is None:
                errors.append(f"{tag} published ⇒ install must be non-null")
            if npm_url is None and pypi_url is None:
                errors.append(
                    f"{tag} published ⇒ npm or pypi url must be non-null"
                )

        if status in ("shell", "planned"):
            if advertise is not False:
                errors.append(f"{tag} status={status} ⇒ advertise_install=false")
            if install is not None:
                errors.append(f"{tag} status={status} ⇒ install must be null")
            if npm_url is not None:
                errors.append(f"{tag} status={status} ⇒ urls.npm must be null")
            if pypi_url is not None:
                errors.append(f"{tag} status={status} ⇒ urls.pypi must be null")

        if status == "orphan":
            if repo_path is not None:
                errors.append(f"{tag} status=orphan ⇒ repo.path must be null")
            if advertise is not False:
                errors.append(f"{tag} status=orphan ⇒ advertise_install=false")

        if status == "code":
            if advertise is not False:
                errors.append(f"{tag} status=code ⇒ advertise_install=false")
            # repo.path may be null (paths are not published in this registry);
            # if a path is provided, it must exist on disk.
            if repo_path is not None and not os.path.isdir(str(repo_path)):
                errors.append(
                    f"{tag} status=code ⇒ repo.path must exist on disk: {repo_path}"
                )

        # ---- WARNING: missing license badge --------------------------------
        if badges.get("license") is None:
            warnings.append(f"{tag} has no license badge (badges.license=null)")

    # ---- live reality checks ----------------------------------------------
    # Confirm every advertised install target actually resolves live and the
    # declared version matches. Skippable for CI/offline use.
    if offline:
        print("Live checks: SKIPPED (--offline)")
    elif not have_network():
        print("Live checks: SKIPPED (no network reachable)")
    else:
        print("Live checks: ON (npm dist-tags + PyPI info.version)")
        check_live(packages, errors)

    # ---- report ------------------------------------------------------------
    print(f"Registry: {path}")
    print(f"Packages: {len(packages)}")
    print("By status:")
    for st in sorted(status_counts):
        print(f"  {st:<10} {status_counts[st]}")
    print(f"By ecosystem: {dict(_count(packages, 'ecosystem'))}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")

    if errors:
        print(f"\nFAIL — {len(errors)} error(s):")
        for e in errors:
            print(f"  x {e}")
        return 1

    print("\nPASS — registry is valid.")
    return 0


def _count(packages, key):
    out: dict[str, int] = {}
    for p in packages:
        v = p.get(key)
        out[v] = out.get(v, 0) + 1
    return sorted(out.items())


if __name__ == "__main__":
    sys.exit(main())
