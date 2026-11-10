"""The MUN OS checkout mdk works with: where it is, which revision it is,
and its card tool.

The card format, its validator and the save rules belong to MUN OS; the
console runs the same code. mdk never reimplements them: it imports the
`mun_card` package and runs the `mun-card` tool from a MUN OS checkout, and
reports which revision that was against the one this devkit was checked with
(mun-os.lock.json)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import MdkError

DEVKIT_ROOT = Path(__file__).resolve().parents[3]
LOCK_FILE = DEVKIT_ROOT / "mun-os.lock.json"


def lock() -> Dict[str, Any]:
    return json.loads(LOCK_FILE.read_text(encoding="utf-8"))


def find(explicit: Optional[str] = None) -> Path:
    """The MUN OS checkout: --mun-os, then MUN_OS_DIR, then ../mun-os beside
    the devkit. It must hold the card tool."""
    candidates = [explicit, os.environ.get("MUN_OS_DIR"), str(DEVKIT_ROOT.parent / "mun-os")]
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate).expanduser().resolve()
        if (root / "tools" / "mun-card" / "mun_card" / "__init__.py").is_file():
            return root
        if candidate is explicit or candidate == os.environ.get("MUN_OS_DIR"):
            raise MdkError("E-OS-TOOLS", f"{root} is not a MUN OS checkout (no tools/mun-card).",
                           "Point --mun-os or MUN_OS_DIR at a checkout of " + lock()["repository"] + ".")
    raise MdkError("E-OS-TOOLS", "No MUN OS checkout found.",
                   f"Clone {lock()['repository']} beside the devkit, or set MUN_OS_DIR.\n"
                   f"This devkit was checked against {lock()['release']} ({lock()['commit'][:12]}).")


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def identity(root: Path) -> Dict[str, Any]:
    """What this checkout is, and whether it is what the devkit was checked
    against. `pin` is "match", "differs" or "unverified" (no Git metadata, as
    inside the build container, where the host has already reported it)."""
    pinned = lock()
    commit = _git(root, "rev-parse", "HEAD")
    found: Dict[str, Any] = {"path": str(root), "commit": commit, "describe": _git(root, "describe", "--tags", "--always"),
                             "pinned_release": pinned["release"], "pinned_commit": pinned["commit"], "differences": []}
    if commit is None:
        found["pin"] = "unverified"
    else:
        for tree, wanted in pinned["trees"].items():
            have = _git(root, "rev-parse", f"HEAD:{tree}")
            if have != wanted:
                found["differences"].append(f"{tree} is tree {have}, pinned {wanted}")
            if _git(root, "status", "--porcelain", "--", tree):
                found["differences"].append(f"{tree} has uncommitted changes")
    for name, wanted in pinned["files_sha256"].items():
        have = _sha256(root / name)
        if have is None and commit is None:
            continue                     # a partial copy (the build environment mounts only the card tool)
        if have != wanted:
            found["differences"].append(f"{name} has SHA-256 {have}, pinned {wanted}")
    if commit is not None or found["differences"]:
        found["pin"] = "differs" if found["differences"] else "match"
    return found


def describe(found: Dict[str, Any]) -> str:
    revision = found["describe"] or (found["commit"][:12] if found["commit"] else "a copy without Git metadata")
    words = {"match": "matches the pinned tools", "unverified": "not verified here",
             "differs": "DIFFERS from the pinned tools"}[found["pin"]]
    return f"MUN OS tools: {found['path']} at {revision}; {words} ({found['pinned_release']})"


def require(explicit: Optional[str] = None, quiet: bool = False) -> Path:
    """The checkout, after reporting its revision. A difference from the pin
    is a warning, or an error with MDK_STRICT_PIN=1."""
    root = find(explicit)
    found = identity(root)
    if found["pin"] == "differs":
        detail = "\n".join(found["differences"])
        if os.environ.get("MDK_STRICT_PIN") == "1":
            raise MdkError("E-OS-PIN", "The MUN OS checkout is not the revision this devkit was checked against.",
                           f"Check out {found['pinned_commit'][:12]} ({found['pinned_release']}), or unset MDK_STRICT_PIN.",
                           detail)
        print(f"warning: {describe(found)}\n  " + detail.replace("\n", "\n  "), file=sys.stderr)
    elif not quiet:
        print(describe(found), file=sys.stderr)
    return root


def mun_card(root: Path):
    """The console's own card package, imported from the checkout."""
    path = str(root / "tools" / "mun-card")
    if path not in sys.path:
        sys.path.insert(0, path)
    import mun_card  # noqa: F401  (the package initialises its submodules lazily)
    import mun_card.image
    import mun_card.minitoml
    import mun_card.saves
    import mun_card.validate
    return mun_card


def card_tool(root: Path) -> List[str]:
    return [sys.executable, str(root / "tools" / "mun-card" / "mun-card")]
