"""Deterministic financial audit rules for trading support & post-trade reconciliation.

All market scenarios use explicit synthetic datasets designed to verify
accounting, protocol, and risk-control invariants without external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass
class AuditAlert:
    alert_id: str
    category: str
    severity: str
    title: str
    summary: str
    metrics: Dict[str, Any]
    recommended_action: str
    requires_write_privilege: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def audit_futures_roll() -> AuditAlert:
    """Detect naive continuous-contract stitching vs actual contract-by-contract settlement."""
    lots, multiplier, fee_per_lot = 2, 10, 2
    # (near_price, far_price) across 4 trading days; roll occurs on day 2
    prices = [(100, None), (103, 110), (101, 107), (None, 114)]

    naive_stitched_pnl = (prices[-1][1] - prices[0][0]) * lots * multiplier  # (114 - 100) * 20 = 280
    day1_pnl = (prices[1][0] - prices[0][0]) * lots * multiplier             # (103 - 100) * 20 = +60
    day2_pnl = (prices[2][0] - prices[1][0]) * lots * multiplier             # (101 - 103) * 20 = -40
    day3_pnl = (prices[3][1] - prices[2][1]) * lots * multiplier             # (114 - 107) * 20 = +140
    actual_gross_pnl = day1_pnl + day2_pnl + day3_pnl                        # 160
    total_fees = (lots + 2 * lots) * fee_per_lot                             # 4 + 8 = 12
    actual_net_pnl = actual_gross_pnl - total_fees                           # 148
    roll_gap_points = prices[2][1] - prices[2][0]                            # 6
    phantom_roll_pnl = roll_gap_points * lots * multiplier                   # 120

    assert naive_stitched_pnl - actual_gross_pnl == phantom_roll_pnl == 120

    return AuditAlert(
        alert_id="ALT-2026-001",
        category="POST_TRADE_RECONCILIATION",
        severity="HIGH",
        title="期货换月账面差异 (+120.00 元)",
        summary=(
            "近远月合约直接拼接计算收益 280.00 元，逐日盯市实际毛利 160.00 元"
            "（扣费 12.00 元后净利 148.00 元），差额 120.00 元来自换月日 6 点跨期价差。"
        ),
        metrics={
            "naive_stitched_pnl": naive_stitched_pnl,
            "actual_gross_pnl": actual_gross_pnl,
            "fees": total_fees,
            "actual_net_pnl": actual_net_pnl,
            "phantom_roll_pnl": phantom_roll_pnl,
            "roll_gap_points": roll_gap_points,
        },
        recommended_action="ADJUST_RECON_LEDGER",
        requires_write_privilege=True,
    )


def audit_market_making_inventory() -> AuditAlert:
    """Decompose market-making PnL into spread capture, mid-price drift, and fees."""
    fills = [
        {"side": 1, "qty": 100, "fill": 4.99, "arrival_mid": 5.00},
        {"side": 1, "qty": 100, "fill": 4.97, "arrival_mid": 4.98},
        {"side": -1, "qty": 70, "fill": 4.97, "arrival_mid": 4.96},
        {"side": -1, "qty": 30, "fill": 5.01, "arrival_mid": 5.00},
    ]
    close_mid = 4.95
    fee_rate = 0.001

    spread_capture = round(sum(f["side"] * f["qty"] * (f["arrival_mid"] - f["fill"]) for f in fills), 4)
    mid_drift = round(sum(f["side"] * f["qty"] * (close_mid - f["arrival_mid"]) for f in fills), 4)
    fees = round(sum(f["qty"] * fee_rate for f in fills), 4)
    end_inventory = sum(f["side"] * f["qty"] for f in fills)
    net_pnl = round(spread_capture + mid_drift - fees, 4)

    assert spread_capture == 3.00
    assert mid_drift == -5.80
    assert fees == 0.30
    assert end_inventory == 100
    assert net_pnl == -3.10

    return AuditAlert(
        alert_id="ALT-2026-002",
        category="INTRADAY_RISK_EXPOSURE",
        severity="CRITICAL",
        title="做市库存与损益分解 (-3.10 元)",
        summary=(
            "策略 4 笔成交捕获价差 +3.00 元，期末持有 100 股多头库存，"
            "市场中价变动拖累 -5.80 元、手续费 -0.30 元，净损益 -3.10 元。"
        ),
        metrics={
            "spread_capture": spread_capture,
            "mid_drift_to_close": mid_drift,
            "fees": -fees,
            "end_inventory_shares": end_inventory,
            "net_pnl": net_pnl,
        },
        recommended_action="AUTO_HEDGE_INVENTORY",
        requires_write_privilege=True,
    )


def audit_cancel_race() -> AuditAlert:
    """Detect in-flight fills during cancel race with ExecID idempotent replay."""
    order_qty = 100
    canceled_qty = 40

    # Synthetic exchange execution reports (includes a duplicate EX-1002 replay packet)
    exchange_events = [
        {"exec_id": "EX-1001", "qty": 40, "phase": "before_cancel"},
        {"exec_id": "EX-1002", "qty": 20, "phase": "in_flight_cancel_race"},
        {"exec_id": "EX-1002", "qty": 20, "phase": "duplicate_replay"},
    ]
    local_recorded_exec_ids = {"EX-1001"}

    seen_exec_ids: Dict[str, int] = {}
    duplicate_events_ignored = 0
    for ev in exchange_events:
        eid = ev["exec_id"]
        if eid in seen_exec_ids:
            duplicate_events_ignored += 1
            continue
        seen_exec_ids[eid] = ev["qty"]

    filled_before_cancel = seen_exec_ids["EX-1001"]
    filled_during_cancel_race = seen_exec_ids["EX-1002"]
    actual_filled_on_exchange = sum(seen_exec_ids.values())
    local_recorded_qty = sum(qty for eid, qty in seen_exec_ids.items() if eid in local_recorded_exec_ids)
    missing_exec_ids = sorted(set(seen_exec_ids.keys()) - local_recorded_exec_ids)
    position_drift = actual_filled_on_exchange - local_recorded_qty

    assert filled_before_cancel + filled_during_cancel_race + canceled_qty == order_qty
    assert actual_filled_on_exchange == 60
    assert duplicate_events_ignored == 1
    assert missing_exec_ids == ["EX-1002"]
    assert position_drift == 20

    return AuditAlert(
        alert_id="ALT-2026-003",
        category="EXECUTION_PROTOCOL",
        severity="HIGH",
        title="撤单竞态仓位差异 (+20 股漏记)",
        summary=(
            "100 股买单：EX-1001 撤前成交 40 股，EX-1002 撤单确认前成交 20 股（重复回报已幂等忽略 1 条），"
            "撤掉 40 股。交易所回报合计 60 股，本地仅记 40 股，漏记回报 EX-1002（+20 股）。"
        ),
        metrics={
            "order_qty": order_qty,
            "filled_before_cancel": filled_before_cancel,
            "in_flight_race_fill": filled_during_cancel_race,
            "canceled_qty": canceled_qty,
            "exchange_filled_qty": actual_filled_on_exchange,
            "local_recorded_qty": local_recorded_qty,
            "missing_exec_ids": missing_exec_ids,
            "duplicate_events_ignored": duplicate_events_ignored,
            "position_drift_shares": position_drift,
        },
        recommended_action="SYNC_EXCHANGE_POSITION",
        requires_write_privilege=True,
    )


def audit_pit_restatement() -> AuditAlert:
    """Verify Point-In-Time query isolation against future restatement leakage."""
    timeline = [
        {"effective_date": "2026-04-10", "as_of_date": "2026-04-10", "value": 100},
        {"effective_date": "2026-04-10", "as_of_date": "2026-04-15", "value": 80},
    ]

    def query_pit(query_date: str):
        eligible = [row for row in timeline if row["as_of_date"] <= query_date]
        return eligible[-1]["value"] if eligible else None

    q_before = query_pit("2026-04-09")
    q_initial = query_pit("2026-04-11")
    q_restated = query_pit("2026-04-16")

    assert (q_before, q_initial, q_restated) == (None, 100, 80)

    return AuditAlert(
        alert_id="ALT-2026-004",
        category="DATA_GOVERNANCE_PIT",
        severity="MEDIUM",
        title="PIT 财报修订快照核验通过",
        summary=(
            "指标于 04-10 初发 100、04-15 修订为 80。时间点查询在 09/11/16 日"
            "严格返回 None -> 100 -> 80，未发生未来数据泄露。"
        ),
        metrics={
            "query_04_09": q_before,
            "query_04_11": q_initial,
            "query_04_16": q_restated,
            "leakage_detected": False,
        },
        recommended_action="LOG_AUDIT_PASS",
        requires_write_privilege=False,
    )


def run_all_deterministic_audits() -> List[AuditAlert]:
    return [
        audit_futures_roll(),
        audit_market_making_inventory(),
        audit_cancel_race(),
        audit_pit_restatement(),
    ]
