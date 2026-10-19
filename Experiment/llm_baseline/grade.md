# Grading: LLM-only baseline, all traces

| | |
|---|---|
| Answers graded | [results/answers_repeat1.md](results/answers_repeat1.md) (repeat 1; 13 questions × 9 slices = 117 answers) |
| Traces | `runs/runs_to_use/`: 2 min (1,305 rounds), 10 min (6,528), 30 min (19,580) |
| Slices | A = first ~200k tokens of the trace (no `batch_server` events); B = ~200k tokens from the middle (about one round); C = 20 short windows spread over the trace |
| Condition | LLM only: system prompt + trace slice + question, no tools, no documents |
| Model | GPT-5.6 (`gpt-5.6-sol`) |
| Ground truth | Expected answers in [experiment_draft.md](../experiment_draft.md) section 2, measured values from each run's `ground_truth/ground_truth.json` and `rounds.csv` |
| Scale | 1 = correct, ½ = partly correct, 0 = wrong or no answer |

## Result: 41.5 / 117 (35.5%)

| Q | Category | Hub scope | 2m·A | 2m·B | 2m·C | 10m·A | 10m·B | 10m·C | 30m·A | 30m·B | 30m·C | Total / 9 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Comprehension | **Multi-hub** | 0 | ½ | ½ | 0 | ½ | ½ | 0 | ½ | ½ | **3** |
| 2 | Comprehension | **Multi-hub** | 0 | ½ | ½ | 0 | ½ | ½ | 0 | ½ | ½ | **3** |
| 3 | Comprehension | **Single-hub** | 0 | 1 | 1 | 0 | 1 | 1 | 0 | 1 | 1 | **6** |
| 4 | Root cause | **Multi-hub** | 0 | 0 | ½ | 0 | 0 | 0 | 0 | 1 | ½ | **2** |
| 5 | Dependency | **Multi-hub** | 0 | 0 | ½ | 0 | 0 | 0 | 0 | 0 | ½ | **1** |
| 6 | Dependency | **Multi-hub** | 0 | ½ | 1 | 0 | ½ | 1 | 0 | ½ | ½ | **4** |
| 7 | Dependency | **Single-hub** | 0 | ½ | 1 | 0 | 1 | 1 | 0 | 1 | 1 | **5.5** |
| 8 | Dependency | **Single-hub** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | **1** |
| 9 | Quantitative | **Single-hub** | 0 | ½ | 0 | 0 | ½ | 0 | 0 | 1 | 0 | **2** |
| 10 | Remediation | **Multi-hub** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 11 | Quantitative | **Multi-hub** | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | **2** |
| 12 | Remediation | **Single-hub** | ½ | 1 | 1 | ½ | 1 | ½ | ½ | 1 | ½ | **6.5** |
| 13 | Root cause | **Single-hub** | 0 | ½ | 1 | 0 | 1 | 1 | 0 | 1 | 1 | **5.5** |
| | | **Total / 13** | 0.5 | 5 | 7 | 0.5 | 7 | 5.5 | 0.5 | 9.5 | 6 | **41.5** |

### Summaries

| By slice | Score | Max | % |
|---|---|---|---|
| A (start of trace) | 1.5 | 39 | 4% |
| B (middle window) | 21.5 | 39 | 55% |
| C (sampled windows) | 18.5 | 39 | 47% |

| By trace | Score | Max | % |
|---|---|---|---|
| 2 min | 12.5 | 39 | 32% |
| 10 min | 13 | 39 | 33% |
| 30 min | 16 | 39 | 41% |

| By category | Questions | Score | Max | % |
|---|---|---|---|---|
| Comprehension | Q1, Q2, Q3 | 12 | 27 | 44% |
| Root cause | Q4, Q13 | 7.5 | 18 | 42% |
| Dependency | Q5, Q6, Q7, Q8 | 11.5 | 36 | 32% |
| Quantitative | Q9, Q11 | 4 | 18 | 22% |
| Remediation | Q10, Q12 | 6.5 | 18 | 36% |

| By hub scope | Questions | Score | Max | % |
|---|---|---|---|---|
| Single-hub | Q3, Q7, Q8, Q9, Q12, Q13 | 26.5 | 54 | 49% |
| Multi-hub | Q1, Q2, Q4, Q5, Q6, Q10, Q11 | 15 | 63 | 24% |

## What the answers got right and wrong

