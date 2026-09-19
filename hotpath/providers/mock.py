"""Offline provider that replays candidate patches from JSON files.

It stands in for the *model only*. Every replayed patch still goes through the real
harness: real worktree, real tests, real benchmark, real accept/reject decision.
That is what makes the offline demo honest: the numbers on the dashboard are measured.

A patch file looks like:
{
  "idea": "...", "strategy": "...", "target_file": "pkg/mod.py", "rationale": "...", "risk": "low",
  "edits": [{"file": "pkg/mod.py", "search": "...exact text...", "replace": "..."}],
  "reasoning": "..."
}
"""
from __future__ import annotations

import json
from pathlib import Path

from hotpath.providers.base import PatchRequest, PlanRequest
from hotpath.schema import Edit, Hypothesis, PatchResponse, PlanResponse


class MockProvider:
    name = "mock"

    def __init__(self, patches_dir: str | None):
        self.patches: list[dict] = []
        if patches_dir:
            for p in sorted(Path(patches_dir).glob("*.json")):
                self.patches.append(json.loads(p.read_text()))

    async def plan(self, req: PlanRequest) -> PlanResponse:
        tried = {e.hypothesis.idea for e in req.history}
        remaining = [p for p in self.patches if p["idea"] not in tried]
        hyps = [Hypothesis(idea=p["idea"], strategy=p["strategy"], target_file=p["target_file"],
                           rationale=p["rationale"], risk=p.get("risk", "low"), extra_files=p.get("extra_files", []))
                for p in remaining[: req.max_hypotheses]]
        return PlanResponse(hypotheses=hyps, notes="mock planner: replaying recorded hypotheses in order")

    async def generate_patch(self, req: PatchRequest) -> PatchResponse:
        for p in self.patches:
            if p["idea"] == req.hypothesis.idea:
                if p.get("raise"):
                    raise RuntimeError(p["raise"])
                # On a retry (previous_failure set) replay the recorded corrected attempt if there is
                # one, so an offline run can exercise the retry-with-feedback loop honestly: the first
                # attempt really fails the harness, the retry really passes it.
                if req.previous_failure and p.get("retry"):
                    r = p["retry"]
                    return PatchResponse(edits=[Edit(**e) for e in r.get("edits", [])], reasoning=r.get("reasoning", ""))
                return PatchResponse(edits=[Edit(**e) for e in p.get("edits", [])], reasoning=p.get("reasoning", ""))
        return PatchResponse(edits=[], reasoning="mock worker has no patch for this hypothesis")
