import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torch
from hotpath.profilelib import torch_run
from model import build_model, generate

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = build_model(seed=0, device=DEVICE)
prompt = torch.randint(0, 256, (1, 32), generator=torch.Generator().manual_seed(7)).to(DEVICE)
generate(model, prompt, 8)  # warm
torch_run(lambda: generate(model, prompt, 64), top=25)
