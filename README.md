# 微标拾取台 · Microtag Harvest

微标拾取台是一个纯本地、完全离线、结果可复现的「页内语义微标注采集与契约核验台」。它读取已经落盘的本地 HTML 归档，抽取页内 JSON-LD、Microdata、RDFa 基础子集、OpenGraph 与 Twitter Card 元数据，按声明式 YAML 契约校验字段，完成归一化、实体合并、冲突定位和证据留存，并写入 SQLite 台账，输出 JSON、Markdown 和自包含 HTML 报告，以及只监听 `127.0.0.1:5017` 的只读复核台。

## 定位与边界

适用：

- 处理本人所有或已获授权的本地 HTML 归档。
- 对完成采集的页面做页内语义标注抽取、契约质检、实体去重和审计。
- 在无网络环境中复现同一批次的抽取结果、问题清单和 golden 摘要。
- 用声明式契约驱动字段必填、类型、格式、枚举和未知字段策略。
- 在合并后的实体上做实体级契约核验：联合（joint）模式允许跨语法/跨文档来源互补满足必填字段，独立声明（independent）模式要求每条来源自完整；字段缺失、互补满足与冲突未裁定分别留证，冲突值在两种模式下都不能凑齐通过。

不适用，也不实现：

- 真实网络抓取、下载、DNS/HTTP 客户端、代理、UA 轮换或反爬绕过。
- JavaScript 执行、浏览器启动、动态渲染。
- HTML 表格抽取、正文抽取、评论抽取、列语义推断、图片二进制处理。
- 通用 XPath/CSS 采集器、任务调度器、分布式队列、云存储。
- WARC 归档、OAI-PMH 收割、页面变更监控、表单填报 RPA。
- 账号体系、权限 RBAC、写操作 API、文件上传、远程管理。
- 完整 JSON-LD、Microdata、RDFa 或公共词汇规范校验器。
- 抓取合规扫描、robots.txt 判定或站点链接巡检。

## 安装

要求 Python 3.11 或更高版本。

```bash
python -m venv .venv
# POSIX: . .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

运行期不访问网络。安装依赖时需要本地或预先准备好的 Python 包环境；项目不依赖 Node、Docker、外部数据库、消息队列或浏览器二进制。

## 快速开始

```bash
make test     # 等价于 pytest -q
make demo     # 扫描、抽取、契约校验、合并、入库、报告、golden 校验
make serve    # 如无台账先执行 demo，然后监听 127.0.0.1:5017
```

`make demo` 使用固定时间 `2026-01-01T00:00:00Z`、合成归档 `fixtures/site` 和契约 `fixtures/contracts/site.yaml`。`make serve` 启动只读 Flask 复核台；它不提供登录、写接口、上传或文件浏览。

直接调用 CLI：

```bash
python -m microtag_harvest run \
  --input fixtures/site \
  --contract fixtures/contracts/site.yaml \
  --out out \
  --now 2026-01-01T00:00:00Z

python -m microtag_harvest compare out/report.json another/report.json
python -m microtag_harvest golden --report out/report.json \
  --expected fixtures/expected/report_summary.json
