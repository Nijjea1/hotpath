"""Editable home for optional fused or Triton kernels used by this target.

Worker patches may add a kernel here and update its ``model.py`` call site. Benchmark and
correctness code remain locked, so adding a kernel cannot weaken the evidence gate.
"""
