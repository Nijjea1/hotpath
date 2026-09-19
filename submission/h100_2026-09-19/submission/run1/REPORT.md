# Hotpath optimization report — dryft_local

**1.460x** vs baseline (tokens_per_s); 3 change(s) shipped. 10 of 51 candidates were accepted (correct and faster than their parent).

- Baseline median: `965.10425 tokens_per_s` (30 samples)
- Optimized median: `1409.12740 tokens_per_s`
- Noise floor (run-to-run CV): 1.14%
- Base commit `74d8af7f` → shipped `bf7b3bde`

## Accepted chain (in order)

1. **Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention for the (q, k, v) attention computation.** — 1.125x vs its parent (`model.py`)
1. **Remove redundant .contiguous() call in Attention.forward before reshape/permute after attention output.** — 1.196x vs its parent (`model.py`)
1. **Build the causal attention mask once, ahead of time, and reuse it rather than constructing it dynamically within every Attention.forward call (except for single-token path which is already optimized).** — 1.359x vs its parent (`model.py`)

## Where the time went

```
Total self time 0.0206s -> 0.0196s (1.06x)
 before_s   after_s    change  location
        —    0.0030       new  fmha_cutlassF_f32_aligned_64x64_rf_sm80(PyTorchMemEffAttention::AttentionKernel<float, cutlass::arch::Sm80, true, 64, 64, 64, true, true>::Params)
        —    0.0030       new  aten::_efficient_attention_forward
   0.0025    0.0025     +0.0%  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 7, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0015    0.0015     -0.2%  void at::native::(anonymous namespace)::vectorized_layer_norm_kernel<float, float, false>(int, float, float const*, float const*, float const*, float*, float*, float*)
   0.0015    0.0015     -0.2%  aten::native_layer_norm
   0.0014    0.0013     -1.6%  aten::mm
   0.0013    0.0013     +1.6%  aten::addmm
   0.0010         — <=0.0001s  aten::bmm
   0.0008    0.0008     -0.3%  aten::cat
   0.0007    0.0007     -0.5%  void at::native::vectorized_elementwise_kernel<4, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul> >(int, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul>)
   0.0007    0.0007     -0.5%  aten::add
   0.0007    0.0007     -0.3%  void at::native::(anonymous namespace)::CatArrayBatchedCopy_vectorized<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 3, 128, 1, 16, 4>(char*, at::native::(anonymous namespace)::CatArrInputTensorMetadata<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 128, 1>, at::native::(anonymous namespace)::TensorSizeStride<unsigned int, 4u>, int, unsigned int)
   0.0005         — <=0.0001s  void gemvNSP_kernel<float, float, float, float, 1, 32, 4, 1024, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005         — <=0.0001s  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 6, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005         — <=0.0001s  void at::native::triu_tril_kernel<float, int, false, 2, false>(at::cuda::detail::TensorInfo<float, int>, at::cuda::detail::TensorInfo<float const, int>, long, long, int)
... 23 more rows
```

Profile times come from `torch.profiler` and show *where* time goes. A `<=` entry is an upper bound, not a measurement: the function fell out of the retained rows, so its cost is only known to be at most that value. Changes under 5% are within the display threshold — a profile is a single observation with no noise floor, so the benchmark table above is what decided each verdict.

## All candidates