python -m microtag_harvest serve --db out/ledger.sqlite3
```

## CLI 子命令

| 子命令 | 作用 |
|---|---|
| `run` | 执行完整流水线并写出文档清单、标注清单、三份报告和 SQLite 台账。 |
| `scan` | 仅发现并哈希 HTML 文件，写出 `documents.json`；此模式不抽取，状态为 `pending`。 |
| `compare BASELINE CURRENT` | 比较两份 JSON 报告，输出新增、修复、仍存问题和实体数量变化。 |
| `golden` | 从 JSON 报告生成确定性 golden 摘要，或校验已有摘要；`--write` 用于显式重建。 |
| `serve` | 启动只读复核台，固定监听 `127.0.0.1:5017`。 |

`run` 常用参数：

- `--input`：本地 HTML 归档目录，默认 `fixtures/site`。
- `--contract`：YAML 契约，默认 `fixtures/contracts/site.yaml`。
- `--out`：输出目录，默认 `out`。
- `--db`：覆盖 SQLite 台账位置，默认 `<out>/ledger.sqlite3`。
- `--now`：固定 ISO-8601 时间，默认 `2026-01-01T00:00:00Z`；禁止依赖当前时钟。
- `--ignore`：额外忽略 glob，可重复或使用逗号分隔。

退出码：成功为 `0`；契约错误、文件缺失、时间参数错误或 golden 不一致为非零。契约错误会输出 `文件:行号: 说明`。

## 输出目录

执行 `make demo` 后：

| 文件 | 说明 |
|---|---|
| `out/documents.json` | 文档清单：相对路径、SHA-256、字节数、解析状态、重复来源。 |
| `out/annotations.json` | 每条候选标注的语法、locator、片段、片段哈希、字段和引用。 |
| `out/report.json` | 完整机器可读报告。 |
| `out/report.md` | 便于审阅的 Markdown 报告。 |
| `out/report.html` | 内嵌 CSS 的自包含 HTML 报告，无外部资源。 |
| `out/ledger.sqlite3` | SQLite 台账。 |

原始 HTML 只读：流水线不会修改、移动或删除输入文件。

## 报告与确定性

JSON、Markdown、HTML 报告的顶层内容包括运行元数据、契约摘要、文档清单、实体簇、问题清单和分组计数。排序规则固定为文档路径、实体 ID、问题文档/locator/代码/字段/ID；字段和来源同样稳定排序。

同一输入、同一契约、同一 `--now` 下重复执行 `run`，`report.json` 字节稳定；同一标注不会因为重跑而增长。SQLite 以 `document_sha256 + syntax + locator + fragment_sha256` 作为标注唯一约束，并区分不同运行、保留契约快照、参数快照和报告哈希。

## 测试

```bash
pytest -q
```

或：

```bash
make test
```

测试完全离线，使用 `tmp_path` 生成临时输出，不依赖当前时间、当前工作目录或随机数。覆盖 JSON-LD 数组与 `@graph`、非法 JSON、远程 context 告警、Microdata 嵌套、RDFa 基础属性、OpenGraph/Twitter Card、契约策略、归一化、实体合并、实体级核验（联合/独立双模式、冲突阻断、缺失与未知/解析失败区分）、问题码、SQLite 幂等与证据回读一致、报告确定性、golden、compare、只读 Web 与转义展示。

## Fixture 来源与合规

`fixtures/site` 全部是手工编写的合成内容。示例域名仅使用 `example.invalid`、`localhost`、`127.0.0.1` 或 `::1`；不包含真实密钥、令牌、真实公司名、真实域名或真实个人信息。页面中的产品、人物、组织、活动和事件均为虚构样例。

项目只应处理使用者本人所有或已获明确授权的本地归档。仓库本身不包含抓取器、网络客户端或远程数据，也不对归档来源的授权状态作判断。

## 已知限制

- 解析器是有边界的子集实现，不追求完整规范覆盖；不支持 Microdata `itemref`、完整 RDFa 上下文/前缀解析或 JSON-LD 远程 context 求值。
- 远程 JSON-LD context 只记录告警，不加载。
- 不做 schema 级别的嵌套字段契约校验；契约字段校验针对实体顶层字段。
- 日期归一化支持常见 ISO 日期/时间和英文月份样例，不做任意自然语言日期解析。
- 实体合并规则固定为：同文档按 `@id` 或 `(类型, name/headline)`；跨文档仅按规范化 `@id`。
- 本地归档没有真实来源地址时，以 `<base href>` 为基准；缺失时使用保留的 `example.invalid` 合成基准。
- 复核台只读取最近一次运行，不提供历史对比 UI；历史比较请使用 `compare` 子命令。

## 文档

- [架构与数据流](docs/ARCHITECTURE.md)
- [契约格式与错误码](docs/CONTRACTS.md)
- [报告结构](docs/REPORTS.md)
- [Fixture 说明](docs/FIXTURES.md)
