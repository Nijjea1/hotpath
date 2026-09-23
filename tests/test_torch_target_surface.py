"""The GPU target's kernel module is a real, protected edit surface."""
import json
import shutil
from pathlib import Path

import pytest

from hotpath.config import load_config
from hotpath.providers.prompts import PLANNER_SYSTEM, WORKER_SYSTEM
from hotpath.schema import Edit
from hotpath.workspace import LockedFileError, Workspace


ROOT = Path(__file__).resolve().parents[1]


def test_mock_patch_edits_called_kernel_and_cannot_edit_benchmark(tmp_path):
    source = ROOT / "targets" / "torch_transformer"
    target = tmp_path / "transformer with spaces"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", ".hotpath", "__pycache__"))
    ws = Workspace(target, Path(".hotpath"))
    base = ws.ensure_repo()
    cfg = load_config(ROOT / "configs" / "torch_transformer.yaml")
    patch = json.loads((source / "mock_patches" / "01_no_item_sync.json").read_text())
    worktree = ws.create_worktree(base, "kernel_patch")
    try:
        edits = [Edit.model_validate(edit) for edit in patch["edits"]]
        files, _ = ws.apply_edits(worktree, edits, cfg.editable, cfg.locked)
        assert files == ["kernels/token_select.py"]
        assert "select_next(logits)" in (worktree / "model.py").read_text()
        assert "keepdim=True" in (worktree / "kernels" / "token_select.py").read_text()
        with pytest.raises(LockedFileError):
            ws.apply_edits(worktree, [Edit(file="bench.py", search="missing", replace="x")],
                           cfg.editable, cfg.locked)
    finally:
        ws.cleanup()


def test_gpu_configs_and_prompts_allow_safe_kernel_authoring():
    for name in ("torch_transformer.yaml", "torch_transformer_h100_openai.yaml",
                 "dryft_local.yaml", "dryft_h100.yaml"):
        cfg = load_config(ROOT / "configs" / name)
        assert any("kernel" in pattern for pattern in cfg.editable), name
        assert not any("kernel" in pattern for pattern in cfg.locked), name
    assert "new Triton kernel module plus its call site" in PLANNER_SYSTEM
    assert "correct PyTorch fallback" in WORKER_SYSTEM
    assert "ROCm" in WORKER_SYSTEM and "MPS" in WORKER_SYSTEM
