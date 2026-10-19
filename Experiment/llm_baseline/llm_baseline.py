#!/usr/bin/env python3
"""
LLM-only baseline for the CASCON experiment: give the model raw LTTng trace
text (no tools of any kind) and ask it the 10 experiment questions.

Each trace is far larger than any context window (~300M tokens for the
2-minute trace), so it is cut into slices of at most --budget tokens:

  A_head     the first N tokens of the trace -- literally "dump the trace in"
  B_middle   N tokens of consecutive events starting at the trace's midpoint
  C_sampled  K short windows spread evenly over the whole trace, N/K tokens each

Every question is a separate API call (system prompt, slice, one question), so
answers cannot leak between questions. The slice comes first in the prompt so
OpenAI's prompt caching can reuse it across the questions of the same slice.

Only each run's trace/ directory is read. ground_truth/ and program.log are
never opened, so nothing about the expected answers reaches the model.

Setup:   python -m venv .venv && .venv/bin/pip install -r requirements.txt
         export OPENAI_API_KEY=...
Usage:   .venv/bin/python llm_baseline.py prepare          # build slices (needs babeltrace2)
         .venv/bin/python llm_baseline.py run --dry-run    # planned calls and token totals
         .venv/bin/python llm_baseline.py run              # append to results/answers.jsonl
         .venv/bin/python llm_baseline.py export           # results/answers.csv for grading
"""
import argparse
import csv
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE.parent / "runs" / "runs_to_use"
SLICES_DIR = HERE / "slices"
RESULTS_DIR = HERE / "results"
ANSWERS_JSONL = RESULTS_DIR / "answers.jsonl"
QUESTIONS_FILE = HERE / "questions.json"

SLICE_KINDS = ["A_head", "B_middle", "C_sampled"]

SYSTEM_PROMPT = (
    "You are an expert in Linux kernel tracing and performance analysis. The user message "
    "contains text from an LTTng kernel trace, printed by babeltrace2, followed by one question "
    "about it. Answer the question using only the trace text provided. Be specific: name "
    "processes and threads (with their TIDs), CPUs, system calls and durations where relevant. "
    "If the trace text is not enough to answer with confidence, say so and explain what is "
    "missing, then give your best answer."
)


# --------------------------------------------------------------------------- traces

def find_traces():
    """[(label, run_dir, kernel_dir)] for every run in runs/runs_to_use, shortest first."""
    traces = []
    for run in sorted(RUNS_DIR.iterdir()):
        m = re.search(r"-(\d+)m-", run.name)
        kernel = run / "trace" / "kernel"
        if m and kernel.is_dir():
            traces.append((int(m.group(1)), f"{m.group(1)}m", run, kernel))
    return [(label, run, kernel) for _, label, run, kernel in sorted(traces)]


def trace_range_ns(kernel):
    """(begin, end) of the trace in ns since the epoch, from babeltrace2's packet index."""
    out = subprocess.run(
        ["babeltrace2", "query", "src.ctf.fs", "babeltrace.trace-infos",
         "--params", f"inputs=[{json.dumps(str(kernel))}]"],
        capture_output=True, text=True, check=True).stdout
    pairs = re.findall(r"range-ns:\s*\n\s*begin:\s*(\d+)\s*\n\s*end:\s*(\d+)", out)
    if not pairs:
        sys.exit(f"could not read the time range of {kernel}")
    return min(int(b) for b, _ in pairs), max(int(e) for _, e in pairs)


def read_events(kernel, enc, max_tokens, start_ns=None):
    """Consecutive babeltrace2 text lines from start_ns (or the trace start) up to max_tokens."""
    cmd = ["babeltrace2"]
    if start_ns is not None:
        cmd.append(f"--begin={start_ns // 10**9}.{start_ns % 10**9:09d}")
    cmd.append(str(kernel))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, errors="replace")
    lines, tokens = [], 0
    try:
        for line in proc.stdout:
            n = len(enc.encode_ordinary(line))
            if tokens + n > max_tokens:
                break
            lines.append(line)
            tokens += n
    finally:
        proc.kill()
        proc.wait()
    return lines, tokens


