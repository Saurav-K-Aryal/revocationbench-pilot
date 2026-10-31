"""Real SQLite concurrency pilot on authored authorization fixtures, not an AI benchmark."""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pilots.common import PilotContext


def _connect(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    c.execute("PRAGMA busy_timeout=10000")
    return c


def _initialize(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    c = _connect(path)
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE grants(id TEXT PRIMARY KEY, parent TEXT, revoked INTEGER NOT NULL);
    INSERT INTO grants VALUES('root',NULL,0),('child','root',0);
    CREATE TABLE account(id INTEGER PRIMARY KEY, used INTEGER, cap INTEGER);
    INSERT INTO account VALUES(1,0,100);
    CREATE TABLE effects(seq INTEGER PRIMARY KEY, op TEXT, amount INTEGER, at_ns INTEGER);
    CREATE TABLE operations(op TEXT PRIMARY KEY,state TEXT);
    CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER);
    """)
    c.close()


def _active(c: sqlite3.Connection, grant: str, ancestors: bool) -> bool:
    seen = set()
    while grant:
        if grant in seen:
            return False
        seen.add(grant)
        row = c.execute("SELECT parent,revoked FROM grants WHERE id=?", (grant,)).fetchone()
        if row is None or row[1]:
            return False
        if not ancestors:
            return True
        grant = row[0]
    return True


def _one_worker(path: Path, arm: str, op: str, amount: int, barrier: threading.Barrier,
                release: threading.Event, jitter: float) -> dict:
    c = _connect(path)
    start = time.perf_counter_ns()
    try:
        # The naive controller checks before the forced race and trusts only its own grant.
        snapshot = c.execute("SELECT used,cap FROM account WHERE id=1").fetchone()
        cached_allowed = _active(c, "child", False) and snapshot[0] + amount <= snapshot[1]
        barrier.wait(timeout=10)
        if not release.wait(timeout=10):
            raise TimeoutError("controller failed to release workers")
        time.sleep(jitter)
        if arm == "naive_preflight":
            if not cached_allowed:
                return {"op": op, "decision": "denied", "latency_ms": 0.0}
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE account SET used=? WHERE id=1", (snapshot[0] + amount,))
            c.execute("INSERT INTO effects(op,amount,at_ns) VALUES(?,?,?)", (op, amount, time.perf_counter_ns()))
            c.execute("COMMIT")
            decision = "executed"
        else:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute("SELECT state FROM operations WHERE op=?", (op,)).fetchone()
            used, cap = c.execute("SELECT used,cap FROM account WHERE id=1").fetchone()
            if previous:
                c.execute("ROLLBACK")
                decision = "duplicate_no_effect"
            elif not _active(c, "child", True) or used + amount > cap:
                c.execute("ROLLBACK")
                decision = "denied"
            else:
                c.execute("UPDATE account SET used=used+? WHERE id=1", (amount,))
                c.execute("INSERT INTO operations VALUES(?,?)", (op, "complete"))
                c.execute("INSERT INTO effects(op,amount,at_ns) VALUES(?,?,?)", (op, amount, time.perf_counter_ns()))
                c.execute("COMMIT")
                decision = "executed"
        return {"op": op, "decision": decision, "latency_ms": (time.perf_counter_ns() - start) / 1e6}
    finally:
        if c.in_transaction:
            c.rollback()
        c.close()


def _concurrent_trial(path: Path, scenario: str, arm: str, seed: int) -> tuple[dict, list[dict]]:
    _initialize(path)
    rng = random.Random(seed)
    if scenario == "over_budget":
        requests, expected_effects = [("one", 60), ("two", 60)], 1
    elif scenario == "within_budget":
        requests, expected_effects = [("one", 60), ("two", 40)], 2
    elif scenario == "duplicate":
        requests, expected_effects = [("same", 40), ("same", 40)], 1
    else:
        requests, expected_effects = [("one", 30), ("two", 30)], 0
    barrier, release = threading.Barrier(3), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_one_worker, path, arm, op, amount, barrier, release,
                               rng.uniform(0, 0.002)) for op, amount in requests]
        barrier.wait(timeout=10)
        c = _connect(path)
        if scenario == "ancestor_revoked":
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE grants SET revoked=1 WHERE id='root'")
            c.execute("INSERT INTO meta VALUES('revoke_ack_ns',?)", (time.perf_counter_ns(),))
            c.execute("COMMIT")
        c.close()
        release.set()
        worker_results = [f.result(timeout=15) for f in futures]
    c = _connect(path)
    effects = c.execute("SELECT seq,op,amount,at_ns FROM effects ORDER BY seq").fetchall()
    recorded_used = c.execute("SELECT used FROM account WHERE id=1").fetchone()[0]
    ack_row = c.execute("SELECT value FROM meta WHERE key='revoke_ack_ns'").fetchone()
    c.close()
    actual_total = sum(e[2] for e in effects)
    duplicate_effects = len(effects) - len({e[1] for e in effects})
    revoked_effects = sum(e[3] > ack_row[0] for e in effects) if ack_row else 0
    violation = actual_total > 100 or duplicate_effects > 0 or revoked_effects > 0
    # Expected authorized completion is scenario-specific, not number of attempted calls.
    row = {"scenario": scenario, "arm": arm, "seed": seed, "effect_count": len(effects),
           "effect_amount": actual_total, "ledger_used": recorded_used,
           "overspend": max(0, actual_total - 100), "duplicate_effects": duplicate_effects,
           "effects_after_revoke_ack": revoked_effects, "violation": violation,
           "expected_authorized_effects": expected_effects,
           "authorized_completion_matches": len(effects) == expected_effects and not violation,
           "mean_worker_latency_ms": sum(x["latency_ms"] for x in worker_results) / 2}
    return row, worker_results


def _external_boundary(path: Path, arm: str) -> dict:
    """An actual second SQLite endpoint cannot join the ledger's transaction."""
    _initialize(path)
    external = path.with_name(path.stem + "_external.sqlite")
    if external.exists():
        external.unlink()
    e = _connect(external)
    e.execute("CREATE TABLE receipts(id INTEGER PRIMARY KEY,op TEXT)")
    e.close()
    decisions = []
    for attempt in range(2):
        c = _connect(path)
        if arm == "transactional_gateway":
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT state FROM operations WHERE op='external'").fetchone()
            if prior:
                c.execute("ROLLBACK")
                decisions.append("unknown_no_retry")
                c.close()
                continue
            c.execute("INSERT INTO operations VALUES('external','unknown')")
            c.execute("UPDATE account SET used=used+40 WHERE id=1")
            c.execute("COMMIT")
        # The endpoint really commits, then its response is deliberately lost.
        e = _connect(external)
        e.execute("INSERT INTO receipts(op) VALUES('external')")
        e.close()
        decisions.append("response_lost_unknown")
        c.close()
    e = _connect(external)
    count = e.execute("SELECT count(*) FROM receipts").fetchone()[0]
    e.close()
    c = _connect(path)
    unresolved = c.execute("SELECT count(*) FROM operations WHERE state='unknown'").fetchone()[0]
    used = c.execute("SELECT used FROM account WHERE id=1").fetchone()[0]
    c.close()
    return {"arm": arm, "scenario": "external_response_lost", "attempts": 2,
            "actual_external_effects": count, "unresolved_operations": unresolved,
            "reserved_amount": used, "decisions": decisions,
            "confirmed_task_completion": False,
            "interpretation": "Boundary control: refusing an unsafe retry leaves an unknown outcome; no exactly-once claim for arbitrary external endpoints."}


def run(ctx: PilotContext) -> dict:
    reps = 2 if ctx.profile == "smoke" else 20
    rows, traces, external_rows = [], [], []
    for scenario in ("over_budget", "within_budget", "duplicate", "ancestor_revoked"):
        for rep in range(reps):
            seed = ctx.seed + rep
            for arm in ("naive_preflight", "transactional_gateway"):
                path = ctx.path(f"{scenario}_{rep}_{arm}.sqlite")
                row, workers = _concurrent_trial(path, scenario, arm, seed)
                rows.append(row)
                traces.append({"scenario": scenario, "arm": arm, "replicate": rep, "workers": workers})
    for arm in ("naive_preflight", "transactional_gateway"):
        external_rows.append(_external_boundary(ctx.path(f"external_{arm}.sqlite"), arm))
    ctx.write_csv("trials.csv", rows)
    ctx.write_json("worker_traces.json", traces)
    ctx.write_json("external_boundary.json", external_rows)
    metrics = {}
    for arm in ("naive_preflight", "transactional_gateway"):
        selected = [r for r in rows if r["arm"] == arm]
        metrics[arm] = {"trials": len(selected), "trials_with_violation": sum(r["violation"] for r in selected),
                        "authorized_completion_matches": sum(r["authorized_completion_matches"] for r in selected),
                        "total_overspend": sum(r["overspend"] for r in selected),
                        "duplicate_effects": sum(r["duplicate_effects"] for r in selected),
                        "effects_after_revoke_ack": sum(r["effects_after_revoke_ack"] for r in selected)}
    return {"experiment_id": "02", "topic": "AI for Information Security", "status": "completed",
            "evidence_level": "engineering fixture with real concurrent SQLite workers and independently audited effects",
            "metrics": metrics,
            "findings": ["Results were computed from committed SQLite effects, not agent claims.",
                         "The within-budget control checks legitimate completion instead of rewarding deny-all behavior.",
                         "The external-response-loss control deliberately exposes unresolved outcomes outside a shared transaction."],
            "limitations": ["Authored forced schedules and a deliberately naive baseline are integration controls, not representative threat prevalence.",
                            "Thread workers use separate real connections on one machine; no distributed network, model agent, Cedar engine, or production backend is tested.",
                            "The reference gateway's guarantees rely on one authoritative SQLite transaction boundary and trusted complete mediation.",
                            "Replicates are related stress trials; their zero observed failures do not imply a population failure-rate bound."],
            "artifacts": ["trials.csv", "worker_traces.json", "external_boundary.json"],
            "proposal_claim_template": "On [N] authored local fault schedules, [controller] produced [observed violations] and [authorized completions] according to committed SQLite effects. This validates the pilot harness only; enterprise-agent security and distributed transfer remain untested."}
