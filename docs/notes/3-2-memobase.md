# Chapter 3-2 · memobase 实验笔记

> 实验路径：`chapter3/memobase/`  
> 对应书中：实验 3-2「记忆策略横向对比」之 Memobase 对照实现

---

## 实验出发点

Agent 需要记住用户，但"记忆"本身并不单一：

- "用户喜欢简短回答" → **稳定偏好**，持续生效
- "用户昨天说要改会议" → **时序事件**，有时间戳，不能概括为偏好
- "用户正在讨论预算" → **当前上下文**，会话结束即过期

把三类信息用同一种结构存储会出问题：用临时事件覆写长期偏好，或把长期偏好当事件堆叠。

**核心问题**：如何设计记忆的表示与管理，让 Agent 在正确的时机用正确的信息回答？

memobase 实验从两个角度回答：
- **手写 Agent**：强迫自己实现每个环节（提取、存储、压缩、检索），观察哪里最容易出错
- **真实 Memobase SDK**：观察框架替你做了什么决策，以及它的代价是什么（黑箱、外部依赖）

---

## 知识地图

```
记忆系统
├── 记忆类型
│   ├── 情景记忆 (episodic)   — 具体事件，带时间
│   ├── 语义记忆 (semantic)   — 稳定事实 / 偏好  ← 手写实现未实现写入
│   ├── 程序记忆 (procedural) — 操作模式 / 习惯
│   └── 工作记忆 (working)    — 当前会话短时上下文
│
├── 记忆操作
│   ├── 提取 (extraction)     — 从对话中识别值得存的信息
│   ├── 写入 (add/store)      — 决定放到哪种类型
│   ├── 检索 (retrieval)      — 相关度 + 近因两轴
│   └── 更新/衰减 (decay)     — 旧信息降权，冲突信息如何处理
│
└── 框架设计
    ├── 手写实现  — 调用方代码决定写哪个桶，机制最透明
    ├── Memobase  — 服务端 LLM 自动分类 Profile/Event，框架决策
    └── Mem0      — ADD-only 追加，时间感知混合检索（见 3-2-mem0.md）
```

**触发时机是类型划分的真正关键：**

| 类型 | 应该在什么时候写入 | 手写实现状态 |
|------|-----------------|-------------|
| episodic | 每次对话发生 | ✅ 已实现 |
| working | 每条消息 | ✅ 已实现 |
| procedural | 任务完成后 | ✅ 已实现 |
| semantic | LLM 判断"这是稳定偏好/持久事实"时 | ❌ 未实现 |

---

## 两条线，不要混淆

| 线 | 入口 | 依赖 | 用途 |
|----|------|------|------|
| **手写 Agent** | `main.py` / `agent.py` | 只需 `KIMI_API_KEY` | 理解记忆机制，可本地跑 |
| **真实 Memobase SDK** | `profile_demo.py` | 需要 Memobase 服务（Docker 或云端） | 对比框架设计，观察 Profile + Event |

---

## 手写 Agent 架构

```
用户输入
    │
    ▼
MemobaseAgent.process_message()
    ├── 写 working memory（每条消息）
    ├── 检索相关记忆 → 注入 system prompt
    ├── 调 Kimi K3 → 生成回复
    ├── 写 episodic memory（用户消息 + 助手回复各一条）
    └── 对话历史溢出时 → 旧10条存 episodic，只保留新10条

MemoryStore（4 类型桶，defaultdict(list)，pickle 持久化）
    ├── episodic   — 每次对话自动写入
    ├── semantic   — ❌ 未实现写入，永远为 0
    ├── procedural — execute_task 完成后写入（学到了什么）
    └── working    — 每条用户消息写入，flush 时清空
```

---

## 逐层代码理解

### MemoryStore（agent.py）

四类记忆共用 `defaultdict(list)` 存储，pickle 落盘。

**重要：** `_save_memories()` 存盘时用了 `dict(self.memories)`，把 defaultdict 转成了普通 dict（第 127 行）。重新加载后就没有自动建 key 的能力，`_compress_memories()` 直接下标访问 `self.memories[memory_type]` 会报 `KeyError`。

**修复（第 115 行）：**
```python
# 原代码（有 bug）
self.memories = data.get('memories', defaultdict(list))

# 修复后
loaded = data.get('memories', {})
self.memories = defaultdict(list, loaded)
```

### MemobaseAgent.process_message()

