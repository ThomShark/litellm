### Shared setup

Before runs at the merge base `3e086e25c980` and After at the PR tip `428e320fee`, which is the latest product commit. Both legs use the same Redis and Postgres containers (one database each), the same config file and the same client, and every call reaches the live `https://api.nodecode.ai/v1`.

1. Start the dedicated Redis and Postgres

```bash
podman run -d --name litellm-proof-redis-a -p 127.0.0.1:6390:6379 docker.io/library/redis:7-alpine
podman run -d --name litellm-proof-pg-a -p 127.0.0.1:5440:5432 -e POSTGRES_USER=proof -e POSTGRES_PASSWORD=proof docker.io/library/postgres:16
podman exec litellm-proof-pg-a psql -U proof -c 'create database litellm_before' -c 'create database litellm_after'
```

2. Start one proxy per checkout with `start-proxy.sh`, which exports the env vars below and runs the QA command from AGENTS.md. The config is a copy of `litellm/proxy/dev_config.yaml` with its model list swapped for the Nodecode deployments and with `router_settings` and `general_settings.coordination_redis` pointed at the Redis above

```bash
export NODECODE_API_KEY=...   # a real Nodecode key, never printed
./start-proxy.sh <merge-base checkout> 4100 litellm_before
./start-proxy.sh <PR tip checkout> 4101 litellm_after
```

<details>
<summary>start-proxy.sh</summary>

```bash
#!/usr/bin/env bash
# Usage: start-proxy.sh <checkout dir> <port> <database name>
# Starts the QA proxy the way the repo's AGENTS.md says, against the dedicated
# Redis (127.0.0.1:6390) and Postgres (127.0.0.1:5440) containers.
set -euo pipefail
checkout=$1 port=$2 db=$3
here=$(cd "$(dirname "$0")" && pwd)
# NODECODE_API_KEY must already be exported by the caller (never written to disk here)
: "${NODECODE_API_KEY:?export NODECODE_API_KEY first}"
export DATABASE_URL="postgresql://proof:proof@127.0.0.1:5440/${db}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-local-proof-master-key}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export NO_PROXY=localhost,127.0.0.1
export NODECODE_INVALID_API_KEY=not-a-real-nodecode-key
cd "$checkout"
exec .venv/bin/python litellm/proxy/proxy_cli.py --config "$here/config.yaml" --host 127.0.0.1 --port "$port" \
  --num_workers 2 --detailed_debug --reload --use_v2_migration_resolver
```

</details>

<details>
<summary>config.yaml</summary>

```yaml
# Copy of litellm/proxy/dev_config.yaml with its model_list swapped for the Nodecode
# deployments this proof needs, and router_settings plus
# general_settings.coordination_redis pointed at one dedicated Redis.
model_list:
  - model_name: nodecode/nodecode-cyber-v1
    litellm_params:
      model: nodecode/nodecode-cyber-v1
      api_key: os.environ/NODECODE_API_KEY
  # The only way to reach Nodecode before this PR: the generic OpenAI route
  - model_name: nodecode-openai-workaround
    litellm_params:
      model: openai/nodecode-cyber-v1
      api_base: https://api.nodecode.ai/v1
      api_key: os.environ/NODECODE_API_KEY
  # Failure paths
  - model_name: nodecode-invalid-key
    litellm_params:
      model: nodecode/nodecode-cyber-v1
      api_key: os.environ/NODECODE_INVALID_API_KEY
  - model_name: nodecode-missing-model
    litellm_params:
      model: nodecode/nodecode-cyber-v0
      api_key: os.environ/NODECODE_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  coordination_redis:
    host: 127.0.0.1
    port: 6390

router_settings:
  redis_host: 127.0.0.1
  redis_port: 6390

sandbox_tools:
  - sandbox_tool_name: e2b_sandbox
    litellm_params:
      sandbox_provider: e2b
      api_key: os.environ/E2B_API_KEY

litellm_settings:
  drop_params: True
  code_interpreter_interception_params:
    enabled: true
    sandbox_tool_name: e2b_sandbox
  callbacks:
    - code_interpreter_interception
```

</details>

   Env var names are `NODECODE_API_KEY`, `NODECODE_INVALID_API_KEY` (a made-up key for the 401 case), `LITELLM_MASTER_KEY`, `DATABASE_URL`, `LITELLM_LOCAL_MODEL_COST_MAP=True` and `NO_PROXY`. `drop_params: True` stays on as in `dev_config.yaml`

3. Each proxy serves two workers, Before starting with PIDs 1948522 and 1948523 and After with 1947703 and 1947704. The `--reload` watcher also watches the config's folder, so both proxies restarted each time a script there was edited between runs, 7 times each. Every restart came back with exactly two workers, 16 worker starts under 8 parent processes per proxy in the logs

4. The client is `proof_client.py`, run through one script per case family as `./reproduce-<family>.sh http://127.0.0.1:<port> <out dir>` for basic, tools, reasoning, errors and cache. It sends plain HTTP with httpx and the proxy's master key, prints the one-line summaries quoted below, and saves every request with its raw response or SSE event list, keys redacted. The upstream requests quoted below come from each proxy's `--detailed_debug` log

5. Spend rows are read from each leg's database after the runs

### Before (3e086e25c980)

The proxy can't build any `nodecode/` deployment, and says so at startup

```text
LiteLLM Router:ERROR: registry.py:1237 - Error creating deployment: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=nodecode/nodecode-cyber-v1
```

#### Chat Completions, Responses and Messages

