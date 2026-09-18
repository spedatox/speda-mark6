# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Command Line Interface and runner daemon for Orion Spark.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from orion_spark.config import SparkConfig
from orion_spark.incidents import IncidentStore
from orion_spark.monitor import HealthMonitor
from orion_spark.notifier import Notifier
from orion_spark.recovery import Defibrillator
from orion_spark.state import DeploymentStateManager


def setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_check(config: SparkConfig) -> int:
    """One-shot probe check."""
    monitor = HealthMonitor(config)
    result = monitor.probe()
    print("\n--- Orion Spark Health Probe ---")
    print(f"Healthy:           {'YES' if result.healthy else 'NO'}")
    print(f"Container:         {result.container_status} (restarts: {result.restart_count})")
    print(f"Live Endpoint:     {'OK' if result.live_ok else 'FAILED'} ({config.live_url})")
    print(f"Ready Endpoint:    {'OK' if result.ready_ok else 'FAILED'} ({config.ready_url})")
    print(f"Current Revision:  {result.revision or 'unknown'}")
    print(f"Details:           {result.details}\n")
    return 0 if result.healthy else 1


def cmd_status(config: SparkConfig) -> int:
    """Displays deployment state, LKG, and quarantined revisions."""
    state_mgr = DeploymentStateManager(config.state_dir)
    state = state_mgr.state
    store = IncidentStore(config.state_dir)
    recent = store.get_recent(5)

    print("\n================ ORION SPARK STATUS ================")
    print(f"State File:          {state_mgr.state_file}")
    print(f"Active Revision:     {state.active_revision or 'none'}")
    print(f"Last Known Good:     {state.last_known_good or 'none'}")
    print(f"Candidate Revision:  {state.candidate_revision or 'none'}")
    if state.candidate_revision and state.candidate_healthy_since:
        healthy_secs = time.time() - state.candidate_healthy_since
        print(f"Candidate Healthy:   {healthy_secs:.1f}s / {config.stability_window_seconds}s")
    print(f"Outage State:        {'IN OUTAGE' if state.in_outage else 'HEALTHY'}")
    print(f"Consecutive Fails:   {state.consecutive_failures} / {config.failure_threshold}")
    print(f"Recovery Counters:   restarts={state.restart_attempts_count}, rollbacks={state.rollback_attempts_count}")

    print("\nQuarantined Revisions:")
    if state.quarantined_revisions:
        for rev, data in state.quarantined_revisions.items():
            print(f"  • {rev}: {data.get('reason')} (at {data.get('timestamp')})")
    else:
        print("  (none)")

    print(f"\nRecent Incidents ({len(recent)}):")
    if recent:
        for inc in recent:
            print(
                f"  • [{inc.timestamp[:19]}] {inc.type} -> recovery: {inc.recovery} "
                f"({inc.result}, downtime: {inc.downtime_seconds}s)"
            )
    else:
        print("  (none)")
    print("====================================================\n")
    return 0


def cmd_defibrillate(config: SparkConfig, reason: str = "Manual defibrillation") -> int:
    """Manually triggers the defibrillation protocol."""
    state_mgr = DeploymentStateManager(config.state_dir)
    store = IncidentStore(config.state_dir)
    notifier = Notifier(config)
    monitor = HealthMonitor(config)
    defibrillator = Defibrillator(
        config=config,
        state_manager=state_mgr,
        incident_store=store,
        notifier=notifier,
        monitor=monitor,
    )
    success = defibrillator.defibrillate(reason=reason)
    return 0 if success else 1


def cmd_incidents(config: SparkConfig, limit: int = 15) -> int:
    """Lists recent incidents."""
    store = IncidentStore(config.state_dir)
    incidents = store.get_recent(limit)
    if not incidents:
        print("No incident records found.")
        return 0

    print(f"\nRecent Incidents (showing up to {limit}):")
    print(f"{'TIMESTAMP':<22} | {'TYPE':<20} | {'RECOVERY':<10} | {'RESULT':<10} | {'DOWNTIME':<10} | {'FAILED REV':<10} | {'RESTORED REV':<12}")
    print("-" * 105)
    for inc in incidents:
        f_rev = (inc.failed_revision or "")[:7]
        r_rev = (inc.restored_revision or "")[:7]
        print(
            f"{inc.timestamp[:19]:<22} | {inc.type:<20} | {inc.recovery:<10} | "
            f"{inc.result:<10} | {str(inc.downtime_seconds) + 's':<10} | {f_rev:<10} | {r_rev:<12}"
        )
    print("")
    return 0


def cmd_lkg(config: SparkConfig, set_rev: str | None) -> int:
    """View or set Last Known Good revision."""
    state_mgr = DeploymentStateManager(config.state_dir)
    if set_rev:
        state_mgr.set_lkg(set_rev)
        print(f"Last Known Good updated to: {set_rev}")
    else:
        print(f"Current Last Known Good: {state_mgr.state.last_known_good or 'none'}")
    return 0


def cmd_quarantine(config: SparkConfig, add_rev: str | None, reason: str, remove_rev: str | None) -> int:
    """Manage quarantined revisions."""
    state_mgr = DeploymentStateManager(config.state_dir)
    if add_rev:
        state_mgr.quarantine(add_rev, reason or "Manual quarantine")
        print(f"Revision {add_rev} added to quarantine.")
        return 0
    if remove_rev:
        if state_mgr.unquarantine(remove_rev):
            print(f"Revision {remove_rev} removed from quarantine.")
        else:
            print(f"Revision {remove_rev} was not in quarantine.")
        return 0

    print("\nQuarantined Revisions:")
    if state_mgr.state.quarantined_revisions:
        for rev, data in state_mgr.state.quarantined_revisions.items():
            print(f"  • {rev}: {data.get('reason')} ({data.get('timestamp')})")
    else:
        print("  (none)")
    print("")
    return 0


