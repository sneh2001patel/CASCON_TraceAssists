/*
 * CASCON experiment workload: a multi-threaded batch server where one worker
 * thread is consistently slow, and the reason is only visible by following
 * the critical path (CPU usage alone does not explain it).
 *
 * Threads (names as they appear in the kernel trace's comm field):
 *  - batch_server (main, the coordinator): every round it hands one batch to
 *    every worker, then blocks until all of them report done. Round latency
 *    is therefore set by the slowest worker. Sleeps --gap-ms between rounds.
 *  - worker-0 .. worker-N-1: each does --work-ms of pure CPU work per round.
 *    worker-<slow> (default worker-2) also owns the "commit shard": after its
 *    CPU work it takes cache_lock to commit the round's results.
 *  - flusher: at the start of every slow round it takes cache_lock and holds
 *    it for --slow-ms while writing back the previous batch (a nanosleep
 *    standing in for slow disk I/O).
 *
 * Ground truth (default --cause lock): the coordinator's rounds take
 * ~--slow-ms instead of ~--work-ms because worker-<slow> is BLOCKED on
 * cache_lock (futex), which flusher holds across a timer sleep. Critical
 * path of the coordinator: batch_server <- worker-<slow> (futex) <- flusher
 * (futex) <- timer.
 *
 * Other causes, for variety (--cause):
 *  - sleep: worker-<slow> itself sleeps --slow-ms (blocked on a timer).
 *  - cpu:   worker-<slow> does --slow-ms of extra CPU work (running).
 *
 * Thread and binary names are deliberately neutral so the trace itself does
 * not give the answer away. The ground truth is written to -o DIR
 * (ground_truth.json + rounds.csv); keep that directory away from the
 * LLM/RAG inputs.
 *
 * All threads are pinned to one CPU (--cpu, -1 disables) for the same reason
 * as the reproducers in lttng_critical_path_demo/: cross-CPU futex wakes are
 * misattributed by Trace Compass's critical path
 * (critical_path_bug_report.md, "Finding 2").
 *
 * Build: gcc -O2 -pthread -Wall -Wextra -o batch_server batch_server.c -lm
 * Run:   ./batch_server -d 2m -o gt/     (also 10m, 30m, 90s, 1h, ...)
 *        ./batch_server -h               for every knob
 */
#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define MAX_WORKERS 64

enum cause { CAUSE_LOCK, CAUSE_SLEEP, CAUSE_CPU };
static const char *cause_names[] = { "lock", "sleep", "cpu" };

static struct {
    double duration_s;
    int workers;
    int slow_worker;
    int work_ms;
    int slow_ms;
    int gap_ms;
    int slow_every;
    int cpu;
    enum cause cause;
    const char *out_dir;
} cfg = {
    .duration_s = 120,
    .workers = 4,
    .slow_worker = 2,
    .work_ms = 5,
    .slow_ms = 40,
    .gap_ms = 50,
    .slow_every = 1,
    .cpu = 3,
    .cause = CAUSE_LOCK,
    .out_dir = ".",
};

struct round_rec {
    long round;
    int slow;
    int64_t start_ns;
    int64_t end_ns;
    double finish_ms[MAX_WORKERS];  /* worker finish time, relative to round start */
    double lock_wait_ms;            /* slow worker's wait on cache_lock (lock mode) */
};

static struct round_rec *rounds;
static long rounds_cap, rounds_done;

static sem_t start_sem[MAX_WORKERS];
static sem_t round_done;
static sem_t flush_go;
static atomic_int remaining;
static pthread_mutex_t cache_lock = PTHREAD_MUTEX_INITIALIZER;
static long cache_committed;

static volatile sig_atomic_t stop_requested;
static atomic_int shutting_down;

static pid_t tids[MAX_WORKERS + 2];  /* [0..workers-1] workers, [workers] flusher */
static unsigned long iters_per_ms;
static volatile unsigned long sink;

