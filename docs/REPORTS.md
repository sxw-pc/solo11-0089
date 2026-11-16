# 报告结构

一次 `run` 会写出 `documents.json`、`annotations.json`、`report.json`、`report.md` 和自包含 `report.html`，并把报告文件摘要写入 SQLite `reports` 表。

## JSON 报告

顶层结构：

```json
{
  "schema_version": 1,
  "run": {
    "run_id": "确定性 12 位摘要",
    "generated_at": "固定 --now",
    "now": "固定 --now",
    "input_dir": "输入目录",
    "contract": {
      "name": "sample-site",
      "version": 1,
      "sha256": "契约源码 SHA-256",
      "path": "契约路径",
      "summary": {"types": {}}
    },
    "params": {"ignores": [], "out_dir": "out", "db_path": "out/ledger.sqlite3"},
    "counts": {
      "documents": 6,
      "annotations": 17,
      "entities": 13,
      "issues": {
        "total": 34,
        "by_severity": {"error": 9, "warn": 24, "info": 1},
        "by_code": {}
      }
    }
  },
  "documents": [],
  "entities": [],
  "issues": [],
  "issue_groups": {}
}
```

### documents

按相对路径稳定排序，字段为 `path`、`sha256`、`size`、`parse_status`、`duplicate_of`、`error`。`parse_status` 为 `ok`、`partial`、`error` 或扫描模式的 `pending`。

### entities

按 `entity_id` 排序。每个实体包含：

- `entity_id`：`sha1(主类型 + "|" + 规范化主键)` 前 12 位。
- `types` / `primary_type`：排序后的类型和主类型。
- `key`：`@id:`、文档范围名称或 locator 范围主键。
- `identifier`：规范化标识符。
- `sources`：来源标注 ID、文档、语法、locator、片段哈希，稳定排序。
- `fields`：每个字段的归一化值、原始值、文档、语法、locator、片段哈希和标注 ID。所有冲突来源都保留。
- `conflicts`：冲突字段名排序列表。
- `verification`：实体级契约核验结论，包含：
  - `verdict`：`pass` / `fail_missing` / `fail_conflict` / `ineligible`；
  - `mode`：契约声明的 `joint` 或 `independent`；
  - `reasons`：`ineligible` 时的原因（`unknown_type`、`extraction_incomplete`）；
  - `eligible_annotation_ids` / `ineligible_annotation_ids`：有资格与无资格来源；
  - `failed_extractions`：解析失败槽位（文档、语法、locator、片段哈希）；
  - `type_results`：逐契约类型的必填字段证据，每条含字段状态（`present` / `complementary` / `missing` / `conflict` / `ineligible`）及提供值的 annotation_id、locator、fragment_sha256 和归一化值。

### issues

按文档路径（大小写折叠）、locator、代码、字段、实体类型、issue ID 排序。每条包含稳定 `issue_id`、代码、严重度、文档、语法、locator、实体类型、字段、证据哈希、消息和详情。问题 ID 与证据哈希共同支持跨运行比较。

### issue_groups

按严重度和问题码统计，和 `run.counts.issues` 一致。

## Markdown 报告

包含运行元数据、严重度/问题码计数、文档表、实体字段表、实体契约结论与逐字段证据表、来源信息和问题表。Markdown 单元格会转义 `|` 并折叠换行，适合直接审阅或粘贴到工单。

## 自包含 HTML 报告

- 所有 CSS 内嵌在 `<style>` 中。
- 不引用 CDN、远程字体、外部脚本、外部图片或远程样式。
- 所有动态文本由 Jinja 自动转义。
- 展示运行信息、计数、文档、实体字段、实体核验结论与字段证据、来源 locator、证据哈希和问题清单。
- 报告只在本地生成，不包含外部链接。

## Golden 摘要

`fixtures/expected/report_summary.json` 是 JSON 报告的确定性摘要，包含：

- schema 版本、固定时间和契约 SHA-256；
- 全部计数；
- 文档路径、哈希、解析状态和重复来源；
- 实体 ID、主类型、主键、类型、来源数量、冲突字段和核验结论（verdict/mode/reasons 与逐字段状态及 annotation_id）；
- 问题 ID、代码、严重度、文档、locator 和字段。

校验：

```bash
python -m microtag_harvest golden \
  --report out/report.json \
  --expected fixtures/expected/report_summary.json
```

显式更新基线：

```bash
python -m microtag_harvest golden \
  --report out/report.json \
  --expected fixtures/expected/report_summary.json \
  --write
```

golden 不一致时命令返回 `1`，并在 stderr 输出计数差异以及新增/移除的实体 ID 和问题 ID。

## 报告比较

```bash
python -m microtag_harvest compare out/report.json another/report.json
```

输出结构：

```json
{
  "baseline_run": "...",
  "current_run": "...",
  "issues": {
    "new": [],
    "fixed": [],
    "remaining": [],
    "counts": {"new": 0, "fixed": 0, "remaining": 0}
  },
  "entities": {
    "before": 13,
    "after": 13,
    "delta": 0,
    "added_ids": [],
    "removed_ids": []
  }
}
```

所有数组按 issue ID 或实体 ID 排序。比较以 issue ID 为准，因此比较器既能看到同码新增/修复，也能保留问题详情变化导致的 ID 差异。

## 确定性条件

同一输入字节、同一契约、同一 `--now` 与同一忽略规则下：

- 报告 JSON 字节稳定；
- 问题顺序、实体 ID、文档顺序稳定；
- 重跑不增加标注行；
- SQLite 中的运行、实体、问题和报告记录按确定性 `run_id` 幂等；
- golden 校验通过。