def ts_of(line):
    """The [HH:MM:SS.nnnnnnnnn] timestamp at the start of a babeltrace2 line."""
    return line[1:19] if line.startswith("[") else "?"


def local_time(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%H:%M:%S")


# --------------------------------------------------------------------------- prepare

def build_slice(kind, label, kernel, enc, budget, windows):
    begin, end = trace_range_ns(kernel)
    span_s = (end - begin) / 1e9
    intro = (f"LTTng kernel trace, babeltrace2 text output. The full trace spans {span_s:.1f} s "
             f"({local_time(begin)} to {local_time(end)}) and contains far more events than fit "
             f"here.")
    meta = {"trace": label, "slice": kind, "budget_tokens": budget, "encoding": enc.name,
            "trace_begin_ns": begin, "trace_end_ns": end, "trace_span_s": round(span_s, 3)}

    if kind in ("A_head", "B_middle"):
        start = None if kind == "A_head" else begin + (end - begin) // 2
        lines, tokens = read_events(kernel, enc, budget, start)
        where = ("the very beginning of the trace" if kind == "A_head"
                 else "consecutive events starting at the midpoint of the trace")
        header = (f"{intro} Shown below: {len(lines)} events ({ts_of(lines[0])} to "
                  f"{ts_of(lines[-1])}), {where}.")
        body = "".join(lines)
        meta.update(events=len(lines), first_ts=ts_of(lines[0]), last_ts=ts_of(lines[-1]))
    else:
        starts = [begin + (2 * i + 1) * (end - begin) // (2 * windows) for i in range(windows)]
        per_window = budget // windows
        with ThreadPoolExecutor(max_workers=8) as pool:
            parts = list(pool.map(lambda s: read_events(kernel, enc, per_window, s), starts))
        chunks, wmeta, tokens = [], [], 0
        for i, (lines, n) in enumerate(parts, 1):
            chunks.append(f"=== window {i}/{windows}: {len(lines)} events, "
                          f"{ts_of(lines[0])} to {ts_of(lines[-1])} ===\n" + "".join(lines))
            wmeta.append({"window": i, "events": len(lines), "first_ts": ts_of(lines[0]),
                          "last_ts": ts_of(lines[-1])})
            tokens += n
        header = (f"{intro} Shown below: {windows} short windows of consecutive events, spaced "
                  f"evenly across the whole trace. Events between windows are omitted.")
        body = "".join(chunks)
        meta.update(events=sum(w["events"] for w in wmeta), windows=wmeta)

    text = f"{header}\n\n{body}"
    meta["tokens"] = len(enc.encode_ordinary(text))
    return text, meta


def cmd_prepare(args):
    import tiktoken

    enc = tiktoken.get_encoding(args.encoding)
    for label, run, kernel in find_traces():
        if args.traces and label not in args.traces:
            continue
        for kind in args.slices:
            out_dir = SLICES_DIR / label
            out_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.time()
            text, meta = build_slice(kind, label, kernel, enc, args.budget, args.windows)
            meta["run"] = run.name
            (out_dir / f"{kind}.txt").write_text(text)
            (out_dir / f"{kind}.json").write_text(json.dumps(meta, indent=2) + "\n")
            print(f"{label:>4} {kind:<10} {meta['events']:>7} events {meta['tokens']:>8} tokens "
                  f"({time.time() - t0:.0f}s)")


# --------------------------------------------------------------------------- run

def load_slices(args):
    slices = []
    for label_dir in sorted(SLICES_DIR.iterdir()) if SLICES_DIR.is_dir() else []:
        if args.traces and label_dir.name not in args.traces:
            continue
        for kind in args.slices:
            txt = label_dir / f"{kind}.txt"
            if txt.exists():
                meta = json.loads((label_dir / f"{kind}.json").read_text())
                slices.append((label_dir.name, kind, txt, meta))
    if not slices:
        sys.exit("no slices found; run `prepare` first")
    return slices


def load_questions(args):
    questions = json.loads(QUESTIONS_FILE.read_text())
    if args.questions:
        questions = [q for q in questions if q["id"] in args.questions]
    return questions


def done_keys(model):
    keys = set()
    if ANSWERS_JSONL.exists():
        for line in ANSWERS_JSONL.read_text().splitlines():
            r = json.loads(line)
            if r.get("model") == model and "error" not in r:
                keys.add((r["trace"], r["slice"], r["question_id"], r["repeat"]))
    return keys


def ask(client, args, cache_key, text, question):
    # The slice is its own content block ending in an explicit cache breakpoint, and every
    # call for the same slice shares a prompt_cache_key, so the ~200k-token slice is read
    # from the prompt cache instead of being billed in full on every question.
    params = {
        "model": args.model,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": text,
             "prompt_cache_breakpoint": {"mode": "explicit"}},
            {"type": "input_text", "text": f"\n\n---\nQuestion: {question}"},
        ]}],
        "prompt_cache_key": cache_key,
        "max_output_tokens": args.max_output_tokens,
        "store": False,
    }
    if args.reasoning_effort:
        params["reasoning"] = {"effort": args.reasoning_effort}
    return client.responses.create(**params)


