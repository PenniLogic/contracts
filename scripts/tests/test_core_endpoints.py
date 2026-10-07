"""Original #1 source fixtures and negative controls, not a backend or proof verifier."""

from __future__ import annotations

import subprocess
import unittest

from support import ROOT
from pl_contracts import node_executable


class CoreEndpointContractTest(unittest.TestCase):
    def test_endpoint_fixtures_and_negative_source_boundaries(self) -> None:
        completed = subprocess.run(
            [node_executable(), "--test", str(ROOT / "scripts" / "tests" / "core_endpoints.test.cjs")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