每条消息的写入只有 episodic 和 working，没有 semantic：
```python
# 写 working
self.memory_store.add_memory(memory_type="working", ...)

# 写 episodic（用户消息 + 助手回复）
self._store_interaction("user", message)
self._store_interaction("assistant", assistant_response)
```

semantic memory 的写入路径**根本不存在**——整个代码库只有一处引用 `"semantic"`，在 `execute_task` 的统计计数里（第 505 行），是读不是写。

### 对话历史滑动窗口（第 438-447 行）

```python
if len(self.conversation_history) > 20:
    old_convs = self.conversation_history[:10]
    for conv in old_convs:
        self.memory_store.add_memory(memory_type="episodic", content=conv, importance=0.5)
    self.conversation_history = self.conversation_history[10:]
```

作用：防止 prompt 无限增长，超过 20 条时把旧 10 条存入 episodic。  
缺陷：每条消息单独存为一个 Memory 对象（格式是 `{"role": ..., "content": ...}`），原来的对话结构（谁说的、是否配对）丢失。真实系统会先 LLM 摘要再存档。

---

## Memobase SDK 架构（profile_demo.py）

```
用户对话（ChatBlob）
    │
    ▼  insert()          写入缓冲区，不触发任何 LLM
    │
    ▼  flush(sync=True)  服务端 LLM 统一提取（摊薄调用成本）
    │
    ├── Profile（稳定属性树）
    │     topic → sub_topic → content
    │     例：basic_info → 城市 → 上海
    │         work → 职位 → 后端工程师
    │         interest → 游戏偏好 → 塞尔达/艾尔登法环
    │
    └── Event Memory（时序事件）
          created_at / event_tip / event_tags
          例："上周把新项目预算定在了 50 万"
```

**Profile vs Event 的分类标准（服务端 LLM 判断）：**

| 特征 | 分到 Profile | 分到 Event |
|------|-------------|-----------|
| 时态 | 一般现在时、习惯 | 过去时、"上周/昨天" |
| 是否会变 | 相对稳定 | 一次性发生 |
| 例子 | "我住在上海" / "喜欢 Go" | "上周预算定了 50 万" |

**关键限制：** 提取模型在**服务端**配置，客户端的 `--model` 参数只是说明用，不能真正指定（`profile_demo.py` 第 260-262 行注释）。

---

## Benchmark 评分问题

运行 `--mode benchmark` 时，有记忆的表现反而更差。原因：

**1. 评分器惩罚记忆量大（`locomo_benchmark.py` 第 270 行）：**
```python
if sum(memory_usage.values()) < 100:
    score += 0.05  # "效率奖励"
```
运行过交互模式后累积了 188 条记忆，拿不到这 0.05 加分。无记忆模式是 0 条，直接加分。

**2. 记忆内容全是噪声：**  
semantic 永远为 0（见上），episodic 存的是之前交互的 Python 偏好对话，与复利计算/规划类任务毫无关系。记忆被召回后注入 prompt 反而干扰模型。

**3. 评分器本身是关键词计数，不是质量评估：**  
`_evaluate_reasoning()` 检查 "step 1"、"first"、"therefore" 等关键词是否出现，以及精确字符串 `"11576.25"` 是否在回答里。这无法区分"记忆真正帮助了回答"和"模型凑巧说了关键词"。

---

## 手写实现 vs Memobase SDK 核心差异

| 维度 | 手写实现 | Memobase SDK |
|------|----------|--------------|
| 分类决策 | 调用方代码决定写哪个桶 | 服务端 LLM 自动分类 |
| 更新策略 | decay（衰减）+ 压缩 | 追加（ADD 为主，服务端提取） |
| semantic 实现 | ❌ 未实现 | ✅ Profile 树 |
| 时序事件 | episodic（无结构化时间线） | ✅ Event Memory（时间线） |
| 可观测性 | 高（本地 pickle，可直接检查） | 低（服务端黑箱，只能看结果） |

---

## 实验观察

- demo 模式跑通，API 连通，记忆写入正常
- 用户说"最喜欢 Python + 函数式风格"（典型语义记忆），系统存成了 episodic —— 自动分类不总是准确
- benchmark 全部失败根本原因：pickle 加载后 defaultdict → dict，第一次触发 `_compress_memories` 时 `KeyError: 'procedural'`，已修复
- `profile_demo.py --dry-run` 只显示 Profile 结构示意，不显示 Event —— 因为 Event 是运行时动态生成的，没有服务无法给出示意数据

---

## 检验题回答

### 机制层

**Q1：情景记忆和语义记忆的 `decay_rate` 应该一样吗？**

