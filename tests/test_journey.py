# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""The whole way from nothing to a saved game, through `mdk` alone: start a
project, build it for a console, check it, make its card, play a session
that saves, and play another that restores.

Each step is the command a developer types. The sessions run in the session
simulator inside the SDK environment; the card is made and read back by
MUN OS's own tool. What it does not show is the game on a console.

Skipped, with the reason, without the SDK environment or e2fsprogs."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import MUN_OS, ROOT, isolate_git, needs

from mdk import container, osrepo
from mdk.errors import MdkError


def ready() -> str:
    try:
        if not container.image_exists():
            return "the SDK environment is not built (run `./mdk env build`)"
        osrepo.mun_card(MUN_OS).image.find_tool("mke2fs")
    except MdkError as exc:
        return exc.message
    except Exception as exc:
        return f"e2fsprogs is not installed ({exc})"
    return ""


def mdk(*arguments, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "mdk"), *arguments], capture_output=True, text=True,
                          cwd=cwd, timeout=600)


@needs(ready())
class Journey(unittest.TestCase):
    def setUp(self):
        isolate_git(self)
        # Under the devkit, where the environment can reach it.
        (ROOT / ".local").mkdir(exist_ok=True)
        self.holder = Path(tempfile.mkdtemp(prefix="journey-", dir=str(ROOT / ".local")))
        self.addCleanup(shutil.rmtree, self.holder, True)
        self.project = self.holder / "star-well"

    def step(self, *arguments):
        done = mdk(*arguments, cwd=self.project if self.project.is_dir() else self.holder)
        self.assertEqual(done.returncode, 0, f"mdk {' '.join(arguments)}\n{done.stdout}\n{done.stderr}")
        return done.stdout + done.stderr

    def test_create_build_check_pack_play_save_restore(self):
        self.step("new", "star-well", "--title", "Star Well", "--id", "org.example.star-well")
        lock = json.loads((self.project / "mun-devkit.lock.json").read_text())
        self.assertEqual(lock["profile"], "linux-arm64-gl-v0")

        self.step("build")
        self.assertIn("check: passed", self.step("check"))
        packed = self.step("pack")
        self.assertIn("Star Well (org.example.star-well) v0.1.0, linux-arm64-gl-v0, entry content/star-well", packed)
        record = json.loads((self.project / "build" / "star-well.card.json").read_text())
        self.assertEqual(record["card"]["id"], "org.example.star-well")
        self.assertEqual(record["entry_needs"], ["libSDL2-2.0.so.0", "libopenal.so.1", "libm.so.6", "libc.so.6"])

        first = self.step("sim", "--container", "--fresh", "--timeout", "30", "--env", "MUN_HELLO_HEADLESS=1",
                          "--env", "MUN_HELLO_SCRIPT=up up up save wait quit")
        self.assertIn("card org.example.star-well", first)
        self.assertIn("content: hello.cfg read", first)
        self.assertIn("restore: no_save", first)
        self.assertIn("save confirmed", first)
        self.assertIn("game exited (0)", first)
        save = self.project / "build" / "sim-card" / "saves" / "org.example.star-well" / "save.json"
        self.assertEqual(json.loads(save.read_text())["payload"], {"count": 3, "saves": 1})

        second = self.step("sim", "--container", "--timeout", "30", "--env", "MUN_HELLO_HEADLESS=1",
                           "--env", "MUN_HELLO_SCRIPT=up save wait quit")
        self.assertIn("restore: ok (count 3, saves 1)", second)
        self.assertIn("save restored", second)
        self.assertEqual(json.loads(save.read_text())["payload"], {"count": 4, "saves": 2})

        # The same sources give the same program and the same card.
        entry = (self.project / "build" / "star-well").read_bytes()
        self.step("build")
        self.assertEqual((self.project / "build" / "star-well").read_bytes(), entry)
        self.step("pack")
        again = json.loads((self.project / "build" / "star-well.card.json").read_text())
        self.assertEqual(again["image_sha256"], record["image_sha256"])

    def test_names_and_places_new_refuses(self):
        for arguments, code in ((["new", "Star Well"], "E-NEW-NAME"), (["new", "x"], "E-NEW-NAME"),
                                (["new", "ok-name", "--id", "Bad Id"], "E-CARD-ID"),
                                (["new", "ok-name", "--title", 'a "quoted" title'], "E-CARD-TITLE")):
            done = mdk(*arguments, cwd=self.holder)
            self.assertEqual(done.returncode, 1, arguments)
            self.assertIn(code, done.stderr)
        self.assertEqual(list(self.holder.iterdir()), [])
        (self.holder / "taken").mkdir()
        (self.holder / "taken" / "file").write_text("mine")
        done = mdk("new", "taken", cwd=self.holder)
        self.assertIn("E-NEW-EXISTS", done.stderr)
        self.assertEqual((self.holder / "taken" / "file").read_text(), "mine")


if __name__ == "__main__":
    unittest.main()