static int64_t now_ns(clockid_t clk) {
    struct timespec ts;
    clock_gettime(clk, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void sleep_ms(long ms) {
    struct timespec ts = { .tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L };
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
    }
}

/* Pure CPU work: no syscalls, so it shows up as RUNNING and nothing else. */
static void spin_iters(unsigned long n) {
    unsigned long acc = sink;
    for (unsigned long i = 0; i < n; i++) {
        acc = acc * 6364136223846793005UL + 1442695040888963407UL;
    }
    sink = acc;
}

static void spin_ms(long ms) {
    spin_iters(iters_per_ms * (unsigned long)ms);
}

static void calibrate(void) {
    unsigned long n = 100000;
    for (;;) {
        int64_t t0 = now_ns(CLOCK_MONOTONIC);
        spin_iters(n);
        int64_t dt = now_ns(CLOCK_MONOTONIC) - t0;
        if (dt > 50000000LL) {
            iters_per_ms = (unsigned long)((double)n * 1e6 / (double)dt);
            return;
        }
        n *= 2;
    }
}

static pid_t gettid_wrapper(void) {
    return (pid_t)syscall(SYS_gettid);
}

static void *worker(void *arg) {
    int id = (int)(intptr_t)arg;
    char name[16];
    snprintf(name, sizeof(name), "worker-%d", id);
    pthread_setname_np(pthread_self(), name);
    tids[id] = gettid_wrapper();

    for (;;) {
        sem_wait(&start_sem[id]);
        if (atomic_load(&shutting_down)) {
            break;
        }
        struct round_rec *r = &rounds[rounds_done];

        spin_ms(cfg.work_ms);

        if (id == cfg.slow_worker) {
            if (cfg.cause == CAUSE_LOCK) {
                int64_t t0 = now_ns(CLOCK_MONOTONIC);
                pthread_mutex_lock(&cache_lock);
                r->lock_wait_ms = (double)(now_ns(CLOCK_MONOTONIC) - t0) / 1e6;
                cache_committed++;
                pthread_mutex_unlock(&cache_lock);
            } else if (r->slow && cfg.cause == CAUSE_SLEEP) {
                sleep_ms(cfg.slow_ms);
            } else if (r->slow && cfg.cause == CAUSE_CPU) {
                spin_ms(cfg.slow_ms);
            }
        }

        r->finish_ms[id] = (double)(now_ns(CLOCK_MONOTONIC) - r->start_ns) / 1e6;
        if (atomic_fetch_sub(&remaining, 1) == 1) {
            sem_post(&round_done);
        }
    }
    return NULL;
}

static void *flusher(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "flusher");
    tids[cfg.workers] = gettid_wrapper();

    for (;;) {
        sem_wait(&flush_go);
        if (atomic_load(&shutting_down)) {
            break;
        }
        pthread_mutex_lock(&cache_lock);
        sleep_ms(cfg.slow_ms);  /* simulated write-back of the previous batch */
        pthread_mutex_unlock(&cache_lock);
    }
    return NULL;
}

static void on_signal(int sig) {
    (void)sig;
    stop_requested = 1;
}

static double parse_duration(const char *s) {
    char *end;
    double v = strtod(s, &end);
    if (end == s || v <= 0) {
        return -1;
    }
    if (*end == '\0' || strcmp(end, "s") == 0) {
        return v;
    }
    if (strcmp(end, "m") == 0) {
        return v * 60;
    }
    if (strcmp(end, "h") == 0) {
        return v * 3600;
    }
    return -1;
}

static void usage(const char *prog) {
    fprintf(stderr,
            "Usage: %s [options]\n"
            "  -d, --duration T     run time: 120, 90s, 2m, 10m, 30m, 1h (default 2m)\n"
            "  -w, --workers N      worker threads (default %d, max %d)\n"
            "  -s, --slow-worker I  index of the slow worker (default %d)\n"
            "  -c, --cause C        lock | sleep | cpu (default lock)\n"
            "      --work-ms MS     CPU work per worker per round (default %d)\n"
            "      --slow-ms MS     extra delay of the slow worker per slow round (default %d)\n"
            "      --gap-ms MS      coordinator idle time between rounds (default %d)\n"
            "      --slow-every K   only every K-th round is slow (default %d = every round)\n"
            "      --cpu N          pin all threads to CPU N, -1 = no pinning (default %d)\n"
            "  -o, --out DIR        where to write ground_truth.json + rounds.csv (default .)\n",
            prog, cfg.workers, MAX_WORKERS, cfg.slow_worker, cfg.work_ms, cfg.slow_ms,
            cfg.gap_ms, cfg.slow_every, cfg.cpu);
}

