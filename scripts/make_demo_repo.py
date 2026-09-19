"""Build the standalone demo repository that Hotpath opens real pull requests against.

    python scripts/make_demo_repo.py ../hotpath-demo-analytics

Copies `demo_repo/` into a fresh git repository on `main`, runs `hotpath init` on it (OpenAI plans,
Baseten explores; recorded patches for offline `--provider mock` runs), and commits the result.
Nothing is pushed: create the GitHub repository and push it yourself, e.g.

    gh repo create <owner>/hotpath-demo-analytics --public --source <dest> --push
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hotpath.init import gather_answers, init_repo  # noqa: E402

README = """# hotpath-demo-analytics

A deliberately slow record-processing library, used to show [Hotpath](https://github.com/Nijjea1/hotpath)
opening pull requests whose every change is proven correct and measurably faster.

- `slowlib.py`: the code Hotpath may edit.
- `tests/`, `bench.py`, `hotprofile.py`, `data.py`: locked. They define "correct" and "fast", so no
  model can edit them, and the `hotpath-verify` workflow fails any Hotpath PR that touches them.
- `mock_patches/`: recorded model output for an offline run (`hotpath run --provider mock`). Each
  patch still goes through the real tests and benchmark; several are wrong on purpose.

## Try it

```sh
pip install git+https://github.com/Nijjea1/hotpath
hotpath run --pr                    # real models: needs OPENAI_API_KEY (+ BASETEN_API_KEY)
hotpath run --pr --provider mock    # offline replay, no keys
```

Hotpath pushes `hotpath/<run id>` with one commit per verified change and opens a PR whose
description carries the benchmark table and every rejected attempt with its reason.
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dest")
    p.add_argument("--force", action="store_true", help="replace dest if it exists")
    args = p.parse_args()
    dest = Path(args.dest).resolve()
    if dest.exists():
        if not args.force:
            print(f"{dest} exists; pass --force to replace it")
            return 1
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "demo_repo", dest, ignore=shutil.ignore_patterns(".hotpath", "__pycache__", ".git"))
    (dest / "README.md").write_text(README, encoding="utf-8", newline="\n")
    git(dest, "init", "-q", "-b", "main")
    answers, notes = gather_answers(
        dest, test_cmd="python tests/check.py", bench_cmd="python bench.py", profile_cmd="python hotprofile.py",
        editable=["*.py"], locked=["data.py", "mock_patches/*"], execution="local", planner="openai",
        worker="baseten", mock_patches_dir="mock_patches", name="hotpath-demo-analytics")
    result = init_repo(dest, answers)
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "Slow analytics library with a locked correctness check and benchmark")
    for f in result.written:
        print(f"wrote {f.relative_to(dest).as_posix()}")
    for n in notes:
        print(f"note: {n}")
    print(f"\ndemo repository ready at {dest} (branch main, 1 commit, not pushed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
