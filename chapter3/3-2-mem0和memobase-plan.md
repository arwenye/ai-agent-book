# 第 3 章实验学习指南：mem0 与 memobase

> 配套实验：`chapter3/mem0/` · `chapter3/memobase/`  
> 对应书中：实验 3-2「记忆策略横向对比」

---

## 一、实验出发点

Agent 需要记住用户。但"记忆"本身并不单一——

- "用户喜欢简短回答"是**稳定偏好**，会持续生效。
- "用户昨天说要改会议"是**时序事件**，有时间戳，不能概括为偏好。
- "用户正在讨论预算"是**当前上下文**，会话结束即过期。

把这三类信息用同一种结构存储，会出问题：  
- 用临时事件覆写长期偏好 → 用户以为自己被记住，实际没有  
- 把长期偏好当事件堆叠 → 检索时返回一堆旧条目，干扰回答  

**核心问题**：如何设计记忆的表示与管理，让 Agent 在正确的时机、用正确的信息回答？

两个实验分别从不同角度回答这个问题：
- **mem0**：用成熟框架（Mem0 v3）处理提取、存储、检索，观察框架决策
- **memobase**：手写四类记忆存储，理解每个环节的机制；另有真实 Memobase SDK 对比

---

## 二、知识点地图

```
记忆系统
├── 记忆类型
│   ├── 情景记忆 (episodic)   — 具体事件，带时间
│   ├── 语义记忆 (semantic)   — 稳定事实 / 偏好
│   ├── 程序记忆 (procedural) — 操作模式 / 习惯
│   └── 工作记忆 (working)    — 当前会话短时上下文
│
├── 记忆操作
│   ├── 提取 (extraction)     — 从对话中识别值得存的信息
│   ├── 写入 (add/store)      — 决定放到哪种类型
│   ├── 检索 (retrieval)      — 相关度 + 近因两轴
│   └── 更新/衰减 (decay)     — 旧信息降权，冲突信息如何处理
│
└── 框架比较
    ├── Mem0 v3     — ADD-only 追加，时间感知混合检索
    └── Memobase    — Profile（稳定属性树）+ Event（时间线）
```

---

## 三、实验 A：memobase 手写 Agent

### 3A-1 实验目的

通过**从零手写**四类记忆存储，理解：
1. 为什么不能用单一列表存所有记忆
2. 压缩（compression）和巩固（consolidation）解决什么问题
3. 检索时相关度（relevance）和近因（recency）如何权衡

### 3A-2 实验步骤

**Step 1：环境验证**
```bash
cd /data/ywh/agent-book/ai-agent-book
source .venv/bin/activate
cd chapter3/memobase
python main.py --mode demo
```
观察输出的记忆类型分布和操作日志。

**Step 2：交互模式——追踪一条信息的生命周期**
```bash
python main.py --mode interactive
```
按以下顺序输入，每步都执行 `/memory` 查看状态：

| 步骤 | 输入 | 目的 |
|------|------|------|
| 1 | `我回答问题时希望带单位，比如"5km"而不是"5"` | 写入语义记忆 |
| 2 | `/memory` | 确认已保存，记录 memory id |
| 3 | `从北京到上海大概多远？` | 触发检索，观察是否带了单位 |
| 4 | `你喜欢什么颜色？` | 与偏好无关的问题，观察是否错误召回 |
| 5 | `昨天我和同事开会讨论了项目进度` | 写入情景记忆 |
| 6 | `/memory` | 对比语义和情景记忆的结构差异 |
| 7 | 重复输入10条不相关对话 | 触发压缩阈值 |
| 8 | `/memory` | 观察压缩后的变化 |

**Step 3：Benchmark——量化对比有无记忆**
```bash
# 有记忆
python main.py --mode benchmark --num-tasks 5 --category multi_turn_reasoning

# 无记忆（对照组）
python main.py --mode benchmark --num-tasks 5 --category multi_turn_reasoning --no-memory
```

### 3A-3 分析目标

1. **保存检查**：写入的信息实际存成了什么结构？（查看 `memory_manager.py` 的 `add` 返回值）
2. **检索检查**：触发检索时，哪些 memory 被召回？评分是多少？
3. **压缩检查**：超过阈值后，哪些记忆被合并/删除？重要性分数如何影响决策？
4. **错误召回**：与偏好无关的问题，系统是否能正确"不召回"？

### 3A-4 结果中重点观察

- `MemoryStore` 四个类型各存了多少条
- 情景记忆和语义记忆的 `importance_score` 随时间的变化
- `decay()` 调用前后的分数差
- Benchmark 有记忆 vs 无记忆的准确率差

---

## 四、实验 B：memobase 真实 SDK（Profile + Event）

### 4B-1 实验目的

观察真实框架如何区分**画像**（Profile，稳定属性树）和**事件**（Event，时间线），理解框架层的设计取舍。

### 4B-2 前置条件

需要运行中的 Memobase 服务：
```bash
# 本地 Docker 方式（需安装 Docker）
git clone https://github.com/memodb-io/memobase
cd memobase
docker compose up -d
# 默认 http://localhost:8019，token: secret
```

或使用 https://www.memobase.ai 的云端账号，将 `project_url` 和 `api_key` 填入 `.env`。

### 4B-3 实验步骤

```bash
cd /data/ywh/agent-book/ai-agent-book/chapter3/memobase

# 先 dry-run，不调服务，只看流程
python profile_demo.py --dry-run

# 查看 Profile（稳定属性树）
python profile_demo.py --op profile

# 查看 Event Memory（时间线）
python profile_demo.py --op event

# 完整 context（Profile + Event 合并）
python profile_demo.py --op context
```

