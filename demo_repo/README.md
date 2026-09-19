# demo_repo

A deliberately slow record-processing library used as Hotpath's general-repo demo target.

- `slowlib.py`: editable. Contains the hot code.
- `tests/`: locked. `check.py` compares every function against an independent reference implementation.
- `bench.py` and `hotprofile.py`: locked. Emit Hotpath-compatible JSON.
- `mock_patches/`: recorded candidate patches replayed by the offline mock provider. Each one still
  runs through the real harness; several are intentionally wrong, slow, or blocked so the full
  accept/reject range is exercised without any API keys.
