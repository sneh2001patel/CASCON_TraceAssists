#!/usr/bin/env python3
"""
Retrieval step for the LLM + RAG baseline: for each experiment question, fetch
the documents the TMLL RAG layer (tmll/rag) retrieves, and freeze them in
retrieved_docs.json so every API call sees exactly the same documents.

Knowledge base: the 37 documents in tmll/rag/knowledge plus this baseline's own
knowledge/ folder (knowledge/workload/batch_server.md, a description of the
program). They are indexed into a separate Chroma store, rag_storage/, so the
framework's own index in tmll/rag/storage is not changed.

Mirrors the framework's retrieval (tmll_assistant_service_syscall_v1.py:
build_thread_rag_response) minus TMLL: the search query is the question alone
(no trace evidence), the 6 nearest documents are kept, and the top 3 get their
full source page fetched live (rag/source_fetcher.attach_source_text). Local
documents have no web page, so their full indexed text is used instead.

Run with the TMLL environment, which has chromadb and sentence-transformers,
from CASCON/Experiment/RAG_baseline:
    "../../../TraceCompass AI Agent/tmll/.venv/bin/python" retrieve_docs.py

Set TMLL_ROOT to use a TMLL checkout in a different location.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMLL_ROOT = Path(os.environ.get(
    "TMLL_ROOT", HERE.parents[2] / "TraceCompass AI Agent" / "tmll"
)).expanduser().resolve()
TMLL_KNOWLEDGE = TMLL_ROOT / "rag" / "knowledge"
LOCAL_KNOWLEDGE = HERE / "knowledge"
STORAGE = HERE / "rag_storage"
QUESTIONS_FILE = HERE / "questions.json"
OUT_FILE = HERE / "retrieved_docs.json"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--limit", type=int, default=6, help="documents per question (default 6)")
    p.add_argument("--no-fetch", action="store_true",
                   help="use only the indexed text, do not fetch full source pages")
    p.add_argument("--questions", nargs="+", metavar="ID",
                   help="retrieve only these questions (e.g. Q11 Q12 Q13) and add them to the\n"
                        "existing retrieved_docs.json, leaving every other question's frozen\n"
                        "documents untouched (default: retrieve all and rewrite the file)")
    args = p.parse_args()

    sys.path.insert(0, str(TMLL_ROOT))
    from rag.config import RAGConfig
    from rag.index_builder import KnowledgeIndex
    from rag.retriever import KnowledgeRetriever
    from rag.source_fetcher import attach_source_text

    config = RAGConfig(knowledge_directory=TMLL_KNOWLEDGE, storage_directory=STORAGE)
    index = KnowledgeIndex(config)
    count = index.build(TMLL_KNOWLEDGE, reset=True)
    count += index.build(LOCAL_KNOWLEDGE, reset=False)
    print(f"indexed {count} files ({index.collection.count()} distinct documents) "
          f"from {TMLL_KNOWLEDGE} + {LOCAL_KNOWLEDGE} into {STORAGE}")

    retriever = KnowledgeRetriever(index=index, config=config)
    questions = json.loads(QUESTIONS_FILE.read_text())
    out = {}
    if args.questions:
        # Add to the frozen file instead of rewriting it, so documents already used by
        # earlier runs (including live-fetched source pages) stay exactly as they were.
        out = json.loads(OUT_FILE.read_text()) if OUT_FILE.exists() else {}
        clash = [qid for qid in args.questions if qid in out]
        if clash:
            sys.exit(f"{clash} already in {OUT_FILE.name}; not overwriting frozen documents")
        questions = [q for q in questions if q["id"] in args.questions]
        missing = set(args.questions) - {q["id"] for q in questions}
        if missing:
            sys.exit(f"not in questions.json: {sorted(missing)}")
    for q in questions:
        documents = retriever.search(q["question"], limit=args.limit)
        if not args.no_fetch:
            documents = attach_source_text(documents)
        out[q["id"]] = {
            "question": q["question"],
            "query": q["question"],
            "documents": [
                {"rank": rank, "id": d.metadata.get("id"), "title": d.metadata.get("title"),
                 "domain": d.metadata.get("domain"), "source_url": d.metadata.get("source_url"),
                 "distance": round(d.distance, 4), "text": d.text,
                 "full_source_text": d.full_source_text}
                for rank, d in enumerate(documents, 1)
            ],
        }
        titles = ", ".join(f"{d.metadata.get('id')}{'*' if d.full_source_text else ''}"
                           for d in documents)
        print(f"{q['id']:<4} {titles}")

    OUT_FILE.write_text(json.dumps(out, indent=2) + "\n")
    print(f"-> {OUT_FILE}  (* = full source page fetched)")


if __name__ == "__main__":
    main()
