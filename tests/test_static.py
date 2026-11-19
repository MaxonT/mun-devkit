# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""The devkit's sources under their analysers, and the warnings a person
reviewed and kept.

The C a game is made of (the client, the sample, their tests) is compiled
in the SDK environment with the compiler's own analyser (-fanalyzer) and
must give no warning at all.

pyflakes must find nothing. Bandit's warnings are each looked at; one that
is not a defect is marked on its own line with the rule and the reason:

    done = subprocess.run(command, check=False)  # nosec B603: no shell; the arguments are ...

Never for a file, a directory or a rule as a whole. The marks are held to
what the analyser says with them ignored: every warning has its mark, and
every mark its warning, rule for rule. A mark that outlives its warning, or
names a rule that is not reported there, fails here.

The analysers are pinned in tools/dev-requirements.txt and installed by
`make dev-tools` into .local/venv. Without them these tests are skipped,
with the reason; `make check-full` fails instead."""

import io
import json
import re
import subprocess  # nosec B404: runs the two pinned analysers on this repository's own files
import tokenize
import unittest

from support import MUN_OS, ROOT, SAMPLE, needs

from mdk import container
from mdk.errors import MdkError

BIN = ROOT / ".local" / "venv" / "bin"
SOURCES = ["mdk", "tools", "tests", "samples", "engines"]
MARK = re.compile(r"# nosec (B\d{3}(?:, B\d{3})*): (\S.*)")


def ready() -> str:
    missing = [name for name in ("bandit", "pyflakes") if not (BIN / name).is_file()]
    return f"{' and '.join(missing)} not installed (run `make dev-tools`)" if missing else ""


def python_files():
    found = [ROOT / "mdk"]
    for source in SOURCES[1:]:
        found += [path for path in (ROOT / source).rglob("*.py") if "build" not in path.relative_to(ROOT).parts]
    return sorted(found)


def marks():
    """{(file, line): (rules, reason)} for every comment that speaks to the
    analyser, and the comments that do so without the form."""
    found, malformed = {}, []
    for path in python_files():
        name = str(path.relative_to(ROOT))
        for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if token.type != tokenize.COMMENT or not re.search(r"\bno(sec|qa)\b", token.string):
                continue
            match = MARK.fullmatch(token.string)
            if match and len(match.group(2).split()) >= 3:
                found[(name, token.start[0])] = (set(match.group(1).split(", ")), match.group(2))
            else:
                malformed.append(f"{name}:{token.start[0]}: {token.string}")
    return found, malformed


class Marks(unittest.TestCase):
    def test_a_mark_names_its_rules_and_gives_a_reason(self):
        _, malformed = marks()
        self.assertEqual(malformed, [])

    def test_nothing_silences_a_rule_or_a_file_as_a_whole(self):
        for name in (".bandit", "bandit.yaml", "bandit.yml", ".flake8", "setup.cfg", "tox.ini"):
            self.assertFalse((ROOT / name).exists(), name)
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for option in ("--skip", "--exclude", "--ignore-nosec", "--severity-level", "--confidence-level", " -s ", " -x "):
            self.assertNotIn(option, "\n".join(line for line in makefile.splitlines() if "bandit" in line), option)


@needs(ready())
class Analysers(unittest.TestCase):
    def bandit(self, *options):
        done = subprocess.run(  # nosec B603: the pinned analyser, by its full path, on fixed directories
            [str(BIN / "bandit"), "-q", "-r", *SOURCES, "-f", "json", *options], cwd=ROOT, capture_output=True, text=True)
        try:
            return json.loads(done.stdout)
        except ValueError:
            self.fail(f"bandit gave no report:\n{done.stdout}\n{done.stderr}")

    def test_pyflakes_finds_nothing(self):
        done = subprocess.run(  # nosec B603: the pinned analyser, by its full path, on fixed directories
            [str(BIN / "pyflakes"), *SOURCES], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((done.stdout.splitlines(), done.stderr), ([], ""))

    def test_bandit_finds_nothing_that_was_not_reviewed(self):
        report = self.bandit()
        self.assertEqual(report["errors"], [])
        self.assertEqual([f"{issue['filename']}:{issue['line_number']} {issue['test_id']} {issue['issue_text']}"
                          for issue in report["results"]], [])

    def test_every_warning_has_its_mark_and_every_mark_its_warning(self):
        found, _ = marks()
        warned = {}
        for issue in self.bandit("--ignore-nosec")["results"]:
            lines = [line for line in issue["line_range"] if (issue["filename"], line) in found]
            where = (issue["filename"], lines[0] if lines else issue["line_number"])
            warned.setdefault(where, set()).add(issue["test_id"])
        marked = {where: rules for where, (rules, _) in found.items()}
        self.assertEqual({f"{name}:{line}": sorted(rules) for (name, line), rules in sorted(marked.items())},
                         {f"{name}:{line}": sorted(rules) for (name, line), rules in sorted(warned.items())})


C_SOURCES = ["src/main.c", "src/game.c", "src/draw.c", "src/sound.c", "src/config.c", "tests/test_game.c",
             "/opt/mun-devkit/client/c/mun.c", "/opt/mun-devkit/client/c/tests/munc.c"]
ANALYSE = ('for f in "$@"; do cc -std=c11 -O2 -fanalyzer -Wall -Wextra -Wpedantic -Wshadow -Wconversion '
           '-Wno-sign-conversion -Isrc -I/opt/mun-devkit/client/c $(sdl2-config --cflags) -c -o /dev/null "$f" '
           '|| echo "$f: not compiled"; done 2>&1')


def environment() -> str:
    try:
        return "" if container.image_exists() else "the SDK environment is not built (run `./mdk env build`)"
    except MdkError as exc:
        return exc.message


@needs(environment())
class CompilerAnalyser(unittest.TestCase):
    def test_the_c_sources_give_no_warning(self):
        done = container.run(["sh", "-c", ANALYSE, "analyse", *C_SOURCES], SAMPLE, MUN_OS, capture=True)
        self.assertEqual((done.stdout.splitlines(), done.returncode), ([], 0), done.stderr)


if __name__ == "__main__":
    unittest.main()
