"""ADR-022 source binding, schema and recursive Spectral regression controls."""

from __future__ import annotations

import subprocess
import unittest

from support import ROOT
from pl_contracts import node_executable

NODE_REPORT_MAX_BYTES = 1024 * 1024


class CustomDestinationContractTest(unittest.TestCase):
    def test_canonical_source_schemas_enums_and_recursive_lint_controls(self) -> None:
        completed = subprocess.run(
            [node_executable(), "--test", str(ROOT / "scripts" / "tests" / "custom_destinations.test.cjs")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(completed.stdout, "Node success report is empty; stderr is not a report fallback")
        self.assertLessEqual(len(completed.stdout.encode("utf-8")), NODE_REPORT_MAX_BYTES,
                             "Node success report exceeds one MiB; nothing truncated or emitted")
        print(completed.stdout, end="", flush=True)


if __name__ == "__main__":
    unittest.main()
