# Capability Optimization Platform

**Version:** 0.1
**Status:** Proposed
**Initial user:** One software engineer running local models

## Summary

The Capability Optimization Platform is a local-first research system for making local AI models useful for daily software engineering work. It evaluates stable engineering capabilities, tests one intervention at a time, preserves reproducible evidence, and promotes only improvements that hold on unseen tasks.

The product is the improvement loop, not a particular model or coding agent:

```text
Observe -> Measure -> Hypothesize -> Experiment -> Compare -> Promote or reject
```

## Problem

Local models can generate plausible code but remain inconsistent at repository navigation, tool use, editing, verification, and recovery. Existing agent frameworks often couple prompts, tools, execution, and scoring so tightly that it is difficult to determine what caused an improvement.

The platform must answer:

> What is the highest-leverage change we can make today to measurably increase the usefulness of local software engineering models?

## Product Decisions

- Build a personal research platform first, not a multi-user product.
- Optimize deeply for the owner's repositories and workflows before pursuing breadth.
- Organize work around durable capabilities, not models or framework features.
- Treat prompts, tools, retrieval strategies, and agent patterns as testable hypotheses.
- Prefer deterministic evaluation over model-based judging.
- Keep research configurations separate from trusted daily-use configurations.
- Require reproducible evidence before replacing a baseline.
- Add infrastructure only when it enables a previously impossible experiment or materially reduces experimentation time.

## Goals

- Establish repeatable baselines for local coding models.
- Compare models, prompts, retrieval, tools, and harness behavior under equal budgets.
- Diagnose why runs fail, not merely whether they fail.
- Turn successful experiments into trusted daily workflows.
- Preserve trajectories, patches, and outcomes as future research data.
- Keep the common developer workflow simple enough to run frequently.

## Non-Goals for Version 1

- Fine-tuning or reinforcement learning
- Distributed or cloud inference
- Browser or desktop automation
- Multi-agent orchestration
- Multi-user access, authentication, or billing
- Hosted dashboards
- Autonomous pull-request merging
- Automatic experiment generation or baseline promotion
- Replacing Pi, Inspect AI, Promptfoo, or other external tools

## Users and Workflows

The primary user is a software engineer evaluating local models against real work such as:

- Explaining unfamiliar code
- Locating relevant files and symbols
- Diagnosing test and configuration failures
- Making bounded bug fixes
- Editing Docker and infrastructure configuration
- Generating or repairing tests
- Reviewing diffs
- Updating technical documentation

Version 1 succeeds when at least one local configuration is reliable enough to be used naturally several times per day on bounded work.

## Capabilities

The initial capability catalog is:

1. Repository understanding
2. Repository navigation and localization
3. Code understanding
4. Planning
5. Tool selection and use
6. Patch generation
7. Verification
8. Repair after failure
9. Documentation
10. Memory and repository knowledge

Capabilities may depend on one another, but each must be measurable independently where practical.

## Evaluation Strategy

Benchmarks are versioned, immutable releases. Tasks should cover atomic coding, repository navigation, full repairs, configuration work, diagnosis-only work, and correct no-change decisions.

Use three task channels:

- **Development:** frequent prompt and tool iteration
- **Validation:** candidate selection
- **Held-out:** milestone proof only

Every experiment changes one declared major variable. Baseline and candidate use the same tasks, resource budgets, context limits, and repetition policy.

Each experiment declares its hypothesis, target capability, independent variable, expected outcome, evaluation criteria, and promotion threshold before it runs.

Deterministic scoring should use tests, compilation, type checking, schema validation, exact output checks, and diff policies. Human review is reserved for qualities that cannot be scored mechanically.

## Required Run Evidence

Every run records:

- Benchmark, task, repository revision, and content hashes
- Model identity, quantization, runtime, and resolved settings
- Harness, prompt, toolset, scorer, and execution-environment versions
- Full resolved configuration and configuration hash
- Attempts, budgets, duration, and termination reason
- Tool events, retrieved context, final response, and patch
- Deterministic scores and failure classification
- Human intervention count

