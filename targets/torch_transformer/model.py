"""Tiny GPT-style decoder with a naive generation loop (the optimization target)."""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

VOCAB, D_MODEL, N_LAYERS, N_HEADS, MAX_SEQ = 256, 256, 4, 4, 1024


class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.qkv = nn.Linear(D_MODEL, 3 * D_MODEL, bias=False)
        self.proj = nn.Linear(D_MODEL, D_MODEL, bias=False)

    def forward(self, x, cache=None):
        B, T, C = x.shape
        q, k, v = self.qkv(x).split(D_MODEL, dim=2)
        q = q.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        k = k.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        v = v.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        if cache is not None:
            pk, pv = cache
            if pk is not None:
                k = torch.cat([pk, k], dim=2)   # grows the cache every step: O(T^2) copying
                v = torch.cat([pv, v], dim=2)
            cache[0], cache[1] = k, v
        Tk = k.shape[2]
        att = (q @ k.transpose(-2, -1)) / math.sqrt(k.shape[-1])
        mask = torch.tril(torch.ones(Tk, Tk, device=x.device))[Tk - T:, :]
        att = att.masked_fill(mask == 0, float("-inf"))
        att = F.softmax(att, dim=-1)
        y = (att @ v).transpose(1, 2).contiguous().view(B, T, C)
        return self.proj(y)


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.ln1 = nn.LayerNorm(D_MODEL)
        self.attn = Attention()
        self.ln2 = nn.LayerNorm(D_MODEL)
        self.mlp = nn.Sequential(nn.Linear(D_MODEL, 4 * D_MODEL), nn.GELU(), nn.Linear(4 * D_MODEL, D_MODEL))

    def forward(self, x, cache=None):
        x = x + self.attn(self.ln1(x), cache)
        return x + self.mlp(self.ln2(x))


class TinyGPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.tok = nn.Embedding(VOCAB, D_MODEL)
        self.pos = nn.Embedding(MAX_SEQ, D_MODEL)
        self.blocks = nn.ModuleList([Block() for _ in range(N_LAYERS)])
        self.ln = nn.LayerNorm(D_MODEL)
        self.head = nn.Linear(D_MODEL, VOCAB, bias=False)

    def forward(self, idx, caches=None, start_pos=0):
        B, T = idx.shape
        pos = torch.arange(start_pos, start_pos + T, device=idx.device)
        x = self.tok(idx) + self.pos(pos)
        for i, blk in enumerate(self.blocks):
            x = blk(x, caches[i] if caches is not None else None)
        return self.head(self.ln(x))


def build_model(seed: int = 0, device: str = "cpu") -> TinyGPT:
    torch.manual_seed(seed)
    return TinyGPT().to(device).eval()


@torch.no_grad()
def generate(model: TinyGPT, prompt: torch.Tensor, n_new: int) -> torch.Tensor:
    """Greedy decode. Returns the full sequence (prompt + generated)."""
    caches = [[None, None] for _ in model.blocks]
    logits = model(prompt, caches, start_pos=0)
    out = prompt
    for _ in range(n_new):
        nxt = int(logits[:, -1, :].argmax(dim=-1).item())     # CPU sync every token
        nxt_t = torch.tensor([[nxt]], device=prompt.device)
        out = torch.cat([out, nxt_t], dim=1)
        logits = model(nxt_t, caches, start_pos=out.shape[1] - 1)
    return out


@torch.no_grad()
def logits_for(model: TinyGPT, seq: torch.Tensor) -> torch.Tensor:
    """Full-sequence logits without a cache (used by the correctness test)."""
    return model(seq)
