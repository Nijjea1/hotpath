"""Retrieve only the source the planner needs: the functions around each hotspot."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path, PureWindowsPath

from hotpath.schema import ContextConfig, ProfileSummary
from hotpath.workspace import LockedFileError, path_allowed, safe_target_path

_cache: dict[tuple[str, str], list[tuple[str, int, int, str]]] = {}


def _safe_source(repo: Path, rel: str) -> Path:
    """Reject both path syntaxes and symlink escapes before reading target data."""
    windows = PureWindowsPath(rel)
    if not rel or windows.drive or windows.root or ".." in windows.parts or "\\" in rel or Path(rel).is_absolute():
        raise ValueError(f"unsafe source path: {rel!r}")
    try:
        return safe_target_path(repo, rel)
    except LockedFileError as exc:
        raise ValueError(f"source path escapes repository or uses a symlink: {rel!r}") from exc


def _functions(path: Path) -> list[tuple[str, int, int, str]]:
    """(qualified name, start line, end line, source) for every function in the file, cached by content hash."""
    text = path.read_text(encoding="utf-8")
    key = (str(path), hashlib.sha1(text.encode()).hexdigest())
    if key in _cache:
        return _cache[key]
    out: list[tuple[str, int, int, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _cache[key] = out
        return out
    lines = text.splitlines()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                src = "\n".join(lines[child.lineno - 1: child.end_lineno])
                out.append((name, child.lineno, child.end_lineno, src))
                visit(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    _cache[key] = out
    return out


def build_source_context(repo: Path, profile: ProfileSummary, cfg: ContextConfig,
                         editable: list[str], locked: list[str]) -> str:
    """Source snippets for hotspot functions, most expensive first, within a character budget.

    Only the planner's `max_hotspots` rows are considered: `ProfileSummary` retains a deeper list for
    the before/after diff, and that extra depth must not silently widen the prompt.
    """
    seen: set[tuple[str, str]] = set()
    chunks: list[str] = []
    used = 0
    for h in profile.hotspots[: cfg.max_hotspots]:
        if not h.file or not h.file.endswith(".py"):
            continue
        allowed, _ = path_allowed(h.file, editable, locked)
        if not allowed:
            continue
        try:
            f = _safe_source(repo, h.file)
        except ValueError:
            continue
        if not f.is_file():
            continue
        for name, start, end, src in _functions(f):
            if start <= h.line <= end and (h.file, name) not in seen:
                seen.add((h.file, name))
                chunk = f"### {h.file}:{start}-{end} `{name}` (editable, {h.pct:.1f}% self time)\n```python\n{src}\n```"
                if used + len(chunk) > cfg.max_source_chars:
                    break
                chunks.append(chunk)
                used += len(chunk)
                break
    if not chunks:
        # No profile hotspots resolved to source: fall back to listing editable files by size.
        files = sorted((p for p in repo.rglob("*.py") if path_allowed(str(p.relative_to(repo)), editable, locked)[0]),
                       key=lambda p: p.stat().st_size, reverse=True)
        for p in files[:6]:
            try:
                p = _safe_source(repo, p.relative_to(repo).as_posix())
            except ValueError:
                continue
            src = p.read_text(encoding="utf-8")
            if used + len(src) > cfg.max_source_chars:
                src = src[: max(0, cfg.max_source_chars - used)] + "\n[INCOMPLETE SOURCE: truncated to context budget]"
            chunks.append(f"### {p.relative_to(repo)} (editable)\n```python\n{src}\n```")
            used += len(src)
            if used >= cfg.max_source_chars:
                break
    return "\n\n".join(chunks)


def source_exists(repo: Path, rel: str) -> bool:
    """Whether `rel` is an existing file, after the same path checks as reading it."""
    return _safe_source(repo, rel).is_file()


def read_target_file(repo: Path, rel: str, max_chars: int) -> str:
    f = _safe_source(repo, rel)
    if not f.is_file():
        raise FileNotFoundError(f"target source does not exist: {rel}")
    # Refuse oversize files before a model call instead of silently showing a prefix.
    if f.stat().st_size > max_chars * 4:
        raise ValueError(f"target source exceeds {max_chars}-character budget: {rel}; increase context.max_source_chars")
    source = f.read_text(encoding="utf-8")
    if len(source) > max_chars:
        raise ValueError(f"target source exceeds {max_chars}-character budget: {rel}; increase context.max_source_chars")
    return source
