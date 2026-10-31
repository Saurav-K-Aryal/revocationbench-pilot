# RevocationBench pilot paper

This repository reports a small local SQLite concurrency pilot, not a completed agent benchmark or a distributed authorization guarantee.

Repository: https://github.com/Saurav-K-Aryal/revocationbench-pilot.

## Manuscript

Download and extract [`overleaf.zip`](overleaf.zip). Upload its `draft.tex`, `refs.bib`, and `figures/` to Overleaf, select `draft.tex` as the main file, and use pdfLaTeX with BibTeX. The document uses `acmart` in `manuscript,screen,nonacm` mode. Inside the ZIP, `references.bib` is an identical convenience copy; the manuscript reads `refs.bib` only. Do not cite both copies.

The sections are Abstract, Introduction and Background, Methods, Results, Future Work, and Conclusion. The manuscript identifies AI assistance and requires author review before submission. The future work distinguishes actual experiments from proposed real-agent, Cedar, and distributed-fault integrations.

## Reproduce the observed pilot

The repository includes the unchanged experiment module, its `pilots/common.py` dependency, and a root `run_experiment.py` wrapper. From that repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python3 run_experiment.py --out results/newrun --profile pilot --seed 2026
```

The experiment itself uses the Python standard library: SQLite, threads, CSV, and JSON. It makes zero model calls. A new run creates 20 related timing replicates of each of four templates for each controller, plus one external-response-loss control per controller. Timing and worker order can vary; compare effect invariants and authorized completion, not byte-identical timestamps or latency.

The paper's observed CSV/JSON are in `data/`. Published copies normalize workspace paths; `PUBLICATION_PROVENANCE.json` records source and public hashes. The observed run is also retained as `results/observed/`. The source project run was `runs/unattended-20261031-130310-7SNZcu/offline/02/`; it includes the underlying SQLite files. These paths are provenance labels, not additional experiments.

## Regenerate the figures

Only figure generation needs Pillow. With an environment containing it:

```sh
python3 generate_figures.py
# Or, from the curated repository with its observed result tables:
python3 generate_figures.py --run results/observed --out figures
```

The script derives every chart value from raw tables, verifies aggregate counts against `result.json`, and records input SHA-256 digests in `figures/derived_summary.json`. It creates two PNG figures. It does not rerun or alter an experiment and does not infer population confidence intervals from authored stress schedules.

## Observed result and boundary

The gateway has zero invariant violations in 80 local schedules and matches the expected authorized effect count in all 80. The deliberately weak cached-preflight baseline violates a tested invariant in 60/80 and matches authorized effects in 20/80. Both controllers complete both legitimate actions in all 20 within-budget controls.

The external endpoint control gives two committed effects after naive retry. The gateway gives one external effect, leaves one unknown operation charged 40 units, and suppresses retry. Neither controller confirms task completion. This control does not establish exactly-once effects at arbitrary external services.

All results depend on authored forced schedules, trusted complete mediation, and a single local transaction boundary for local effects. There are no LLM agents, Cedar policy engine, distributed replicas, network faults, or production workloads in this study. The weak baseline is an integration control rather than a state-of-the-art comparator. The related replicates are not independent evidence of a deployment failure rate.

## References and provenance

Five primary sources were verified on October 31, 2026: AgentDojo and Progent on arXiv; Cedar on Amazon Science and the official SPLASH/OOPSLA program; official SQLite isolation documentation; and IETF's experimental bounded-capability-receipts draft-06. The Internet-Draft is explicitly work in progress, not a standard. The observed run was independently read back from all 160 local-trial SQLite databases and both external controls before publication; the standalone public runner was rerun and matched all effect-count metrics.

## Preprints.org classification

Subject: **Computer Science and Mathematics**. Topic within Subject: **Security Systems**.

Optional MDPI Topic: [Recent Advances in Security, Privacy, and Trust, 2nd Edition](https://www.mdpi.com/topics/044g6136h2). The detailed classification is in `PREPRINTS_METADATA.txt`. These metadata do not imply a submission.
