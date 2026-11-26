from litellm import completion
prefix = 'You are a careful security assistant. ' + ' '.join(f'Rule {i}: answer briefly and cite the port number when one is given.' for i in range(120))
for turn in range(2):
    r = completion(model='nodecode/nodecode-cyber-v1', messages=[{'role': 'system', 'content': prefix}, {'role': 'user', 'content': 'Port 22 is open. One line: what service usually runs there?'}], reasoning_effort='none')
    u = r.usage
    cached = (u.prompt_tokens_details.cached_tokens if u.prompt_tokens_details else 0) or 0
    expect = (u.prompt_tokens - cached) * 0.33e-6 + cached * 0.009e-6 + u.completion_tokens * 1.32e-6
    print(turn + 1, 'prompt', u.prompt_tokens, 'cached', cached, 'output', u.completion_tokens, 'cost', r._hidden_params['response_cost'], 'cost-map math', round(expect, 12), repr(r.choices[0].message.content[:60]))
