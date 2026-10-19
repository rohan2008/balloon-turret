"""
Proof that laser_should_fire() is the ONLY way an "L1" (laser on) command
can ever be produced.

Two independent checks:
  1. STATIC (reads the source code): the text "L1" appears in exactly one
     place in the program, that place is only reachable through
     TurretOutputs.set(laser=...), and every call to that passes either
     False or the result of laser_should_fire().
  2. RUNTIME: run many scenarios with LASER_ENABLED patched to True and a
     fake Arduino that FAILS the test if "L1" is ever sent while the most
     recent laser_should_fire() answer was not True.
"""

import ast
import glob
import os
import unittest
from unittest import mock

from tests.fakes import patch_all, REPO, FakeSerial, box_at, run_tracker, tt
import tests.fakes as fakes

PROJECT_FILES = [p for p in glob.glob(os.path.join(REPO, "*.py"))]


def parse_all():
    trees = {}
    for path in PROJECT_FILES:
        with open(path, encoding="utf-8") as f:
            trees[os.path.basename(path)] = ast.parse(f.read())
    return trees


def enclosing_functions(tree):
    """Map every AST node -> name of the function it's inside ('' at module level)."""
    owner = {}

    def visit(node, name):
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            owner[child] = child_name
            visit(child, child_name)
    visit(tree, "")
    return owner


class StaticProof(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.trees = parse_all()

    def test_L1_text_appears_in_exactly_one_function(self):
        places = []
        for name, tree in self.trees.items():
            owner = enclosing_functions(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "L1":
                    places.append((name, owner.get(node, "")))
        self.assertEqual(len(places), 1, f'"L1" must appear exactly once, found: {places}')
        self.assertEqual(places[0][1], "set_laser")

    def test_set_laser_can_only_turn_on_from_outputs_set(self):
        """set_laser(link, ON?, ...) may be called with a variable only inside
        TurretOutputs.set; everywhere else the ON argument must be the constant False."""
        callers = []
        for name, tree in self.trees.items():
            owner = enclosing_functions(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "set_laser":
                    on_arg = node.args[1]
                    always_off = isinstance(on_arg, ast.Constant) and on_arg.value is False
                    callers.append((owner.get(node, ""), always_off))
        self.assertIn(("set", False), callers)
        for function, always_off in callers:
            if function != "set":
                self.assertTrue(always_off, f"{function}() may call set_laser() only with False")

    def test_every_laser_true_comes_from_laser_should_fire(self):
        """Every outputs.set(laser=X): X is the constant False, or a variable
        that is only ever assigned from laser_should_fire(...)."""
        for name, tree in self.trees.items():
            assigned_from = {}           # variable name -> list of value node types/calls
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assigned_from.setdefault(target.id, []).append(node.value)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "set"):
                    continue
                laser_args = [k.value for k in node.keywords if k.arg == "laser"]
                for value in laser_args:
                    if isinstance(value, ast.Constant):
                        self.assertIs(value.value, False, f"{name}: laser=True constant!")
                        continue
                    self.assertIsInstance(value, ast.Name, f"{name}: laser= must be a variable")
                    sources = assigned_from.get(value.id, [])
                    self.assertTrue(sources, f"{name}: {value.id} never assigned")
                    for src in sources:
                        self.assertTrue(isinstance(src, ast.Call)
                                        and getattr(src.func, "id", None) == "laser_should_fire",
                                        f"{name}: {value.id} assigned from something other "
                                        f"than laser_should_fire()")


class RuntimeProof(unittest.TestCase):
    """Every L1 on the wire must be preceded by a laser_should_fire() == True."""

    def run_checked(self, argv, **kwargs):
        decisions = []
        real_decide = tt.laser_should_fire

        def recording_decide(*a, **k):
            result = real_decide(*a, **k)
            decisions.append(result)
            return result

        violations = []
        real_write = FakeSerial.write

        def checked_write(port, data):
            if data.decode().strip() == "L1" and not (decisions and decisions[-1] is True):
                violations.append(len(decisions))
            real_write(port, data)

        with patch_all("LASER_ENABLED", True), \
                patch_all("laser_should_fire", recording_decide), \
                mock.patch.object(FakeSerial, "write", checked_write):
            result = run_tracker(argv, **kwargs)
        self.assertEqual(violations, [], "L1 sent without laser_should_fire() saying yes")
        return result

    def test_auto_mode_lock_loss_and_relock(self):
        det = lambda i: None if 20 <= i <= 23 else box_at(325, 243, 30)
        r = self.run_checked(["--port", "COM9"], detector=det, frames=50)
        self.assertIn("L1", r.serial_lines)

    def test_manual_button_and_kill_key(self):
        r = self.run_checked(["--port", "COM9", "--mode", "manual"],
                             detector=lambda i: box_at(325, 243, 30), frames=40,
                             button_fn=lambda i: 8 <= i <= 30, keys={25: ord("q")})
        self.assertIn("L1", r.serial_lines)

    def test_focus_and_detector_errors(self):
        def det(i):
            if i in (30, 31):
                raise RuntimeError("boom")
            return box_at(325, 243, 30)
        self.run_checked(["--port", "COM9"], detector=det, frames=60,
                         focus_fn=lambda i: i != 50)


if __name__ == "__main__":
    unittest.main()
