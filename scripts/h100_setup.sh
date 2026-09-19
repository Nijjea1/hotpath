#!/usr/bin/env bash
# One-shot Hotpath setup for a Baseten H100 workstation (Linux).
# Run this IN the H100 terminal, from inside the cloned hotpath repo:
#
#   git clone https://github.com/Nijjea1/hotpath.git && cd hotpath
#   bash scripts/h100_setup.sh
#
# It stops and tells you what to do if a prerequisite is missing (GPU, keys).
set -euo pipefail

say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }

say "1/6  GPU check"
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi; then
  echo "!! No GPU visible. Are you on the H100 workstation? (nvidia-smi failed)"; exit 1
fi

say "2/6  Python env + install"
python -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -e ".[dev]"

say "3/6  Torch + CUDA"
if ! python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  echo "torch/CUDA not ready — installing the CUDA 12.8 build..."
  pip install torch --index-url https://download.pytorch.org/whl/cu128
fi
python -c "import torch; assert torch.cuda.is_available(); print('CUDA OK:', torch.cuda.get_device_name(0))"

say "4/6  API keys"
if [ ! -f .env ]; then
  echo "No .env found. Create it with your keys, then re-run this script:"
  echo ""
  echo "  cat > .env <<'EOF'"
  echo "  OPENAI_API_KEY=sk-your-openai-key"
  echo "  BASETEN_API_KEY=your-baseten-key"
  echo "  EOF"
  exit 1
fi
set -a; . ./.env; set +a
: "${OPENAI_API_KEY:?OPENAI_API_KEY missing in .env}"
: "${BASETEN_API_KEY:?BASETEN_API_KEY missing in .env}"
echo "keys loaded (OpenAI planner + Baseten worker)"

say "5/6  Baseline tokens/sec (untouched model)"
( cd targets/torch_transformer && python tests/check.py && python bench.py )

say "6/6  Optimization search on the H100"
hotpath run configs/dryft_local.yaml --autocommit

say "Export"
hotpath export configs/dryft_local.yaml submission/ --ablate || true
echo ""
echo "Done. Baseline vs optimized tokens/sec and the accepted chain are in: submission/REPORT.md"
echo "Watch live next time with:  hotpath serve configs/dryft_local.yaml   (then ssh -L 8765:localhost:8765 ...)"
