## Coding clients and Admin UI

Before runs at the merge base `3e086e25c980c9305c676162a8617d3d7b0c1bf8` and After at `428e320feef64eef6e1d3b041577a21097431053`. Both proxies use the same config, the same Redis and the same Postgres server (one database per revision), and both start with two workers.

### Shared setup

One Redis and one Postgres instance, with a separate database for each revision

```bash
podman run -d --name litellm-proof-redis-b -p 127.0.0.1:6391:6379 docker.io/library/redis:7-alpine
podman run -d --name litellm-proof-pg-b -p 127.0.0.1:5441:5432 -e POSTGRES_PASSWORD=proofpw docker.io/library/postgres:16-alpine
podman exec litellm-proof-pg-b psql -U postgres -qc 'create database litellm_before' -c 'create database litellm_after'
```

The config is a copy of `litellm/proxy/dev_config.yaml` (identical at both commits) with the model list swapped for the one deployment under test. `router_settings` and `general_settings.coordination_redis` point at the Redis above.

<details>
<summary>proxy-config.yaml</summary>

```yaml
model_list:
  - model_name: nodecode/nodecode-cyber-v1
    litellm_params:
      model: nodecode/nodecode-cyber-v1
      api_key: os.environ/NODECODE_API_KEY

router_settings:
  redis_host: 127.0.0.1
  redis_port: 6391

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  coordination_redis:
    host: 127.0.0.1
    port: 6391

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

Each proxy runs from its own checkout. Before listens on 4200 with `DATABASE_URL` pointing at `litellm_before`, and After listens on 4201 with `litellm_after`. `LITELLM_UI_PATH` points at a scratch folder per revision so the packaged UI inside the checkout is never rewritten.

```bash
export NODECODE_API_KEY=...            # a Nodecode key
export LITELLM_MASTER_KEY=...          # a local proxy key
export DATABASE_URL=postgresql://postgres:proofpw@127.0.0.1:5441/litellm_after
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_UI_PATH=<scratch folder>/ui-after
export NO_PROXY=localhost,127.0.0.1
export PYTHONUNBUFFERED=1
.venv/bin/python litellm/proxy/proxy_cli.py --config proxy-config.yaml --host 127.0.0.1 --port 4201 \
  --num_workers 2 --detailed_debug --reload --use_v2_migration_resolver > proxy-after.log 2>&1
```

Both logs show two `Started server process` lines and no `"workers" flag is ignored` line. At startup the Before proxy also logs `Error creating deployment: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=nodecode/nodecode-cyber-v1`, once per worker.

Claude Code 2.1.323 starts in a folder that holds only `input.json` with `{"a": 19, "b": 17}`, from a fresh `CLAUDE_CONFIG_DIR` and with these variables set. Every model variable points at the same deployment so nothing reaches another model.

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4201   # 4200 for Before
export ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY
export ANTHROPIC_MODEL=nodecode/nodecode-cyber-v1
export ANTHROPIC_DEFAULT_OPUS_MODEL=nodecode/nodecode-cyber-v1
export ANTHROPIC_DEFAULT_SONNET_MODEL=nodecode/nodecode-cyber-v1
export ANTHROPIC_DEFAULT_HAIKU_MODEL=nodecode/nodecode-cyber-v1
export ANTHROPIC_SMALL_FAST_MODEL=nodecode/nodecode-cyber-v1
export CLAUDE_CODE_SUBAGENT_MODEL=nodecode/nodecode-cyber-v1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_AUTOUPDATER=1
claude --model nodecode/nodecode-cyber-v1 --permission-mode default
```

Both legs use `--permission-mode default`. Claude Code's auto mode needs Anthropic's server-side safety check, which does not pass through a gateway, and Claude Code says so and stops the turn. With the default mode the user approves each shell command, which is what a gateway user does today.

Codex 0.162.1 starts with `codex` in a folder with the same `input.json`, from a fresh `CODEX_HOME` holding this `config.toml`, with `LITELLM_API_KEY` set to the proxy key.

```toml
model = "nodecode/nodecode-cyber-v1"
model_provider = "litellm"

[model_providers.litellm]
name = "LiteLLM proxy"
base_url = "http://127.0.0.1:4201/v1"   # 4200 for Before
env_key = "LITELLM_API_KEY"
wire_api = "responses"
```

Both clients get the same prompt, typed into the interactive session.

```
Read input.json, use Python to multiply a and b, write the product to result.txt, then tell me the result.
```

The Admin UI cases log in at `http://127.0.0.1:<port>/ui/` as `admin` with the master key, open Models + Endpoints, pick the Add Model tab and type `Nodecode` into Provider.