def cmd_run(args):
    slices, questions = load_slices(args), load_questions(args)
    done = done_keys(args.model)
    plan = []
    for label, kind, txt, meta in slices:
        jobs = [(q, rep) for q in questions for rep in range(1, args.repeats + 1)
                if (label, kind, q["id"], rep) not in done]
        if jobs:
            plan.append((label, kind, txt, meta, jobs))

    n_calls = sum(len(p[4]) for p in plan)
    n_tokens = sum(p[3]["tokens"] * len(p[4]) for p in plan)
    print(f"{n_calls} calls to {args.model}, ~{n_tokens / 1e6:.1f}M input tokens "
          f"({len(done)} answers already recorded)")
    for label, kind, _, meta, jobs in plan:
        print(f"  {label:>4} {kind:<10} {meta['tokens']:>8} tokens x {len(jobs)} calls")
    if args.dry_run or not plan:
        return

    from openai import OpenAI

    client = OpenAI(max_retries=args.max_retries, timeout=args.timeout)
    RESULTS_DIR.mkdir(exist_ok=True)
    lock = threading.Lock()
    out_of_credit = threading.Event()

    def one(label, kind, text, meta, q, rep):
        if out_of_credit.is_set():
            return
        rec = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model": args.model, "trace": label, "slice": kind, "run": meta["run"],
               "slice_tokens": meta["tokens"], "slice_events": meta["events"],
               "question_id": q["id"], "question_type": q["type"], "question": q["question"],
               "repeat": rep, "reasoning_effort": args.reasoning_effort}
        t0 = time.time()
        try:
            resp = ask(client, args, f"cascon-{label}-{kind}", text, q["question"])
            usage = resp.usage
            rec.update(
                answer=resp.output_text, status=resp.status, response_model=resp.model,
                incomplete_reason=getattr(resp.incomplete_details, "reason", None),
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cached_tokens=getattr(usage.input_tokens_details, "cached_tokens", None),
                reasoning_tokens=getattr(usage.output_tokens_details, "reasoning_tokens", None))
        except Exception as e:  # recorded and retried on the next `run`
            rec["error"] = f"{type(e).__name__}: {e}"
            if "insufficient_quota" in str(e):
                out_of_credit.set()
        rec["latency_s"] = round(time.time() - t0, 1)
        with lock:
            with ANSWERS_JSONL.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            status = rec.get("error", rec.get("status"))
            print(f"  {label:>4} {kind:<10} {q['id']:<4} rep {rep}: {status} "
                  f"({rec['latency_s']}s, cached {rec.get('cached_tokens')})", flush=True)

    for label, kind, txt, meta, jobs in plan:
        text = txt.read_text()
        # First call alone so the slice is in the prompt cache before the rest go out in parallel.
        one(label, kind, text, meta, *jobs[0])
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for q, rep in jobs[1:]:
                pool.submit(one, label, kind, text, meta, q, rep)
        if out_of_credit.is_set():
            sys.exit("Stopped: the API account is out of credit. Add credit, then run the same "
                     "command again to continue where it left off.")


# --------------------------------------------------------------------------- export

