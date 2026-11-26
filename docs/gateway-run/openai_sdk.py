from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4310",
    api_key="sk-local-nodecode",
)

response = client.chat.completions.create(
    model="cyber-v1",
    messages=[{"role": "user", "content": "hello from litellm"}],
)

print(response.choices[0].message.content)
