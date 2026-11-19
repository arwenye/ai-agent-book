# Chapter 3-1 · user-memory 实验笔记

> 实验路径：`chapter3/user-memory/`

---

## 架构概览

三个角色，各司其职：

```
用户输入
   ↓
ConversationalAgent          → 只读记忆，生成回复
   ↓（每 N 轮触发）
BackgroundMemoryProcessor    → 只写记忆，不参与对话
   ↓
MemoryManager                → 存储抽象层（4 种格式）
```

读写分离的核心原因：写入需要调用 LLM 分析，会阻塞响应；分离后对话延迟与记忆更新完全解耦。

---

## 逐层代码理解

### 第一层：main.py（粘合剂）

```
用户输入 → agent.chat(msg)
              └→ 检查 conversation_count % interval == 0
                     └→ processor.process_recent_conversations()
```

只负责计数、到点触发后台处理，最后看。

### 第二层：conversational_agent.py

只做两件事：
1. 调用 `memory_manager.get_context_string()` 把记忆注入 system prompt
2. 调用 LLM 生成回复，把对话追加到 `ConversationHistory`

绝不调用 `add_memory()` / `update_memory()`，记忆对它是只读的。

**两个对话容器的区别：**
- `self.conversation`：当前会话的对话流，直接用于拼接发给 LLM 的 messages
- `self.conversation_history`：带 session_id 落库的持久化记录，供后台处理器读取

**记忆 prompt 的构建方式（O(N) token 保证）：**

```python
# 只有最后一条用户消息携带记忆，历史轮不带
self.conversation.append({"role": "user", "content": message})          # 存原始消息
api_messages = self.conversation[:-1] + [{"role": "user", "content": full_message}]  # 发带记忆的版本
# full_message = message + "\n\n=== USER MEMORIES ===\n" + memory_context
```

如果把 full_message 存入历史，记忆会随历史被重复发送 N 次 → O(N²) token 增长。

### 第三层：background_memory_processor.py

不做提取逻辑，只负责：触发时机、取对话窗口、调用 agent。

`process_recent_conversations()` 的执行顺序：
1. **加锁** — 防止主动触发和后台线程同时运行
2. **先标记已处理** — 防止中途 return 导致 `should_process()` 死循环
3. **从磁盘重读历史** — agent 和 processor 各自有独立的 `ConversationHistory` 实例，必须 `load_history()` 同步
4. **过滤已处理的 turn_id** — 用 set 去重，避免同一条对话被反复提取
5. **调用 UserMemoryAgent** — ReAct 循环自动执行工具，直接修改 memory 文件
6. **从 tool_calls 收集统计** — 遍历 agent 记录的工具调用历史，生成 add/update/delete 计数

```
磁盘对话文件 → load_history() → recent_turns
    → 过滤 processed_turn_ids → unprocessed_turns
    → 转格式 → conversation_context
    → analyze_conversation() → UserMemoryAgent ReAct 循环
    → 调用工具 → memory 文件（写入）+ tool_calls（记录）
    → 遍历 tool_calls → 返回 {operations, summary}
```

### 第四层：agent.py（真正做决策）

ReAct 模式：推理 + 工具调用循环

```
系统 prompt: "你是记忆写入专家，分析对话，决定 add/update/delete"
工具: add_memory / update_memory / delete_memory / search_memories

循环:
  LLM 推理 → 决定调用哪个工具 → 执行 → 看结果 → 继续推理
  直到 LLM 输出 "FINAL ANSWER"
```

这里的 LLM 要判断"这条信息值得长期保存吗？是更新还是新增？"——最容易出错的地方。

**流式工具调用的分片拼接：**
LLM 流式输出时，工具调用按 chunk 分批到达，每个 chunk 携带 `index`。当 index=1 的 chunk 到达但列表里只有 index=0 时，先补空槽再写入，防止 `IndexError`。

### 第五层：memory_manager.py

四种模式共用同一套接口 `add/update/delete/get_context_string`，内部数据结构不同：

| 模式 | 存的是 | 适合场景 |
|------|--------|----------|
| notes | `List[MemoryNote]` | 轻量问答，单一事实 |
| enhanced_notes | 同上（差异只在提取 prompt） | 需要保留事实背后的上下文 |
| json_cards | `Dict[category][subcategory][key]` | 程序代码按类别检索 |
| advanced_json_cards | `Dict[category][card_key]`（含 backstory/person/relationship） | 多人/多关系，防止张冠李戴 |

`consolidate_memories()`（离线，不调 LLM）：
- Pass 1：内容去重，保留较新的，合并 tags
- Pass 2：同一属性 key 的冲突版本，时间戳胜者覆盖旧版本

---

## 记忆模式对比（实验结果）

