# Grading: LLM + RAG baseline, all traces

| | |
|---|---|
| Answers graded | [results/answers_repeat1.md](results/answers_repeat1.md) (repeat 1; 13 questions × 9 slices = 117 answers) |
| Traces | `runs/runs_to_use/`: 2 min (1,305 rounds), 10 min (6,528), 30 min (19,580) |
| Slices | A = first ~200k tokens of the trace (no `batch_server` events); B = ~200k tokens from the middle (about one round); C = 20 short windows spread over the trace |
| Condition | LLM + RAG: the LLM-only setup plus the documents the TMLL RAG layer retrieves for each question (frozen in `retrieved_docs.json`), including the `batch_server` program description when retrieved; no TMLL trace analysis |
| Model | GPT-5.6 (`gpt-5.6-sol`) |
| Ground truth | Expected answers in [experiment_draft.md](../experiment_draft.md) section 2, measured values from each run's `ground_truth/ground_truth.json` and `rounds.csv` |
| Scale | 1 = correct, ½ = partly correct, 0 = wrong or no answer · ½* = correct only from the retrieved program description, with no trace evidence (A slices) |

## Result: 80 / 117 (68.4%)

| Q | Category | Hub scope | 2m·A | 2m·B | 2m·C | 10m·A | 10m·B | 10m·C | 30m·A | 30m·B | 30m·C | Total / 9 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Comprehension | **Multi-hub** | 0 | 0 | ½ | 0 | ½ | ½ | 0 | ½ | ½ | **2.5** |
| 2 | Comprehension | **Multi-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 3 | Comprehension | **Single-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 4 | Root cause | **Multi-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 5 | Dependency | **Multi-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 6 | Dependency | **Multi-hub** | 0 | 1 | 1 | 0 | 1 | 1 | 0 | 1 | ½ | **5.5** |
| 7 | Dependency | **Single-hub** | ½* | ½ | 1 | 0 | 1 | 1 | ½* | 1 | 1 | **6.5** |
| 8 | Dependency | **Single-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 9 | Quantitative | **Single-hub** | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | **2** |
| 10 | Remediation | **Multi-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 11 | Quantitative | **Multi-hub** | 0 | 1 | ½ | 0 | 1 | 1 | 0 | 1 | 1 | **5.5** |
| 12 | Remediation | **Single-hub** | ½* | 1 | 1 | ½* | 1 | 1 | ½* | 1 | 1 | **7.5** |
| 13 | Root cause | **Single-hub** | 0 | ½ | 1 | 0 | 1 | 1 | 0 | 1 | 1 | **5.5** |
| | | **Total / 13** | 4 | 10 | 11 | 3.5 | 12.5 | 11.5 | 4 | 12.5 | 11 | **80** |

### Summaries

| By slice | Score | Max | % |
|---|---|---|---|
| A (start of trace) | 11.5 | 39 | 29% |
| B (middle window) | 35 | 39 | 90% |
| C (sampled windows) | 33.5 | 39 | 86% |

| By trace | Score | Max | % |
|---|---|---|---|
| 2 min | 25 | 39 | 64% |
| 10 min | 27.5 | 39 | 71% |
| 30 min | 27.5 | 39 | 71% |

| By category | Questions | Score | Max | % |
|---|---|---|---|---|
| Comprehension | Q1, Q2, Q3 | 17.5 | 27 | 65% |
| Root cause | Q4, Q13 | 13 | 18 | 72% |
| Dependency | Q5, Q6, Q7, Q8 | 27 | 36 | 75% |
| Quantitative | Q9, Q11 | 7.5 | 18 | 42% |
| Remediation | Q10, Q12 | 15 | 18 | 83% |

| By hub scope | Questions | Score | Max | % |
|---|---|---|---|---|
| Single-hub | Q3, Q7, Q8, Q9, Q12, Q13 | 36.5 | 54 | 68% |
| Multi-hub | Q1, Q2, Q4, Q5, Q6, Q10, Q11 | 43.5 | 63 | 69% |

## What the answers got right and wrong

| Q | Notes |
|---|---|
| 1 | The program description was not retrieved for Q1, so results match the LLM-only baseline: B/C name `batch_server` without saying what it does; 2m·B and A slices miss it. |
| 2 | B/C combine the documented roles with the trace (`worker-2` commits under the cache lock and blocks on it; `flusher` sleeps ~40 ms). A slices give the roles from the document only (½*). |
| 3 | B/C: all six threads on CPU 3. A slices: "expected CPU 3" from the document (½*). |
| 4 | B/C call `flusher` the causal bottleneck and `worker-2` the thread the round waits on (1 under the rubric). A slices: the same as a hypothesis from the document (½*). |
| 5 | B/C: blocked on the lock held by the sleeping `flusher`, with measured waits (24.86 ms, 27.06 ms). A slices: from the document (½*). |
| 6 | B/C: No, equal CPU, the extra time is a futex wait on `flusher`; 30m·C can't compare CPU reliably (½). A slices: undetermined. |
| 7 | Mostly `flusher` on the same futex address. 2m·B names `batch_server` (its slice ends before `flusher`'s wake). A: 2m/30m from the document (½*), 10m undetermined. |
| 8 | B/C: sleeps ~40 ms (40.43 / 40.02 ms) while holding the lock, then wakes `worker-2`. A slices: from the document (½*). |
| 9 | Only 10m·B (4.97 / 10.63 / 24.86 ms) and 30m·B (5.19 / 10.60 / 27.05 ms) are complete. 2m·B mis-splits the round; C slices break down a µs-long wake-up. |
| 10 | B/C: release the lock before the sleep; active round ~40 → ~20 ms (2×), 90 → 70 ms per round period. A slices: the same from the document (½*); 2m·A's estimate (90 → 50 ms) is wrong. |
| 11 | The document lets B/C recognise the lock wait as a wait, not a finish: ~20–23 ms (ground truth 22.1–22.2 ms). 2m·C overestimates (~35 ms, ½). A slices guess ~40 ms from the document (0). |
| 12 | All B/C correct (~40 ms → µs). A slices: the fix from the document (½*). |
| 13 | The program description was not retrieved for Q13, so results match the LLM-only baseline. |

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

- **The program description is decisive when retrieved** (Q2–Q5, Q8, Q10–Q12): it tells the model what to look for, and with trace data (B/C) the answers then confirm it with measured values. It was not retrieved for Q1 and Q13, which score like the LLM-only baseline.
- **A slices (no `batch_server` events) still score ½\*** on most questions by answering from the document alone. These answers are correct about the design but not supported by the trace.
- **Q9 stays weak** (2 / 9): a per-round breakdown needs a complete round in the slice, which only two B slices contain.
- **Multi-hub and single-hub questions score about the same with RAG** (see the hub table), because the document supplies the cross-thread structure the LLM alone lacks.

## Comparison with the other conditions

| Condition | Score | % | Notes |
|---|---|---|---|
| LLM only | 41.5 / 117 | 35% | 9 slices per question |
| LLM + RAG | 80 / 117 | 68% | 9 slices per question |
| Framework | 39 / 39 | 100% | 13 questions × 3 traces, whole trace; see `framework_results/*/grade.md` (Q1–Q10 and Q11–Q13 were run with different framework versions; questions carry thread IDs; answers come from one conversation per trace) |
