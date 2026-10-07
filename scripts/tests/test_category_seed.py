"""Exact accepted source publication and derivation, not runtime seed application."""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest

from support import ROOT, SpecDir, run_script, spec_text, section_text, replace_section, replace_once
from test_import_provider import schema_document
from pl_contracts import node_executable


class CategorySeedSourceTest(unittest.TestCase):
    def test_exact_accepted_bytes_and_all_derived_vocabularies_registry_defaults(self) -> None:
        data = (ROOT / "spec" / "category-seed.v1.json").read_bytes()
        self.assertEqual(len(data), 14237)
        self.assertEqual(hashlib.sha256(data).hexdigest(), "3fcf568abcfc7da7409e463ea6bbda9d952af739cfa9ef42a8e9fe90b993390c")
        self.assertEqual(hashlib.sha1(b"blob 14237\0" + data).hexdigest(), "16b70af7b527bfd617539d9bd413ba78738f8f05")
        seed = json.loads(data)
        document = schema_document()
        for name, field, value in (("CategorySystemKey", "categories", "key"), ("CategoryIcon", "icons", "id"),
                                   ("CategoryColour", "colours", "id")):
            self.assertEqual(document["components"]["schemas"][name]["enum"], [row[value] for row in seed[field]])
        self.assertEqual(len(seed["categories"]), 59)
        self.assertEqual(sum(row["parent_key"] is None for row in seed["categories"]), 16)
        self.assertEqual(seed["locales"], ["en-IN"])
        keys = {row["key"]: row for row in seed["categories"]}
        for key in ("debt", "debt.interest", "debt.fees", "fees", "fees.foreign_exchange"):
            self.assertEqual(keys[key]["nature"], "EXPENSE")
        self.assertNotIn("uncategorised", keys)
        for target in ("kotlin", "typescript", "python"):
            manifest = json.loads((ROOT / "build" / "generated" / target / "contracts-manifest.json").read_bytes())
            self.assertEqual(manifest["provider_sources_sha256"]["category-seed.v1.json"], hashlib.sha256(data).hexdigest())
            self.assertEqual((ROOT / "build" / "generated" / target / "category-seed.v1.json").read_bytes(), data)

    def test_byte_or_binding_or_vocab_drift_refuses_on_the_actual_lint_cli(self) -> None:
        for mutation in ("bytes", "pin", "enum"):
            with self.subTest(mutation=mutation):
                spec = SpecDir()
                self.addCleanup(spec.cleanup)
                text = spec_text()
                if mutation == "bytes":
                    with (spec.path / "category-seed.v1.json").open("ab") as stream:
                        stream.write(b"\n")
                elif mutation == "pin":
                    text = text.replace("fd58da679672a4de7aabee2562644bed3799e614", "0" * 40)
                else:
                    target = ("components", "schemas", "CategoryIcon")
                    section = section_text(text, target)
                    old = next(line for line in section.splitlines() if line.startswith("      enum:"))
                    values = schema_document()["components"]["schemas"]["CategoryIcon"]["enum"] + ["unknown_icon"]
                    text = replace_section(text, target,
                        replace_once(section, old, "      enum: " + json.dumps(values)))
                result = run_script("lint_spec.py", "--spec", str(spec.write(text)))
                self.assertEqual(result.returncode, 1)
                self.assertIn("pl-category-seed-binding", result.stderr)
                generation = run_script("generate_clients.py", "--spec", str(spec.path / "openapi.yaml"),
                                        "--output-dir", str(spec.path / "generated"))
                self.assertEqual(generation.returncode, 1)
                self.assertIn("provider constraint generation rejected", generation.stderr)
                self.assertFalse((spec.path / "generated").exists())

    def test_actual_enum_derivation_is_idempotent_without_touching_accepted_bytes(self) -> None:
        spec = SpecDir()
        self.addCleanup(spec.cleanup)
        source = spec.write(spec_text())
        original = source.read_bytes()
        seed = (spec.path / "category-seed.v1.json").read_bytes()
        command = [node_executable(), str(ROOT / "scripts" / "category_seed.cjs"),
                   "--write-enums", str(source)]
        for _ in range(2):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((spec.path / "category-seed.v1.json").read_bytes(), seed)


if __name__ == "__main__":
    unittest.main()