| 模式 | 存储格式 | 保存了哪些字段 | 信息是否可追溯来源 | 适合场景 |
|------|----------|--------------|-----------------|----------|
| notes | 扁平列表 | note_id / content / session_id / created_at / updated_at / tags | ✓ session_id | 轻量问答，单一事实 |
| enhanced_notes | 扁平列表（同上） | 同上 | ✓ session_id | 需保留事实背后上下文 |
| json_cards | 层次 JSON（personal/work/两层分类） | value / source / updated_at | ✓ source | 程序按类别检索 |
| advanced_json_cards | 层次 JSON（三层分类） | backstory / person / relationship / _metadata | ✓ _metadata.source | 多人/多关系，金融/医疗 |

---

## 实验观察

### Step 1 — quickstart.py 结果

Session 1（三条输入）各触发了独立的 `add` 操作，提取出 5 条 note：

```
Note 1: "User's name is Alex"              tags: [personal, name]
Note 2: "Alex is a software developer"     tags: [profession, career]
Note 3: "Alex loves Python and ML"         tags: [interests, programming, technical]
Note 4: "Alex recently switched from PyTorch to JAX..."  tags: [technical, tools, frameworks, machine-learning]
Note 5: "Alex is working on a recommendation system"     tags: [projects, machine-learning, work]
```

**观察**：Session 2 中"I switched from PyTorch to JAX"触发的是 **add**（新增），而非 update——系统没有识别出这是对 Note 3 中 PyTorch 信息的更新，而是把它作为新事实添加。这说明 `notes` 模式下冲突检测依赖 LLM 判断，存在漏判。

### Step 2 — interactive 模式

PyTorch → JAX 的框架切换在 interactive 模式下触发了 **替换**（update），与 quickstart 不同。差异来源：interactive 模式每轮都处理，处理时已有的 PyTorch 记录在上下文里可见；quickstart 是批处理，处理时上下文不同。

### Step 4 — 冲突纠正

"我住在北京" → `process` → `memory` → "不对，我搬到上海了" → `process` 后：结果为更新，北京和上海没有同时存在。预期行为符合。

---

## 评测设计（evaluation 模式）

### 流程

```
test_case
  ├── conversation_histories  ← 原始输入（多轮对话）
  ├── user_question           ← 要回答的问题
  └── evaluation_criteria     ← 评分依据

流程：
  历史对话 → BackgroundProcessor → 写入 memory 文件
                                        ↓
                               清空原始对话（模拟新会话）
                                        ↓
                               agent.memory_manager.load_memory()
                                        ↓
                               ConversationalAgent 读记忆文件
                                        ↓
                               回答问题 → LLM Judge 打分
```

**关键**：processor 和 agent 实例不共享内存，`load_memory()` 必须显式调用才能同步文件写入。

### LLM Judge 评分维度（5 维度，4 级量表）

前四个维度各自打 1–4 分：

| 维度 | 含义 | 优秀示例 | 边界说明 |
|------|------|----------|----------|
| **precision**（精确性） | 姓名、数字、日期、归属关系是否完全准确 | 给出正确账号，未与储蓄账户混淆 | 允许改述；错误数字/人物/日期不允许 |
| **recall**（完整性） | 回答所需信息是否全部具备 | 既回答直接事实，也给出相关前提 | 不扣无关历史遗漏；必需的风险提示遗漏要扣分 |
| **reasoning**（推理） | 跨会话关联、时序更新、歧义冲突是否正确处理 | 把女儿与其医生在独立对话中正确关联 | 有歧义时主动澄清是正确做法 |
| **proactivity**（主动性） | 是否在适当时机补充有用的后续帮助或风险提示 | 设置直存时主动带上路由号码 | 简洁直接回答仍可得良好/优秀；无关建议或编造细节不算主动性 |

第五个维度是**否决项**：

| 维度 | 含义 | 触发条件 |
|------|------|----------|
| **hallucination**（幻觉） | 任何实质性事实主张在权威来源中不存在或被推翻 | 一旦 `detected=true`，最终 reward 强制归零 |

分级说明：4=优秀 / 3=良好 / 2=及格 / 1=不及格，最终 reward ≥ 0.6 为 PASSED。

### 测试集结构

三层，难度递增：

| 层 | 特点 | 示例 |
|---|---|---|
| layer1 | 单次对话，直接事实提取 | 银行开户、机票预订 |
| layer2 | 多实体/多账户，需区分归属 | 多辆车、多张信用卡 |
| layer3 | 跨会话综合推理，存在矛盾或隐含关系 | 离婚财产、身份盗窃级联 |

---

## 运行命令

```bash
cd chapter3/user-memory
source ../../.venv/bin/activate

python quickstart.py
python main.py --mode interactive --user lab_user --memory-mode notes 2>&1 | tee session.log
python main.py --mode demo --memory-mode advanced_json_cards
python main.py --mode evaluation --memory-mode advanced_json_cards
python main.py --mode evaluation --memory-mode advanced_json_cards --case layer1_01_bank_account
```

详细实验指南draft链接：
https://my.feishu.cn/docx/XAQMdCInYoPJK3xojPDcSJG4nue