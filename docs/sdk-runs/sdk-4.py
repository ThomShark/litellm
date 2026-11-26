import os
from litellm import completion


response = completion(
    model="nodecode/nodecode-cyber-v1",
    messages=[{"role": "user", "content": "How many prime numbers are there below 100?"}],
    reasoning_effort="high",
)

print(response.choices[0].message.reasoning_content)
print(response.choices[0].message.content)
