# Grading: framework, 10-minute trace

| | |
|---|---|
| Answers graded | [answers.md](answers.md): Q1–Q10 final run (2026-10-15 20:28–20:31), then the Q11–Q13 batch (18:26). Earlier versions' answers are kept in [answers_earlier_versions.md](answers_earlier_versions.md). |
| Framework version | `HelloWorldView.java` of 2026-10-15 18:21 for all 13 questions |
| Trace | `runs/runs_to_use/batch-server-10m-20261013-194601` (6,528 rounds) |
| Condition | Full framework: TMLL + RAG + LLM + analyzers, with the `batch_server` program description in the knowledge base. TMLL was available for every answer. |
| Model | GPT-5.6 (`gpt-5.6-sol`) |
| Ground truth | Expected answers in [experiment_draft.md](../../experiment_draft.md) section 2, measured values from the run's `ground_truth/ground_truth.json` and `rounds.csv` |
| Scale | 1 = correct, ½ = partly correct, 0 = wrong |

## Result: 13 / 13

Q1–Q10: 10 / 10 (one fresh conversation). Q11–Q13: 3 / 3 (a separate fresh conversation). All 13 from the same framework version.

| Q | Category | Hub scope | Score | What the answer got right |
|---|---|---|---|---|
| 1 | Comprehension | **Multi-hub** | 1 | Names all 6 threads with their IDs and gives the documented design: the main thread coordinates repeated rounds, four workers compute, `worker-2` commits to a locked cache, `flusher` simulates cache write-back; it says these roles come from the documentation, not the trace. Trace facts: 97,931 `futex` and 13,057 `clock_nanosleep` calls, 68.2% of busy CPU time, the files it writes, background activity. Missing: the round rate (~11 per second) and that `flusher` holds the lock while sleeping. |
| 2 | Comprehension | **Multi-hub** | 1 | Each thread's documented role plus per-round trace evidence: each worker runs ~5.0 ms per round (5.041 / 5.000 / 4.993 / 5.003 ms) and waits 10.8–12.1 ms for CPU; median finishes 15.8 / 17.8 / 20.0 ms against `worker-2`'s 42.9 ms; `worker-2` blocks 24.703 ms per round in `futex` until `flusher` wakes it; `flusher` holds the lock ~40.125 ms per round, including a 40.100 ms sleep. |
| 3 | Comprehension | **Single-hub** | 1 | CPU 3 for 99.9% of its running time (392.459 ms), CPU 8 for 0.482 ms at the start (before it pins itself). |
| 4 | Root cause | **Multi-hub** | 1 | `worker-2`: it ended all 6,528 of the coordinator's waits on other threads (261,974 ms). Chain `batch_server` → `worker-2` → `flusher` → timer. Finishes 22.127 ms after the others (ground truth 22.128 ms); median round 42.870 ms and p99 43.327 ms (ground truth 42.86 / 43.32 ms). |
| 5 | Dependency | **Multi-hub** | 1 | Blocked in `futex` 24.703 ms per round until `flusher` wakes it (ground truth 24.71 ms). Separates idle waits for new work (331,899 ms) from the slowdown waits (161,260 ms, ended by `flusher`). `flusher` holds the lock ~40.125 ms per round. Its own CPU work (4.993 ms per round) is like the others'. |
| 6 | Dependency | **Multi-hub** | 1 | No: per-round CPU is 4.993 ms for `worker-2` against 5.041 / 5.000 / 5.003 ms for the others, slightly less, yet it finishes 22.127 ms later. The cause is the `futex` wait on `flusher`. |
| 7 | Dependency | **Single-hub** | 1 | Two kinds of `futex` wait: 6,529 waits for new work (typically 50.064 ms, ended by the coordinator) and 6,528 waits during rounds (typically 24.871 ms, ended by `flusher`), the latter being the one that matters. Lock interpretation marked medium confidence. |
| 8 | Dependency | **Single-hub** | 1 | It sleeps while holding the lock: inferred hold 40.125 ms per round (median 40.034 ms), of which 40.100 ms is the sleep and 0.018 ms CPU. Moving the sleep out leaves ~0.025 ms. |
| 9 | Quantitative | **Single-hub** | 1 | Per round: 4.663 ms running, 10.753 ms waiting for CPU, 24.703 ms blocked on `flusher` (round profile: 4.993 ms running, 11.375 ms waiting), excluding idle time between rounds. Ground truth: ~5 ms, ~11.5 ms, 24.71 ms. |
| 10 | Remediation | **Multi-hub** | 1 | Move the sleep outside the lock: hold 40.125 → 0.025 ms. Active round 41.820 → 20.124 ms (2.1×), removing the 24.703 ms wait, limited by the other workers at 19.693 ms in 5,180 of 6,528 rounds. Ground truth: rounds 41.80 ms, other workers done by 19.68 ms. |
| 11 | Quantitative | **Multi-hub** | 1 | 22.127 ms later on average (median 22.717 ms); ground truth 22.128 ms (median 22.718 ms). Also each worker's median finish time. From the `process.round_profile` finding. |
| 12 | Remediation | **Single-hub** | 1 | Move the sleep (mean 40.100 ms) outside `cache_lock`: hold 40.125 → 0.025 ms per round (median 0.022 ms). Ground truth: ~40 ms → microseconds. |
| 13 | Root cause | **Single-hub** | 1 | It delays itself: 6,528 timer sleeps (typical 40.012 ms); its 6,529 waits for the coordinator (typical 50.043 ms) are idle time; no other thread on the critical path of a typical sleep; 0 late cycles out of 13,057. |

