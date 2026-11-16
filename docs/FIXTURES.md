# Fixture 说明

`fixtures/` 下的全部 HTML、YAML 和期望数据均为手工编写的合成对象，用于离线测试和演示。它们不来自任何真实网站、客户数据或生产抓取。

## 数据合规

- 示例域名只使用保留域名 `example.invalid`，以及 `localhost`、`127.0.0.1` 或 `::1`。
- 不包含真实密钥、令牌、密码、真实公司名、真实域名或真实个人信息。
- 人物、组织、产品、活动和事件均为虚构。
- 邮件地址使用 `@example.invalid`。
- 页面中的 `<base href>` 使用 `https://example.invalid/...`；抽取时不发起任何请求。
- 仓库不包含真实 HTML 归档下载、网络客户端、CDN、远程字体或远程脚本。

## 合成站点

| 文件 | 内容 |
|---|---|
| `fixtures/site/catalog/widget.html` | JSON-LD `@graph` 中两个同 `@id` 的 Product；Microdata Product 与 JSON-LD 合并；OpenGraph 与 Twitter Card。 |
| `fixtures/site/people/avery.html` | JSON-LD 数组、Person 与 Organization、远程 context 告警、RDFa Person、悬空引用和循环引用。 |
| `fixtures/site/events/spring-meetup.html` | JSON-LD Event、Microdata Event、RDFa Article、日期文本归一化和元数据。 |
| `fixtures/site/edge/broken.html` | 故意触发非法 JSON、未知类型、缺失必填、类型错误、格式错误、枚举错误、未知字段、无类型 JSON-LD、无类型 Microdata 和远程 context 告警。 |
| `fixtures/site/edge/copy-a.html` | 无标注的普通合成页。 |
| `fixtures/site/edge/copy-b.html` | 与 `copy-a.html` 字节相同，用于重复文档标记和 `DOC_DUPLICATE_CONTENT`。 |

## 合成契约

`fixtures/contracts/site.yaml` 声明 `Organization`、`Person`、`Product`、`Event`、`Article` 和 `Metadata`。`Metadata` 使用 `unknown_fields: ignore`，其余类型使用全局 `unknown_field: warn`；未知类型策略为 `error`。该契约显式启用 `entity_verification.mode: joint`（联合模式，允许跨语法/跨文档互补满足必填字段）。

`fixtures/contracts/site_independent.yaml` 声明相同的类型与字段，但启用 `mode: independent`（独立声明模式）：每条有资格来源必须自己完整，互补来源不能让实体通过。两个契约配合同一输入即可在测试中验证业务结论随授权模式切换，而逐来源审计问题在两种模式下都保留。

契约字段覆盖字符串、URL、日期、对象、数组和枚举，并包含必填和推荐字段。

## Golden 摘要

`fixtures/expected/report_summary.json` 是 `make demo` 所用固定输入、契约和时间下的 golden 摘要。它由以下命令显式重建：

```bash
python -m microtag_harvest run \
  --input fixtures/site \
  --contract fixtures/contracts/site.yaml \
  --out out \
  --now 2026-01-01T00:00:00Z
python -m microtag_harvest golden \
  --report out/report.json \
  --expected fixtures/expected/report_summary.json \
  --write
```

重建后应运行 `pytest -q` 与 `make demo`；除有意更新 fixture 或规则外，不应修改 golden。

## 扩展 fixture

新增页面时：

1. 使用保留示例域和虚构实体。
2. 在页面中提供明确 `<base href>`，避免依赖机器路径。
3. 若要新增契约类型或字段，先更新 YAML 契约。
4. 运行 `make demo`，检查新增问题是否符合预期。
5. 更新 golden 后运行完整测试。

不要将真实抓取归档、个人数据、凭据或真实域名加入 `fixtures/`。
