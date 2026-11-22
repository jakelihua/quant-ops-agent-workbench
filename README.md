# Quant Ops Agent Workbench

**Trading Support & Post-Trade Reconciliation Prototype (Synthetic Deterministic Demo)**

<p align="left">
  <a href="#简体中文"><strong>简体中文</strong></a> ·
  <a href="#english"><strong>English</strong></a>
</p>

---

<a id="简体中文"></a>
## 简体中文

这是一个基于 Python 3 标准库与原生前端构建的**交易支持与盘后对账原型**，用于演示如何用确定性规则复算交易差异，并将涉及账本或仓位变更的建议路由至人工复核。

> **边界声明**：本仓库所有行情与成交均采用显式合成数据，**未接入外部大模型 API 或实盘交易执行器**，不执行任何真实金融业务写入。

### 核心模块

```text
[4 组合成交易样例]
        │
        ▼
1. 确定性规则复算 (engine/rules.py)
   · 期货换月：拼接 280 vs 逐日毛利 160（净利 148），差额 120
   · 做市分解：价差 +3.00、中价变动 -5.80、费用 -0.30、净损益 -3.10、期末 100 股
   · 撤单竞态：100 股买单按 ExecID 幂等重放（EX-1001 40 股 + EX-1002 20 股，重复 EX-1002 忽略 1 条），回报 60 vs 本地 40，定位漏记 EX-1002（+20 股）
   · PIT 快照：04-10 初发 100 / 04-15 修订 80，09/11/16 日查询返回 None -> 100 -> 80
        │
        ▼
2. 工单路由原型 (agent/harness.py)
   · 日志去重、保留前 4 条并截断超长单行（上限 240 字符/行）
   · 按告警类别匹配预设 Runbook；未知类别降级至低置信度并转入人工复核
   · 将涉及账本或仓位变更的 3 项示例写动作路由至 PENDING_APPROVAL，仅只读核验自动归档
        │
        ▼
3. 浏览器工单演示台 (web/index.html)
   · 在浏览器内存中演示工单的批准与驳回状态流转（刷新即恢复初始状态）
```

### 快速复跑（无需安装第三方包）

```bash
git clone https://github.com/jakelihua/quant-ops-agent-workbench.git
cd quant-ops-agent-workbench

# 运行全部 13 个测试用例，并重新生成 evidence/workbench_snapshot.json
python3 verify.py
```

### 打开浏览器工单演示台

* **方式一（读取刚生成的快照）**：在仓库根目录启动本地静态服务后访问页面：
  ```bash
  python3 -m http.server 8000 --bind 127.0.0.1
  # 浏览器打开 http://127.0.0.1:8000/web/index.html
  ```
* **方式二（快速预览）**：直接双击打开 `web/index.html`，将自动回退读取页面内置的同口径样例数据。

---

<a id="english"></a>
## English

A zero-dependency **trading support and post-trade reconciliation prototype** built with Python 3 standard library and vanilla HTML/CSS/JS. It demonstrates how deterministic rules reconcile trading discrepancies and route proposed ledger or position mutations into a human-in-the-loop (HITL) review queue.

> **Scope & Disclaimer**: All market and execution scenarios use explicit synthetic datasets. This prototype does **not** connect to any external LLM API or live trading execution gateway, and performs **zero** real financial write operations.

### Architecture Overview

```text
[4 Synthetic Trading Scenarios]
        │
        ▼
1. Deterministic Audit Rules (engine/rules.py)
   · Futures Roll: naive stitched 280 vs mark-to-market gross 160 (net 148 after 12 fees) -> 120 roll gap
   · Market Making Decomposition: +3.00 spread capture, -5.80 mid-price drift, -0.30 fees -> -3.10 net PnL (100-share end inventory)
   · Cancel Race (ExecID Idempotent Replay): 100-share buy order (EX-1001 40 + EX-1002 20, 1 duplicate EX-1002 ignored) -> 60 exchange fills vs 40 local -> missing EX-1002 (+20 shares)
   · Point-In-Time (PIT) Snapshot: 100 published Apr 10, restated to 80 Apr 15 -> queries on Apr 9/11/16 return None -> 100 -> 80
        │
        ▼
2. Triage & Routing Prototype (agent/harness.py)
   · Log deduplication, top-4 retention, and per-line truncation (240 chars max)
   · Category-based preset Runbook lookup; unknown categories fall back to low confidence and escalate to human review
   · Routes all 3 mutating recommendations to PENDING_APPROVAL; auto-archives only read-only PIT audit
        │
        ▼
3. Browser Review Console (web/index.html)
   · Client-side demonstration of Approve / Reject ticket state transitions (refresh resets state)
```

### Quickstart (Zero External Dependencies)

```bash
git clone https://github.com/jakelihua/quant-ops-agent-workbench.git
cd quant-ops-agent-workbench

# Run all 13 deterministic test cases and regenerate evidence/workbench_snapshot.json
python3 verify.py
```

### Open the Browser Review Console

* **Option 1 (Load newly generated snapshot)**: Start a local static server from the repo root:
  ```bash
  python3 -m http.server 8000 --bind 127.0.0.1
  # Open http://127.0.0.1:8000/web/index.html
  ```
* **Option 2 (Instant preview)**: Double-click `web/index.html` directly to load the built-in matching fallback sample.

## License

MIT