1. `./reproduce-basic.sh http://127.0.0.1:4100 before`

2. Every Nodecode call fails the same way on all three endpoints, in JSON and in streaming mode. The generic `openai/` route still answers, but LiteLLM can't price it, so no `x-litellm-response-cost` header comes back

```text
chat json: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
chat stream: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
responses json: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
responses stream: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
messages json: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
messages stream: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
openai/ workaround chat json: HTTP 200, text '323', reasoning 56 chars, usage prompt 44 (cached 0), completion 20 (reasoning 17), cost header absent
saved 7 captures to before/basic.json
```

<details>
<summary>Raw response, chat JSON</summary>

```json
{
  "status": 400,
  "body": {
    "error": {
      "message": "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att... (227 chars)",
      "type": "invalid_request_error",
      "param": null,
      "code": "400"
    }
  }
}
```

</details>

#### Tool roundtrips

1. `./reproduce-tools.sh http://127.0.0.1:4100 before`

2. The first call of each roundtrip fails on all three endpoints, in JSON and streaming, so no tool call ever comes back

```text
chat tool json: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
... the other 5 calls return the same 400 (all in before/tools.out.txt)
```

#### Reasoning controls

1. `./reproduce-reasoning.sh http://127.0.0.1:4100 before`

```text
chat reasoning_effort=none: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
... the other 6 calls return the same 400 (all in before/reasoning.out.txt)
```

#### Failure paths

1. `./reproduce-errors.sh http://127.0.0.1:4100 before`

2. A bad key and a missing model both come back as the same proxy-side 400, so the caller never sees the provider's 401 or 404

```text
nodecode-invalid-key chat: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode-invalid-key. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode-invalid-key' failed with the error above. No fallback was attempted."
... the other 7 calls return the same 400 (all in before/errors.out.txt)
```

#### Three-turn cached conversation

1. `PROOF_CACHE_SIDES=proxy ./reproduce-cache.sh http://127.0.0.1:4100 before`

2. The first turn already fails on every endpoint

```text
cache chat proxy turn 1: HTTP 400 "litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No fallback was att"
... the other 2 calls return the same 400 (all in before/cache.out.txt)
```

#### Persisted spend

1. Every Nodecode row is a failure at 0 spend with no provider, and the `openai/` workaround row is a success at 0 spend under provider `openai`

```text
model_group | provider | status | calls | spend
nodecode-invalid-key | (none) | failure | 8 | 0
nodecode-missing-model | (none) | failure | 8 | 0
nodecode/nodecode-cyber-v1 | (none) | failure | 21 | 0
nodecode-openai-workaround | openai | success | 1 | 0
(4 rows)
```

   This snapshot was taken before the last Before run of the reasoning family, whose 7 calls failed the same way and add 7 more failure rows at 0 spend

### After (428e320fee)

The proxy builds every deployment, and LiteLLM sends each endpoint to the provider's own route, chat to `/v1/chat/completions`, Responses to `/v1/responses` and Messages to `/v1/messages`

<details>
<summary>Upstream requests for the first call on each endpoint (from the debug log)</summary>

```text
POST https://api.nodecode.ai/v1/
{'model': 'nodecode-cyber-v1', 'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_completion_tokens': 512, 'extra_body': {}}

POST https://api.nodecode.ai/v1/responses
{'model': 'nodecode-cyber-v1', 'input': 'What is 19 * 17? Answer with the number only.', 'max_output_tokens': 512}

POST https://api.nodecode.ai/v1/messages
{'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_tokens': 512, 'model': 'nodecode-cyber-v1', 'stream': False}
```

</details>

#### Chat Completions, Responses and Messages

1. `./reproduce-basic.sh http://127.0.0.1:4101 after`

2. All six calls answer 323 and finish normally. The JSON calls carry `x-litellm-response-cost`, and it equals the cost-map math, for example 44 x $0.33/M + 19 x $1.32/M = $0.0000396 for chat. The streams carry no cost header, and their spend rows below are priced the same way. The `openai/` workaround still gets no price, as before

```text
chat json: HTTP 200, text '323', reasoning 51 chars, usage prompt 44 (cached 0), completion 19 (reasoning 16), cost header 3.96e-05
chat stream: HTTP 200 stream, 10 events, text '323', usage prompt 44 (cached 0), completion 21 (reasoning 18), [DONE] True, cost header absent
responses json: HTTP 200 status completed, output ['reasoning', 'message'], text '323', usage input 44 (cached 0), output 26, cost header 4.884e-05
responses stream: HTTP 200 stream, 17 events, status completed, output ['reasoning', 'message'], text '323', usage input 44 (cached 0), output 21, cost header absent
messages json: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 16, cost header 3.5640000000000004e-05
messages stream: HTTP 200 stream, 15 events, 1 message_stop, blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 21, cost header absent
openai/ workaround chat json: HTTP 200, text '323', reasoning 55 chars, usage prompt 44 (cached 0), completion 21 (reasoning 18), cost header absent
saved 7 captures to after/basic.json
```

<details>
<summary>Raw responses, JSON mode</summary>

