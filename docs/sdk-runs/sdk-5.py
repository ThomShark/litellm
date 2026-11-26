import os
from litellm import completion


prompt = "Name the product CVE-2021-44228 affects and rate its severity from 1 to 10."

response = completion(
    model="nodecode/nodecode-cyber-v1",
    messages=[{"role": "user", "content": prompt}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "cve_rating",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "severity": {"type": "integer"},
                },
                "required": ["product", "severity"],
                "additionalProperties": False,
            },
        },
    },
)

print(response.choices[0].message.content)