不应该一样。语义记忆是稳定偏好（"我不吃辣"），应该衰减慢（低 `decay_rate`）；情景记忆是具体事件，时效性强，应该衰减快。但在当前实现里，semantic memory 写入路径根本不存在，`decay_rate` 的设计差异是有意义的，只是变成了死代码。

**Q2：压缩时为什么优先删低重要性的记忆而不是最旧的？**

"我不吃辣"可能两年前存入，很久没访问，但永远有效。纯按时间删除会把有价值的稳定偏好删掉。重要性 = 访问频率 × 时间衰减，更准确地反映"这条信息还有用吗"。按时间删是懒人做法，按重要性删才对。

**Q3：Mem0 ADD-only 在"用户更正错误信息"时出什么问题？**

旧的错误事实不会被删除，只追加新事实。两条并存，检索时都可能被返回。时间权重会让新事实排名更高，但旧事实不消失——在某些查询场景下仍可干扰回答，尤其是当查询词更匹配旧事实时。

---

### 设计层

**Q4：用户说"我上次告诉你我住北京，那是错的，我一直住上海"——三种实现各如何处理？**

| 实现 | 处理方式 | 结果 |
|------|----------|------|
| 手写 Agent | 没有冲突检测，写一条新 episodic | 北京和上海同时存在，检索可能同时返回 |
| Memobase SDK | 服务端 LLM 应把 `basic_info→城市` 更新为上海 | Profile 属性树支持覆盖，但不保证 LLM 判断正确 |
| Mem0 ADD-only | 追加"上海"，北京记录不删除 | 两条并存，时间权重优先上海，但北京未消失 |

**Q5：Memobase 把"连续三次选同款酒店"从 Event 升级为 Profile，这个决策应该在哪里做、由谁做？**

应该在独立的"记忆整合"步骤里，不是在单次 `flush` 里。`flush` 只看当前批次对话，无法跨批次归纳。需要类似手写实现的 `consolidate_memories()` 机制：定期扫描全部 Event，由 LLM 归纳出重复模式，再写入 Profile。这是 Memobase 目前的设计限制——跨批次的归纳需要额外工程，不是 flush 自带的。

---

### 工程层

**Q6：pickle vs Chroma，查询复杂度是多少，谁先出问题？**

- 手写 pickle：`search_memories` 全遍历 O(n)，字符串匹配，记忆几百条时开始明显变慢
- Mem0 Chroma：ANN 搜索 O(log n)，但每次存取都调 OpenAI embedding API，网络延迟是瓶颈，不是向量操作

手写在数据量上先出问题；Mem0 在网络/成本上先出问题（每条记忆写入都要 embedding 调用）。

**Q7：替换 Mem0 本地 embedding 成本地模型，改哪里？**

修改 `config.py` 里的 Mem0 embedder 配置：
```python
config.mem0.embedder = {
    "provider": "huggingface",
    "config": {"model": "BAAI/bge-m3"}
}
```
OpenRouter 覆盖不了这个——OpenRouter 没有 embeddings endpoint，`env.example` 里有注释说明这一点。所以即使用了 OpenRouter 做 chat LLM，embedding 仍然需要单独处理。

---

### 横向判断

**Q8：给一个需要上线的用户画像服务，选哪个方案？**

**推荐 Memobase SDK（真实服务）。**

理由：
1. 自动分类 Profile/Event，省去自己实现提取 LLM 的工程量
2. 服务端统一管理，天然支持多用户隔离

风险：
1. 服务端黑箱，提取质量取决于服务端 LLM 配置，调试困难
2. 依赖外部服务可用性，自托管需要额外运维

如果需要完全可控/可观测（比如金融、医疗场景），改选手写实现并补全 semantic 写入路径——可见性比便利性更重要。

---

## 运行命令

```bash
cd chapter3/memobase
source ../../.venv/bin/activate

# 手写 Agent
python main.py --mode demo
python main.py --mode interactive
python main.py --mode benchmark --num-tasks 5 --category multi_turn_reasoning
python main.py --mode benchmark --num-tasks 5 --category multi_turn_reasoning --no-memory

# 录制完整 session
script -q -c "python main.py --mode interactive" logs/session_$(date +%Y%m%d_%H%M%S).log

# 真实 Memobase SDK（需要服务端）
python profile_demo.py --dry-run          # 离线预览流程
python profile_demo.py --op profile       # 仅召回 Profile
python profile_demo.py --op event         # 仅查看 Event 时间线
python profile_demo.py --op context       # Profile + Event 合并上下文
```