static int parse_args(int argc, char **argv) {
    enum { OPT_WORK = 1000, OPT_SLOW_MS, OPT_GAP, OPT_EVERY, OPT_CPU };
    static const struct option opts[] = {
        { "duration", required_argument, NULL, 'd' },
        { "workers", required_argument, NULL, 'w' },
        { "slow-worker", required_argument, NULL, 's' },
        { "cause", required_argument, NULL, 'c' },
        { "work-ms", required_argument, NULL, OPT_WORK },
        { "slow-ms", required_argument, NULL, OPT_SLOW_MS },
        { "gap-ms", required_argument, NULL, OPT_GAP },
        { "slow-every", required_argument, NULL, OPT_EVERY },
        { "cpu", required_argument, NULL, OPT_CPU },
        { "out", required_argument, NULL, 'o' },
        { "help", no_argument, NULL, 'h' },
        { 0, 0, 0, 0 },
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "d:w:s:c:o:h", opts, NULL)) != -1) {
        switch (opt) {
        case 'd':
            cfg.duration_s = parse_duration(optarg);
            if (cfg.duration_s <= 0) {
                fprintf(stderr, "bad duration: %s\n", optarg);
                return -1;
            }
            break;
        case 'w': cfg.workers = atoi(optarg); break;
        case 's': cfg.slow_worker = atoi(optarg); break;
        case 'c':
            if (strcmp(optarg, "lock") == 0) {
                cfg.cause = CAUSE_LOCK;
            } else if (strcmp(optarg, "sleep") == 0) {
                cfg.cause = CAUSE_SLEEP;
            } else if (strcmp(optarg, "cpu") == 0) {
                cfg.cause = CAUSE_CPU;
            } else {
                fprintf(stderr, "bad cause: %s\n", optarg);
                return -1;
            }
            break;
        case OPT_WORK: cfg.work_ms = atoi(optarg); break;
        case OPT_SLOW_MS: cfg.slow_ms = atoi(optarg); break;
        case OPT_GAP: cfg.gap_ms = atoi(optarg); break;
        case OPT_EVERY: cfg.slow_every = atoi(optarg); break;
        case OPT_CPU: cfg.cpu = atoi(optarg); break;
        case 'o': cfg.out_dir = optarg; break;
        default:
            usage(argv[0]);
            return -1;
        }
    }
    if (cfg.workers < 1 || cfg.workers > MAX_WORKERS || cfg.slow_worker < 0 ||
        cfg.slow_worker >= cfg.workers || cfg.work_ms < 0 || cfg.slow_ms < 0 ||
        cfg.gap_ms < 1 || cfg.slow_every < 1 || cfg.cpu >= sysconf(_SC_NPROCESSORS_ONLN)) {
        fprintf(stderr, "invalid option combination\n");
        usage(argv[0]);
        return -1;
    }
    return 0;
}

static int cmp_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

/* mean / p50 / p99 / max over rounds matching want_slow (-1 = all) */
struct stats { long n; double mean, p50, p99, max; };

static struct stats stats_of(double (*field)(const struct round_rec *), int want_slow) {
    struct stats s = { 0 };
    double *v = malloc(sizeof(double) * (size_t)(rounds_done ? rounds_done : 1));
    double sum = 0;
    for (long i = 0; i < rounds_done; i++) {
        if (want_slow != -1 && rounds[i].slow != want_slow) {
            continue;
        }
        v[s.n] = field(&rounds[i]);
        sum += v[s.n++];
    }
    if (s.n > 0) {
        qsort(v, (size_t)s.n, sizeof(double), cmp_double);
        s.mean = sum / (double)s.n;
        s.p50 = v[s.n / 2];
        s.p99 = v[(long)ceil(0.99 * (double)s.n) - 1];
        s.max = v[s.n - 1];
    }
    free(v);
    return s;
}

static double f_latency(const struct round_rec *r) {
    return (double)(r->end_ns - r->start_ns) / 1e6;
}
static double f_slow_finish(const struct round_rec *r) {
    return r->finish_ms[cfg.slow_worker];
}
static double f_other_finish(const struct round_rec *r) {
    double m = 0;
    for (int i = 0; i < cfg.workers; i++) {
        if (i != cfg.slow_worker && r->finish_ms[i] > m) {
            m = r->finish_ms[i];
        }
    }
    return m;
}
static double f_lock_wait(const struct round_rec *r) {
    return r->lock_wait_ms;
}

static void print_stats(FILE *f, const char *key, struct stats s, int last) {
    fprintf(f, "    \"%s\": {\"rounds\": %ld, \"mean_ms\": %.3f, \"p50_ms\": %.3f, "
               "\"p99_ms\": %.3f, \"max_ms\": %.3f}%s\n",
            key, s.n, s.mean, s.p50, s.p99, s.max, last ? "" : ",");
}

