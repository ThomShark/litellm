import os
from litellm import completion


response = completion(
    model="nodecode/nodecode-cyber-v1",
    messages=[{"role": "user", "content": "22/tcp and 8080/tcp are open. What next?"}],
)

print(response.choices[0].message.content)
