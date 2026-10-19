#!/usr/bin/env python3
"""
Plot the paper's score-breakdown table (Table 1 in Paper/new_main.tex) as a
grouped horizontal bar chart: one panel per row group (slices, categories,
hub scope), one bar per system, x = percentage of the maximum points.

Each bar is labelled with points/maximum, so the differing maxima stay visible.

Usage:  python plot_breakdown.py            # writes figures/score_breakdown.{png,pdf}
        python plot_breakdown.py --column   # one-column paper version, also into Paper/figures/
        python plot_breakdown.py --show     # also opens a window
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "figures"

SYSTEMS = ["LLM", "RAG", "TraceAssist"]
# First three categorical slots of the validated default palette (blue, orange, aqua).
COLORS = {"LLM": "#2a78d6", "RAG": "#eb6834", "TraceAssist": "#1baf7a"}
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"

# (row label, {system: (points, maximum) or None})  -- values from the grade files.
GROUPS = [
    ("Trace slice (baselines only; TraceAssist reads the whole trace)", [
        ("Slice A (head)",    {"LLM": (1.5, 39),  "RAG": (11.5, 39), "TraceAssist": None}),
        ("Slice B (middle)",  {"LLM": (21.5, 39), "RAG": (35, 39),   "TraceAssist": None}),
        ("Slice C (sampled)", {"LLM": (18.5, 39), "RAG": (33.5, 39), "TraceAssist": None}),
    ]),
    ("Question category", [
        ("Comprehension", {"LLM": (12, 27),   "RAG": (17.5, 27), "TraceAssist": (9, 9)}),
        ("Root cause",    {"LLM": (7.5, 18),  "RAG": (13, 18),   "TraceAssist": (6, 6)}),
        ("Dependency",    {"LLM": (11.5, 36), "RAG": (27, 36),   "TraceAssist": (12, 12)}),
        ("Quantitative",  {"LLM": (4, 18),    "RAG": (7.5, 18),  "TraceAssist": (6, 6)}),
        ("Remediation",   {"LLM": (6.5, 18),  "RAG": (15, 18),   "TraceAssist": (6, 6)}),
    ]),
    ("Hub scope", [
        ("Single-hub", {"LLM": (26.5, 54), "RAG": (36.5, 54), "TraceAssist": (18, 18)}),
        ("Multi-hub",  {"LLM": (15, 63),   "RAG": (43.5, 63), "TraceAssist": (21, 21)}),
    ]),
]


def fmt(points):
    return f"{points:g}"


# Two layouts: the full-size preview, and a one-column version sized for the paper
# (3.4 in wide, set in fonts that stay readable at that size).
LAYOUTS = {
    "full":   dict(figsize=(7.0, 7.2), base=9, value=7.5, title=9.5, left=0.17, top=0.92,
                   bottom=0.07, hspace=0.35, xlim=125, surface=SURFACE,
                   slice_title="Trace slice (baselines only; TraceAssist reads the whole trace)"),
    "column": dict(figsize=(3.4, 5.4), base=7, value=5.4, title=7, left=0.25, top=0.925,
                   bottom=0.075, hspace=0.55, xlim=138, surface="#ffffff",
                   slice_title="Trace slice (baselines only)"),
}
PAPER_FIGURES = HERE.parent / "Paper" / "figures"
# Shorter row labels for the column version (the panel title already says "Trace slice").
SHORT_LABELS = {"Slice A (head)": "A (head)", "Slice B (middle)": "B (middle)",
                "Slice C (sampled)": "C (sampled)"}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--column", action="store_true",
                   help="one-column paper version: figures/score_breakdown_column.{png,pdf},\n"
                        "with the PDF also copied to Paper/figures/")
    p.add_argument("--show", action="store_true", help="open an interactive window too")
    args = p.parse_args()
    L = LAYOUTS["column" if args.column else "full"]
    surface = L["surface"]

    plt.rcParams.update({"font.size": L["base"], "font.family": "sans-serif",
                         "axes.edgecolor": AXIS, "axes.labelcolor": INK_2,
                         "xtick.color": MUTED, "ytick.color": INK})

    heights = [len(rows) for _, rows in GROUPS]
    fig, axes = plt.subplots(len(GROUPS), 1, figsize=L["figsize"], sharex=True,
                             gridspec_kw={"height_ratios": heights, "hspace": L["hspace"]})
    fig.patch.set_facecolor(surface)

    bar_h = 0.26

    for g, (ax, (title, rows)) in enumerate(zip(axes, GROUPS)):
        ax.set_facecolor(surface)
        for i, (_, values) in enumerate(rows):
            present = [s for s in SYSTEMS if values[s] is not None]
            for k, system in enumerate(present):
                # Centre whichever bars the row has around the row's tick.
                y = i + (k - (len(present) - 1) / 2) * bar_h
                points, maximum = values[system]
                pct = 100 * points / maximum
                # Thin bar, with a surface-coloured edge acting as the gap between adjacent bars.
                ax.barh(y, pct, height=bar_h, color=COLORS[system],
                        edgecolor=surface, linewidth=1.5 if not args.column else 0.8)
                ax.text(pct + 1, y, f"{fmt(points)}/{maximum} ({pct:.0f}%)", va="center",
                        ha="left", fontsize=L["value"], color=INK_2)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([SHORT_LABELS.get(label, label) if args.column else label for label, _ in rows])
        ax.invert_yaxis()
        ax.set_xlim(0, L["xlim"])
        ax.set_title(L["slice_title"] if g == 0 else title, loc="left",
                     fontsize=L["title"], color=INK, fontweight="bold")
        ax.grid(axis="x", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="y", length=0)
        # Shared x axes hide the tick labels on all but the bottom panel; show them on every panel.
        ax.tick_params(axis="x", labelbottom=True)

    axes[-1].set_xticks(range(0, 101, 20))
    axes[-1].set_xlabel("Points scored, % of maximum")

    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[s]) for s in SYSTEMS]
    fig.legend(handles, ["LLM only", "LLM + RAG", "TraceAssist"], loc="upper center",
               ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.998), fontsize=L["base"],
               handlelength=1.2, columnspacing=1.2)
    fig.subplots_adjust(top=L["top"], left=L["left"], right=0.98, bottom=L["bottom"])

    OUT_DIR.mkdir(exist_ok=True)
    name = "score_breakdown_column" if args.column else "score_breakdown"
    for ext in ("png", "pdf"):
        out = OUT_DIR / f"{name}.{ext}"
        fig.savefig(out, dpi=300 if args.column else 200, facecolor=surface)
        print(f"-> {out}")
    if args.column:
        out = PAPER_FIGURES / f"{name}.pdf"
        fig.savefig(out, facecolor=surface)
        print(f"-> {out}")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
