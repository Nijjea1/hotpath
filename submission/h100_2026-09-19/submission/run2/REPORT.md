# Hotpath optimization report — dryft_local

**1.379x** vs baseline (tokens_per_s); 2 change(s) shipped. 4 of 57 candidates were accepted (correct and faster than their parent).

- Baseline median: `916.00052 tokens_per_s` (30 samples)
- Optimized median: `1263.04711 tokens_per_s`
- Noise floor (run-to-run CV): 3.19%
- Base commit `74d8af7f` → shipped `95a2e2cf`

## Accepted chain (in order)

1. **Cache the causal mask or skip mask construction in single-token decoding path in Attention.forward.** — 1.197x vs its parent (`model.py`)
1. **Eliminate CPU-GPU synchronization point in generation loop by avoiding .item() call for next token selection; keep next token index as a 1x1 tensor and concatenate directly on device.** — 1.094x vs its parent (`model.py`)

## Where the time went

```
Total self time 0.0206s -> 0.0168s (1.23x)
 before_s   after_s    change  location
   0.0025    0.0025     +0.3%  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 7, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0015    0.0015     -0.2%  void at::native::(anonymous namespace)::vectorized_layer_norm_kernel<float, float, false>(int, float, float const*, float const*, float const*, float*, float*, float*)
   0.0015    0.0015     -0.2%  aten::native_layer_norm
   0.0014    0.0013     -0.4%  aten::mm
   0.0013    0.0013     +1.0%  aten::addmm
   0.0010    0.0010     +0.7%  aten::bmm
   0.0008    0.0008     -0.5%  aten::cat
   0.0007    0.0007     -0.5%  void at::native::vectorized_elementwise_kernel<4, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul> >(int, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul>)
   0.0007    0.0007     -0.5%  aten::add
   0.0007    0.0007     -0.4%  void at::native::(anonymous namespace)::CatArrayBatchedCopy_vectorized<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 3, 128, 1, 16, 4>(char*, at::native::(anonymous namespace)::CatArrInputTensorMetadata<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 128, 1>, at::native::(anonymous namespace)::TensorSizeStride<unsigned int, 4u>, int, unsigned int)
   0.0005    0.0005     +0.9%  void gemvNSP_kernel<float, float, float, float, 1, 32, 4, 1024, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005    0.0005     +0.5%  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 6, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005         — <=0.0001s  void at::native::triu_tril_kernel<float, int, false, 2, false>(at::cuda::detail::TensorInfo<float, int>, at::cuda::detail::TensorInfo<float const, int>, long, long, int)
   0.0005         — <=0.0001s  aten::tril
   0.0004    0.0004     +0.2%  aten::_softmax
... 18 more rows
```

Profile times come from `torch.profiler` and show *where* time goes. A `<=` entry is an upper bound, not a measurement: the function fell out of the retained rows, so its cost is only known to be at most that value. Changes under 5% are within the display threshold — a profile is a single observation with no noise floor, so the benchmark table above is what decided each verdict.

## All candidates

