#!/usr/bin/env python3
"""
LLM + RAG baseline for the CASCON experiment: the LLM-only baseline
(../llm_baseline) plus the documents the TMLL RAG layer (tmll/rag) retrieves
for each question. No TMLL trace analysis is used, so any difference from the
LLM-only results comes from the retrieved documents alone.

Each call contains: system prompt, trace slice, RETRIEVED KNOWLEDGE (in the
format of tmll/rag/prompt_builder.py), one question. Everything else matches
the LLM-only baseline:

  slices/              identical copies of ../llm_baseline/slices (A_head,
                       B_middle, C_sampled for the 2m, 10m and 30m traces)
  questions.json       identical copy of ../llm_baseline/questions.json
  retrieved_docs.json  documents per question, frozen by retrieve_docs.py so
                       every call sees exactly the same documents

Only the slices are sent, never ground_truth/ or program.log.

Setup:   python -m venv .venv && .venv/bin/pip install -r requirements.txt
         export OPENAI_API_KEY=...
         "../../../TraceCompass AI Agent/tmll/.venv/bin/python" retrieve_docs.py   # once; already done
Usage:   .venv/bin/python rag_baseline.py run --dry-run    # planned calls and token totals
         .venv/bin/python rag_baseline.py run --repeats 1  # append to results/answers.jsonl
         .venv/bin/python rag_baseline.py export --repeat 1
"""
import argparse
import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLICES_DIR = HERE / "slices"
RESULTS_DIR = HERE / "results"
ANSWERS_JSONL = RESULTS_DIR / "answers.jsonl"
QUESTIONS_FILE = HERE / "questions.json"
RETRIEVED_DOCS_FILE = HERE / "retrieved_docs.json"

SLICE_KINDS = ["A_head", "B_middle", "C_sampled"]

# The LLM-only system prompt, extended only to explain the RETRIEVED KNOWLEDGE section.
SYSTEM_PROMPT = (
    "You are an expert in Linux kernel tracing and performance analysis. The user message "
    "contains text from an LTTng kernel trace, printed by babeltrace2, then background "
    "documentation retrieved for the question (RETRIEVED KNOWLEDGE), then one question about the "
    "trace. Answer the question using only the trace text as the source of facts about this "
    "system; use the retrieved documentation only to interpret the trace. Be specific: name "
    "processes and threads (with their TIDs), CPUs, system calls and durations where relevant. "
    "If the trace text is not enough to answer with confidence, say so and explain what is "
    "missing, then give your best answer."
)


# --------------------------------------------------------------------------- inputs

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
        sys.exit(f"no slices found in {SLICES_DIR}")
    return slices


def load_questions(args):
    questions = json.loads(QUESTIONS_FILE.read_text())
    if args.questions:
        questions = [q for q in questions if q["id"] in args.questions]
    return questions


def load_retrieved_docs():
    if not RETRIEVED_DOCS_FILE.exists():
        sys.exit("retrieved_docs.json not found; run retrieve_docs.py with the TMLL environment")
    return json.loads(RETRIEVED_DOCS_FILE.read_text())


def knowledge_text(documents):
    """RETRIEVED KNOWLEDGE section, in the format of tmll/rag/prompt_builder.py."""
    sections = []
    for d in documents:
        body = d["full_source_text"] or d["text"]
        note = " (full source fetched)" if d["full_source_text"] else " (indexed summary only)"
        sections.append(f"[{d['rank']}] {d['title']}{note}\n{body}\nSource: {d['source_url']}")
    return "RETRIEVED KNOWLEDGE\n" + "\n\n".join(sections)


def done_keys(model):
    keys = set()
    if ANSWERS_JSONL.exists():
        for line in ANSWERS_JSONL.read_text().splitlines():
            r = json.loads(line)
            if r.get("model") == model and "error" not in r:
                keys.add((r["trace"], r["slice"], r["question_id"], r["repeat"]))
    return keys


# --------------------------------------------------------------------------- run

