# Grading: framework, 30-minute trace

| | |
|---|---|
| Answers graded | [answers.md](answers.md): Q1–Q10 final run (2026-10-15 21:12–21:24), then the Q11–Q13 batch (18:36–18:37). Earlier versions' answers are kept in [answers_earlier_versions.md](answers_earlier_versions.md). |
| Framework version | `HelloWorldView.java` of 2026-10-15 18:21 for all 13 questions |
| Trace | `runs/runs_to_use/batch-server-30m-20261013-191113` (19,580 rounds) |
| Condition | Full framework: TMLL + RAG + LLM + analyzers, with the `batch_server` program description in the knowledge base. TMLL was available for every answer (the Q1–Q10 run was redone after an earlier attempt ran without it; see the history). |
| Model | GPT-5.6 (`gpt-5.6-sol`) |
| Ground truth | Expected answers in [experiment_draft.md](../../experiment_draft.md) section 2, measured values from the run's `ground_truth/ground_truth.json` and `rounds.csv` |
| Scale | 1 = correct, ½ = partly correct, 0 = wrong |

## Result: 13 / 13

Q1–Q10: 10 / 10 (one fresh conversation). Q11–Q13: 3 / 3 (a separate fresh conversation). All 13 from the same framework version.

| Q | Category | Hub scope | Score | What the answer got right |
|---|---|---|---|---|
| 1 | Comprehension | **Multi-hub** | 1 | Names all 6 threads with their IDs and gives the documented design: the main thread coordinates recurring rounds, four workers compute, `worker-2` commits under a cache lock, `flusher` simulates cache write-back. Trace facts: 293,712 `futex` and 39,161 `clock_nanosleep` calls, 68.6% of busy CPU time, the files it writes, background activity. Missing: the round rate (~11 per second) and that `flusher` holds the lock while sleeping. |
| 2 | Comprehension | **Multi-hub** | 1 | Each thread's documented role plus per-round trace evidence: each worker runs ~5.0 ms per round (5.034 / 4.993 / 4.986 / 4.998 ms) and waits 10.7–12.0 ms for CPU; median finishes 15.7 / 17.8 / 19.9 ms against `worker-2`'s 42.9 ms; `worker-2` blocks 24.708 ms per round in `futex` until `flusher` wakes it; `flusher` holds the lock ~40.127 ms per round, including a 40.102 ms sleep. The most complete Q2 of any run. |
| 3 | Comprehension | **Single-hub** | 1 | CPU 3 for 99.9% of its running time (869.919 ms), CPU 11 for 0.537 ms at the start (before it pins itself). |
| 4 | Root cause | **Multi-hub** | 1 | `worker-2`: it ended ~100% of the coordinator's waits on other threads (19,580 waits, 785,818 ms). Chain `batch_server` ← `worker-2` ← `flusher` ← timer. Finishes 22.156 ms after the others (ground truth 22.156 ms); median round 42.871 ms and p99 43.323 ms (ground truth 42.86 / 43.31 ms). |
| 5 | Dependency | **Multi-hub** | 1 | Blocked in `futex` 24.708 ms per round until `flusher` wakes it (ground truth 24.72 ms). Separates idle waits for new work (995,442 ms) from the slowdown waits (483,785 ms, ended by `flusher`). `flusher` holds the lock ~40.127 ms per round. CPU sharing is secondary (11.381 ms per round). |
| 6 | Dependency | **Multi-hub** | 1 | No: per-round CPU is 4.986 ms for `worker-2` against 5.034 / 4.993 / 4.998 ms for the others, slightly less, yet it finishes 22.156 ms later; its wait for CPU (11.381 ms) is also like theirs (10.739–12.031 ms). The cause is the `futex` wait on `flusher`. **Flaw:** it also reports 2 of 39,159 cycles as "abnormally late" (at 430,410 and 1,059,372 ms); the ground truth has no late rounds (every cycle is within 2.7 ms of the 92.889 ms median, and these two last 92.904 and 90.443 ms), so this is a false positive from TMLL's late-cycle search. It does not change the answer, so the score stays 1. |
| 7 | Dependency | **Single-hub** | 1 | Waits in `futex`, woken by `flusher` (24.708 ms on average, typically 24.880 ms). Separates its 19,581 waits for new work (ended by the coordinator) from the 19,580 waits ended by `flusher`, and identifies the latter as the bottleneck wait. |
| 8 | Dependency | **Single-hub** | 1 | It sleeps while holding the lock: inferred hold 40.127 ms per round (median 40.035 ms), of which 40.102 ms is the sleep and 0.017 ms CPU. Moving the sleep out leaves ~0.025 ms. Lock ownership marked as an inference. |
| 9 | Quantitative | **Single-hub** | 1 | Per round: 4.670 ms running, 10.745 ms waiting for CPU, 24.708 ms blocked on `flusher` (round profile: 4.986 ms running, 11.381 ms waiting). Ground truth: ~5 ms, ~11.5 ms, 24.72 ms. |
| 10 | Remediation | **Multi-hub** | 1 | Move the sleep outside `cache_lock`. Mean round 41.829 → 20.098 ms (2.1×), removing the 24.708 ms wait, limited by the other workers at 19.673 ms. Limited by the other workers in 15,567 of 19,580 rounds; lock hold 40.127 → 0.025 ms. Ground truth: rounds 41.82 ms, other workers done by 19.66 ms. |
| 11 | Quantitative | **Multi-hub** | 1 | 22.156 ms later on average (median 22.743 ms); ground truth 22.156 ms (median 22.744 ms). From the `process.round_profile` finding. |
| 12 | Remediation | **Single-hub** | 1 | Move the sleep (mean 40.102 ms) outside `cache_lock`: hold 40.127 → 0.025 ms per round (~25 µs). Ground truth: ~40 ms → microseconds. |
| 13 | Root cause | **Single-hub** | 1 | It delays itself: 19,580 timer sleeps (typical 40.012 ms); its 19,581 waits for the coordinator (typical 50.044 ms) are idle time; no other thread on the critical path of a typical sleep; 0 late cycles out of 39,161. |

