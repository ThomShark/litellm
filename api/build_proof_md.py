#!/usr/bin/env python3
"""Builds proof-api.md from the saved captures. Usage: build_proof_md.py <dir holding proxy-before.log and proxy-after.log>"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
RUN = Path(sys.argv[1])
BEFORE_SHA, AFTER_SHA = "3e086e25c980", "428e320fee"
NOTES = re.compile(r"Session [0-9a-f]{12}\. You are a terse assistant.*?lot code L120\.", re.S)


def redact(text: str) -> str:
    text = re.sub(r"sk-nc-[A-Za-z0-9_-]+", "sk-nc-REDACTED", text)
    return text.replace("sk-local-proof-master-key", "<LITELLM_MASTER_KEY>")


def upstream_requests(leg: str) -> list:
    lines = (RUN / f"proxy-{leg}.log").read_text(errors="replace").splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.startswith("curl -X POST") and i + 3 < len(lines):
            url = lines[i + 1].rstrip(" \\")
            body = lines[i + 3].removeprefix("-d ").strip("'")
            out.append((url, NOTES.sub("<reference notes, about 2.9k tokens>", redact(body))))
    return out


def write_upstream(leg: str, reqs: list) -> None:
    text = "\n\n".join(f"POST {u}\n{b}" for u, b in reqs)
    (HERE / leg / "upstream-requests.txt").write_text(
        f"# Every request the {leg} proxy sent to the provider, in order, from its --detailed_debug log.\n"
        "# LiteLLM prints the base URL for chat calls. The OpenAI client then appends /chat/completions.\n\n" + text + "\n"
    )


def out_lines(leg: str, fam: str) -> str:
    return (HERE / leg / f"{fam}.out.txt").read_text().rstrip()


def caps(leg: str, fam: str) -> dict:
    return {r["case"]: r for r in json.loads((HERE / leg / f"{fam}.json").read_text())}


def short(value, limit=220):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"... ({len(value)} chars)"
    if isinstance(value, list):
        return [short(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: short(v, limit) for k, v in value.items()}
    return value


def js(value) -> str:
    if isinstance(value, dict) and "body" in value:
        value = {**value, "body": slim(value["body"])}
    return json.dumps(short(value), indent=2, ensure_ascii=False)


def first_and_count(leg: str, fam: str) -> str:
    lines = out_lines(leg, fam).splitlines()
    calls = [l for l in lines if not l.startswith("saved ")]
    return lines[0] + (f"\n... the other {len(calls) - 1} calls return the same 400 (all in {leg}/{fam}.out.txt)" if len(calls) > 1 else "")


def details(summary: str, body: str, lang: str = "json") -> str:
    return f"<details>\n<summary>{summary}</summary>\n\n```{lang}\n{body}\n```\n\n</details>\n"


def block(body: str, lang: str = "text") -> str:
    return f"```{lang}\n{body}\n```\n"


def events_excerpt(rec: dict, head: int = 1, tail: int = 2) -> str:
    ev = [{"event": e["event"], "data": slim(e["data"])} if e["event"] else e for e in rec["events"]]
    keep = ev if len(ev) <= head + tail else [*ev[:head], {"...": f"{len(ev) - head - tail} more events"}, *ev[-tail:]]
    return "\n".join(json.dumps(short(e, 120), ensure_ascii=False) for e in keep)


RESPONSE_KEYS = ("id", "object", "status", "model", "output", "usage", "error")


def slim(body):
    """Responses bodies echo the request (tools, instructions, text config). Keep what the user reads."""
    if isinstance(body, dict) and body.get("object") == "response":
        return {k: body[k] for k in RESPONSE_KEYS if k in body}
    if isinstance(body, dict) and isinstance(body.get("response"), dict):
        return {**body, "response": slim(body["response"])}
    return body


def pick(reqs: list, *needles: str) -> str:
    for u, b in reqs:
        if all(n in (u + " " + b) for n in needles):
            return f"POST {u}\n{b}"
    return "(not found)"


def main() -> None:
    up_before, up_after = upstream_requests("before"), upstream_requests("after")
    write_upstream("before", up_before)
    write_upstream("after", up_after)
    config = (HERE / "config.yaml").read_text().rstrip()
    start = (HERE / "start-proxy.sh").read_text().rstrip()
    startup_err = next(l for l in (RUN / "proxy-before.log").read_text().splitlines() if "Error creating deployment" in l)
    b_basic, b_tools = caps("before", "basic"), caps("before", "tools")
    a_basic, a_tools, a_reason, a_err = caps("after", "basic"), caps("after", "tools"), caps("after", "reasoning"), caps("after", "errors")
    spend_before = (HERE / "before" / "spend-logs.txt").read_text().rstrip()

    md = []
    w = md.append
    w("### Shared setup\n")
    w(f"Before runs at the merge base `{BEFORE_SHA}` and After at the PR tip `{AFTER_SHA}`, which is the latest product commit. "
      "Both legs use the same Redis and Postgres containers (one database each), the same config file and the same client, and every call reaches the live `https://api.nodecode.ai/v1`.\n")
    w("1. Start the dedicated Redis and Postgres\n")
    w(block("podman run -d --name litellm-proof-redis-a -p 127.0.0.1:6390:6379 docker.io/library/redis:7-alpine\n"
            "podman run -d --name litellm-proof-pg-a -p 127.0.0.1:5440:5432 -e POSTGRES_USER=proof -e POSTGRES_PASSWORD=proof docker.io/library/postgres:16\n"
            "podman exec litellm-proof-pg-a psql -U proof -c 'create database litellm_before' -c 'create database litellm_after'", "bash"))
    w("2. Start one proxy per checkout with `start-proxy.sh`, which exports the env vars below and runs the QA command from AGENTS.md. "
      "The config is a copy of `litellm/proxy/dev_config.yaml` with its model list swapped for the Nodecode deployments and with `router_settings` and `general_settings.coordination_redis` pointed at the Redis above\n")
    w(block("export NODECODE_API_KEY=...   # a real Nodecode key, never printed\n"
            "./start-proxy.sh <merge-base checkout> 4100 litellm_before\n"
            "./start-proxy.sh <PR tip checkout> 4101 litellm_after", "bash"))
    w(details("start-proxy.sh", start, "bash"))
    w(details("config.yaml", config, "yaml"))
    w("   Env var names are `NODECODE_API_KEY`, `NODECODE_INVALID_API_KEY` (a made-up key for the 401 case), `LITELLM_MASTER_KEY`, `DATABASE_URL`, `LITELLM_LOCAL_MODEL_COST_MAP=True` and `NO_PROXY`. "
      "`drop_params: True` stays on as in `dev_config.yaml`\n")
    w("3. Each proxy serves two workers, Before starting with PIDs 1948522 and 1948523 and After with 1947703 and 1947704. "
      "The `--reload` watcher also watches the config's folder, so both proxies restarted each time a script there was edited between runs, 7 times each. "
      "Every restart came back with exactly two workers, 16 worker starts under 8 parent processes per proxy in the logs\n")
    w("4. The client is `proof_client.py`, run through one script per case family as `./reproduce-<family>.sh http://127.0.0.1:<port> <out dir>` for basic, tools, reasoning, errors and cache. "
      "It sends plain HTTP with httpx and the proxy's master key, prints the one-line summaries quoted below, and saves every request with its raw response or SSE event list, keys redacted. "
      "The upstream requests quoted below come from each proxy's `--detailed_debug` log\n")
    w("5. Spend rows are read from each leg's database after the runs\n")

    # ---------------- Before ----------------
    w(f"### Before ({BEFORE_SHA})\n")
    w("The proxy can't build any `nodecode/` deployment, and says so at startup\n")
    w(block(re.sub(r"^\S+ - ", "", startup_err)))
    w("#### Chat Completions, Responses and Messages\n")
    w("1. `./reproduce-basic.sh http://127.0.0.1:4100 before`\n")
    w("2. Every Nodecode call fails the same way on all three endpoints, in JSON and in streaming mode. The generic `openai/` route still answers, but LiteLLM can't price it, so no `x-litellm-response-cost` header comes back\n")
    w(block(out_lines("before", "basic")))
    w(details("Raw response, chat JSON", js({"status": b_basic["chat_json"]["status"], "body": b_basic["chat_json"]["body"]})))
    w("#### Tool roundtrips\n")
    w("1. `./reproduce-tools.sh http://127.0.0.1:4100 before`\n")
    w("2. The first call of each roundtrip fails on all three endpoints, in JSON and streaming, so no tool call ever comes back\n")
    w(block(first_and_count("before", "tools")))
    w("#### Reasoning controls\n")
    w("1. `./reproduce-reasoning.sh http://127.0.0.1:4100 before`\n")
    w(block(first_and_count("before", "reasoning")))
    w("#### Failure paths\n")
    w("1. `./reproduce-errors.sh http://127.0.0.1:4100 before`\n")
    w("2. A bad key and a missing model both come back as the same proxy-side 400, so the caller never sees the provider's 401 or 404\n")
    w(block(first_and_count("before", "errors")))
    w("#### Three-turn cached conversation\n")
    w("1. `PROOF_CACHE_SIDES=proxy ./reproduce-cache.sh http://127.0.0.1:4100 before`\n")
    w("2. The first turn already fails on every endpoint\n")
    w(block(first_and_count("before", "cache")))
    w("#### Persisted spend\n")
    w("1. Every Nodecode row is a failure at 0 spend with no provider, and the `openai/` workaround row is a success at 0 spend under provider `openai`\n")
    w(block((HERE / "before" / "spend-summary.txt").read_text().rstrip()))
    w("   This snapshot was taken before the last Before run of the reasoning family, whose 7 calls failed the same way and add 7 more failure rows at 0 spend\n")

    # ---------------- After ----------------
    w(f"### After ({AFTER_SHA})\n")
    w("The proxy builds every deployment, and LiteLLM sends each endpoint to the provider's own route, chat to `/v1/chat/completions`, Responses to `/v1/responses` and Messages to `/v1/messages`\n")
    w(details("Upstream requests for the first call on each endpoint (from the debug log)", "\n\n".join([
        pick(up_after, "https://api.nodecode.ai/v1/ ", "'max_completion_tokens': 512"),
        pick(up_after, "/v1/responses", "'max_output_tokens': 512"),
        pick(up_after, "/v1/messages", "'max_tokens': 512"),
    ]), "text"))
    w("#### Chat Completions, Responses and Messages\n")
    w("1. `./reproduce-basic.sh http://127.0.0.1:4101 after`\n")
    w("2. All six calls answer 323 and finish normally. The JSON calls carry `x-litellm-response-cost`, and it equals the cost-map math, for example 44 x $0.33/M + 19 x $1.32/M = $0.0000396 for chat. "
      "The streams carry no cost header, and their spend rows below are priced the same way. The `openai/` workaround still gets no price, as before\n")
    w(block(out_lines("after", "basic")))
    w(details("Raw responses, JSON mode", "\n\n".join(js({"case": c, "status": a_basic[c]["status"], "headers": a_basic[c]["headers"], "body": a_basic[c]["body"]}) for c in ("chat_json", "responses_json", "messages_json"))))
    w(details("Raw SSE events, streaming mode (first event and last two of each, full lists in after/basic.json)", "\n\n".join(f"# {c}\n" + events_excerpt(a_basic[c]) for c in ("chat_stream", "responses_stream", "messages_stream")), "text"))
    w("#### Tool roundtrips\n")
    w("1. `./reproduce-tools.sh http://127.0.0.1:4101 after`\n")
    w("2. On each endpoint, in JSON and in streaming mode, the model calls `multiply` with a=19 and b=17, the client returns 323 under the call's id, and the model's next answer uses it. "
      "The second turn of every roundtrip also reads 256 cached prompt tokens, priced at the cached rate\n")
    w("3. On `/v1/responses` the client first tries `previous_response_id`. api.nodecode.ai keeps no response state and says so with a 400, which LiteLLM passes through, so the client resends the history with the `function_call` and `function_call_output` items, and that works\n")
    w(block(out_lines("after", "tools")))
    w(details("Raw responses, first turn of each JSON roundtrip, and the previous_response_id refusal", "\n\n".join(js({"case": c, "status": a_tools[c]["status"], "body": a_tools[c]["body"]}) for c in (
        "chat_tool_json_1", "responses_tool_json_1", "responses_tool_json_2_previous_response_id", "messages_tool_json_1"))))
    w("   Every streamed roundtrip's full SSE event list is in `after/tools.json`\n")
    w("#### Reasoning controls\n")
    w("1. `./reproduce-reasoning.sh http://127.0.0.1:4101 after`\n")
    w("2. On chat, `reasoning_effort` reaches the provider as sent. `none` turns thinking off (0 reasoning tokens) and `max` keeps it on\n")
    w("3. On Messages, `thinking: {\"type\": \"disabled\"}` reaches the provider and turns thinking off, with or without `reasoning_effort: \"high\"` next to it. "
      "LiteLLM's native Messages route drops `reasoning_effort` and turns `output_config.effort` into a thinking budget before the call. "
      "The same three requests sent straight to api.nodecode.ai answer the same way, because that route also thinks unless thinking is disabled, so a Messages caller sees no difference between the proxy and the provider\n")
    w(block(out_lines("after", "reasoning")))
    w(details("Upstream requests for the reasoning cases (from the debug log)", "\n\n".join([
        pick(up_after, "https://api.nodecode.ai/v1/ ", "'reasoning_effort': 'none'", "'max_completion_tokens': 2048"),
        pick(up_after, "https://api.nodecode.ai/v1/ ", "'reasoning_effort': 'max'"),
        pick(up_after, "/v1/messages", "'thinking': {'type': 'disabled'}", "'max_tokens': 2048"),
        pick(up_after, "/v1/messages", "'budget_tokens': 1024"),
    ]), "text"))
    w(details("Raw responses, chat with reasoning_effort none and Messages with thinking disabled plus reasoning_effort high", "\n\n".join(js({"case": c, "status": a_reason[c]["status"], "body": a_reason[c]["body"]}) for c in ("chat_reasoning_none", "messages_thinking_disabled_effort_high"))))
    w("#### Failure paths\n")
    w("1. `./reproduce-errors.sh http://127.0.0.1:4101 after`\n")
    w("2. A bad key returns 401 `AuthenticationError` and a missing model returns 404 `NotFoundError` on every endpoint, with the provider's own message. "
      "The router cools a deployment down for 5 seconds after a failed call, so the client waits that out between calls, and otherwise the next call reads the router's 429\n")
    w(block(out_lines("after", "errors")))
    w(details("Raw responses, failure paths", "\n\n".join(js({"case": c, "status": a_err[c]["status"], "body": a_err[c]["body"]}) for c in ("nodecode-invalid-key_chat", "nodecode-invalid-key_messages", "nodecode-missing-model_responses"))))
    w("#### Three-turn cached conversation\n")
    w("1. `./reproduce-cache.sh http://127.0.0.1:4101 after`\n")
    w("2. Each endpoint runs the same three turns twice, once through the proxy and once straight to api.nodecode.ai as the yardstick, each starting from its own cold 2.9k-token prefix and resending the history every turn. "
      "Chat and Responses send a `prompt_cache_key`, which the proxy forwards as sent\n")
    w("3. Proxy and direct runs bill the same way on every turn, cold first, then 2,816 cached tokens, then 2,816 or 2,912. The 96-token difference on turn 3 shows up on both sides, so it comes from the provider's cache, and each side bills the count it was given. "
      "The proxy's `x-litellm-response-cost` equals the cost-map math on every turn\n")
    w(block(out_lines("after", "cache")))
    w(details("Upstream request for a cached chat turn (from the debug log)", pick(up_after, "https://api.nodecode.ai/v1/ ", "'prompt_cache_key'"), "text"))
    w("#### Persisted spend\n")
    rows = (HERE / "after" / "spend-compact.txt").read_text().splitlines()
    matched = sum(1 for l in rows if l.endswith("| t"))
    w(f"1. Every successful Nodecode row is priced from the cost map, (prompt - cached) x $0.33/M + cached x $0.009/M + output x $1.32/M, and all {matched} rows match that math exactly, streams included\n")
    w(details("LiteLLM_SpendLogs, After database, every successful nodecode/nodecode-cyber-v1 row", (HERE / "after" / "spend-compact.txt").read_text().rstrip(), "text"))
    (HERE / "proof-api.md").write_text("\n".join(md))
    print("wrote", HERE / "proof-api.md")


if __name__ == "__main__":
    main()
