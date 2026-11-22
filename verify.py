#!/usr/bin/env python3
"""Deterministic verification suite for quant-ops-agent-workbench.

Runs 13 automated test cases across:
1. Deterministic financial audit rules (futures roll, MM decomposition, cancel race, PIT)
2. Log deduplication & long-line truncation
3. Unknown category graceful fallback to low-confidence escalation
4. Write-action routing & read-only auto-archive
5. Python HITL state transition & snapshot generation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.rules import (
    AuditAlert,
    audit_cancel_race,
    audit_futures_roll,
    audit_market_making_inventory,
    audit_pit_restatement,
    run_all_deterministic_audits,
)
from agent.harness import AgentHarness, apply_human_decision


def test_futures_roll_rule():
    alert = audit_futures_roll()
    assert alert.metrics["naive_stitched_pnl"] == 280
    assert alert.metrics["actual_gross_pnl"] == 160
    assert alert.metrics["actual_net_pnl"] == 148
    assert alert.metrics["phantom_roll_pnl"] == 120


def test_market_making_rule():
    alert = audit_market_making_inventory()
    assert alert.metrics["spread_capture"] == 3.00
    assert alert.metrics["mid_drift_to_close"] == -5.80
    assert alert.metrics["fees"] == -0.30
    assert alert.metrics["net_pnl"] == -3.10
    assert alert.metrics["end_inventory_shares"] == 100


def test_cancel_race_rule():
    alert = audit_cancel_race()
    assert alert.metrics["order_qty"] == 100
    assert alert.metrics["filled_before_cancel"] == 40
    assert alert.metrics["in_flight_race_fill"] == 20
    assert alert.metrics["canceled_qty"] == 40
    assert alert.metrics["exchange_filled_qty"] == 60
    assert alert.metrics["local_recorded_qty"] == 40
    assert alert.metrics["position_drift_shares"] == 20


def test_pit_restatement_rule():
    alert = audit_pit_restatement()
    assert alert.metrics["query_04_09"] is None
    assert alert.metrics["query_04_11"] == 100
    assert alert.metrics["query_04_16"] == 80
    assert alert.metrics["leakage_detected"] is False


def test_harness_log_dedup_and_truncation():
    harness = AgentHarness(max_log_lines=4, max_line_chars=240)
    alert = audit_futures_roll()
    huge_line = "X" * 4096
    noisy_logs = ["[DEBUG] duplicate"] * 50 + [huge_line, "[ERR] roll gap 6 pts"]
    ctx = harness.compress_context(alert, noisy_logs)
    assert len(ctx["pruned_logs"]) == 3
    assert len(ctx["pruned_logs"][1]) == 240
    assert ctx["pruned_logs"][1].endswith("...")
    assert ctx["retained_log_chars"] <= 4 * 240


def test_unknown_category_escalates_gracefully():
    harness = AgentHarness(confidence_threshold=0.85)
    unknown_alert = AuditAlert(
        alert_id="ALT-2026-999",
        category="UNSEEN_EXCHANGE_ERROR",
        severity="MEDIUM",
        title="未知回报码异常",
        summary="收到未登记的交易所回报码。",
        metrics={"code": 9999},
        recommended_action="LOG_AUDIT_PASS",
        requires_write_privilege=False,
    )
    ticket = harness.triage(unknown_alert)
    assert ticket.runbook_id == "RB-UNKNOWN-000"
    assert ticket.routing_confidence == 0.60
    assert ticket.guardrail["policy_triggered"] == "LOW_CONFIDENCE_ESCALATION"
    assert ticket.status == "PENDING_APPROVAL"


def test_guardrail_blocks_ledger_mutation():
    harness = AgentHarness()
    decision = harness.evaluate_guardrails("ADJUST_RECON_LEDGER", requires_write=True, confidence=0.99)
    assert decision.allowed_auto_execute is False
    assert decision.policy_triggered == "ROUTE_WRITE_TO_HUMAN_REVIEW"
    assert decision.required_state == "PENDING_APPROVAL"


def test_guardrail_blocks_auto_hedge():
    harness = AgentHarness()
    decision = harness.evaluate_guardrails("AUTO_HEDGE_INVENTORY", requires_write=True, confidence=0.99)
    assert decision.allowed_auto_execute is False
    assert decision.required_state == "PENDING_APPROVAL"


def test_guardrail_blocks_low_confidence_read_action():
    harness = AgentHarness(confidence_threshold=0.85)
    decision = harness.evaluate_guardrails("LOG_AUDIT_PASS", requires_write=False, confidence=0.72)
    assert decision.allowed_auto_execute is False
    assert decision.policy_triggered == "LOW_CONFIDENCE_ESCALATION"


def test_guardrail_allows_high_confidence_read_only():
    harness = AgentHarness(confidence_threshold=0.85)
    decision = harness.evaluate_guardrails("LOG_AUDIT_PASS", requires_write=False, confidence=0.96)
    assert decision.allowed_auto_execute is True
    assert decision.required_state == "AUTO_ARCHIVED"


def test_hitl_approval_transition():
    harness = AgentHarness()
    ticket = harness.triage(audit_cancel_race())
    assert ticket.status == "PENDING_APPROVAL"
    updated = apply_human_decision(ticket, reviewer="ops_lead_01", approve=True, note="已核对交易所回报，批准同步仓位")
    assert updated.status == "APPROVED"
    assert updated.human_reviewer == "ops_lead_01"


def test_hitl_rejects_invalid_double_transition():
    harness = AgentHarness()
    ticket = harness.triage(audit_futures_roll())
    apply_human_decision(ticket, reviewer="ops_lead_01", approve=False, note="暂缓调整，等待结算单二次确认")
    assert ticket.status == "REJECTED"
    try:
        apply_human_decision(ticket, reviewer="ops_lead_02", approve=True, note="尝试二次流转")
        raise AssertionError("Expected ValueError on double transition")
    except ValueError:
        pass


def test_end_to_end_snapshot_generation():
    harness = AgentHarness()
    alerts = run_all_deterministic_audits()
    tickets = [harness.triage(a) for a in alerts]
    assert len(tickets) == 4
    pending = [t for t in tickets if t.status == "PENDING_APPROVAL"]
    archived = [t for t in tickets if t.status == "AUTO_ARCHIVED"]
    assert len(pending) == 3
    assert len(archived) == 1

    evidence_dir = ROOT / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    snapshot = {
        "schema_version": "1.1.0",
        "data_provenance": "synthetic_deterministic",
        "summary": {
            "total_alerts": len(tickets),
            "routed_to_human_review": len(pending),
            "auto_archived_read_only": len(archived),
            "live_write_executed": 0,
        },
        "tickets": [t.to_dict() for t in tickets],
    }
    out_path = evidence_dir / "workbench_snapshot.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert out_path.exists()


TESTS: List[Tuple[str, Callable[[], None]]] = [
    ("rule_futures_roll_120_gap", test_futures_roll_rule),
    ("rule_market_making_decomp_310", test_market_making_rule),
    ("rule_cancel_race_20_inflight", test_cancel_race_rule),
    ("rule_pit_restatement_isolation", test_pit_restatement_rule),
    ("harness_log_dedup_and_truncation", test_harness_log_dedup_and_truncation),
    ("harness_unknown_category_escalation", test_unknown_category_escalates_gracefully),
    ("guardrail_route_ledger_write", test_guardrail_blocks_ledger_mutation),
    ("guardrail_route_auto_hedge", test_guardrail_blocks_auto_hedge),
    ("guardrail_route_low_confidence", test_guardrail_blocks_low_confidence_read_action),
    ("guardrail_allow_readonly_audit", test_guardrail_allows_high_confidence_read_only),
    ("hitl_approval_state_machine", test_hitl_approval_transition),
    ("hitl_prevent_double_transition", test_hitl_rejects_invalid_double_transition),
    ("e2e_workbench_snapshot_export", test_end_to_end_snapshot_generation),
]


def main() -> int:
    passed = 0
    for name, fn in TESTS:
        fn()
        passed += 1
        print(f"[PASS] {name}")
    print(f"\nALL CASES PASSED: {passed}/{len(TESTS)} test cases verified.")
    print("Snapshot written to: evidence/workbench_snapshot.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
