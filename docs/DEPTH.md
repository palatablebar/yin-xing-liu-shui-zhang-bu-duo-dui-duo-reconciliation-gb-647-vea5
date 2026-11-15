# 深度登记 ledgerbank

- 规模：中型（8 个功能模块 / 8 个页面 / 101 条测试）
- 核心规则：方向相同、金额差 ≤ 0.02 元、日期差 ≤ 3 天的候选；一对一、一对多、多对一按层级与金额差确定性择一；多对一用最多 4 条的受限子集和；手续费单独计入银行侧并按最大余额法分摊。
- 使用者与工作流：连锁零售财务共享组会计每月把 12 个账户的银行流水与账簿凭证放在一起核对；一笔付款拆多张凭证、多笔流水合并付一张发票，原先逐条手工比对，现在一次运行得到匹配组、未匹配清单、隔离证据与两侧守恒。月底关账时出纳按截止时点发布冻结版本，发布后到的手续费与凭证补录只影响下一版，不改写已发报告。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 流水采信 | 导入银行流水或账簿凭证，看到编码、分段与逐行诊断 | POST /api/statements/import、页面 / | `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/segments.py` | `tests/test_m1_ingest.py` |
| M2 金额日期规则 | 查看列定义、借贷符号、容差与日期窗口 | GET /api/contract | `app/modules/m2_contract/contract.py` `app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py` |
| M3 质量隔离 | 按原因码看隔离行并导出 CSV | GET /api/quarantine、页面 /quarantine | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` | `tests/test_m3_quality.py` |
| M4 候选匹配 | 生成一对一、一对多、多对一候选 | POST /api/candidates、页面 /candidates | `app/modules/m4_candidates/candidate.py` `app/modules/m4_candidates/window.py` | `tests/test_m4_candidates.py` |
| M5 多对多求解 | 求解不重叠匹配组，看手续费分摊 | POST /api/matches、页面 /matches | `app/modules/m5_matching/subset.py` `app/modules/m5_matching/fees.py` `app/modules/m5_matching/solve.py` | `tests/test_m5_matching.py` |
| M6 差异与批次 | 看两侧守恒、差异归因与历史批次 | GET /api/differences、GET /api/batches、页面 /batches | `app/modules/m6_diff/differences.py` `app/modules/m6_diff/history.py` | `tests/test_m6_differences.py` |
| M7 报告导出 | 导出报告 CSV 与运行清单 JSON | GET /api/export/report.csv、页面 /export | `app/modules/m7_export/csv_out.py` `app/modules/m7_export/manifest_out.py` | `tests/test_m7_export.py` |
| M8 关账发布 | 定截止时点、预览补录影响、发布冻结版本并下载同版报告 | GET /api/publications/preview、POST /api/publications、页面 /publications | `app/modules/m8_publication/cutoff.py` `app/modules/m8_publication/impact.py` `app/modules/m8_publication/snapshot.py` `app/modules/m8_publication/service.py` | `tests/test_m8_publication.py` |

端到端：`tests/test_e2e.py`（样例 → 采信 → 质量 → 候选 → 匹配 → 差异 → 导出，含 6 个页面 200、幂等重跑与失败输入恢复）；不变量：`tests/test_invariants.py`；关账发布：`tests/test_m8_publication.py`（截止时点、补录影响、冻结同版、中断恢复、并发幂等）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-06 | 金额写成 `1,200.50`、`２，１００．００` | `Decimal()` 直接抛错或把全角字符当单位 | NFKC 全角转半角、去千分位与币种符号、括号负数，再 Decimal 到分 | `app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py::test_thousands_and_fullwidth_amount` |
| TRAP-07 | 金额列出现 `-` / `N/A` / 空串 | 被当成 0 参与合计，合计虚低 | 哨兵表集中定义，金额返回 None 并进隔离，手续费哨兵按 0 计 | `app/config.py`、`app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py::test_sentinel_amount_returns_none` |
| TRAP-09 | 流水号 9007199254740993 超过 2^53 | JSON 往返或 CSV 输出变成科学计数法 | 业务号全程按字符串保存，导出逐字保留 | `app/modules/m7_export/csv_out.py`、`app/store.py` | `tests/test_m7_export.py::test_big_txn_no_scientific_notation` |
| TRAP-10 | 22 笔金额用 float 累加 | 合计与逐笔复算差 0.01 | 金额一律 Decimal，合计用同一条路径量化到分 | `app/modules/m5_matching/solve.py`、`app/modules/m6_diff/differences.py` | `tests/test_m5_matching.py::test_bank_signed_total_matches_decimal_sum` |
| TRAP-11 | 同一时刻写成 `2026-03-03T10:00:00+08:00` 与 `2026-03-03T02:00:00Z` | 落进两个日期桶，日期差多一天 | 先归一 UTC 再派生本地日；纯日期按 +08:00 当日 00:00 | `app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py::test_instant_offsets_collapse_to_same_utc` |
| TRAP-14 | 同一流水号两条不同金额（1250.50 / 999.00） | 后写覆盖，匹配金额随文件顺序变化 | 保留发生时间最早一条，时间相同取行号小者，其余进冲突隔离 | `app/modules/m3_quality/rules.py` | `tests/test_m3_quality.py::test_duplicate_txn_keeps_earliest` |
| TRAP-16 | 文件末行没有换行且列数不足 | 半行被当成有效流水 | 末行缺列且无换行时判 `TRUNCATED_TAIL` 进隔离 | `app/modules/m1_ingest/segments.py` | `tests/test_m1_ingest.py::test_truncated_tail_rejected` |
| TRAP-18 | 0 字节 / 仅表头 / 全空行 | 都返回「成功 0 行」，用户不知道文件有没有内容 | 三种空态分别返回 `EMPTY_FILE`、`HEADER_ONLY`、`ALL_BLANK` | `app/modules/m1_ingest/reader.py` | `tests/test_m1_ingest.py::test_empty_states_are_three_outcomes` |
| TRAP-19 | 同一对流水同时命中一对一与子集候选 | 结果取决于候选生成顺序 | 层级优先 + 金额差 + 日期差 + 业务号排序，输入顺序变化不影响结果 | `app/modules/m5_matching/subset.py` | `tests/test_m5_matching.py::test_result_independent_of_candidate_order` |
| TRAP-23 | 日期差正好 3 天与 4 天；23:59:59 与次日 00:00 | 端点重复计入或跨日归错桶 | 本地日桶左闭右开 `[日初, 次日初)`；窗口按自然日差 ≤3 | `app/modules/m4_candidates/window.py` | `tests/test_m4_candidates.py::test_window_boundary_three_days_included` |
| TRAP-24 | 手续费按比例分摊后合计差一分 | 各自四舍五入，分摊合计不等于实际合计 | 最大余额法分到 0.01 元，按组号确定性分配尾差 | `app/modules/m5_matching/fees.py` | `tests/test_m5_matching.py::test_fee_allocation_sums_to_actual` |
| TRAP-25 | 关账发布后手续费才入账、凭证才补录 | 按当前文件重发，已发出的报告被静默改写 | 发布按 cutoff 锁定当时最新已匹配批次，报告与清单原文冻结落库，补录只能经新版本体现，旧版标 superseded 但完整保留 | `app/modules/m8_publication/cutoff.py` `app/modules/m8_publication/snapshot.py` | `tests/test_m8_publication.py::test_frozen_report_survives_later_supplement` |
| TRAP-26 | 同一补录被出纳重试或双击发布 | 每点一次新增一个批次，报告被重复计数 | publication_id 由 run_id + cutoff 决定，`BEGIN IMMEDIATE` 串行事务内幂等判定，同 id 重试沿用原版本号，不新增行 | `app/store.py` `app/modules/m8_publication/ids.py` | `tests/test_m8_publication.py::test_retry_same_cutoff_keeps_single_publication`、`test_concurrent_identical_publishes_create_one_row` |
| TRAP-27 | 发布过程中断，留下半个快照 | 下载到空文件，或旧版已被覆盖无法回退 | 快照文本与版本写入同一事务；残留只可能是同 id 的 publishing，期间导出回退实时数据，重试原地修复且旧版文本不删 | `app/store.py` `app/modules/m7_export/handler.py` | `tests/test_m8_publication.py::test_interrupted_publication_keeps_old_version_and_retries_in_place` |

## 对账规则

- 银行合计 = 银行匹配金额 + 银行未匹配金额 − 手续费合计。
- 账簿合计 = 账簿匹配金额 + 账簿未匹配金额。
- 银行笔数 = 匹配涉及银行行数 + 未匹配银行行数；账簿同理。
- 手续费分摊合计 = 匹配组实际手续费合计。
- 匹配差额 = 银行匹配金额 − 账簿匹配金额，容差 0.02 元内允许非 0，并在差异清单与页面显式列出。
- 以上五账在 `/api/differences`、页面 `/batches` 与报告 CSV 中同时展示。
- 关账发布后，`/api/differences`（默认 scope=published）、`/api/export/report.csv` 与 `/api/export/manifest.json` 都回放同一发布快照；需要工作区实时数字时显式传 `scope=workspace`。

## 关账发布语义（M8）

- 截止时点：`POST /api/publications` 必须带 ISO-8601 的 cutoff（无时区按 Asia/Shanghai）；只选 `created_at <= cutoff` 的最新 `matched` 批次，截止后到达的数据不进本版，没有合格批次返回 409。
- 补录影响：预览以最近一次发布的来源批次为基准，列出匹配组（新增/消失/成员或手续费变动）、未匹配项（新出现/已消解）与手续费（补录/调整/合计差额），全部金额为 Decimal 字符串。
- 版本：新 cutoff 发布版本号 +1，上一版置 `superseded` 并记录 `superseded_by`；cutoff 早于最近发布返回 `E_CUTOFF_REGRESS`，已发布版本不可回退。
- 同版：冻结报告与清单复用 M7 导出函数生成，附 sha256；清单内嵌完整对账结论供差异接口回放。
- 幂等：`publication_id = sha256(run_id | cutoff)`，同 id 重试不新增批次、不重复计数；并发发布由 `BEGIN IMMEDIATE` 串行化。

## 反例与不变量

- 反例（朴素实现会挂）：`tests/test_m5_matching.py::test_bank_signed_total_matches_decimal_sum`（float 累加对不上分）、`tests/test_m1_ingest.py::test_truncated_tail_rejected`（`readlines()` 会把半行当数据）、`tests/test_m3_quality.py::test_duplicate_txn_keeps_earliest`（后写覆盖会得到 999.00）、`tests/test_m5_matching.py::test_many_to_one_subset_bounded_to_four`（5 笔合并会被错误凑成一笔）、`tests/test_m4_candidates.py::test_window_four_days_excluded`（宽窗口会多配一对）。
- 不变量：`tests/test_invariants.py::test_bank_conservation`（守恒）、`tests/test_invariants.py::test_disjoint_assignment`（一组不占两行）、`tests/test_invariants.py::test_fee_conservation`（分摊守恒）、`tests/test_invariants.py::test_rerun_row_counts_stable`（幂等）、`tests/test_invariants.py::test_quarantine_excluded_from_totals`（隔离不计入合计）。
- 端到端：`tests/test_e2e.py::test_full_pipeline_from_samples`。

## 已知不覆盖

- 凭证科目层拆分不做，只按银行账户与金额对账。
- 多币种不折算；外币金额若带币种代码以外字符会进隔离区，样例只覆盖人民币。
- 手续费只按组内银行金额比例分摊，不区分银行收费与第三方收费。
- 跨月批次不合并，一次运行对应一份月度流水。
