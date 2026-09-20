"""Path conventions shared by the host tools and the helpers copied into a target's environment.

Standard library only: this module is copied next to benchlib and profilelib into the target's
virtualenv and runner image, where Hotpath itself is not installed.
"""
from __future__ import annotations

from pathlib import PurePosixPath

TEST_DIRS = ("tests", "test", "testing", "__tests__")


def is_test_path(rel: str) -> bool:
    """True for files that belong to a test suite in any of the supported ecosystems."""
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts:
        return False
    name = parts[-1]
    return (any(p in TEST_DIRS for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py" or ".test." in name or ".spec." in name or name.endswith("_test.go"))
