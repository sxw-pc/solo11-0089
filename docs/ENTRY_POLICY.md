# 入场核验随机政策与最优性证书

`microtag_harvest/entry_policy.py` 为已核实的合成 Event 签发**一轮事前随机核验政策**：每轮只执行抽中的一个 check，抽取概率非负且和为 1，且在当轮抽取之前公布。本文档给出数学约定、两份合同的含义以及离线复算方式。全部数值均为合成演练参数，不声称任何现实安保性能。

## 模型约定

- 一轮至多有一种伪造入场风险实际发生。
- 对手先观察公布的抽取概率 `p`，再选择风险 `r`，但看不到当轮随机结果。
- 风险 `r` 的漏检损失为
  `residual_r(p) = loss_r × (1 − Σ_check p_check × detection_check,r)`。
- 政策的最坏值是所有风险残余损失的**最大值**，不是先验平均。
- 先验期望避免额为
  `Σ_r prior_r × loss_r × (Σ_check p_check × detection_check,r)`。

## 两份独立合同

| 合同 | 目标 | 门槛（fixture） | 含义 |
|---|---|---|---|
| `deterrence_cover` | 最小化最坏漏检损失 | `max_worst_loss = 6` | 为每类可能的伪造入场购买覆盖，给出可签发的最坏风险承诺。 |
| `throughput_yield` | 最大化先验加权期望避免损失 | `min_expected_avoided = 6` | 为先验总体回收最多的核验吞吐量购买一轮检查。 |

两者改变的是真实核验抽取义务，不是放宽单个检查的宽严开关。最优政策也过不了门槛时合同**拒签**，门槛不会被调低。

## fixture 下的精确解

`deterrence_cover` 的最优政策（与 `fixtures/entry_screening/witness.json` 同值，分数约分不影响相等性）：

| check | 概率 |
|---|---|
| name-review | 8/87 |
| qr-verify | 0 |
| expiry-review | 27/87（求解器输出约分形式 9/29） |
| dual-quick | 52/87 |

三类风险的漏检损失均为 `160/29 ≈ 5.517 ≤ 6`，合同签发。

`throughput_yield` 的最优政策是纯 `dual-quick`：期望避免 `249/40 = 6.225 ≥ 6`，但对 `expired-pass` 检出率为 0，最坏漏检为 8——它**不能**提供最坏承诺，这正是两份合同必须独立存在的原因。

## 精确有理数 LP 与最优性证书

求解器只使用 `fractions.Fraction`，通过有界顶点搜索枚举可行多面体的全部顶点（最多 4 风险、5 checks，基组合数量很小），不依赖任何外部 LP 库，也不使用 Monte Carlo 样本。

### minimax 原始–对偶证书（`minimax_primal_dual`）

- 原始问题：在单纯形约束与 `Σ_a p[a]L[a,r] ≤ t` 下最小化 `t`。
- 对偶问题：对手混合策略 `q`，在 `Σ_r q[r]L[a,r] ≥ v` 下最大化 `v`。
- 原始可行解给出最坏损失上界；对手混合给出每个纯 check 期望漏检的下界。
- 两者**精确相等**（fixture 下均为 `160/29`）才声称全局最优。fixture 见证的对手混合为 `name-swap=10/29、cloned-qr=4/29、expired-pass=15/29`。

### 吞吐量顶点证书（`simplex_extreme_point`）

目标在概率单纯形上为线性，最优必在顶点（某个纯 check）取得。证书记录每个纯 check 的精确期望得分，证明被选 check 不劣于任何其他顶点。

### 离线复算

`verify_minimax_certificate` 与 `verify_throughput_certificate` 从原始 fixture 数值重新推导证书中的每一项；`verify_issued_report` 额外校验模型指纹。任何被篡改的分数都会被拒绝。

## 身份与矩阵绑定（拒绝陈旧证书）

每份报告携带 `model_fingerprint`，对 Event 标识、风险（损失/先验）、check 检出矩阵与合同门槛做规范化哈希：

- Event 实体身份变化（唯一 Event 核实失败）→ 整个签发失败。
- 风险矩阵或损失改动 → 指纹变化、政策与证书重新求解；旧报告在 `verify_issued_report` 下报 `stale certificate`。
- fixture 见证**没有**被硬编码为通用答案：求解器对任意合规输入重新求解，测试中的矩阵/损失扰动会产生不同政策。

## 输入校验

`validate_intake` 强制：唯一 Event、风险/check ID 唯一、非负损失、先验非负且和为 1、每个检出率在 `[0,1]`、每个 check 必须给每个风险一个检出率、最多 4 风险 5 checks。模型输出按 ID 规范化排序，因此输入重排不改变政策与指纹。

## 运行

```bash
python -B -m microtag_harvest.entry_screening_intake \
  --fixture fixtures/entry_screening/intake.json
```

旧的先验单 check 预览（`preview`）保留在同一输出的 `prior_preview` 字段中，用于对照它为何无法提供最坏承诺。
