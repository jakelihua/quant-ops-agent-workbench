# Quant Ops Agent Workbench · 量化交易支持智能化工作台原型

> **AI 工程师进量化机构，不写 Alpha 因子也能创造核心价值？**  
> 本仓库演示一套面向头部量化机构**交易支持系统（Trading Support）与盘后对账（Post-Trade Reconciliation）**的 AI-Native 全栈工程原型。

## 为什么做这个项目？

头部量化机构最稀缺的，不是只会写几行 Prompt 的“AI 调包侠”，而是能把 **确定性金融规则 + Agent Harness 执行层 + 金融级护栏（Guardrails）+ 全栈人工复核控制台（Human-in-the-Loop）** 端到端落地的**智能化工程师**。

在资金与仓位高度敏感的交易台场景里，**绝对不能允许大模型直接触发写操作**。本仓库给出了一套可直接复跑验证的生产级工程范式：

```text
[行情/成交/账本异常]
        │
        ▼
┌───────────────────────────────────────────┐
│  1. Deterministic Rules Engine (engine/)  │  确定性财务与协议核算（零幻觉）
│  · 期货换月 120 元跨期虚增检测            │
│  · 做市 +3.00 价差 / -5.80 漂移库存敞口   │
│  · 撤单竞态 20 股飞行中成交与 70bp IS     │
│  · PIT 双时间戳财报修订防穿越核验         │
└───────────────────┬───────────────────────┘
                    ▼
┌───────────────────────────────────────────┐
│  2. Agent Harness & Guardrails (agent/)   │  状态剪枝 · 置信度路由 · 金融护栏
│  · 上下文去噪与 Token 预算压缩            │
│  · Runbook 故障知识库归因                 │
│  · 写操作强制拦截 (Autonomous Writes = 0) │
└───────────────────┬───────────────────────┘
                    ▼
┌───────────────────────────────────────────┐
│  3. HITL Trading Support Console (web/)   │  全栈审单台 · 交易员一键审批/驳回
└───────────────────────────────────────────┘
```

## 快速复跑（零外部依赖，1 秒验证）

只需要本机安装 Python 3（无需安装任何第三方包）：

```bash
git clone https://github.com/jakelihua/quant-ops-agent-workbench.git
cd quant-ops-agent-workbench

# 一键运行全部 12 项确定性规则、护栏拦截与 HITL 状态机断言
python3 verify.py
```

运行成功后，终端将输出 `ALL CHECKS PASSED: 12/12 deterministic assertions verified.`，并生成结构化审计快照 `evidence/workbench_snapshot.json`。

### 预览全栈交易支持审单台

直接用浏览器打开 [`web/index.html`](web/index.html)，即可交互式体验告警队列、Agent 归因证据链、金融护栏拦截提示与人工审批（Approve / Reject）状态流转。

## 目录结构

- `engine/rules.py`：4 类量化交易台真实异常的确定性规则引擎（全部采用清晰可复算的合成数据）。
- `agent/harness.py`：Agent Harness 执行层，包含上下文去噪压缩、工具路由置信度校验、硬性金融护栏与 HITL 状态机。
- `web/index.html`：零依赖全栈交易支持控制台原型。
- `verify.py`：12 项端到端确定性自动化断言套件。
- `evidence/workbench_snapshot.json`：运行 `verify.py` 自动产出的最新审计留档。

## License

MIT