```json
{
  "case": "chat_json",
  "status": 200,
  "headers": {
    "content-type": "application/json",
    "x-litellm-call-id": "3a62ec36-ae79-47b9-b243-5f4e61f31a42",
    "x-litellm-model-id": "a76abacf4e0730a006c19dde8fb14769c0bb2a73df9b825d3134a51a83c9adc6",
    "x-litellm-response-cost": "3.96e-05"
  },
  "body": {
    "id": "chatcmpl-8e3025deacf82780",
    "created": 1795666363,
    "model": "nodecode/nodecode-cyber-v1",
    "object": "chat.completion",
    "system_fingerprint": "vllm-0.32.1rc1.dev36+ge68efc685-tp4-866259b6",
    "choices": [
      {
        "finish_reason": "stop",
        "index": 0,
        "message": {
          "content": "323",
          "role": "assistant",
          "reasoning_content": "We need answer only number. 19*17=323. Need comply.",
          "provider_specific_fields": {
            "reasoning": "We need answer only number. 19*17=323. Need comply."
          }
        },
        "provider_specific_fields": {
          "token_ids": null,
          "stop_reason": null,
          "weight_versions": null
        },
        "logprobs": null,
        "stop_reason": null,
        "token_ids": null
      }
    ],
    "usage": {
      "completion_tokens": 19,
      "prompt_tokens": 44,
      "total_tokens": 63,
      "completion_tokens_details": {
        "reasoning_tokens": 16
      },
      "prompt_tokens_details": {
        "cached_tokens": 0,
        "created_cache_tokens": 0
      }
    }
  }
}

{
  "case": "responses_json",
  "status": 200,
  "headers": {
    "content-type": "application/json",
    "x-litellm-call-id": "a00a2dde-b87a-4207-ad61-366a9495321f",
    "x-litellm-model-id": "a76abacf4e0730a006c19dde8fb14769c0bb2a73df9b825d3134a51a83c9adc6",
    "x-litellm-response-cost": "4.884e-05"
  },
  "body": {
    "id": "resp_zsLOj8geJQ2FEoxqRKpfeCn4J3XzOGd7cZIK4s5O-8EJH0ovb5ss5O-LaTWdiwUrPFp9dCihCi7lKQZYXFpNIY-THWaEXv3dH5SHVy75TzX0e9NQiKeNTGBt8gikrSAsCS7Lp7RzxAH_K3NKYKGi4XXq7ZyIQXOKaRblLVWHNd3hBgpAgh_mFOXd9xe5qeXRrp3IbsCEz4YUnJHrIqm0El4... (453 chars)",
    "object": "response",
    "status": "completed",
    "model": "nodecode/nodecode-cyber-v1",
    "output": [
      {
        "id": "rs_01a2160e47cb7c5385e113566417ca41_0",
        "summary": [],
        "type": "reasoning",
        "content": [
          {
            "text": "We need answer only number. 19*17=323. Must comply \"number only\". final 323.",
            "type": "reasoning_text"
          }
        ],
        "encrypted_content": "nc1:We need answer only number. 19*17=323. Must comply \"number only\". final 323."
      },
      {
        "id": "msg_01a2160e47cb7c5385e113566417ca41_1",
        "content": [
          {
            "annotations": [],
            "text": "323",
            "type": "output_text",
            "logprobs": []
          }
        ],
        "role": "assistant",
        "status": "completed",
        "type": "message",
        "phase": null
      }
    ],
    "usage": {
      "input_tokens": 44,
      "input_tokens_details": {
        "audio_tokens": null,
        "cached_tokens": 0,
        "cached_tokens_details": null,
        "image_tokens": null,
        "text_tokens": null,
        "video_tokens": null
      },
      "output_tokens": 26,
      "output_tokens_details": {
        "audio_tokens": null,
        "reasoning_tokens": 23,
        "text_tokens": null
      },
      "total_tokens": 70,
      "cost": null
    },
    "error": null
  }
}

{
  "case": "messages_json",
  "status": 200,
  "headers": {
    "content-type": "application/json",
    "x-litellm-call-id": "99114023-9cc8-4417-89ad-9ed9c3ebcf9d",
    "x-litellm-model-id": "a76abacf4e0730a006c19dde8fb14769c0bb2a73df9b825d3134a51a83c9adc6",
    "x-litellm-response-cost": "3.5640000000000004e-05"
  },
  "body": {
    "id": "msg_01a2160e4c53796283d59441802782fd",
    "type": "message",
    "role": "assistant",
    "model": "nodecode/nodecode-cyber-v1",
    "content": [
      {
        "type": "thinking",
        "thinking": "We need answer only number. 19*17=323.",
        "signature": ""
      },
      {
        "type": "text",
        "text": "323"
      }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "usage": {
      "input_tokens": 44,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "output_tokens": 16
    }
  }
}
```

</details>

<details>
<summary>Raw SSE events, streaming mode (first event and last two of each, full lists in after/basic.json)</summary>

