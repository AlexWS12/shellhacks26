# Accuracy check of the AI reader against the built-in DESC parser, field by field. DESC stays on its parser: this
# reads the same PDF as a separate, throwaway source ("desc-ai-eval") in a work directory.
#   uv run python scripts/eval_ai_reader.py [--pages 1-44] [--work DIR] [--out ../docs/ai-reader-eval.md]
# Model calls go through the registry cache, so a rerun costs nothing.

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

FIELDS = ["project_id", "name", "description", "status", "in_service_date", "cost_total", "cost_by_year", "build_start",
          "miles", "endpoints"]


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def key(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", pid).upper()


def builtin_projects() -> dict:
    from app.core import extract_desc
    from app.core.pdftext import read_pdf

    out = {}
    for i, text in enumerate(read_pdf(config.DESC_PDF).pages, start=1):
        raw = extract_desc.parse_page(text, i)
        if raw is None:
            continue
        try:
            p, _ = extract_desc.to_project(raw, config.DESC_PDF.name)
        except ValueError:
            continue
        out[key(raw.pid)] = (p, raw)
    return out


def values(p, raw=None) -> dict:
    from app.core.endpoints import split_endpoints

    pid = raw.pid if raw is not None else (p.source_ref.split("ID ", 1)[1] if "ID " in p.source_ref else "")
    years = {k: v for k, v in (p.cost_by_year or {}).items() if v}  # $0 cells and blanks count the same
    ends = [e.name for e in p.endpoints] if raw is None else split_endpoints(p.name)
    return {"project_id": key(pid), "name": norm(p.name), "description": norm(p.description), "status": norm(p.status),
            "in_service_date": p.in_service_date, "cost_total": p.cost_total, "cost_by_year": years,
            "build_start": p.build_start, "miles": p.miles, "endpoints": ends}


async def read(args, work: Path):
    from app.clients import models
    from app.readers.ai_reader import AIReader, estimate_for
    from app.runtime import run as runmod
    from app.runtime.executor import execute
    from app.store import sources

    config.SOURCES_DB, config.DRAFTS_DIR, runmod.RUNS_DIR = work / "sources.db", work / "drafts", work / "runs"
    if args.models:  # the reader role's chain for this check only (config/models.local.json isn't touched)
        chain = [dict(zip(("provider", "model"), m.strip().split("/", 1))) for m in args.models.split(",")]
        (work / "models.local.json").write_text(json.dumps({"roles": {"reader": {"models": chain}}}))
        config.MODELS_LOCAL_FILE = work / "models.local.json"
        models._loaded = None
    config.SUBMISSIONS_DIR = work / "subs"
    rel = str(config.DESC_PDF.relative_to(config.DATA_DIR))
    src = sources.get("desc-ai-eval") or sources.add("DESC-AI", "Dominion (AI reader check)", ["SC"], "ai",
                                                   source_id="desc-ai-eval", utility_key="desc-ai-eval", file_path=rel)
    est = estimate_for(src, args.pages)
    run = runmod.new_run("live")
    run.pace, run.purpose = 0, "extract"
    run.open_log()
    models.start(run.emit)
    reader = AIReader(src, args.pages)
    started = time.monotonic()
    await execute(run, [reader], [])
    return run, reader, est, round(time.monotonic() - started, 1)


def compare(ai: dict, base: dict) -> dict:
    from app.core.models import Project

    matched = {k: (p, ai[k]) for k, p in base.items() if k in ai}
    rows = {f: {"agree": 0, "disagree": 0, "ai_empty": 0, "examples": []} for f in FIELDS}
    for k, ((bp, raw), ap) in matched.items():
        b, a = values(bp, raw), values(ap)
        for f in FIELDS:
            want, got = b[f], a[f]
            if got in (None, "", [], {}) and want not in (None, "", [], {}):
                rows[f]["ai_empty"] += 1
                rows[f]["examples"].append(f"{bp.id}: AI empty, parser {json.dumps(want, default=str)[:90]}")
            elif got == want or (f == "endpoints" and sorted(got) == sorted(want)):  # order of ends doesn't matter
                rows[f]["agree"] += 1
            else:
                rows[f]["disagree"] += 1
                rows[f]["examples"].append(f"{bp.id}: AI {json.dumps(got, default=str)[:90]} vs parser "
                                           f"{json.dumps(want, default=str)[:90]}")
    year_agree = year_total = 0
    for k, ((bp, raw), ap) in matched.items():
        for y, v in values(bp, raw)["cost_by_year"].items():
            year_total += 1
            year_agree += values(ap)["cost_by_year"].get(y) == v
    return {"matched": len(matched), "missed": sorted(set(base) - set(ai)), "extra": sorted(set(ai) - set(base)),
            "rows": rows, "year_cells": (year_agree, year_total), "_types": Project}


def report(args, run, reader, est, secs, cmp) -> str:
    from app.core.pdftext import read_pdf
    from app.readers.ai_reader import recheck

    draft = reader.result or {}
    ev = run.events
    texts = dict(enumerate(read_pdf(config.DESC_PDF).pages, start=1))
    from app.core.models import Project

    ai_projects = [Project(**p) for p in draft.get("projects", [])]
    audit = [f for p in ai_projects for f in recheck(p, texts)]
    kept = sum(len([v for v in (p.provenance or {}).values() if isinstance(v, (dict, list))]) for p in ai_projects)
    kept += sum(len(v) - 1 for p in ai_projects for v in (p.provenance or {}).values() if isinstance(v, list))
    dropped = [c for c in draft.get("checks", []) if c["rule"] == "ai_field_dropped"]
    failed = [e for e in ev if e["type"] == "project.extract_failed"]
    calls = [e for e in ev if e["type"] == "tool.result" and e.get("tool") in ("locate_pages", "extract_pages")]
    used = sorted({e.get("model") for e in calls if e.get("model")})
    model_fail = [e for e in ev if e["type"] == "model.call_failed"]
    located = next((e for e in ev if e["type"] == "pages.located"), {})
    conf = draft.get("confidence", {})
    total_cmp = sum(r["agree"] + r["disagree"] + r["ai_empty"] for r in cmp["rows"].values())
    total_agree = sum(r["agree"] for r in cmp["rows"].values())
    ya, yt = cmp["year_cells"]
    lines = [
        "# AI reader: accuracy check against the DESC parser",
        "",
        f"Run `{run.id}` on {time.strftime('%Y-%m-%d %H:%M')}, {secs} s. Source: `{config.DESC_PDF.name}` "
        f"({len(texts)} pages{', pages ' + args.pages if args.pages else ''}), read as a throwaway source "
        "`desc-ai-eval`. DESC itself stays on its built-in parser.",
        "",
        (f"Reader models for this check: {args.models} (set with --models; config/models.json is unchanged). "
         if args.models else "") +
        f"Models used: {', '.join(used) or 'none (all from cache)'}; {len(calls)} model steps, "
        f"{len(model_fail)} failed calls retried or moved to a backup. Estimate before the run: "
        f"{est['input_tokens']:,} input + {est['output_tokens']:,} output tokens over {est['calls']} calls, "
        + (f"${est['usd']:.4f}." if est["usd"] is not None else "price unknown (no price in config/models.json)."),
        "",
        "## Headline",
        "",
        "| | |", "|---|---|",
        f"| Pages located | {len(located.get('candidates', []))} of {located.get('total', '?')} (by {located.get('by', '?')}) |",
        f"| Projects found | {len(ai_projects)} (parser: {len(cmp['_base'])}) |",
        f"| Matched by project ID | {cmp['matched']} of {len(cmp['_base'])} |",
        f"| Missed | {len(cmp['missed'])}{': ' + ', '.join(cmp['missed']) if cmp['missed'] else ''} |",
        f"| Extra (not in the parser's output) | {len(cmp['extra'])}{': ' + ', '.join(cmp['extra']) if cmp['extra'] else ''} |",
        f"| Field agreement (matched projects) | **{total_agree} of {total_cmp} = {total_agree / max(1, total_cmp):.1%}** |",
        f"| Yearly cost cells agreeing | {ya} of {yt} = {ya / max(1, yt):.1%} |",
        f"| Fields kept after the snippet check | {kept} |",
        f"| Fields dropped by the snippet check | {len(dropped)} |",
        f"| Kept fields whose snippet isn't on its page (independent re-audit) | **{len(audit)}** |",
        f"| Items rejected (no name or date, off-schema) | {len(failed)} |",
        f"| Mean confidence (kept / returned fields) | {sum(conf.values()) / max(1, len(conf)):.3f} |",
        f"| Voltage found (no parser equivalent, not scored) | {sum(1 for p in ai_projects if (p.provenance or {}).get('voltage_kv_parsed'))}"
        f" of {len(ai_projects)} projects |",
        "",
        "## By field",
        "",
        "Agreement is exact after whitespace normalization. Dates and money are compared after code parsed both "
        "sides (ISO dates, whole dollars). `cost_by_year` ignores $0 cells on both sides. `endpoints` (order ignored): "
        "the parser's side is the endpoint splitter on its project name; the AI reader keeps two ends the model stated "
        "only when both are in the title, otherwise it uses the same splitter. `ai empty` means the model gave nothing or the snippet check dropped it.",
        "",
        "| Field | Agree | Disagree | AI empty | Agreement |", "|---|---|---|---|---|",
    ]
    for f, r in cmp["rows"].items():
        n = r["agree"] + r["disagree"] + r["ai_empty"]
        lines.append(f"| {f} | {r['agree']} | {r['disagree']} | {r['ai_empty']} | {r['agree'] / max(1, n):.1%} |")
    lines += ["", "## Differences", ""]
    for f, r in cmp["rows"].items():
        if r["examples"]:
            lines.append(f"**{f}**")
            lines += [f"- {x}" for x in r["examples"][:8]] + ([f"- ... {len(r['examples']) - 8} more"] if len(r["examples"]) > 8 else [])
            lines.append("")
    if dropped:
        lines += ["## Dropped by the snippet check", ""] + [f"- {c['detail']} ({c['source']})" for c in dropped[:15]] + [""]
    if failed:
        lines += ["## Items rejected", ""] + [f"- p.{e['page']}: {e['reason']}" for e in failed[:15]] + [""]
    lines += ["## How it was checked", "",
              "1. The built-in parser reads the DESC PDF as it does in every run.",
              "2. The AI reader reads the same PDF: locate (page summaries to the `reader` model), extract (candidate "
              "pages in chunks, every field with page + exact snippet), verify (code: snippet on its page, value in "
              "its snippet; dates, costs, voltage parsed by code; endpoints from the existing splitter).",
              "3. Projects are matched on the project ID; each field is compared as above.",
              "4. Every kept field's snippet is looked up again on its cited page by separate code (`recheck`).",
              "", "Rerun: `cd backend && uv run python scripts/eval_ai_reader.py` (cached calls are free).", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--models", default=None, help="reader chain for this check, e.g. gemini/gemini-3.1-flash-lite,gemini/x")
    ap.add_argument("--out", default=str(config.REPO_DIR / "docs" / "ai-reader-eval.md"))
    args = ap.parse_args()
    work = Path(args.work or tempfile.mkdtemp(prefix="ai-reader-eval-"))
    work.mkdir(parents=True, exist_ok=True)
    base = builtin_projects()
    run, reader, est, secs = asyncio.run(read(args, work))
    ai = {}
    for p in (reader.result or {}).get("projects", []):
        from app.core.models import Project

        pp = Project(**p)
        ai[key(pp.source_ref.split("ID ", 1)[1]) if "ID " in pp.source_ref else pp.id] = pp
    if args.pages:  # compare only the pages that were read
        from app.readers.ai_reader import parse_pages

        keep = set(parse_pages(args.pages, 10_000))
        base = {k: v for k, v in base.items() if v[0].source_page in keep}
    cmp = compare(ai, base)
    cmp["_base"] = base
    text = report(args, run, reader, est, secs, cmp)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text.split("## By field")[0])
    print(f"wrote {args.out}; run log and draft in {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