### Before (3e086e25c980c9305c676162a8617d3d7b0c1bf8)

#### Claude Code

1. Start Claude Code against port 4200 and send the prompt
2. The first request fails with `API Error: 400 litellm.BadRequestError: You passed in model=nodecode/nodecode-cyber-v1. There are no healthy deployments for this model`, and no `result.txt` is written
3. Every request of the session is logged as a failure with no spend, because the deployment was never created

{{claude-before.png}}

#### Codex

1. Start Codex against port 4200 and send the prompt
2. Codex shows the same 400 from the proxy, `There are no healthy deployments for this model`, and no `result.txt` is written

{{codex-before.png}}

#### Admin UI

1. Type `Nodecode` into Provider on the Add Model tab
2. The dropdown answers `No providers found`

{{ui-before.png}}

### After (428e320feef64eef6e1d3b041577a21097431053)

#### Claude Code

1. Start Claude Code against port 4201 and send the prompt
2. Claude reads `input.json`, then asks to run `python3 -I -c "import json; d=json.load(open('input.json')); print(d['a']*d['b'])"`, which prints 323 once approved
3. It asks to run the same one-liner writing `result.txt`, prints 323 again, and answers `The product is 323 (19 × 17), written to /tmp/nodecode-qa/claude-code/result.txt.`
4. `result.txt` holds `323`

{{claude-after.png}}

The detailed transcript (ctrl+o) shows the tool calls and their results.

{{claude-after-transcript.png}}

The session made four `/v1/messages` calls, all sent to `https://api.nodecode.ai/v1/messages` and persisted with cached tokens and cost.

| startTime (UTC) | prompt | cached | output | spend (USD) |
|---|---|---|---|---|
| 04:15:00 | 12,422 | 12,416 | 50 | 0.000179724 |
| 04:15:01 | 12,499 | 12,416 | 172 | 0.000366174 |
| 04:15:31 | 12,672 | 12,544 | 103 | 0.000291096 |
| 04:15:56 | 12,787 | 12,672 | 29 | 0.000190278 |

The last row is 115 uncached input tokens at $0.33/M, 12,672 cached at $0.009/M and 29 output at $1.32/M, which adds up to $0.000190278. The first rows already read from cache because an earlier session with the same system prompt ran a minute before.

#### Codex

1. Start Codex against port 4201 and send the prompt
2. Codex lists the folder and reads `input.json`, runs `python3 -c` to print 323, then runs it again to write `result.txt`
3. It answers `19 × 17 = 323` and `The product is written to result.txt.`, and `result.txt` holds `323`

{{codex-after.png}}

The session made five `/v1/responses` calls, all sent to `https://api.nodecode.ai/v1/responses`. The second one is Codex naming the thread.

| startTime (UTC) | prompt | cached | output | reasoning | spend (USD) |
|---|---|---|---|---|---|
| 04:18:39 | 7,160 | 0 | 143 | 12 | 0.002551560 |
| 04:18:39 | 4,599 | 0 | 349 | 335 | 0.001978350 |
| 04:18:40 | 7,523 | 7,168 | 164 | 25 | 0.000398142 |
| 04:18:41 | 7,747 | 7,680 | 162 | 17 | 0.000305070 |
| 04:18:42 | 7,957 | 7,808 | 20 | 0 | 0.000145842 |

Both legs show Codex's own startup warning `Model metadata for nodecode/nodecode-cyber-v1 not found`, which comes from Codex's built-in model list and not from LiteLLM. Claude Code likewise notes that it does not recognize the model name and assumes a 1M token context, which matches the model.

#### Admin UI

1. Type `Nodecode` into Provider on the Add Model tab
2. The dropdown lists Nodecode

{{ui-after.png}}

3. Pick Nodecode and open LiteLLM Model Name(s). It offers `All NODECODE Models (Wildcard)` and `nodecode/nodecode-cyber-v1`

{{ui-after-models.png}}

4. Pick `nodecode/nodecode-cyber-v1`. The mapping row fills in, API Base shows `https://api.nodecode.ai/v1` as its default, and the form asks for an API Key

{{ui-after-selected.png}}

5. Paste a Nodecode key into API Key, leave API Base empty and press Test Connect. The dialog reads `Connection to nodecode/nodecode-cyber-v1 successful!`, and the call is persisted against `https://api.nodecode.ai/v1/` with 38 input and 16 output tokens for $0.00003366

{{ui-after-test-connect.png}}

The whole After database holds $0.011184 of spend, which includes the Test Connect call and two calls from a first Claude Code attempt that stopped at the auto mode notice.
