"""Alternate isolated revisions and write actual replies for side-by-side review."""
import argparse
import hashlib
import html
import json
import os
import importlib.util
from pathlib import Path
import subprocess
import sys


def review(manifest, destination):
    groups = {}
    for run in manifest["runs"]:
        if not Path(run["output"]).exists():
            continue
        report = json.loads(Path(run["output"]).read_text(encoding="utf-8"))
        for case in report["results"]:
            groups.setdefault((case["id"], run["repeat"]), {})[run["revision"]] = (case, report, run)
    out = ["<!doctype html><meta charset='utf-8'><title>Behavior comparison</title>",
           "<style>body{font:16px system-ui;background:#11151b;color:#eee;margin:24px}table{width:100%;table-layout:fixed}td,th{vertical-align:top;border:1px solid #39434e;padding:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#82cfff}.pending{color:#ffc070}</style>",
           "<h1>Behavior comparison</h1><p>Review actual completed live replies. Captures, failures and blocked calls provide no recovery score.</p>"]
    for (case_id, repeat), revisions in groups.items():
        out.append(f"<h2>{html.escape(case_id)} · repetition {repeat + 1}</h2><table><tr>")
        for label in ("original", "repaired", "candidate"):
            out.append(f"<th>{label}</th>")
        out.append("</tr><tr>")
        for label in ("original", "repaired", "candidate"):
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
    out.append("<h2>Human assessment</h2><p>For each capability record improved, unchanged, regressed or inconclusive with quoted passages: natural memory, voice, reasoning, initiative, preferences/boundaries and executed-action awareness. First-response memory use must require no recall tool. Compare simplification with repaired separately. Acceptance requires gains in memory and voice plus another capability against original, without recurring material regressions.</p>")
    destination.write_text("\n".join(out), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("original", "repaired", "candidate"):
        parser.add_argument("--" + label, required=True, type=Path, help="Selected revision's packages/igor")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.live and (not args.config or args.repetitions < 3):
        parser.error("Live comparison needs a verified configuration and at least three repetitions")
    output = args.output_dir.resolve()
    # Artifacts contain context and fixtures and must stay outside all checkouts.
    for label in ("original", "repaired", "candidate"):
        package = getattr(args, label).resolve()
        if output.is_relative_to(package) or output.is_relative_to(package.parents[1]):
            parser.error("Store evaluation output outside Git checkouts")
    output.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name("run_eval.py")
    spec = importlib.util.spec_from_file_location("comparison_harness", runner)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    if args.live:
        for label, revision in (("original", "3e76e49"), ("repaired", "41d932b")):
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
    manifest = {"model": args.model, "live": args.live, "runs": [], "accepted": False,
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest()}
    orders = [("original", "repaired", "candidate"), ("candidate", "repaired", "original"), ("repaired", "original", "candidate")]
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