### 4B-4 分析目标

对比手写实现与 Memobase SDK 的输出：

| 维度 | 手写实现 | Memobase SDK |
|------|----------|--------------|
| 结构 | 四类 + pickle | Profile 树 + Event 列表 |
| 更新 | 重写 / 衰减 | 追加（服务端提取） |
| 时间感知 | `created_at` + decay | Event 自带时间线 |
| 临时事件误归偏好 | 看 importance 是否提升 | 看 Event 是否变成 Profile |

---

## 五、实验 C：mem0 框架

### 5C-1 实验目的

理解 Mem0 v3 的 **ADD-only** 设计：所有记忆追加、不删除、不改写；通过时间感知混合检索（semantic + BM25 + time）优先返回当前事实。观察这种设计在"用户搬家"场景下的表现。

### 5C-2 实验步骤

**Step 1：快速验证**
```bash
cd /data/ywh/agent-book/ai-agent-book/chapter3/mem0
python quickstart.py
```

**Step 2：手动操作记忆，追踪"搬家"场景**
```bash
# 添加第一条事实
python main.py --mode memory --op add \
  --text "我住在北京，是一名后端工程师" \
  --user-id user_test

# 查询
python main.py --mode memory --op search \
  --query "这个用户住在哪里？" \
  --user-id user_test

# 记录返回的 memory-id，然后添加新事实（不删旧的）
python main.py --mode memory --op add \
  --text "我已经搬到上海了" \
  --user-id user_test

# 再次查询，观察两条记录都存在，但哪条排名更高
python main.py --mode memory --op search \
  --query "这个用户住在哪里？" \
  --user-id user_test

# 查看所有记忆
python main.py --mode memory --op get-all \
  --user-id user_test
```

**Step 3：交互模式**
```bash
python main.py --mode interactive
# 交互命令: help, memories, metrics, save, load, new, exit
```

**Step 4：Demo 模式（书中搬家示例）**
```bash
python main.py --mode demo --user-id demo_user
```

### 5C-3 分析目标

1. **ADD-only 的副作用**：两条矛盾的事实同时存在，检索如何排序？时间权重是否足够？
2. **embedding 依赖**：本地后端使用 OpenAI embedding（`text-embedding-3-small`），这是 Mem0 对 OpenAI 的强依赖。若无 `OPENAI_API_KEY`，存取失败。
3. **用户隔离**：换一个 `user-id` 后，能否检索到 `user_test` 的记忆？

### 5C-4 结果中重点观察

- `op search` 返回的 `score` 字段（相关度 × 时间权重）
- `op get-all` 中两条矛盾事实是否并存
- 交互模式下 `memories` 命令显示的结构与手写实现的对比

---

## 六、三条线横向对比

| 维度 | 手写 Agent | Memobase SDK | Mem0 |
|------|-----------|--------------|------|
| **部署依赖** | 无（只需 Kimi Key） | Memobase 服务 | OpenAI embedding |
| **记忆结构** | 4 类 pickle | Profile 树 + Event | 追加式向量库 |
| **更新策略** | decay + 压缩 | 服务端 LLM 提取 | ADD-only |
| **冲突处理** | importance 衰减 | Event vs Profile 分离 | 并存 + 时间排序 |
| **可观测性** | 完整（本地） | 有限（服务端黑箱） | 有限（框架内部） |
| **适合学习** | ✅ 机制最透明 | ✅ 对比框架设计 | ✅ 生产范式 |

---

## 七、掌握检验题

做完实验后，用以下问题检验自己的理解：

**机制层**
1. 情景记忆和语义记忆的 `decay_rate` 应该一样吗？为什么？
2. 手写实现中，压缩时为什么优先删低重要性的记忆而不是最旧的？
3. Mem0 的 ADD-only 在"用户更正错误信息"时会出什么问题？

**设计层**
4. 如果用户说"我上次告诉你我住北京，那是错的，我一直住上海"，三种实现各如何处理？
5. Memobase 把"用户连续三次选同款酒店"从 Event 升级为 Profile 偏好，这个决策应该在哪里做、由谁做？

**工程层**
6. 手写实现用 pickle 持久化，Mem0 用 Chroma 向量库，各自的查询复杂度是多少？数据量增大后谁先出问题？
7. Mem0 本地后端强依赖 OpenAI embedding，如果想替换成本地模型（比如 `bge-m3`），需要改哪里？

**横向判断**
8. 给一个需要上线的用户画像服务，你会选三种方案中的哪一个？说出至少两个选择理由和一个风险。

---

## 八、常见问题速查

| 问题 | 原因 | 解决 |
|------|------|------|
| `KIMI_API_KEY` 报错 | `.env` 未加载或 key 有误 | 确认 `.env` 在当前目录，`python-dotenv` 已装 |
| mem0 存取报 OpenAI 错误 | 本地后端 embedding 依赖 `OPENAI_API_KEY` | 加 `OPENAI_API_KEY` 或换 Mem0 Cloud |
| memobase `profile_demo.py` 连接失败 | 没有运行 Memobase 服务 | 先 `--dry-run`，或部署 Docker 服务 |
| 记忆溢出 / 响应慢 | `MAX_MEMORY_ENTRIES` 太大 | 调低阈值，或手动 `/reset` |
| Kimi K3 temperature 报错 | K3 是推理模型，只接受 `temperature=1` | `agent.py` 已有 `_reasoning_safe_temperature` 兜底 |