| Q | Notes |
|---|---|
| 1 | B/C slices name `batch_server` and its threads (on CPU 3) as one workload among the desktop activity, but none says what it does (rounds, coordinator, workers, flusher, lock). A slices never reach `batch_server`. |
| 2 | B/C slices give roles from behaviour (coordinator wakes everyone and sleeps; workers compute then block; flusher timer-driven), but none finds `worker-2`'s commit under `cache_lock` or that `flusher` holds it while sleeping. |
| 3 | B/C slices: CPU 3 (main thread). A slices: undetermined. |
| 4 | Only 30m·B finds the chain `flusher` → `worker-2` → `batch_server`. 2m·C and 30m·C lean to `worker-2` but hedge. Others blame CPU-3 serialization or name whichever worker was last in the rotation (`worker-3`, `worker-0`, `worker-1`). |
| 5 | Mostly blamed on CPU contention among the workers on CPU 3. 2m·C and 30m·C also mention futex waits / `flusher`. |
| 6 | B/C slices all say No with near-equal per-worker CPU times (~5 ms); 2m·C and 10m·C also point to futex waiting (1), the rest give no reason for the slowness (½). |
| 7 | With trace data, mostly finds `flusher`'s futex wake on `worker-2`'s lock address (24.86 ms, 27.06 ms). 2m·B names `batch_server` (the wait for new work). |
| 8 | Denies that `flusher` holds a lock in all but 30m·B, which infers it from the 40 ms sleep followed by the wake of `worker-2`. |
| 9 | Only 30m·B is complete (5.19 / 10.60 / 27.05 ms). 2m·B and 10m·B get running and CPU wait right but not the lock wait. C slices break down a µs-long wake-up instead of a round. |
| 10 | All B/C slices propose removing the CPU-3 pinning so workers run in parallel ("~4× faster"). That does not address the lock, the actual bottleneck. A slices guess unrelated fixes. |
| 11 | 10m·B (20.3 ms) and 30m·B (22.8 ms) measure one round correctly. Four answers mistake `worker-2`'s lock-wait futex for its finish and conclude it is not last. |
| 12 | B/C: right fix (sleep outside the lock), mostly with a µs estimate. A: generic fix only (½). 10m·C pairs sleeps across windows ("60 s" hold, ½); 30m·C gives no number (½). |
| 13 | B/C find the self-imposed `clock_nanosleep` ended by a timer (40.43 / 40.02 ms). 2m·B blames the wait for `batch_server` (idle time). A slices guess `lttng-consumerd`. |

## Grading rules

- **Q1:** 1 = says what `batch_server` does (rounds, coordinator, workers, flusher); ½ = names `batch_server` and its threads but not what they do; 0 = no `batch_server`.
- **Q2:** 1 = each thread's role, including `worker-2`'s commit under `cache_lock` and `flusher` holding it; ½ = roles from behaviour without the lock.
- **Q4:** 1 = identifies `worker-2` as the thread the round waits on (naming `flusher` as the root cause as well is fine); ½ = leans to `worker-2` but hedges; 0 = another thread or "CPU serialization".
- **Q6:** 1 = No, with equal CPU and the extra time attributed to waiting; ½ = No with equal CPU but no reason, or a weak comparison; 0 = undetermined.
- **Q9:** 1 = running ~5 ms, waiting for CPU ~11–12 ms and blocked on the lock ~25 ms; ½ = two of the three; 0 = otherwise (including breaking down a µs wake-up instead of a round).
- **Q10:** 1 = release the lock before the sleep, with a ~2× estimate for the active round; ½ = the fix with a wrong estimate; 0 = a different fix (e.g. removing the CPU pinning).
- **Q11:** 1 = ~22 ms (within a few ms) with `worker-2` last; ½ = the mechanism but a wrong number. **Q12:** 1 = the fix with ~40 ms → µs; ½ = the fix without a usable number.
- **Q13:** 1 = it delays itself with a timer-ended sleep; ½ = partly blames another thread.
- Other questions follow the expected answers in the draft. An answer that says the slice is insufficient, with no answer, scores 0.

## Notes

- **Slice A never contains `batch_server`**, so the A slices score almost nothing (only the generic Q12 fix). The effective test is the B and C slices.
- **Typical failure: blaming CPU 3.** Without knowing the program, answers often explain the slowness by the four workers sharing CPU 3 (Q4, Q5, Q10). That contention is real but secondary; the lock held during `flusher`'s sleep is never found except in 30m·B.
- **Multi-hub questions are much harder** for the LLM alone than single-hub ones (see the hub table), the same pattern TAAF reports.
- **Trace length barely matters:** each slice holds ~200k tokens regardless of trace length, so the 2-, 10- and 30-minute traces give the model similar amounts of data.

## Comparison with the other conditions

| Condition | Score | % | Notes |
|---|---|---|---|
| LLM only | 41.5 / 117 | 35% | 9 slices per question |
| LLM + RAG | 80 / 117 | 68% | 9 slices per question |
| Framework | 39 / 39 | 100% | 13 questions × 3 traces, whole trace; see `framework_results/*/grade.md` (Q1–Q10 and Q11–Q13 were run with different framework versions; questions carry thread IDs; answers come from one conversation per trace) |