## Notes

**Protocol.** Q1–Q10 were asked as one batch in a fresh conversation (Q3 and Q5–Q9 with thread IDs, as the framework is designed to take; Q2 and Q4 by name, resolved by the framework's name resolver). Q11–Q13 were asked as a separate batch in a fresh conversation. TMLL (via the Trace Server) was available throughout.

**TMLL's system-call scan stops at 5,000,000 rows** (one row per call), about a fifth of this trace's ~23 million calls. It does not affect the answers: their figures come from the Java analyses, which cover the whole trace.

**Run time:** the Q1–Q10 batch took 915.1 s (about 15 minutes), mostly Q1's trace overview (150.5 s), Q2's process analysis (206.5 s) and 42–112 s of thread analysis per thread question; TMLL's results were cached from earlier runs, so its share was under 0.1 s per answer. The Q11–Q13 batch took 476.0 s. The earliest cold run of Q1–Q10, with TMLL's first system-call scan, took 2,880.6 s, 1,356.7 s of it the scan.

**Differences from the baselines to report:**

- The framework takes thread IDs in its questions (by design); the baselines did not get them.
- The framework answered each batch as one chained conversation; the baselines made a separate call per question.
- The framework analysed the whole trace; the baselines saw a token-budgeted slice.

## History (earlier framework versions)

These results are superseded by the single-version run above; the answers are kept in [answers_earlier_versions.md](answers_earlier_versions.md).

- **Q1–Q10 on the 18:21 version without TMLL (2026-10-15 18:59–19:11): 10 / 10, superseded.** The Trace Server, which TMLL reads the trace through, had stopped (it was a background process started from an earlier Claude Code session and ended with that session), so every answer from Q2 on had a `tmll.unavailable` finding and Q6 said late-cycle detection could not run. The answers were all correct, because their key numbers come from the Java analyses, and the rerun with TMLL gives the same figures; the only content difference is Q6's late-cycle paragraph, which in the rerun is a false positive (see Q6). That run took 877.2 s. The Trace Server was restarted at 20:22 as a detached process.
- **Q1–Q10, earlier versions (2026-10-15 10:25 and 11:38–11:52, before 18:06): 10 / 10** after Q1 was rerun at 13:19 under the changed "what does this trace record" instructions (13:14). The first two Q1 attempts (10:25, 13:02) scored ½: correct trace contents but nothing on what the program does, because the earlier instructions said to "use documents only to explain what a concept means". An 11:03 attempt that asked the 30-minute questions against the 2-minute trace is excluded.
- **Q11–Q13, version before 18:06 (16:38 run): 2.5 / 3.** Q11 ½: `worker-2` last every round but no number, because the thread analysis lacked the other workers' finish times (the process analysis had them: 41.829 − 19.673 = 22.16 ms). Q12 1 (~19 µs, from the model's own arithmetic), Q13 1. The fixes between versions are traced on the 2-minute trace; see [../2min/grade.md](../2min/grade.md).
