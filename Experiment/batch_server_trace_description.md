# What the `batch_server` trace records

This file describes the program that runs while the CASCON experiment traces are captured: what it is, what its threads do, and what that activity looks like in an LTTng kernel trace. It is the reference answer to questions like *"What does this trace do?"* or *"What is the program in this trace doing?"*

- Program source: [batch_server.c](batch_server.c)
- Capture script: [capture.sh](capture.sh) (`./capture.sh 2m | 10m | 30m`)
- Per-run answer key: `runs/<session>/ground_truth/ground_truth.json` and `rounds.csv`

> **Part 1** describes the program as its developer would. **Part 2** gives the performance behaviour and the root cause of the slowness. It is the answer key, so keep Part 2 out of anything the systems under test can see.

---

## Part 1: What the program does

### One-paragraph summary

The trace records a multi-threaded **batch-processing server** called `batch_server` running on Linux for a fixed length of time (2, 10 or 30 minutes). The program runs in repeated **rounds**, about 11 per second. In each round:

1. A coordinator thread hands one batch of work to each of 4 worker threads and waits for all of them to finish.
2. Each worker does a short burst of computation.
3. One worker also commits its results to a shared in-memory cache that is protected by a lock.
4. A background `flusher` thread writes the cache back to storage. In the program this write is simulated with a sleep while holding the cache lock.

When every worker has finished, the round ends. The coordinator then idles for 50 ms and starts the next round. All of the program's threads run on a single CPU (CPU 3).

### Threads

The table uses default settings. The names are the `comm` / thread names you see in the trace.

| Thread | Count | Role | Activity per round |
|---|---|---|---|
| `batch_server` | 1 | Coordinator (main thread, PID = its TID) | Starts the round, wakes the flusher and all workers, blocks until the last worker reports done, then sleeps 50 ms |
| `worker-0`, `worker-1`, `worker-3` | 3 | Compute workers | Wait for a batch, spin on the CPU for about 5 ms, report done |
| `worker-2` | 1 | Compute worker that also owns the commit step | Waits for a batch, spins about 5 ms, takes `cache_lock`, commits the round's results, releases the lock, reports done |
| `flusher` | 1 | Background cache write-back | Waits to be told to flush, takes `cache_lock`, writes back the previous batch (a 40 ms sleep standing in for disk I/O), releases the lock |

### Lifecycle of one run

1. **Startup** (well under a second)
   - The process starts and pins itself to CPU 3; all threads it creates inherit this.
   - It calibrates a CPU busy-loop (about 50–100 ms of pure computation on the main thread).
   - It creates 4 workers and 1 flusher (`clone3`, thread stacks via `mmap`), then waits 100 ms.
   - It prints its PID and thread IDs to stdout.
2. **Steady state** (the requested duration). Rounds repeat as described above.
   - Expected round counts: about 1,300 for 2 minutes, about 6,600 for 10 minutes, about 19,800 for 30 minutes.
   - All round bookkeeping goes to memory allocated up front, so this phase makes no file I/O, network, `brk` or `mmap` calls.
3. **Shutdown**
   - The coordinator tells every thread to exit and joins them.
   - It writes `ground_truth.json` and `rounds.csv` to disk (`mkdir`, `openat`, `write`, `close`).
   - It prints a short summary and exits.

### What this looks like in the kernel trace

**Scheduling events.** `sched_switch` / `sched_wakeup` between the 6 threads, all on CPU 3, in a regular pattern every ~90 ms:
- The coordinator wakes, wakes the flusher and the workers, then blocks.
- Workers run one after another on the shared CPU.
- The coordinator is woken at the end of the round, then sleeps.

**System calls:**
- `futex` (wait/wake) from the semaphores that start and finish each round, and from `cache_lock` when it is contended.
- `clock_nanosleep` from the coordinator's 50 ms gap and the flusher's simulated write-back.
- Almost nothing else during the steady state. The CPU work is a pure computation loop with no system calls, so it appears only as time in the RUNNING state.