static const char *expected_answer(void) {
    switch (cfg.cause) {
    case CAUSE_LOCK:
        return "worker-%d is the slow thread. It is not CPU-bound: after its CPU work it blocks "
               "on a mutex (futex) held by the flusher thread, which holds it across a ~%d ms "
               "timer sleep (simulated disk write-back). Coordinator critical path: "
               "batch_server <- worker-%d (futex) <- flusher (futex) <- timer.";
    case CAUSE_SLEEP:
        return "worker-%d is the slow thread. It blocks on a ~%d ms timer sleep (nanosleep) "
               "every slow round. Coordinator critical path: batch_server <- worker-%d "
               "(futex) <- timer.";
    case CAUSE_CPU:
    default:
        return "worker-%d is the slow thread. It is CPU-bound: it runs ~%d ms of extra "
               "computation every slow round. Coordinator critical path: batch_server <- "
               "worker-%d (futex), worker mostly RUNNING.";
    }
}

static int write_ground_truth(pid_t pid, int64_t run_start_mono, int64_t run_end_mono,
                              int64_t realtime_minus_mono) {
    char path[4096];
    mkdir(cfg.out_dir, 0755);

    snprintf(path, sizeof(path), "%s/rounds.csv", cfg.out_dir);
    FILE *csv = fopen(path, "w");
    if (!csv) {
        perror(path);
        return -1;
    }
    fprintf(csv, "round,slow_round,start_mono_ns,end_mono_ns,latency_ms,slow_worker_finish_ms,"
                 "max_other_finish_ms,lock_wait_ms\n");
    for (long i = 0; i < rounds_done; i++) {
        struct round_rec *r = &rounds[i];
        fprintf(csv, "%ld,%d,%lld,%lld,%.3f,%.3f,%.3f,%.3f\n", r->round, r->slow,
                (long long)r->start_ns, (long long)r->end_ns, f_latency(r), f_slow_finish(r),
                f_other_finish(r), r->lock_wait_ms);
    }
    fclose(csv);

    snprintf(path, sizeof(path), "%s/ground_truth.json", cfg.out_dir);
    FILE *f = fopen(path, "w");
    if (!f) {
        perror(path);
        return -1;
    }
    char answer[1024];
    snprintf(answer, sizeof(answer), expected_answer(), cfg.slow_worker, cfg.slow_ms,
             cfg.slow_worker);

    fprintf(f, "{\n");
    fprintf(f, "  \"program\": \"batch_server\",\n");
    fprintf(f, "  \"config\": {\"duration_s\": %.1f, \"workers\": %d, \"slow_worker\": %d, "
               "\"cause\": \"%s\", \"work_ms\": %d, \"slow_ms\": %d, \"gap_ms\": %d, "
               "\"slow_every\": %d, \"pinned_cpu\": %d},\n",
            cfg.duration_s, cfg.workers, cfg.slow_worker, cause_names[cfg.cause], cfg.work_ms,
            cfg.slow_ms, cfg.gap_ms, cfg.slow_every, cfg.cpu);
    fprintf(f, "  \"pid\": %d,\n", pid);
    fprintf(f, "  \"threads\": [\n");
    fprintf(f, "    {\"name\": \"batch_server\", \"tid\": %d, \"role\": \"coordinator\"},\n", pid);
    for (int i = 0; i < cfg.workers; i++) {
        fprintf(f, "    {\"name\": \"worker-%d\", \"tid\": %d, \"role\": \"%s\"},\n", i, tids[i],
                i == cfg.slow_worker ? "worker (SLOW)" : "worker");
    }
    fprintf(f, "    {\"name\": \"flusher\", \"tid\": %d, \"role\": \"%s\"}\n", tids[cfg.workers],
            cfg.cause == CAUSE_LOCK ? "holds cache_lock during write-back (ROOT CAUSE)" : "idle");
    fprintf(f, "  ],\n");
    fprintf(f, "  \"slow_thread\": {\"name\": \"worker-%d\", \"tid\": %d},\n", cfg.slow_worker,
            tids[cfg.slow_worker]);
    fprintf(f, "  \"expected_answer\": \"%s\",\n", answer);
    fprintf(f, "  \"run_window\": {\"start_mono_ns\": %lld, \"end_mono_ns\": %lld, "
               "\"realtime_minus_mono_ns\": %lld},\n",
            (long long)run_start_mono, (long long)run_end_mono, (long long)realtime_minus_mono);
    fprintf(f, "  \"cache_commits\": %ld,\n", cache_committed);
    fprintf(f, "  \"stats\": {\n");
    print_stats(f, "round_latency_all", stats_of(f_latency, -1), 0);
    print_stats(f, "round_latency_slow_rounds", stats_of(f_latency, 1), 0);
    print_stats(f, "round_latency_normal_rounds", stats_of(f_latency, 0), 0);
    print_stats(f, "slow_worker_finish_slow_rounds", stats_of(f_slow_finish, 1), 0);
    print_stats(f, "other_workers_max_finish_slow_rounds", stats_of(f_other_finish, 1), 0);
    print_stats(f, "slow_worker_lock_wait_slow_rounds", stats_of(f_lock_wait, 1), 1);
    fprintf(f, "  }\n");
    fprintf(f, "}\n");
    fclose(f);
    return 0;
}