## Notes

**Protocol.** Q1–Q10 were asked as one batch in a fresh conversation (Q3 and Q5–Q9 with thread IDs, as the framework is designed to take; Q2 and Q4 by name, resolved by the framework's name resolver). Q11–Q13 were asked as a separate batch in a fresh conversation. TMLL (via the Trace Server) was available throughout.

**TMLL's system-call scan stops at 5,000,000 rows**, fewer than this trace contains. It does not affect the answers: their figures come from the Java analyses, which cover the whole trace.

**Run time:** the Q1–Q10 batch took 260.0 s (about 4.3 minutes) with warm caches (TMLL's results were cached from earlier runs); the Q11–Q13 batch took 83.9 s. The earliest cold run of Q1–Q10 took 1,683 s, including a 560 s TMLL system-call scan.

**Differences from the baselines to report:**

- The framework takes thread IDs in its questions (by design); the baselines did not get them.
- The framework answered each batch as one chained conversation; the baselines made a separate call per question.
- The framework analysed the whole trace; the baselines saw a token-budgeted slice.

## History (earlier framework versions)

These results are superseded by the single-version run above; the answers are kept in [answers_earlier_versions.md](answers_earlier_versions.md).

- **Q1–Q10, first run (2026-10-14 12:11–12:34): about 7 / 10.** Q5, Q7 and Q8 explained each thread through its single longest wait, which is the program's one-off 100 ms startup pause, so they blamed `batch_server` instead of `flusher` (½, ½, 0); Q1 and Q9 scored ½. Q8 also reported a wrong sleep count (4,173) from TMLL's partial scan.
- **A run with the 2-minute trace's thread IDs (2026-10-14 12:58–13:02) is excluded:** the framework correctly answered that those threads are not in this trace.
- **Q1–Q10, second run (2026-10-14 13:23–13:30, version before 18:06): 10 / 10** after Q1 was rerun on 2026-10-15 at 13:23 under the changed "what does this trace record" instructions (13:14).
- **Q11–Q13, version before 18:06 (2026-10-15 16:18 run): 2.5 / 3.** Q11 ½: `worker-2` last every round but no number, because the thread analysis lacked the other workers' finish times (the process analysis had them: 40.131 − 18.003 = 22.13 ms). Q12 1 (~19 µs), Q13 1. The fixes between versions are traced on the 2-minute trace; see [../2min/grade.md](../2min/grade.md).
