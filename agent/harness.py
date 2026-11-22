"""Deterministic triage routing prototype with log deduplication/truncation,
preset Runbook lookup, write-action routing rules, and HITL state transitions.

Note: This prototype does NOT connect to an external LLM or live trading gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from engine.rules import AuditAlert


# Preset Runbook table keyed by alert category
INCIDENT_RUNBOOKS: Dict[str, Dict[str, Any]] = {
    "POST_TRADE_RECONCILIATION": {
        "runbook_id": "RB-FUT-101",
        "root_cause_pattern": "近远月合约切换时未按独立合约逐日盯市，跨期价差被误计入账面收益。",
        "standard_sop": [
            "核对换月日近远月结算价与跨期价差（roll_gap_points）",
            "按实际持有合约逐日盯市重算损益",
            "生成调整建议单送人工复核，不直接修改历史成交库",
        ],
    },
    "INTRADAY_RISK_EXPOSURE": {
        "runbook_id": "RB-MM-204",
        "root_cause_pattern": "期末留存单边多头库存，收盘中价下跌拖累整体损益，掩盖了价差收益。",
        "standard_sop": [
            "拆解账目为价差捕获（+3.00）、中价变动（-5.80）与费用（-0.30）",
            "核对期末 100 股库存敞口与当日风控限额",
            "生成降仓或对冲建议单，交由值班人员复核决定",
        ],
    },
    "EXECUTION_PROTOCOL": {
        "runbook_id": "RB-EXEC-309",
        "root_cause_pattern": "撤单请求发出与撤单确认返回之间发生飞行中成交，导致本地缓存少记仓位。",
        "standard_sop": [
            "以交易所成交回报为准核对总成交量（60 股 vs 本地 40 股）",
            "确认漏记的 20 股回报序号并做幂等重放",
            "生成仓位修正建议单，送人工复核后再同步 OMS 仓位",
        ],
    },
    "DATA_GOVERNANCE_PIT": {
        "runbook_id": "RB-DATA-402",
        "root_cause_pattern": "历史数据修订场景下的双时间戳（Effective Date / As-Of Date）快照核验。",
        "standard_sop": [
            "确认查询按 as_of_date 过滤历史快照",
            "归档本次只读核验记录至审计日志",
        ],
    },
}

UNKNOWN_RUNBOOK: Dict[str, Any] = {
    "runbook_id": "RB-UNKNOWN-000",
    "root_cause_pattern": "未命中预设告警类别，无法自动匹配标准处置手册。",
    "standard_sop": [
        "保留原始异常快照并通知值班工程师人工排查",
    ],
}

# Mutating action names routed to human review in this prototype
WRITE_ACTIONS = {
    "ADJUST_RECON_LEDGER",
    "AUTO_HEDGE_INVENTORY",
    "SYNC_EXCHANGE_POSITION",
    "CANCEL_ALL_ORDERS",
    "TRANSFER_FUNDS",
}


@dataclass
class GuardrailDecision:
    allowed_auto_execute: bool
    policy_triggered: str
    reason: str
    required_state: str


@dataclass
class TriageTicket:
    ticket_id: str
    alert: Dict[str, Any]
    runbook_id: str
    retained_log_chars: int
    routing_confidence: float
    root_cause_analysis: str
    proposed_steps: List[str]
    guardrail: Dict[str, Any]
    status: str  # PENDING_APPROVAL | AUTO_ARCHIVED -> APPROVED | REJECTED
    human_reviewer: Optional[str] = None
    review_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentHarness:
    """Rule-based triage router: deduplicates/truncates logs, looks up Runbook, enforces review routing."""

    def __init__(self, max_log_lines: int = 4, max_line_chars: int = 240, confidence_threshold: float = 0.85):
        self.max_log_lines = max_log_lines
        self.max_line_chars = max_line_chars
        self.confidence_threshold = confidence_threshold

    def compress_context(self, alert: AuditAlert, raw_log_lines: List[str]) -> Dict[str, Any]:
        """Deduplicate log lines, retain at most max_log_lines, and truncate each line to max_line_chars."""
        deduped = list(dict.fromkeys(raw_log_lines))
        retained: List[str] = []
        for line in deduped[: self.max_log_lines]:
            if len(line) > self.max_line_chars:
                retained.append(line[: self.max_line_chars - 3] + "...")
            else:
                retained.append(line)
        total_chars = sum(len(line) for line in retained)
        return {
            "alert_id": alert.alert_id,
            "category": alert.category,
            "metrics": alert.metrics,
            "pruned_logs": retained,
            "retained_log_chars": total_chars,
        }

    def evaluate_guardrails(self, action: str, requires_write: bool, confidence: float) -> GuardrailDecision:
        """Route any write action or low-confidence ticket to PENDING_APPROVAL."""
        if action in WRITE_ACTIONS or requires_write:
            return GuardrailDecision(
                allowed_auto_execute=False,
                policy_triggered="ROUTE_WRITE_TO_HUMAN_REVIEW",
                reason=f"建议动作 `{action}` 涉及账本或仓位变更，原型将其送入人工复核队列，不执行自动写入。",
                required_state="PENDING_APPROVAL",
            )
        if confidence < self.confidence_threshold:
            return GuardrailDecision(
                allowed_auto_execute=False,
                policy_triggered="LOW_CONFIDENCE_ESCALATION",
                reason=f"路由置信度 {confidence:.2f} 低于阈值 {self.confidence_threshold:.2f}，转入人工复核。",
                required_state="PENDING_APPROVAL",
            )
        return GuardrailDecision(
            allowed_auto_execute=True,
            policy_triggered="READ_ONLY_AUDIT_PASS",
            reason="只读核验且命中预设类别，自动归档审计记录。",
            required_state="AUTO_ARCHIVED",
        )

    def triage(self, alert: AuditAlert, raw_logs: Optional[List[str]] = None) -> TriageTicket:
        logs = raw_logs or [
            f"[INFO] alert={alert.alert_id} category={alert.category}",
            f"[METRIC] {alert.metrics}",
            "[DEBUG] heartbeat ok",
            "[DEBUG] heartbeat ok",  # duplicate noise to be removed
        ]
        ctx = self.compress_context(alert, logs)
        known = alert.category in INCIDENT_RUNBOOKS
        runbook = INCIDENT_RUNBOOKS.get(alert.category, UNKNOWN_RUNBOOK)
        confidence = 0.96 if known else 0.60

        decision = self.evaluate_guardrails(
            action=alert.recommended_action,
            requires_write=alert.requires_write_privilege,
            confidence=confidence,
        )

        return TriageTicket(
            ticket_id=f"TKT-{alert.alert_id.split('-')[-1]}",
            alert=alert.to_dict(),
            runbook_id=runbook["runbook_id"],
            retained_log_chars=ctx["retained_log_chars"],
            routing_confidence=confidence,
            root_cause_analysis=runbook["root_cause_pattern"],
            proposed_steps=runbook["standard_sop"],
            guardrail=asdict(decision),
            status=decision.required_state,
        )


def apply_human_decision(ticket: TriageTicket, reviewer: str, approve: bool, note: str) -> TriageTicket:
    """State transition check for Human-in-the-Loop approval in Python."""
    if ticket.status != "PENDING_APPROVAL":
        raise ValueError(f"Invalid transition: ticket {ticket.ticket_id} is in state {ticket.status}")
    ticket.status = "APPROVED" if approve else "REJECTED"
    ticket.human_reviewer = reviewer
    ticket.review_note = note
    return ticket
