import os
from litellm import completion


response = completion(
    model="nodecode/nodecode-cyber-v1",
    messages=[{"role": "user", "content": "Explain SQL injection in two sentences"}],
    stream=True,
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
