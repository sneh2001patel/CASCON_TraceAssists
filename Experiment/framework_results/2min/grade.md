# Grading: framework, 2-minute trace

| | |
|---|---|
| Answers graded | [ans2.md](ans2.md): Q1–Q10 final run (2026-10-15 20:41–20:43), then the Q11–Q13 batch (18:22–18:23). Earlier versions' answers are kept in [answers_earlier_versions.md](answers_earlier_versions.md) and, for the very first run, [answers.md](answers.md). |
| Framework version | `HelloWorldView.java` of 2026-10-15 18:21 for all 13 questions |
| Trace | `runs/runs_to_use/batch-server-2m-20261013-190630` (1,305 rounds) |
| Condition | Full framework: TMLL + RAG + LLM + analyzers, with the `batch_server` program description in the knowledge base. TMLL was available for every answer. |
| Model | GPT-5.6 (`gpt-5.6-sol`) |
| Ground truth | Expected answers in [experiment_draft.md](../../experiment_draft.md) section 2, measured values from the run's `ground_truth/ground_truth.json` and `rounds.csv` |
| Scale | 1 = correct, ½ = partly correct, 0 = wrong |

## Result: 13 / 13

Q1–Q10: 10 / 10 (one fresh conversation). Q11–Q13: 3 / 3 (a separate fresh conversation). All 13 from the same framework version.

| Q | Category | Hub scope | Score | What the answer got right |
|---|---|---|---|---|
| 1 | Comprehension | **Multi-hub** | 1 | Names all 6 threads with their IDs and gives the documented design: the main thread coordinates repeated rounds, four workers compute, `worker-2` commits under a shared cache lock, `flusher` simulates write-back by sleeping while holding that lock; it says these roles come from the documentation, not the trace. Trace facts: 19,587 `futex` and 2,611 `clock_nanosleep` calls, 68.7% of busy CPU time, how the program was started, background activity. Missing: the round rate (~11 per second). |
| 2 | Comprehension | **Multi-hub** | 1 | Each thread's documented role plus per-round trace evidence: each worker runs ~5.0 ms per round (5.053 / 5.007 / 5.005 / 5.018 ms) and waits 10.7–12.0 ms for CPU; median finishes 15.8 / 17.9 / 20.0 ms against `worker-2`'s 42.9 ms; `worker-2` blocks in `futex` until `flusher` wakes it (1,305 waits, 32,140 ms) and finishes 22.173 ms after the others; `flusher` holds the lock ~40.127 ms per round, including a 40.102 ms sleep. |
| 3 | Comprehension | **Single-hub** | 1 | CPU 3 for 99.7% of its running time (199.036 ms), CPU 7 for 0.559 ms at the start (before it pins itself). |
| 4 | Root cause | **Multi-hub** | 1 | `worker-2`: it ended ~100% of the coordinator's waits on other threads (1,305 waits, 52,384 ms). Chain `batch_server` ← `worker-2` ← `flusher` ← timer. Finishes 22.173 ms after the others (ground truth 22.173 ms); median round 42.872 ms and p99 43.355 ms (ground truth 42.86 / 43.34 ms). |
| 5 | Dependency | **Multi-hub** | 1 | Blocked in `futex` 24.628 ms per round until `flusher` wakes it (ground truth 24.64 ms). Separates idle waits for new work (66,388 ms, ended by the coordinator) from the slowdown waits (32,140 ms, typically 24.786 ms, ended by `flusher`). `flusher` holds the lock ~40.127 ms per round. CPU sharing on CPU 3 ranked as secondary. |
| 6 | Dependency | **Multi-hub** | 1 | No: per-round CPU is 5.005 ms for `worker-2` against 5.053 / 5.007 / 5.018 ms for the others, yet it finishes 22.173 ms later. The cause is the `futex` wait on `flusher`; 0 late cycles out of 2,611. |
| 7 | Dependency | **Single-hub** | 1 | Waits in `futex`, woken by `flusher` (1,305 waits, typically 24.786 ms), which sleeps on a timer while holding the lock. Sets aside its 1,306 waits for new work (typically 50.054 ms, ended by the coordinator) as idle time. Lock-hold estimate marked medium confidence. |
| 8 | Dependency | **Single-hub** | 1 | It sleeps while holding the lock: inferred hold 40.127 ms per round (median 40.034 ms), most of it the ~40.1 ms sleep, which the documentation says simulates write-back. Moving the sleep out leaves ~0.025 ms. Lock ownership marked as an inference. |
| 9 | Quantitative | **Single-hub** | 1 | Per round: 4.640 ms running, 10.863 ms waiting for CPU, 24.628 ms blocked on `flusher` (round profile: 5.005 ms running, 11.518 ms waiting, finish 41.868 ms), excluding idle time between rounds. Ground truth: ~5 ms, ~11.5 ms, 24.64 ms. |
| 10 | Remediation | **Multi-hub** | 1 | Move the sleep outside the lock: hold 40.127 → 0.025 ms. Round 41.868 → 20.169 ms (2.1×), removing the 24.628 ms wait, limited by the other workers at 19.695 ms in 1,017 of 1,305 rounds. Ground truth: rounds 41.86 ms, other workers done by 19.68 ms. |
| 11 | Quantitative | **Multi-hub** | 1 | 22.173 ms later on average (median 22.683 ms); ground truth 22.173 ms (median 22.684 ms). Also `worker-2`'s mean finish (41.868 ms into the round) and the other three's latest (19.695 ms). From the `process.round_profile` finding. |
| 12 | Remediation | **Single-hub** | 1 | Move the sleep (mean 40.102 ms) outside `cache_lock`: hold 40.127 → 0.025 ms per round (median 0.022 ms), i.e. 0.018 ms running plus 0.007 ms waiting for CPU after the timer fires. Ground truth: ~40 ms → microseconds. |
| 13 | Root cause | **Single-hub** | 1 | It delays itself: 1,305 timer sleeps (typical 40.012 ms); its 1,306 waits for the coordinator (typical 50.044 ms) are idle time; no other thread on the critical path of a typical sleep; 0 late cycles out of 2,611. |

