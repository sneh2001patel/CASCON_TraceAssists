#!/usr/bin/env bash
#
# Captures an LTTng kernel trace of batch_server (see batch_server.c) for the
# CASCON experiment: one run per trace length (2m / 10m / 30m).
#
# Usage: ./capture.sh <duration> [extra batch_server options]
#   ./capture.sh 2m
#   ./capture.sh 10m
#   ./capture.sh 30m --cause sleep --slow-every 10
#
# Output: runs/<session>/trace/         the LTTng trace (give this to the LLM/RAG/framework)
#         runs/<session>/ground_truth/  ground_truth.json + rounds.csv (the answer key; never
#                                       give this to the systems under test)
#         runs/<session>/program.log    batch_server stdout
#
# Event set matches lttng_critical_path_demo/capture.sh, so Trace Compass can build the
# execution graph / critical path. Kernel channel buffers are enlarged because a 30-minute
# trace with all syscalls enabled otherwise risks discarded events.
#
# Requires: lttng-sessiond already running (sudo lttng-sessiond --daemonize), and the
# current user in the "tracing" group.

set -euo pipefail

if [ $# -lt 1 ]; then
    sed -n '5,9p' "$0" >&2
    exit 1
fi

DURATION="$1"
shift

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="${EXP_DIR}/batch_server"
SESSION_NAME="batch-server-${DURATION}-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${EXP_DIR}/runs/${SESSION_NAME}"
CHANNEL="cascon"

if [ ! -x "${BINARY}" ] || [ "${EXP_DIR}/batch_server.c" -nt "${BINARY}" ]; then
    echo "== Building batch_server =="
    gcc -O2 -pthread -Wall -Wextra -o "${BINARY}" "${EXP_DIR}/batch_server.c" -lm
fi

mkdir -p "${RUN_DIR}"

echo "== Creating LTTng session: ${SESSION_NAME} =="
lttng create "${SESSION_NAME}" --output="${RUN_DIR}/trace"

echo "== Creating kernel channel with larger buffers =="
lttng enable-channel --kernel --subbuf-size=8M --num-subbuf=8 "${CHANNEL}"

echo "== Enabling kernel scheduling/process events =="
lttng enable-event --kernel --channel="${CHANNEL}" \
    sched_switch,sched_process_fork,sched_process_exit,sched_process_free,sched_wakeup,sched_wakeup_new

# Needed so Trace Compass attributes timer wake-ups (the flusher's nanosleep) to a TIMER
# edge instead of the idle task (see lttng_critical_path_demo/capture.sh).
echo "== Enabling interrupt/timer context events =="
lttng enable-event --kernel --channel="${CHANNEL}" \
    timer_hrtimer_expire_entry,timer_hrtimer_expire_exit,irq_handler_entry,irq_handler_exit,irq_softirq_entry,irq_softirq_exit,irq_softirq_raise
lttng enable-event --kernel --channel="${CHANNEL}" \
    x86_irq_vectors_local_timer_entry,x86_irq_vectors_local_timer_exit,x86_irq_vectors_reschedule_entry,x86_irq_vectors_reschedule_exit,x86_irq_vectors_call_function_entry,x86_irq_vectors_call_function_exit,x86_irq_vectors_call_function_single_entry,x86_irq_vectors_call_function_single_exit \
    || echo "  (x86 IRQ vector events unavailable on this arch, continuing)"

echo "== Enabling kernel syscalls (needed for futex/mutex correlation) =="
lttng enable-event --kernel --channel="${CHANNEL}" --syscall --all

echo "== Enabling statedump events =="
lttng enable-event --kernel --channel="${CHANNEL}" \
    lttng_statedump_process_state,lttng_statedump_file_descriptor,lttng_statedump_block_device \
    || echo "  (statedump events unavailable/optional, continuing)"

echo "== Starting trace =="
lttng start

echo "== Running batch_server -d ${DURATION} $* =="
"${BINARY}" -d "${DURATION}" -o "${RUN_DIR}/ground_truth" "$@" | tee "${RUN_DIR}/program.log"

echo "== Stopping trace =="
lttng stop "${SESSION_NAME}"

echo "== Destroying session (trace data is kept on disk) =="
lttng destroy "${SESSION_NAME}"

echo
echo "== Done =="
echo "Trace:        ${RUN_DIR}/trace"
echo "Ground truth: ${RUN_DIR}/ground_truth"
du -sh "${RUN_DIR}/trace"
