"""Scripted provider for deterministic orchestration tests; never calls an API."""
from collections import deque
from copy import deepcopy


class FakeProvider:
    name = "scripted-fake"

    def __init__(self, plans=(), patches=()):
        self.plans, self.patches = deque(plans), deque(patches)
        self.plan_requests, self.patch_requests = [], []

    @staticmethod
    def take(queue):
        if not queue:
            raise AssertionError("unexpected provider request: script exhausted")
        response = queue.popleft()
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def plan(self, request):
        self.plan_requests.append(deepcopy(request))
        return self.take(self.plans)

    async def generate_patch(self, request):
        self.patch_requests.append(deepcopy(request))
        return self.take(self.patches)