## Notes

**Protocol.** Q1–Q10 were asked as one batch in a fresh conversation (Q3 and Q5–Q9 with thread IDs, as the framework is designed to take; Q2 and Q4 by name, resolved by the framework's name resolver). Q11–Q13 were asked as a separate batch in a fresh conversation (the batch labelled them Q1–Q3). TMLL (via the Trace Server) was available throughout.

**The key numbers come from the trace, not the document:** 24.628 ms per round, 1,305 waits, the 40.127 ms lock hold, and the 66,388 ms vs 32,140 ms split between idle waits and slowdown waits. The document supplies only the lock's name and the design intent. In a 10-minute run where the questions used the wrong thread IDs (so there was no trace evidence), the framework refused to present the document as a finding.

**Run time:** the Q1–Q10 batch took 137.2 s (about 2.3 minutes) with warm caches (TMLL's results were cached from earlier runs); the Q11–Q13 batch took 24.1 s (7.8 + 5.8 + 10.5 s).

**Differences from the baselines to report:**

- The framework takes thread IDs in its questions (by design); the baselines did not get them.
- The framework answered each batch as one chained conversation; the baselines made a separate call per question.
- The framework analysed the whole trace; the baselines saw a token-budgeted slice.

## History (earlier framework versions)

These results are superseded by the single-version run above.

**Q1–Q10.**

- **First run (2026-10-14, [answers.md](answers.md)): 7 / 10.** Q5, Q7 and Q9 scored ½ and Q8 0: they explained each thread through its single longest wait, which is always the program's one-off 100 ms startup pause, so they pointed to `batch_server` instead of `flusher`. Q1 scored ½.
- **Second run (versions before 18:06, [answers_earlier_versions.md](answers_earlier_versions.md)): 10 / 10** after Q1 was rerun on 2026-10-15 at 13:29. Two things had changed since the first run: the program description was added to the knowledge base (`tmll/rag/knowledge/workload/batch_server.md`, which names `cache_lock` and explains the design), and thread questions gained a "Process context" step (the thread's part in its process's wait chain). Q1 needed a further instruction change (13:14) that lets a document with domain "workload" name the program's threads and roles. The second run took 154.4 s for Q1–Q10. Compared with the final run, its answers lacked the measured lock hold and round profile: Q10 estimated the coordinator's wait dropping 40.14 → 18.44 ms rather than measuring the round time.
- **To separate the effects of the document and the code change,** rerun this trace with the current code but without the program description in the index.

**Q11–Q13.** Framework versions tried (each a fresh 3-question batch):

| Version | Q11 | Q12 | Q13 | Total |
|---|---|---|---|---|
| Before 18:06 (16:04 run) | ½: estimated ~24.6 ms, equating the gap with `worker-2`'s lock wait | 1: ~19 µs, from the model's own arithmetic | 1 | 2.5 |
| Before 18:06 (16:12 run, Q11 without the thread ID) | ½: said ~40.1 ms, the coordinator's whole wait on `worker-2` | ½: right fix, declined to estimate | 1 | 2 |
| 18:06: adds `process.round_profile` (each thread's finish time per round) | 1: exact | ½: declined to estimate | 1 | 2.5 |
| 18:16: adds an inferred lock-hold finding | 1 | ½: 41.8 ms → 1.7 ms; the hold started when `flusher` was woken, counting its ~1.7 ms wait for CPU 3 before it can take the lock | 1 | 2.5 |
| **18:21: lock hold starts when `flusher` first runs** | **1** | **1**: 40.1 ms → 0.025 ms | **1** | **3** |

Q11 missed because the thread analysis lacked the other workers' finish times, which only the process analysis had. Q12 varied because the post-change hold time depended on the model adding up numbers itself (one run did, one declined); with 18:21 it is a measured finding. The 1.7 ms pre-lock wait was checked in the trace: over 326 rounds, `flusher` waits 1.717 ms on average (median 2.820 ms) between being woken and running, and starts its sleep 0.003 ms after it runs.

**Repeatability:** the earlier versions gave different Q11 and Q12 answers from run to run. The 18:21 answers rest on measured findings, and the three traces' final runs agree closely (e.g. Q11: 22.173 / 22.127 / 22.156 ms, each matching its ground truth), but each trace has been run once on this version.
