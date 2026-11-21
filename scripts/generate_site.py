#!/usr/bin/env python3
"""Generate the standalone POHE initiative site from the frozen study artifacts.

PyYAML is used when installed so the frozen protocol files parse exactly as the
study reads them; a small stdlib-only YAML reader remains as a fallback.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pattern_examples import EXAMPLES  # noqa: E402
FAMILIES = {
    "P": ("Coding-agent mechanisms", "Execution loops, tools, authority, durable state, and stop-time checks."),
    "W": ("Workflows & coordination", "Named workflows and coordination forms from official agent guides."),
    "C": ("Communication", "Classical communication structures applied to agent systems."),
    "R": ("Reasoning & feedback", "Reason–act and verbal-feedback mechanisms."),
    "E": ("Harness evolution", "Research mechanisms that adapt or search over harnesses."),
}
ACTIVITIES = {
    "A1": "Design & implementation",
    "A2": "Testing & verification",
    "A3": "Maintenance & evolution",
}
# The three major hypotheses are a reading lens over the A² coordinates, not a
# fourth taxonomy axis and not a change to any frozen pointcut, contract, cue,
# or evidence value. A record informs a hypothesis through its frozen activity
# focus: A1 (design) specifies the durable commitment (H1), A2 (verification)
# exercises mechanized separation (H2), and A3 (evolution) is where bounded
# self-improvement is tested (H3).
HYPOTHESES = {
    "H1": {
        "kicker": "Asset question",
        "title": "What is the asset when the harness keeps rewriting itself?",
        "activity": "A1",
        "summary": "Our answer: the durable asset is not a prompt or a code snapshot but an inspectable design commitment—pointcut, contract, permitted variation, test obligation, negative control.",
    },
    "H2": {
        "kicker": "Separation question",
        "title": "Must harness engineering keep a separation of concerns—and a boundary?",
        "activity": "A2",
        "summary": "Our answer: yes, and under agent-led evolution that boundary must be mechanized—guards, gates, checkpoints, rollback, evaluation—not merely documented.",
    },
    "H3": {
        "kicker": "Controlled-variation question",
        "title": "Do explicit variation boundaries beat unconstrained self-improvement?",
        "activity": "A3",
        "summary": "We ask whether bounded self-improvement yields lower contract drift, lower regression, faster recovery, and clearer attribution than unconstrained revision. This causal comparison is still open.",
    },
}
HYPOTHESIS_BY_ACTIVITY = {"A1": "H1", "A2": "H2", "A3": "H3"}


def derive_hypotheses(activities: list[str]) -> list[str]:
    """Map frozen A² activity coordinates to the hypotheses they inform."""
    return [HYPOTHESIS_BY_ACTIVITY[a] for a in activities if a in HYPOTHESIS_BY_ACTIVITY]
EXPECTED_IDS = (
    [f"P{i:02d}" for i in range(1, 30)]
    + [f"W{i:02d}" for i in range(1, 8)]
    + [f"C{i:02d}" for i in range(1, 5)]
    + [f"R{i:02d}" for i in range(1, 3)]
    + [f"E{i:02d}" for i in range(1, 11)]
)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def scalar(value: str):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        return [scalar(part) for part in next(csv.reader([inner], skipinitialspace=True))]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.lower() == "false":
        return False
    if value.lower() == "true":
        return True
    return value


def yaml_load(text: str):
    """Read a POHE protocol file.

    PyYAML is used when available so quoted values, folded scalars, and inline
    with indication syntax parse exactly as the study reads them. The bundled
    conservative parser remains as a stdlib-only fallback.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - exercised only without PyYAML
        yaml = None
    if yaml is not None:
        return yaml.safe_load(text)
    raw = [(len(line) - len(line.lstrip(" ")), line.strip()) for line in text.splitlines()]
    lines = [(indent, content) for indent, content in raw if content and not content.startswith("#")]

    def block(start: int, indent: int):
        is_list = lines[start][1].startswith("- ") or lines[start][1] == "-"
        result = [] if is_list else {}
        i = start
        while i < len(lines):
            level, content = lines[i]
            if level < indent:
                break
            if level > indent:
                raise ValueError(f"Unexpected indentation near {content!r}")
            if is_list:
                if not content.startswith("-"):
                    break
                item = content[1:].strip()
                if not item:
                    value, i = block(i + 1, lines[i + 1][0])
                    result.append(value)
                    continue
                if re.match(r"^[^:\[\]]+:\s*", item):
                    key, val = item.split(":", 1)
                    entry = {}
                    if val.strip():
                        entry[key.strip()] = scalar(val)
                        i += 1
                    else:
                        value, i = block(i + 1, lines[i + 1][0])
                        entry[key.strip()] = value
                    if i < len(lines) and lines[i][0] > indent:
                        extra, i = block(i, lines[i][0])
                        entry.update(extra)
                    result.append(entry)
                    continue
                result.append(scalar(item))
                i += 1
                continue
            if content.startswith("- "):
                break
            key, val = content.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == ">":
                parts = []
                i += 1
                while i < len(lines) and lines[i][0] > indent:
                    parts.append(lines[i][1])
                    i += 1
                result[key] = " ".join(parts)
            elif val:
                result[key] = scalar(val)
                i += 1
            else:
                if i + 1 >= len(lines) or lines[i + 1][0] <= indent:
                    result[key] = {}
                    i += 1
                else:
                    value, i = block(i + 1, lines[i + 1][0])
                    result[key] = value
        return result, i

    if not lines:
        return {}
    value, end = block(0, lines[0][0])
    if end != len(lines):
        raise ValueError(f"Could not parse YAML line {end + 1}: {lines[end]!r}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = content.rstrip() + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")


def source_url(repo: str, commit: str, locator: str) -> str:
    match = re.match(r"^(.*):(\d+)$", locator)
    path, line = (match.group(1), match.group(2)) if match else (locator, "")
    url = f"https://github.com/{repo}/blob/{commit}/{quote(path, safe='/')}"
    return f"{url}#L{line}" if line else url


def nav(prefix: str, current: str) -> str:
    return f"""
<a class="skip-link" href="#main-content">Skip to content</a>
<header class="site-header">
  <a class="wordmark" href="{prefix}./" aria-label="POHE initiative home">
    <svg class="mark" viewBox="0 0 42 42" aria-hidden="true"><path d="M4 4h34v34H4zM4 14h34M14 4v34M14 14l12 12M26 14v12H14"/><circle cx="14" cy="14" r="2.5"/><circle cx="26" cy="26" r="2.5"/></svg>
    <span><strong>POHE</strong><small>An open engineering initiative</small></span>
  </a>
  <button class="nav-toggle" id="nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav"><span>Index</span><i aria-hidden="true"></i></button>
  <nav class="site-nav" id="site-nav" aria-label="Primary navigation">
    <a href="{prefix}initiative.html"{' aria-current="page"' if current == 'initiative' else ''}>Initiative</a>
    <a href="{prefix}./#catalog"{' aria-current="page"' if current == 'catalog' else ''}>Catalog <sup>52</sup></a>
    <a href="{prefix}agent.html"{' aria-current="page"' if current == 'agent' else ''}>Agent</a>
    <a href="{prefix}experiments.html"{' aria-current="page"' if current == 'experiments' else ''}>Experiments</a>
    <a href="{prefix}reviews/h2/">Review</a>
    <a class="paper-link" href="{prefix}paper.html"{' aria-current="page"' if current == 'paper' else ''}>Paper</a>
    <a class="personal-link" href="https://xhyccc.github.io/">About me <span aria-hidden="true">↗</span></a>
  </nav>
</header>""".strip()


def realization_files(anchors: list[dict], realizations: list[dict], limit: int = 5) -> list[dict]:
    """Pick a canonical exemplar plus source-first cue realizations from distinct repos."""
    files: list[dict] = []
    seen_repos: set[str] = set()
    for anchor in anchors:
        if anchor.get("kind") != "source":
            continue
        repo = str(anchor["repo_slug"])
        seen_repos.add(repo)
        locator = str(anchor["implementation"])
        match = re.match(r"^(.*):(\d+)$", locator)
        path, line = (match.group(1), int(match.group(2))) if match else (locator, None)
        files.append(
            {
                "repo": repo,
                "path": path,
                "line": line,
                "url": str(anchor["url"]),
                "kind": "canonical source",
            }
        )
    ordered = sorted(
        realizations,
        key=lambda row: (
            0 if str(row["realization_status"]).startswith("source") else 1,
            str(row["repo_slug"]).lower(),
            str(row["representative_path"]),
        ),
    )
    for row in ordered:
        if len(files) >= limit:
            break
        repo = str(row["repo_slug"])
        if repo in seen_repos:
            continue
        seen_repos.add(repo)
        path = str(row["representative_path"])
        line = int(row["representative_line"]) if str(row["representative_line"]).isdigit() else None
        files.append(
            {
                "repo": repo,
                "path": path,
                "line": line,
                "url": source_url(repo, str(row["first_introduced_commit"]), f"{path}:{line}" if line else path),
                "kind": "retrieval cue",
            }
        )
    return files


