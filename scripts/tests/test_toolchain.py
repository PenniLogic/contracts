"""Toolchain pins: every download is https and SHA-256 pinned; a mismatching download is refused and discarded."""

from __future__ import annotations

import io
import json
import re
import unittest
from unittest import mock

from support import ROOT

import pl_contracts
import toolchain

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PinsTest(unittest.TestCase):
    pins = pl_contracts.versions()

    def test_generator_and_oasdiff_pins_are_well_formed(self) -> None:
        generator = self.pins["openapi_generator"]
        self.assertRegex(generator["sha256"], HEX64)
        self.assertTrue(generator["url"].startswith("https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/"))
        self.assertIn(generator["version"], generator["url"])
        for key, asset in self.pins["oasdiff"]["assets"].items():
            self.assertIn(key, ("linux_amd64", "windows_amd64"))
            self.assertRegex(asset["sha256"], HEX64)
            self.assertTrue(asset["url"].startswith("https://github.com/oasdiff/oasdiff/releases/download/v" + self.pins["oasdiff"]["version"] + "/"))
        self.assertRegex(self.pins["gradle"]["distribution_sha256"], HEX64)
        self.assertRegex(self.pins["gradle"]["wrapper_jar_sha256"], HEX64)

    def test_gradle_wrapper_files_match_the_pins(self) -> None:
        wrapper = ROOT / "smoke" / "kotlin" / "gradle" / "wrapper"
        self.assertEqual(pl_contracts.sha256_file(wrapper / "gradle-wrapper.jar"), self.pins["gradle"]["wrapper_jar_sha256"])
        properties = (wrapper / "gradle-wrapper.properties").read_text(encoding="utf-8")
        self.assertIn(f"distributionSha256Sum={self.pins['gradle']['distribution_sha256']}", properties)
        self.assertIn(f"gradle-{self.pins['gradle']['version']}-bin.zip", properties)
        self.assertIn("validateDistributionUrl=true", properties)

    def test_node_pins_match_the_lockfile(self) -> None:
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/@stoplight/spectral-cli"]["version"], self.pins["spectral"]["version"])
        self.assertEqual(lock["packages"]["node_modules/typescript"]["version"], self.pins["typescript"]["version"])
        self.assertTrue(lock["packages"]["node_modules/@stoplight/spectral-cli"]["integrity"].startswith("sha512-"))
        self.assertEqual((ROOT / ".nvmrc").read_text(encoding="utf-8").strip(), "24.14.0")

    def test_kotlin_pins_match_the_build_file(self) -> None:
        build = (ROOT / "smoke" / "kotlin" / "build.gradle.kts").read_text(encoding="utf-8")
        kotlin = self.pins["kotlin"]
        self.assertIn(f'kotlin("jvm") version "{kotlin["version"]}"', build)
        self.assertIn(f'val ktorVersion = "{kotlin["ktor"]}"', build)
        self.assertIn(f'val serializationVersion = "{kotlin["kotlinx_serialization"]}"', build)
        self.assertTrue((ROOT / "smoke" / "kotlin" / "gradle" / "verification-metadata.xml").is_file())

    def test_python_requirements_are_hash_pinned(self) -> None:
        text = (ROOT / "smoke" / "python" / "requirements.txt").read_text(encoding="utf-8")
        requirements = [line for line in text.splitlines() if line and not line.startswith(("#", " ", "-"))]
        self.assertTrue(requirements)
        for line in requirements:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==\S+", line)
        self.assertGreaterEqual(text.count("--hash=sha256:"), len(requirements))


class DownloadRefusalTest(unittest.TestCase):
    def test_mismatching_digest_is_refused(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b"not the pinned artifact")
        with mock.patch.object(toolchain.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(pl_contracts.PipelineError) as caught:
                toolchain.download("https://example.invalid/tool.jar", "0" * 64)
        self.assertIn("SHA-256 mismatch", str(caught.exception))
        self.assertIn("discarded", str(caught.exception))

    def test_plain_http_is_refused(self) -> None:
        with self.assertRaises(pl_contracts.PipelineError):
            toolchain.download("http://example.invalid/tool.jar", "0" * 64)

    def test_matching_digest_is_returned(self) -> None:
        payload = b"pinned"
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(payload)
        with mock.patch.object(toolchain.urllib.request, "urlopen", return_value=response):
            self.assertEqual(toolchain.download("https://example.invalid/tool.jar", pl_contracts.sha256_bytes(payload)), payload)

    def test_verify_reports_missing_tools_actionably(self) -> None:
        pins = json.loads(json.dumps(pl_contracts.versions()))
        pins["openapi_generator"]["version"] = "0.0.0-absent"
        with self.assertRaises(pl_contracts.PipelineError) as caught:
            toolchain.verify(pins)
        self.assertIn("python scripts/toolchain.py install", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
