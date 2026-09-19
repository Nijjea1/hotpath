"""FROZEN reference. Identical to the original model.py; never edited by the agent."""
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

    def forward(self, x):
        B, T, C = x.shape
        q, k, v = self.qkv(x).split(D_MODEL, dim=2)
        q = q.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        k = k.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        v = v.view(B, T, N_HEADS, C // N_HEADS).transpose(1, 2)
        att = (q @ k.transpose(-2, -1)) / math.sqrt(k.shape[-1])
        mask = torch.tril(torch.ones(T, T, device=x.device))
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

    def forward(self, x):
        x = x + self.attn(self.ln1(x))
        return x + self.mlp(self.ln2(x))


class RefGPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.tok = nn.Embedding(VOCAB, D_MODEL)
        self.pos = nn.Embedding(MAX_SEQ, D_MODEL)
        self.blocks = nn.ModuleList([Block() for _ in range(N_LAYERS)])
        self.ln = nn.LayerNorm(D_MODEL)
        self.head = nn.Linear(D_MODEL, VOCAB, bias=False)

    def forward(self, idx):
        B, T = idx.shape
        x = self.tok(idx) + self.pos(torch.arange(T, device=idx.device))
        for blk in self.blocks:
            x = blk(x)
        return self.head(self.ln(x))


def build_reference(seed: int = 0, device: str = "cpu") -> RefGPT:
    torch.manual_seed(seed)
    return RefGPT().to(device).eval()


@torch.no_grad()
def ref_generate(model: RefGPT, prompt: torch.Tensor, n_new: int) -> torch.Tensor:
    """Cache-free greedy decode: recomputes the whole sequence each step. Slow, obviously right."""
    out = prompt
    for _ in range(n_new):
        nxt = model(out)[:, -1, :].argmax(dim=-1, keepdim=True)
        out = torch.cat([out, nxt], dim=1)
    return out
