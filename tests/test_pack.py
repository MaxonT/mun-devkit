# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""Packing the sample with MUN OS's card tool: the card is one a console's
validator accepts, it holds exactly the build, and making it again gives the
same image. Skipped, with the reason, without the sample's build or e2fsprogs."""

import json
import shutil
import subprocess
import sys
import unittest

from support import MUN_OS, ROOT, SAMPLE, Scratch, needs

from mdk import checks, osrepo

ENTRY = SAMPLE / "build" / "hello-mun"


def e2fsprogs() -> bool:
    try:
        osrepo.mun_card(MUN_OS).image.find_tool("mke2fs")
        return True
    except Exception:
        return False


@needs("" if ENTRY.is_file() else f"{ENTRY.relative_to(ROOT)} is not built (run `make sample`)")
@needs("" if e2fsprogs() else "e2fsprogs (mke2fs, debugfs) is not installed")
class Pack(Scratch):
    def project_copy(self):
        """The sample's description and build, copied: packing never touches the sample itself."""
        target = self.scratch / "hello"
        (target / "build").mkdir(parents=True)
        shutil.copy(SAMPLE / "mun-project.toml", target)
        shutil.copy(ENTRY, target / "build")
        shutil.copytree(SAMPLE / "build" / "content", target / "build" / "content")
        return target

    def mdk(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "mdk"), *arguments], capture_output=True, text=True)

    def test_the_card_is_valid_holds_the_build_and_is_reproducible(self):
        target = self.project_copy()
        first = self.mdk("pack", "--project", str(target))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        record = json.loads((target / "build" / "hello-mun.card.json").read_text())
        image = target / "build" / "hello-mun.img"
        self.assertEqual(record["image_sha256"], checks.sha256_file(image))
        self.assertEqual(record["entry_sha256"], checks.sha256_file(ENTRY))
        self.assertEqual(record["card"], {"id": "mun.hello", "title": "MUN Hello", "version": "0.1.0", "kind": "game",
                                          "arch": "aarch64", "profile": "linux-arm64-gl-v0",
                                          "entry": "content/hello-mun", "access": "mount", "naming": "mun"})
        # The record says which MUN OS made the card and whether it is the pinned one.
        self.assertEqual(record["tools"]["mun_os"]["pin"], osrepo.identity(MUN_OS)["pin"])
        self.assertEqual(record["tools"]["mun_os"]["commit"], osrepo.identity(MUN_OS)["commit"])

        # What the card tool itself reads back from the image, file by file.
        tool = osrepo.card_tool(MUN_OS)
        listed = subprocess.run(tool + ["hash", str(image), "--files"], capture_output=True, text=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        files = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in listed.stdout.splitlines()}
        self.assertEqual(files["content/hello-mun"], record["entry_sha256"])
        self.assertEqual(files["content/hello.cfg"], checks.sha256_file(SAMPLE / "build" / "content" / "hello.cfg"))
        self.assertIn("content/sounds/blip.wav", files)
        self.assertIn("mun.toml", files)

        again = self.mdk("pack", "--project", str(target))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(checks.sha256_file(image), record["image_sha256"])

    def test_a_title_that_starts_with_a_dash_is_a_title(self):
        # The card tool has an option of that name: the title goes to it as a value.
        target = self.project_copy()
        description = target / "mun-project.toml"
        description.write_text(description.read_text().replace('title = "MUN Hello"', 'title = "--version"'))
        done = self.mdk("pack", "--project", str(target))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads((target / "build" / "hello-mun.card.json").read_text())["card"]["title"], "--version")

    def test_a_build_that_fails_the_checks_is_not_packed(self):
        target = self.project_copy()
        (target / "build" / "hello-mun").write_bytes(b"#!/bin/sh\n")
        done = self.mdk("pack", "--project", str(target))
        self.assertEqual(done.returncode, 1)
        self.assertIn("E-NOT-ELF", done.stdout)
        self.assertIn("E-CHECK", done.stderr)
        self.assertFalse((target / "build" / "hello-mun.img").exists())

    def test_the_card_tool_s_refusal_is_passed_on(self):
        target = self.project_copy()
        (target / "build" / "content" / "big.bin").write_bytes(b"\xa5" * (70 * 1024 * 1024))   # more than the card holds
        done = self.mdk("pack", "--project", str(target))
        self.assertEqual(done.returncode, 1)
        self.assertIn("W-CARD-SIZE", done.stdout)
        self.assertIn("E-CARD-TOOL", done.stderr)


if __name__ == "__main__":
    unittest.main()
