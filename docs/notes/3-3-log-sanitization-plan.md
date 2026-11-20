# 实验规划：Log Sanitization

> 路径：`chapter3/log-sanitization` | 预计专注时间：2-3 小时

---

## 术语

**PII（Personally Identifiable Information，个人身份信息）**：能直接或间接识别个人身份的数据。按敏感程度分级：
- Level 1：姓名、邮箱、手机号（低风险，泄露影响有限）
- Level 2：IP 地址、Cookie、位置信息（中风险，可追踪行为）
- Level 3：SSN、信用卡号、医疗记录、生物特征（高风险，直接造成财产或人身损害）

**Luhn 算法**：信用卡号校验算法。从右往左，奇数位数字翻倍（超过 9 减 9），所有位求和，结果能被 10 整除则合法。用途：排除随机 16 位数字的误报，不能验证卡是否真实存在。

---

## 出发点（Why）

Agent 把工具调用结果、用户输入原样写入日志时，日志文件就成了 PII 的集散地——但脱敏太激进又让日志失去排障价值。这个实验要回答：**正则规则和本地小模型各自能处理什么级别的 PII(Personally Identifiable Information)，误报和漏报的权衡点在哪里**。

---

## 知识地图（What）

```
日志脱敏
├── 引擎 A：离线正则（regex_sanitizer.py）
│   ├── 18 类规则，按匹配优先级排列（私钥 → IP）
│   └── 校验函数降低误报：Luhn（信用卡）、校验码（身份证）
│
└── 引擎 B：本地 LLM（agent.py）
    ├── qwen3:0.6b via Ollama，结构化 JSON 输出
    ├── 检测 Level 3 PII（医疗记录、全址、生物特征——regex 识别不了的）
    └── 防幻觉过滤：_value_appears_in_text() 验证输出值在原文中存在
```

---

## 实验目的（Goal）

运行完这个实验，你应该能回答：
1. 给定一段含 10 种 PII 类型的日志，regex 引擎能召回几种？哪种会误报？
2. LLM 路径对"张三患有糖尿病，住在上海市徐汇区天钥桥路 XXX 号"这类自然语言 PII 能否正确提取？
3. `_value_appears_in_text()` 防止了什么问题，去掉它会出现什么错误？

---

## 高 ROI 实验步骤

> 原则：先跑起来看输出，再提问题，再动手验证，不要先读代码。

| 步骤 | 操作 | 观察什么 |
|------|------|----------|
| 1 | `cd chapter3/log-sanitization && python main.py --demo` | 哪些类别被命中，哪些漏掉了，占位符格式是什么 |
| 2 | 在 `samples.py` 里找一条 sample，手动加一个"4111 1111 1111 1111"（Luhn 通过）和"1234 5678 9012 3456"（Luhn 失败），重跑 demo | 验证 Luhn 校验是否实际过滤 |
| 3 | `python main.py --mode llm --test-id <任意>` 或查看 `campaign.py` 的入口，用包含医疗/地址 PII 的样本测 LLM 路径 | LLM 是否提取出 regex 漏掉的信息；JSON 解析是否有异常 |
| 4 | 修改 `_RULES` 临时把 `ip_address` 规则移到最前面 | 观察 IP 地址的误报率是否上升（包住了版本号等） |

**值得读的代码**（核心机制，读完能回答设计决策问题）：

| 文件 | 读哪里 | 为什么 |
|------|--------|--------|
| `regex_sanitizer.py` | `_RULES` 列表（第 60-183 行） | 优先级排列逻辑和分组号含义（0 vs N vs tuple）|
| `regex_sanitizer.py` | `sanitize()`（第 186-240 行） | 多规则顺序应用的核心循环，如何避免重复脱敏 |
| `agent.py` | `detect_pii()`（第 139-248 行） | JSON schema 约束输出 + `_value_appears_in_text` 防幻觉 |
| `config.py` | `SYSTEM_PROMPT`（第 28-52 行） | Level 3 PII 的分类定义，prompt 如何约束模型行为 |

**跳过**：`metrics.py`（性能采集细节）、`test_loader.py`（测试数据加载格式）、`validation/`（输出评估格式）、`campaign.py`（批量测试脚手架）

---

## 设计决策（Design Decisions）

1. **正则优先级排列**：私钥 > JWT > URL 凭证 > API Key > 信用卡 > IP——越具体危险的越先匹配，防止低优先级的宽泛模式（如 `\d+`）吃掉高价值信息的上下文
2. **校验函数（Luhn / 身份证校验码）**：正则无法区分真实卡号和随机 16 位数，校验函数是降低误报的第一道防线；替代方案是 ML 分类器，但对离线轻量场景成本过高
3. **LLM 只做 Level 3**：0.6B 模型识别格式化 PII（如信用卡号）能力不稳定，regex 更可靠；LLM 的优势在自然语言语境（"患有糖尿病"），两者互补而不是替代
4. **`_value_appears_in_text` 过滤**：0.6B 模型会幻觉输出不存在于原文的值，这个验证是第二道防线，把"声称找到但不存在的"过滤掉

---

## 失败模式（Failure Modes）

1. **IP 地址误报**：`192.168.1.100` 是私网地址，不是 PII，但规则会全打；正则无法区分"内网调试 IP"和"用户真实 IP"
2. **secret_assignment 过宽**：`password=test` 中 `test` 也会被脱敏，日志里 test 环境凭证全消失，本地调试时反而麻烦
3. **LLM JSON 解析失败**：0.6B 模型低温下也可能生成不合法 JSON，`json.JSONDecodeError` fallback 到按行分割——fallback 质量极低，会误把普通句子当 PII 值
4. **顺序无法保证幂等**：先用 regex 再用 LLM，如果 LLM 返回 `[REDACTED_EMAIL]`（已被 regex 脱敏的占位符），`sanitize_text()` 会再次尝试替换——实际不会出错但需要确认

---

## 思考题（Think）

**机制层**（跑一下就能验证）
- 给 `sanitize()` 传一段含 `password = "hello" secret: world` 的文本，观察两个变量的分组号分别是哪个被命中

**设计层**（需要想清楚才能回答）
- 为什么 `secret_assignment` 的分组号是 `(1, 2, 3)` 的 tuple 而不是单个数字？
- 如果把 LLM 探测到的 PII 类型加回到 regex 引擎（动态规则），会引入什么风险？

**工程层**（上线时会遇到的）
- Ollama 服务不可达时，整个 LLM 路径是否有降级到只用 regex 的兜底？目前代码里有没有？
- 假设日志量 100MB/天，哪个引擎先成为瓶颈？如何设计两阶段 pipeline 控制成本？

---

## 要跳过的陷阱

- [ ] 不要花时间研究 18 条正则的具体语法，只需理解**优先级机制**和**校验函数的作用**
- [ ] 不要在 `campaign.py` 的批量测试框架上纠结，先用单条 sample 手动验证
- [ ] qwen3:0.6b 不一定每次 JSON 格式都正确，遇到解析错误是正常现象，观察 fallback 行为即可

---

## 实验观察（实验后填写）

[待填写]

---

## 检验题答案（实验后填写）

[待填写]
