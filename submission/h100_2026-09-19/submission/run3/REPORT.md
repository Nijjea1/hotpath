# Hotpath optimization report — dryft_local

**1.468x** vs baseline (tokens_per_s); 1 change(s) shipped. 4 of 42 candidates were accepted (correct and faster than their parent).

- Baseline median: `965.54271 tokens_per_s` (30 samples)
- Optimized median: `1417.38662 tokens_per_s`
- Noise floor (run-to-run CV): 3.70%
- Base commit `74d8af7f` → shipped `67267a6c`

## Accepted chain (in order)

1. **Use torch.nn.functional.scaled_dot_product_attention for the attention computation in Attention.forward.** — 1.612x vs its parent (`model.py`)

## Where the time went

```
Total self time 0.0206s -> 0.0196s (1.06x)
 before_s   after_s    change  location
        —    0.0030       new  fmha_cutlassF_f32_aligned_64x64_rf_sm80(PyTorchMemEffAttention::AttentionKernel<float, cutlass::arch::Sm80, true, 64, 64, 64, true, true>::Params)
        —    0.0030       new  aten::_efficient_attention_forward
   0.0025    0.0025     -0.6%  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 7, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0015    0.0015     -0.2%  void at::native::(anonymous namespace)::vectorized_layer_norm_kernel<float, float, false>(int, float, float const*, float const*, float const*, float*, float*, float*)
   0.0015    0.0015     -0.2%  aten::native_layer_norm
   0.0014    0.0013     -1.8%  aten::mm
   0.0013    0.0013     +0.7%  aten::addmm
   0.0010         — <=0.0001s  aten::bmm
   0.0008    0.0008     -0.0%  aten::cat
   0.0007    0.0007     -0.7%  void at::native::vectorized_elementwise_kernel<4, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul> >(int, at::native::CUDAFunctor_add<float>, std::array<char*, 3ul>)
   0.0007    0.0007     -0.7%  aten::add
   0.0007    0.0007     -0.0%  void at::native::(anonymous namespace)::CatArrayBatchedCopy_vectorized<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 3, 128, 1, 16, 4>(char*, at::native::(anonymous namespace)::CatArrInputTensorMetadata<at::native::(anonymous namespace)::OpaqueType<4u>, unsigned int, 128, 1>, at::native::(anonymous namespace)::TensorSizeStride<unsigned int, 4u>, int, unsigned int)
   0.0005         — <=0.0001s  void gemvNSP_kernel<float, float, float, float, 1, 32, 4, 1024, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005         — <=0.0001s  std::enable_if<!(false), void>::type internal::gemvx::kernel<int, int, float, float, float, float, false, true, true, false, 6, false, cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float> >(cublasGemvParamsEx<int, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float const>, cublasGemvTensorStridedBatched<float>, float>)
   0.0005         — <=0.0001s  void at::native::triu_tril_kernel<float, int, false, 2, false>(at::cuda::detail::TensorInfo<float, int>, at::cuda::detail::TensorInfo<float const, int>, long, long, int)
... 23 more rows
```

Profile times come from `torch.profiler` and show *where* time goes. A `<=` entry is an upper bound, not a measurement: the function fell out of the retained rows, so its cost is only known to be at most that value. Changes under 5% are within the display threshold — a profile is a single observation with no noise floor, so the benchmark table above is what decided each verdict.

## All candidates

