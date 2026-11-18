# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""Start a project: a copy of the native sample under a new name, ready for
`mdk build`, `mdk check`, `mdk pack` and `mdk sim`.

The copy is the project's own from then on: nothing links it back to the
devkit's sample, and a later change to the sample does not reach it. It
records the devkit revision it was started from (mun-devkit.lock.json)."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import __version__, osrepo
from .errors import MdkError

TEMPLATE = osrepo.DEVKIT_ROOT / "samples" / "hello-mun"
# What a project starts with; build outputs and the sample's own README stay behind.
COPIED = ("Makefile", "mun-project.toml", "content", "src", "tests", "tools")
NAME = re.compile(r"[a-z][a-z0-9-]{1,39}")
README = """# {title}

A MUN game, started from the MUN Devkit's native sample.

```sh
mdk build      # compile for a console, inside the SDK environment
mdk check      # the build against its runtime profile
mdk pack       # build/{name}.img
mdk sim --container --env MUN_HELLO_HEADLESS=1 --env "MUN_HELLO_SCRIPT=up up save wait quit"
make test      # the game's own logic, on this computer
```

`mun-project.toml` says what the card will say. `src/game.c` is the game;
`src/main.c` holds the session, the save and the loop. `MUN_DEVKIT` names
the devkit this project builds with (`{devkit}` from here).
"""


def devkit_revision() -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(osrepo.DEVKIT_ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=False, env=osrepo._git_environment())
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def create(name: str, directory: Path, title: Optional[str], card_id: Optional[str], mun_os: Path) -> Path:
    """Make the project `name` in `directory` (which must not exist or be empty)."""
    validate = osrepo.mun_card(mun_os).validate
    if not NAME.fullmatch(name):
        raise MdkError("E-NEW-NAME", f"{name!r} cannot be a project name.",
                       "Use 2 to 40 lowercase letters, digits or '-', starting with a letter: star-well.")
    title = title or name.replace("-", " ").title()
    card_id = card_id or "game." + name.replace("-", "")
    if not validate.ID_PATTERN.fullmatch(card_id):
        raise MdkError("E-CARD-ID", f"card.id {card_id!r} is not valid.",
                       "3 to 64 characters: lowercase letters, digits, '.', '-' or '_'. It names the game's save "
                       "on every disk: choose it once (for example org.example.star-well).")
    if not title.strip() or len(title) > validate.TITLE_MAX_CHARS or '"' in title or "\\" in title or "\n" in title:
        raise MdkError("E-CARD-TITLE", "The title is empty, too long, or has a quote, backslash or line break.")
    directory = Path(directory)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise MdkError("E-NEW-EXISTS", f"{directory} already exists and is not empty.", "Name another directory.")
    directory.mkdir(parents=True, exist_ok=True)
    for item in COPIED:
        source = TEMPLATE / item
        if source.is_dir():
            shutil.copytree(source, directory / item)
        else:
            shutil.copy(source, directory / item)
    relative = os.path.relpath(osrepo.DEVKIT_ROOT, directory.resolve())

    def rewrite(path: str, replacements) -> None:
        target = directory / path
        text = target.read_text(encoding="utf-8")
        for old, new in replacements:
            if old not in text:
                raise MdkError("E-NEW-TEMPLATE", f"The sample's {path} no longer has {old!r}.",
                               "The devkit's project template and `mdk new` disagree: report it.")
            text = text.replace(old, new)
        target.write_text(text, encoding="utf-8")

    rewrite("mun-project.toml", [
        ("# MUN Hello: the devkit's native sample. `mdk build`, `mdk check`, `mdk pack`\n"
         "# and `mdk sim` read this file.", "# `mdk build`, `mdk check`, `mdk pack` and `mdk sim` read this file."),
        ('name = "hello-mun"', f'name = "{name}"'), ('id = "mun.hello"', f'id = "{card_id}"'),
        ('title = "MUN Hello"', f'title = "{title}"'), ('entry = "build/hello-mun"', f'entry = "build/{name}"'),
        ('run = "make"', f'run = "make NAME={name}"')])
    rewrite("Makefile", [("MUN_DEVKIT ?= ../..", f"MUN_DEVKIT ?= {relative}"), ("NAME       ?= hello-mun", f"NAME       ?= {name}")])
    rewrite("content/hello.cfg", [("title = MUN HELLO", f"title = {title.upper()}")])
    (directory / "README.md").write_text(README.format(title=title, name=name, devkit=relative), encoding="utf-8")
    (directory / ".gitignore").write_text("build/\n", encoding="utf-8")
    lock = {"_comment": "The MUN Devkit revision this project was started from or last checked with.",
            "repository": "https://github.com/Play-MUN/mun-devkit", "commit": devkit_revision() or "unknown",
            "mdk": __version__, "profile": "linux-arm64-gl-v0"}
    (directory / "mun-devkit.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return directory
