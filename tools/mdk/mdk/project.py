# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""A project's description, mun-project.toml, read with the same strict TOML
subset MUN OS reads a card's manifest with.

    [project]
    name = "hello-mun"            # also the card image's file name

    [card]                         # what the card will say; MUN OS validates it
    id = "mun.hello"
    title = "MUN Hello"
    version = "0.1.0"
    profile = "linux-arm64-gl-v0"
    size_mib = 64                  # optional; the card tool's default otherwise

    [build]
    run = "make"                   # run in the project directory, inside the SDK environment
    entry = "build/hello-mun"      # the executable the build leaves
    content = "build/content"      # optional: the data the game reads, mounted read-only
    hardening = "required"         # optional: "required" makes a missing hardening property
                                   # an error; "report" (the default) only says so
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import osrepo
from .errors import MdkError

FILE_NAME = "mun-project.toml"
TABLES = {"project": {"name"}, "card": {"id", "title", "version", "profile", "size_mib", "cover", "accent", "background"},
          "build": {"run", "entry", "content", "hardening"}}
REQUIRED = {"project": ("name",), "card": ("id", "title", "version", "profile"), "build": ("run", "entry")}


def profile_record(name: str) -> Dict[str, Any]:
    path = osrepo.DEVKIT_ROOT / "contracts" / "profiles" / f"{name}.json"
    if not path.is_file():
        known = sorted(p.stem for p in path.parent.glob("*.json"))
        raise MdkError("E-PROFILE", f"mdk has no record of the runtime profile {name!r}.",
                       "Profiles a console implements today: " + ", ".join(known) + ".")
    return json.loads(path.read_text(encoding="utf-8"))


class Project:
    def __init__(self, directory: Path, data: Dict[str, Any]):
        self.directory = directory
        self.name: str = data["project"]["name"]
        self.card: Dict[str, Any] = data["card"]
        self.build: Dict[str, Any] = data["build"]
        self.profile = profile_record(self.card["profile"])

    def path(self, relative: str) -> Path:
        """A path named in the description: relative and inside the project."""
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise MdkError("E-PROJECT", f"{relative!r} in {FILE_NAME} must be a path inside the project.")
        return self.directory / candidate

    @property
    def entry(self) -> Path:
        return self.path(self.build["entry"])

    @property
    def content(self) -> Optional[Path]:
        return self.path(self.build["content"]) if self.build.get("content") else None

    @property
    def output(self) -> Path:
        return self.directory / "build"

    @property
    def image(self) -> Path:
        return self.output / f"{self.name}.img"


def load(directory: Path, mun_os: Path) -> Project:
    directory = Path(directory).resolve()
    path = directory / FILE_NAME
    if not path.is_file():
        raise MdkError("E-PROJECT", f"No {FILE_NAME} in {directory}.",
                       "Run mdk from a project directory, or name one with --project.")
    minitoml = osrepo.mun_card(mun_os).minitoml
    try:
        data = minitoml.loads(path.read_text(encoding="utf-8"))
    except (minitoml.TomlSyntaxError, UnicodeDecodeError) as exc:
        raise MdkError("E-PROJECT", f"{path} is not valid.", "It uses the card manifest's TOML subset: "
                       "tables, strings, integers, booleans and arrays of strings.", str(exc))
    for table, keys in data.items():
        if table not in TABLES or not isinstance(keys, dict):
            raise MdkError("E-PROJECT", f"{FILE_NAME}: unknown table [{table}].", "Known: " + ", ".join(TABLES) + ".")
        unknown = sorted(set(keys) - TABLES[table])
        if unknown:
            raise MdkError("E-PROJECT", f"{FILE_NAME}: unknown key in [{table}]: {', '.join(unknown)}.")
    for table, keys in REQUIRED.items():
        missing = [key for key in keys if not isinstance(data.get(table, {}).get(key), str) or not data[table][key]]
        if missing:
            raise MdkError("E-PROJECT", f"{FILE_NAME}: [{table}] needs {', '.join(missing)}.")
    if data["build"].get("hardening", "report") not in ("required", "report"):
        raise MdkError("E-PROJECT", f'{FILE_NAME}: build.hardening is "required" or "report".')
    name = data["project"]["name"]
    if not name.replace("-", "").replace("_", "").isalnum():
        raise MdkError("E-PROJECT", f"{FILE_NAME}: project.name must be letters, digits, '-' or '_'.")
    return Project(directory, data)
