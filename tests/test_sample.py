"""The native sample, as built for a console (AArch64 Linux), run in the
session simulator inside the SDK environment: content, save and restore,
refusals, and every way a session ends. What this establishes is that the
program follows the contracts; it does not run on a console's devices here.

Skipped, with the reason, without a container runtime, the environment or
the sample's build (`make sample`)."""

import hashlib
import json
import shutil
import subprocess
import sys
import unittest

from support import ROOT, SAMPLE

from mdk import container
from mdk.errors import MdkError

ENTRY = SAMPLE / "build" / "hello-mun"
SAVE = SAMPLE / "build" / "sim-card" / "saves" / "mun.hello"


def environment_ready() -> str:
    """"" when the sample can be run, else why not."""
    if not ENTRY.is_file():
        return f"{ENTRY.relative_to(ROOT)} is not built (run `make sample`)"
    try:
        if not container.image_exists():
            return "the SDK environment is not built (run `./mdk env build`)"
    except MdkError as exc:
        return exc.message
    return ""


@unittest.skipIf(environment_ready(), environment_ready())
class Sample(unittest.TestCase):
    def session(self, script=None, *options, headless=True):
        """One simulated session; returns (its record, the game's log)."""
        command = [sys.executable, str(ROOT / "mdk"), "sim", "--project", str(SAMPLE), "--container", "--json",
                   "--timeout", "30", *options]
        if headless:
            command += ["--env", "MUN_HELLO_HEADLESS=1"]
        if script is not None:
            command += ["--env", f"MUN_HELLO_SCRIPT={script}"]
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)
        try:
            return json.loads(done.stdout), done.stderr
        except ValueError:
            self.fail(f"no session record:\n{done.stdout}\n{done.stderr}")

    def setUp(self):
        shutil.rmtree(SAMPLE / "build" / "sim-card", ignore_errors=True)

    def test_new_game_save_quit_then_restore(self):
        record, log = self.session("up up up save wait quit")
        self.assertEqual((record["result"], record["restore"]), ("exited", {"present": False}))
        self.assertEqual(record["saves"], [{"ok": True, "bytes": (SAVE / "save.json").stat().st_size}])
        self.assertIn("content: hello.cfg read", log)
        self.assertIn("restore: no_save", log)
        self.assertIn('save requested: {"count":3,"saves":1}', log)
        self.assertIn("save confirmed", log)
        self.assertEqual(json.loads((SAVE / "save.json").read_text())["payload"], {"count": 3, "saves": 1})

        record, log = self.session("up save wait quit")
        self.assertTrue(record["restore"]["copied"])
        self.assertIn("restore: ok (count 3, saves 1)", log)
        self.assertEqual(json.loads((SAVE / "save.json").read_text())["payload"], {"count": 4, "saves": 2})
        self.assertEqual(json.loads((SAVE / "save.json.prev").read_text())["payload"], {"count": 3, "saves": 1})

    def test_the_console_s_stop_ends_the_game_cleanly(self):
        record, log = self.session("up idle:100000", "--stop-after", "1")
        self.assertEqual(record["result"], "exited")
        self.assertTrue(record["stop"]["requested"])
        self.assertFalse(record["stop"]["killed"])
        self.assertLess(record["stop"]["seconds_to_end"], 1.0)
        self.assertIn("asked to stop", log)
        self.assertIn("exit 0", log)

    def test_a_stop_during_a_save_still_ends_in_time_and_the_card_is_whole(self):
        record, log = self.session("up up save idle:100000", "--stop-after", "0.6", "--save-delay", "4")
        self.assertEqual(record["result"], "exited")
        self.assertFalse(record["stop"]["killed"])
        self.assertLess(record["stop"]["seconds_to_end"], 3.0)
        self.assertIn("a save was still unanswered", log)

    def test_a_refused_save_is_reported_and_the_previous_save_stays(self):
        self.session("up save wait quit")
        before = hashlib.sha256((SAVE / "save.json").read_bytes()).hexdigest()
        record, log = self.session("up up save wait quit", "--fail", "no_space")
        self.assertEqual(record["result"], "exited")
        self.assertEqual(record["saves"], [{"ok": False, "code": "no_space"}])
        self.assertIn("save not confirmed: refused (no_space) No hay espacio en la Game Card", log)
        self.assertIn("saves confirmed 1", log)                  # the count of confirmed saves did not move
        self.assertEqual(hashlib.sha256((SAVE / "save.json").read_bytes()).hexdigest(), before)

    def test_a_failed_and_a_crashed_game_are_told_apart(self):
        record, _ = self.session("up fail")
        self.assertEqual((record["result"], record["exit_code"]), ("failed", 3))
        record, _ = self.session("up crash")
        self.assertEqual((record["result"], record["signal"]), ("crashed", "SIGABRT"))

    def test_a_damaged_save_is_reported_and_not_overwritten_unasked(self):
        SAVE.mkdir(parents=True)
        (SAVE / "save.json").write_bytes(b"{ torn")
        record, log = self.session("up quit")
        self.assertEqual(record["result"], "exited")
        self.assertIn("restore: damaged (count 0", log)
        self.assertEqual((SAVE / "save.json").read_bytes(), b"{ torn")
        # The player saves on purpose: the damaged file is set aside, not lost.
        self.session("up save wait quit")
        self.assertEqual(json.loads((SAVE / "save.json").read_text())["payload"], {"count": 1, "saves": 1})
        self.assertEqual(len([p for p in SAVE.iterdir() if p.name.startswith("save.json.damaged-")]), 1)

    def test_a_save_of_a_later_schema_is_not_guessed_at(self):
        SAVE.mkdir(parents=True)
        (SAVE / "save.json").write_text(json.dumps({"format": "mun-save/1", "game": "mun.hello", "schema": 2,
                                                     "payload": {"count": 5, "saves": 1, "extra": True}}) + "\n")
        _, log = self.session("quit")
        self.assertIn("restore: ok (count 0, saves 0)", log)     # read, and not applied

    def test_it_draws_with_the_console_s_mesa_in_software(self):
        frame = SAMPLE / "build" / "frame.ppm"
        frame.unlink(missing_ok=True)
        record, log = self.session("up up idle:4 quit", "--env", "SDL_VIDEODRIVER=offscreen", "--env",
                                   "ALSOFT_DRIVERS=null", "--env", "MUN_HELLO_WINDOW=1280x720", "--env",
                                   "MUN_HELLO_CAPTURE=/work/build/frame.ppm", headless=False)
        self.assertEqual(record["result"], "exited")
        self.assertIn("OpenGL", log)
        self.assertIn("blip from the card's content", log)
        data = frame.read_bytes()
        header, pixels = data.split(b"\n255\n", 1)
        self.assertEqual(header, b"P6\n1280 720")
        self.assertEqual(len(pixels), 1280 * 720 * 3)
        self.assertEqual(tuple(pixels[:3]), (227, 154, 99))          # the accent bar along the top, from hello.cfg
        row = 400 * 1280 * 3
        self.assertEqual(tuple(pixels[row:row + 3]), (22, 32, 43))   # the background, from hello.cfg
        self.assertGreater(len(set(pixels[i:i + 3] for i in range(0, len(pixels), 3 * 97))), 2)   # and text


if __name__ == "__main__":
    unittest.main()