def cmd_export(args):
    if not ANSWERS_JSONL.exists():
        sys.exit("no answers recorded yet")
    latest = {}
    for line in ANSWERS_JSONL.read_text().splitlines():
        r = json.loads(line)
        if "error" not in r and (args.repeat is None or r["repeat"] == args.repeat):
            latest[(r["model"], r["trace"], r["slice"], r["question_id"], r["repeat"])] = r
    order = {f"Q{i}": i for i in range(1, 100)}
    rows = sorted(latest.values(), key=lambda r: (r["model"], int(r["trace"][:-1]), r["slice"],
                                                  order.get(r["question_id"], 0), r["repeat"]))
    cols = ["model", "response_model", "trace", "slice", "question_id", "question_type",
            "question", "repeat", "answer", "status", "slice_tokens", "input_tokens",
            "cached_tokens", "output_tokens", "reasoning_tokens", "latency_s"]
    name = "answers.csv" if args.repeat is None else f"answers_repeat{args.repeat}.csv"
    out = RESULTS_DIR / name
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} answers -> {out}")

    md = out.with_suffix(".md")
    md.write_text(answers_markdown(rows, args.repeat))
    print(f"{len(rows)} answers -> {md}")


SLICE_LABELS = {"A_head": "A, start of trace", "B_middle": "B, middle window",
                "C_sampled": "C, sampled windows"}


def answers_markdown(rows, repeat):
    """Answers grouped by question, so all traces/slices for one question sit together."""
    models = sorted({r.get("response_model") or r["model"] for r in rows})
    lines = [f"# LLM-only baseline answers ({', '.join(models)}"
             + (f", repeat {repeat})" if repeat else ")"), ""]
    by_q = {}
    for r in sorted(rows, key=lambda r: (int(r["question_id"][1:]), int(r["trace"][:-1]),
                                         r["slice"], r["repeat"])):
        by_q.setdefault(r["question_id"], []).append(r)
    for qid, qrows in by_q.items():
        lines += [f"## {qid} ({qrows[0]['question_type']}): {qrows[0]['question']}", ""]
        for r in qrows:
            rep = "" if repeat else f", repeat {r['repeat']}"
            lines += [f"### {qid} · {r['trace']} trace · {SLICE_LABELS[r['slice']]}{rep}", "",
                      f"*{r['input_tokens']:,} input tokens ({r['cached_tokens'] or 0:,} cached), "
                      f"{r['output_tokens']:,} output tokens, {r['latency_s']} s*", ""]
            # Quote the answer so its own headings and lists stay nested under this one.
            lines += [f"> {line}" if line.strip() else ">" for line in r["answer"].splitlines()]
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--traces", nargs="+", metavar="LABEL", help="e.g. 2m 10m (default: all)")
        sp.add_argument("--slices", nargs="+", choices=SLICE_KINDS, default=SLICE_KINDS)

    sp = sub.add_parser("prepare", help="cut each trace into token-budgeted slices")
    common(sp)
    sp.add_argument("--budget", type=int, default=200_000,
                    help="max trace tokens per slice; keep below the model's context window "
                         "minus room for the answer (default 200000)")
    sp.add_argument("--windows", type=int, default=20, help="windows in C_sampled (default 20)")
    sp.add_argument("--encoding", default="o200k_base", help="tiktoken encoding (default o200k_base)")
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("run", help="ask every question against every slice")
    common(sp)
    sp.add_argument("--model", default="gpt-5.6")
    sp.add_argument("--questions", nargs="+", metavar="ID", help="e.g. Q1 Q4 (default: all)")
    sp.add_argument("--repeats", type=int, default=3)
    sp.add_argument("--reasoning-effort", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                    help="default: the model's own default")
    sp.add_argument("--max-output-tokens", type=int, default=32_000,
                    help="includes reasoning tokens (default 32000)")
    sp.add_argument("--workers", type=int, default=2,
                    help="parallel calls per slice; each call is ~200k tokens and cached tokens "
                         "count toward the tokens-per-minute limit too (default 2)")
    sp.add_argument("--timeout", type=float, default=900, help="seconds per call (default 900)")
    sp.add_argument("--max-retries", type=int, default=5)
    sp.add_argument("--dry-run", action="store_true", help="only print what would be sent")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("export", help="write results/answers.csv")
    sp.add_argument("--repeat", type=int,
                    help="only this repeat, written to results/answers_repeat<N>.csv")
    sp.set_defaults(func=cmd_export)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