| # | Iter | Hypothesis | Status | vs parent | vs baseline | Why |
|---|------|------------|--------|-----------|-------------|-----|
| 1 | 1 | Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention (SDPA). | rejected_correctness | – | – | tests failed (exit 1) |
| 2 | 1 | Preallocate the KV cache tensor in Attention.forward to max sequence length and write new keys/values in place instead of concatenating with torch.cat each step. | rejected_speed | 0.983x | 0.962x | slower: 0.983x vs parent |
| 3 | 1 | Remove .item() call to avoid CPU-GPU sync in the generate loop; keep the next token on device. | rejected_correctness | – | – | tests failed (exit 1) |
| 4 | 1 | Cache the causal mask or skip mask construction in single-token decoding path in Attention.forward. | accepted | 1.197x | 1.226x | shipped in this bundle |
| 5 | 1 | Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention (SDPA). _(retry of exp_e28ae6b79a)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 6 | 1 | Remove .item() call to avoid CPU-GPU sync in the generate loop; keep the next token on device. _(retry of exp_b900006822)_ | accepted | 1.175x | 1.089x | accepted, but not in the final head |
| 7 | 2 | Compile the TinyGPT forward method with torch.compile using mode='reduce-overhead' to reduce Python/kernel launch overhead. | rejected_correctness | – | – | tests failed (exit 1) |
| 8 | 2 | Fuse LayerNorm and elementwise addition into a single kernel in Block.forward by writing a custom kernel or function for x + LayerNorm(x). | rejected_correctness | – | – | tests failed (exit 1) |
| 9 | 2 | Fuse the two matmul operations in Attention.forward (q@k^T and att@v) using a custom CUDA/Triton kernel specialized for single-token decode (T=1), optimizing the use case where masking is no-op and shapes are fixed. | rejected_speed | 0.938x | 1.155x | slower: 0.938x vs parent |
| 10 | 2 | Enable TF32 precision for matmul operations in Attention and MLP Linear layers if device supports it by setting torch.backends.cuda.matmul.allow_tf32 = True at model init. | rejected_speed | 0.974x | 1.271x | slower: 0.974x vs parent |
| 11 | 2 | Compile the TinyGPT forward method with torch.compile using mode='reduce-overhead' to reduce Python/kernel launch overhead. _(retry of exp_5b3614a33a)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 12 | 2 | Fuse LayerNorm and elementwise addition into a single kernel in Block.forward by writing a custom kernel or function for x + LayerNorm(x). _(retry of exp_a1a1f5acae)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 13 | 2 | Fuse the two sequential matmul operations (q @ k^T and att @ v) in Attention.forward with causal masking into a single custom CUDA/Triton kernel for the multi-token case (T > 1), while supporting standard masking. | rejected_correctness | – | – | tests failed (exit 1) |
| 14 | 2 | Capture the single-token decode step (where T=1 and mask is a no-op) in a CUDA graph and replay it during the generate loop to cut kernel launch and Python overhead. | rejected_speed | 1.009x | 1.118x | 1.009x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 15 | 2 | Fuse the two sequential matmul operations (q @ k^T and att @ v) in Attention.forward with causal masking into a single custom CUDA/Triton kernel for the multi-token case (T > 1), while supporting standard masking. _(retry of exp_8862ee9e1b)_ | rejected_speed | 1.034x | 1.032x | 1.034x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 16 | 3 | Fuse the matmul and softmax operations in Attention.forward using torch.nn.functional.scaled_dot_product_attention (SDPA) for the multi-token path (T > 1). | rejected_speed | 0.947x | 1.238x | slower: 0.947x vs parent |
| 17 | 3 | Fuse the two sequential add+LayerNorm patterns in Block.forward (x + self.attn(self.ln1(x), cache) and x + self.mlp(self.ln2(x))) by replacing them with a custom helper function that applies LayerNorm then addition in a single call. | rejected_speed | 1.031x | 1.288x | 1.031x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 18 | 3 | Faster cache update in Attention.forward by removing torch.cat for the value (v) cache: update in-place using a preallocated tensor but only for the second cache (pv), as only values are read in the final att@v and may be less correctness-sensitive. | rejected_speed | 1.009x | 1.241x | 1.009x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 19 | 3 | Use torch.baddbmm for the att = (q @ k^T) + bias computation in Attention to fuse the batch matmul and addition under the hood (for multi-token path, i.e., T > 1). | rejected_correctness | – | – | tests failed (exit 1) |
| 20 | 3 | Use torch.baddbmm for the att = (q @ k^T) + bias computation in Attention to fuse the batch matmul and addition under the hood (for multi-token path, i.e., T > 1). _(retry of exp_06ffb8f602)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 21 | 3 | Avoid repeatedly recomputing the shape and device mask in Attention.forward multi-token path by caching the causal mask per device and sequence length (Tk) at the module level, rather than rebuilding it every time. | accepted | 1.115x | 1.163x | accepted, but not in the final head |
| 22 | 3 | Fuse LayerNorm and elementwise addition into a single operation in Block.forward for the x + self.mlp(self.ln2(x)) path by introducing a helper that computes y = LayerNorm(x) + x in one go, leveraging in-place CUDA kernels if possible. | rejected_correctness | – | – | tests failed (exit 1) |
| 23 | 3 | Preallocate the cache for keys and values in Attention at model/call initialization to the max sequence length and perform in-place updates, but only for the multi-token decode path. Carefully validate the write boundaries via positional tracking. | rejected_speed | 0.946x | 1.061x | slower: 0.946x vs parent |
| 24 | 3 | Enable TF32 for all Linear and matmul ops at model init if running on CUDA and correctness tolerance is acceptable (controlled via a model.py constant flag), to accelerate attention and MLP layers in multi-token decode. | rejected_speed | 0.922x | 1.012x | slower: 0.922x vs parent |
| 25 | 3 | Fuse LayerNorm and elementwise addition into a single operation in Block.forward for the x + self.mlp(self.ln2(x)) path by introducing a helper that computes y = LayerNorm(x) + x in one go, leveraging in-place CUDA kernels if possible. _(retry of exp_d0121a65d9)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 26 | 4 | Preallocate a single contiguous buffer per layer for the KV (key/value) cache in Attention and index into it during decode, updating slices in-place instead of storing lists of tensors or using torch.cat. Track the current decode position to write at the correct offset each step. | rejected_speed | 1.011x | 1.175x | 1.011x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 27 | 4 | Fuse LayerNorm and elementwise addition in Block.forward for x + self.attn(self.ln1(x), cache) into a single operation using a helper function that performs LayerNorm followed by addition in one fused call, leveraging efficient torch operations. | rejected_correctness | – | – | tests failed (exit 1) |
| 28 | 4 | Fuse LayerNorm and elementwise addition for x + self.mlp(self.ln2(x)) in Block.forward using the same fused function as above for the second residual path. | rejected_speed | 0.988x | 1.277x | slower: 0.988x vs parent |
| 29 | 4 | Replace manual attention softmax with torch.nn.functional.scaled_dot_product_attention (SDPA) only in the multi-token path (T > 1); fallback to the existing math for T==1. Carefully maintain mask compatibility and mask dtype to preserve correctness. | rejected_correctness | – | – | tests failed (exit 1) |
| 30 | 4 | Fuse LayerNorm and elementwise addition in Block.forward for x + self.attn(self.ln1(x), cache) into a single operation using a helper function that performs LayerNorm followed by addition in one fused call, leveraging efficient torch operations. _(retry of exp_66de7073dc)_ | rejected_speed | 0.979x | 1.222x | slower: 0.979x vs parent |
| 31 | 4 | Replace manual attention softmax with torch.nn.functional.scaled_dot_product_attention (SDPA) only in the multi-token path (T > 1); fallback to the existing math for T==1. Carefully maintain mask compatibility and mask dtype to preserve correctness. _(retry of exp_1b82007db3)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 32 | 4 | Fuse layer normalization and the subsequent residual addition in Block.forward for both residual paths into a single, more efficient operation using torch.add (with out parameter for potential in-place efficiency). | rejected_speed | 0.925x | 1.071x | slower: 0.925x vs parent |
| 33 | 4 | Leverage batched matrix multiplication (torch.bmm) for the QK^T and AV matmuls in multihead attention inside Attention.forward to avoid unnecessary reshapes/copies. | rejected_speed | 1.011x | 1.143x | 1.011x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 34 | 4 | Cache the softmax denominator (sum) in Attention.forward when decoding a single token (T=1), as its value is always 1 (since attention is only over the current position). | rejected_correctness | – | – | tests failed (exit 1) |
| 35 | 4 | Skip .contiguous() in the output reshape of Attention.forward if the layout is already correct, or only call it conditionally when contiguousness is not guaranteed by the prior ops. | rejected_correctness | – | – | tests failed (exit 1) |
| 36 | 4 | Cache the softmax denominator (sum) in Attention.forward when decoding a single token (T=1), as its value is always 1 (since attention is only over the current position). _(retry of exp_09490f6860)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 37 | 4 | Skip .contiguous() in the output reshape of Attention.forward if the layout is already correct, or only call it conditionally when contiguousness is not guaranteed by the prior ops. _(retry of exp_4fffbbb419)_ | rejected_speed | 0.988x | 1.126x | slower: 0.988x vs parent |
| 38 | 5 | Eliminate CPU-GPU synchronization point in generation loop by avoiding .item() call for next token selection; keep next token index as a 1x1 tensor and concatenate directly on device. | accepted | 1.094x | 1.379x | shipped in this bundle |
| 39 | 5 | Fuse addmm for linear projections in Attention (qkv and proj) using manual torch.addmm or in-place Linear with out parameter to reduce kernel launches. | rejected_speed | 0.971x | 1.203x | slower: 0.971x vs parent |
| 40 | 5 | Fused residual path for Block + MLP: apply in-place addition (residual) where safe to avoid extra allocations/copies. | rejected_speed | 0.941x | 1.215x | slower: 0.941x vs parent |
| 41 | 5 | Remove unnecessary calls to .contiguous() in Attention output if resulting layout is already correct; only call .contiguous() when required by downstream ops. | rejected_correctness | – | – | tests failed (exit 1) |
| 42 | 5 | Remove unnecessary calls to .contiguous() in Attention output if resulting layout is already correct; only call .contiguous() when required by downstream ops. _(retry of exp_6278f718d1)_ | rejected_speed | 0.932x | 1.223x | slower: 0.932x vs parent |
| 43 | 5 | Use torch.compile with mode='reduce-overhead' on the TinyGPT model for the generation loop and model forward calls. | rejected_correctness | – | – | tests failed (exit 1) |
| 44 | 5 | Eliminate redundant LayerNorm computation in Block.forward by fusing LayerNorm and the subsequent addition for the x + self.mlp(self.ln2(x)) path, using torch.add with the out parameter for efficient memory reuse. | rejected_speed | 0.995x | 1.068x | slower: 0.995x vs parent |
| 45 | 5 | Fuse consecutive Linear projections in Attention (qkv and proj) by using torch.addmm with the out parameter and preallocated buffers to minimize temporary allocations and reduce kernel launches. | rejected_correctness | – | – | tests failed (exit 1) |
| 46 | 5 | Directly batch the q, k, v matmul and value matmul in Attention.forward using torch.bmm, avoiding reshape/copy patterns. | rejected_speed | 1.026x | 1.173x | 1.026x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 47 | 5 | Use torch.compile with mode='reduce-overhead' on the TinyGPT model for the generation loop and model forward calls. _(retry of exp_300dacfb8d)_ | patch_failed | – | – | search text not found in model.py: '        logits = model(prompt, caches, start |
| 48 | 5 | Fuse consecutive Linear projections in Attention (qkv and proj) by using torch.addmm with the out parameter and preallocated buffers to minimize temporary allocations and reduce kernel launches. _(retry of exp_8c8cf9df4a)_ | rejected_speed | 1.021x | 1.166x | 1.021x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 49 | 6 | Replace manual attention (matmul + mask + softmax + value matmul) in Attention.forward with torch.nn.functional.scaled_dot_product_attention (SDPA) for the multi-token path (T > 1), preserving current masking semantics and dtype. Single-token path remains as is to avoid risk. | rejected_speed | 0.998x | 1.369x | slower: 0.998x vs parent |
| 50 | 6 | Fuse the residual addition of x + self.mlp(self.ln2(x)) in Block.forward into an in-place add (where safe) by using torch.add(..., out=x) when possible, to cut memory usage and kernel launches. Only perform this fusion if input x is not required after the operation (i.e., no aliasing). | rejected_speed | 0.913x | 1.266x | slower: 0.913x vs parent |
| 51 | 6 | Fuse the input embedding summation in TinyGPT.forward (self.tok(idx) + self.pos(pos)) into a single kernel by using torch.add with the out argument, to cut a temporary allocation. | rejected_speed | 0.983x | 1.363x | slower: 0.983x vs parent |
| 52 | 6 | Pre-pack the causal mask used in Attention.forward as a boolean type (if compatible with all attended ops) to ensure highest masking/memory efficiency on CUDA, as SDPA and some kernels are optimized for bool masks. | rejected_speed | 1.015x | 1.314x | 1.015x is below the noise-adjusted threshold of 1.064x (baseline noise CV 3.2%) |
| 53 | 6 | Replace the manual attention computation in Attention.forward (matmul + masking + softmax + value matmul) in the multi-token case (T > 1) with torch.nn.functional.scaled_dot_product_attention (SDPA), passing the precomputed causal mask in the correct format and shape. For single-token (T==1 with cache), keep current logic to fully preserve correctness for the autoregressive step. | rejected_correctness | – | – | tests failed (exit 1) |
| 54 | 6 | Leverage in-place addition for the residual in Block.forward for the x + self.attn(self.ln1(x), cache) path, using torch.add(..., out=x) only when it is guaranteed that x will not be needed after (i.e., last usage in forward) to avoid unnecessary allocations/copies. If x is not contiguous or shape/dtype do not match, fallback to regular addition. | rejected_speed | 0.996x | 1.248x | slower: 0.996x vs parent |
| 55 | 6 | Move causal mask to boolean dtype (mask = mask.to(torch.bool)) at construction time in Attention.__init__, and propagate this mask type throughout all usage in Attention.forward. Adjust logic in forward to handle bool mask with SDPA and manual masking, avoiding any dtype conversion during forward. Only applied if behavior and numerical results are bit-identical. | rejected_speed | 0.989x | 1.283x | slower: 0.989x vs parent |
| 56 | 6 | Enable torch.set_float32_matmul_precision('high') at module import/init time if running on CUDA to favor TF32 matmuls for attention/MLP for all Linear and matmul ops. Only enable if precision tests confirm output remains within tolerance; make this runtime conditional and mark as medium risk since math order changes. | rejected_speed | 0.940x | 1.232x | slower: 0.940x vs parent |
| 57 | 6 | Replace the manual attention computation in Attention.forward (matmul + masking + softmax + value matmul) in the multi-token case (T > 1) with torch.nn.functional.scaled_dot_product_attention (SDPA), passing the precomputed causal mask in the correct format and shape. For single-token (T==1 with cache), keep current logic to fully preserve correctness for the autoregressive step. _(retry of exp_35557b5f51)_ | rejected_correctness | – | – | tests failed (exit 1) |

## Ablation (leave-one-out re-measurement)

```
Ablation for run_7522558b61: full stack median 1242.59929 tokens_per_s

 contribution        full     without  idea
       1.330x  1241.81808   933.41433  Cache the causal mask or skip mask construction in single-token decoding path in Attention.forward.  (pulls its weight)
       1.035x  1243.38050  1201.54337  Eliminate CPU-GPU synchronization point in generation loop by avoiding .item() call for next token selection; keep next token index as a 1x1 tensor and concatenate directly on device.  (removing it costs nothing measurable)
```

## Files in this bundle

- `optimized_src/` — the full source tree with every shipped change
- `changes.patch` — unified diff from the baseline to the shipped stack