def ask(client, args, cache_key, text, knowledge, question):
    # The slice is its own content block ending in an explicit cache breakpoint, and every
    # call for the same slice shares a prompt_cache_key, so the ~200k-token slice is read
    # from the prompt cache. The per-question parts (knowledge, question) come after it.
    params = {
        "model": args.model,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": text,
             "prompt_cache_breakpoint": {"mode": "explicit"}},
            {"type": "input_text", "text": f"\n\n---\n{knowledge}\n\n---\nQuestion: {question}"},
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
    docs = load_retrieved_docs()
    missing = [q["id"] for q in questions if q["id"] not in docs]
    if missing:
        sys.exit(f"no retrieved documents for {missing}; rerun retrieve_docs.py")
    done = done_keys(args.model)
    plan = []
    for label, kind, txt, meta in slices:
        jobs = [(q, rep) for q in questions for rep in range(1, args.repeats + 1)
                if (label, kind, q["id"], rep) not in done]
        if jobs:
            plan.append((label, kind, txt, meta, jobs))

    n_calls = sum(len(p[4]) for p in plan)
    n_tokens = sum(p[3]["tokens"] * len(p[4]) for p in plan)
    print(f"{n_calls} calls to {args.model}, ~{n_tokens / 1e6:.1f}M input tokens of trace "
          f"plus ~4.5k tokens of retrieved knowledge each ({len(done)} answers already recorded)")
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
        retrieved = docs[q["id"]]["documents"]
        rec = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model": args.model, "trace": label, "slice": kind, "run": meta["run"],
               "slice_tokens": meta["tokens"], "slice_events": meta["events"],
               "question_id": q["id"], "question_type": q["type"], "question": q["question"],
               "repeat": rep, "reasoning_effort": args.reasoning_effort,
               "retrieved_doc_ids": [d["id"] for d in retrieved]}
        t0 = time.time()
        try:
            resp = ask(client, args, f"cascon-{label}-{kind}", text, knowledge_text(retrieved),
                       q["question"])
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

SLICE_LABELS = {"A_head": "A, start of trace", "B_middle": "B, middle window",
                "C_sampled": "C, sampled windows"}


def cmd_export(args):
    if not ANSWERS_JSONL.exists():
        sys.exit("no answers recorded yet")
    latest = {}
    for line in ANSWERS_JSONL.read_text().splitlines():
        r = json.loads(line)
        if "error" not in r and (args.repeat is None or r["repeat"] == args.repeat):
            latest[(r["model"], r["trace"], r["slice"], r["question_id"], r["repeat"])] = r
    rows = sorted(latest.values(), key=lambda r: (r["model"], int(r["trace"][:-1]), r["slice"],
                                                  int(r["question_id"][1:]), r["repeat"]))
    cols = ["model", "response_model", "trace", "slice", "question_id", "question_type",
            "question", "repeat", "answer", "status", "retrieved_doc_ids", "slice_tokens",
            "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "latency_s"]
    name = "answers.csv" if args.repeat is None else f"answers_repeat{args.repeat}.csv"
    out = RESULTS_DIR / name
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "retrieved_doc_ids": " ".join(r["retrieved_doc_ids"])})
    print(f"{len(rows)} answers -> {out}")

    md = out.with_suffix(".md")
    md.write_text(answers_markdown(rows, args.repeat))
    print(f"{len(rows)} answers -> {md}")


def answers_markdown(rows, repeat):
    """Answers grouped by question, so all traces/slices for one question sit together."""
    models = sorted({r.get("response_model") or r["model"] for r in rows})
    lines = [f"# LLM + RAG baseline answers ({', '.join(models)}"
             + (f", repeat {repeat})" if repeat else ")"), ""]
    by_q = {}
    for r in sorted(rows, key=lambda r: (int(r["question_id"][1:]), int(r["trace"][:-1]),
                                         r["slice"], r["repeat"])):
        by_q.setdefault(r["question_id"], []).append(r)
    for qid, qrows in by_q.items():
        lines += [f"## {qid} ({qrows[0]['question_type']}): {qrows[0]['question']}", "",
                  "Retrieved documents: "
                  + ", ".join(f"`{d}`" for d in qrows[0]["retrieved_doc_ids"]), ""]
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

    sp = sub.add_parser("run", help="ask every question against every slice")
    sp.add_argument("--traces", nargs="+", metavar="LABEL", help="e.g. 2m 10m (default: all)")
    sp.add_argument("--slices", nargs="+", choices=SLICE_KINDS, default=SLICE_KINDS)
    sp.add_argument("--model", default="gpt-5.6")
    sp.add_argument("--questions", nargs="+", metavar="ID", help="e.g. Q1 Q4 (default: all)")
    sp.add_argument("--repeats", type=int, default=1)
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

    sp = sub.add_parser("export", help="write results/answers.csv and .md")
    sp.add_argument("--repeat", type=int,
                    help="only this repeat, written to results/answers_repeat<N>.csv")
    sp.set_defaults(func=cmd_export)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
