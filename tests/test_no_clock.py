# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""The tests wait for states, not for time.

A test that sleeps "long enough", or tells the simulator to act after so
many seconds, passes or fails with the load of the computer that runs it. A
test here waits for what it needs to have happened (a save is in the card,
the service has attended a request, a process has ended), and a timeout in
it only bounds a failure: it never decides a result.

This reads the tests themselves (their syntax trees) and names every place
where one of them hands a decision to the clock."""

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The simulator's options and arguments that act after an amount of time.
# They exist for a person at a keyboard; a test uses --stop-when and
# --hold-saves, which act on what the game has done.
TIMED = {"--stop-after", "--save-delay"}
TIMED_ARGUMENTS = {"stop_after", "save_delay"}
# The tests of those two options themselves. Each gives its option a value
# under which nothing depends on how long anything takes (stop at once; a
# delay nobody waits for). A sleep is allowed nowhere.
OF_THE_OPTION = {"test_a_card_s_delay_ends_with_the_session", "test_stop_after_asks_the_game_to_stop"}


def clocked(tree: ast.AST):
    """(line, what) for each wait on the clock in a module."""
    found = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        timed_allowed = function in OF_THE_OPTION
        if isinstance(node, ast.Call):
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name == "sleep":
                found.append((node.lineno, "sleep()"))
            if not timed_allowed:
                found.extend((node.lineno, f"{keyword.arg}=") for keyword in node.keywords
                             if keyword.arg in TIMED_ARGUMENTS)
        elif isinstance(node, ast.Constant) and node.value in TIMED and not timed_allowed:
            found.append((node.lineno, node.value))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "")
    return sorted(found)


class NoClock(unittest.TestCase):
    def test_no_test_waits_for_an_amount_of_time(self):
        found = []
        for path in sorted(HERE.glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            found += [f"{path.name}:{line}: {what}" for line, what in clocked(tree)]
        self.assertEqual(found, [])

    def test_the_reader_sees_each_kind(self):
        tree = ast.parse("import time\ntime.sleep(1)\nsleep(2)\nrun('--stop-after', '1')\n"
                         "gate(save_delay=1)\nwait_for(state)\nx = '--stop-when'\n"
                         "def test_stop_after_asks_the_game_to_stop():\n"
                         "    run('--stop-after', '0')\n    time.sleep(1)\n")
        self.assertEqual(clocked(tree), [(2, "sleep()"), (3, "sleep()"), (4, "--stop-after"), (5, "save_delay="),
                                         (10, "sleep()")])


if __name__ == "__main__":
    unittest.main()
