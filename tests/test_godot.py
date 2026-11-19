# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""The Godot adapter (engines/godot/addons/mun) and the Godot probe, run by
the pinned Godot release's Linux ARM64 build, headless, in the session
simulator inside the SDK environment.

What this establishes: that engine build has a UNIX-socket stream, and the
adapter's GDScript speaks the console's save protocol with it. It runs the
project with the engine's editor build, not an exported game, and draws
nothing: presentation, sound devices and controllers are not covered.

Skipped, with the reason, without the environment or the engine
(`engines/godot/fetch.sh`)."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, isolate_git, needs

from mdk import container
from mdk.errors import MdkError

VERSION = "4.7.2-stable"
ENGINE = ROOT / ".local" / "godot" / VERSION / f"Godot_v{VERSION}_linux.arm64"
PROJECT = """[project]
name = "hello-mun-godot-dev"
[card]
id = "mun.hellogodot"
title = "MUN Hello (Godot)"
version = "0.1.0"
profile = "linux-arm64-gl-v0"
[build]
run = "true"
entry = "run.sh"
"""
RUN = f"""#!/bin/sh
cp -rL /opt/mun-devkit/samples/hello-mun-godot "$HOME/project"
exec /opt/mun-devkit/.local/godot/{VERSION}/{ENGINE.name} --headless --path "$HOME/project"
"""


def ready() -> str:
    if not ENGINE.is_file():
        return f"Godot {VERSION} is not fetched (run engines/godot/fetch.sh)"
    try:
        if not container.image_exists():
            return "the SDK environment is not built (run `./mdk env build`)"
    except MdkError as exc:
        return exc.message
    return ""


@needs(ready())
class GodotProbe(unittest.TestCase):
    def setUp(self):
        isolate_git(self)
        # Inside the devkit so that the environment can reach it; a development
        # stand-in for an exported game: the entry starts the engine on the project.
        self.project = Path(tempfile.mkdtemp(prefix="godot-probe-", dir=str(ROOT / ".local")))
        self.addCleanup(shutil.rmtree, self.project, True)
        (self.project / "mun-project.toml").write_text(PROJECT)
        (self.project / "run.sh").write_text(RUN)
        (self.project / "run.sh").chmod(0o755)
        self.save = self.project / "build" / "sim-card" / "saves" / "mun.hellogodot"

    def session(self, script, *options):
        command = [sys.executable, str(ROOT / "mdk"), "sim", "--project", str(self.project), "--container", "--json",
                   "--timeout", "60", "--env", f"MUN_HELLO_SCRIPT={script}", *options]
        done = subprocess.run(command, capture_output=True, text=True, timeout=180)
        # The engine prints to standard output, before the record.
        start = done.stdout.rfind("\n{\n")
        try:
            return json.loads(done.stdout[start:]), done.stdout[:start] + done.stderr
        except ValueError:
            self.fail(f"no session record:\n{done.stdout}\n{done.stderr}")

    def test_the_engine_build_is_the_pinned_one_and_saves_from_gdscript(self):
        record, log = self.session("up up up save wait quit")
        self.assertIn("engine 4.7.2-stable (official)", log)
        self.assertEqual(record["result"], "exited")
        self.assertEqual(record["saves"], [{"ok": True, "bytes": (self.save / "save.json").stat().st_size}])
        self.assertIn("save confirmed", log)
        envelope = json.loads((self.save / "save.json").read_text())
        self.assertEqual((envelope["format"], envelope["schema"], envelope["payload"]),
                         ("mun-save/1", 1, {"count": 3, "saves": 1}))

        record, log = self.session("up save wait quit")
        self.assertIn("restore: ok (count 3, saves 1)", log)
        self.assertEqual(json.loads((self.save / "save.json").read_text())["payload"], {"count": 4, "saves": 2})

    def test_a_refusal_reaches_the_game_with_the_console_s_code(self):
        record, log = self.session("up save wait quit", "--fail", "no_space")
        self.assertEqual(record["result"], "exited")
        self.assertIn("save not confirmed: refused (no_space) No hay espacio en la Game Card", log)
        self.assertFalse((self.save / "save.json").exists())

    def test_a_save_the_console_could_not_attend_then_one_it_did(self):
        record, log = self.session("up save wait up save wait quit", "--fail", "save_failed:1")
        self.assertEqual(record["result"], "exited")
        self.assertEqual([save.get("code", "ok") for save in record["saves"]], ["save_failed", "ok"])
        self.assertIn("save not confirmed: refused (save_failed)", log)
        self.assertEqual(json.loads((self.save / "save.json").read_text())["payload"], {"count": 2, "saves": 1})

    def test_a_console_that_closes_without_answering_does_not_hang_the_game(self):
        record, log = self.session("up save wait up save wait quit", "--fail", "closed:1")
        self.assertEqual(record["result"], "exited")
        self.assertEqual([save.get("code", "ok") for save in record["saves"]], ["closed", "ok"])
        self.assertIn("save not confirmed: io", log)
        self.assertEqual(json.loads((self.save / "save.json").read_text())["payload"], {"count": 2, "saves": 1})

    def test_a_slow_card_does_not_stop_frames_and_a_second_save_waits(self):
        record, log = self.session("up save up save up wait quit", "--save-delay", "0.5")
        self.assertEqual(record["result"], "exited")
        self.assertEqual(record["saves"], [{"ok": True, "bytes": (self.save / "save.json").stat().st_size}])
        self.assertEqual(json.loads((self.save / "save.json").read_text())["payload"], {"count": 1, "saves": 1})

    def test_a_damaged_save_and_a_failing_exit(self):
        self.save.mkdir(parents=True)
        (self.save / "save.json").write_bytes(b"{ torn")
        record, log = self.session("up fail")
        self.assertIn("restore: damaged (count 0, saves 0)", log)
        self.assertEqual((record["result"], record["exit_code"]), ("failed", 3))

    def test_the_engine_alone_does_not_end_cleanly_on_the_console_s_stop(self):
        # Godot installs no SIGTERM handler: the stock engine dies by the
        # signal instead of exiting 0. A game for a console needs the engine
        # built with a termination module; this records the stock behaviour.
        record, _ = self.session("up idle:100000", "--stop-after", "2")
        self.assertEqual((record["result"], record.get("signal")), ("crashed", "SIGTERM"))
        self.assertFalse(record["stop"]["killed"])


if __name__ == "__main__":
    unittest.main()