| # | Iter | Hypothesis | Status | vs parent | vs baseline | Why |
|---|------|------------|--------|-----------|-------------|-----|
| 1 | 1 | Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention for the (q, k, v) attention computation. | accepted | 1.125x | 1.096x | shipped in this bundle |
| 2 | 1 | Preallocate the KV cache to max length and write new keys/values in place in Attention.forward, instead of growing with torch.cat each decode step. | rejected_correctness | – | – | tests failed (exit 1) |
| 3 | 1 | Remove CPU-GPU sync (item()) in generate loop and keep next token on device as a tensor during greedy decoding. | rejected_speed | 1.018x | 1.024x | 1.018x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 4 | 1 | Build the causal attention mask once (or skip it for single-token decode, where not needed) instead of constructing torch.tril every Attention.forward call. | accepted | 1.061x | 1.067x | accepted, but not in the final head |
| 5 | 1 | Preallocate the KV cache to max length and write new keys/values in place in Attention.forward, instead of growing with torch.cat each decode step. _(retry of exp_aeb0317f6f)_ | rejected_speed | 0.967x | 0.943x | slower: 0.967x vs parent |
| 6 | 2 | Wrap the TinyGPT model's forward pass (or the relevant attention and block operations) in torch.compile using mode='reduce-overhead' for CUDA graphs. | rejected_correctness | – | – | tests failed (exit 1) |
| 7 | 2 | Fuse LayerNorm and following elementwise addition in Block.forward (i.e., x = x + self.attn(self.ln1(x), cache)) into a single custom kernel to reduce memory bandwidth and kernel launches. | rejected_correctness | – | – | tests failed (exit 1) |
| 8 | 2 | Enable TF32 for linear layers and matmul in Attention and Block (if the GPU supports it). | rejected_speed | 0.913x | 0.931x | slower: 0.913x vs parent |
| 9 | 2 | Capture the single-token decode step in generate() in a CUDA graph and replay it for each new token. | rejected_correctness | – | – | tests failed (exit 1) |
| 10 | 2 | Wrap the TinyGPT model's forward pass (or the relevant attention and block operations) in torch.compile using mode='reduce-overhead' for CUDA graphs. _(retry of exp_364b9d9ad5)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 11 | 2 | Fuse LayerNorm and following elementwise addition in Block.forward (i.e., x = x + self.attn(self.ln1(x), cache)) into a single custom kernel to reduce memory bandwidth and kernel launches. _(retry of exp_f0115cee9e)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 12 | 2 | Capture the single-token decode step in generate() in a CUDA graph and replay it for each new token. _(retry of exp_02d705c7b0)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 13 | 2 | Fuse the two LayerNorm operations in Block.forward (self.ln1(x) and self.ln2(x)) into a single custom CUDA kernel that processes two normalized paths at once, reducing kernel launches and improving memory locality. | rejected_correctness | – | – | tests failed (exit 1) |
| 14 | 2 | Replace the torch.cat([pk, k], ...) and torch.cat([pv, v], ...) pattern in Attention.forward with in-place preallocation and assignment if cache is provided and used during decoding, using a fixed-size buffer per sequence. | rejected_speed | 0.988x | 0.981x | slower: 0.988x vs parent |
| 15 | 2 | Fuse the two matmul/addmm ops in the block (attention + MLP) into a single custom fused CUDA kernel where possible within a Block, reducing memory read/write traffic and kernel launch latencies. | rejected_speed | 0.930x | 0.989x | slower: 0.930x vs parent |
| 16 | 2 | Fuse the two LayerNorm operations in Block.forward (self.ln1(x) and self.ln2(x)) into a single custom CUDA kernel that processes two normalized paths at once, reducing kernel launches and improving memory locality. _(retry of exp_aaed6d8f05)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 17 | 3 | Fuse LayerNorm and elementwise addition in Block.forward (x = x + self.attn(self.ln1(x), cache)) into a single custom CUDA kernel to reduce memory bandwidth and kernel launch overhead. | rejected_correctness | – | – | tests failed (exit 1) |
| 18 | 3 | Rewrite the residual addition in Block.forward (for both attention and MLP paths) using in-place add_ instead of out-of-place addition to reduce memory allocations and improve memory efficiency. | rejected_speed | 0.978x | 1.065x | slower: 0.978x vs parent |
| 19 | 3 | Fuse LayerNorm and elementwise addition in Block.forward (x = x + self.attn(self.ln1(x), cache)) into a single custom CUDA kernel to reduce memory bandwidth and kernel launch overhead. _(retry of exp_f336f85d0d)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 20 | 3 | Wrap the entire forward pass of TinyGPT or at least the Block class with torch.compile (mode='reduce-overhead') to enable CUDA graph capture and cut per-token kernel-launch overhead. | rejected_correctness | – | – | tests failed (exit 1) |
| 21 | 3 | Fuse residual addition and following LayerNorm in Block.forward (i.e., x = x + self.attn(self.ln1(x), cache)) into a single custom kernel for combined normalization and addition. | rejected_correctness | – | – | tests failed (exit 1) |
| 22 | 3 | Wrap the entire forward pass of TinyGPT or at least the Block class with torch.compile (mode='reduce-overhead') to enable CUDA graph capture and cut per-token kernel-launch overhead. _(retry of exp_d2428c55f5)_ | rejected_speed | 1.022x | 1.053x | 1.022x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 23 | 3 | Fuse residual addition and following LayerNorm in Block.forward (i.e., x = x + self.attn(self.ln1(x), cache)) into a single custom kernel for combined normalization and addition. _(retry of exp_76e4c07892)_ | rejected_speed | 0.918x | 0.967x | slower: 0.918x vs parent |
| 24 | 4 | Remove redundant .contiguous() call in Attention.forward before reshape/permute after attention output. | accepted | 1.196x | 1.097x | shipped in this bundle |
| 25 | 4 | Special-case the single-token decode in Attention: skip causal mask and optimize QKV concat. | rejected_correctness | – | – | tests failed (exit 1) |
| 26 | 4 | Use autocast (mixed precision) for attention and MLP layers when on CUDA for further acceleration, respecting correctness tolerance. | rejected_correctness | – | – | tests failed (exit 1) |
| 27 | 4 | Batch the residual + MLP add operation in Block.forward as in-place (x += self.mlp(self.ln2(x))) if shapes match and x is not reused later. | not_selected | 1.137x | 1.057x | correct and 1.137x faster, but not in the top-2 beam; may be re-proposed on a su |
| 28 | 4 | Special-case the single-token decode in Attention: skip causal mask and optimize QKV concat. _(retry of exp_f6cda43875)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 29 | 4 | Use autocast (mixed precision) for attention and MLP layers when on CUDA for further acceleration, respecting correctness tolerance. _(retry of exp_9764890939)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 30 | 4 | Fuse the two out-of-place additions in Block.forward (x = x + self.attn(self.ln1(x), cache) and x += self.mlp(self.ln2(x))) into in-place add_ operations when possible. | rejected_speed | 0.926x | 0.990x | slower: 0.926x vs parent |
| 31 | 4 | Optimize the usage of torch.cat in Attention.forward when updating the cache by avoiding concatenation for the first step (when pk and pv are None) and directly assigning k and v to the cache instead. | rejected_speed | 0.985x | 1.047x | slower: 0.985x vs parent |
| 32 | 4 | Fuse the split and view/transpose operations for QKV computation in Attention.forward into a single operation (e.g., using einops or manual tensor ops) to avoid intermediate allocations. | rejected_speed | 1.016x | 1.076x | 1.016x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 33 | 4 | Special-case single-token decode in Attention.forward to skip constructing or indexing the attention mask; operate only over the most recent token for the attention step when T=1. | accepted | 1.279x | 1.249x | accepted, but not in the final head |
| 34 | 5 | Use torch.nn.functional.scaled_dot_product_attention (with is_causal=True) in Attention.forward for flash/memory-efficient attention when T > 1. | rejected_speed | 0.996x | 1.231x | slower: 0.996x vs parent |
| 35 | 5 | Remove the .item() sync point in generate: keep next token on device and use in-place token build-up throughout greedy decode. | accepted | 1.082x | 1.322x | accepted, but not in the final head |
| 36 | 5 | Preallocate the key and value caches for each Attention module to maximum sequence length, and update them in-place during decoding rather than growing with torch.cat each step. | not_selected | 1.059x | 1.047x | correct and 1.059x faster, but not in the top-2 beam; may be re-proposed on a su |
| 37 | 5 | Build the causal attention mask once, ahead of time, and reuse it rather than constructing it dynamically within every Attention.forward call (except for single-token path which is already optimized). | accepted | 1.359x | 1.460x | shipped in this bundle |
| 38 | 5 | Use torch.nn.functional.scaled_dot_product_attention with is_causal=True (for platforms supporting flash/mem-efficient attention) for multi-token paths in Attention.forward. | rejected_correctness | – | – | tests failed (exit 1) |
| 39 | 5 | Batch the residual + MLP add operation in Block.forward to in-place (x += self.mlp(self.ln2(x))) if input/output shapes match and x is not reused later, to save memory bandwidth and reduce allocations. | not_selected | 1.083x | 1.085x | correct and 1.083x faster, but not in the top-2 beam; may be re-proposed on a su |
| 40 | 5 | Use torch.nn.functional.scaled_dot_product_attention with is_causal=True (for platforms supporting flash/mem-efficient attention) for multi-token paths in Attention.forward. _(retry of exp_dc8520e45e)_ | not_selected | 1.057x | 1.050x | correct and 1.057x faster, but not in the top-2 beam; may be re-proposed on a su |
| 41 | 6 | Preallocate key and value caches in Attention to maximum sequence length and write new keys/values in-place during decoding, rather than growing with torch.cat each step. | patch_failed | – | – | edit to model.py is a no-op (search equals replace): no change |
| 42 | 6 | Change the two out-of-place residual add operations in Block.forward (x = x + self.attn(...); x = x + self.mlp(...)) to use in-place x += ... to save memory allocations and bandwidth if tensor aliasing allows. | rejected_speed | 1.013x | 1.462x | 1.013x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 43 | 6 | Enable TF32 matrix multiplication (matmuls and attention) on supported GPUs by setting torch.backends.cuda.matmul.allow_tf32 = True at the start of model code execution. | rejected_speed | 1.004x | 1.441x | 1.004x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 44 | 6 | For single-token decode, refactor Attention.forward to also skip slicing the causal mask entirely, and ensure no unnecessary slicing or device moves are performed when T==1 and cache is used. | rejected_speed | 1.001x | 1.374x | 1.001x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 45 | 6 | Preallocate key and value caches in Attention to maximum sequence length and write new keys/values in-place during decoding, rather than growing with torch.cat each step. _(retry of exp_cde974a819)_ | rejected_speed | 1.022x | 1.407x | 1.022x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%) |
| 46 | 6 | Preallocate KV cache to maximum sequence length in Attention and update in-place during decoding. | rejected_correctness | – | – | tests failed (exit 1) |
| 47 | 6 | Apply in-place addition for the residual connection after the MLP in Block.forward (i.e., x += self.mlp(self.ln2(x))) when it is safe. | rejected_speed | 0.993x | 1.248x | slower: 0.993x vs parent |
| 48 | 6 | Fuse Q/K/V split and view/transpose into a single operation in Attention.forward to avoid intermediate allocations. | rejected_correctness | – | – | tests failed (exit 1) |
| 49 | 6 | Use torch.nn.functional.scaled_dot_product_attention in Attention.forward for multi-token cases (T > 1) when running on a compatible CUDA version. | rejected_speed | 0.983x | 1.306x | slower: 0.983x vs parent |
| 50 | 6 | Preallocate KV cache to maximum sequence length in Attention and update in-place during decoding. _(retry of exp_6dded43b00)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 51 | 6 | Fuse Q/K/V split and view/transpose into a single operation in Attention.forward to avoid intermediate allocations. _(retry of exp_1bd37d5c1a)_ | rejected_correctness | – | – | tests failed (exit 1) |

## Ablation (leave-one-out re-measurement)

```
Ablation for run_73124208c0: full stack median 1382.56257 tokens_per_s

 contribution        full     without  idea
could_not_apply           -           -  Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention for the (q, k, v) attention computation.  (later edits depend on this one: search text not found in model.py: '        y = y.transpose(1, 2).contiguous().view(B, T, C)\n        return self.pro')
could_not_apply           -           -  Remove redundant .contiguous() call in Attention.forward before reshape/permute after attention output.  (later edits depend on this one: search text not found in model.py: 'class Attention(nn.Module):\n    def __init__(self):\n        super().__init__()\n ')
       1.376x  1382.56257  1004.58628  Build the causal attention mask once, ahead of time, and reuse it rather than constructing it dynamically within every Attention.forward call (except for single-token path which is already optimized).  (pulls its weight)
```

## Files in this bundle

- `optimized_src/` — the full source tree with every shipped change
- `changes.patch` — unified diff from the baseline to the shipped stack
