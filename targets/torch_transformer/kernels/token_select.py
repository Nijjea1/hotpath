"""Editable token-selection operation used by the development transformer.

This deliberately round-trips through the host so the agent has a small,
correctness-preserving GPU optimization to try across batch sizes.
"""
import torch


def select_next(logits: torch.Tensor) -> torch.Tensor:
    tokens = logits[:, -1, :].argmax(dim=-1).tolist()
    return torch.tensor(tokens, device=logits.device).unsqueeze(1)