def cmd_run(config: SparkConfig) -> int:
    """Continuous watchdog loop."""
    logger = logging.getLogger("orion_spark.runner")
    logger.info("Starting Orion Spark Watchdog Daemon...")
    logger.info(f"Target Speda: {config.speda_host}:{config.speda_port} (Interval: {config.probe_interval_seconds}s)")
    logger.info(f"Failure Threshold: {config.failure_threshold} consecutive failed probes")
    logger.info(f"Stability Window: {config.stability_window_seconds}s")
    logger.info(f"State Dir: {config.state_dir}")

    state_mgr = DeploymentStateManager(config.state_dir)
    store = IncidentStore(config.state_dir)
    notifier = Notifier(config)
    monitor = HealthMonitor(config)
    defibrillator = Defibrillator(
        config=config,
        state_manager=state_mgr,
        incident_store=store,
        notifier=notifier,
        monitor=monitor,
    )

    running = True

    def sig_handler(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received. Exiting Orion Spark.")
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    while running:
        try:
            result = monitor.probe()

            # Check if active revision is quarantined
            if result.revision and state_mgr.is_quarantined(result.revision):
                logger.critical(
                    f"ACTIVE REVISION {result.revision[:7]} IS IN QUARANTINE! "
                    "Initiating immediate defibrillation to restore LKG."
                )
                defibrillator.defibrillate(reason=f"Active revision {result.revision[:7]} is quarantined")
                time.sleep(config.probe_interval_seconds)
                continue

            if result.healthy:
                logger.debug(result.summary())
                outage_recovered, promoted_lkg, downtime = state_mgr.record_probe_success(
                    active_rev=result.revision,
                    stability_window_seconds=config.stability_window_seconds,
                )
                if outage_recovered:
                    logger.info(f"Outage ended. Downtime: {downtime}s")
                if promoted_lkg:
                    logger.info(f"Revision {promoted_lkg[:7]} verified and promoted to Last Known Good.")
            else:
                logger.warning(result.summary())
                outage_declared = state_mgr.record_probe_failure(
                    active_rev=result.revision,
                    failure_threshold=config.failure_threshold,
                )
                if outage_declared:
                    logger.warning("Consecutive probe failure threshold reached. Initiating Defibrillation Protocol.")
                    defibrillator.defibrillate(reason=f"Outage confirmed: {result.details}")

        except Exception as e:
            logger.error(f"Error in watchdog loop: {e}", exc_info=True)

        # Sleep interval with interruptibility
        sleep_until = time.time() + config.probe_interval_seconds
        while running and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.1, sleep_until - time.time())))

    logger.info("Orion Spark stopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-spark",
        description="Orion Spark — Lightweight Host Watchdog for Speda Mark VI",
    )
    parser.add_argument("--env-file", type=str, default=None, help="Path to custom .env file")
    parser.add_argument("--dry-run", action="store_true", help="Simulate recovery actions without executing")
    parser.add_argument("--state-dir", type=str, default=None, help="Directory for state and incidents storage")

    subparsers = parser.add_subparsers(dest="subcommand", help="Command to execute")

    # run / daemon
    subparsers.add_parser("run", help="Run the continuous watchdog monitor loop")
    subparsers.add_parser("daemon", help="Alias for run")

    # check
    subparsers.add_parser("check", help="Run a single health probe and print status")

    # status
    subparsers.add_parser("status", help="Show current deployment state, LKG, and incidents")

    # defibrillate
    defib_p = subparsers.add_parser("defibrillate", help="Trigger manual defibrillation protocol")
    defib_p.add_argument("--reason", type=str, default="Manual defibrillation", help="Reason for defibrillation")

    # incidents
    inc_p = subparsers.add_parser("incidents", help="Show recent incident records")
    inc_p.add_argument("--limit", type=int, default=15, help="Number of records to show")

    # lkg
    lkg_p = subparsers.add_parser("lkg", help="View or set Last Known Good revision")
    lkg_p.add_argument("--set", dest="set_rev", type=str, default=None, help="Set LKG revision hash")

    # quarantine
    quar_p = subparsers.add_parser("quarantine", help="View or manage quarantined builds")
    quar_p.add_argument("--add", type=str, default=None, help="Add revision hash to quarantine")
    quar_p.add_argument("--reason", type=str, default="Manual quarantine", help="Reason for quarantining")
    quar_p.add_argument("--remove", type=str, default=None, help="Remove revision hash from quarantine")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = SparkConfig.load(env_file=args.env_file)
    if args.dry_run:
        config.dry_run = True
    if args.state_dir:
        config.state_dir = Path(args.state_dir)

    setup_logging(config.log_level)

    subcommand = args.subcommand or "run"

    if subcommand in ("run", "daemon"):
        return cmd_run(config)
    elif subcommand == "check":
        return cmd_check(config)
    elif subcommand == "status":
        return cmd_status(config)
    elif subcommand == "defibrillate":
        return cmd_defibrillate(config, reason=args.reason)
    elif subcommand == "incidents":
        return cmd_incidents(config, limit=args.limit)
    elif subcommand == "lkg":
        return cmd_lkg(config, set_rev=args.set_rev)
    elif subcommand == "quarantine":
        return cmd_quarantine(config, add_rev=args.add, reason=args.reason, remove_rev=args.remove)
    else:
        parser.print_help()
        return 1
