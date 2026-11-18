# H2 independent human review

This directory contains the public, sealed review build for the H2 static
enforcement-gap sample.

- Package: `69c998855a98ae1d`
- Protocol: `h2-static-gap-1.0.0`
- Sample: 9 repositories, 54 cells
- Reviewer input: pseudonymous reviewer ID only
- Excluded: agent labels, agent rationales, agent source anchors, prior human
  reviews, role selection, and agent-reveal data

Reviewers export one JSON file. The public build does not adjudicate
disagreements. After responses are collected, the research team resolves
disagreements in a separate adjudication stage.

Rebuild from the workspace root:

```bash
python3 pohe-evidence/phase1/human_review/build_package.py \
  --manifest pattern-mining-study/data/manifests/current_41_repositories.csv \
  --packets pohe-evidence/phase1/panel41/packets \
  --checkouts pattern-mining-study/.cache/repositories \
  --catalog pohe-agent/src/pohe_agent/data/patterns.json \
  --out pohe-site/reviews/h2 \
  --public
python3 pohe-evidence/phase1/human_review/validate_package.py \
  pohe-site/reviews/h2
```

Do not add overlays to the `--overlays` argument when building the public
version. The frozen sample is read from `sample.json`; the reviewed-overlap
selection metadata is intentionally omitted from the public data.