```text
# chat_stream
{"event": null, "data": {"id": "chatcmpl-b38ced01f7fe7723", "created": 1795666364, "model": "nodecode/nodecode-cyber-v1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "", "role": "assistant"}}]}}
{"...": "7 more events"}
{"event": null, "data": {"id": "chatcmpl-b38ced01f7fe7723", "created": 1795666364, "model": "nodecode/nodecode-cyber-v1", "object": "chat.completion.chunk", "system_fingerprint": "vllm-0.32.1rc1.dev36+ge68efc685-tp4-866259b6", "choices": [], "usage": {"completion_tokens": 21, "prompt_tokens": 44, "total_tokens": 65, "completion_tokens_details": {"reasoning_tokens": 18}, "prompt_tokens_details": {"cached_tokens": 0, "created_cache_tokens": 0}, "cost": 4.224e-05}}}
{"event": null, "data": "[DONE]"}

# responses_stream
{"event": "response.created", "data": {"type": "response.created", "response": {"id": "resp_p0Rj76EbMrR_JVSd2e6GobYwKmfJvaWUEtK9zvH-XJGteBTKdw9AxPUGDbKauA_pHw20YX0qMUmASKIi4zbfblaVAoN33idBYcy9bl2eMMqBXEt5Q5s... (453 chars)", "object": "response", "status": "in_progress", "model": "nodecode/nodecode-cyber-v1", "output": []}, "sequence_number": 0, "model": "nodecode/nodecode-cyber-v1"}}
{"...": "14 more events"}
{"event": "response.output_item.done", "data": {"type": "response.output_item.done", "output_index": 1, "sequence_number": 15, "item": {"type": "message", "id": "msg_01a2160e4a1b7a90aa63668f7cae4406_1", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "323", "annotations": [], "logprobs": []}]}, "model": "nodecode/nodecode-cyber-v1"}}
{"event": "response.completed", "data": {"type": "response.completed", "response": {"id": "resp_p0Rj76EbMrR_JVSd2e6GobYwKmfJvaWUEtK9zvH-XJGteBTKdw9AxPUGDbKauA_pHw20YX0qMUmASKIi4zbfblaVAoN33idBYcy9bl2eMMqBXEt5Q5s... (453 chars)", "object": "response", "status": "completed", "model": "nodecode/nodecode-cyber-v1", "output": [{"id": "rs_01a2160e4a1b7a90aa63668f7cae4406_0", "summary": [], "type": "reasoning", "content": [{"text": "We need answer only number. 19*17=323. Need final only.", "type": "reasoning_text"}], "encrypted_content": "nc1:We need answer only number. 19*17=323. Need final only."}, {"id": "msg_01a2160e4a1b7a90aa63668f7cae4406_1", "content": [{"annotations": [], "text": "323", "type": "output_text", "logprobs": []}], "role": "assistant", "status": "completed", "type": "message"}], "usage": {"input_tokens": 44, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 21, "output_tokens_details": {"reasoning_tokens": 18}, "total_tokens": 65, "cost": 4.224e-05}}, "sequence_number": 16, "model": "nodecode/nodecode-cyber-v1"}}

# messages_stream
{"event": "message_start", "data": {"type": "message_start", "message": {"id": "msg_01a2160e4d8a7ea392adde68cbff9a7a", "type": "message", "role": "assistant", "model": "nodecode/nodecode-cyber-v1", "content": [], "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 0, "output_tokens": 0}}}}
{"...": "12 more events"}
{"event": "message_delta", "data": {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"input_tokens": 44, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 21}}}
{"event": "message_stop", "data": {"type": "message_stop"}}
```

</details>

#### Tool roundtrips

1. `./reproduce-tools.sh http://127.0.0.1:4101 after`

2. On each endpoint, in JSON and in streaming mode, the model calls `multiply` with a=19 and b=17, the client returns 323 under the call's id, and the model's next answer uses it. The second turn of every roundtrip also reads 256 cached prompt tokens, priced at the cached rate

3. On `/v1/responses` the client first tries `previous_response_id`. api.nodecode.ai keeps no response state and says so with a 400, which LiteLLM passes through, so the client resends the history with the `function_call` and `function_call_output` items, and that works

```text
chat tool json: call multiply({"a": 19, "b": 17}) id chatcmpl-tool-8884c840363f063b -> client returned 323 -> HTTP 200, text 'The product of 19 and 17 is **323**.', reasoning 0 chars, usage prompt 403 (cached 256), completion 14 (reasoning 0), cost header 6.929399999999999e-05
chat tool stream: call multiply({"a": 19, "b": 17}) id chatcmpl-tool-9da8a2948aafd482 -> client returned 323 -> HTTP 200 stream, 6 events, text '19 × 17 = **323**', usage prompt 382 (cached 256), completion 10 (reasoning 0), [DONE] True, cost header absent
responses tool json: previous_response_id continuation -> HTTP 400 "litellm.BadRequestError: NodecodeException - Responses are not stored here: send the whole conversation as `input`, with `store: false`.\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No "
responses tool json: call multiply({"a": 19, "b": 17}) call_id chatcmpl-tool-bc5911155a81e83b -> client returned 323 via resent history -> HTTP 200 status completed, output ['message'], text '19 × 17 = **323**', usage input 382 (cached 256), output 10, cost header 5.7084000000000004e-05
responses tool stream: previous_response_id continuation -> HTTP 400 "litellm.BadRequestError: NodecodeException - Responses are not stored here: send the whole conversation as `input`, with `store: false`.\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No "
responses tool stream: call multiply({"a": 19, "b": 17}) call_id chatcmpl-tool-834741d14e5c1f49 -> client returned 323 via resent history -> HTTP 200 stream, 10 events, status completed, output ['message'], text '19 × 17 = **323**', usage input 382 (cached 256), output 10, cost header absent
messages tool json: tool_use multiply({"a": 19, "b": 17}) id chatcmpl-tool-ba2c93873b36017a -> client returned 323 -> HTTP 200 blocks ['text'], stop end_turn, text '19 × 17 = **323**', usage input 142 (cached 256), output 10, cost header 6.2364e-05
messages tool stream: tool_use multiply({"a": 19, "b": 17}) id chatcmpl-tool-87990fa965d67a82 -> client returned 323 -> HTTP 200 stream, 8 events, 1 message_stop, blocks ['text'], stop end_turn, text '19 × 17 = **323**', usage input 139 (cached 256), output 10, cost header absent
saved 14 captures to after/tools.json
```