A bare `failed` result is insufficient. Initial failure categories include localization, repository understanding, reasoning, retrieval, missing knowledge, tool selection, tool execution, editing, verification, repair, environment, timeout, model runtime, evaluator, and unknown.

Execution data is a reusable research dataset. Store experiment metadata, benchmark versions, model configurations, prompts, tool trajectories, retrieved context, validation results, successful patches, and failure traces.

## Success Metrics

The north-star metric is estimated engineering time saved in real workflows.

Supporting metrics are:

- Correct completion rate
- First-attempt success
- Human interventions
- Trust: would the user accept the result without major correction?
- Median and p90 runtime
- Timeouts and tool failures
- Capability breadth
- Regressions against the current baseline

Benchmark pass rate is evidence, not the product objective.

## Promotion Policy

A candidate can replace a baseline only when it:

- Improves paired pass rate by at least 5 percentage points
- Introduces no regression on critical tasks
- Does not reduce a protected capability by more than 3 percentage points
- Does not increase median runtime by more than 20 percent
- Does not increase timeout or intervention rates
- Includes repeated runs for stochastic configurations
- Contains complete reproducibility evidence
- Confirms the improvement on held-out tasks

Promotion requires human approval in version 1.

Automatic experiment generation and automatic baseline promotion are deferred until the manual promotion policy has proved reliable.

## Memory Strategy

Memory separates:

- **Permanent:** repository architecture, coding conventions, and durable developer preferences
- **Long-lived:** validated architectural summaries and recurring patterns
- **Ephemeral:** current investigations and temporary hypotheses

Obsolete information must expire or be superseded so stale context does not pollute later experiments.

## Developer Workflow

The common workflow should stay short:

```text
install -> diagnose environment -> run smoke benchmark -> compare results -> inspect failures -> create one experiment
```

Routine experimentation should require a few commands with sensible defaults.

## Versioning

Version independently:

- Benchmark
- Capability
- Prompt
- Model configuration
- Harness
- Retriever
- Memory
- Scorer
- Experiment

Every result must be reproducible from the recorded versions or content hashes.

## Capability Maturity

| Level | Meaning |
|---|---|
| 0 - Unsupported | No implementation exists |
| 1 - Experimental | A prototype exists but is not measured |
| 2 - Measured | A repeatable benchmark and baseline exist |
| 3 - Reliable | Held-out quality thresholds are met |
| 4 - Optimized | Alternatives were compared and a champion promoted |
| 5 - Self-improving | Experiment recommendation and promotion are substantially automated |

Roadmap priority goes to the lowest-maturity capability with the greatest impact on observed failures.

## Roadmap

### Phase 1: Trustworthy experiment loop

Create a deterministic end-to-end run using a fake harness, isolated repository, protected scorers, persistence, and reports.

### Phase 2: Local-model baseline

Integrate Ollama and Pi, then establish raw and tool-enabled baselines on a small development benchmark.

### Phase 3: Capability studies

Compare repository maps, lexical search, symbol search, verification policies, prompts, and budgets one variable at a time.

### Phase 4: Daily utility

Promote reliable configurations into bounded workflows used on real repositories and measure time saved.

### Phase 5: Guided optimization

Use accumulated failure and experiment evidence to recommend high-value follow-up experiments.

## Acceptance Criteria

Version 1 is successful when:

1. Experiments are reproducible and comparable.
2. Tasks execute in isolated workspaces with protected evaluation.
3. Results contain deterministic scores, artifacts, provenance, and useful failure diagnoses.
4. A baseline and candidate can be compared under equal budgets.
5. A held-out experiment demonstrates at least one repeatable capability improvement.
6. One promoted local configuration provides recurring value in daily engineering work.
