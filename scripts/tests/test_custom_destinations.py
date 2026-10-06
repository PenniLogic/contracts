"""ADR-022 source binding, schema and recursive Spectral regression controls."""

from __future__ import annotations

import subprocess
import unittest

from support import ROOT
from pl_contracts import node_executable


class CustomDestinationContractTest(unittest.TestCase):
    def test_canonical_source_schemas_enums_and_recursive_lint_controls(self) -> None:
        completed = subprocess.run(
            [node_executable(), "--test", str(ROOT / "scripts" / "tests" / "custom_destinations.test.cjs")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
