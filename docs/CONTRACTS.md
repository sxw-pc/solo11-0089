# 契约格式与错误码

契约是声明式的 YAML 文件。它定义允许的实体类型、必填/推荐字段、顶层字段类型、格式、枚举和未知字段策略。字段校验只覆盖实体顶层字段，不递归展开对象内部的契约。

## 完整示例

```yaml
name: sample-site
version: 1
unknown_type: error       # error | warn | ignore
unknown_field: warn       # 全局默认：error | warn | ignore
types:
  Product:
    required: [name, sku, offers]
    recommended: [image]
    fields:
      name: {type: string, format: nonempty}
      sku: {type: string, format: nonempty}
      image: {type: url, format: uri}
      category: {type: string, enum: [widgets, tools, samples]}
      offers: {type: object}
    # unknown_fields: ignore  # 可覆盖该类型的全局策略
```

- `name`：契约名称，写入运行元数据和契约快照。
- `version`：整数。
- `unknown_type`：实体出现契约未声明类型时的策略；默认 `error`。
- `unknown_field`：实体出现未声明顶层字段时的全局策略；默认 `warn`。
- `types`：非空映射，键为归一化后的类型名。
- `required` / `recommended`：字段名列表；缺失或空字符串/空数组/空对象都算缺失。
- `fields`：字段名到字段规格的映射；字段规格可写 `required: true` 作为必填的额外声明。

## 字段类型

| 类型 | 接受值 |
|---|---|
| `string` | 字符串。 |
| `url` | 字符串，且经文档 base 解析后可识别为 URL。 |
| `date` | 字符串，且归一化后为 ISO-8601。 |
| `number` | 整数或浮点数（不含布尔）。 |
| `integer` | 整数（不含布尔）。 |
| `boolean` | 布尔值。 |
| `array` | 数组。 |
| `object` | 对象。 |
| `identifier` | 字符串标识符。 |

## 格式

| 格式 | 规则 |
|---|---|
| `uri` / `url` | 归一化后以允许的 URI scheme 开头；相对 URL 按文档 base 解析。 |
| `iso8601` | 文本可归一化为 ISO-8601 日期或时间。 |
| `email` | 基本 `local@domain.tld` 形状；合成 fixture 使用保留示例域。 |
| `nonempty` | 折叠空白后非空。 |

字段 `enum` 对字符串先做空白折叠，再与允许值比较。URL 移除 fragment；标识符保留 fragment，因为 `@id` 的 fragment 具有语义。

## 类型数组与未知类型

实体 `@type` 可以是字符串或数组。每个类型分别校验；任一类型未声明都会根据 `unknown_type` 产生问题。类型名会从完整词汇 URL 或前缀形式归一化为最后一段，例如 `https://example.invalid/vocab/Product` 归一化为 `Product`。

## 契约错误

YAML 语法错误和语义配置错误都必须给出文件与行号，CLI 返回非零退出码：

```text
fixtures/contracts/site.yaml:5: unknown_type must be one of ['error', 'ignore', 'warn']
```

语义错误包括：根不是映射、名称为空、版本不是整数、策略非法、类型不是映射、必填/推荐不是字符串列表、字段不是映射、字段类型/格式非法、per-type 策略非法。

## 问题码

| 代码 | 严重度 | 含义 |
|---|---|---|
| `CONTRACT_UNKNOWN_TYPE` | 策略决定 | 类型未在契约声明。 |
| `CONTRACT_MISSING_REQUIRED` | `error` | 必填字段缺失或为空。 |
| `CONTRACT_RECOMMENDED_MISSING` | `warn` | 推荐字段缺失或为空。 |
| `CONTRACT_TYPE_MISMATCH` | `error` | 顶层字段类型不匹配。 |
| `CONTRACT_FORMAT_INVALID` | `error` | 字段格式不匹配。 |
| `CONTRACT_ENUM_INVALID` | `error` | 字段值不在枚举中。 |
| `CONTRACT_UNKNOWN_FIELD` | 策略决定 | 顶层字段未声明。 |

抽取、合并和存储相关问题码：

| 代码 | 严重度 | 含义 |
|---|---|---|
| `DOC_DUPLICATE_CONTENT` | `info` | 文档字节与更早路径重复。 |
| `DOC_HTML_PARSE_ERROR` | `error` | 文件级 HTML 解析失败。 |
| `PARSER_FAILURE` | `error` | 单个语法抽取器异常。 |
| `PARSE_JSONLD_INVALID` | `error` | JSON-LD 脚本不是合法 JSON。 |
| `REMOTE_CONTEXT_NOT_LOADED` | `warn` | 发现远程 JSON-LD context，仅记录不加载。 |
| `JSONLD_ENTITY_WITHOUT_TYPE` | `warn` | JSON-LD 对象没有可用 `@type`。 |
| `MICRODATA_ITEM_WITHOUT_TYPE` | `warn` | 顶层 Microdata item 没有 `itemtype`。 |
| `RDFA_ENTITY_WITHOUT_TYPE` | `warn` | RDFa 实体没有可用 `typeof`。 |
| `ENTITY_DUPLICATE_ID` | `warn` | 同文档同语法重复使用 `@id`。 |
| `MULTI_SYNTAX_CONFLICT` | `warn` | 多语法描述同一实体且标量字段冲突。 |
| `FIELD_VALUE_CONFLICT` | `warn` | 合并来源对同一字段给出不同值。 |
| `DANGLING_REFERENCE` | `warn` | 引用无匹配规范化 `@id`。 |
| `REFERENCE_CYCLE` | `warn` | 引用图存在环。 |

每条问题都携带文档相对路径、语法、locator、实体类型、字段和片段证据哈希；问题 ID 由这些稳定字段与消息/详情共同摘要生成。