**Timer / interrupt events.** hrtimer expiries waking the sleeping threads.

**Other processes.** The trace is system-wide, so it also contains everything else on the machine (desktop, daemons, the LTTng session daemon, kernel threads). `batch_server` and its threads are the workload under study.

### What the program does *not* do

- No network activity (no sockets).
- No real disk I/O during the steady state. The "write-back" is a sleep, so there are no block-device requests from this program until it writes its two result files at exit.
- No child processes (`fork`/`exec`). Only threads.
- No reading of input files.

### Configurable variants

The trace length is the only setting that varies across the 2m / 10m / 30m traces. The other settings are fixed at the defaults below unless a run says otherwise; check `config` in that run's `ground_truth.json`.

| Option | Default | Meaning |
|---|---|---|
| `-d` | 2m | Trace length |
| `-w` | 4 | Number of workers |
| `-s` | 2 | Which worker owns the commit step / is affected |
| `-c` | `lock` | Source of the delay: `lock`, `sleep` or `cpu` (see Part 2) |
| `--work-ms` | 5 | CPU work per worker per round |
| `--slow-ms` | 40 | Length of the flusher's write-back (or the extra delay in other modes) |
| `--gap-ms` | 50 | Coordinator idle time between rounds |
| `--slow-every` | 1 | Only every K-th round is affected |
| `--cpu` | 3 | CPU every thread is pinned to |

---

## Part 2: Performance behaviour (answer key, do not expose)

### The problem the trace contains

Every round takes about **40–43 ms** instead of the about **20 ms** that the computation alone needs. The four workers sharing one CPU at 5 ms each account for about 20 ms.

- **The slow thread is `worker-2`.** The other workers finish by about 19–20 ms into each round. `worker-2` finishes at about 41 ms. Because the coordinator waits for every worker, `worker-2` sets the latency of every round.
- **`worker-2` is not slow because of CPU.** It does the same 5 ms of computation as the others. For the remaining time it is **BLOCKED on a futex** (`cache_lock`), on average about 25 ms per round.
- **The lock is held by `flusher`.** `flusher` holds the lock across its 40 ms write-back sleep. So the underlying wait is a timer.
- **Critical path of the coordinator** (`batch_server`) for one round:

  ```
  batch_server (blocked, futex) <- worker-2 (blocked on cache_lock, futex) <- flusher (sleeping, timer) <- timer interrupt
  ```

- **Why CPU-based views don't explain it:** `worker-2`'s CPU time per round is the same as the other workers' (about 5 ms). The difference is only visible as waiting time, and the reason for the wait is in another thread. That is why following the critical path is needed.

Measured on a 5-second test run (lock mode, defaults):

| Measure | Value |
|---|---|
| Rounds | 55 |
| Round latency, mean / p50 / max | 41.6 / 42.8 / 43.2 ms |
| Last of the other workers to finish (mean) | 19.4 ms into the round |
| `worker-2` finish (mean) | 41.6 ms into the round |
| `worker-2` wait on `cache_lock` (mean) | 24.8 ms |

The exact figures for each captured trace are in that run's `ground_truth.json` (`stats`). `rounds.csv` has one row per round, with timestamps on the same clock (CLOCK_MONOTONIC) as the LTTng kernel trace.

### How the other `--cause` modes change the answer

| `--cause` | Why `worker-2` is slow | Critical path | `flusher` |
|---|---|---|---|
| `lock` (default) | Blocked on `cache_lock` held by `flusher` | `batch_server <- worker-2 <- flusher <- timer` | Active every affected round |
| `sleep` | Sleeps 40 ms itself (`clock_nanosleep`) | `batch_server <- worker-2 <- timer` | Idle all run |
| `cpu` | Runs 40 ms of extra computation | `batch_server <- worker-2` (RUNNING) | Idle all run |

With `--slow-every K > 1`, only every K-th round is affected. The other rounds take about 20 ms, so the problem shows up as periodic latency spikes rather than a constant slowdown.
