# POHE Initiative — Pattern-Oriented Harness Engineering

POHE is an open research and engineering initiative for AI-agent harnesses.
Its public site maintains a 52-record pattern catalog, an evidence method, an
independent review protocol, executed experiments, and open tooling. Each
candidate record shows a proposed pointcut, a protected contract, permitted
variation, an analytical object-interaction model, and the evidence layer
behind it.

Published at **https://harness-pattern.github.io/pohe/**

## What the initiative publishes

- An explicit **initiative charter** covering the mission, workstreams,
  participation paths, and evidence boundaries.
- A generated, deterministic rendering of frozen study artifacts. Nothing is
  computed at request time, and the site makes no network calls.
- An **evidence-layered** catalog. A retrieval cue is not a confirmed
  implementation, a source anchor is one exemplar and not a prevalence
  estimate, and the 52 records are a scoped candidate inventory.
- A **proposed** discipline. Specifications, contracts, and analytical
  interaction diagrams describe intended obligations; they do not certify any
  repository's behavior.

The catalog deliberately keeps these boundaries visible instead of compressing
them into a single confidence score.

The method page closes with a colophon honoring the author's unpublished
master's research on design patterns. It records the sources that shaped the
vocabulary here: the Gang of Four catalog, Roy T. Fielding's dissertation on
architectural styles, and Douglas C. Schmidt's work on distributed-computing
patterns and the POSA series.

## Structure

```
initiative.html             mission, workstreams, participation, and boundaries
index.html                  catalog with search/filter/deep links
method.html                 evidence key, frozen inputs, and reproduction
experiments.html            the two executed harness-evolution experiments
paper.html                  position paper and H1–H3 research agenda
patterns/<ID>/index.html     one detail page per record
data/patterns.json          generated catalog data
assets/diagrams/            SVG + PDF interaction diagrams (one pair per record)
assets/paper/               the complete position paper PDF
assets/styles.css           local stylesheet (light/dark, responsive, print)
assets/site.js              filter + navigation logic (no network)
scripts/generate_site.py    deterministic generator
tests/validate_site.py      stdlib integrity and accessibility checks
```

## Rebuild

The generator reads the companion study checkout and manuscript by default.
PyYAML is used when available, with a stdlib-only fallback parser.

```sh
python3 scripts/generate_site.py
python3 -I tests/validate_site.py
python3 -m http.server 8000
```

For presentation changes using the committed catalog and existing assets:

```sh
python3 scripts/generate_site.py --from-export
python3 -I tests/validate_site.py
```

This renders 59 HTML pages without changing the frozen JSON, diagrams, or paper.
The UI uses local design tokens, system fonts, light/dark themes, and keyboard
search (`/`).

Override the inputs with `--study /path/to/pattern-mining-study` and
`--paper /path/to/main.pdf`. Generated files are the output of the script; edit
the generator rather than the rendered HTML.

## Evidence boundaries

- 52 records across five source families (P, W, C, R, E).
- 41 repositories / 36 repository families in the frozen audit panel.
- 51 of 52 records have a canonical source anchor; E08 is specification-only.
- Human validation, detector validation, and the H1/H2 causal studies remain
  open. The study's 200 role-conditioned profiles are one model's consistency
  under different prompts, not independent human agreement.

## License

The site content summarizes an unpublished research manuscript. The
interaction diagrams and catalog text are provided for review and discussion.
Canonical source anchors link to the named public implementation repositories
at the recorded commit; follow each repository's own license.
