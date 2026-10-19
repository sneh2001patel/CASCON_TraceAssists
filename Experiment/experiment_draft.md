# Things todo:
1. Create a problematic software:
    - A software with multithreading, where one thread is slow (use critical path to solve for it)
        - Trace is 2 mins long
        - Trace is 10 mins long
        - The trace is 30 mins long

    Done: program is [batch_server.c](batch_server.c) (described in [batch_server_trace_description.md](batch_server_trace_description.md)), captured with [capture.sh](capture.sh). Traces to use are in `runs/runs_to_use/`:

    | Trace | Run folder | Size | Rounds | `batch_server` PID | `worker-2` TID | `flusher` TID |
    |---|---|---|---|---|---|---|
    | 2 min | `batch-server-2m-20261013-190630` | 140 MB | 1,305 | 159971 | 159975 | 159977 |
    | 10 min | `batch-server-10m-20261013-194601` | 684 MB | 6,528 | 200393 | 200412 | 200414 |
    | 30 min | `batch-server-30m-20261013-191113` | 2.0 GB | 19,580 | 164853 | 164857 | 164859 |

    Do not use `runs/batch-server-2m-20261013-184234` or `runs/batch-server-10m-20261013-184715` (inflated by background activity on the machine).

2. For each trace pass it to LLM to find out how accurate it is, it should answer 10 questions:

    The expected answers are the ground truth for all three traces. The program behaves the same in each; only the round count and the thread IDs differ (see the table above). Measured values come from each run's `ground_truth/ground_truth.json`.

    | # | Category | Hub scope | Question | Expected answer (ground truth) |
    |---|---|---|---|---|
    | 1 | Comprehension | **Multi-hub** | What does this trace record? | A system-wide Linux kernel trace (LTTng) of a multi-threaded batch-processing server, `batch_server`. It runs in repeated rounds (~11 per second): a coordinator thread hands a batch to 4 worker threads and waits for all of them to finish. Each worker does ~5 ms of CPU work. One worker also commits results to a shared cache under a lock, and a background `flusher` thread writes the cache back while holding that lock. The trace also contains unrelated background activity from the rest of the machine. |
    | 2 | Comprehension | **Multi-hub** | What does each thread of `batch_server` do? | `batch_server` (main thread, coordinator): starts each round, wakes the workers and the flusher, blocks until the last worker is done, then sleeps 50 ms. `worker-0`, `worker-1`, `worker-3`: ~5 ms of pure CPU work per round, then report done. `worker-2`: the same ~5 ms of CPU work, then takes `cache_lock` to commit the round's results, then reports done. `flusher`: takes `cache_lock` and holds it during a ~40 ms write-back (a sleep), then releases it. |
    | 3 | Comprehension | **Single-hub** | Which CPU(s) does `batch_server` run on? | Only CPU 3. All 6 threads are pinned to it. |
    | 4 | Root cause | **Multi-hub** | Which thread is the bottleneck of `batch_server`? | `worker-2`. It finishes ~41.8 ms into each round, while the other three workers finish by ~19.7 ms. The coordinator waits for every worker, so `worker-2` sets the length of every round. |
    | 5 | Dependency | **Multi-hub** | Why is `worker-2` slow? | It is blocked on a lock (`cache_lock`, a futex wait) for ~24.7 ms per round on average. `flusher` holds that lock while it sleeps for ~40 ms (simulated write-back), so `worker-2` cannot commit until `flusher` finishes. |
    | 6 | Dependency | **Multi-hub** | Is `worker-2` slow because it uses more CPU than the other workers? | No. It does the same ~5 ms of CPU work per round as the other workers. The extra time is spent blocked waiting for the lock, not running. |
    | 7 | Dependency | **Single-hub** | What is `worker-2` waiting on, and which thread wakes it up? | A futex (the mutex `cache_lock`). `flusher` wakes it when it releases the lock after its write-back sleep. |
    | 8 | Dependency | **Single-hub** | Why does `flusher` hold the lock for so long? | It sleeps (`clock_nanosleep`, ~40 ms) while holding the lock, standing in for slow disk write-back. Its wake-up comes from a timer, not from another thread. There is no real disk I/O. |
    | 9 | Quantitative | **Single-hub** | Break down how `worker-2` spends its time in a typical round (running, waiting for CPU, blocked). | Round ≈ 41.8 ms. **Running** ≈ 5 ms (its CPU work). **Blocked** on `cache_lock` ≈ 24.7 ms on average (p50 24.9, p99 29.6). **Waiting for the CPU** (runnable, preempted) ≈ 12 ms: before it reaches the lock it shares CPU 3 with the other three workers, so it takes ~17 ms to get through its 5 ms of work. |
    | 10 | Remediation | **Multi-hub** | What change would fix the slowdown, and how much faster would rounds be? | Stop holding `cache_lock` during the write-back: for example copy the data under the lock and write it back after releasing it, or move the commit out of the round's critical work. Rounds would drop from ~41.8 ms to ~20 ms (the time for 4 workers × 5 ms on one CPU), about 2× faster. Throughput would go from ~10.9 to ~14 rounds per second, because of the fixed 50 ms gap between rounds. |
    | 11 | Quantitative | **Multi-hub** | In a typical round, how much later does `worker-2` finish than the other three workers? | About 22 ms later, in every round. The other three workers are all done ~19.7 ms after the round starts; `worker-2` finishes at ~41.8 ms. Measured gap: 22.2 ms (2 min), 22.1 ms (10 min), 22.2 ms (30 min) on average, p50 ~22.7 ms, p99 ~25.3 ms; `worker-2` is last in 100% of rounds. The gap is a little smaller than its ~24.7 ms lock wait because the wait starts while the other workers are still running. Measured per round in `ground_truth/rounds.csv` (`slow_worker_finish_ms` − `max_other_finish_ms`). |
    | 12 | Remediation | **Single-hub** | How could `flusher` be changed to hold `cache_lock` for less time, and how long would it hold the lock afterwards? | Do the write-back (the ~40 ms `clock_nanosleep`) after releasing `cache_lock`: take the lock only to copy or swap out the data to flush, release it, then write back. It holds the lock for the whole ~40 ms sleep in every round now; afterwards it would hold it only for the copy, effectively zero (microseconds; `flusher` runs ~0.01 ms per round). `worker-2`'s ~24.7 ms lock wait would then disappear. |
    | 13 | Root cause | **Single-hub** | What is the root cause of `flusher`'s long blocked time: does another thread hold it up, or does it delay itself? | It delays itself. Each round it sleeps ~40 ms in `clock_nanosleep` (while holding `cache_lock`), and each sleep is ended by a timer (hrtimer expiry), not by a wake-up from another thread. That makes it the root of the chain `batch_server` ← `worker-2` ← `flusher` ← timer. Measured: one ~40.0 ms sleep per round, i.e. 1,305 / 6,528 / 19,580 sleeps (2 / 10 / 30 min). Its other long waits (for the coordinator to start the next round) are idle time, not a delay. |

    Hub scope follows TAAF (Ezaz et al., arXiv:2601.02632, Section 4.4; see [single_hub_vs_multi_hub_findings.md](../../RAG_Qs/single_hub_vs_multi_hub_findings.md)): a question is **single-hub** if one central entity and its direct relationships are enough to answer it, and **multi-hub** if the answer combines or compares evidence across several entities. The set has 7 multi-hub questions (Q1, Q2, Q4, Q5, Q6, Q10, Q11) and 6 single-hub questions (Q3, Q7, Q8, Q9, Q12, Q13).
    - Q3: one entity (`batch_server`) and the CPUs it ran on.
    - Q7: centred on `worker-2`: its waits and the thread that ends them.
    - Q8: centred on `flusher`: its sleeps and the wake-up it sends to `worker-2`.
    - Q9: a breakdown of one thread's time.
    - Q12: centred on `flusher` and its own lock-holding (compare Q10, which needs the whole chain and the other workers' finish times).
    - Q13: centred on `flusher`: its own waits and what ended them (a timer). It is the only single-hub root-cause question this program allows, because the bottleneck's cause (`worker-2` → `flusher`) always spans two threads; only the end of the chain lies inside one thread. It uses the same sleep as Q8, from a different angle: Q8 asks why the lock is held (and needs the link to `worker-2`), Q13 asks what causes `flusher`'s own delay.
    - Q7 and Q8 are borderline: each needs one neighbouring thread (`flusher` for Q7, `worker-2` for Q8), but stays centred on one thread, so both are labelled single-hub. Q5 is multi-hub because explaining the slowdown needs `worker-2`, the lock, `flusher` and its timer together. Q11 is multi-hub because it compares `worker-2` with all three other workers.

    How to ask and score:
    - Ask Q4 before Q5–Q9, or in a separate session. Q5–Q9 name `worker-2`, which gives away Q4's answer.
    - Answers may cite thread IDs instead of names. Use the run table above to map them.
    - Q5, Q7, Q8 and the critical path behind them follow from the program's design. Q9's "waiting for CPU" ≈ 12 ms is derived (round time − lock wait − CPU work), not measured directly. Confirm these in Trace Compass (critical path, resources view) before final scoring.

3. For each trace find out how accurate a normal RAG model is (nothing but RAG), how does it help answer the question

4. My framework using the TMLL + RAG + LLM + Anaylzer 

    The framework only runs its analysis (TMLL, critical path, findings) when the question contains a thread ID in the form `tid <number>`. So for the framework, each question that names a thread gets that thread's ID added in brackets. Everything else in the wording is the same as in section 2.

    | # | Framework wording | ID added |
    |---|---|---|
    | 1 | What does this trace record? | none (no thread named) |
    | 2 | What does each thread of batch_server (tid `<PID>`) do? | `batch_server` main thread (= PID) |
    | 3 | Which CPU(s) does batch_server (tid `<PID>`) run on? | `batch_server` main thread (= PID) |
    | 4 | Which thread is the bottleneck of batch_server (tid `<PID>`)? | `batch_server` main thread (= PID). Not `worker-2`'s ID, which would give away the answer |
    | 5 | Why is worker-2 (tid `<W2>`) slow? | `worker-2` |
    | 6 | Is worker-2 (tid `<W2>`) slow because it uses more CPU than the other workers? | `worker-2` |
    | 7 | What is worker-2 (tid `<W2>`) waiting on, and which thread wakes it up? | `worker-2` |
    | 8 | Why does flusher (tid `<FL>`) hold the lock for so long? | `flusher` |
    | 9 | Break down how worker-2 (tid `<W2>`) spends its time in a typical round (running, waiting for CPU, blocked). | `worker-2` |
    | 10 | What change would fix the slowdown, and how much faster would rounds be? | none (no thread named) |
    | 11 | In a typical round, how much later does worker-2 (tid `<W2>`) finish than the other three workers? | `worker-2` |
    | 12 | How could flusher (tid `<FL>`) be changed to hold cache_lock for less time, and how long would it hold the lock afterwards? | `flusher` |
    | 13 | What is the root cause of flusher (tid `<FL>`)'s long blocked time: does another thread hold it up, or does it delay itself? | `flusher` |

    Ready to paste, one question at a time, in order (Q11–Q13 were added later; for the earlier runs, ask them as a follow-up batch):

    **2 min** (`batch-server-2m-20261013-190630`):
    ```
    What does this trace record?
    What does each thread of batch_server (tid 159971) do?
    Which CPU(s) does batch_server (tid 159971) run on?
    Which thread is the bottleneck of batch_server (tid 159971)?
    Why is worker-2 (tid 159975) slow?
    Is worker-2 (tid 159975) slow because it uses more CPU than the other workers?
    What is worker-2 (tid 159975) waiting on, and which thread wakes it up?
    Why does flusher (tid 159977) hold the lock for so long?
    Break down how worker-2 (tid 159975) spends its time in a typical round (running, waiting for CPU, blocked).
    What change would fix the slowdown, and how much faster would rounds be?
    In a typical round, how much later does worker-2 (tid 159975) finish than the other three workers?
    How could flusher (tid 159977) be changed to hold cache_lock for less time, and how long would it hold the lock afterwards?
    What is the root cause of flusher (tid 159977)'s long blocked time: does another thread hold it up, or does it delay itself?
    ```

    **10 min** (`batch-server-10m-20261013-194601`):
    ```
    What does this trace record?
    What does each thread of batch_server (tid 200393) do?
    Which CPU(s) does batch_server (tid 200393) run on?
    Which thread is the bottleneck of batch_server (tid 200393)?
    Why is worker-2 (tid 200412) slow?
    Is worker-2 (tid 200412) slow because it uses more CPU than the other workers?
    What is worker-2 (tid 200412) waiting on, and which thread wakes it up?
    Why does flusher (tid 200414) hold the lock for so long?
    Break down how worker-2 (tid 200412) spends its time in a typical round (running, waiting for CPU, blocked).
    What change would fix the slowdown, and how much faster would rounds be?
    In a typical round, how much later does worker-2 (tid 200412) finish than the other three workers?
    How could flusher (tid 200414) be changed to hold cache_lock for less time, and how long would it hold the lock afterwards?
    What is the root cause of flusher (tid 200414)'s long blocked time: does another thread hold it up, or does it delay itself?
    ```

    **30 min** (`batch-server-30m-20261013-191113`):
    ```
    What does this trace record?
    What does each thread of batch_server (tid 164853) do?
    Which CPU(s) does batch_server (tid 164853) run on?
    Which thread is the bottleneck of batch_server (tid 164853)?
    Why is worker-2 (tid 164857) slow?
    Is worker-2 (tid 164857) slow because it uses more CPU than the other workers?
    What is worker-2 (tid 164857) waiting on, and which thread wakes it up?
    Why does flusher (tid 164859) hold the lock for so long?
    Break down how worker-2 (tid 164857) spends its time in a typical round (running, waiting for CPU, blocked).
    What change would fix the slowdown, and how much faster would rounds be?
    In a typical round, how much later does worker-2 (tid 164857) finish than the other three workers?
    How could flusher (tid 164859) be changed to hold cache_lock for less time, and how long would it hold the lock afterwards?
    What is the root cause of flusher (tid 164859)'s long blocked time: does another thread hold it up, or does it delay itself?
    ```

    How to run:
    - Open the trace's `trace/kernel` folder from `runs/runs_to_use/` in Trace Compass, keep "Use RAG" checked, and ask the 10 questions in order.
    - The chat keeps one conversation going (each question sees the earlier answers). Close and reopen the assistant view before switching to the next trace, so one trace's conversation does not carry into the next.
    - Each answer is appended to `framework_results/answers.csv`; the exact context the LLM received is saved in `framework_results/contexts/`.
    - Differences from sections 2 and 3, to report in the paper: the framework gets the thread ID in the question, and it answers the 10 questions as one conversation instead of 10 separate calls.


Questions: 1, 3,4, 7, 10, 11, 12, 13,  20, 17,