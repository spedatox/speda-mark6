"""Alternate isolated revisions and write actual replies for side-by-side review."""
import argparse
import hashlib
import html
import json
import os
import importlib.util
import re
from pathlib import Path
import subprocess
import sys


def blind_identity_text(text):
    """Remove explicit identity labels and forms of address, not reasoning."""
    labels = r"\b(?:speda|ultron|optimus(?:\s+prime)?|nightcrawler|atomix|scourge|soundwave|sentinel|orion(?:\s+pax)?|jarvis|j\.a\.r\.v\.i\.s\.?|sir|my friend)\b"
    return re.sub(labels, "[address or name masked]", text, flags=re.IGNORECASE)


def identity_review(groups, destination, rubric):
    """Produce pending human scores and a blind view of valid whole cases."""
    score_path = destination.with_name("identity-scores.json")
    prior = json.loads(score_path.read_text(encoding="utf-8")) if score_path.exists() else {}
    previous = {row["sample_id"]: row for row in prior.get("samples", [])}
    comparison_path = destination.with_name("identity-comparison.json")
    prior_comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.exists() else {}
    previous_comparisons = {(row["case"], row["repeat"]): row for row in prior_comparison.get("comparisons", [])}
    scores, key, samples, comparisons = [], [], [], []
    for (case_id, repeat), revisions in groups.items():
        valid_samples = {}
        for label, (case, report, run) in revisions.items():
            turns = case.get("turns", [case])
            if not (case.get("setting") and report.get("live") and case["status"] == "completed"
                    and turns and all(t.get("status") == "completed" and t.get("model_identity_verified") for t in turns)):
                continue
            # Hash only the evidence identity: a revision's opaque sample id is
            # stable across incremental review regeneration, but reveals no label.
            evidence = {"case": case_id, "repeat": repeat, "revision": label,
                "model": report["model"], "config_sha256": report.get("config_sha256"),
                "cases_sha256": report.get("cases_sha256"), "source_sha256": report.get("source", {}).get("app_sha256"),
                "turns": turns}
            sample_id = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            valid_samples[label] = sample_id
            scores.append(previous.get(sample_id, {"sample_id": sample_id, "guessed_agent": None,
                "metrics": {name: None for name in rubric["metrics"]}, "evidence": [],
                "safety_evidence_or_authorization_regression": None}))
            key.append({"sample_id": sample_id, "expected_agent": case["agent"], "setting": case["setting"],
                "revision": label, "case": case_id, "repeat": repeat + 1, "output": str(run["output"]),
                "model": report["model"], "config_sha256": report.get("config_sha256"),
                "cases_sha256": report.get("cases_sha256"), "source_sha256": report.get("source", {}).get("app_sha256")})
            samples.append((sample_id, [blind_identity_text(t.get("response", "")) for t in turns]))
        identity_case = next((case for case, _, _ in revisions.values() if case.get("setting")), None)
        if identity_case:
            settings = {(report.get("model"), report.get("config_sha256"), report.get("cases_sha256"))
                        for _, report, _ in revisions.values()}
            comparable = len(revisions) >= 2 and len(valid_samples) == len(revisions) and len(settings) == 1
            row = {"case": case_id, "repeat": repeat + 1, "agent": identity_case["agent"],
                "setting": identity_case["setting"], "samples_by_revision": valid_samples,
                "comparable_completed_evidence": comparable,
                "outcomes": {name: None for name in rubric["metrics"]},
                "evidence": [], "safety_evidence_or_authorization_regression": None}
            old = previous_comparisons.get((case_id, repeat + 1))
            if old and old.get("samples_by_revision") == valid_samples and comparable:
                row.update({name: old.get(name, row[name]) for name in ("outcomes", "evidence", "safety_evidence_or_authorization_regression")})
            comparisons.append(row)
    out = ["<!doctype html><meta charset='utf-8'><title>Blind identity review</title>",
           "<style>body{font:16px system-ui;max-width:900px;margin:32px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #aaa;margin:24px 0;padding:16px 0}</style>",
           "<h1>Blind identity review</h1><p>These are completed, provider-verified replies. Guess the speaker from judgment and delivery before opening identity-key.json or the named comparison. Names and forms of address are masked; catchphrases and domain labels do not earn recognition credit.</p>",
           "<p>Record your guess and six metric scores in identity-scores.json. Candidate identities: SPEDA, Ultron, Optimus, Nightcrawler, Atomix, Scourge, Sentinel, Orion. A serious reply can be restrained and still consistent; an indistinguishable reply is inconclusive.</p>",
           "<p>" + html.escape(rubric["scale"]) + "</p>",
           "<p>Metrics: " + html.escape(", ".join(rubric["metrics"])) + "</p>"]
    for sample_id, replies in sorted(samples):
        out.append(f"<article><h2>Sample {sample_id}</h2>")
        for number, reply in enumerate(replies, 1):
            out.append(f"<b>Turn {number}</b><pre>{html.escape(reply)}</pre>")
        out.append("</article>")
    if not samples:
        out.append("<p>No valid completed identity conversations. Captures and synthetic provider replies establish no identity score.</p>")
    destination.write_text("\n".join(out), encoding="utf-8")
    score_path.write_text(json.dumps({"assessment": "pending human review; no automatic behavioral scores",
        "scale": rubric["scale"], "samples": sorted(scores, key=lambda row: row["sample_id"])}, indent=2), encoding="utf-8")
    destination.with_name("identity-key.json").write_text(json.dumps({"samples": key,
        "comparison": rubric["comparison"], "limits": rubric["limits"]}, indent=2), encoding="utf-8")
    comparison_path.write_text(json.dumps({"assessment": "pending independent human comparison after blind scoring",
        "allowed_outcomes": ["improved", "unchanged", "regressed", "inconclusive"],
        "comparisons": comparisons}, indent=2), encoding="utf-8")


