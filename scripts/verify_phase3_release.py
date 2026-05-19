from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        digest, rel = line.split(maxsplit=1)
        entries.append((digest.lower(), rel.strip().lstrip("*")))
    return entries


def verify_release(root: Path) -> int:
    sums_path = root / "SHA256SUMS.txt"
    if not sums_path.exists():
        raise FileNotFoundError(f"missing checksum file: {sums_path}")

    failures: list[str] = []
    for expected, rel in parse_sha256sums(sums_path):
        path = root / rel
        if not path.exists():
            failures.append(f"MISSING {rel}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            failures.append(f"SHA256 {rel}: expected {expected}, got {actual}")

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print(f"OK: verified {len(parse_sha256sums(sums_path))} files under {root}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify optic_system Phase 3 release checksums.")
    parser.add_argument("release_root", type=Path)
    args = parser.parse_args()
    raise SystemExit(verify_release(args.release_root.resolve()))


if __name__ == "__main__":
    main()
