"""Download and verify the pinned generator and diff tools (SHA-256 checked, refused on mismatch).

Usage:
  python scripts/toolchain.py install     download openapi-generator-cli and oasdiff into .toolchain/
  python scripts/toolchain.py verify      verify the cached files against toolchain/versions.json
  python scripts/toolchain.py paths       print the resolved tool paths as JSON
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import PipelineError, TOOLCHAIN, fail, sha256_bytes, sha256_file, versions  # noqa: E402

USER_AGENT = "PenniLogic-contracts-toolchain/1 (+https://github.com/PenniLogic/contracts)"


def platform_key() -> str:
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        raise PipelineError(f"unsupported CPU architecture for the pinned oasdiff binary: {machine}")
    if sys.platform.startswith("linux"):
        return "linux_amd64"
    if sys.platform.startswith("win"):
        return "windows_amd64"
    raise PipelineError(f"unsupported platform for the pinned oasdiff binary: {sys.platform}; add a pinned asset to toolchain/versions.json")


def download(url: str, expected_sha256: str, attempts: int = 3) -> bytes:
    if not url.startswith("https://"):
        raise PipelineError(f"refusing non-https download: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    data = b""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - https only, pinned URL
                data = response.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts:
                raise PipelineError(f"download failed after {attempts} attempts: {url} ({error})") from error
            print(f"download attempt {attempt} failed ({error}); retrying")
            time.sleep(2 * attempt)
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise PipelineError(
            f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {actual}; the download was discarded. "
            "Re-pin toolchain/versions.json only after verifying the publisher's checksum."
        )
    return data


def generator_jar_path(pins: dict | None = None) -> Path:
    pins = pins or versions()
    return TOOLCHAIN / f"openapi-generator-cli-{pins['openapi_generator']['version']}.jar"


def oasdiff_path(pins: dict | None = None) -> Path:
    pins = pins or versions()
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return TOOLCHAIN / f"oasdiff-{pins['oasdiff']['version']}{suffix}"


def install_generator(pins: dict, quiet: bool = False) -> Path:
    pin = pins["openapi_generator"]
    target = generator_jar_path(pins)
    if target.is_file() and sha256_file(target) == pin["sha256"]:
        if not quiet:
            print(f"openapi-generator-cli {pin['version']} already verified at {target}")
        return target
    print(f"downloading openapi-generator-cli {pin['version']} ...")
    data = download(pin["url"], pin["sha256"])
    TOOLCHAIN.mkdir(exist_ok=True)
    target.write_bytes(data)
    print(f"openapi-generator-cli {pin['version']} verified (sha256 {pin['sha256']})")
    return target


def _marker(target: Path) -> Path:
    return TOOLCHAIN / f"{target.name}.tarball.sha256"


def _oasdiff_verified(target: Path, expected_sha256: str) -> bool:
    # The extracted binary is recorded against the verified tarball digest it came from.
    marker = _marker(target)
    return target.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected_sha256


def install_oasdiff(pins: dict, quiet: bool = False) -> Path:
    pin = pins["oasdiff"]
    asset = pin["assets"][platform_key()]
    target = oasdiff_path(pins)
    if _oasdiff_verified(target, asset["sha256"]):
        if not quiet:
            print(f"oasdiff {pin['version']} already verified at {target}")
        return target
    print(f"downloading oasdiff {pin['version']} ({platform_key()}) ...")
    data = download(asset["url"], asset["sha256"])
    member_name = "oasdiff.exe" if sys.platform.startswith("win") else "oasdiff"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = next((m for m in archive.getmembers() if m.isfile() and Path(m.name).name == member_name), None)
        if member is None:
            raise PipelineError(f"oasdiff archive does not contain {member_name}")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise PipelineError("oasdiff archive member could not be read")
        binary = extracted.read()
    TOOLCHAIN.mkdir(exist_ok=True)
    target.write_bytes(binary)
    if os.name != "nt":
        target.chmod(0o755)
    _marker(target).write_text(asset["sha256"] + "\n", encoding="utf-8")
    print(f"oasdiff {pin['version']} verified (tarball sha256 {asset['sha256']})")
    return target


def verify(pins: dict) -> None:
    jar = generator_jar_path(pins)
    if not jar.is_file():
        raise PipelineError(f"{jar} is missing; run: python scripts/toolchain.py install")
    if sha256_file(jar) != pins["openapi_generator"]["sha256"]:
        raise PipelineError(f"{jar} does not match the pinned SHA-256; delete .toolchain/ and run: python scripts/toolchain.py install")
    binary = oasdiff_path(pins)
    if not _oasdiff_verified(binary, pins["oasdiff"]["assets"][platform_key()]["sha256"]):
        raise PipelineError(f"{binary} is missing or unverified; run: python scripts/toolchain.py install")


def ensure_installed(quiet: bool = True) -> dict:
    """Install anything missing and return the tool paths (used by the other scripts)."""
    pins = versions()
    install_generator(pins, quiet)
    install_oasdiff(pins, quiet)
    verify(pins)
    return {"openapi_generator": str(generator_jar_path(pins)), "oasdiff": str(oasdiff_path(pins))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("install", "verify", "paths"))
    args = parser.parse_args()
    try:
        if args.command == "install":
            ensure_installed(quiet=False)
        elif args.command == "verify":
            verify(versions())
            print("toolchain verified")
        else:
            pins = versions()
            print(json.dumps({"openapi_generator": str(generator_jar_path(pins)), "oasdiff": str(oasdiff_path(pins))}, indent=2))
    except (PipelineError, OSError) as error:
        return fail(f"toolchain: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