<details>
<summary>Raw responses, first turn of each JSON roundtrip, and the previous_response_id refusal</summary>

```json
{
  "case": "chat_tool_json_1",
  "status": 200,
  "body": {
    "id": "chatcmpl-b793394030b2fd99",
    "created": 1795666366,
    "model": "nodecode/nodecode-cyber-v1",
    "object": "chat.completion",
    "system_fingerprint": "vllm-0.32.1rc1.dev36+ge68efc685-tp4-866259b6",
    "choices": [
      {
        "finish_reason": "tool_calls",
        "index": 0,
        "message": {
          "role": "assistant",
          "tool_calls": [
            {
              "function": {
                "arguments": "{\"a\": 19, \"b\": 17}",
                "name": "multiply"
              },
              "id": "chatcmpl-tool-8884c840363f063b",
              "type": "function"
            }
          ],
          "reasoning_content": "The user wants me to multiply 19 by 17 using the multiply tool. Let me do that.",
          "provider_specific_fields": {
            "reasoning": "The user wants me to multiply 19 by 17 using the multiply tool. Let me do that."
          },
          "content": null
        },
        "provider_specific_fields": {
          "token_ids": null,
          "stop_reason": null,
          "weight_versions": null
        },
        "logprobs": null,
        "stop_reason": null,
        "token_ids": null
      }
    ],
    "usage": {
      "completion_tokens": 76,
      "prompt_tokens": 315,
      "total_tokens": 391,
      "completion_tokens_details": {
        "reasoning_tokens": 21
      },
      "prompt_tokens_details": {
        "cached_tokens": 0,
        "created_cache_tokens": 160
      }
    }
  }
}

{
  "case": "responses_tool_json_1",
  "status": 200,
  "body": {
    "id": "resp_hp1VTJRN3W5EdGA-nVhjkGDsBQIj0OfHFBq3OnIGWAODgxblQqP3Z195nQT3lUfU2pgoIGUOEXdjyuSa3AqMyt8_TVOiDzaJ-DD_9PU4BzkGtbJtO1q5nVQL3osqj2CAVIQmuEAiuiL-TOsDMbbPN9-v9s7fFpVbXMPO2fHVcrbrpDa4Sg16sueOwJgQFgEydsjbGewtrpuUvvzmH10nsho... (453 chars)",
    "object": "response",
    "status": "completed",
    "model": "nodecode/nodecode-cyber-v1",
    "output": [
      {
        "id": "rs_01a2160e58137fa39437c70ad65573de_0",
        "summary": [],
        "type": "reasoning",
        "content": [
          {
            "text": "We need answer. Need use multiply tool. Call multiply 19 17 =323. Then tell.",
            "type": "reasoning_text"
          }
        ],
        "encrypted_content": "nc1:We need answer. Need use multiply tool. Call multiply 19 17 =323. Then tell."
      },
      {
        "arguments": "{\"a\": 19, \"b\": 17}",
        "call_id": "chatcmpl-tool-bc5911155a81e83b",
        "name": "multiply",
        "type": "function_call",
        "id": "fc_01a2160e58137fa39437c70ad65573de_1",
        "namespace": null,
        "status": "completed"
      }
    ],
    "usage": {
      "input_tokens": 315,
      "input_tokens_details": {
        "audio_tokens": null,
        "cached_tokens": 256,
        "cached_tokens_details": null,
        "image_tokens": null,
        "text_tokens": null,
        "video_tokens": null
      },
      "output_tokens": 76,
      "output_tokens_details": {
        "audio_tokens": null,
        "reasoning_tokens": 21,
        "text_tokens": null
      },
      "total_tokens": 391,
      "cost": null
    },
    "error": null
  }
}

{
  "case": "responses_tool_json_2_previous_response_id",
  "status": 400,
  "body": {
    "error": {
      "message": "litellm.BadRequestError: NodecodeException - Responses are not stored here: send the whole conversation as `input`, with `store: false`.\n\nLiteLLM: model group 'nodecode/nodecode-cyber-v1' failed with the error above. No ... (243 chars)",
      "type": "invalid_request_error",
      "param": null,
      "code": "400",
      "provider_specific_fields": {
        "code": "previous_response_id_unsupported",
        "request_id": "01a2160e-5a81-70c1-bc2c-4f8170021b59"
      }
    }
  }
}

{
  "case": "messages_tool_json_1",
  "status": 200,
  "body": {
    "id": "msg_01a2160e5f2077b2a7aa98577db59386",
    "type": "message",
    "role": "assistant",
    "model": "nodecode/nodecode-cyber-v1",
    "content": [
      {
        "type": "thinking",
        "thinking": "The user wants me to multiply 19 by 17 using the multiply tool.",
        "signature": ""
      },
      {
        "type": "tool_use",
        "id": "chatcmpl-tool-ba2c93873b36017a",
        "name": "multiply",
        "input": {
          "a": 19,
          "b": 17
        }
      }
    ],
    "stop_reason": "tool_use",
    "stop_sequence": null,
    "usage": {
      "input_tokens": 59,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 256,
      "output_tokens": 71
    }
  }
}
```

</details>

   Every streamed roundtrip's full SSE event list is in `after/tools.json`

#### Reasoning controls

1. `./reproduce-reasoning.sh http://127.0.0.1:4101 after`

2. On chat, `reasoning_effort` reaches the provider as sent. `none` turns thinking off (0 reasoning tokens) and `max` keeps it on