| # | Iter | Hypothesis | Status | vs parent | vs baseline | Why |
|---|------|------------|--------|-----------|-------------|-----|
| 1 | 1 | Rewrite Attention.forward to use torch.nn.functional.scaled_dot_product_attention in place of the manual matmul+tril+masked_fill+softmax pattern. | rejected_correctness | – | – | tests failed (exit 1) |
| 2 | 1 | Preallocate the key and value caches in Attention.forward (and manage cache writes in place) instead of growing them with torch.cat every generation step. | rejected_speed | 0.966x | 0.987x | slower: 0.966x vs parent |
| 3 | 1 | Remove the CPU-GPU sync point in generate (eliminate .item() on logits argmax, keep next token on device). | rejected_speed | 0.973x | 0.963x | slower: 0.973x vs parent |
| 4 | 1 | Move the creation of the causal attention mask out of Attention.forward and reuse a cached version, since mask shape does not change during inference. | rejected_speed | 0.963x | 0.957x | slower: 0.963x vs parent |
| 5 | 1 | Rewrite Attention.forward to use torch.nn.functional.scaled_dot_product_attention in place of the manual matmul+tril+masked_fill+softmax pattern. _(retry of exp_4f9f5ea93a)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 6 | 2 | Apply torch.compile in 'reduce-overhead' mode to the model's forward pass in TinyGPT.forward. | rejected_correctness | – | – | tests failed (exit 1) |
| 7 | 2 | Fuse LayerNorm and subsequent elementwise/add ops in Block.forward into one custom kernel, replacing the call sequence x = x + self.attn(self.ln1(x), cache). | rejected_speed | 0.984x | 0.964x | slower: 0.984x vs parent |
| 8 | 2 | Fuse MLP matmul/add/activation in Block.forward into a single custom kernel. | rejected_correctness | – | – | tests failed (exit 1) |
| 9 | 2 | Fuse attention output projection and attention value matmul into a single kernel in Attention.forward. | rejected_correctness | – | – | tests failed (exit 1) |
| 10 | 2 | Apply torch.compile in 'reduce-overhead' mode to the model's forward pass in TinyGPT.forward. _(retry of exp_453ccd370d)_ | rejected_speed | 0.978x | 0.977x | slower: 0.978x vs parent |
| 11 | 2 | Fuse MLP matmul/add/activation in Block.forward into a single custom kernel. _(retry of exp_67af8b79f7)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 12 | 2 | Fuse attention output projection and attention value matmul into a single kernel in Attention.forward. _(retry of exp_e8da3de211)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 13 | 3 | Use torch.nn.functional.scaled_dot_product_attention for the attention computation in Attention.forward. | rejected_correctness | – | – | tests failed (exit 1) |
| 14 | 3 | Enable TF32 matmul for attention and MLP layers if supported, to improve matmul throughput with minimal effect on numerics. | rejected_speed | 1.035x | 0.914x | 1.035x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 15 | 3 | Fuse LayerNorm and add in Block.forward manually using a custom kernel to replace x = x + self.attn(self.ln1(x), cache). | rejected_speed | 0.817x | 0.781x | slower: 0.817x vs parent |
| 16 | 3 | Preallocate the key and value caches in Attention.forward to max length, writing new tokens in place, removing the torch.cat each step. | rejected_correctness | – | – | tests failed (exit 1) |
| 17 | 3 | Use torch.nn.functional.scaled_dot_product_attention for the attention computation in Attention.forward. _(retry of exp_8418acd7b8)_ | accepted | 1.612x | 1.468x | shipped in this bundle |
| 18 | 3 | Preallocate the key and value caches in Attention.forward to max length, writing new tokens in place, removing the torch.cat each step. _(retry of exp_38ac9ae4b0)_ | rejected_speed | 0.928x | 0.922x | slower: 0.928x vs parent |
| 19 | 4 | Remove unnecessary transpose and contiguous/view pair after attention output in Attention.forward. | rejected_speed | 0.963x | 1.294x | slower: 0.963x vs parent |
| 20 | 4 | Remove unnecessary output projection bias parameter to streamline Attention output. | patch_failed | – | – | worker returned no edits |
| 21 | 4 | Remove redundant .split(D_MODEL, dim=2) calls if unnecessary due to recent refactoring to F.scaled_dot_product_attention. | rejected_speed | 1.027x | 1.369x | 1.027x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 22 | 4 | Skip LayerNorm computation for input sequences of length 1 during decoding, reusing precomputed normalization stats where possible. | rejected_correctness | – | – | tests failed (exit 1) |
| 23 | 4 | Skip LayerNorm computation for input sequences of length 1 during decoding, reusing precomputed normalization stats where possible. _(retry of exp_bbd50afcfc)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 24 | 4 | Remove unnecessary output projection bias parameter to streamline Attention output. _(retry of exp_eb155583a0)_ | rejected_speed | 1.046x | 1.446x | 1.046x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 25 | 4 | Capture and replay the single-token decode step of TinyGPT's forward pass in a CUDA graph to minimize kernel launch and Python overhead during generation. | rejected_correctness | – | – | tests failed (exit 1) |
| 26 | 4 | Fuse the projection layer (self.proj) into the attention softmax/value matmul as a single custom kernel, eliminating a separate GEMV/matmul kernel call in Attention.forward. | rejected_speed | 0.813x | 0.751x | slower: 0.813x vs parent |
| 27 | 4 | Fuse addmm/linear operations in Block's MLP (nn.Linear + GELU + Linear) into one kernel, reducing kernel launches and memory movement for the MLP component. | rejected_correctness | – | – | tests failed (exit 1) |
| 28 | 4 | Capture and replay the single-token decode step of TinyGPT's forward pass in a CUDA graph to minimize kernel launch and Python overhead during generation. _(retry of exp_864b985f6c)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 29 | 4 | Fuse addmm/linear operations in Block's MLP (nn.Linear + GELU + Linear) into one kernel, reducing kernel launches and memory movement for the MLP component. _(retry of exp_a99e5d5cf4)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 30 | 5 | Fuse the QKV projection in Attention.forward into a single custom kernel, replacing the three separate view/transpose and split operations after the initial linear. | rejected_speed | 0.939x | 1.357x | slower: 0.939x vs parent |
| 31 | 5 | Fuse the MLP's two Linear layers and intermediate GELU in Block.forward into a single custom kernel. | rejected_speed | 0.667x | 0.934x | slower: 0.667x vs parent |
| 32 | 5 | Remove redundant tensor operations in Attention.forward after using scaled_dot_product_attention: eliminate unnecessary .transpose/.contiguous/.view calls. | accepted | 1.118x | 1.001x | accepted, but not in the final head |
| 33 | 5 | Fuse LN1 input normalization, attention computation, and first residual (x + attn(...)) into a single custom kernel in Block.forward, replacing sequence of ln1 + attn + add with a single call. | rejected_speed | 0.839x | 0.788x | slower: 0.839x vs parent |
| 34 | 6 | Remove unnecessary per-token CPU sync in the generation loop by keeping the next token entirely on device, avoiding .item() and torch.tensor calls. | rejected_speed | 1.063x | 1.549x | 1.063x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 35 | 6 | Preallocate and reuse the output tensor in generate() for the growing sequence, reducing expensive tensor concatenation and allocations at every token step. | rejected_speed | 0.967x | 1.409x | slower: 0.967x vs parent |
| 36 | 6 | Fuse the two LayerNorms in Block (ln1 and ln2) for the single-token decode case, running one LayerNorm and saving stats/results where possible, since both operate on length-1 tensors and normalization is less useful/redundant there. | rejected_correctness | – | – | tests failed (exit 1) |
| 37 | 6 | Enable TF32 matmul globally for attention and MLP layers unless explicitly disabled, accepting minimal floating-point order changes for throughput gain. | rejected_speed | 0.988x | 1.463x | slower: 0.988x vs parent |
| 38 | 6 | Fuse the two LayerNorms in Block (ln1 and ln2) for the single-token decode case, running one LayerNorm and saving stats/results where possible, since both operate on length-1 tensors and normalization is less useful/redundant there. _(retry of exp_98988068cb)_ | rejected_correctness | – | – | tests failed (exit 1) |
| 39 | 6 | Build and reuse a causal mask buffer once per device, not every step, in Attention.forward. | not_selected | 1.122x | 1.040x | correct and 1.122x faster, but not in the top-2 beam; may be re-proposed on a su |
| 40 | 6 | Keep the next-token tensor on device in generate(), avoiding .item() and CPU sync in per-token decode. | rejected_speed | 1.072x | 1.045x | 1.072x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 41 | 6 | Fuse LayerNorm and following elementwise add in Block.forward into a single call for the residual connection. | rejected_speed | 1.048x | 0.982x | 1.048x is below the noise-adjusted threshold of 1.074x (baseline noise CV 3.7%) |
| 42 | 6 | Minimize or conditionally skip the causal mask allocation and application for single-token decode in Attention.forward. | accepted | 1.250x | 1.267x | accepted, but not in the final head |

## Ablation (leave-one-out re-measurement)

```
Ablation for run_1cc853d8c6: full stack median 1416.45236 tokens_per_s

 contribution        full     without  idea
       1.539x  1416.45236   920.33258  Use torch.nn.functional.scaled_dot_product_attention for the attention computation in Attention.forward.  (pulls its weight)
```

## Files in this bundle

- `optimized_src/` — the full source tree with every shipped change
- `changes.patch` — unified diff from the baseline to the shipped stack