int main(int argc, char **argv) {
    if (parse_args(argc, argv) != 0) {
        return 2;
    }

    if (cfg.cpu >= 0) {
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(cfg.cpu, &set);
        if (sched_setaffinity(0, sizeof(set), &set) != 0) {
            perror("sched_setaffinity");
        }
    }

    struct sigaction sa = { .sa_handler = on_signal };
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Every round lasts at least gap_ms, so this bounds the round count; allocating
     * up front keeps malloc/brk syscalls out of the traced steady state. */
    rounds_cap = (long)(cfg.duration_s * 1000.0 / cfg.gap_ms) + 16;
    rounds = calloc((size_t)rounds_cap, sizeof(*rounds));
    if (!rounds) {
        perror("calloc");
        return 1;
    }

    calibrate();

    for (int i = 0; i < cfg.workers; i++) {
        sem_init(&start_sem[i], 0, 0);
    }
    sem_init(&round_done, 0, 0);
    sem_init(&flush_go, 0, 0);

    pthread_t worker_threads[MAX_WORKERS], flusher_thread;
    for (int i = 0; i < cfg.workers; i++) {
        pthread_create(&worker_threads[i], NULL, worker, (void *)(intptr_t)i);
    }
    pthread_create(&flusher_thread, NULL, flusher, NULL);
    sleep_ms(100);  /* let every thread publish its TID */

    pid_t pid = getpid();
    printf("batch_server pid=%d duration=%.0fs workers=%d slow=worker-%d cause=%s "
           "slow_ms=%d cpu=%d\n",
           pid, cfg.duration_s, cfg.workers, cfg.slow_worker, cause_names[cfg.cause],
           cfg.slow_ms, cfg.cpu);
    printf("  tids:");
    for (int i = 0; i < cfg.workers; i++) {
        printf(" worker-%d=%d", i, tids[i]);
    }
    printf(" flusher=%d\n", tids[cfg.workers]);
    fflush(stdout);

    int64_t realtime_minus_mono = now_ns(CLOCK_REALTIME) - now_ns(CLOCK_MONOTONIC);
    int64_t run_start = now_ns(CLOCK_MONOTONIC);
    int64_t deadline = run_start + (int64_t)(cfg.duration_s * 1e9);

    while (!stop_requested && rounds_done < rounds_cap && now_ns(CLOCK_MONOTONIC) < deadline) {
        struct round_rec *r = &rounds[rounds_done];
        r->round = rounds_done;
        r->slow = (rounds_done % cfg.slow_every) == 0;
        atomic_store(&remaining, cfg.workers);
        r->start_ns = now_ns(CLOCK_MONOTONIC);

        if (r->slow && cfg.cause == CAUSE_LOCK) {
            sem_post(&flush_go);
        }
        for (int i = 0; i < cfg.workers; i++) {
            sem_post(&start_sem[i]);
        }
        sem_wait(&round_done);
        r->end_ns = now_ns(CLOCK_MONOTONIC);
        rounds_done++;

        sleep_ms(cfg.gap_ms);
    }
    int64_t run_end = now_ns(CLOCK_MONOTONIC);

    atomic_store(&shutting_down, 1);
    for (int i = 0; i < cfg.workers; i++) {
        sem_post(&start_sem[i]);
    }
    sem_post(&flush_go);
    for (int i = 0; i < cfg.workers; i++) {
        pthread_join(worker_threads[i], NULL);
    }
    pthread_join(flusher_thread, NULL);

    if (write_ground_truth(pid, run_start, run_end, realtime_minus_mono) != 0) {
        return 1;
    }

    struct stats slow = stats_of(f_latency, 1), normal = stats_of(f_latency, 0);
    struct stats sw = stats_of(f_slow_finish, 1), ow = stats_of(f_other_finish, 1);
    printf("batch_server done: %ld rounds in %.1fs\n", rounds_done,
           (double)(run_end - run_start) / 1e9);
    printf("  round latency: slow rounds mean %.2f ms (n=%ld), normal rounds mean %.2f ms (n=%ld)\n",
           slow.mean, slow.n, normal.mean, normal.n);
    printf("  slow rounds: worker-%d finishes at %.2f ms, other workers by %.2f ms\n",
           cfg.slow_worker, sw.mean, ow.mean);
    printf("  ground truth: %s/ground_truth.json, %s/rounds.csv\n", cfg.out_dir, cfg.out_dir);
    return 0;
}