3. On Messages, `thinking: {"type": "disabled"}` reaches the provider and turns thinking off, with or without `reasoning_effort: "high"` next to it. LiteLLM's native Messages route drops `reasoning_effort` and turns `output_config.effort` into a thinking budget before the call. The same three requests sent straight to api.nodecode.ai answer the same way, because that route also thinks unless thinking is disabled, so a Messages caller sees no difference between the proxy and the provider

```text
chat reasoning_effort=none: HTTP 200, text '323', reasoning 0 chars, usage prompt 18 (cached 0), completion 2 (reasoning 0), cost header 8.580000000000001e-06
chat reasoning_effort=max: HTTP 200, text '323', reasoning 63 chars, usage prompt 44 (cached 0), completion 21 (reasoning 18), cost header 4.224e-05
messages default: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 21, cost header 4.224e-05
messages thinking disabled: HTTP 200 blocks ['text'], stop end_turn, text '323', usage input 18 (cached 0), output 2, cost header 8.580000000000001e-06
messages thinking disabled + reasoning_effort high: HTTP 200 blocks ['text'], stop end_turn, text '323', usage input 18 (cached 0), output 2, cost header 8.580000000000001e-06
messages reasoning_effort none: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 16, cost header 3.5640000000000004e-05
messages output_config effort low: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 16, cost header 3.5640000000000004e-05
direct messages thinking disabled + reasoning_effort high: HTTP 200 blocks ['text'], stop end_turn, text '323', usage input 18 (cached 0), output 2, cost header absent
direct messages reasoning_effort none: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 19, cost header absent
direct messages output_config effort low: HTTP 200 blocks ['thinking', 'text'], stop end_turn, text '323', usage input 44 (cached 0), output 21, cost header absent
saved 10 captures to after/reasoning.json
```

<details>
<summary>Upstream requests for the reasoning cases (from the debug log)</summary>

```text
POST https://api.nodecode.ai/v1/
{'model': 'nodecode-cyber-v1', 'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_completion_tokens': 2048, 'reasoning_effort': 'none', 'extra_body': {}}

POST https://api.nodecode.ai/v1/
{'model': 'nodecode-cyber-v1', 'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_completion_tokens': 2048, 'reasoning_effort': 'max', 'extra_body': {}}

POST https://api.nodecode.ai/v1/messages
{'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_tokens': 2048, 'model': 'nodecode-cyber-v1', 'stream': False, 'thinking': {'type': 'disabled'}}

POST https://api.nodecode.ai/v1/messages
{'messages': [{'role': 'user', 'content': 'What is 19 * 17? Answer with the number only.'}], 'max_tokens': 2048, 'model': 'nodecode-cyber-v1', 'stream': False, 'thinking': {'type': 'enabled', 'budget_tokens': 1024}}
```

</details>

<details>
<summary>Raw responses, chat with reasoning_effort none and Messages with thinking disabled plus reasoning_effort high</summary>

```json
{
  "case": "chat_reasoning_none",
  "status": 200,
  "body": {
    "id": "chatcmpl-ad3126e40a7cb841",
    "created": 1795666973,
    "model": "nodecode/nodecode-cyber-v1",
    "object": "chat.completion",
    "system_fingerprint": "vllm-0.32.1rc1.dev36+ge68efc685-tp4-866259b6",
    "choices": [
      {
        "finish_reason": "stop",
        "index": 0,
        "message": {
          "content": "323",
          "role": "assistant"
        },
        "provider_specific_fields": {
          "weight_versions": null,
          "token_ids": null,
          "stop_reason": null
        },
        "logprobs": null,
        "stop_reason": null,
        "token_ids": null
      }
    ],
    "usage": {
      "completion_tokens": 2,
      "prompt_tokens": 18,
      "total_tokens": 20,
      "completion_tokens_details": {
        "reasoning_tokens": 0
      },
      "prompt_tokens_details": {
        "cached_tokens": 0,
        "created_cache_tokens": 0
      }
    }
  }
}

{
  "case": "messages_thinking_disabled_effort_high",
  "status": 200,
  "body": {
    "id": "msg_01a216179a747143a6e1aba8542cebc3",
    "type": "message",
    "role": "assistant",
    "model": "nodecode/nodecode-cyber-v1",
    "content": [
      {
        "type": "text",
        "text": "323"
      }
    ],
    "stop_reason": "end_turn",
    "stop_sequence": null,
    "usage": {
      "input_tokens": 18,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "output_tokens": 2
    }
  }
}
```

</details>

#### Failure paths

1. `./reproduce-errors.sh http://127.0.0.1:4101 after`

2. A bad key returns 401 `AuthenticationError` and a missing model returns 404 `NotFoundError` on every endpoint, with the provider's own message. The router cools a deployment down for 5 seconds after a failed call, so the client waits that out between calls, and otherwise the next call reads the router's 429

