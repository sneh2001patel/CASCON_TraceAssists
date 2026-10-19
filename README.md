# CASCON

Paper and experiment materials for the CASCON research project.

- `Paper/`: LaTeX paper sources, references, and figures.
- `Experiment/`: workload source, capture scripts, baseline implementations,
  experiment results, and ground-truth measurements.

Raw traces under `Experiment/runs/**/trace/`, Python environments, generated
trace slices, retrieval indexes, compiled executables, and LaTeX auxiliary files
are kept locally and excluded from Git. Paper and figure PDFs are included.

The RAG retrieval script uses the TMLL checkout in the sibling repository at
`../TraceCompass AI Agent/tmll` by default. Set the `TMLL_ROOT` environment
variable to use another checkout. Install each baseline's dependencies from
its `requirements.txt` into a local Python environment.
