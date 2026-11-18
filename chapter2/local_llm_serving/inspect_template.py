"""Chat template inspector — 渲染并打印 Qwen3 chat template 的原始字符串。

调试方法：直接修改下面的 MESSAGES / TOOLS / MODEL_PATH，然后运行。
"""
from transformers import AutoTokenizer

MODEL_PATH = "/data/ai-model/models/llm/Qwen/Qwen3-0.6B"

# ── 修改这里来测试不同的对话内容 ──────────────────────────────────────────
MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What's the weather in Tokyo?"},
]

# 设为 None 则不注入工具定义
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_temperature",
            "description": "Get weather temperature for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get current time in a timezone",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA timezone string"},
                },
                "required": ["timezone"],
            },
        },
    },
]
# ─────────────────────────────────────────────────────────────────────────────


def render(messages, tools=None):
    tok = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    return tok.apply_chat_template(
        messages,
        tools=tools,
        tokenize=False,
        add_generation_prompt=True,
    )


def count_tokens(text):
    tok = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    return len(tok.encode(text))


if __name__ == "__main__":
    print("=== 不带工具 ===")
    plain = render(MESSAGES)
    print(plain)
    print(f"token 数: {count_tokens(plain)}")

    print("\n=== 带工具定义 ===")
    with_tools = render(MESSAGES, TOOLS)
    print(with_tools)
    print(f"token 数: {count_tokens(with_tools)}")

    print("\n=== 工具调用回合（assistant → tool result → assistant）===")
    multi_turn = MESSAGES + [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {"name": "get_current_temperature", "arguments": '{"location": "Tokyo"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_001",
            "content": '{"temperature": 22, "unit": "celsius"}',
        },
    ]
    after_tool = render(multi_turn, TOOLS)
    print(after_tool)
    print(f"token 数: {count_tokens(after_tool)}")
