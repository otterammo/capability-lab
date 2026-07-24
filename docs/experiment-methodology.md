# Experiment Methodology

Each experiment declares a benchmark release, resolved configuration, seed, and one major variable. Baseline and candidate runs must use the same tasks, budgets, context limits, and repetition policy. Failed attempts remain evidence; selective reruns are invalid.

Development tasks support iteration, validation tasks select candidates, and held-out tasks prove milestones. This slice ships only one development smoke task and does not implement comparison or promotion.

Promotion remains a human decision and requires complete provenance, no critical regression, at least five percentage points of paired pass-rate improvement, protected-capability regression no worse than three points, median runtime growth no greater than twenty percent, and no increase in timeout or intervention rate.
