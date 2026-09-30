"""Run one smoke consumer against the generated clients in build/generated/<language>.

  python scripts/smoke.py python      venv + hashed requirements, mypy --strict, import and money conformance tests
  python scripts/smoke.py typescript  tsc strict over the generated client and the consumer, then node --test
  python scripts/smoke.py kotlin      wrapper jar SHA-256 check, then Gradle compiles the client and runs the tests

Each consumer proves that the generated client compiles or imports cleanly and that the ADR-015 money
and instant seams behave (float construction rejected; fixture vectors). Generate first with
`python scripts/generate_clients.py` (or `--verify`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import BUILD, ROOT, PipelineError, fail, node_executable, run, sha256_file, versions  # noqa: E402

GENERATED = BUILD / "generated"
SMOKE = ROOT / "smoke"


def require_generated(language: str) -> Path:
    output = GENERATED / language
    if not (output / "contracts-manifest.json").is_file():
        raise PipelineError(f"{output.relative_to(ROOT).as_posix()} is missing; run python scripts/generate_clients.py first")
    return output


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def smoke_python() -> None:
    generated = require_generated("python")
    venv = BUILD / "venv"
    interpreter = venv_python(venv)
    if not interpreter.is_file():
        print(f"creating virtual environment {venv.relative_to(ROOT).as_posix()}")
        run([sys.executable, "-m", "venv", str(venv)])
    requirements = SMOKE / "python" / "requirements.txt"
    print("installing hashed requirements (pip --require-hashes --no-deps --only-binary :all:)")
    run([str(interpreter), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--require-hashes", "--no-deps",
         "--only-binary", ":all:", "-r", str(requirements)])
    env = {"PYTHONPATH": os.pathsep.join([str(generated), str(SMOKE / "python"), str(SMOKE / "python" / "tests")]), "PYTHONDONTWRITEBYTECODE": "1"}
    print("mypy --strict over the smoke consumer, the seams and the generated models")
    # mypy resolves imports through mypy_path (smoke/python/mypy.ini), not PYTHONPATH.
    run([str(interpreter), "-m", "mypy", "--config-file", str(SMOKE / "python" / "mypy.ini"),
         str(SMOKE / "python" / "consumer.py"), str(SMOKE / "python" / "tests"), str(generated / "pennilogic_contracts")],
        env={"PYTHONDONTWRITEBYTECODE": "1"})
    print("import test and money/instant conformance tests")
    run([str(interpreter), "-m", "unittest", "discover", "-s", str(SMOKE / "python" / "tests"), "-p", "test_*.py", "-v"], env=env)


def smoke_typescript() -> None:
    require_generated("typescript")
    node = node_executable()
    tsc = ROOT / "node_modules" / "typescript" / "bin" / "tsc"
    if not tsc.is_file():
        raise PipelineError("node_modules/typescript is missing; run: npm ci --no-audit --no-fund")
    out_dir = BUILD / "smoke-typescript"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    print("tsc --strict over the generated typescript-fetch client and the smoke consumer")
    run([node, str(tsc), "-p", str(SMOKE / "typescript" / "tsconfig.json"), "--outDir", str(out_dir)])
    (out_dir / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    tests = sorted((out_dir / "smoke" / "typescript" / "test").glob("*.test.js"))
    if not tests:
        raise PipelineError("no compiled TypeScript smoke tests found")
    print("node --test money/instant conformance tests")
    run([node, "--test", *[str(test) for test in tests]], env={"PL_CONTRACTS_ROOT": str(ROOT)})


def smoke_kotlin() -> None:
    require_generated("kotlin")
    project = SMOKE / "kotlin"
    pins = versions()["gradle"]
    jar = project / "gradle" / "wrapper" / "gradle-wrapper.jar"
    actual = sha256_file(jar)
    if actual != pins["wrapper_jar_sha256"]:
        raise PipelineError(f"{jar.relative_to(ROOT).as_posix()} sha256 {actual} does not match toolchain/versions.json ({pins['wrapper_jar_sha256']}); refusing to run Gradle")
    properties = (project / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(encoding="utf-8")
    if f"distributionSha256Sum={pins['distribution_sha256']}" not in properties or f"gradle-{pins['version']}-bin.zip" not in properties:
        raise PipelineError("gradle-wrapper.properties does not pin the Gradle distribution recorded in toolchain/versions.json")
    print(f"wrapper jar verified ({actual}); Gradle {pins['version']} distribution pinned by sha256")
    arguments = ["--no-daemon", "--no-configuration-cache", "--console=plain", "--warning-mode=all", "build"]
    if os.name == "nt":
        command = ["cmd.exe", "/c", str(project / "gradlew.bat"), *arguments]
    else:
        # `sh` runs the wrapper even when a checkout did not preserve the executable bit.
        command = ["sh", str(project / "gradlew"), *arguments]
    run(command, cwd=project)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("language", choices=("python", "typescript", "kotlin"))
    args = parser.parse_args()
    try:
        {"python": smoke_python, "typescript": smoke_typescript, "kotlin": smoke_kotlin}[args.language]()
    except (PipelineError, OSError) as error:
        return fail(f"smoke {args.language} failed: {error}")
    print(f"smoke {args.language}: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
