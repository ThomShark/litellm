import os
from litellm import completion


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_open_ports",
            "description": "List the open TCP ports of a host in scope",
            "parameters": {
                "type": "object",
                "properties": {"host": {"type": "string", "description": "Host or IP"}},
                "required": ["host"],
            },
        },
    }
]

response = completion(
    model="nodecode/nodecode-cyber-v1",
    messages=[{"role": "user", "content": "Which ports are open on scanme.nmap.org?"}],
    tools=tools,
    tool_choice="auto",
)

print(response.choices[0].message.tool_calls)
