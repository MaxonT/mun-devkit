# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0
"""Git for the tests' own repositories, apart from the developer's.

Some tests make repositories, and some run code that asks Git about one (the
MUN OS checkout's revision, the devkit's own, the files it tracks). Git would
otherwise bring along what the developer set up for their own work: global
and system configuration with hooks (core.hooksPath), a template directory
whose hooks every new repository copies (init.templateDir), a file-system
monitor that `git status` runs (core.fsmonitor), commit signing; and, when a
Git hook runs the tests, the variables Git sets for that hook (GIT_DIR,
GIT_INDEX_FILE), which would point every `git -C DIR` at the repository being
committed.

isolate() gives a test an environment with none of that, for every git it or
the code under test starts, and for their children: no GIT_* variable of the
caller; HOME and XDG_CONFIG_HOME in a directory of the test's own, whose only
configuration is written here (no hooks, an empty template, no monitor, no
signing); no system configuration or attributes.

The last needs more than GIT_CONFIG_NOSYSTEM in the environment: code that
drops Git's variables before calling git could drop that one too, and its git
would then read the machine's /etc/gitconfig. So on POSIX the PATH starts
with a directory of the test's own holding a `git` that sets
GIT_CONFIG_NOSYSTEM and GIT_ATTR_NOSYSTEM as it starts and runs the git that
PATH found before. tests/test_git_isolation.py runs the tests that use Git
under a hostile configuration and a foreign GIT_DIR, to keep it so.

The same arrangement as MUN OS's tests/gitenv.py.
"""

import os
import shlex
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch


def _quoted(path: Path) -> str:
    """A path as a Git configuration value."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def environment(root: Path) -> dict:
    """The environment for git under `root`, a directory of the test's own.

    Writes root/git-home (HOME) with its configuration, and an empty
    directory that serves both as the hooks directory and as the template.
    The container runtime keeps finding its own settings (DOCKER_CONFIG names
    the developer's, where it was not already set): they are not Git's."""
    home = Path(root) / "git-home"
    empty = home / "nothing"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    empty.mkdir(exist_ok=True)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = MUN Devkit tests\n\temail = tests@example.invalid\n"
        f"[core]\n\thooksPath = {_quoted(empty)}\n\tfsmonitor = false\n"
        f"[init]\n\ttemplateDir = {_quoted(empty)}\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n"
        "[tag]\n\tgpgsign = false\n")
    result = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if "DOCKER_CONFIG" not in result and result.get("HOME"):
        result["DOCKER_CONFIG"] = str(Path(result["HOME"]) / ".docker")
    result.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                  GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    launcher = _launcher(home / "bin", result.get("PATH", os.defpath))
    if launcher:
        result["PATH"] = launcher + os.pathsep + result.get("PATH", os.defpath)
    return result


def _launcher(directory: Path, path: str):
    """A directory whose `git` runs the git found on `path` with no system
    configuration, or None where that cannot be done (no git, not POSIX)."""
    # Skips this very directory: a second call for the same root, under a PATH
    # that already starts with it, would otherwise make the launcher run itself.
    path = os.pathsep.join(entry for entry in path.split(os.pathsep)
                           if entry and Path(entry).resolve() != directory.resolve())
    git = shutil.which("git", path=path)
    if os.name != "posix" or not git:
        return None
    directory.mkdir(exist_ok=True)
    script = directory / "git"
    script.write_text("#!/bin/sh\n"
                      "GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1\n"
                      "export GIT_CONFIG_NOSYSTEM GIT_ATTR_NOSYSTEM\n"
                      f"exec {shlex.quote(git)} \"$@\"\n")
    script.chmod(0o755)
    return str(directory)


def isolate(test: unittest.TestCase, root: Path) -> dict:
    """Put os.environ under environment(root) until `test` ends."""
    values = environment(root)
    patcher = patch.dict(os.environ, values, clear=True)
    patcher.start()
    test.addCleanup(patcher.stop)
    return values
