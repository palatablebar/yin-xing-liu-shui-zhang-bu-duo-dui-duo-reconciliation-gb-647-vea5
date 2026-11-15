# 定义表 ledgerbank

## 数值

- 金额、手续费一律 `Decimal`，量化为 0.01 元，对外输出十进制字符串。
- 禁止 float 累加；合计由同一套 `Decimal` 路径复算。
- 银行侧签向金额 = 金额 × 方向（收 +1，付 −1）；签向手续费固定为负向支出。
- 大整数编号（如 9007199254740993）全程按字符串保存，不过 `int()`。

## 时间

- 存储与比较一律 UTC，页面按 +08:00 展示。
- 带偏移时间按其偏移换算；纯日期按 +08:00 当日 00:00 换算。
- 本地日桶是半开区间 `[日初, 次日初)`；候选日期差按本地日相差的自然日数计算。

## 键与去重

- 银行侧业务键：流水号；账簿侧业务键：凭证号。
- 同一业务键出现多条有效行：保留发生时间最早的一条，时间相同取文件行号小者，其余进隔离区。
- 业务键只用于去重与组内引用，不用金额或名称生成。

## 空值

- `null`、空串、`-`、`--`、`N/A`、`NA`、`NULL`、`NONE`、`未知` 是空值哨兵，只在一处定义。
- 金额为空值 → 隔离，原因码 `AMOUNT_SENTINEL`；手续费为空值 → 按 0 计。
- 空文件、仅表头、全空行是三种空态，分别返回 `EMPTY_FILE`、`HEADER_ONLY`、`ALL_BLANK`。

## 匹配与手续费

- 容差 0.02 元，窗口 3 天，子集上限 4 条；三者进入 run_id 的计算参数。
- 匹配金额只用金额与方向，不用手续费；手续费单独汇总在银行侧。
- 组内手续费分摊权重 = 组内银行签向金额绝对值，尾差按最大余额法分配，分摊合计必须等于组内实际手续费合计。
- 匹配差额 = 银行匹配金额 − 账簿匹配金额，容差内允许非 0。

## 状态

- 行状态：`valid`（参与对账）、`quarantine`（隔离，附原因码）。
- 批次状态：`partial_bank`、`partial_ledger`、`ready`、`matched`。
- 发布状态：`publishing`（中断残留，重试原地修复）、`published`（当前正式版）、`superseded`（被新版替代，文本完整保留）。
- 原因码表在 `app/reasons.py`，页面、CSV 与清单共用同一份名称。

## 关账发布

- `publication_id = "pub-" + sha256(run_id + "|" + cutoff_at)` 前 16 位；同批次同 cutoff 重试命中同一行，版本号不变。
- cutoff 统一归一化为 UTC ISO-8601 字符串；选批规则是 `status == matched` 且 `created_at <= cutoff` 的最新批次。
- 发布行内冻结报告 CSV、清单 JSON 原文与各自 sha256；清单另嵌完整 `reconciliation`（含 totals、counts、identities、differences），金额均为十进制字符串。
- 补录影响以最近发布的来源批次为基准：匹配组按 `match_id` 对齐（added/removed/changed），未匹配项按 `(side, txn_no)` 对齐（newly_unmatched/resolved），手续费按银行流水号对齐（fee_added/fee_changed）。
- 版本号由 `MAX(version_no)+1` 在同一 `BEGIN IMMEDIATE` 事务内分配；旧版写入 `superseded_by` 后不删数据。

## 运行清单

- `run_id` 由规则版本、两侧文件 sha256、参数共同计算，同一组输入重跑得到同一 `run_id`。
- 清单包含输入文件名、sha256、字节数、规则版本、参数、计数与耗时。
