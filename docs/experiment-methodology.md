# Experiment Methodology

Each experiment declares a benchmark release, resolved configuration, seed, and one major variable. Baseline and candidate runs must use the same tasks, budgets, context limits, and repetition policy. Failed attempts remain evidence; selective reruns are invalid.

Development tasks support iteration, validation tasks select candidates, and held-out tasks prove milestones. The raw-versus-Pi comparison runs the unchanged development smoke task once through each harness. It retains the two normal run records and artifacts, then writes one compact comparison report containing run references, paired classifications and score booleans, deltas, and equality evidence. It does not rerun or promote either result.

The comparison path accepts only baseline `raw-ollama` followed by candidate `pi`, with both resolved endpoints exactly `http://desktop:11434`. It resolves each side once and rejects an invalid endpoint, reversed/same/fake pair, or missing model configuration before either run or comparison artifact is created.

The raw baseline makes one non-streaming native Ollama generation request and exposes no tools. Pi keeps its read, bash, edit, and write tools. Both use the same model name and digest, endpoint, task and benchmark hashes, task timeout and tool budget, sandbox image and limits, seed, temperature, maximum output tokens, and repetition count. Any mismatch makes the report incomparable.

Ollama's OpenAI-compatible endpoint cannot set server context size per request. Neither side overrides the server context: equality therefore requires the same Ollama endpoint and model digest. The declared `context_window` is Pi's local truncation ceiling, not a claim that its OpenAI request changes Ollama's server context; the raw request omits `num_ctx`. Changing server context requires a separately versioned model and is outside this comparison.

Promotion remains a human decision and requires complete provenance, no critical regression, at least five percentage points of paired pass-rate improvement, protected-capability regression no worse than three points, median runtime growth no greater than twenty percent, and no increase in timeout or intervention rate.
