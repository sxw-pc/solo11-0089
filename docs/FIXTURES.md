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

`fixtures/contracts/site.yaml` 声明 `Organization`、`Person`、`Product`、`Event`、`Article` 和 `Metadata`。`Metadata` 使用 `unknown_fields: ignore`，其余类型使用全局 `unknown_field: warn`；未知类型策略为 `error`。

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

## 赞助竞价 fixture

`fixtures/sponsor_auction/intake.json` 绑定合成站点中经采集器核实的 `Event`
（`https://example.invalid/events/spring#event`），并声明三个位置
`hero`、`sidebar`、`footer`、两份授权销售合同 `first_price` 与
`externality_price`，以及五份虚构报价：

| 报价 | 广告主 | 位置 | 金额 |
|---|---|---|---|
| `b1` | `adv1` | `hero`+`sidebar` | 20 |
| `b6` | `adv1` | `hero` | 16 |
| `b2` | `adv2` | `hero` | 15 |
| `b3` | `adv3` | `sidebar` | 3 |
| `b4` | `adv4` | `footer` | 5 |

`b1` 与 `b6` 是同一广告主的互斥替代报价，最多赢一份。组合最优为
`b1+b4 = 25`；逐位置原子预览只能得到 `b6+b3+b4 = 24`。同一分配下
`first_price` 卖方收入 25，`externality_price` 卖方收入 18（`b1` 付 18、
`b4` 付 0）。所有数据均为本地虚构素材。

## 扩展 fixture

新增页面时：

1. 使用保留示例域和虚构实体。
2. 在页面中提供明确 `<base href>`，避免依赖机器路径。
3. 若要新增契约类型或字段，先更新 YAML 契约。
4. 运行 `make demo`，检查新增问题是否符合预期。
5. 更新 golden 后运行完整测试。

不要将真实抓取归档、个人数据、凭据或真实域名加入 `fixtures/`。
