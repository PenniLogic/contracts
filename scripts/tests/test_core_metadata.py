"""Source-bound component history and deprecation declarations, not a published release."""

from __future__ import annotations

import json
import subprocess
import unittest

from support import ROOT
from test_import_provider import schema_document
from pl_contracts import node_executable

BASE = "ffdd507206990cb880baea150ffae2f6a7bb3043"


class CoreMetadataTest(unittest.TestCase):
    def test_component_changelog_exactly_covers_the_complete_accepted_base_delta(self) -> None:
        base_bytes = subprocess.check_output(
            ["git", "show", BASE + ":spec/openapi.yaml"], cwd=ROOT,
        )
        parsed = subprocess.run(
            [node_executable(), "-e",
             "const {Yaml}=require('@stoplight/spectral-parsers');"
             "process.stdout.write(JSON.stringify(Yaml.parse(require('node:fs').readFileSync(0,'utf8')).data));"],
            input=base_bytes, cwd=ROOT, capture_output=True, check=False,
        )
        self.assertEqual(parsed.returncode, 0)
        base = json.loads(parsed.stdout)
        document = schema_document()
        catalogue = document["x-contract-changelog"]
        self.assertEqual(catalogue["schema_version"], 1)
        self.assertEqual(catalogue["format"], "pennilogic-component-changelog@1")
        entry = catalogue["entries"][-1]
        self.assertEqual(entry["version"], document["info"]["version"])
        self.assertEqual(entry["previous_version"], base["info"]["version"])
        self.assertEqual(entry["publication"], "unreleased")
        for group, actual in entry["components"].items():
            old = base["components"].get(group, {})
            current = document["components"].get(group, {})
            expected = {
                "added": sorted(current.keys() - old.keys()),
                "changed": sorted(name for name in current.keys() & old.keys() if old[name] != current[name]),
                "removed": sorted(old.keys() - current.keys()),
            }
            self.assertEqual({key: sorted(value) for key, value in actual.items()}, expected, group)
            for values in actual.values():
                self.assertEqual(len(values), len(set(values)), group)
        self.assertEqual(set(entry["components"]), set(base["components"]) | set(document["components"]))

    def test_retirement_policy_declares_standard_headers_without_an_active_retirement(self) -> None:
        document = schema_document()
        policy = document["x-core-contract"]["compatibility"]
        self.assertEqual(policy["minimumDeprecationNotice"], "P90D")
        self.assertEqual(policy["deprecationHeader"], "#/components/headers/CoreDeprecation")
        self.assertEqual(policy["sunsetHeader"], "#/components/headers/CoreSunset")
        self.assertEqual(policy["immutableRollback"], "re-pin-prior-published-version")
        self.assertFalse(any(operation.get("deprecated") for item in document["paths"].values()
                             for method, operation in item.items() if method in
                             ("get", "post", "put", "patch", "delete", "options", "head", "trace")))


if __name__ == "__main__":
    unittest.main()
