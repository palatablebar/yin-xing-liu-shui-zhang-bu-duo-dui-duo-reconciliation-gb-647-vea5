# 运行手册 ledgerbank

## 启动与停止

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:18206/api/health
docker compose down -v --remove-orphans
```

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

命名卷 `ledgerbank-data` 保存批次、流水行、候选与匹配。重建后从「载入样例」重新开始。

## 主流程

1. `POST /api/demo/run`：读取两份脏样例，采信、质量、候选、匹配一次跑完。
2. `GET /api/quarantine`：查看隔离行与原因分布。
3. `GET /api/candidates`：查看已生成候选。
4. `GET /api/matches`：查看匹配组与手续费分摊。
5. `GET /api/differences`：查看两侧守恒与差异归因（发布后默认回放冻结版本，`?scope=workspace` 看实时）。
6. `GET /api/export/report.csv` 与 `GET /api/export/manifest.json`：导出（发布后与冻结版本同版）。

## 关账发布（月底）

1. 确认当前最新批次为 `matched`，半成品（`partial_*`）不能发布。
2. 预览补录影响（只读，不写版本）：
   ```bash
   curl -s "http://127.0.0.1:18206/api/publications/preview?cutoff=2026-03-31T18:00"
   ```
   返回本次锁定的 `run_id`、基准版本，以及受影响的匹配组、未匹配项与手续费明细。
3. 发布正式版本：
   ```bash
   curl -s -X POST http://127.0.0.1:18206/api/publications \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "cutoff=2026-03-31T18:00"
   ```
   cutoff 无时区时按 Asia/Shanghai 解释；只冻结该时点之前最新已匹配批次。
4. 在页面 `/publications` 复核版本历史，下载冻结报告与清单；旧版状态为 `superseded` 仍可原样下载。
5. 发布后到达的手续费/凭证补录：重新导入两侧文件生成新批次，再用更晚的 cutoff 发布下一版；cutoff 早于最近发布会被拒绝（409 `E_CUTOFF_REGRESS`）。

### 发布幂等与中断

- 同一 cutoff 重试返回同一 `publication_id` 与版本号，不新增批次；并发请求由数据库串行事务保证只落一行。
- 发布中断时该行停留在 `publishing`，期间导出自动回退实时数据；按同一 cutoff 重试即原地修复，旧版文本不删除。
- 报告与清单的 sha256 记录在发布行中，下载后可自行比对。

## 单步重跑

```bash
curl -s -X POST http://127.0.0.1:18206/api/candidates
curl -s -X POST http://127.0.0.1:18206/api/matches
```

同一输入重跑命中同一 `run_id`，先删后插，行数与合计不变。

## 导入自有文件

```bash
curl -s -X POST http://127.0.0.1:18206/api/statements/import \
  -F side=bank -F file=@sample_data/bank_statement_dirty.csv
```

支持 UTF-8（含 BOM）、UTF-8 无 BOM、GB18030；列名按 `GET /api/contract` 的别名表映射。

## 失败处理

- 400：参数或文件不可用，页面顶部提示原因，保留已填内容。
- 404：批次或样例不存在，先执行 `POST /api/demo/run`。
- 409：还没有候选就求解匹配，先执行 `POST /api/candidates`。
- 隔离行不是失败：按原因码修正源文件后重新导入。

## 日志

```bash
docker compose logs -f app
```
