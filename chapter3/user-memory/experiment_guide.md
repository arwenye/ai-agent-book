# 实验指南：长期用户记忆系统

> Chapter 3 · user-memory · 教学实验文档

---

## 实验目的

掌握**长期用户记忆**的核心工程问题：

1. 如何把对话中的零散事实提取为可持久化的结构化记忆
2. 为什么"保存全部聊天记录"不等于"下次能用上相关信息"
3. 对话响应与记忆写入为何要分离到两个独立模块
4. 四种记忆格式（notes / enhanced_notes / json_cards / advanced_json_cards）各自的信息密度与适用场景

---

## 实验出发点

### 核心矛盾

LLM 没有跨会话记忆。把原始对话历史全部塞进上下文有三个问题：

| 问题 | 原因 |
|------|------|
| 上下文窗口溢出 | 历史对话线性增长，很快超出限制 |
| 信号/噪声比低 | 大量临时闲聊淹没真正重要的事实 |
| 对话延迟增加 | 每次回答前都要处理全量历史 |

### 本实验的解法

```
用户输入
  ↓
ConversationalAgent        ← 只读记忆，不写；负责当前响应
  ↓ （轮次计数器满足 interval）
BackgroundMemoryProcessor  ← 异步分析对话，决定 add/update/delete
  ↓
MemoryManager              ← 按所选模式持久化到 JSON 文件
```

**关键设计决策**：写入由后台处理器独占，对话 Agent 永远只读记忆，这样对话延迟与记忆更新完全解耦。

---

## 实验步骤

### Step 0 — 环境准备

```bash
# 从仓库根目录
uv sync --locked --python 3.12 --extra ch3
source .venv/bin/activate
cd chapter3/user-memory
cp env.example .env
# 编辑 .env，填入至少一个提供商的 API Key
```

### Step 1 — 冒烟：验证完整闭环

```bash
python quickstart.py
```

观察：
- Session 1 中三条用户消息（Python 偏好、项目、IDE 主题）各自触发了哪些 add 操作
- Session 2 中"I switched from PyTorch to JAX"触发了 update 还是新增了 add
- 最终 `data/memories/quickstart_user_memory.json` 里保存的内容

### Step 2 — 交互模式，感受分离架构

```bash
python main.py --mode interactive --user lab_user --memory-mode notes
```

按以下顺序输入，每条后按 Enter：

```
我是 Alice，软件工程师，主要写 Python。
我最近在做一个推荐系统，用 PyTorch。
process          ← 手动触发后台处理
memory           ← 查看当前记忆
我把框架换成了 JAX，性能更好。
process
memory           ← 对比前后差异
quit
```

**观察要点**：`process` 命令之前和之后，`memory` 命令的输出有何变化；PyTorch→JAX 的更新是产生了新条目还是替换了旧条目。

### Step 3 — 对比四种记忆格式

对同一份脚本依次运行四种模式（每次运行前用 `reset` 或删除 `data/memories/` 下的文件清空状态）：

```bash
python main.py --mode demo --memory-mode notes
python main.py --mode demo --memory-mode enhanced_notes
python main.py --mode demo --memory-mode json_cards
python main.py --mode demo --memory-mode advanced_json_cards
```

运行后打开 `data/memories/` 对应文件，填写下表：

| 模式 | 存储格式 | 保存了哪些字段 | 信息是否可追溯来源 | 适合什么场景 |
|------|----------|--------------|-----------------|------------|
| notes | | | | |
| enhanced_notes | | | | |
| json_cards | | | | |
| advanced_json_cards | | | | |

### Step 4 — 冲突与纠正

在 interactive 模式中构造一个信息更新场景：

1. 输入一条包含可验证事实的偏好（例如"我住在北京"）
2. `process` → `memory` 确认已保存
3. 输入纠正（"不对，我搬到上海了"）
4. `process` → `memory` 检查：是增加了一条新记录，还是更新了原记录？

预期行为：**更新**，不应出现"北京"和"上海"同时存在的矛盾记录。

### Step 5 — 量化基准（evaluation 模式）

```bash
python main.py --mode evaluation --memory-mode advanced_json_cards
```

记录输出的总分和分项分（准确率/相关性/一致性）。这是后续调优的基准线。

---

## 实验分析目标

### A. 架构理解

- `ConversationalAgent.chat()` 在哪里读取记忆、怎样把它注入 system prompt？
  - 对应代码：[conversational_agent.py](conversational_agent.py)