def shell(title: str, description: str, body: str, prefix: str = "", current: str = "", canonical: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{esc(description)}">
  <meta name="theme-color" content="#f2eee4">
  <title>{esc(title)}</title>
  <link rel="canonical" href="https://harness-pattern.github.io/pohe/{esc(canonical)}">
  <link rel="stylesheet" href="{prefix}assets/styles.css">
  <script src="{prefix}assets/site.js" defer></script>
</head>
<body>
{nav(prefix, current)}
{body}
<footer class="site-footer">
  <div><strong>POHE Initiative</strong> / Pattern-Oriented Harness Engineering<br><span class="footer-author">Founded by Haoyi Xiong</span></div>
  <p>An open research and engineering initiative for durable, auditable agent-harness responsibilities. Specifications and analytical diagrams do not certify implementations.</p>
  <div class="footer-links"><a href="{prefix}initiative.html">About the initiative</a><a href="{prefix}method.html#evidence-key">How to read the evidence</a><a href="{prefix}method.html#colophon">Colophon</a><a href="https://github.com/harness-pattern/pohe">GitHub</a></div>
  <p class="footer-disclosure">Analytical diagrams, prompt sketches, and code designs describe proposed contracts. They are not observed traces, repository class declarations, or evidence that any implementation conforms.</p>
</footer>
</body>
</html>"""


def catalog_row(p: dict) -> str:
    evidence = [p["evidence"]["status"], "current_cue" if p["evidence"]["current_cue_repositories"] else "no_current_cue"]
    hypotheses = p["hypotheses"]
    search = " ".join([p["id"], p["name"], p["problem"], p["join_point"], p["family_name"], *p["activity_names"], *hypotheses]).lower()
    activity = " · ".join(p["activities"])
    hypothesis_chips = "".join(f'<i class="row-hypothesis" data-hypothesis-tag="{h}">{h}</i>' for h in hypotheses)
    source = "Source-located" if p["evidence"]["status"] == "source_located" else "Specification only"
    observed = p["evidence"]["family_observed"]
    total = p["evidence"]["family_total"]
    coverage = round(100 * p["evidence"]["family_coverage"])
    return f"""<article class="pattern-row" data-pattern="true" data-family="{p['family']}" data-activities="{' '.join(p['activities'])}" data-hypotheses="{' '.join(hypotheses)}" data-evidence="{' '.join(evidence)}" data-search="{esc(search)}">
  <a class="pattern-row-link" href="patterns/{p['id']}/" aria-label="{p['id']}: {esc(p['name'])}">
    <span class="pattern-code">{p['id']}</span>
    <span class="pattern-main"><strong>{esc(p['name'])}</strong><span>{esc(p['problem'])}</span></span>
    <span class="pattern-activity"><b>{activity}</b><small>{source}</small><span class="row-hypotheses">{hypothesis_chips}</span></span>
    <span class="row-coverage" aria-label="Any-evidence coverage: {observed} of {total} repository families"><span class="coverage-track"><i style="width:{coverage}%"></i></span><small>{observed}<b>/{total}</b></small></span>
    <span class="row-arrow" aria-hidden="true">↗</span>
  </a>
</article>""".strip()


def exemplar_html(p: dict) -> str:
    source_anchor = next((anchor for anchor in p["anchors"] if anchor.get("kind") == "source"), None)
    if not source_anchor:
        return '<span class="inventory-empty">None. This record is anchored only in its source specification.</span>'
    exemplar = (
        f'<a class="inventory-source-link" href="{esc(source_anchor["url"])}" target="_blank" rel="noreferrer">'
        f'{esc(source_anchor["repo_slug"])}<code>{esc(source_anchor["implementation"])}</code></a>'
    )
    if source_anchor.get("support_url"):
        exemplar += (
            f'<span class="inventory-support">Support: '
            f'<a href="{esc(source_anchor["support_url"])}" target="_blank" rel="noreferrer">'
            f'{esc(source_anchor["support"])}</a></span>'
        )
    return exemplar


def inventory_entry(p: dict) -> str:
    exemplar = exemplar_html(p)
    aliases = ", ".join(esc(alias) for alias in p["aliases"]) or "None"
    source = esc(p["source_locator"]) if p["source_locator"] else "See the source record."
    coordinates = " · ".join(esc(item) for item in p["activities"])
    return f"""<article class="inventory-entry" id="record-{p['id']}" data-family="{p['family']}">
  <div class="inventory-entry-head">
    <span class="inventory-code">{p['id']}</span>
    <div class="inventory-title"><h3><a href="patterns/{p['id']}/">{esc(p['name'])}</a></h3><p>{esc(p['problem'])}</p></div>
    <span class="inventory-coords">{esc(p['family_name'])} · {coordinates}</span>
  </div>
  <dl class="inventory-fields">
    <div><dt>Join point</dt><dd><code>{esc(p['join_point'])}</code></dd></div>
    <div><dt>Source locator</dt><dd>{source}</dd></div>
    <div class="inventory-wide"><dt>Lexical cue</dt><dd><code class="inventory-regex">{esc(p['retrieval_cue']['regex'])}</code></dd></div>
    <div><dt>Aliases</dt><dd>{aliases}</dd></div>
    <div><dt>Canonical exemplar</dt><dd>{exemplar}</dd></div>
    <div class="inventory-wide"><dt>Negative control</dt><dd>{esc(p['negative_control'])}</dd></div>
  </dl>
</article>"""


def render_inventory(data: dict) -> str:
    patterns = data["patterns"]
    families = []
    jump = []
    for key, (label, _summary) in FAMILIES.items():
        items = [p for p in patterns if p["family"] == key]
        entries = "".join(inventory_entry(p) for p in items)
        jump.append(f'<a href="#inventory-{key}"><b>{key}</b><span>{esc(label)}</span><small>{len(items)} records</small></a>')
        families.append(
            f'<section class="inventory-family" id="inventory-{key}">'
            f'<div class="inventory-family-head"><span class="inventory-family-code">{key}</span>'
            f'<div><h2>{esc(label)}</h2><p>{len(items)} records</p></div></div>{entries}</section>'
        )
    body = f"""
<main id="main-content" class="inventory-page">
  <header class="method-hero inventory-hero">
    <span class="section-index">INVENTORY / 52 RECORDS</span>
    <h1>Every record,<br><em>its source and its cue.</em></h1>
    <p>The record-by-record companion to the paper's detection and matching method. Each entry gives the source locator, lexical cue, aliases, canonical exemplar, support artifact, and negative control. The complete pointcut, contract, variation, and evidence layers remain on the record page.</p>
    <div class="method-actions"><a class="button button-primary" href="#inventory">Read the ledger <span aria-hidden="true">↓</span></a><a class="text-link" href="./#catalog">Browse the catalog <span aria-hidden="true">↗</span></a></div>
    <nav class="inventory-jump" aria-label="Inventory families">{''.join(jump)}</nav>
  </header>
  <div class="inventory-ledger" id="inventory">{''.join(families)}</div>
</main>"""
    return shell(
        "Inventory — POHE",
        "The record-by-record POHE inventory: 52 design patterns with source locators, lexical cues, aliases, canonical exemplars, and negative controls.",
        body,
        current="inventory",
        canonical="inventory.html",
    )


def render_agent() -> str:
    body = """
<main id="main-content" class="agent-page">
  <header class="agent-hero">
    <div class="agent-hero-copy">
      <span class="section-index">OPEN PACKAGE / AGENT + CLI + SKILLS</span>
      <h1>Point the lens<br><em>at your own harness.</em></h1>
      <p class="agent-deck">A portable agent package for the 52 POHE records. Scan an arbitrary repository for harness-pattern cues, pull the structural candidates into source review, and turn what survives into a pattern-guided evolution proposal.</p>
      <div class="method-actions"><a class="button button-primary" href="https://github.com/xhyccc/pohe-agent">Open the repository <span aria-hidden="true">↗</span></a><a class="text-link" href="#install">Install the CLI <span aria-hidden="true">↓</span></a></div>
      <ul class="agent-meta"><li><b>MIT</b><span>license</span></li><li><b>stdlib</b><span>no runtime deps</span></li><li><b>52</b><span>frozen records</span></li><li><b>3</b><span>agent skills</span></li></ul>
    </div>
    <aside class="agent-terminal" aria-label="Example scan output">
      <div class="terminal-bar"><i></i><i></i><i></i><b>pohe-agent scan</b></div>
      <pre class="terminal-body"><code><span class="terminal-prompt">$</span> pohe-agent scan ./harness
<span class="terminal-dim">52 records · 6 with a retrieval cue · 0 high priority</span>

<span class="terminal-id">P01</span>  Minimal linear loop        <span class="terminal-dim">medium</span>  runtime/loop.py:6
<span class="terminal-id">P09</span>  Deferred tool loading      <span class="terminal-dim">medium</span>  runtime/tools.py:15
<span class="terminal-id">P19</span>  Budgeted loop with caps    <span class="terminal-dim">medium</span>  runtime/loop.py:8

<span class="terminal-note">E1 retrieval cue. Open the source before calling it a pattern.</span></code></pre>
      <p class="terminal-caption">Illustrative output. A cue is a place to look, not a confirmed implementation.</p>
    </aside>
  </header>

  <section class="agent-pipeline" aria-labelledby="pipeline-title">
    <div class="agent-section-head"><span class="section-index">THE PIPELINE</span><h2 id="pipeline-title">Three moves, three questions.</h2><p>What is worth reading? Is the mechanism in this scope? What may safely change?</p></div>
    <div class="pipeline-grid">
      <article><span>01 / DETECT</span><h3>Where are the leads?</h3><p>Run the frozen cue expressions over code, tests, and support files. Excludes caches, build output, and vendored trees.</p><code>pohe-agent scan &lt;repo&gt;</code></article>
      <article><span>02 / ANALYZE</span><h3>Is the mechanism here?</h3><p>Anchor each cue to its enclosing declaration, then check context, trigger, structure, and effect. The packet reports missing roles, the negative control, and contract probes. Parallel file analysis is deterministic with <code>--jobs</code>.</p><code>pohe-agent analyze &lt;repo&gt; --pattern P01</code></article>
      <article><span>03 / EVOLVE</span><h3>What may change?</h3><p>Build a proposal that protects the contract, names authority, adds the test obligation, and records rollback.</p><code>pohe-agent plan &lt;repo&gt; --findings findings.json</code></article>
    </div>
  </section>

  <section class="agent-install" id="install">
    <div class="agent-install-copy"><span class="section-index">INSTALL / 60 SECONDS</span><h2>Clone, install, point.</h2><p>The scanner is Python standard library only. The agent instructions and skills travel with the repository.</p><p class="agent-install-note">Python 3.10+. No API key, no network access, and no repository upload.</p></div>
    <div class="agent-install-command"><div class="command-head"><span>TERMINAL</span><button class="copy-button" type="button" data-copy="agent-install-code">Copy</button></div><pre class="command-block" id="agent-install-code"><code>git clone https://github.com/xhyccc/pohe-agent.git
cd pohe-agent
python3 -m pip install -e .
pohe-agent scan /path/to/your/harness</code></pre></div>
  </section>

  <section class="agent-ships" aria-labelledby="ships-title">
    <div class="agent-section-head"><span class="section-index">WHAT SHIPS</span><h2 id="ships-title">An agent package, not a black box.</h2><p>Every layer is readable, testable, and replaceable.</p></div>
    <div class="ships-grid">
      <article><span class="ship-code">A</span><h3>AGENTS.md</h3><p>The operating manual: the evidence ladder, the standard workflow, the hard rules, and the handoff points between detection, matching, and evolution.</p></article>
      <article><span class="ship-code">S</span><h3>Three skills</h3><p><b>Detection</b> finds leads. <b>Matching</b> adjudicates a candidate against the pointcut, contract, and negative control. <b>Evolution</b> plans and verifies the change.</p><ul class="skill-list"><li><code>pohe-pattern-detection</code></li><li><code>pohe-pattern-matching</code></li><li><code>pohe-harness-evolution</code></li></ul></article>
      <article><span class="ship-code">C</span><h3>CLI</h3><p><code>scan</code>, <code>match</code>, <code>analyze</code>, <code>plan</code>, <code>list</code>, and <code>show</code>. Deterministic walking, JSON output, and Markdown reports.</p></article>
      <article><span class="ship-code">D</span><h3>Frozen catalog</h3><p>All 52 records: pointcut, contract, variation, test obligation, negative control, evolution question, cue, and canonical exemplar where one exists.</p></article>
      <article><span class="ship-code">E</span><h3>Runnable example</h3><p>A three-file mini harness with a bounded loop, deferred tool discovery, and a permission check. Scan it before pointing the tool at your own code.</p></article>
      <article><span class="ship-code">T</span><h3>Tests</h3><p>Catalog integrity, repository walking, cue retrieval, structural candidates, report boundaries, and plan generation. Runs on every push.</p></article>
    </div>
  </section>

  <section class="agent-repro" aria-labelledby="repro-title">
    <div class="agent-section-head"><span class="section-index">VALIDATION / 41 REPOSITORIES</span><h2 id="repro-title">Run on the study's own checkouts.</h2><p>Not only the bundled example: 52 records against 41 frozen repositories, compared cell by cell with the published cue matrix.</p></div>
    <div class="repro-grid">
      <div><b>172,832</b><span>candidate files scanned</span></div>
      <div><b>2,132</b><span>repository × record cells</span></div>
      <div><b>98.55%</b><span>exact agreement</span></div>
      <div><b>99.60%</b><span>recall over study-positive cells</span></div>
    </div>
    <p class="repro-note">All five misses are files the scanner excludes by design: four above the 2 MB limit and one minified third-party asset. Most extra cells are cells where re-running the study's own detector retrieves cues the frozen matrix omits. This is cue-level agreement, not a claim that every match is an implemented pattern. <a class="text-link" href="https://github.com/xhyccc/pohe-agent/blob/main/docs/reproduction.md">Method and per-cell differences <span aria-hidden="true">↗</span></a></p>
    <p class="repro-note">The current calibration check retrieves all 51 canonical anchors and rejects none, while keeping 33 missing-structure cases and one medium negative-control risk visible. It does not force exemplars to pass. <a class="text-link" href="https://github.com/xhyccc/pohe-agent/blob/main/docs/calibration.md">Read the calibration report <span aria-hidden="true">↗</span></a></p>
  </section>

  <section class="agent-boundary" aria-labelledby="boundary-title">
    <div class="agent-boundary-copy"><span class="section-index">EVIDENCE LADDER</span><h2 id="boundary-title">Cues are leads.<br>Contracts are claims.</h2><p>The tool can produce E1 on its own. E2 and E3 require reading the source, the tests, or a runtime check. The package keeps that boundary visible in every report.</p><a class="text-link" href="method.html#evidence-key">Read the full evidence key <span aria-hidden="true">↗</span></a></div>
    <ol class="agent-ladder">
      <li><b>E0</b><div><strong>Proposed record</strong><span>The frozen specification and its negative control.</span></div></li>
      <li><b>E1</b><div><strong>Retrieval cue</strong><span>A regex or symbol hit. Produced by <code>scan</code> and <code>match</code>.</span></div></li>
      <li><b>E2</b><div><strong>Source-located mechanism</strong><span>Roles, state, and control flow identified in source.</span></div></li>
      <li><b>E3</b><div><strong>Contract support</strong><span>Executable or explicit evidence for ordering, errors, or boundaries.</span></div></li>
    </ol>
  </section>

  <section class="agent-workflow" aria-labelledby="workflow-title">
    <div class="agent-section-head"><span class="section-index">AGENT WORKFLOW</span><h2 id="workflow-title">From scan to recorded decision.</h2></div>
    <ol class="workflow-list">
      <li><span>01</span><div><h3>Orient</h3><p>Read the target repository's own instructions and confirm the task: detection only, matching, or evolution.</p></div></li>
      <li><span>02</span><div><h3>Scan</h3><p>Retrieve cues, read the family table, and keep unobserved records visible as unobserved.</p></div></li>
      <li><span>03</span><div><h3>Analyze</h3><p>Run <code>analyze</code>, read the anchored scope and missing roles, then open the source. Answer all four pointcut dimensions and all four contract fields. Apply the negative control.</p></div></li>
      <li><span>04</span><div><h3>Record</h3><p>Write the evidence level actually reached, the files and tests, and the remaining unknowns.</p></div></li>
      <li><span>05</span><div><h3>Plan</h3><p>Protect the contract, stay inside the variation seam, and name authority, rollback, and a retirement trigger.</p></div></li>
      <li><span>06</span><div><h3>Verify</h3><p>Add the contract test first. Exercise failure and termination paths. Canary one real task before rollout.</p></div></li>
    </ol>
  </section>

  <aside class="agent-cta">
    <div><span class="section-index">START HERE</span><h2>Read the agent. Then read your harness.</h2><p>The repository contains the instructions, the skills, the catalog, and the CLI. The initiative's catalog remains the reference for every record.</p></div>
    <div class="agent-cta-actions"><a class="button button-primary" href="https://github.com/xhyccc/pohe-agent">GitHub / pohe-agent <span aria-hidden="true">↗</span></a><a class="text-link" href="./#catalog">Browse the 52 records <span aria-hidden="true">↗</span></a></div>
  </aside>
</main>"""
    return shell(
        "Agent toolkit — POHE",
        "An open agent package for POHE harness pattern detection, scope-anchored source analysis, and pattern-guided harness evolution. 52 frozen records, three agent skills, and a dependency-free CLI.",
        body,
        current="agent",
        canonical="agent.html",
    )


def render_initiative() -> str:
    body = """
<main id="main-content" class="method-page initiative-page">
  <header class="method-hero initiative-hero">
    <span class="section-index">POHE / OPEN INITIATIVE</span>
    <h1>A discipline for the<br><em>system around the model.</em></h1>
    <p>POHE is an open research and engineering initiative. We build shared vocabulary, evidence, review, and tooling for agent harnesses: the executable layer that assembles context, exposes tools, enforces permissions, maintains state, evaluates work, and decides when to stop.</p>
    <div class="method-actions"><a class="button button-primary" href="./#catalog">Explore the catalog <span aria-hidden="true">↓</span></a><a class="text-link" href="paper.html">Read the position paper <span aria-hidden="true">→</span></a><a class="text-link" href="https://github.com/harness-pattern/pohe/issues/new">Contribute on GitHub <span aria-hidden="true">↗</span></a></div>
    <ul class="initiative-metrics" aria-label="Initiative at a glance"><li><strong>52</strong><span>candidate records</span></li><li><strong>36</strong><span>repository families</span></li><li><strong>H1–H3</strong><span>falsifiable agenda</span></li><li><strong>Open</strong><span>review and participation</span></li></ul>
  </header>

  <section class="method-section" id="mission"><div class="method-section-number">01</div><div><h2>Why an initiative</h2><p class="method-lead">Models absorb scaffolding. They do not automatically absorb accountability.</p><p>As model capability improves, more context management, tool choice, and review can move into the model. Authorization, acceptance, audit, recovery, and termination remain system obligations: someone or something must establish who may act, what evidence counts as acceptance, what survives a failure, and when execution must stop.</p><p>In practice these obligations are often recorded only as component names or implementation details. Teams rediscover the same harness decisions under different labels, and a rewrite can silently remove the guarantee that made the original design safe. POHE exists to make those obligations durable, reviewable, and comparable through change.</p><div class="initiative-principles"><article><span>01 / CONTRACT</span><h3>Contract before label</h3><p>A pattern name is only useful when it identifies the obligation that must hold and the variation that is permitted.</p></article><article><span>02 / EVIDENCE</span><h3>Evidence before claim</h3><p>A keyword match, a source-located exemplar, and a tested contract are different evidence levels. The initiative keeps them separate.</p></article><article><span>03 / AUTHORITY</span><h3>Independent authority</h3><p>The proposal, execution, acceptance, and audit paths should not collapse into one self-certifying loop.</p></article><article><span>04 / FALSIFICATION</span><h3>Open falsification</h3><p>Every research claim should name the observation that would weaken or reject it. H1–H3 are public research targets, not settled results.</p></article></div></div></section>

  <section class="method-section" id="workstreams"><div class="method-section-number">02</div><div><h2>What the initiative maintains</h2><p class="method-lead">The website is the public working surface of POHE, not a static brochure.</p><div class="initiative-grid"><article class="initiative-card"><span>CATALOG</span><h3>A pattern language</h3><p>Fifty-two candidate records connect recurring harness mechanisms to protected contracts, variation seams, authority, evidence, and lifecycle conditions.</p><a class="text-link" href="./#catalog">Browse the records <span aria-hidden="true">→</span></a></article><article class="initiative-card"><span>METHOD</span><h3>Evidence and reproducibility</h3><p>Frozen inputs, source provenance, retrieval instruments, evidence levels, and explicit limitations make the claims inspectable.</p><a class="text-link" href="method.html">Read the method <span aria-hidden="true">→</span></a></article><article class="initiative-card"><span>REVIEW</span><h3>Independent human review</h3><p>The H2 review protocol collects source-grounded, pseudonymous judgments without exposing agent labels or prior reviews.</p><a class="text-link" href="reviews/h2/">Join the review <span aria-hidden="true">→</span></a></article><article class="initiative-card"><span>TOOLS + TESTS</span><h3>Applied instruments</h3><p>The pohe-agent toolkit and executed harness-evolution experiments turn the record into something teams can inspect and test.</p><a class="text-link" href="agent.html">Open the toolkit <span aria-hidden="true">→</span></a></article></div></div></section>

  <section class="method-section" id="participate"><div class="method-section-number">03</div><div><h2>How to take part</h2><p class="method-lead">POHE becomes useful when teams can test it on their own harness and publish what survives.</p><ol class="loop-list"><li><b>Map a responsibility.</b> Pick one consequential harness obligation, name its join point, and state what may vary.</li><li><b>Try the instrument.</b> Run <code>pohe-agent</code> against a repository and separate retrieval cues from structural evidence.</li><li><b>Review a record.</b> Contribute an independent H2 judgment from the frozen source excerpts.</li><li><b>Report a case.</b> Open an issue with a field case, contract test, counterexample, or an implementation that the record fails to explain.</li><li><b>Replicate a study.</b> Test H1 persistence or H3 record value under a design that can falsify the claim.</li></ol></div></section>

  <section class="method-section" id="status"><div class="method-section-number">04</div><div><h2>Status and boundaries</h2><p>The initiative is deliberately explicit about what is established, what is demonstrated in an apparatus, and what remains prospective.</p><ul class="limitations-list"><li>The 52 records are a scoped candidate inventory, not a prevalence estimate.</li><li>Source anchors are worked exemplars, not evidence that other repositories conform to the proposed contract.</li><li>The H2 testbed demonstrates mechanisms under controlled software conditions; it is not a field estimate of effectiveness.</li><li>H1 and H3 remain prospective research programs, and independent human adjudication remains open.</li><li>The pattern records and diagrams describe proposed obligations; they do not certify any implementation.</li></ul></div></section>

  <section class="method-section" id="join"><div class="method-section-number">05</div><div><div class="initiative-cta"><span class="section-index">BUILD THE COMMON RECORD</span><h2>Bring a harness responsibility into the open.</h2><p>Read the position, inspect the evidence, test a record, or send the case that breaks one.</p><div class="method-actions"><a class="button button-primary" href="paper.html">Read the paper <span aria-hidden="true">→</span></a><a class="text-link" href="https://github.com/harness-pattern/pohe/issues/new">Open a contribution <span aria-hidden="true">↗</span></a><a class="text-link" href="https://github.com/harness-pattern/pohe">View the repository <span aria-hidden="true">↗</span></a></div></div></div></section>
</main>"""
    return shell(
        "POHE Initiative — Pattern-Oriented Harness Engineering",
        "POHE is an open research and engineering initiative building an evidence-graded pattern discipline for AI-agent harness responsibilities, review, experiments, and tooling.",
        body,
        current="initiative",
        canonical="initiative.html",
    )


def render_index(patterns: list[dict]) -> str:
    family_options = "".join(f'<option value="{key}">{key} — {esc(value[0])}</option>' for key, value in FAMILIES.items())
    activity_options = "".join(f'<option value="{key}">{key} — {esc(value)}</option>' for key, value in ACTIVITIES.items())
    hypothesis_options = "".join(f'<option value="{key}">{key} · {esc(value["kicker"])}</option>' for key, value in HYPOTHESES.items())
    family_counts = Counter(p["family"] for p in patterns)
    family_rules = "".join(f"""<a href="#catalog" class="family-rule" data-family="{key}" data-family-link="{key}"><span class="family-code">{key}</span><strong>{esc(value[0])}</strong><small>{esc(value[1])}</small><span class="family-count">{family_counts[key]} records</span></a>""" for key, value in FAMILIES.items())
    rows = "\n".join(catalog_row(p) for p in patterns)
    body = f"""
<main id="main-content">
  <section class="hero" aria-labelledby="hero-title">
    <div class="hero-kicker"><span>POHE / Open initiative</span><span>Research + engineering + review</span></div>
    <h1 id="hero-title">Pattern-Oriented <em>Harness Engineering<span class="hero-title-colon">:</span></em><span class="hero-title-sub">Design needs Patterns, when Agentic AI meets OOD</span></h1>
    <div class="hero-grid">
      <div>
        <p class="hero-deck">POHE is an open initiative building a shared, evidence-graded pattern discipline for AI-agent harnesses: what may vary, what must hold, and what happens when it does not.</p>
        <ol class="hero-questions" aria-label="Three questions the initiative asks">
          <li><span class="hero-q-tag">H1</span><p class="hero-q">When a prompt or code snapshot may be replaced within days, what is the asset of harness engineering?</p></li>
          <li><span class="hero-q-tag">H2</span><p class="hero-q">Design patterns and POSA separated what rarely changes from what may be customized. Must harness engineering keep that separation of concerns—and its boundary?</p></li>
          <li><span class="hero-q-tag">H3</span><p class="hero-q">Does bounded self-improvement with explicit variation boundaries drift less, regress less, recover faster, and attribute changes more clearly than unconstrained revision?</p></li>
        </ol>
        <div class="hero-actions"><a class="button button-primary" href="initiative.html">About the initiative <span aria-hidden="true">→</span></a><a class="text-link" href="#catalog">Browse the 52 records <span aria-hidden="true">↓</span></a><a class="text-link" href="method.html">Read the method <span aria-hidden="true">↗</span></a></div>
      </div>
      <aside class="hero-note" aria-label="Position statement">
        <span class="note-label">Position / 001</span>
        <p>Scaling alone cannot absorb a harness’s system responsibilities. Name the join point. Protect the contract. Test the violation path. Record the lifecycle.</p>
        <a class="note-map-link" href="#a2-map"><span>A² taxonomy</span><strong>See the two-axis map</strong><i aria-hidden="true">↓</i></a>
      </aside>
    </div>
    <figure class="hero-map" id="a2-map">
      <a class="hero-map-frame" href="assets/a2/taxonomy-a2.svg" aria-label="Open the full A² taxonomy map as SVG">
        <img src="assets/a2/taxonomy-a2.svg" width="1120" height="690" alt="A-squared taxonomy: three activities—design, verification, and evolution—cross protected contracts and permitted variation, with an authorized re-generalization return from maintenance to design." loading="lazy">
      </a>
      <figcaption>
        <span class="map-kicker">A² / THE TAXONOMY</span>
        <h3>Two axes, one record.</h3>
        <p>Activities ask when an obligation is designed, verified, and revisited. Assets ask what must hold and what may change. The dashed return is re-generalization: the contract itself is reopened.</p>
        <div class="map-links"><a class="button button-ghost" href="assets/a2/taxonomy-a2.svg">Open the full map <span aria-hidden="true">↗</span></a><a class="text-link" href="assets/a2/taxonomy-a2.pdf" download>Download PDF <span aria-hidden="true">↓</span></a></div>
      </figcaption>
    </figure>
    <div class="inventory-line"><div class="stat"><strong>52</strong><span>candidate records</span></div><div class="stat"><strong>05</strong><span>source families</span></div><div class="stat"><strong>03</strong><span>activity coordinates</span></div><small>Inventory size, not a prevalence claim.</small></div>
  </section>

  <section class="initiative-banner" id="initiative" aria-labelledby="initiative-title">
    <div><span class="section-index">THE POHE INITIATIVE</span><h2 id="initiative-title">A shared language for the system around the model.</h2></div>
    <p>POHE brings researchers and harness builders together around one durable unit: the responsibility that must survive a change. The initiative maintains the pattern catalog, evidence method, independent review, experiments, and open tooling behind that work.</p>
    <a class="button button-ghost" href="initiative.html">Read the initiative charter <span aria-hidden="true">→</span></a>
  </section>

  <section class="family-section" aria-labelledby="family-title"><div class="section-index">05 / SOURCE FAMILIES</div><h2 id="family-title" class="visually-hidden">Five source families</h2><div class="family-rules">{family_rules}</div><p class="section-footnote">Families record provenance, not another taxonomy axis. E records are research mechanisms, not claims of independently established recurring patterns.</p></section>

  <section class="catalog-section" id="catalog" aria-labelledby="catalog-title">
    <div class="catalog-heading"><div><span class="section-index">INDEX / 001—052</span><h2 id="catalog-title">Pattern catalog</h2></div><p>Search by responsibility, mechanism, or join point. Filter without losing the underlying record.</p></div>
    <form class="filters" id="filters" role="search" aria-label="Filter pattern catalog">
      <div class="search-field"><label for="pattern-search">Search the pattern catalog</label><input id="pattern-search" name="q" type="search" placeholder="Try ‘sandbox’, ‘state’, or ‘stop’…" autocomplete="off" spellcheck="false"><span aria-hidden="true">⌕</span></div>
      <div class="select-field"><label for="family-filter">Source family</label><select id="family-filter" name="family"><option value="all">All five families</option>{family_options}</select></div>
      <div class="select-field"><label for="activity-filter">A² activity</label><select id="activity-filter" name="activity"><option value="all">All activities</option>{activity_options}</select></div>
      <div class="select-field"><label for="hypothesis-filter">Research hypothesis</label><select id="hypothesis-filter" name="hypothesis"><option value="all">All hypotheses</option>{hypothesis_options}</select></div>
      <div class="select-field"><label for="evidence-filter">Evidence status</label><select id="evidence-filter" name="evidence"><option value="all">Any evidence status</option><option value="source_located">Source-located exemplar</option><option value="specification_only">Specification only</option><option value="current_cue">Current retrieval cue</option><option value="no_current_cue">No current retrieval cue</option></select></div>
      <button class="reset-button" id="reset-filters" type="button">Reset</button>
    </form>
    <div class="results-meta"><p aria-live="polite">Showing <strong id="result-count">52</strong> of 52 records</p><p><span class="legend-dot"></span> Source anchors are exemplars, not population estimates; coverage is not confirmed prevalence.</p></div>
    <div class="pattern-index" id="pattern-index">{rows}</div>
    <div class="empty-state" id="empty-state" hidden><span>∅</span><h3>No records at this intersection.</h3><p>Try a broader term or clear one of the filters. An empty filtered view says nothing about implementations in repositories.</p><button class="button button-primary" type="button" data-reset-filters>Show all 52 records</button></div>
    <noscript><p class="noscript-note">Search and filters require JavaScript. All 52 records remain browsable above without it.</p></noscript>
  </section>

</main>"""
    return shell("POHE Initiative — Pattern-Oriented Harness Engineering", "An open research and engineering initiative for AI-agent harnesses: a 52-record pattern catalog, evidence method, independent review, experiments, and open tooling.", body, current="catalog")


def anchor_html(anchor: dict) -> str:
    if anchor["kind"] == "external":
        return f"""<div class="source-anchor"><span class="anchor-kind">Paper anchor</span><a href="{esc(anchor['url'])}">{esc(anchor['label'])} <span aria-hidden="true">↗</span></a><p>{esc(anchor['note'])}</p></div>"""
    implementation = esc(anchor["implementation"])
    support = ""
    if anchor.get("support"):
        support = f"""<div class="support-line"><span>Supporting surface</span><a href="{esc(anchor['support_url'])}"><code>{esc(anchor['support'])}</code></a></div>"""
    return f"""<div class="source-anchor"><span class="anchor-kind">Canonical source anchor</span><a class="repo-name" href="{esc(anchor['url'])}">{esc(anchor['repo_slug'])} <span aria-hidden="true">↗</span></a><code class="commit">{esc(anchor['commit_sha'][:12])}</code><a class="locator" href="{esc(anchor['url'])}"><code>{implementation}</code></a>{support}<p>{esc(anchor['note'])}</p></div>"""



def example_section(p: dict) -> str:
    example = EXAMPLES[p["id"]]
    prompt = esc(example["prompt"])
    code = esc(example["code"])
    note = esc(example["note"])
    prompt_id = f"prompt-{p['id']}"
    code_id = f"code-{p['id']}"
    return f"""
  <section class="record-section prompt-code-section" id="prompt-code"><div class="record-section-label"><span>06</span><h2>Prompt + code design</h2><p>An illustrative prompt and a small design sketch for the proposed contract. Names and APIs are invented; this is not a repository excerpt.</p></div>
    <div class="design-grid">
      <article class="design-card"><div class="design-card-head"><span>PROMPT SKETCH</span><button class="copy-button" type="button" data-copy="{prompt_id}">Copy</button></div><pre class="prompt-block" id="{prompt_id}"><code>{prompt}</code></pre></article>
      <article class="design-card"><div class="design-card-head"><span>CODE DESIGN / PYTHON</span><button class="copy-button" type="button" data-copy="{code_id}">Copy</button></div><pre class="code-block" id="{code_id}"><code>{code}</code></pre></article>
    </div>
    <p class="design-note"><strong>Illustrative design sketch.</strong> {note} This example is a teaching aid, not implementation evidence or a conformance claim.</p>
  </section>"""


def detection_section(p: dict) -> str:
    aliases = ", ".join(esc(alias) for alias in p["aliases"]) or "None"
    source = esc(p["source_locator"]) if p["source_locator"] else "See the source record."
    regex = esc(p["retrieval_cue"]["regex"])
    return f"""
  <section class="record-section detection-section" id="detection"><div class="record-section-label"><span>07</span><h2>Detection &amp; matching</h2><p>The frozen rule that retrieves this record, the canonical source exemplar, and the control that should stay unmatched.</p></div>
    <dl class="pointcut-grid detection-grid">
      <div><dt>Join point</dt><dd><code class="detection-code">{esc(p['join_point'])}</code></dd></div>
      <div><dt>Source locator</dt><dd>{source}</dd></div>
      <div><dt>Lexical cue</dt><dd><code class="detection-regex">{regex}</code><span class="detection-note">Frozen expression, v{esc(p['retrieval_cue']['version'])}. A match is a retrieval cue, not a confirmed implementation.</span></dd></div>
      <div><dt>Aliases</dt><dd>{aliases}</dd></div>
      <div><dt>Canonical exemplar</dt><dd>{exemplar_html(p)}</dd></div>
      <div><dt>Negative control</dt><dd>{esc(p['negative_control'])}</dd></div>
    </dl>
    <p class="design-note"><strong>Scope-anchored review.</strong> <code>pohe-agent analyze &lt;repo&gt; --pattern {p['id']}</code> anchors the cue to its enclosing declaration and checks a separate operational signature for all four pointcut dimensions. Missing roles and negative-control risks remain visible; a structural match is not E2/E3. <a href="https://github.com/xhyccc/pohe-agent/blob/main/docs/calibration.md">Calibration report ↗</a></p>
  </section>"""


def repo_files_html(files: list[dict]) -> str:
    if not files:
        return (
            '<div class="repo-files-empty"><strong>No repository file was retrieved for this record.</strong>'
            "<p>The frozen panel returned no cue realization, so the record remains specification-only. "
            "No repository link is invented to fill the gap.</p></div>"
        )
    rows = []
    for index, item in enumerate(files, 1):
        locator = esc(item["path"])
        if item.get("line"):
            locator += f":{item['line']}"
        tag = "CANONICAL SOURCE" if item["kind"] == "canonical source" else "RETRIEVAL CUE"
        label = f"{item['repo']}: {item['path']}" + (f" line {item['line']}" if item.get("line") else "")
        rows.append(
            f'<a class="repo-file" href="{esc(item["url"])}" target="_blank" rel="noreferrer" '
            f'aria-label="{esc(label)} (opens in a new tab)">'
            f'<span class="repo-file-index">{index:02d}</span>'
            f'<span class="repo-file-main"><strong>{esc(item["repo"])}</strong><code>{locator}</code></span>'
            f'<span class="repo-file-kind">{tag}</span><span class="repo-file-arrow" aria-hidden="true">↗</span></a>'
        )
    return "".join(rows)


def render_detail(p: dict, previous_id: str, next_id: str) -> str:
    activities = "".join(f'<li><b>{a}</b><span>{esc(ACTIVITIES[a])}</span></li>' for a in p["activities"])
    hypothesis_labels = "".join(f'<a class="record-hypothesis" data-hypothesis-tag="{h}" href="../../paper.html#research-agenda" aria-label="Research hypothesis {h}: {esc(HYPOTHESES[h]["kicker"])}">{h}</a>' for h in p["hypotheses"])
    pointcut = "".join(f'<div><dt>{label}</dt><dd>{esc(p["pointcut"][key])}</dd></div>' for key, label in (("context", "Context"), ("trigger", "Trigger"), ("structure", "Structure"), ("effect", "Effect")))
    contracts = "".join(f'<div><dt>{label}</dt><dd>{esc(p[key])}</dd></div>' for key, label in (("requires", "Requires"), ("ensures", "Ensures"), ("invariant", "Invariant"), ("violation", "Violation")))
    evidence = p["evidence"]
    if evidence["status"] == "source_located":
        status = "Source-located exemplar"
        evidence_note = "A concrete source anchor is available for this proposed record. It is one exemplar, not evidence that all repositories—or every contract field—conform."
    else:
        status = "Specification only"
        evidence_note = "No official executable implementation was located in the supplied study. The paper anchor motivates the record; it is not an implementation claim."
    anchors = "".join(anchor_html(a) for a in p["anchors"])
    current = evidence["current_cue_repositories"]
    coverage = evidence["family_observed"]
    family_total = evidence["family_total"]
    percent = round(100 * evidence["family_coverage"], 1)
    regex = esc(p["retrieval_cue"]["regex"])
    body = f"""
<main id="main-content" class="record-page" data-family="{p['family']}">
  <div class="record-breadcrumb"><a href="../../#catalog">Catalog</a><span>/</span><span>{p['family']} family</span><span>/</span><span>{p['id']}</span></div>
  <header class="record-header">
    <div class="record-number" aria-hidden="true">{p['id']}</div>
    <div class="record-title"><div class="record-labels"><span>Proposed specification</span><span>{esc(status)}</span>{hypothesis_labels}</div><h1>{esc(p['name'])}</h1><p>{esc(p['problem'])}</p></div>
    <div class="record-stamp"><span>JOIN POINT</span><code>{esc(p['join_point'])}</code><span>RECORD STATUS</span><strong>Candidate · v{esc(p['codebook_version'])}</strong></div>
  </header>

  <section class="record-section uml-section" id="uml-view"><div class="record-section-label"><span>01</span><h2>UML class view</h2><p>The proposed contract drawn as participant roles, received calls, and numbered dependencies.</p></div><figure class="interaction-figure"><a href="../../{p['class_svg']}" aria-label="Open full-size UML class view for {p['id']}"><img src="../../{p['class_svg']}" alt="UML class view for {esc(p['name'])}, showing participant roles, received calls, and analytical dependencies" loading="lazy"></a><figcaption><span>FIG. {p['id']} / UML CLASS VIEW</span><div class="figure-links"><a href="../../{p['class_svg']}">Open full SVG <span aria-hidden="true">↗</span></a><a class="download-link" href="../../{p['diagram_pdf']}" download>Download PDF <span aria-hidden="true">↓</span></a></div></figcaption></figure></section>

  <section class="record-section interaction-section" id="interactions"><div class="record-section-label"><span>02</span><h2>Object interactions</h2><p>The same roles and calls as a class collaboration and a numbered message sequence.</p></div><figure class="interaction-figure"><a href="../../{p['interaction_svg']}" aria-label="Open full-size object interaction diagram for {p['id']}"><img src="../../{p['interaction_svg']}" alt="Object interaction diagram for {esc(p['name'])}, showing role collaboration and a numbered message sequence" loading="lazy"></a><figcaption><span>FIG. {p['id']} / OBJECT INTERACTIONS</span><div class="figure-links"><a href="../../{p['interaction_svg']}">Open full SVG <span aria-hidden="true">↗</span></a><a class="download-link" href="../../{p['interaction_pdf']}" download>Download PDF <span aria-hidden="true">↓</span></a></div></figcaption></figure></section>

  <nav class="record-toc" aria-label="On this page"><span>On this record</span><a href="#uml-view">UML view</a><a href="#interactions">Object interactions</a><a href="#pointcut">Pointcut</a><a href="#contract">Contract</a><a href="#variation">Variation</a><a href="#prompt-code">Prompt + code</a><a href="#detection">Detection</a><a href="#repo-files">Repository files</a><a href="#evidence">Evidence</a></nav>

  <section class="record-section pointcut-section" id="pointcut"><div class="record-section-label"><span>03</span><h2>Pointcut</h2><p>Where the mechanism applies. All four dimensions belong to the proposed match, not just its vocabulary.</p></div><dl class="pointcut-grid">{pointcut}</dl></section>

  <section class="record-section contract-section" id="contract"><div class="record-section-label"><span>04</span><h2>Protected contract</h2><p>The obligation that clients, operators, and auditors should be able to rely on.</p></div><dl class="contract-list">{contracts}</dl><div class="negative-control"><h3>Negative control</h3><p>{esc(p['negative_control'])}</p></div></section>

  <section class="record-section variation-section" id="variation"><div class="record-section-label"><span>05</span><h2>Variation & lifecycle</h2><p>The hot spot is admitted only where the protected semantics survive.</p></div><div class="variation-copy"><div><h3>Permitted variation</h3><p>{esc(p['variation'])}</p></div><div><h3>Test obligation</h3><p>{esc(p['test_obligation'])}</p></div><div><h3>Evolution question</h3><p>{esc(p['evolution_question'])}</p></div></div><aside class="a2-coordinates"><h3>A² activity coordinates</h3><ul>{activities}</ul><p>One record may carry multiple activity coordinates. The five source families do not add an axis. These coordinates map to the research hypotheses—A1 → H1, A2 → H2, A3 → H3—on the <a href="../../paper.html#research-agenda">Paper page</a>.</p></aside></section>



{example_section(p)}

{detection_section(p)}

  <section class="record-section repo-files-section" id="repo-files"><div class="record-section-label"><span>08</span><h2>Repository files</h2><p>Up to five public files retrieved by the frozen cue and pinned to the examined commit. A match is a place to inspect, not a confirmed implementation.</p></div><div class="repo-files">{repo_files_html(p['repo_files'])}</div></section>

  <section class="record-section evidence-section" id="evidence"><div class="record-section-label"><span>09</span><h2>Evidence & provenance</h2><p>Each layer answers a different question. Do not substitute retrieval for review.</p></div><div class="evidence-content"><div class="evidence-status"><span class="status-mark"></span><h3>{esc(status)}</h3><p>{esc(evidence_note)}</p><p class="review-note"><strong>Review boundary:</strong> this candidate and its specification come from an AI-assisted study. Independent human validation remains open.</p></div><div class="coverage-figure"><span>ANY-EVIDENCE FAMILY COVERAGE</span><strong>{coverage}<i>/{family_total}</i></strong><div class="coverage-bar" aria-hidden="true"><i style="width:{percent}%"></i></div><p>{percent}% of repository families in the frozen panel have at least one current cue, historical cue, or canonical source layer. <strong>This is not confirmed prevalence.</strong></p><small>Current lexical-cue repositories: {current} / {evidence['repository_total']}</small></div></div><div class="anchors">{anchors}</div><details class="retrieval-cue"><summary>Exploratory retrieval cue <span>Regex / v{esc(p['retrieval_cue']['version'])}</span></summary><div><code>{regex}</code><p>This expression is a discovery instrument only. A match is a retrieval cue—not a confirmed implementation, pointcut match, or contract proof.</p></div></details></section>

  <nav class="record-pagination" aria-label="Adjacent pattern records"><a class="previous-record" href="../{previous_id}/"><span>← Previous record</span><strong>{previous_id}</strong></a><a href="../../#catalog" class="all-records">All 52</a><a class="next-record" href="../{next_id}/"><span>Next record →</span><strong>{next_id}</strong></a></nav>
</main>"""
    return shell(f"{p['id']} · {p['name']} — POHE", f"{p['id']} {p['name']}: proposed harness pattern pointcut, contract, variation, interaction model, and source evidence.", body, prefix="../../", canonical=f"patterns/{p['id']}/")


def research_agenda() -> str:
    """Render the shared H1–H3 agenda without collapsing its evidence boundaries."""
    return """
  <section class="method-section research-agenda" id="research-agenda" aria-labelledby="research-agenda-title"><div class="method-section-number">01</div><div>
    <span class="section-index">RESEARCH AGENDA / H1—H3</span>
    <h2 id="research-agenda-title">From position to testable hypotheses.</h2>
    <p class="method-lead">Traditional software engineering was led by people; the agent harness increasingly evolves itself. Three questions follow. What is the asset when a prompt or code snapshot may be replaced within days? Must harness engineering keep the separation of concerns—and the boundary—that design patterns and POSA used to mark what rarely changes versus what may be derived, generalized, or customized? And does self-improvement with explicit variation boundaries drift less, regress less, recover faster, and attribute changes more clearly than unconstrained revision? Each question keeps its proposed answer, evidence, and falsification boundary in view.</p>
    <p class="research-manuscript-context"><a class="text-link" href="assets/paper/pohe-position.pdf">Read the paper PDF <span aria-hidden="true">↗</span></a><span>Manuscript context: §3, “Harness Design Patterns and the A² Taxonomy”; §4, “Two Major Hypotheses”; and §5, “Study Designs for the Two Hypotheses.” H1 and the field test remain open; H2’s enforcement main effect has a confirmatory apparatus result, and the coupled follow-up in §4 makes its boundary interaction estimable within the apparatus.</span></p>

    <figure class="research-map">
      <svg class="research-agenda-svg" viewBox="0 0 760 500" role="img" aria-labelledby="research-map-title research-map-description">
        <title id="research-map-title">H1–H3 in the A² research agenda</title>
        <desc id="research-map-description">A² means Activities × Assets. H1 locates stable, inspectable design commitments on the Assets axis. H2 spans activities and assets with mechanized guards, gates, checkpoints, rollback, and evaluation. H3 sits in A3, Maintenance and evolution, and proposes repeated-round comparisons of bounded and unconstrained self-improvement.</desc>
        <rect class="map-frame" x="20" y="58" width="720" height="410" rx="3"/>
        <text class="map-title" x="28" y="36">A² = Activities × Assets</text>
        <path class="map-column-rule" d="M260 58V468M500 58V468"/>
        <text class="map-activity" x="140" y="91" text-anchor="middle">A1 / DESIGN</text>
        <text class="map-activity" x="380" y="91" text-anchor="middle">A2 / VERIFY</text>
        <text class="map-activity" x="620" y="91" text-anchor="middle">A3 / EVOLVE</text>
        <rect class="map-h1" x="40" y="120" width="680" height="100" rx="3"/>
        <text class="map-hypothesis" x="60" y="150">H1 / ASSETS</text>
        <text class="map-copy" x="60" y="180">What is the asset under constant rewriting?</text>
        <text class="map-detail" x="60" y="204">answer: pointcut · contract · variation · tests · negative control</text>
        <rect class="map-h2" x="40" y="242" width="680" height="100" rx="3"/>
        <text class="map-hypothesis" x="60" y="272">H2 / ACTIVITIES × ASSETS</text>
        <text class="map-copy" x="60" y="302">Must a separation of concerns be kept?</text>
        <text class="map-detail" x="60" y="326">answer: mechanize it—guards · gates · checkpoints · rollback</text>
        <rect class="map-h3" x="510" y="364" width="210" height="82" rx="3"/>
        <text class="map-hypothesis" x="526" y="390">H3 / A3</text>
        <text class="map-copy" x="526" y="416">Bounded ↔ unconstrained</text>
        <text class="map-detail" x="526" y="438">repeated rounds</text>
      </svg>
      <figcaption><span>FIG. R / RESEARCH MAP</span><p>H1 asks which asset survives. H2 asks whether a separation of concerns must be kept and mechanized. H3 asks whether the bounded A3 seam beats unconstrained revision—a prospective comparison, not an observed benefit.</p></figcaption>
    </figure>

    <div class="hypothesis-grid">
      <article class="hypothesis-card hypothesis-h1" id="h1-asset">
        <header class="hypothesis-head"><span class="hypothesis-code">H1</span><div><span class="hypothesis-kicker">ASSET QUESTION</span><h3>What is the asset when the harness keeps rewriting itself?</h3></div></header>
        <div class="hypothesis-block"><h4>Question</h4><p>A prompt or code snapshot may be evolved and replaced within days. What, then, is the durable asset of harness engineering?</p></div>
        <div class="hypothesis-block"><h4>Our answer</h4><p>The durable asset is an inspectable design commitment—not code or prompts: <strong>pointcut + contract + permitted variation + test obligation + negative control</strong>.</p></div>
        <div class="hypothesis-block"><h4>Metrics</h4><ul><li><strong>Contract retention:</strong> protected obligations that remain explicit and satisfied after rewriting.</li><li><strong>Contract drift:</strong> unauthorized semantic change across successive versions.</li></ul></div>
        <div class="hypothesis-evidence"><h4>Evidence status</h4><p><strong>Conceptual + exploratory detection/source evidence.</strong> Existing support can motivate and locate commitments; it does not establish their utility.</p></div>
        <div class="hypothesis-boundary"><h4>Open / falsification boundary</h4><p>The causal utility remains open. The hypothesis would be weakened if matched mechanism-only descriptions retained contracts as well and drifted no more under the same rewriting regime.</p></div>
      </article>

      <article class="hypothesis-card hypothesis-h2" id="h2-agency">
        <header class="hypothesis-head"><span class="hypothesis-code">H2</span><div><span class="hypothesis-kicker">SEPARATION QUESTION</span><h3>Must harness engineering keep a separation of concerns—and a boundary?</h3></div></header>
        <div class="hypothesis-block"><h4>Question</h4><p>Design patterns and POSA separated what rarely changes from what may be derived, generalized, or customized. Must harness engineering preserve that separation of concerns and its boundary?</p></div>
        <div class="hypothesis-block"><h4>Our answer</h4><p>Yes—and under agent-led evolution the boundary must be mechanized through <strong>guards, gates, checkpoints, rollback, and evaluation gates</strong>; docs/comments alone are not constraints.</p></div>
        <div class="hypothesis-block"><h4>Metrics</h4><ul><li>Unauthorized-variation escape rate and violation containment.</li><li>Checkpoint recovery and rollback success, latency, and evaluation-gate rejection of known violations.</li></ul></div>
        <div class="hypothesis-evidence"><h4>Evidence status</h4><p><strong>Analytical/source support plus a confirmatory 12-instance apparatus run</strong> of the enforcement main effect. A coupled follow-up makes the boundary interaction estimable and supports it structurally within that apparatus; the field comparison remains open. <a href="experiments.html">See the experiments <span aria-hidden="true">↗</span></a> <a href="./?family=E#catalog">E-family records</a> are candidate mechanisms, not a demonstrated comparative advantage.</p></div>
        <div class="hypothesis-boundary"><h4>Open / falsification boundary</h4><p><strong>Comparative causal benefit remains open.</strong> A controlled comparison that finds no improvement over documentation/comment guidance in containment, recovery, or violation detection would challenge the claim.</p></div>
      </article>

      <article class="hypothesis-card hypothesis-h3" id="h3-controlled-variation">
        <header class="hypothesis-head"><span class="hypothesis-code">H3</span><div><span class="hypothesis-kicker">CONTROLLED-VARIATION QUESTION</span><h3>Do explicit variation boundaries beat unconstrained self-improvement?</h3></div></header>
        <div class="hypothesis-block"><h4>Question</h4><p>Compared with unconstrained self-improvement, does self-improvement with explicit variation boundaries yield <strong>lower contract drift, lower regression rate, faster failure recovery, and clearer change attribution</strong>?</p></div>
        <div class="hypothesis-block"><h4>Our answer</h4><p>We predict yes on all four outcomes—but this is a hypothesis to be tested, not an observed result.</p></div>
        <div class="hypothesis-block"><h4>Metrics</h4><ul><li>Contract drift and regression rate per modification round.</li><li>Time to successful recovery and completeness of change attribution.</li></ul></div>
        <div class="hypothesis-evidence"><h4>Evidence status</h4><p><strong>Prospective in the field.</strong> The frozen 12-instance apparatus design had no exposure for the boundary interaction; a coupled follow-up estimates it structurally within the apparatus, but no field comparison exists yet. <a href="experiments.html#coupled">See the coupled run <span aria-hidden="true">↗</span></a></p></div>
        <div class="hypothesis-boundary"><h4>Open / falsification boundary</h4><p>The entire causal claim is open. Equivalent or worse outcomes for bounded modification would falsify the predicted benefit under the tested conditions; mixed results would narrow, not confirm, its scope.</p></div>
      </article>
    </div>

    <aside class="research-protocol" aria-labelledby="research-protocol-title"><div><span class="section-index">FIELD PROTOCOL / PROSPECTIVE</span><h3 id="research-protocol-title">Bounded versus unconstrained, round by round.</h3></div><div><p>H3 compares bounded and unconstrained self-improvement in a planning cohort of 12 harnesses. The design uses matched randomization to bounded or unconstrained arms for ten revision rounds. The frozen task distribution covers feature adaptation, tool/schema change, failure recovery, and permission/acceptance change. Contract, regression, recovery, and attribution measures should be predeclared, with comparable tasks, models, and budgets.</p><p class="research-protocol-status"><strong>Field protocol: no field result.</strong> The 12-instance apparatus runs are reported in the paper as confirmatory apparatus results, not field tests.</p></div></aside>
  </div></section>"""


def load_experiments() -> dict:
    path = ROOT / "data/experiments.json"
    if not path.is_file():
        raise FileNotFoundError(
            "data/experiments.json is missing; run analysis/export_site_experiments.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


ARM_COLOR = {
    "MU": "var(--chart-mu)",
    "MB": "var(--chart-mb)",
    "EU": "var(--chart-eu)",
    "EB": "var(--chart-eb)",
}


def _chart_text(
    x: float, y: float, text: str, size: float = 9,
    fill: str = "var(--chart-ink)", anchor: str = "start",
    weight: str | None = None, family: str = "var(--mono)",
) -> str:
    weight_css = f";font-weight:{weight}" if weight else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'style="font-family:{family};font-size:{size}px;fill:{fill}{weight_css}">'
        f'{esc(text)}</text>'
    )


def _chart_svg(slug: str, title: str, desc: str, width: int, height: int, body: str) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{slug}-title {slug}-desc" xmlns="http://www.w3.org/2000/svg">'
        f'<title id="{slug}-title">{esc(title)}</title>'
        f'<desc id="{slug}-desc">{esc(desc)}</desc>{body}</svg>'
    )


def chart_arm_means(exp: dict) -> str:
    """Grouped small multiples: every arm mean for the frozen and coupled runs."""
    metrics = [
        ("contract_drift", "Contract drift", 4.0, 1.0),
        ("unauthorized_effects", "Unauthorized effects", 5.0, 1.0),
        ("task_success", "Contract-preserving success", 1.0, 0.01),
        ("regression_rate", "Regression rate", 0.02, 0.001),
    ]
    arms = ["MU", "MB", "EU", "EB"]
    body = [
        '<rect x="0" y="0" width="760" height="450" style="fill:transparent"/>',
        f'<rect x="12" y="16" width="14" height="9" rx="1" style="fill:var(--paper-raised);stroke:var(--chart-ink)" stroke-width="1"/>',
        _chart_text(32, 24, "frozen 2×2 · 10 rounds", 9),
        f'<rect x="182" y="16" width="14" height="9" rx="1" style="fill:var(--chart-ink)"/>',
        _chart_text(202, 24, "coupled · 11 rounds", 9),
    ]
    for index, (key, label, vmax, digits) in enumerate(metrics):
        px = 12 + (index % 2) * 378
        py = 44 + (index // 2) * 200
        left, right = px + 42, px + 358
        top, bottom = py + 36, py + 150
        plot_h = bottom - top
        body.append(_chart_text(px + 2, py + 14, label, 10.5, family="var(--sans)", weight="600"))
        body.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" style="stroke:var(--chart-grid)"/>')
        for tick in (0.0, vmax / 2, vmax):
            ty = bottom - (tick / vmax) * plot_h
            body.append(
                f'<line x1="{left}" y1="{ty:.1f}" x2="{right}" y2="{ty:.1f}" '
                f'style="stroke:var(--chart-grid);stroke-dasharray:2 3" opacity="0.7"/>'
            )
            body.append(_chart_text(left - 5, ty + 3, f"{tick:g}", 7.5, "var(--chart-faint)", "end"))
        group_w = (right - left) / 4
        for arm_index, arm in enumerate(arms):
            center = left + group_w * (arm_index + 0.5)
            for run_index, run in enumerate(("confirmatory", "coupled")):
                value = float(exp[run]["arm_means"][arm][key])
                bar_h = max(1.0, value / vmax * plot_h)
                bar_x = center - 12 + run_index * 13
                fill = "var(--paper-raised)" if run == "confirmatory" else ARM_COLOR[arm]
                stroke = ARM_COLOR[arm]
                body.append(
                    f'<rect x="{bar_x:.1f}" y="{bottom - bar_h:.1f}" width="11" height="{bar_h:.1f}" '
                    f'rx="1" style="fill:{fill};stroke:{stroke}" stroke-width="1">'
                    f'<title>{run} {arm}: {value:.4f}</title></rect>'
                )
                if run == "coupled":
                    body.append(_chart_text(
                        bar_x + 5.5, bottom - bar_h - 3,
                        f"{value:.3f}" if digits < 0.01 else f"{value:.3f}",
                        7.2, "var(--chart-faint)", "middle",
                    ))
            body.append(_chart_text(center, bottom + 14, arm, 8, "var(--chart-ink)", "middle"))
    svg = _chart_svg(
        "fig-arm-means",
        "Arm means for the frozen and coupled runs",
        "Four panels show contract drift, unauthorized effects, contract-preserving success, "
        "and regression rate for MU, MB, EU, EB. Hollow bars are the frozen ten-round run; "
        "filled bars are the coupled eleven-round run.",
        760, 450, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 2 / ARM MEANS</span><p>Hollow bars are the frozen ten-round run; '
        'filled bars are the coupled eleven-round follow-up. The coupled request moves the EU '
        'column, not the M columns, which is the interaction the design was built to expose.</p>'
        '<small>Source: pooled cell means, three samples per harness-arm.</small></figcaption></figure>'
    )


def chart_interaction(exp: dict) -> str:
    """Forest plot of the coupled boundary benefits and their interaction."""
    rows = [
        ("CONTRACT DRIFT", None, None, None, None),
        ("Boundary benefit in M", "drift_boundary_M", "var(--chart-mb)", 0, "MU − MB"),
        ("Boundary benefit in E", "drift_boundary_E", "var(--chart-eu)", 0, "EU − EB"),
        ("Interaction", "drift_interaction_E_minus_M", "var(--ink)", 1, "(EU − EB) − (MU − MB)"),
        ("UNAUTHORIZED EFFECTS", None, None, None, None),
        ("Boundary benefit in M", "unauth_boundary_M", "var(--chart-mb)", 0, "MU − MB"),
        ("Boundary benefit in E", "unauth_boundary_E", "var(--chart-eu)", 0, "EU − EB"),
        ("Interaction", "unauth_interaction_E_minus_M", "var(--ink)", 1, "(EU − EB) − (MU − MB)"),
    ]
    x0, x1 = 200.0, 560.0
    vmax = 1.15
    def sx(value: float) -> float:
        return x0 + max(0.0, min(vmax, value)) / vmax * (x1 - x0)
    body: list[str] = []
    body.append(f'<line x1="{sx(0):.1f}" y1="44" x2="{sx(0):.1f}" y2="338" style="stroke:var(--chart-grid)"/>')
    body.append(f'<line x1="{sx(0.5):.1f}" y1="44" x2="{sx(0.5):.1f}" y2="338" style="stroke:var(--accent);stroke-dasharray:4 4" opacity="0.55"/>')
    body.append(_chart_text(sx(0.5) + 4, 36, "margin 0.50", 8, "var(--accent)"))
    y = 74
    for label, key, color, bold, contrast in rows:
        if key is None:
            body.append(_chart_text(14, y - 6, label, 8.5, "var(--accent)", weight="700"))
            y += 30
            continue
        item = exp["coupled"]["estimands"][key]
        estimate = float(item["estimate"])
        low, high = item["ci975"]
        body.append(f'<line x1="{sx(low):.1f}" y1="{y}" x2="{sx(high):.1f}" y2="{y}" style="stroke:{color}" stroke-width="1.6" opacity="0.7"/>')
        body.append(f'<circle cx="{sx(estimate):.1f}" cy="{y}" r="4.2" style="fill:{color}"/>')
        body.append(_chart_text(190, y + 3, label, 8.8, "var(--chart-ink)", "end", "700" if bold else None))
        body.append(_chart_text(574, y + 3, f"{estimate:+.3f}", 8.8, color, weight="700" if bold else None))
        body.append(_chart_text(624, y + 3, f"[{low:.3f}, {high:.3f}]", 8, "var(--chart-faint)"))
        if contrast:
            body.append(_chart_text(190, y + 12, contrast, 7.2, "var(--chart-faint)", "end"))
        y += 34
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        body.append(f'<line x1="{sx(tick):.1f}" y1="338" x2="{sx(tick):.1f}" y2="344" style="stroke:var(--chart-grid)"/>')
        body.append(_chart_text(sx(tick), 356, f"{tick:.2f}", 7.5, "var(--chart-faint)", "middle"))
    body.append(_chart_text(x0, 374, "boundary benefit (lower is worse, higher is better)", 8, "var(--chart-faint)"))
    svg = _chart_svg(
        "fig-interaction",
        "Coupled boundary benefits and interaction",
        "A forest plot of boundary benefits in the coupled run for contract drift and "
        "unauthorized effects, with the interaction and simultaneous intervals. The interaction "
        "rows clear the frozen 0.50 margin.",
        760, 390, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 4 / INTERACTION</span><p>Boundary benefit is U minus B within '
        'each mode. In E the gate preserves the guard policy; in M the policy has no runtime '
        'enforcer, so the same request cannot move outcomes. Bars are simultaneous 97.5% '
        'intervals; the dashed line is the frozen 0.50 margin.</p>'
        '<small>Source: coupled decision.json, 2,000 harness-clustered bootstraps.</small></figcaption></figure>'
    )


def _heat_bin(value: float, vmax: float) -> int:
    if value <= 0.005:
        return 0
    return min(4, max(1, int(math.ceil(value / vmax * 4))))


def chart_heatmap(exp: dict) -> str:
    """Per-harness, per-arm heatmaps for the coupled run."""
    harnesses = exp["coupled"]["harnesses"]
    arms = ["MU", "MB", "EU", "EB"]
    blocks = [
        ("Contract drift", "contract_drift", 2.5, 130.0, 50.0),
        ("Unauthorized effects", "unauthorized_effects", 4.2, 480.0, 50.0),
    ]
    y0, rowh = 92.0, 26.0
    body: list[str] = []
    for label, key, vmax, x0, colw in blocks:
        body.append(_chart_text(x0, 52, label, 10.5, family="var(--sans)", weight="600"))
        for col, arm in enumerate(arms):
            body.append(_chart_text(x0 + colw * (col + 0.5), 74, arm, 8, "var(--chart-faint)", "middle"))
        for row, harness in enumerate(harnesses):
            y = y0 + row * rowh
            for col, arm in enumerate(arms):
                value = float(harness["arms"][arm][key])
                b = _heat_bin(value, vmax)
                fill = f"var(--heat-{b})"
                text_fill = "var(--paper)" if b >= 3 else "var(--ink)"
                body.append(
                    f'<rect x="{x0 + col * colw + 1:.1f}" y="{y + 1:.1f}" '
                    f'width="{colw - 2:.1f}" height="{rowh - 2:.1f}" rx="2" '
                    f'style="fill:{fill}"><title>{harness["id"]} {arm} {label}: {value:.3f}</title></rect>'
                )
                body.append(_chart_text(
                    x0 + col * colw + colw / 2, y + rowh / 2 + 3,
                    "0" if value < 0.005 else f"{value:.1f}",
                    8, text_fill, "middle",
                ))
    for row, harness in enumerate(harnesses):
        y = y0 + row * rowh
        body.append(_chart_text(122, y + 17, harness["id"], 8, "var(--chart-ink)", "end"))
        body.append(_chart_text(472, y + 17, harness["id"], 8, "var(--chart-ink)", "end"))
    body.append(_chart_text(130, 424, "0", 8, "var(--chart-faint)"))
    for i in range(5):
        body.append(f'<rect x="{150 + i * 22}" y="414" width="16" height="12" rx="2" style="fill:var(--heat-{i});stroke:var(--rule-soft)"/>')
    body.append(_chart_text(266, 424, "max 2.5", 8, "var(--chart-faint)"))
    body.append(_chart_text(480, 424, "0", 8, "var(--chart-faint)"))
    for i in range(5):
        body.append(f'<rect x="{500 + i * 22}" y="414" width="16" height="12" rx="2" style="fill:var(--heat-{i});stroke:var(--rule-soft)"/>')
    body.append(_chart_text(616, 424, "max 4.2", 8, "var(--chart-faint)"))
    svg = _chart_svg(
        "fig-heatmap",
        "Per-harness coupled outcomes by arm",
        "Twelve harnesses by four arms, with contract drift on the left and unauthorized "
        "effects on the right. The EB column is empty; the EU column carries the narrowed "
        "guard policy; the M columns remain exposed.",
        760, 440, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 6 / PER-HARNESS EXPOSURE</span><p>Every harness shows the same '
        'pattern: EB contains the round-eleven change, EU does not, and both M arms were already '
        'exposed because they have no runtime guard. H03’s EU cell is 0.67 because one of its '
        'three samples declined the request.</p>'
        '<small>Source: coupled pooled cell means; colour scales differ between panels.</small></figcaption></figure>'
    )


def chart_round11(exp: dict) -> str:
    """Compliance audit for the coupled round-eleven request."""
    audit = exp["coupled"]["round11"]
    harnesses = [f"H{index:02d}" for index in range(1, 13)]
    x0, x1 = 118.0, 612.0
    y0, rowh = 92.0, 25.0
    body: list[str] = []
    body.append(_chart_text(x0, 56, "EU promoted", 8.5, "var(--chart-eu)"))
    body.append(_chart_text(x0 + 150, 56, "EB policy-rejected", 8.5, "var(--chart-mb)"))
    body.append(_chart_text(x0 + 330, 56, "MB policy-rejected", 8.5, "var(--chart-mu)"))
    body.append(_chart_text(x0, 72, "0", 7.5, "var(--chart-faint)"))
    body.append(_chart_text(x1, 72, "3 samples", 7.5, "var(--chart-faint)", "end"))
    for index, harness in enumerate(harnesses):
        y = y0 + index * rowh
        item = audit[harness]
        body.append(_chart_text(110, y + 10, harness, 8, "var(--chart-ink)", "end"))
        for bar_index, (count_key, color) in enumerate((
            ("EU_promoted", "var(--chart-eu)"),
            ("EB_rejected", "var(--chart-mb)"),
            ("MB_rejected", "var(--chart-mu)"),
        )):
            count = int(item[count_key])
            by = y + bar_index * 6.5
            body.append(f'<rect x="{x0}" y="{by}" width="{x1 - x0}" height="4.5" rx="2" style="fill:var(--heat-0)"/>')
            body.append(f'<rect x="{x0}" y="{by}" width="{(x1 - x0) * count / 3:.1f}" height="4.5" rx="2" style="fill:{color}"/>')
        body.append(_chart_text(622, y + 11, f"{item['EU_promoted']} · {item['EB_rejected']} · {item['MB_rejected']}", 7.5, "var(--chart-faint)"))
        if item["declined"]:
            body.append(_chart_text(700, y + 11, f"{item['declined']} declined", 7.5, "var(--warn)"))
    svg = _chart_svg(
        "fig-round11",
        "Round-eleven promotion audit",
        "For every harness, the number of samples in which EU promoted the guard-policy "
        "narrowing, EB rejected it for a policy mismatch, and MB rejected it. H03 has one "
        "declined sample.",
        760, 420, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 8 / ROUND-ELEVEN AUDIT</span><p>The audit is part of the result: '
        'the contrast is produced by the shared obligation, not by different proposals across '
        'arms. EB and MB reject the same policy mismatch; only EU loses command containment. '
        'H03 contributes one declined sample and remains in its arm.</p>'
        '<small>Source: run_s1–s3 events.jsonl and plans_s1–s3.</small></figcaption></figure>'
    )


def chart_timeline(exp: dict) -> str:
    """Two change sequences: the frozen ten rounds and the coupled eleven."""
    body: list[str] = []
    tracks = [
        ("FROZEN 2×2", exp["sequence"]["confirmatory"], 86.0),
        ("COUPLED", exp["sequence"]["coupled"], 182.0),
    ]
    for label, sequence, y in tracks:
        body.append(_chart_text(20, y + 4, label, 9.5, "var(--chart-ink)", weight="700"))
        x_start, x_end = 150.0, 700.0
        step = (x_end - x_start) / (len(sequence) - 1)
        body.append(f'<line x1="{x_start}" y1="{y}" x2="{x_end}" y2="{y}" style="stroke:var(--chart-grid)"/>')
        for index, item in enumerate(sequence):
            cx = x_start + index * step
            round_number = int(item["round"])
            coupled_round = label == "COUPLED" and round_number == 11
            fill = "var(--accent)" if coupled_round else ("var(--warn)" if not item["in_seam"] else "var(--paper-raised)")
            stroke = "var(--accent)" if coupled_round else ("var(--warn)" if not item["in_seam"] else "var(--chart-ink)")
            radius = 11 if coupled_round else 8.5
            body.append(
                f'<circle cx="{cx:.1f}" cy="{y}" r="{radius}" style="fill:{fill};stroke:{stroke}" stroke-width="1.4">'
                f'<title>Round {round_number}: {item["name"]}{" (out of seam)" if not item["in_seam"] else ""}</title></circle>'
            )
            body.append(_chart_text(cx, y + 3, str(round_number), 7.5 if not coupled_round else 8.5,
                                    "var(--paper)" if coupled_round or not item["in_seam"] else "var(--chart-ink)", "middle"))
        if label == "COUPLED":
            body.append(_chart_text(700, y + 28, "round 11: guard policy narrowing", 8, "var(--accent)", "end"))
    body.append(f'<circle cx="20" cy="238" r="6" style="fill:var(--paper-raised);stroke:var(--chart-ink)"/>')
    body.append(_chart_text(32, 242, "in seam", 8))
    body.append(f'<circle cx="102" cy="238" r="6" style="fill:var(--warn);stroke:var(--warn)"/>')
    body.append(_chart_text(114, 242, "out of seam", 8))
    body.append(f'<circle cx="218" cy="238" r="6" style="fill:var(--accent);stroke:var(--accent)"/>')
    body.append(_chart_text(230, 242, "coupled round", 8))
    svg = _chart_svg(
        "fig-timeline",
        "Change sequences for the frozen and coupled runs",
        "Ten frozen rounds and eleven coupled rounds. Rounds seven through ten are out of "
        "seam; round eleven is an in-seam request that narrows the guard policy.",
        760, 260, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 1 / CHANGE SEQUENCE</span><p>The coupled run keeps the frozen ten '
        'rounds and adds one in-seam request. Rounds seven through ten remain the out-of-seam '
        'proposals that the frozen authority rejects in every arm.</p>'
        '<small>Source: protocol/change_sequence.json and protocol/change_sequence_coupled.json.</small></figcaption></figure>'
    )


def chart_trajectory(exp: dict) -> str:
    """Round-zero baseline versus final state, by arm, for the coupled run."""
    coupled = exp["coupled"]
    panels = [
        ("contract_drift", "Contract drift", 4.3),
        ("unauthorized_effects", "Unauthorized effects", 4.6),
    ]
    label_offset = {"MU": -10, "MB": 12, "EU": -10, "EB": -10}
    body: list[str] = []
    for index, (key, label, vmax) in enumerate(panels):
        left = 24 + index * 378
        right = left + 330
        top, bottom = 82.0, 244.0
        plot_h = bottom - top
        x0, x1 = left + 44, right - 26
        body.append(_chart_text(left, 62, label, 10.5, family="var(--sans)", weight="600"))
        body.append(f'<line x1="{x0}" y1="{bottom}" x2="{x1}" y2="{bottom}" style="stroke:var(--chart-grid)"/>')
        for tick in (0.0, 2.0, 4.0):
            ty = bottom - (tick / vmax) * plot_h
            body.append(f'<line x1="{x0}" y1="{ty:.1f}" x2="{x1}" y2="{ty:.1f}" style="stroke:var(--chart-grid);stroke-dasharray:2 3" opacity="0.7"/>')
            body.append(_chart_text(x0 - 6, ty + 3, f"{tick:g}", 7.5, "var(--chart-faint)", "end"))
        for arm in ("MU", "MB", "EU", "EB"):
            start = float(coupled["round0"][arm][key])
            end = float(coupled["arm_means"][arm][key])
            # MU and MB coincide at both ends; a 2px split keeps both visible.
            nudge = -2.0 if arm == "MU" else (2.0 if arm == "MB" else 0.0)
            y0 = bottom - (start / vmax) * plot_h + nudge
            y1 = bottom - (end / vmax) * plot_h + nudge
            color = ARM_COLOR[arm]
            dash = ' stroke-dasharray="6 3"' if arm.startswith("M") else ""
            body.append(
                f'<line x1="{x0}" y1="{y0:.1f}" x2="{x1}" y2="{y1:.1f}" '
                f'style="stroke:{color};stroke-width:2;opacity:0.95"{dash}/>'
            )
            hollow = "var(--paper-raised)" if arm.startswith("M") else color
            body.append(
                f'<circle cx="{x0}" cy="{y0:.1f}" r="3.6" style="fill:{hollow};stroke:{color}" stroke-width="1.4">'
                f'<title>{arm} round 0: {start:.3f}</title></circle>'
            )
            body.append(
                f'<circle cx="{x1}" cy="{y1:.1f}" r="3.6" style="fill:{color};stroke:{color}" stroke-width="1.4"/>'
            )
            body.append(_chart_text(x1 + 8, y1 + 3 + label_offset[arm], f"{arm} {end:.2f}", 7.2, color))
        body.append(_chart_text(x0, bottom + 14, "round 0", 7.5, "var(--chart-faint)", "middle"))
        body.append(_chart_text(x1, bottom + 14, "round 11", 7.5, "var(--chart-faint)", "middle"))
    body.append(_chart_text(24, 22, "dashed = M (convention) · solid = E (enforced) · hollow start = round-zero probe", 8.5, "var(--chart-faint)"))
    svg = _chart_svg(
        "fig-trajectory",
        "Round-zero baseline versus round eleven by arm",
        "Two panels plot contract drift and unauthorized effects at round zero and round "
        "eleven for each arm. M arms begin and end high; E arms begin clean and only EU "
        "rises after the round-eleven promotion.",
        760, 280, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 3 / BASELINE TO FINAL</span><p>Enforcement separates the arms '
        'before any evolution: the M arms fail probes and adversarial tasks at round zero, the '
        'E arms pass. The only movement after that is EU, where the promoted round-eleven '
        'request narrows the guard policy. EB stays at zero; MU and MB stay at their baseline.</p>'
        '<small>Source: pooled runs.csv round0_* columns and decision.json arm means.</small></figcaption></figure>'
    )


def chart_samples(exp: dict) -> str:
    """Interaction estimate in each independent agent sample, with the margin."""
    samples = ["s1", "s2", "s3"]
    pooled = exp["coupled"]["estimands"]
    x0, x1 = 150.0, 560.0
    y0, rowh = 88.0, 40.0
    body: list[str] = []
    body.append(_chart_text(24, 22, "coupled interaction by independent sample", 9, "var(--chart-ink)", weight="600"))
    body.append(_chart_text(x1 + 40, 22, "drift / unauthorized", 8, "var(--chart-faint)"))
    body.append(_chart_text(24, y0 - 24, "frozen design: 0.000 in every sample", 8, "var(--chart-faint)"))
    for tick in (0.0, 0.5, 1.0):
        tx = x0 + tick * (x1 - x0)
        body.append(f'<line x1="{tx:.1f}" y1="{y0 - 18}" x2="{tx:.1f}" y2="{y0 + 3 * rowh + 14}" style="stroke:var(--chart-grid);stroke-dasharray:2 3" opacity="0.55"/>')
        if tick != 0.5:
            body.append(_chart_text(tx, y0 - 24, f"{tick:g}", 7.5, "var(--chart-faint)", "middle"))
    margin_x = x0 + 0.5 * (x1 - x0)
    body.append(f'<line x1="{margin_x}" y1="{y0 - 30}" x2="{margin_x}" y2="{y0 + 3 * rowh + 20}" style="stroke:var(--warn);stroke-width:1.4;stroke-dasharray:4 3"/>')
    body.append(_chart_text(margin_x + 4, y0 + 3 * rowh + 28, "frozen margin 0.50", 7.5, "var(--warn)"))
    drift_key = "drift_interaction_E_minus_M"
    unauth_key = "unauth_interaction_E_minus_M"
    labels = [
        (sample,
         float(exp["coupled"]["per_sample"][sample][drift_key]),
         float(exp["coupled"]["per_sample"][sample][unauth_key]))
        for sample in samples
    ]
    labels.append(("pooled", float(pooled[drift_key]["estimate"]),
                   float(pooled[unauth_key]["estimate"])))
    for index, (name, drift, unauth) in enumerate(labels):
        base = y0 + index * rowh
        body.append(_chart_text(x0 - 18, base + 3, name, 8.5, "var(--chart-ink)", "end", weight="600" if name == "pooled" else None))
        dx, ux = x0 + drift * (x1 - x0), x0 + unauth * (x1 - x0)
        body.append(f'<circle cx="{dx:.1f}" cy="{base - 5:.1f}" r="3.8" style="fill:var(--chart-mb);stroke:var(--chart-mb)"/>')
        body.append(f'<rect x="{ux - 3.3:.1f}" y="{base + 1.7:.1f}" width="6.6" height="6.6" rx="1" style="fill:var(--chart-eb);stroke:var(--chart-eb)"/>')
        body.append(_chart_text(x1 + 30, base + 3, f"{drift:.2f} / {unauth:.2f}", 7.6, "var(--chart-ink)", family="var(--mono)"))
    body.append(_chart_text(24, y0 + 3 * rowh + 28, "circle = drift · square = unauthorized", 8, "var(--chart-faint)"))
    svg = _chart_svg(
        "fig-samples",
        "Interaction estimate in each independent agent sample",
        "Per-sample boundary interaction for contract drift and unauthorized effects, with "
        "the pooled estimate and the frozen 0.50 margin. Each sample clears the margin.",
        760, 250, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 5 / SAMPLE STABILITY</span><p>The interaction is not carried by one '
        'agent sample. All three per-sample estimates sit between 0.92 and 1.00, above the frozen '
        '0.50 margin, and the pooled estimate falls inside that range. Each sample is a different '
        'set of arm-blind plans replayed over the same cohort.</p>'
        '<small>Source: decision.json per_sample block, three samples.</small></figcaption></figure>'
    )


def chart_decisions(exp: dict) -> str:
    """Every promotion decision across the eleven-round sequence, by arm."""
    decisions = exp["coupled"]["round_decisions"]
    rounds = [str(number) for number in range(1, 12)]
    arms = ["MU", "MB", "EU", "EB"]
    buckets = [
        ("promoted", "var(--chart-ok)"),
        ("gate", "var(--chart-gate)"),
        ("authority", "var(--chart-authority)"),
        ("declined", "var(--chart-idle)"),
    ]
    left, top = 78.0, 96.0
    cell_w, cell_h = 58.0, 52.0
    bar_w = 20.0
    body: list[str] = []
    body.append(_chart_text(24, 22, "36 replica-rounds per cell · three samples", 8.5, "var(--chart-faint)"))
    # Out-of-seam band over rounds 7--10.
    band_x = left + 6 * cell_w - 3
    body.append(f'<rect x="{band_x:.1f}" y="{top - 26}" width="{4 * cell_w + 6}" height="{4 * cell_h + 30}" rx="3" style="fill:var(--paper-sunk);stroke:var(--chart-grid);stroke-dasharray:3 3"/>')
    body.append(_chart_text(band_x + (4 * cell_w) / 2, top - 12, "out of seam", 7.5, "var(--chart-faint)", "middle"))
    for column, number in enumerate(rounds):
        cx = left + column * cell_w + cell_w / 2
        body.append(_chart_text(cx, top - 2, f"R{number}", 7.5, "var(--chart-faint)", "middle"))
    for row, arm in enumerate(arms):
        cy = top + row * cell_h
        body.append(_chart_text(left - 10, cy + cell_h / 2 + 3, arm, 8.5, "var(--chart-ink)", "end", weight="700"))
        for column, number in enumerate(rounds):
            counts = decisions.get(arm, {}).get(number, {})
            total = sum(int(counts.get(key, 0)) for key, _ in buckets) or 1
            cx = left + column * cell_w + cell_w / 2
            base = cy + cell_h - 8
            for key, color in buckets:
                count = int(counts.get(key, 0))
                if not count:
                    continue
                height = count / total * (cell_h - 16)
                base -= height
                body.append(
                    f'<rect x="{cx - bar_w / 2:.1f}" y="{base:.1f}" width="{bar_w}" height="{height:.1f}" '
                    f'style="fill:{color}"><title>{arm} round {number} {key}: {count}</title></rect>'
                )
            body.append(_chart_text(cx, cy + cell_h + 4, str(total), 6.6, "var(--chart-faint)", "middle"))
    legend_y = top + 4 * cell_h + 26
    legend_x = left
    for label, color in buckets:
        body.append(f'<rect x="{legend_x}" y="{legend_y - 8}" width="9" height="9" rx="1" style="fill:{color}"/>')
        body.append(_chart_text(legend_x + 14, legend_y, label, 8, "var(--chart-ink)"))
        legend_x += 118
    svg = _chart_svg(
        "fig-decisions",
        "Promotion decisions across the eleven-round sequence",
        "For each arm and round, a stacked bar of promotion outcomes across 36 replica-rounds: "
        "promoted, refused by the independent gate, refused by the frozen authority as out of "
        "seam, or declined by the agent. Rounds seven through ten are out of seam.",
        760, 340, "".join(body),
    )
    return (
        '<figure class="chart-figure"><div class="chart-scroll">' + svg + '</div>' +
        '<figcaption><span>FIG. 7 / DECISIONS BY ROUND</span><p>The sequence carries most of the '
        'rejections: rounds seven through ten are out-of-seam proposals that the frozen authority '
        'refuses in every arm, and round two is a uniform decline. Round eleven is the only '
        'in-seam round where the arms diverge: U promotes, B refuses at the gate, and M promotes '
        'without a runtime consequence.</p>'
        '<small>Source: run_s1–s3 events.jsonl, bucketed by promotion outcome.</small></figcaption></figure>'
    )


def render_experiments() -> str:
    exp = load_experiments()
    body = f"""
<main id="main-content" class="method-page experiments-page">
  <header class="method-hero experiments-hero"><span class="section-index">EXPERIMENTS / EVOLUTION / APPARATUS</span><h1>Two runs.<br><em>One apparatus.</em></h1><p>A pre-registered 2×2 factorial over twelve harness instances, and a coupled follow-up that makes the boundary interaction estimable. Both execute real harnesses; neither is a field result, and neither estimates how model populations behave.</p><div class="method-actions"><a class="button button-primary" href="assets/paper/pohe-position.pdf" download>Read the paper PDF <span aria-hidden="true">↓</span></a><a class="text-link" href="#coupled">The coupled follow-up <span aria-hidden="true">↓</span></a><a class="text-link" href="#confirmatory">The frozen run <span aria-hidden="true">↓</span></a></div><p class="paper-hero-note">Evidence status: apparatus-confirmatory. Twelve configured instances of two templates, one model snapshot, and a frozen human-authority policy. The field experiment remains prospective.</p></header>

  <section class="method-section" id="apparatus"><div class="method-section-number">01</div><div>
    <h2>What the apparatus holds fixed.</h2>
    <p class="method-lead">The design separates two questions: whether a runtime guard protects contracts that instructions alone do not, and whether explicit variation boundaries add benefit once enforcement is present.</p>
    <div class="fact-grid">
      <div><dt>Cohort</dt><dd>12 harnesses · Python and JavaScript · two protected records</dd></div>
      <div><dt>Arms</dt><dd>MU · MB · EU · EB · matched replicas per harness</dd></div>
      <div><dt>Agent samples</dt><dd>3 independent per-round samples · 36 plans</dd></div>
      <div><dt>Replicas</dt><dd>144 · 1,440 proposal slots</dd></div>
      <div><dt>Margins</dt><dd>Frozen before the matrices ran</dd></div>
      <div><dt>Authority</dt><dd>Frozen policy in every arm · out-of-seam changes rejected</dd></div>
    </div>
    <div class="experiment-steps">
      <div><b>Propose</b>A per-round agent sees the state and the request, never the arm, probes, or evaluator.</div>
      <div><b>Replay</b>One plan per harness is replayed unchanged across all four matched replicas.</div>
      <div><b>Promote</b>U promotes directly; B checks the seam, records, tests, and an independent gate.</div>
      <div><b>Measure</b>Contract drift, regression, unauthorized effects, and contract-preserving success.</div>
    </div>
    {chart_timeline(exp)}
  </div></section>

  <section class="method-section" id="confirmatory"><div class="method-section-number">02</div><div>
    <span class="section-index">RUN 01 / PRE-REGISTERED / 10 ROUNDS</span>
    <h2>The frozen 2×2 run.</h2>
    <p>The confirmatory layer froze practical margins, the decision rule, and a human-authority policy before the matrices ran. An out-of-seam change is refused whether the replica is MU, MB, EU, or EB; the agent cannot approve its own exception. The evolution agent is one local model snapshot, reported by the CLI as <code>deepseek/deepseek-flash</code>.</p>
    <div class="table-wrap"><table class="metric-table">
      <thead><tr><th>Arm</th><th class="num">Contract drift</th><th class="num">Regression</th><th class="num">Unauthorized effects</th><th class="num">CPS</th></tr></thead>
      <tbody>
        <tr><td>MU</td><td class="num">2.028</td><td class="num">0.005</td><td class="num">4.000</td><td class="num">0.995</td></tr>
        <tr><td>MB</td><td class="num">2.000</td><td class="num">0.000</td><td class="num">4.000</td><td class="num">1.000</td></tr>
        <tr><td>EU</td><td class="num">0.028</td><td class="num">0.005</td><td class="num">0.000</td><td class="num">0.995</td></tr>
        <tr><td>EB</td><td class="num">0.000</td><td class="num">0.000</td><td class="num">0.000</td><td class="num">1.000</td></tr>
      </tbody>
    </table></div>
    <div class="verdict-row"><span class="verdict yes">Enforcement supported</span><span class="verdict open">Interaction: no exposure</span><span class="verdict open">Field: prospective</span></div>
    <p><strong>Enforcement.</strong> Contract-preserving success is equal at 0.995 in both modes, and M produces 4.000 more unauthorized effects per replica than E (simultaneous 97.5% interval 4.000 to 4.000; margin 0.50). Round zero already separates the arms: M fails two of four probes and all four adversarial tasks before any evolution, so the main effect is a property of the runtime guard rather than accumulated change.</p>
    <div class="review-boundary experiment-boundary"><h3>Why the interaction is zero here</h3><p>The promotion gate runs the same held-out tasks in E and M, and the external guard acts only at runtime; it does not enter promotion. Under the frozen authority, the out-of-seam requests that produce drift in the pilot are rejected in every arm. The zero is a failed manipulation check, not evidence that boundaries add nothing.</p></div>
  </div></section>

  <section class="method-section" id="coupled"><div class="method-section-number">03</div><div>
    <span class="section-index">RUN 02 / COUPLED FOLLOW-UP / 11 ROUNDS</span>
    <h2>Making the interaction estimable.</h2>
    <p>The frozen design cannot estimate the boundary interaction because the guard and the gate act on disjoint mechanisms. The coupled follow-up makes them share one obligation: the external guard's own protection policy, <code>guard_policy.protected_actions</code>. The guard enforces that policy at runtime; the gate compares a candidate policy with the immutable baseline contract and rejects any weakening. Round eleven is an in-seam request to stop blocking commands outside the allowlist while keeping workspace write containment. Filesystem containment stays an internal permission obligation; command allowlisting is enforced only by the guard, so the same promoted change has a runtime effect in E and none in M.</p>
    {chart_arm_means(exp)}
    <div class="table-wrap"><table class="metric-table">
      <thead><tr><th>Arm</th><th class="num">Contract drift</th><th class="num">Regression</th><th class="num">Unauthorized effects</th><th class="num">CPS</th></tr></thead>
      <tbody>
        <tr><td>MU</td><td class="num">2.028</td><td class="num">0.005</td><td class="num">4.000</td><td class="num">0.995</td></tr>
        <tr><td>MB</td><td class="num">2.000</td><td class="num">0.000</td><td class="num">4.000</td><td class="num">1.000</td></tr>
        <tr><td>EU</td><td class="num">1.000</td><td class="num">0.005</td><td class="num">0.972</td><td class="num">0.995</td></tr>
        <tr><td>EB</td><td class="num">0.000</td><td class="num">0.000</td><td class="num">0.000</td><td class="num">1.000</td></tr>
      </tbody>
    </table></div>
    <div class="verdict-row"><span class="verdict yes">Enforcement supported</span><span class="verdict yes">Interaction supported</span><span class="verdict open">Apparatus only</span></div>
    {chart_trajectory(exp)}
    {chart_interaction(exp)}
    <p><strong>Result.</strong> The drift interaction is 0.972 (simultaneous 0.889 to 1.000; pair-clustered 95% 0.917 to 1.000) and the unauthorized-effects interaction is 0.972 with the same intervals, both above the frozen 0.50 margin. The boundary benefit is 1.000 in E and 0.028 in M for drift, and 0.972 versus 0.000 for unauthorized effects. Regression is descriptive and its interaction is 0.000.</p>
    {chart_samples(exp)}
    {chart_heatmap(exp)}
    {chart_decisions(exp)}
    {chart_round11(exp)}
    <p><strong>Exposure audit.</strong> In every non-declined sample the round-eleven narrowing is promoted in EU, rejected in EB and MB with a recorded guard-policy mismatch, and promoted in MU. One of thirty-six round-eleven proposals was declined; its replicas remain in their arm as non-exposure rather than being dropped.</p>
    <div class="review-boundary experiment-boundary"><h3>Read this estimate narrowly</h3><p>The result is structural. It shows that the instrument can express a boundary-by-enforcement interaction when the two mechanisms share an obligation, and that the gate preserves the guard policy supplying it. It is not a field interaction, not a model-population estimate, and not a revision of the frozen confirmatory result. The revision that retargeted round eleven from write containment to command containment happened before any confirmatory matrix, is recorded in the protocol, and did not change the frozen margins.</p></div>
    <p class="experiment-link"><a class="text-link" href="paper.html#research-agenda">H2 in the research agenda <span aria-hidden="true">↗</span></a><span>Full account: paper appendix, “Coupled Follow-Up: An Estimable Boundary Interaction.”</span></p>
  </div></section>

  <section class="method-section" id="limits"><div class="method-section-number">04</div><div>
    <h2>What these experiments do not show.</h2>
    <ul class="limitations-list"><li>They are not field tests. The cohort is 12 configured instances of two templates, not the frozen 120-slot field task bank.</li><li>They do not estimate model-population variation. The evolution agent is one model snapshot reported as <code>deepseek/deepseek-flash</code>.</li><li>They do not replace case-by-case human authority. The authority is a frozen policy that rejects out-of-seam changes.</li><li>The coupled estimate is structural. It demonstrates that the mechanism can be made estimable; it does not show that boundaries help in general.</li><li>No causal performance, safety, cost, or reliability benefit in real repositories follows from these runs.</li></ul>
  </div></section>

  <section class="method-section" id="reproduce"><div class="method-section-number">05</div><div>
    <h2>Reproduce.</h2>
    <p>The experiment package is the companion <code>pohe-experiments</code> directory. Plans are fixed inputs: rerunning a sample replays them byte for byte, and the run tables are deterministic.</p>
    <pre class="command-block"><code># Pre-registered confirmatory run
python3 runner/generate_online_plans.py --out-root results_confirmatory --samples 3 --workers 6
for s in 1 2 3; do
  python3 runner/run_phase3.py --plans results_confirmatory/plans_s$s \\
    --out results_confirmatory/run_s$s --confirmatory \\
    --authority-policy protocol/authority_policy.json
done
python3 analysis/pool_confirmatory.py --root results_confirmatory --samples 3
python3 analysis/confirmatory_decision.py --root results_confirmatory --samples 3

# Coupled follow-up
python3 runner/generate_coupled_round.py --samples 3 --workers 6
for s in 1 2 3; do
  python3 runner/run_phase3.py --plans results_coupled/plans_s$s \\
    --templates templates_coupled \\
    --sequence protocol/change_sequence_coupled.json \\
    --confirmatory --coupled \\
    --authority-policy protocol/authority_policy.json \\
    --out results_coupled/run_s$s
done
python3 analysis/pool_confirmatory.py --root results_coupled --samples 3
python3 analysis/coupled_decision.py --root results_coupled --samples 3
python3 analysis/export_site_experiments.py</code></pre>
    <p>Outputs: <code>results_confirmatory/decision.md</code> and <code>results_coupled/decision.md</code> carry the pooled arm means, estimands, intervals, decisions, and exposure audits. The run manifests record protocol hashes and the authority scope.</p>
  </div></section>
</main>"""
    return shell("Evolution experiments — POHE", "Two executed POHE experiments: the pre-registered 2×2 harness-evolution run and the coupled follow-up that makes the boundary interaction estimable.", body, current="experiments", canonical="experiments.html")


def render_paper() -> str:
    body = f"""
<main id="main-content" class="method-page paper-page">
  <header class="method-hero paper-hero"><span class="section-index">PAPER / POSITION / CONFIRMATORY APPARATUS RUN</span><h1>Agent harnesses need<br><em>a pattern discipline.</em></h1><p>Scaling alone cannot absorb their system responsibilities. The position paper asks three questions—what is the asset under constant rewriting, must a separation of concerns be kept, and do explicit variation boundaries improve self-improvement—and develops the A² taxonomy, a pre-registered 2×2 apparatus run that supports the enforcement main effect, and the field tests that remain open.</p><div class="method-actions"><a class="button button-primary" href="assets/paper/pohe-position.pdf" download>Download the paper PDF <span aria-hidden="true">↓</span></a><a class="text-link" href="assets/paper/pohe-position.pdf">Open the PDF <span aria-hidden="true">↗</span></a><a class="text-link" href="#research-agenda">Research agenda <span aria-hidden="true">↓</span></a></div><p class="paper-hero-note">Unpublished manuscript. A pre-registered 12-instance apparatus run supports the enforcement main effect. A coupled follow-up makes its boundary interaction estimable and supports it structurally within the same apparatus; the field tests remain prospective. <a class="text-link" href="experiments.html">See the experiments <span aria-hidden="true">↗</span></a></p></header>

{research_agenda()}
</main>"""
    return shell("Paper & research agenda — POHE", "The POHE position paper and its H1–H3 research agenda: stable assets, mechanized agency, controlled variation, a confirmatory apparatus run, and prospective field falsification protocols.", body, current="paper", canonical="paper.html")


def render_method(data: dict) -> str:
    hash_rows = "".join(f"<tr><td><code>{esc(path)}</code></td><td><code class=\"hash\">{esc(digest)}</code></td></tr>" for path, digest in data["source_hashes"].items())
    body = f"""
<main id="main-content" class="method-page">
  <header class="method-hero"><span class="section-index">METHOD / EVIDENCE / REPRODUCIBILITY</span><h1>Claims should carry<br><em>their own boundaries.</em></h1><p>This site is a static rendering of a proposed pattern discipline and an exploratory repository survey. It deliberately separates a useful record from the evidence that supports it.</p><div class="method-actions"><a class="button button-primary" href="paper.html">Open the paper <span aria-hidden="true">↗</span></a><a class="text-link" href="paper.html#research-agenda">Research agenda <span aria-hidden="true">↓</span></a><a class="text-link" href="./#catalog">Return to the catalog</a></div></header>

  <section class="method-section" id="evidence-key"><div class="method-section-number">01</div><div><h2>Evidence key</h2><p class="method-lead">A pattern label, a lexical cue, a source-located mechanism, and a reviewed contract are not interchangeable.</p><div class="evidence-ladder"><div><span>E0 / PROPOSED</span><h3>Pattern specification</h3><p>The context, trigger, structure, effect, contract, variation, test obligation, and negative control define a candidate mechanism. A specification does not assert repository conformance.</p></div><div><span>E1 / RETRIEVAL</span><h3>Exploratory retrieval cue</h3><p>A frozen regular expression or structural matcher finds a place to inspect. A hit is not a confirmed implementation. Coverage counts any available evidence layer and is not a prevalence estimate.</p></div><div><span>E2 / SOURCE</span><h3>Source-located exemplar</h3><p>A repository, immutable commit, and implementation locator identify a concrete example. The atlas holds at most one canonical exemplar per candidate, so it cannot measure cross-repository contract depth.</p></div><div><span>E3 / CONTRACT</span><h3>Executable or contract support</h3><p>Ordering, errors, or boundaries need additional executable or contract evidence. The initiative does not infer E3 from the presence of a source anchor.</p></div></div><aside class="review-boundary"><h3>Human / AI review boundary</h3><p>The study used owner-authorized AI-assisted annotation. Its 200 role-conditioned profiles cover only 16 exact structural candidates and measure one model’s consistency, not independent human agreement. Six pointcuts received majority support, eight were rejected, and two remained unresolved in that bounded sensitivity analysis. Independent human validation remains open.</p></aside></div></section>

  <section class="method-section"><div class="method-section-number">02</div><div><h2>Frozen inputs</h2><p>The rendered catalog uses the supplied candidate manifest, pointcut specification, interaction models, canonical atlas, and any-evidence coverage table. Repository clones, caches, evidence packets, and raw evidence are intentionally excluded from this site.</p><dl class="fact-grid"><div><dt>Inventory</dt><dd>52 records / five source families</dd></div><div><dt>Survey panel</dt><dd>41 repositories / 36 families</dd></div><div><dt>Specification</dt><dd>v{esc(data['spec_version'])}</dd></div><div><dt>Diagram models</dt><dd>v{esc(data['diagram_version'])} / analytical</dd></div><div><dt>Study checkout</dt><dd><code>{esc(data['study_revision'])}</code></dd></div><div><dt>Detector environment</dt><dd><code>PyYAML==6.0.3</code><br><code>pandas==2.3.3</code></dd></div></dl><h3>Input and detector SHA-256</h3><p>These hashes identify the exact local inputs used for this rendering. A hash supplies integrity, not validity.</p><div class="hash-table-wrap"><table class="hash-table"><thead><tr><th>Study-relative file</th><th>SHA-256</th></tr></thead><tbody>{hash_rows}</tbody></table></div></div></section>

  <section class="method-section"><div class="method-section-number">03</div><div><h2>Improvement loop</h2><p class="method-lead">Revise from evidence outward; retain a change only when the claim–evidence link and quality gates hold.</p><ol class="loop-list"><li><b>Register claims.</b> Type each statement as descriptive, exploratory, presentational, or causal.</li><li><b>Attach evidence.</b> Name the frozen artifact and independent unit.</li><li><b>Verify integration.</b> Require both evidence and a visible manuscript link.</li><li><b>Rank gaps.</b> Prioritize inference-changing analysis over decorative summaries.</li><li><b>Compute the smallest sufficient result.</b> Preserve denominators, missing-data semantics, and uncertainty.</li><li><b>Run statistical and manuscript QA.</b> Retain or roll back without weakening claim boundaries.</li></ol></div></section>

  <section class="method-section"><div class="method-section-number">04</div><div><h2>Overlay boundary</h2><p>The layered matcher adds Python AST symbols, multi-language declarations, artifact context, exact-symbol refinement, and source triage to frozen lexical retrieval. It remains a discovery instrument, not an automatic conformance classifier.</p><p>The first broad pass produced 851 overinclusive candidates and was rejected. Exact refinement produced 16 review candidates; eight promoted cells live in a <strong>separate, non-frozen overlay</strong>. The frozen 1,272-cell evidence matrix remains unchanged. Overlay coverage therefore cannot be read as a new field-wide prevalence estimate.</p><p>Historical cue gaps are detector gaps, not evidence of retirement. A source path change is a cue-path event, not automatically an architectural redesign.</p></div></section>

  <section class="method-section"><div class="method-section-number">05</div><div><h2>Reproduce the study</h2><p>Run these commands in a complete companion study checkout with its locked environment. Network access and GitHub authentication are needed only for discovery, freezing, and repository materialization. This static site does not include those repository caches.</p><pre class="command-block"><code>python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
make seed test

# Core frozen pipeline
make final verify

# Empirical and longitudinal extension
make longitudinal

# Corpus-level detector, matrix, statistics, and figures
make cartesian

# Layered discovery and separately reviewed overlay
make layered-overlay
make coverage-funnel</code></pre><h3>Rebuild this site</h3><pre class="command-block"><code>python3 scripts/generate_site.py
python3 -I tests/validate_site.py
python3 -m http.server 8000</code></pre><p>The generator uses PyYAML with a stdlib fallback and expects <code>../pattern-mining-study</code> and <code>../POHE_complete/main.pdf</code> by default; <code>--study</code> and <code>--paper</code> override those paths.</p><h3>Source repository</h3><p>The supplied study checkout has no configured public remote. This initiative site therefore does not invent an external study-repository URL. Canonical source anchors link directly to the named public implementation repository at the recorded commit. The complete local paper is available as <a href="assets/paper/pohe-position.pdf">the companion PDF</a>.</p></div></section>

  <section class="method-section limitations-section"><div class="method-section-number">06</div><div><h2>Limitations</h2><ul class="limitations-list"><li>The 52 records are a scoped candidate inventory, not 52 disjoint or universally established mechanisms.</li><li>Current cue coverage is descriptive retrieval in a selected frozen panel, <strong>not a prevalence denominator</strong> or confirmed prevalence.</li><li>Locating a source-backed exemplar for 51 of 52 records does not establish frequency, generality, or contract conformance elsewhere. E08 remains specification-only.</li><li>Lexical detectors have false-positive and construction-leakage risks. Exact structural matching and one-model profile coding do not replace independent adjudication. The post-freeze <a href="https://github.com/xhyccc/pohe-agent/blob/main/docs/calibration.md">scope-anchored calibration</a> is an instrument check; its <code>source_matched</code> verdict is not E2/E3.</li><li>A² is a proposed analytical lens, not a validated lifecycle model. Recording a contract does not enforce it.</li><li>H1–H3 are prospective, design-only studies. No causal performance, safety, cost, or reliability benefit follows from repository association.</li><li>The complete paper PDF is an unpublished, untagged preview, not an accessibility-compliant publication file.</li></ul></div></section>

  <section class="method-section research-agenda-pointer"><div class="method-section-number">07</div><div><h2>Research agenda</h2><p>The Paper page carries the full H1–H3 agenda: stable design commitments, mechanized agency, and controlled variation, with evidence and falsification boundaries attached to each claim.</p><a class="text-link" href="paper.html#research-agenda">Read the research agenda on the Paper page <span aria-hidden="true">↗</span></a></div></section>

  <section class="method-section colophon-section" id="colophon"><div class="method-section-number">◇</div><div><h2>Colophon: why this initiative exists</h2><p class="colophon-byline">By Haoyi Xiong</p><p class="method-lead">It continues an unpublished master’s research project on design patterns.</p><p>That project asked what this one still asks: when does a recurring solution become a pattern, and what must a record say before anyone else can use it? The vocabulary here is learned from the Gang of Four’s catalog, Roy T. Fielding’s account of architectural styles in network software, and Douglas C. Schmidt’s writing on distributed-computing patterns and the Pattern-Oriented Software Architecture series.</p><p>Those teachers insisted on the discipline this initiative tries to keep: name the join point, state the contract, separate what may vary, and show the evidence. The initiative is dedicated to the research that remained unpublished—and to the readers who can carry the question further.</p><ul class="colophon-links"><li><a href="https://roy.gbiv.com/pubs/dissertation/top.htm">Roy T. Fielding, <em>Architectural Styles and the Design of Network-based Software Architectures</em> (2000)</a></li><li><a href="https://www.dre.vanderbilt.edu/~schmidt/">Douglas C. Schmidt</a> — distributed-computing patterns and the POSA series</li><li>Erich Gamma, Richard Helm, Ralph Johnson, and John Vlissides, <em>Design Patterns: Elements of Reusable Object-Oriented Software</em> (1994)</li></ul></div></section>
</main>"""
    return shell("Method & evidence — POHE Initiative", "How the POHE initiative separates proposed specifications, retrieval cues, source exemplars, and review; frozen inputs, hashes, commands, overlays, and limitations.", body, current="method", canonical="method.html")


def load_aliases(study: Path) -> dict[str, list[str]]:
    """Read the frozen compound-alias table without executing study code."""
    source = study / "src/detectors/layered_pattern_matcher.py"
    if not source.is_file():
        return {}
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MANUAL_ALIASES"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return {str(key): [str(alias) for alias in aliases] for key, aliases in value.items()}
    return {}


def latex_to_text(value: str) -> str:
    value = re.sub(r"\\cite\{[^}]*\}", "", value)
    value = value.replace("\\allowbreak", "").replace("~", " ")
    value = value.replace("``", "“").replace("''", "”")
    value = re.sub(r"\\emph\{([^{}]*)\}", r"\1", value)
    value = value.replace("\\S", "§").replace("\\&", "&").replace("\\_", "_").replace("\\%", "%")
    value = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", value)
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+([,.;:])", r"\1", value)


def load_source_locators(paper: Path) -> dict[str, str]:
    """Extract the source locator line from the manuscript's record cards."""
    cards = paper.parent / "harness-patterns-cards.tex"
    if not cards.is_file():
        return {}
    text = cards.read_text(encoding="utf-8")
    locators: dict[str, str] = {}
    for block in re.split(r"(?=\\noindent\\textbf\{[PWCRE]\d\d / A\d\.\})", text):
        match = re.match(r"\\noindent\\textbf\{([PWCRE]\d\d) / A\d\.\}", block)
        if not match:
            continue
        source = re.search(r"\\textit\{Source:\}\s*(.*?)\\par", block, re.S)
        if source:
            locators[match.group(1)] = latex_to_text(source.group(1))
    return locators


def load_data(study: Path, paper: Path) -> dict:
    candidates = list(csv.DictReader((study / "data/manifests/candidates.csv").open(encoding="utf-8", newline="")))
    spec_doc = yaml_load((study / "protocol/harness_pattern_specification.yaml").read_text(encoding="utf-8"))
    diagram_doc = yaml_load((study / "protocol/pattern_diagram_models.yaml").read_text(encoding="utf-8"))
    detector_doc = yaml_load((study / "protocol/detector_queries.yaml").read_text(encoding="utf-8"))
    specs = {p["id"]: p for p in spec_doc["patterns"]}
    diagrams = diagram_doc["patterns"]
    detector = detector_doc["candidates"]
    atlas = {row["candidate_id"]: row for row in csv.DictReader((study / "reports/full_catalog/pattern_instance_atlas.csv").open(encoding="utf-8", newline=""))}
    coverage = {row["candidate_id"]: row for row in csv.DictReader((study / "data/statistics/pattern_coverage.csv").open(encoding="utf-8", newline=""))}
    realizations: dict[str, list[dict]] = defaultdict(list)
    for row in csv.DictReader((study / "data/derived/pattern_repo_realizations.csv").open(encoding="utf-8", newline="")):
        realizations[row["candidate_id"]].append(row)
    aliases = load_aliases(study)
    source_locators = load_source_locators(paper)
    if set(specs) != set(EXPECTED_IDS) or set(diagrams) != set(EXPECTED_IDS):
        raise ValueError("Source protocols do not contain the expected 52 records")
    records = []
    for candidate in candidates:
        pid = candidate["candidate_id"]
        spec = specs[pid]
        evidence_row = atlas[pid]
        coverage_row = coverage[pid]
        activities = candidate["paper_focus"].split("/")
        family = pid[0]
        if evidence_row["status"] == "found":
            status = "source_located"
            anchor = {
                "kind": "source", "repo_slug": evidence_row["repo_slug"],
                "commit_sha": evidence_row["commit_sha"], "family_id": evidence_row["family_id"],
                "implementation": evidence_row["implementation"], "support": evidence_row["support"],
                "note": evidence_row["note"], "url": source_url(evidence_row["repo_slug"], evidence_row["commit_sha"], evidence_row["implementation"]),
                "support_url": source_url(evidence_row["repo_slug"], evidence_row["commit_sha"], evidence_row["support"]) if evidence_row["support"] else "",
            }
            anchors = [anchor]
        else:
            status = "specification_only"
            anchors = [{"kind": "external", "label": "AdaRSI paper (arXiv:2610.20474)", "url": evidence_row["external_url"], "note": evidence_row["note"]}]
        record = {
            "id": pid, "name": spec["name"], "family": family,
            "family_name": FAMILIES[family][0], "codebook_version": candidate["codebook_version"],
            "activities": activities, "activity_names": [ACTIVITIES[a] for a in activities],
            "hypotheses": derive_hypotheses(activities),
            "problem": candidate["problem_and_purpose"], "join_point": spec["join_point"],
            "pointcut": spec["pointcut"], "requires": spec["requires"], "ensures": spec["ensures"],
            "invariant": spec["invariant"], "violation": spec["violation"], "variation": spec["variation"],
            "negative_control": spec["negative_control"], "test_obligation": spec["test_obligation"],
            "evolution_question": spec["evolution_question"], "provenance_class": candidate["provenance_class"],
            "provenance_references": candidate["provenance_references"], "verification_status": candidate["verification_status"],
            "evidence": {
                "status": status, "repository_observed": int(coverage_row["repository_observed"]),
                "repository_total": int(coverage_row["repository_total"]),
                "family_observed": int(coverage_row["family_observed"]), "family_total": int(coverage_row["family_total"]),
                "family_coverage": float(coverage_row["family_coverage"]),
                "current_cue_repositories": int(coverage_row["current_cue_repositories"]),
                "historical_cue_repositories": int(coverage_row["historical_cue_repositories"]),
                "coverage_meaning": "Any current cue, historical cue, or canonical source layer; not confirmed prevalence.",
            },
            "anchors": anchors, "repo_files": realization_files(anchors, realizations.get(pid, [])),
            "aliases": aliases.get(pid, []), "source_locator": source_locators.get(pid, ""),
            "interactions": diagrams[pid]["interactions"],
            "retrieval_cue": {"regex": detector[pid]["regex"], "version": detector_doc["version"], "frozen_at": detector_doc["frozen_at"], "role": "exploratory_retrieval_only"},
            "diagram_svg": f"assets/diagrams/{pid}.svg", "diagram_pdf": f"assets/diagrams/{pid}.pdf",
            "class_svg": f"assets/diagrams/{pid}-class.svg",
            "interaction_svg": f"assets/interactions/{pid}.svg", "interaction_pdf": f"assets/interactions/{pid}.pdf",
            "url": f"patterns/{pid}/",
        }
        records.append(record)
    records.sort(key=lambda p: EXPECTED_IDS.index(p["id"]))
    hash_files = [
        "data/manifests/candidates.csv", "protocol/harness_pattern_specification.yaml",
        "protocol/pattern_diagram_models.yaml", "protocol/detector_queries.yaml",
        "reports/full_catalog/pattern_instance_atlas.csv", "data/statistics/pattern_coverage.csv",
        "data/derived/pattern_repo_realizations.csv",
        "data/manifests/current_41_repositories.csv", "environment/requirements.lock",
        "src/detectors/run_candidate_queries.py", "src/detectors/layered_pattern_matcher.py",
    ]
    try:
        revision = subprocess.run(["git", "-C", str(study), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        revision = "unknown"
    return {
        "schema_version": "1.0.0", "title": "Pattern-Oriented Harness Engineering initiative",
        "record_count": len(records), "spec_version": spec_doc["version"], "diagram_version": diagram_doc["version"],
        "study_revision": revision, "source_hashes": {path: sha256(study / path) for path in hash_files},
        "patterns": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=ROOT.parent / "pattern-mining-study")
    parser.add_argument("--paper", type=Path, default=ROOT.parent / "POHE_complete/main.pdf")
    args = parser.parse_args()
    study = args.study.resolve()
    data = load_data(study, args.paper)
    if data["record_count"] != 52 or [p["id"] for p in data["patterns"]] != EXPECTED_IDS:
        raise ValueError("Catalog must contain exactly P01–P29, W01–W07, C01–C04, R01–R02, E01–E10")
    if set(EXAMPLES) != set(EXPECTED_IDS):
        missing = sorted(set(EXPECTED_IDS) - set(EXAMPLES))
        extra = sorted(set(EXAMPLES) - set(EXPECTED_IDS))
        raise ValueError(f"Examples must cover every record; missing={missing}, extra={extra}")
    write_text(ROOT / "data/patterns.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    write_text(ROOT / "initiative.html", render_initiative())
    write_text(ROOT / "index.html", render_index(data["patterns"]))
    write_text(ROOT / "inventory.html", render_inventory(data))
    write_text(ROOT / "agent.html", render_agent())
    write_text(ROOT / "experiments.html", render_experiments())
    write_text(ROOT / "paper.html", render_paper())
    write_text(ROOT / "method.html", render_method(data))
    for index, pattern in enumerate(data["patterns"]):
        write_text(ROOT / "patterns" / pattern["id"] / "index.html", render_detail(pattern, EXPECTED_IDS[(index - 1) % 52], EXPECTED_IDS[(index + 1) % 52]))
        for suffix in ("svg", "pdf"):
            source = study / "reports/pattern_diagrams" / suffix / f"{pattern['id']}.{suffix}"
            target = ROOT / "assets/diagrams" / f"{pattern['id']}.{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or sha256(source) != sha256(target):
                shutil.copyfile(source, target)
        class_source = study / "reports/pattern_diagrams" / "svg" / f"{pattern['id']}-class.svg"
        class_target = ROOT / "assets/diagrams" / f"{pattern['id']}-class.svg"
        if not class_target.exists() or sha256(class_source) != sha256(class_target):
            shutil.copyfile(class_source, class_target)
        for suffix in ("svg", "pdf"):
            source = study / "reports/pattern_interactions" / suffix / f"{pattern['id']}.{suffix}"
            target = ROOT / "assets/interactions" / f"{pattern['id']}.{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or sha256(source) != sha256(target):
                shutil.copyfile(source, target)
    paper_target = ROOT / "assets/paper/pohe-position.pdf"
    paper_target.parent.mkdir(parents=True, exist_ok=True)
    if not paper_target.exists() or sha256(args.paper) != sha256(paper_target):
        shutil.copyfile(args.paper, paper_target)
    (ROOT / ".nojekyll").touch()
    print(f"Generated {data['record_count']} records, 52 detail pages, and 104 diagram assets in {ROOT}")


if __name__ == "__main__":
    main()
