# Model evaluation

## Hardware (inspected 2026-10-11)

| | |
|---|---|
| Machine | Lenovo 82XV, Windows 11 Home build 26300 |
| CPU | Intel Core i5-13420H, 8 cores / 12 threads |
| RAM | 16 GB |
| GPU | NVIDIA GeForce RTX 2050, **4 GB VRAM** (driver 566.36) |
| Disk | 77 GB free on C: |

## Choice

| Model | Ollama tag | Size | Fits in 4 GB VRAM? | Role |
|---|---|---|---|---|
| Qwen3 4B Instruct-2507 (Q4_K_M) | `qwen3:4b-instruct-2507-q4_K_M` | 2.5 GB | Mostly | **Chat model** (Apache 2.0) |
| Qwen3 4B Thinking-2507 (Q4_K_M) | `qwen3:4b` | 2.5 GB | Mostly | Replaced: it always reasons first, taking 1–4 minutes per answer. See Findings. |
| Qwen3 8B (Q4_K_M) | `qwen3:8b` | ~5.2 GB | No, split with CPU | Optional quality comparison (not downloaded) |
| nomic-embed-text v1.5 | `nomic-embed-text` | 274 MB | Yes | **Embeddings** (768 dimensions) |

The brief prefers Qwen3 8B "if hardware supports it." With 4 GB VRAM, it would run largely on the CPU at a fraction of the speed, while also competing for 16 GB of RAM with Docker, Postgres, the API and a browser. Qwen3 4B is the practical default.

The cost of the smaller model is lower writing quality and less reliable tool selection. The design limits both: all numbers are computed in code, and Milestone 1b routes questions to analytics functions with deterministic code rather than relying on the model to choose tools.

## Measured (2026-10-11, Ollama 0.40.3)

| Test | Result |
|---|---|
| `qwen3:4b` short reply, cold load | 9.2 s wall time, 28 tokens/s generation |
| GPU placement at 4k context | 3.5 GB loaded: 33% CPU, 67% GPU |
| `nomic-embed-text` | 768-dimension vectors |

Latency and accuracy against the 50-question evaluation set come in Milestone 3.

## Findings

### `qwen3:4b` is a reasoning-only model, which makes chat too slow

`ollama show qwen3:4b` reports a 262,144-token context and the `thinking` capability. That makes it **Qwen3-4B-Thinking-2507**, which always reasons before answering. Measured behavior with Ollama 0.40.3:

| Setting | What happened |
|---|---|
| `think: false` | The model still reasons, and the reasoning is written **into the answer** ("Hmm, the user wants…"). |
| `think: false` + `/no_think` | Same. The reasoning still lands in the answer. |
| Raw prompt with an empty `<think></think>` pre-filled (Qwen's official "no thinking" switch) | The model ignores it and reasons anyway. |
| `think: true` | The reasoning goes to a separate `thinking` field and the answer is clean. **This is what the adapter uses.** |

**Cost:** the demo question ("Summarize our underperforming assets") produced about 1,100 reasoning tokens before a roughly 300-token answer.

- **Speed:** 99 seconds end to end at an 8k context, which ran 42% on the processor at 14.5 tokens/s.
- **Timeout:** one run through the UI hit the 240-second timeout.

**How the app handles it:**

- **Answers can't break the page.** The adapter treats a timeout or a cut-off answer as a failure, and the UI then shows the verified summary with a note. The demo still works, just without the model's wording.
- **Leaked reasoning is caught.** An earlier version showed a cut-off stream of reasoning as the answer. The number check passed it because the figures in it were correct. This is now caught by `done_reason == "length"`.

**Resolved 2026-10-11:** the chat now uses `qwen3:4b-instruct-2507-q4_K_M` (see below). The original recommendation was to switch to `qwen3:4b-instruct-2507-q4_K_M`. It's the non-reasoning sibling from the same release, about 2.5 GB and Apache 2.0. The adapter detects which kind of model is installed (`/api/show` capabilities), so switching only means changing `PIQ_CHAT_MODEL` and adding the tag to `PIQ_ALLOWED_MODELS`.

### `qwen3:4b-instruct-2507-q4_K_M` (current chat model)

Ollama lists a `thinking` capability for this tag too, but the model doesn't reason: the thinking field comes back empty. Because of that listing, the adapter sends `think: true`, which does no harm here. Measured on the real prompts:

| Question | Time | Output | Number check |
|---|---:|---:|---|
| Summarize our underperforming assets. | 10.1 s | 197 tokens | Figures verified, but it named only 2 of the 4 flagged properties (see below) |
| Why was Westlake flagged? | 7.1 s | 159 tokens | Verified; stated "no causal evidence" |
| Show the NOI budget variance for Westlake | 14.3 s | 271 tokens | **Rejected:** invented "−$208,879.90". The verified summary was shown. |
| Summary after the fixes below (cold start, through the API) | 27.7 s | — | Verified; named all four properties with the correct rules |

**Two fixes came out of this:**

1. **Start from the verified summary.** The prompt now includes the code-built summary, and the model is told to rephrase it without dropping anything.
2. **Every flagged property must be named.** For the underperforming summary, any omitted property means the verified summary is shown instead. This check exists because the first summary had correct numbers but said Harbor and Sunset "do not meet all criteria". Covered by `test_summary_that_omits_flagged_properties_is_rejected`.

### Number verification

Every dollar amount and percentage in a model answer must match a value in the facts the model was given, within rounding (0.5%, or 0.05 for percentages). If any figure doesn't match, the answer is replaced by a deterministic summary built from the same facts, and the rejected figures are listed.

**Limitation:** plain integers such as "4 of 55" aren't checked, and a wrong figure could pass if it happens to equal some other value in the facts.

## Licenses

Checked with `ollama show <model> --license` on 2026-10-11:

| Model | License | Commercial use |
|---|---|---|
| `qwen3:4b` | Apache License 2.0 | Permitted |
| `nomic-embed-text` | Apache License 2.0 | Permitted |

Before any commercial use, confirm the licenses against the upstream model cards on Hugging Face as well.

Docker Desktop is free for personal use and small businesses. A larger client running this themselves would need a paid Docker subscription or an alternative such as Podman or Docker Engine.
