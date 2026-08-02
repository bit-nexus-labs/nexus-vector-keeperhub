"""Fail-closed hygiene verifier for tracked public-repository files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
_MAX_TEXT_BYTES = 2_000_000

_FORBIDDEN_EXACT_NAMES = frozenset(
    {
        ".env",
        "terminal.txt",
        "trades.log",
        "nexus_runtime.log",
        "bot_data.db",
        "trades_data.db",
    }
)
_FORBIDDEN_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".p12",
    ".pfx",
)
_FORBIDDEN_COMPONENTS = frozenset({"__pycache__", ".venv", "logs"})

_SECRET_PATTERNS = (
    ("PRIVATE_KEY_MATERIAL", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GITHUB_TOKEN", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GITHUB_FINE_GRAINED_TOKEN", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("KEEPERHUB_KEY", re.compile(rb"\bkh_[A-Za-z0-9]{20,}\b")),
    ("OPENAI_KEY", re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b")),
)


class RepositoryHygieneError(RuntimeError):
    """Machine-classifiable hygiene failure without secret-value echo."""

    def __init__(self, code: str, path: str | None = None) -> None:
        self.code = code
        self.path = path
        message = code if path is None else f"{code}:{path}"
        super().__init__(message)


def _fail(code: str, path: str | None = None) -> None:
    raise RepositoryHygieneError(code, path)


def _tracked_paths() -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        _fail("TRACKED_FILE_ENUMERATION_FAILED")
    try:
        decoded = completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        _fail("TRACKED_PATH_NOT_UTF8")
    paths = tuple(path for path in decoded.split("\0") if path)
    if not paths:
        _fail("EMPTY_TRACKED_FILE_SET")
    return paths


def _validate_relative_path(raw_path: str) -> PurePosixPath:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        _fail("INVALID_TRACKED_PATH")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        _fail("UNSAFE_TRACKED_PATH", raw_path)
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = lowered_parts[-1]
    if name in _FORBIDDEN_EXACT_NAMES:
        _fail("FORBIDDEN_TRACKED_FILE", raw_path)
    if name.startswith(".env.") or name.endswith(_FORBIDDEN_SUFFIXES):
        _fail("FORBIDDEN_TRACKED_FILE", raw_path)
    if any(part in _FORBIDDEN_COMPONENTS for part in lowered_parts):
        _fail("FORBIDDEN_TRACKED_COMPONENT", raw_path)
    return path


def verify_paths(paths: Iterable[str], *, root: Path = ROOT) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = _validate_relative_path(raw_path)
        canonical = path.as_posix()
        if canonical in seen:
            _fail("DUPLICATE_TRACKED_PATH", canonical)
        seen.add(canonical)
        target = root.joinpath(*path.parts)
        try:
            if not target.is_file():
                _fail("TRACKED_FILE_MISSING", canonical)
            size = target.stat().st_size
            if size > _MAX_TEXT_BYTES:
                continue
            content = target.read_bytes()
        except OSError:
            _fail("TRACKED_FILE_UNREADABLE", canonical)
        for code, pattern in _SECRET_PATTERNS:
            if pattern.search(content) is not None:
                _fail(code, canonical)
        normalized.append(canonical)
    return tuple(normalized)


def verify() -> tuple[str, ...]:
    return verify_paths(_tracked_paths())


def main() -> int:
    try:
        paths = verify()
    except RepositoryHygieneError as error:
        location = "" if error.path is None else f" [{error.path}]"
        print(f"REPOSITORY_HYGIENE_VERIFY: FAIL ({error.code}){location}")
        return 1
    print(f"REPOSITORY_HYGIENE_VERIFY: PASS ({len(paths)} scanned text files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