```text
nodecode-invalid-key chat: HTTP 401 "litellm.AuthenticationError: AuthenticationError: NodecodeException - Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/keys or run `nodecode login`.\n\nLiteLLM: model group 'nodecode-inval"
nodecode-invalid-key responses: HTTP 401 "litellm.AuthenticationError: AuthenticationError: NodecodeException - Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/keys or run `nodecode login`.\n\nLiteLLM: model group 'nodecode-inval"
nodecode-invalid-key messages: HTTP 401 'litellm.AuthenticationError: AuthenticationError: NodecodeException - {"type":"error","error":{"type":"authentication_error","message":"Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/k'
nodecode-invalid-key chat stream: HTTP 401 "litellm.AuthenticationError: AuthenticationError: NodecodeException - Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/keys or run `nodecode login`.\n\nLiteLLM: model group 'nodecode-inval"
nodecode-missing-model chat: HTTP 404 "litellm.NotFoundError: NodecodeException - The model `nodecode-cyber-v0` does not exist. GET /v1/models lists the ones on your plan.\n\nLiteLLM: model group 'nodecode-missing-model' failed with the error above. No fallback"
nodecode-missing-model responses: HTTP 404 "litellm.NotFoundError: NodecodeException - The model `nodecode-cyber-v0` does not exist. GET /v1/models lists the ones on your plan.\n\nLiteLLM: model group 'nodecode-missing-model' failed with the error above. No fallback"
nodecode-missing-model messages: HTTP 404 'litellm.NotFoundError: NotFoundError: NodecodeException - {"type":"error","error":{"type":"not_found_error","message":"The model `nodecode-cyber-v0` does not exist. GET /v1/models lists the ones on your plan.","code":"mo'
nodecode-missing-model chat stream: HTTP 404 "litellm.NotFoundError: NodecodeException - The model `nodecode-cyber-v0` does not exist. GET /v1/models lists the ones on your plan.\n\nLiteLLM: model group 'nodecode-missing-model' failed with the error above. No fallback"
saved 8 captures to after/errors.json
```

<details>
<summary>Raw responses, failure paths</summary>

```json
{
  "case": "nodecode-invalid-key_chat",
  "status": 401,
  "body": {
    "error": {
      "message": "litellm.AuthenticationError: AuthenticationError: NodecodeException - Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/keys or run `nodecode login`.\n\nLiteLLM: model group 'nodecode-inval... (283 chars)",
      "type": "authentication_error",
      "param": null,
      "code": "401",
      "provider_specific_fields": {
        "code": "invalid_api_key",
        "request_id": "01a2160f-ece8-7713-988f-ffde28b546ba"
      }
    }
  }
}

{
  "case": "nodecode-invalid-key_messages",
  "status": 401,
  "body": {
    "type": "error",
    "error": {
      "type": "authentication_error",
      "message": "litellm.AuthenticationError: AuthenticationError: NodecodeException - {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\",\"message\":\"Invalid API key. Keys start with `sk-nc-`; make one at https://nodecode.ai/account/k... (429 chars)"
    }
  }
}

{
  "case": "nodecode-missing-model_responses",
  "status": 404,
  "body": {
    "error": {
      "message": "litellm.NotFoundError: NodecodeException - The model `nodecode-cyber-v0` does not exist. GET /v1/models lists the ones on your plan.\n\nLiteLLM: model group 'nodecode-missing-model' failed with the error above. No fallback... (235 chars)",
      "type": "invalid_request_error",
      "param": null,
      "code": "404",
      "provider_specific_fields": {
        "code": "model_not_found",
        "request_id": "01a21610-6552-77f0-9d03-b6d03dce2173"
      }
    }
  }
}
```

</details>

#### Three-turn cached conversation

1. `./reproduce-cache.sh http://127.0.0.1:4101 after`

2. Each endpoint runs the same three turns twice, once through the proxy and once straight to api.nodecode.ai as the yardstick, each starting from its own cold 2.9k-token prefix and resending the history every turn. Chat and Responses send a `prompt_cache_key`, which the proxy forwards as sent

3. Proxy and direct runs bill the same way on every turn, cold first, then 2,816 cached tokens, then 2,816 or 2,912. The 96-token difference on turn 3 shows up on both sides, so it comes from the provider's cache, and each side bills the count it was given. The proxy's `x-litellm-response-cost` equals the cost-map math on every turn

```text
cache chat proxy turn 1: answer 'ready', prompt 2925 (cached 0), output 2, cost at the cost-map rates 0.000967890, cost header 0.0009678900000000001
cache chat proxy turn 2: answer '323', prompt 2944 (cached 2816), output 2, cost at the cost-map rates 0.000070224, cost header 7.022400000000001e-05
cache chat proxy turn 3: answer '324', prompt 2963 (cached 2816), output 2, cost at the cost-map rates 0.000076494, cost header 7.6494e-05
cache chat direct turn 1: answer 'ready', prompt 2927 (cached 0), output 2, cost at the cost-map rates 0.000968550, cost header absent
cache chat direct turn 2: answer '323', prompt 2946 (cached 2816), output 2, cost at the cost-map rates 0.000070884, cost header absent
cache chat direct turn 3: answer '324', prompt 2965 (cached 2912), output 2, cost at the cost-map rates 0.000046338, cost header absent
cache responses proxy turn 1: answer 'ready', prompt 2928 (cached 0), output 2, cost at the cost-map rates 0.000968880, cost header 0.0009688800000000001
cache responses proxy turn 2: answer '323', prompt 2947 (cached 2816), output 2, cost at the cost-map rates 0.000071214, cost header 7.121400000000001e-05
cache responses proxy turn 3: answer '324', prompt 2966 (cached 2912), output 2, cost at the cost-map rates 0.000046668, cost header 4.6668e-05
cache responses direct turn 1: answer 'ready', prompt 2924 (cached 0), output 2, cost at the cost-map rates 0.000967560, cost header absent
cache responses direct turn 2: answer '323', prompt 2943 (cached 2816), output 2, cost at the cost-map rates 0.000069894, cost header absent
cache responses direct turn 3: answer '324', prompt 2962 (cached 2816), output 2, cost at the cost-map rates 0.000076164, cost header absent
cache messages proxy turn 1: answer 'ready', prompt 2924 (cached 0), output 2, cost at the cost-map rates 0.000967560, cost header 0.0009675600000000001
cache messages proxy turn 2: answer '323', prompt 2943 (cached 2816), output 2, cost at the cost-map rates 0.000069894, cost header 6.9894e-05
cache messages proxy turn 3: answer '324', prompt 2962 (cached 2816), output 2, cost at the cost-map rates 0.000076164, cost header 7.6164e-05
cache messages direct turn 1: answer 'ready', prompt 2926 (cached 0), output 2, cost at the cost-map rates 0.000968220, cost header absent
cache messages direct turn 2: answer '323', prompt 2945 (cached 2816), output 2, cost at the cost-map rates 0.000070554, cost header absent
cache messages direct turn 3: answer '324', prompt 2964 (cached 2912), output 2, cost at the cost-map rates 0.000046008, cost header absent
saved 18 captures to after/cache.json
```

