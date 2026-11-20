# Chapter 3-3 · Log Sanitization 实验笔记

> 实验路径：`chapter3/log-sanitization/`  
> 对应书中：实验 3-3「Agent 日志脱敏」

---

## 术语

**PII（Personally Identifiable Information）**：能直接或间接识别个人身份的数据。按敏感程度分级：
- Level 1：姓名、邮箱、手机号（低风险）
- Level 2：IP 地址、Cookie、位置信息（中风险）
- Level 3：SSN、信用卡号、医疗记录、生物特征（高风险，直接造成财产或人身损害）

**Luhn 算法**：信用卡号校验算法。从右往左奇数位翻倍（>9 减 9），所有位求和能被 10 整除则合法。用于降低正则误报率，不能验证卡是否真实存在。

---

## 实验出发点

Agent 把工具调用结果和用户输入原样写入日志，日志就成了 PII 的集散地。正则规则快速确定但无法识别自然语言语境中的 PII（"患有糖尿病"），LLM 能处理语义但速度慢且可能幻觉。这个实验的核心问题是：**两种引擎各自能处理什么级别的 PII，以及脱敏激进度和日志可用性之间怎么取舍**。

---

## 知识地图

```
日志脱敏
├── 引擎 A：离线正则（regex_sanitizer.py）
│   ├── 18 类规则，按优先级排列（私钥 → IP）
│   └── 校验函数降低误报：Luhn（信用卡）、校验码（身份证）
│
└── 引擎 B：本地 LLM（agent.py）
    ├── qwen3:0.6b via Ollama，结构化 JSON schema 约束输出
    ├── 检测 Level 3 PII（医疗、全址、生物特征）
    └── 防幻觉：_value_appears_in_text() 验证 LLM 输出值在原文中存在
```

---

## 架构

```
两条独立路径，不合并

正则路径：
  输入文本
      ↓
  regex_sanitizer.sanitize()
      ├── 按优先级逐条规则匹配
      ├── 校验函数过滤误报（Luhn/身份证校验码）
      ├── 重叠去重（优先级高的胜出）
      └── 位置替换 → 带类型占位符 [REDACTED_EMAIL] [REDACTED_API_KEY]...

LLM 路径：
  输入文本
      ↓
  detect_pii()         — LLM 返回 PII 值列表（verbatim 子串）
      ↓ _value_appears_in_text() 过滤幻觉
  sanitize_text()      — re.sub 逐值替换
      └── 统一占位符 [REDACTED]（无类型标签）
```

---

## 核心机制

### `_RULES` 优先级排列（`regex_sanitizer.py` 第 60-183 行）

每条规则是一个五元组 `(类别, 占位符, 正则, 分组号, 校验函数)`。越具体危险的越排前面（私钥 > JWT > API Key > 信用卡 > IP），防止宽泛模式（如 `\d+`）吃掉高价值信息的上下文。

```python
_RULES = [
    ("private_key", "[REDACTED_PRIVATE_KEY]", re.compile(r"-----BEGIN ..."), 0, None),
    ("jwt",         "[REDACTED_JWT]",          re.compile(r"eyJ..."),         0, None),
    ...
    ("credit_card", "[REDACTED_CREDIT_CARD]",  re.compile(r"\b(?:\d[ -]?){13,19}\b"), 0, _luhn_ok),
    ("ip_address",  "[REDACTED_IP]",           re.compile(r"\b(?:...)\b"),    0, None),
]
```

**`secret_assignment` 分组号用 tuple `(1, 2, 3)` 的原因**：三种引号风格（双引号/单引号/无引号）对应三个互斥 group，同一次匹配只有一个有值。代码逐个试，取第一个 `start >= 0` 的：

```python
for g in groups:           # 依次试 1、2、3
    start, end = m.span(g)
    if start >= 0:
        break
```

### `sanitize()` 重叠去重（`regex_sanitizer.py` 第 186-239 行）

所有规则跑完后统一去重：先按优先级排，对每个候选 span 检查是否与已接受的 span 有重叠，有则跳过。

### `detect_pii()` 防幻觉过滤（`agent.py` 第 215/218 行）

LLM 返回的每个值都要过 `_value_appears_in_text()` 验证在原文中真实存在：

```python
if value and isinstance(value, str) and _value_appears_in_text(value, conversation_text):
    pii_values.append(value)   # 幻觉输出在这里被过滤
```

注意 legacy_values 分支（第 222-228 行）跳过了这个检查，存在漏洞。

### `sanitize_text()` LLM 路径替换（`agent.py` 第 250-270 行）

拿到 PII 值列表后，逐个做 `re.sub(re.escape(value), '[REDACTED]', text)`。简单直接，但丢失了类型信息（统一 `[REDACTED]`，不像正则有 `[REDACTED_EMAIL]`）。

---

## 实验观察

**LLM 路径未能跑起来** → 根因：ollama 0.40.15 安装不完整，缺少 `llama-server` 推理二进制，`/usr/local/lib/ollama/` 下只有 CUDA 库没有实际 runner。正则路径不依赖 ollama，可独立验证。

**两条路径完全独立** → 没有"先正则再 LLM 补充"的 pipeline，是两种互斥运行模式。这意味着 LLM 路径不会自动继承正则的类型标签，重叠处理也更简陋。

---

## 检验题回答

**机制层：`secret_assignment` 为什么用 `(1, 2, 3)` tuple？**

三种引号风格互斥，同一次匹配只有一个 group 有值。用 tuple 是为了逐个试到有值的那个，单个数字只能覆盖一种引号风格。

**设计层：LLM 路径如何和正则保持一致的处理形式？**

实际上**没有对齐**。正则是位置替换 + 带类型标签，LLM 是值替换 + 通用 `[REDACTED]`。LLM 路径无法做精确 span 替换（LLM 返回的是值字符串，不是位置），所以用子串匹配兜底。代价是：
1. 丢失类型标签，排障时看不出原来是什么类型的 PII
2. `re.sub` 大小写不敏感可能误伤相似子串

**设计层：`_value_appears_in_text` 防止了什么？**

0.6B 小模型会幻觉输出不存在于原文的值（比如把"李四"推测成 SSN）。这个验证确保只替换原文中真实存在的子串，避免误删。

---

## 个人项目参考

- **分层脱敏**：正则处理格式化 PII（API key、信用卡、邮箱），LLM 处理自然语言语境 PII（医疗、地址）→ 适合 Agent 日志落盘前的两阶段处理 → 两阶段 pipeline 比两种模式互斥更完整，但本实验未实现
- **防 LLM 幻觉**：用 `value in text` 子串验证过滤 LLM 输出，比完全信任模型输出安全得多 → 适合任何"要求 LLM 返回原文中存在的值"的场景 → 注意大小写策略要一致
- **校验函数降误报**：正则 + 业务校验（Luhn、身份证校验码）的组合，比纯正则精度高，比 ML 分类器轻量 → 适合离线/低延迟场景 → 有校验算法的类别用校验，没有的接受一定误报率

---

## 未解决的问题

- LLM 路径在实验中未能实际运行（ollama llama-server 缺失），Level 3 PII 的实际检出率未验证
- 两条路径的 pipeline 合并（先正则再 LLM 补 Level 3）在本实验中未实现，值得自己动手验证
