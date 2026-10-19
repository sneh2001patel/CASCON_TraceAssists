---
id: workload-batch-server-001
title: batch_server program description
domain: workload
concept: batch_server
trace_type: lttng_kernel
source_url: local://batch_server/program_description
source_title: batch_server developer documentation
---

# What the `batch_server` program does

This document describes the `batch_server` program: what it is, what its threads do, and what
its activity looks like in an LTTng kernel trace.

## Summary

`batch_server` is a multi-threaded **batch-processing server** running on Linux for a fixed length
of time (2, 10 or 30 minutes). The program runs in repeated **rounds**, about 11 per second. In
each round:

1. A coordinator thread hands one batch of work to each of 4 worker threads and waits for all of
   them to finish.
2. Each worker does a short burst of computation.
3. One worker also commits its results to a shared in-memory cache that is protected by a lock.
4. A background `flusher` thread writes the cache back to storage. In the program this write is
   simulated with a sleep while holding the cache lock.

When every worker has finished, the round ends. The coordinator then idles for 50 ms and starts
the next round. All of the program's threads run on a single CPU (CPU 3).

## Threads

The names are the `comm` / thread names you see in the trace.

| Thread | Count | Role | Activity per round |
|---|---|---|---|
| `batch_server` | 1 | Coordinator (main thread, PID = its TID) | Starts the round, wakes the flusher and all workers, blocks until the last worker reports done, then sleeps 50 ms |
| `worker-0`, `worker-1`, `worker-3` | 3 | Compute workers | Wait for a batch, spin on the CPU for about 5 ms, report done |
| `worker-2` | 1 | Compute worker that also owns the commit step | Waits for a batch, spins about 5 ms, takes `cache_lock`, commits the round's results, releases the lock, reports done |
| `flusher` | 1 | Background cache write-back | Waits to be told to flush, takes `cache_lock`, writes back the previous batch (a 40 ms sleep standing in for disk I/O), releases the lock |

## Lifecycle of one run

1. **Startup** (well under a second)
   - The process starts and pins itself to CPU 3; all threads it creates inherit this.
   - It calibrates a CPU busy-loop (about 50–100 ms of pure computation on the main thread).
   - It creates 4 workers and 1 flusher (`clone3`, thread stacks via `mmap`), then waits 100 ms.
   - It prints its PID and thread IDs to stdout.
2. **Steady state** (the requested duration). Rounds repeat as described above.
   - Expected round counts: about 1,300 for 2 minutes, about 6,600 for 10 minutes, about 19,800
     for 30 minutes.
   - All round bookkeeping goes to memory allocated up front, so this phase makes no file I/O,
     network, `brk` or `mmap` calls.
3. **Shutdown**
   - The coordinator tells every thread to exit and joins them.
   - It writes two result files to disk, a JSON summary and a CSV of per-round timings (`mkdir`,
     `openat`, `write`, `close`).
   - It prints a short summary and exits.

## What this looks like in the kernel trace

**Scheduling events.** `sched_switch` / `sched_wakeup` between the 6 threads, all on CPU 3, in a
regular pattern every ~90 ms:
- The coordinator wakes, wakes the flusher and the workers, then blocks.
- Workers run one after another on the shared CPU.
- The coordinator is woken at the end of the round, then sleeps.

**System calls:**
- `futex` (wait/wake) from the semaphores that start and finish each round, and from `cache_lock`
  when it is contended.
- `clock_nanosleep` from the coordinator's 50 ms gap and the flusher's simulated write-back.
- Almost nothing else during the steady state. The CPU work is a pure computation loop with no
  system calls, so it appears only as time in the RUNNING state.

**Timer / interrupt events.** hrtimer expiries waking the sleeping threads.

**Other processes.** The trace is system-wide, so it also contains everything else on the machine
(desktop, daemons, the LTTng session daemon, kernel threads). `batch_server` and its threads are
the workload under study.

## What the program does *not* do

- No network activity (no sockets).
- No real disk I/O during the steady state. The "write-back" is a sleep, so there are no
  block-device requests from this program until it writes its two result files at exit.
- No child processes (`fork`/`exec`). Only threads.
- No reading of input files.
