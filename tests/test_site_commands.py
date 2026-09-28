"""The site's CLI reference is generated from the parser; this keeps the checked-in copy honest."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_site_commands", ROOT / "scripts" / "gen_site_commands.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_site_command_table_matches_the_cli():
    gen = _gen()
    current = gen.OUT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert current == gen.render(), "site/src/data/commands.ts is stale: run `python scripts/gen_site_commands.py`"


def test_every_subcommand_is_documented():
    names = [c["name"] for c in _gen().commands()]
    assert set(names) >= {"doctor", "init", "go", "check", "assess", "run", "pr", "serve", "ablate", "export"}
    assert all(c["summary"] for c in _gen().commands()), "every subcommand needs a help= summary"


def test_every_command_has_a_usage_guide_on_the_site():
    """The flag table is generated; the "when to use it" examples are hand-written, so require one per command."""
    import re
    guide = (ROOT / "site" / "src" / "data" / "guide.ts").read_text(encoding="utf-8")
    block = guide[guide.index("export const COMMAND_GUIDE"):]
    block = block[:block.index("\n};")]
    documented = set(re.findall(r"^  (\w+): \{", block, flags=re.M))
    missing = {c["name"] for c in _gen().commands()} - documented
    assert not missing, f"add these commands to COMMAND_GUIDE in site/src/data/guide.ts: {sorted(missing)}"
