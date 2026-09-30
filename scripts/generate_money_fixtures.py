"""Generate spec/fixtures/money-roundtrip-generated.v1.json deterministically (ADR-015 §7).

The file carries at least 10 000 seeded values across every registry currency plus every boundary
vector, each as the canonical wire object and its minor units. Every language's
CrossLanguageMoneyRoundTripTest reads it in place, round-trips every value, and checks the SHA-256 of
its own emitted `amount|currency|minor_units` lines against `round_trip_sha256`, so the three outputs
are byte-identical to each other and to this file.

The pseudo-random source is SplitMix64 (stated in the header) so the file can be regenerated in any
language; `python scripts/generate_money_fixtures.py --check` fails when the committed file differs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pl_contracts import FIXTURES, REGISTRY, fail  # noqa: E402

TARGET = FIXTURES / "money-roundtrip-generated.v1.json"
SEED = 0x50454E4E494C4F47  # "PENNILOG" in ASCII, recorded in the file header
COUNT = 10_000
MAX_MINOR_UNITS = 2**63 - 1
MASK = (1 << 64) - 1


class SplitMix64:
    """SplitMix64 (Steele, Lea, Flood 2014): the reference algorithm, so any language reproduces the stream."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return z ^ (z >> 31)

    def below(self, bound: int) -> int:
        """Uniform integer in [0, bound) by rejection sampling on the top bits (bound < 2**63)."""
        threshold = (MASK + 1) - ((MASK + 1) % bound)
        while True:
            value = self.next()
            if value < threshold:
                return value % bound


def format_amount(minor_units: int, exponent: int) -> str:
    negative = minor_units < 0
    digits = str(-minor_units if negative else minor_units).rjust(exponent + 1, "0")
    integer_part, fraction = digits[: len(digits) - exponent], digits[len(digits) - exponent:]
    unsigned = integer_part if exponent == 0 else f"{integer_part}.{fraction}"
    return f"-{unsigned}" if negative else unsigned


def boundary_rows(currencies: list[dict]) -> list[dict]:
    rows = []
    for entry in currencies:
        for name, minor in (
            ("zero", 0), ("one minor unit", 1), ("negative minor unit", -1),
            ("2^53-1", 2**53 - 1), ("2^53+1", 2**53 + 1), ("maximum", MAX_MINOR_UNITS), ("minimum", -MAX_MINOR_UNITS),
            ("ten", 10), ("one major unit", 10 ** entry["exponent"]), ("negative one major unit", -(10 ** entry["exponent"])),
        ):
            rows.append({"name": f"{name} {entry['code']}", "wire": {"amount": format_amount(minor, entry["exponent"]), "currency": entry["code"]}, "minor_units": str(minor)})
    return rows


def generated_rows(currencies: list[dict], seed: int, count: int) -> list[dict]:
    rng = SplitMix64(seed)
    rows = []
    for index in range(count):
        entry = currencies[index % len(currencies)]
        digits = 1 + rng.below(19)  # 1..19 significant digits so every magnitude class appears
        low = 10 ** (digits - 1)
        span = min(10**digits, MAX_MINOR_UNITS + 1) - low
        magnitude = low + rng.below(span)
        minor = -magnitude if rng.below(2) == 1 else magnitude
        rows.append({"wire": {"amount": format_amount(minor, entry["exponent"]), "currency": entry["code"]}, "minor_units": str(minor)})
    return rows


def round_trip_digest(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['wire']['amount']}|{row['wire']['currency']}|{row['minor_units']}\n".encode("ascii"))
    return digest.hexdigest()


def render(seed: int = SEED, count: int = COUNT) -> str:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    currencies = sorted(registry["currencies"], key=lambda e: e["code"])
    boundaries = boundary_rows(currencies)
    generated = generated_rows(currencies, seed, count)
    rows = boundaries + generated
    header = {
        "schema_version": 1,
        "decision": "ADR-015 §7",
        "description": (
            "Seeded, deterministic money vectors across every registry currency plus every boundary vector, consumed unchanged by "
            "every language's CrossLanguageMoneyRoundTripTest. Regenerate with python scripts/generate_money_fixtures.py; "
            "`--check` fails when this file differs from the generator output. Synthetic values only."
        ),
        "generator": "scripts/generate_money_fixtures.py",
        "algorithm": "SplitMix64; per value: currency = registry[index mod 3] (codes sorted), digits = 1 + below(19), magnitude = 10^(digits-1) + below(span), sign = below(2); rejection sampling on the top 64 bits for below(bound)",
        "seed": f"0x{seed:016X}",
        "boundary_count": len(boundaries),
        "generated_count": len(generated),
        "count": len(rows),
        "round_trip_line_format": "amount|currency|minor_units\\n for every row in order, SHA-256 hex",
        "round_trip_sha256": round_trip_digest(rows),
    }
    lines = [",\n".join("    " + json.dumps(row, separators=(", ", ": ")) for row in rows)]
    body = "{\n" + ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in header.items())
    return body + ',\n  "values": [\n' + lines[0] + "\n  ]\n}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when the committed file differs from the generator output")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != content.encode("utf-8"):
            return fail(f"{TARGET.relative_to(FIXTURES.parent.parent).as_posix()} differs from the generator output; run python scripts/generate_money_fixtures.py")
        print(f"{TARGET.name}: matches the generator output ({content.count(chr(10))} lines)")
        return 0
    TARGET.write_bytes(content.encode("utf-8"))
    print(f"wrote {TARGET.relative_to(FIXTURES.parent.parent).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