def review(manifest, destination):
    groups = {}
    identity_rubric = None
    for run in manifest["runs"]:
        if not Path(run["output"]).exists():
            continue
        report = json.loads(Path(run["output"]).read_text(encoding="utf-8"))
        identity_rubric = report.get("identity_review", identity_rubric)
        for case in report["results"]:
            groups.setdefault((case["id"], run["repeat"]), {})[run["revision"]] = (case, report, run)
    out = ["<!doctype html><meta charset='utf-8'><title>Behavior comparison</title>",
           "<style>body{font:16px system-ui;background:#11151b;color:#eee;margin:24px}table{width:100%;table-layout:fixed}td,th{vertical-align:top;border:1px solid #39434e;padding:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#82cfff}.pending{color:#ffc070}</style>",
           "<h1>Behavior comparison</h1><p>Review actual completed live replies. Captures, failures and blocked calls provide no recovery score.</p>"]
    for (case_id, repeat), revisions in groups.items():
        out.append(f"<h2>{html.escape(case_id)} · repetition {repeat + 1}</h2><table><tr>")
        for label in manifest.get("revisions", ("original", "repaired", "candidate")):
            out.append(f"<th>{label}</th>")
        out.append("</tr><tr>")
        for label in manifest.get("revisions", ("original", "repaired", "candidate")):
            item = revisions.get(label)
            if not item:
                out.append("<td class='pending'>No run</td>")
                continue
            case, report, run = item
            out.append("<td>")
            out.append(f"<p>{html.escape(case['rubric'])}</p>")
            for turn in case.get("turns", [case]):
                status = turn.get("status", case["status"])
                actual = report.get("live") and status == "completed" and turn.get("model_identity_verified")
                out.append(f"<b>Turn {turn.get('turn', 0) + 1}: {html.escape(status)}</b>")
                out.append("<pre>" + html.escape(turn.get("response", "")) + "</pre>" if actual else
                           "<p class='pending'>No valid live response for behavioral judgment.</p>")
                metrics = {k: turn.get(k) for k in ("conversation_elapsed_ms", "post_turn_elapsed_ms", "elapsed_ms", "cache_state", "error", "blocked_tool") if k in turn}
                metrics["automatic_recall"] = turn.get("recall", {}).get("automatic_recall")
                out.append("<pre>" + html.escape(json.dumps(metrics, ensure_ascii=False, indent=2)) + "</pre>")
            out.append(f"<a href='{html.escape(Path(run['output']).name)}'>Full responses, assembled requests, tool/provider traces and fixtures</a></td>")
        out.append("</tr></table>")
    if identity_rubric:
        out.append("<h2>Human identity assessment</h2><p>" + html.escape(identity_rubric["comparison"]) + "</p>")
        out.append("<p>Complete <a href='blind_identity_review.html'>blind speaker recognition</a> first. Six scores and evidence are pending in identity-scores.json; identity-key.json contains the answer key. Record independent improved/unchanged/regressed/inconclusive outcomes in identity-comparison.json. Report per-agent, per-setting regressions as well as improvements. Inspect actual tool receipts separately from archived fixtures. Input delivery establishes no personality score.</p>")
        identity_review(groups, destination.with_name("blind_identity_review.html"), identity_rubric)
    else:
        out.append("<h2>Human assessment</h2><p>For each capability record improved, unchanged, regressed or inconclusive with quoted passages: natural memory, voice, reasoning, initiative, preferences/boundaries and executed-action awareness. First-response memory use must require no recall tool. Compare simplification with repaired separately. Acceptance requires gains in memory and voice plus another capability against original, without recurring material regressions.</p>")
    destination.write_text("\n".join(out), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("original", "repaired", "candidate", "baseline"):
        parser.add_argument("--" + label, required=label == "candidate", type=Path, help="Selected revision's packages/igor")
    parser.add_argument("--baseline-ref", help="Expected unchanged baseline Git commit (required for live two-way comparison)")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.baseline:
        if args.original or args.repaired:
            parser.error("--baseline compares with --candidate; do not also supply --original/--repaired")
        labels = ("baseline", "candidate")
        if args.live and not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.baseline_ref or ""):
            parser.error("Live baseline comparison requires --baseline-ref with its expected Git commit")
    else:
        if not args.original or not args.repaired or args.baseline_ref:
            parser.error("Supply --baseline, or both --original and --repaired")
        labels = ("original", "repaired", "candidate")
    if args.live and (not args.config or args.repetitions < 3):
        parser.error("Live comparison needs a verified configuration and at least three repetitions")
    output = args.output_dir.resolve()
    # Artifacts contain context and fixtures and must stay outside all checkouts.
    for label in labels:
        package = getattr(args, label).resolve()
        if output.is_relative_to(package) or output.is_relative_to(package.parents[1]):
            parser.error("Store evaluation output outside Git checkouts")
    output.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name("run_eval.py")
    spec = importlib.util.spec_from_file_location("comparison_harness", runner)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    if args.live:
        expected_sources = (("baseline", args.baseline_ref),) if args.baseline else (("original", "3e76e49"), ("repaired", "41d932b"))
        for label, revision in expected_sources:
            source = harness.source_identity(getattr(args, label).resolve())
            if not (source.get("revision") or "").startswith(revision) or source["working_tree_diff_sha256"] != hashlib.sha256(b"").hexdigest():
                parser.error(f"{label} must be an unchanged checkout of {revision}")
            if not (getattr(args, label).resolve() / ".git").exists() and source.get("archived_source_verified") is False:
                # A packages/igor directory is normally below a Git root.
                try:
                    subprocess.check_output(["git", "-C", str(getattr(args, label)), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError:
                    parser.error(f"{label} archive source hashes are not verified")
    scenarios = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    chosen = [case["id"] for case in scenarios if not args.case or case["id"] in args.case]
    if set(args.case) - set(chosen):
        parser.error("Unknown case id")
    manifest = {"model": args.model, "live": args.live, "runs": [], "accepted": False, "revisions": labels,
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest()}
    orders = [labels, tuple(reversed(labels))] if args.baseline else [labels, ("candidate", "repaired", "original"), ("repaired", "original", "candidate")]
    for case_id in chosen:
        for repetition in range(args.repetitions):
            for label in orders[repetition % len(orders)]:
                path = output / f"{case_id}-{repetition + 1}-{label}.json"
                command = [sys.executable, str(runner), "--app-root", str(getattr(args, label).resolve()),
                    "--model", args.model, "--cases", str(args.cases.resolve()), "--case", case_id,
                    "--embedding-fixture", str(output / "fixture-embeddings.json"), "--output", str(path)]
                if args.config:
                    command += ["--config", str(args.config.resolve())]
                if args.live:
                    command.append("--live")
                # Each subprocess imports only its selected application tree.
                environment = dict(os.environ)
                environment.pop("PYTHONPATH", None)
                result = subprocess.run(command, env=environment)
                manifest["runs"].append({"revision": label, "repeat": repetition, "case": case_id,
                    "output": str(path), "exit_code": result.returncode})
                (output / "comparison.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                review(manifest, output / "review.html")
                if args.live and path.exists():
                    report = json.loads(path.read_text(encoding="utf-8"))
                    if any(case["status"] == "blocked" for case in report["results"]):
                        return 2
                if result.returncode:
                    return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