<details>
<summary>Upstream request for a cached chat turn (from the debug log)</summary>

```text
POST https://api.nodecode.ai/v1/
{'model': 'nodecode-cyber-v1', 'messages': [{'role': 'system', 'content': '<reference notes, about 2.9k tokens>'}, {'role': 'user', 'content': 'Reply with the word ready.'}], 'max_completion_tokens': 256, 'reasoning_effort': 'none', 'prompt_cache_key': 'proof-proxy-2d653a20acfe', 'extra_body': {}}
```

</details>

#### Persisted spend

1. Every successful Nodecode row is priced from the cost map, (prompt - cached) x $0.33/M + cached x $0.009/M + output x $1.32/M, and all 44 rows match that math exactly, streams included

<details>
<summary>LiteLLM_SpendLogs, After database, every successful nodecode/nodecode-cyber-v1 row</summary>

```text
call_type | prompt | cached | output | spend | matches_cost_map
acompletion | 44 | 0 | 19 | 3.96e-05 | t
acompletion | 44 | 0 | 21 | 4.224e-05 | t
aresponses | 44 | 0 | 26 | 4.884e-05 | t
aresponses | 44 | 0 | 21 | 4.224e-05 | t
anthropic_messages | 44 | 0 | 16 | 3.5640000000000004e-05 | t
anthropic_messages | 44 | 0 | 21 | 4.224e-05 | t
acompletion | 315 | 0 | 76 | 0.00020427 | t
acompletion | 403 | 256 | 14 | 6.929399999999999e-05 | t
acompletion | 315 | 256 | 71 | 0.000115494 | t
acompletion | 382 | 256 | 10 | 5.7084000000000004e-05 | t
aresponses | 315 | 256 | 76 | 0.000122094 | t
aresponses | 382 | 256 | 10 | 5.7084000000000004e-05 | t
aresponses | 315 | 256 | 55 | 9.4374e-05 | t
aresponses | 382 | 256 | 10 | 5.7084000000000004e-05 | t
anthropic_messages | 315 | 256 | 71 | 0.000115494 | t
anthropic_messages | 398 | 256 | 10 | 6.2364e-05 | t
anthropic_messages | 315 | 256 | 68 | 0.000111534 | t
anthropic_messages | 395 | 256 | 10 | 6.1374e-05 | t
acompletion | 18 | 0 | 2 | 8.580000000000001e-06 | t
acompletion | 44 | 0 | 20 | 4.092e-05 | t
anthropic_messages | 44 | 0 | 16 | 3.5640000000000004e-05 | t
anthropic_messages | 18 | 0 | 2 | 8.580000000000001e-06 | t
anthropic_messages | 18 | 0 | 2 | 8.580000000000001e-06 | t
acompletion | 2926 | 0 | 2 | 0.0009682200000000001 | t
acompletion | 2945 | 2816 | 2 | 7.0554e-05 | t
acompletion | 2964 | 2944 | 2 | 3.5736e-05 | t
anthropic_messages | 44 | 0 | 19 | 3.96e-05 | t
anthropic_messages | 44 | 0 | 21 | 4.224e-05 | t
acompletion | 2925 | 0 | 2 | 0.0009678900000000001 | t
acompletion | 2944 | 2816 | 2 | 7.022400000000001e-05 | t
acompletion | 2963 | 2816 | 2 | 7.6494e-05 | t
aresponses | 2928 | 0 | 2 | 0.0009688800000000001 | t
aresponses | 2947 | 2816 | 2 | 7.121400000000001e-05 | t
aresponses | 2966 | 2912 | 2 | 4.6668e-05 | t
anthropic_messages | 2924 | 0 | 2 | 0.0009675600000000001 | t
anthropic_messages | 2943 | 2816 | 2 | 6.9894e-05 | t
anthropic_messages | 2962 | 2816 | 2 | 7.6164e-05 | t
acompletion | 18 | 0 | 2 | 8.580000000000001e-06 | t
acompletion | 44 | 0 | 21 | 4.224e-05 | t
anthropic_messages | 44 | 0 | 21 | 4.224e-05 | t
anthropic_messages | 18 | 0 | 2 | 8.580000000000001e-06 | t
anthropic_messages | 18 | 0 | 2 | 8.580000000000001e-06 | t
anthropic_messages | 44 | 0 | 16 | 3.5640000000000004e-05 | t
anthropic_messages | 44 | 0 | 16 | 3.5640000000000004e-05 | t
(44 rows)
```

</details>