- `BackgroundMemoryProcessor.process_recent_conversations()` 什么条件下被触发？
  - 对应代码：[background_memory_processor.py](background_memory_processor.py)
- 两个模块共享同一个 `MemoryManager` 实例吗？读写是否需要加锁？

### B. 记忆提取质量

- 后台处理器判断"值得保存"的 prompt 是什么结构？
  - 在 `agent.py` 中找到 `UserMemoryAgent` 使用的 system prompt
- 模型会不会把"临时计划"（今天打算去超市）误存为"长期偏好"？
- 模型猜测的信息（用户没有明说的）会不会被写入记忆？

### C. 记忆格式取舍

- `notes` 模式下，`consolidate_memories()` 的两个 pass（去重 + 冲突解决）各自做了什么？
  - 对应代码：[memory_manager.py:269-362](memory_manager.py)
- `advanced_json_cards` 的 `backstory` 字段解决了什么问题（见 `memory_trajectory.json` 里的系统 prompt 注释）？

---

## 实验结果需要观察什么

### 记忆文件（data/memories/）

- **格式完整性**：notes 模式是否产生了扁平列表，advanced_json_cards 是否产生了分类树
- **来源字段**：每条记忆能否追溯到产生它的 session_id
- **时间戳一致性**：`created_at` 与 `updated_at` 在纠正场景下是否行为正确（created 不变，updated 更新）

### 对话输出（交互模式）

- 在 Session 2 中，Agent 是否能正确引用 Session 1 存下的事实
- 引用的事实有没有幻觉成分（Agent 自己补充了用户没说过的细节）

### 后台处理操作日志

- add / update / delete 三种操作的分布是否合理（大多数轮次应以 add 为主，纠正时才出现 update）
- 相邻两轮完全无关的闲聊是否正确触发"no updates needed"

### evaluation 分数

- 有记忆 vs 无记忆（`enable_memory_context=False`）的分差
- 四种模式的分数差异，以及与记忆文件复杂度的关系

---

## 基于实验的问题

这些问题没有唯一答案，逐题思考能帮你掌握本实验涉及的方法：

**Q1 — 分离架构**  
如果把记忆写入直接放在 `ConversationalAgent.chat()` 里（同步执行），会出现什么问题？分离到后台处理器能解决哪些问题，会引入哪些新的复杂性（比如后台写入与前台读取的时序）？

**Q2 — 什么值得保存**  
后台处理器用 LLM 来判断哪些信息值得写入记忆。这个判断本身有什么局限性？举出两类容易误判的场景（误存 / 漏存），并说明如何设计 prompt 或过滤规则来缓解。

**Q3 — 记忆模式选择**  
`notes` 和 `advanced_json_cards` 在实现上最大的区别是存储结构。这个结构差异对下游的检索和注入有什么影响？如果你在设计一个生产级记忆系统，会在什么条件下从 `notes` 升级到结构化卡片？

**Q4 — 冲突解决**  
`consolidate_memories()` 用时间戳决定胜者（最近更新的覆盖旧的）。这个策略在什么情况下会出错？（提示：考虑用户在不同场合说了前后矛盾但各自合理的事情。）你会如何改进？

**Q5 — 记忆的边界**  
对话 Agent 把全部记忆都注入到 system prompt 里。当记忆条目很多时，这会带来哪些问题？本实验的代码里有没有对记忆长度的限制？如果没有，你会在哪里加？

**Q6 — 评测设计**  
evaluation 模式用的是"历史对话 → 问题 → 参考答案"三元组。这种评测设计有什么优点？它可能低估或高估记忆系统能力的场景各是什么？

---

## 参考文件

| 文件 | 作用 |
|------|------|
| [conversational_agent.py](conversational_agent.py) | 读记忆、构造上下文、流式响应 |
| [background_memory_processor.py](background_memory_processor.py) | 分析对话历史、调用工具写记忆 |
| [agent.py](agent.py) | UserMemoryAgent：ReAct + 工具（add/update/delete） |
| [memory_manager.py](memory_manager.py) | 四种存储模式实现；consolidate_memories() |
| [quickstart.py](quickstart.py) | 最简完整闭环示例，读代码的起点 |
| [data/memories/default_user_memory.json](data/memories/default_user_memory.json) | advanced_json_cards 格式的示例输出 |
| [memory_trajectory.json](memory_trajectory.json) | 一次真实后台处理的完整 trace（含原始对话 + 工具调用） |
