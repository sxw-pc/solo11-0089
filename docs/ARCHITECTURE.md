# 架构与数据流

微标拾取台把本地 HTML 归档转换成可审计的实体簇、问题清单与 SQLite 台账。系统只读输入、不联网、不使用当前时钟，所有排序和标识均由输入内容与固定参数推导。

## 模块职责

| 模块 | 职责 |
|---|---|
| `cli.py` | 参数解析、子命令、退出码与错误输出。 |
| `config.py` | 输入/输出/契约/数据库路径、忽略规则和固定 `--now` 校验。 |
| `scanner.py` | 发现 `.html`/`.htm`，排序，计算 SHA-256 和字节数，标记重复内容。 |
| `parsers/common.py` | 统一候选记录 `Annotation`、字段值、locator、片段哈希和解析问题结构。 |
| `parsers/jsonld.py` | JSON-LD 数组、`@graph`、嵌套对象、`@type` 数组、远程 context 告警、非法 JSON。 |
| `parsers/microdata.py` | `itemscope`/`itemtype`/`itemprop`/嵌套 item/`itemid`。 |
| `parsers/rdfa.py` | `typeof`/`property`/`about`/`resource`/`vocab` 基础子集。 |
| `parsers/meta.py` | OpenGraph 与 Twitter Card 统一为 `Metadata` 候选字段。 |
| `contracts.py` | YAML 解析、字段/类型/格式/枚举/未知字段策略、实体核验模式（joint/independent）、行号错误、契约快照摘要。 |
| `validation.py` | 逐来源契约字段校验，以及合并后绑定 `entity_id` 的实体级核验结论与字段级证据（缺失/互补/冲突/无资格不可混同）。 |
| `normalize.py` | 空白折叠、URL base 解析与 fragment 移除、ISO 日期、标识符和嵌套值归一化。 |
| `merge.py` | 同页/跨页实体分组、字段合并、冲突保留、实体 ID 生成。 |
| `audit.py` | 重复 `@id`、悬空引用和有向引用环检测。 |
| `issues.py` | 稳定问题码、严重度、确定性 issue ID、证据结构和分组统计。 |
| `pipeline.py` | 串联扫描、提取、校验、合并、报告生成、产物写入和入库。 |
| `store.py` | SQLite 表结构、事务、幂等写入和只读查询 API 重导出。 |
| `queries.py` | 运行、文档、实体、问题、报告和原始片段的只读查询。 |
| `report.py` | JSON、Markdown、自包含 HTML、golden 摘要和报告比较。 |
| `web.py` | 只读 Flask 复核台；固定 loopback，设置 CSP 与安全响应头。 |

## 数据流

1. **扫描**：`scanner.discover` 按相对路径排序发现 HTML，计算 SHA-256，跳过默认忽略目录（`out`、`.git` 等），将后续同哈希文档标记为重复。
2. **抽取**：每份文档以 lxml 解析，依次运行 JSON-LD、Microdata、RDFa、Meta 抽取器。任何单文件或单抽取器失败只记录问题，不中断批次。
3. **契约校验（逐来源）**：候选实体的每个类型都按 YAML 契约检查必填、推荐、类型、格式、枚举、未知字段和未知类型；问题绑定到具体标注。
4. **归一化**：文本折叠空白；URL 以文档 `<base href>` 解析并移除 fragment；标识符保留 fragment；日期转换为 ISO-8601；嵌套对象/数组递归处理。
5. **合并**：有 `@id` 的候选跨文档合并；无 `@id` 的候选仅在文档内按 `(类型, name/headline)` 合并；无名称时保持独立。冲突值全部保留，不做静默覆盖。
6. **实体级核验**：合并分组与冲突驱动 `verify_entities` 产出实体结论（`pass` / `fail_missing` / `fail_conflict` / `ineligible`）和字段证据（`present` / `complementary` / `missing` / `conflict` / `ineligible`）。联合模式允许互补满足，独立模式要求每条来源自完整；两种模式都禁止用冲突值凑齐。只有成功解析且类型在契约声明的来源有资格参与。
7. **问题定位**：为每个问题记录文档、语法、locator、实体类型、字段、证据 SHA-256 和确定性 issue ID；引用图检测悬空链接与环。
8. **入库**：写入 `runs`、`documents`、`run_documents`、`annotations`、`entities`、`entity_sources`、`entity_verification`、`entity_field_evidence`、`issues`、`contracts`、`reports`。标注唯一约束保证重跑不增长；核验证据与报告指向同一组 annotation_id/locator/fragment_sha256。
9. **报告**：生成排序稳定的 JSON、Markdown 和自包含 HTML（含实体结论与字段证据表）；计算 golden 摘要与契约/参数/报告哈希。
10. **复核**：Flask 只读查询最近一次运行，展示文档、实体、字段来源、核验结论、原始片段（自动转义）和问题过滤。

## 关键数据结构

- **候选标注**：文档路径、文档 SHA-256、语法、locator、片段与片段哈希、类型列表、标识符、字段值列表、引用列表、base URL。
- **合并实体**：确定性 12 位实体 ID、类型列表、主类型、规范化主键、所有字段值及其来源、来源标注、冲突字段、实体核验结论。
- **实体核验**：实体结论与模式、有资格/无资格 annotation_id、解析失败槽位、逐类型必填字段证据（字段状态、提供值的 annotation_id/locator/fragment_sha256）。
- **问题**：稳定问题码、严重度、文档/语法/locator/实体类型/字段、证据哈希、详情。

实体 ID 由 `sha1(主类型 + "|" + 规范化主键)` 的前 12 位组成。主键优先为 `@id:`，其次为文档范围名称，最后为 locator 范围。

## 确定性与安全

- 不使用随机数、`sleep`、当前时间或网络请求。
- 输入路径以外的写入只发生在显式输出目录和数据库。
- 所有集合在输出前显式排序；报告使用排序键 JSON。
- 报告 HTML 内嵌 CSS，不引用 CDN、远程字体、脚本或图片。
- Web 只允许 GET/HEAD/OPTIONS 路由；不提供上传、写接口或文件浏览；CSP 禁止脚本、表单和外部资源。
- 证据只保存受控长度的片段和完整片段哈希，不把整页 HTML 无界写入问题日志。

## 失败处理

- 缺失输入目录或契约文件：CLI 返回非零。
- YAML 语法/语义错误：抛出带文件与行号的契约错误，CLI 返回 `2`，不写半成品运行。
- HTML 文件级解析失败：文档状态为 `error`，记录 `DOC_HTML_PARSE_ERROR`，继续其他文件。
- 单个语法抽取器异常：记录 `PARSER_FAILURE`，文档状态为 `partial`，继续其他语法和文档。
- JSON-LD 非法 JSON：记录 `PARSE_JSONLD_INVALID`，不中断。
