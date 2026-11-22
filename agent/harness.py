"""Production-grade Agent Harness with Context Pruning, Confidence Routing,
Financial Guardrails, and Human-in-the-Loop (HITL) State Machine.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from engine.rules import AuditAlert


# Runbook knowledge base simulating enterprise Hybrid Retrieval / GraphRAG memory
INCIDENT_RUNBOOKS: Dict[str, Dict[str, Any]] = {
    "POST_TRADE_RECONCILIATION": {
        "runbook_id": "RB-FUT-101",
        "root_cause_pattern": "近远月主力合约切换时未按独立合约逐日盯市，跨期价差被误计为交易收益。",
        "standard_sop": [
            "锁定换月日近远月结算价与跨期价差（roll_gap_points）",
            "切换账本核算模式为逐合约独立持仓 + 逐日盯市（Mark-to-Market）",
            "生成调整分录提交清算与风控复核，严禁自动覆盖历史成交库",
        ],
    },
    "INTRADAY_RISK_EXPOSURE": {
        "runbook_id": "RB-MM-204",
        "root_cause_pattern": "单边趋势行情引发逆向选择，做市买单被动吃满导致库存敞口超限，中价漂移吞噬价差收益。",
        "standard_sop": [
            "拆分账目为 Spread Capture（价差捕获）与 Mid Drift（中价漂移）",
            "检查当前库存敞口是否触发策略风控阈值",
            "生成对冲或降仓建议单，交由值班交易员在控制台一键审批执行",
        ],
    },
    "EXECUTION_PROTOCOL": {
        "runbook_id": "RB-EXEC-309",
        "root_cause_pattern": "撤单指令网络传输窗口内与交易所撮合引擎发生竞态，产生飞行中成交（In-Flight Fill）。",
        "standard_sop": [
            "以交易所回报序列号（ExecID）为唯一真相源（Single Source of Truth）",
            "执行日志幂等重放（Idempotent Replay），确保二次回放新增事件为 0",
            "同步修正本地 OMS 仓位缓存并通知风控模块重算敞口",
        ],
    },
    "DATA_GOVERNANCE_PIT": {
        "runbook_id": "RB-DATA-402",
        "root_cause_pattern": "历史数据修订场景下的双时间戳（Effective Date / As-Of Date）隔离核验。",
        "standard_sop": [
            "确认查询引擎严格按 as_of_date 过滤历史快照",
            "归档本次合规核验记录至审计日志，无需人工介入写操作",
        ],
    },
}

# Strict financial guardrail policy: mutating actions can NEVER be auto-executed by an LLM/Agent.
FORBIDDEN_AUTONOMOUS_ACTIONS = {
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
    compressed_context_tokens: int
    routing_confidence: float
    root_cause_analysis: str
    proposed_steps: List[str]
    guardrail: Dict[str, Any]
    status: str  # DETECTED -> PENDING_APPROVAL | AUTO_ARCHIVED -> APPROVED | REJECTED
    human_reviewer: Optional[str] = None
    review_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentHarness:
    """Deterministic Agent Harness enforcing bounded context, confidence routing, and guardrails."""

    def __init__(self, max_context_tokens: int = 512, confidence_threshold: float = 0.85):
        self.max_context_tokens = max_context_tokens
        self.confidence_threshold = confidence_threshold

    def compress_context(self, alert: AuditAlert, raw_log_lines: List[str]) -> Dict[str, Any]:
        """Prune redundant log noise and cap context budget to prevent context rot."""
        deduped = list(dict.fromkeys(raw_log_lines))
        # Estimate compact token count deterministically
        pruned = deduped[:4]
        est_tokens = min(self.max_context_tokens, 64 + sum(len(line) // 2 for line in pruned))
        return {
            "alert_id": alert.alert_id,
            "category": alert.category,
            "metrics": alert.metrics,
            "pruned_logs": pruned,
            "estimated_tokens": est_tokens,
        }

    def evaluate_guardrails(self, action: str, requires_write: bool, confidence: float) -> GuardrailDecision:
        """Hard deterministic guardrail: never allow autonomous financial mutations."""
        if action in FORBIDDEN_AUTONOMOUS_ACTIONS or requires_write:
            return GuardrailDecision(
                allowed_auto_execute=False,
                policy_triggered="FIN_GUARDRAIL_NO_AUTONOMOUS_WRITE",
                reason=f"动作 `{action}` 涉及资金/仓位/账本变更，金融护栏强制拦截，必须进入人工审批队列（HITL）。",
                required_state="PENDING_APPROVAL",
            )
        if confidence < self.confidence_threshold:
            return GuardrailDecision(
                allowed_auto_execute=False,
                policy_triggered="HARNESS_LOW_CONFIDENCE_ESCALATION",
                reason=f"工具路由置信度 {confidence:.2f} 低于阈值 {self.confidence_threshold:.2f}，升级至人工复核。",
                required_state="PENDING_APPROVAL",
            )
        return GuardrailDecision(
            allowed_auto_execute=True,
            policy_triggered="READ_ONLY_AUDIT_PASS",
            reason="只读合规核验且置信度达标，允许自动归档审计记录。",
            required_state="AUTO_ARCHIVED",
        )

    def triage(self, alert: AuditAlert, raw_logs: Optional[List[str]] = None) -> TriageTicket:
        logs = raw_logs or [
            f"[INFO] alert={alert.alert_id} category={alert.category}",
            f"[METRIC] {alert.metrics}",
            "[DEBUG] heartbeat ok",
            "[DEBUG] heartbeat ok",  # duplicate noise to be pruned
        ]
        ctx = self.compress_context(alert, logs)
        runbook = INCIDENT_RUNBOOKS[alert.category]

        # Deterministic routing confidence based on exact rule + runbook alignment
        confidence = 0.96 if alert.category in INCIDENT_RUNBOOKS else 0.60
        decision = self.evaluate_guardrails(
            action=alert.recommended_action,
            requires_write=alert.requires_write_privilege,
            confidence=confidence,
        )

        return TriageTicket(
            ticket_id=f"TKT-{alert.alert_id.split('-')[-1]}",
            alert=alert.to_dict(),
            runbook_id=runbook["runbook_id"],
            compressed_context_tokens=ctx["estimated_tokens"],
            routing_confidence=confidence,
            root_cause_analysis=runbook["root_cause_pattern"],
            proposed_steps=runbook["standard_sop"],
            guardrail=asdict(decision),
            status=decision.required_state,
        )


def apply_human_decision(ticket: TriageTicket, reviewer: str, approve: bool, note: str) -> TriageTicket:
    """Strict state machine transition for Human-in-the-Loop approval."""
    if ticket.status != "PENDING_APPROVAL":
        raise ValueError(f"Invalid transition: ticket {ticket.ticket_id} is in state {ticket.status}")
    ticket.status = "APPROVED" if approve else "REJECTED"
    ticket.human_reviewer = reviewer
    ticket.review_note = note
    return ticket
