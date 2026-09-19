"""OpenAI (and OpenAI-compatible, e.g. Baseten) provider using structured outputs.

The response is parsed straight into the Pydantic schema, so a malformed model reply
becomes an exception the caller records as patch_failed instead of a crash.
"""
from __future__ import annotations

import os

from hotpath import observability as obs
from hotpath.providers.base import PatchRequest, PlanRequest
from hotpath.providers.prompts import planner_messages, worker_messages
from hotpath.schema import PatchResponse, PlanResponse


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str, api_key_env: str = "OPENAI_API_KEY", base_url: str | None = None,
                 timeout: float = 180.0, role: str = "worker"):
        from openai import AsyncOpenAI

        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"{api_key_env} is not set; use provider 'mock' for offline runs")
        self.model = model
        self.client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=timeout)
        self.name = f"openai:{model}" if not base_url else f"compat:{model}"
        self.role = role
        # gen_ai.system names the API provider in Sentry's AI monitoring.
        self.system = "openai" if not base_url else ("baseten" if "baseten" in base_url else "openai-compatible")

    async def plan(self, req: PlanRequest) -> PlanResponse:
        with obs.ai_chat(self.model, self.role, self.system) as sp:
            resp = await self.client.beta.chat.completions.parse(
                model=self.model, messages=planner_messages(req), response_format=PlanResponse)
            obs.record_ai_usage(sp, resp)
            parsed = resp.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("planner returned no parseable plan")
        return parsed

    async def generate_patch(self, req: PatchRequest) -> PatchResponse:
        with obs.ai_chat(self.model, self.role, self.system) as sp:
            resp = await self.client.beta.chat.completions.parse(
                model=self.model, messages=worker_messages(req), response_format=PatchResponse)
            obs.record_ai_usage(sp, resp)
            parsed = resp.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("worker returned no parseable patch")
        return parsed

