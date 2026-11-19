# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""Make a card image of a project with MUN OS's own card tool, and record
what went into it.

mdk does not write a manifest or build a file system: `mun-card create` does,
with the validator the console runs, and `mun-card inspect` reads the result
back the way a console would. mdk adds the checks before, and a record after."""

import json
import subprocess  # nosec B404: runs MUN OS's card tool from the pinned checkout
from pathlib import Path
from typing import Any, Dict

from . import __version__, checks, osrepo
from .errors import MdkError
from .project import Project


def pack(project: Project, mun_os: Path) -> Dict[str, Any]:
    report = checks.check_project(project, mun_os)
    print(checks.render(report))
    if not report["ok"]:
        raise MdkError("E-CHECK", "The card was not made: the build does not pass mdk's checks.",
                       "Fix the errors above and run `mdk pack` again.")
    project.output.mkdir(parents=True, exist_ok=True)
    tool = osrepo.card_tool(mun_os)
    card = project.card
    command = tool + ["create", str(project.image), "--variant", project.profile["card_variant"],
                      "--game", str(project.entry), f"--title={card['title']}", f"--id={card['id']}",
                      f"--version={card['version']}", "--force"]       # =VALUE: a title may start with '-'
    if project.content is not None:
        command += ["--content", str(project.content)]
    if isinstance(card.get("size_mib"), int):
        command += ["--size", str(card["size_mib"])]
    for option in ("cover", "accent", "background"):
        if card.get(option):
            value = str(project.path(card[option])) if option == "cover" else card[option]
            command.append(f"--{option}={value}")
    created = subprocess.run(  # nosec B603: this Python and the pinned card tool, no shell; values go as --name=VALUE or absolute paths
        command, capture_output=True, text=True, check=False)
    print(created.stdout, end="")
    if created.returncode != 0:
        raise MdkError("E-CARD-TOOL", "MUN OS's card tool refused to make the card.",
                       "Its message is above; the codes are listed in MUN OS's docs/game-cards.md.",
                       (created.stderr or created.stdout).strip())
    inspected = subprocess.run(  # nosec B603: this Python and the pinned checkout's card tool, no shell, on the image just made
        tool + ["inspect", str(project.image), "--json"], capture_output=True, text=True,
        check=False)
    try:
        seen = json.loads(inspected.stdout)
    except ValueError:
        raise MdkError("E-CARD-TOOL", "The card tool's inspection of the new card could not be read.",
                       detail=(inspected.stderr or inspected.stdout).strip())
    if not seen.get("valid"):
        error = seen.get("error", {})
        raise MdkError("E-CARD-INVALID", f"The card was made, and a console would refuse it: [{error.get('code')}] "
                       f"{error.get('message')}.", "See MUN OS's docs/game-cards.md for that code.",
                       str(error.get("detail") or ""))
    tool_line = next((line.strip() for line in created.stdout.splitlines() if line.strip().startswith("mke2fs:")), "")
    record = {
        "format": "mdk-card-record/1",
        "image": project.image.name,
        "image_sha256": seen["sha256"],
        "image_bytes": project.image.stat().st_size,
        "card": {key: seen["card"].get(key) for key in ("id", "title", "version", "kind", "arch", "profile", "entry",
                                                         "access", "naming")},
        "entry_sha256": report["entry"]["sha256"],
        "entry_bytes": report["entry"]["size"],
        "entry_needs": report["entry"]["needed"],
        "content": report["content"],
        "tools": {"mdk": __version__, "mun_os": osrepo.identity(mun_os), "mke2fs": tool_line.replace("mke2fs: ", "")},
    }
    record_path = project.output / f"{project.name}.card.json"
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"card     {project.image}")
    print(f"         sha256 {record['image_sha256']}")
    print(f"         a console reads it as: {record['card']['title']} ({record['card']['id']}) "
          f"v{record['card']['version']}, {record['card']['profile']}, entry {record['card']['entry']}")
    print(f"record   {record_path}")
    return record
