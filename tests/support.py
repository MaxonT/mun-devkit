"""Shared by the tests: the devkit's paths, the MUN OS checkout, and a way to
talk to a save socket as a game would."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mdk"))

from mdk import osrepo, sim  # noqa: E402

MUN_OS = osrepo.find()
SAVES = osrepo.mun_card(MUN_OS).saves
SAMPLE = ROOT / "samples" / "hello-mun"


class Scratch(unittest.TestCase):
    """A test with a short scratch directory (a UNIX socket's path is limited)."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="mdk-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def card(self, card_id="mun.test", version="1.0.0"):
        return sim.SimCard(self.scratch / "card", card_id, version, SAVES)

    def gate(self, card=None, **options):
        gate = sim.SaveGate(self.scratch / "save.sock", card or self.card(), "stest", **options)
        self.addCleanup(gate.close)
        return gate


def ask(path, data: bytes, timeout: float = 3.0, half_close: bool = False):
    """Send `data` to the socket and return what comes back before `timeout`:
    the decoded answer line, "" when the peer closed without one, or None
    when nothing came at all."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        try:
            connection.sendall(data)
        except OSError:
            pass                        # the peer may close while a long line is still being sent
        if half_close:
            connection.shutdown(socket.SHUT_WR)
        received = b""
        try:
            while b"\n" not in received:
                chunk = connection.recv(65536)
                if not chunk:
                    return json.loads(received) if received.strip() else ""
                received += chunk
        except socket.timeout:
            return None
        except OSError:
            return ""
        return json.loads(received.split(b"\n", 1)[0])


def save_line(data, schema=1, kind="save") -> bytes:
    return (json.dumps({"type": kind, "schema": schema, "data": data}) + "\n").encode("utf-8")


def compile_munc(directory: Path) -> Path:
    """The client's test program, built for this computer, with the sanitizers
    the compiler offers."""
    compiler = shutil.which("cc")
    if not compiler:
        raise unittest.SkipTest("no C compiler on this computer")
    target = directory / "munc"
    sources = [str(ROOT / "client" / "c" / "tests" / "munc.c"), str(ROOT / "client" / "c" / "mun.c")]
    common = [compiler, "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-g", f"-I{ROOT / 'client' / 'c'}"]
    for extra in (["-fsanitize=address,undefined", "-fno-sanitize-recover=all"], []):
        done = subprocess.run(common + extra + ["-o", str(target)] + sources, capture_output=True, text=True)
        if done.returncode == 0:
            return target
    raise AssertionError("the client does not compile:\n" + done.stderr)


def munc(program: Path, arguments, environment=None, stdin: bytes = b"", timeout: float = 20.0) -> dict:
    """Run munc and return its "name=value" lines as a dictionary."""
    env = {"PATH": os.environ.get("PATH", ""), **(environment or {})}
    done = subprocess.run([str(program), *arguments], input=stdin, capture_output=True, env=env, timeout=timeout)
    if done.returncode not in (0, 2) or done.stderr:
        raise AssertionError(f"munc {arguments} ended with {done.returncode}:\n{done.stderr.decode('utf-8', 'replace')}")
    fields = {}
    for line in done.stdout.decode("utf-8").splitlines():
        name, _, value = line.partition("=")
        fields[name] = value
    return fields
