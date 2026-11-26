#!/usr/bin/env python3
"""Live proof client for the Nodecode provider PR.

Usage: proof_client.py <family> <proxy base url> <output dir>
family is one of basic, tools, reasoning, errors, cache.

Reads LITELLM_MASTER_KEY from the environment. The cache family also sends the
same turns straight to https://api.nodecode.ai/v1 with NODECODE_API_KEY, as the
yardstick. Every capture is written with keys redacted.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

MODEL = "nodecode/nodecode-cyber-v1"
QUESTION = "What is 19 * 17? Answer with the number only."
TOOL_PROMPT = "Use the multiply tool to multiply 19 by 17, then tell me the product."
ARGS_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
}
CHAT_TOOLS = [
    {
        "type": "function",
        "function": {"name": "multiply", "description": "Multiply two integers", "parameters": ARGS_SCHEMA},
    }
]
RESPONSES_TOOLS = [
    {"type": "function", "name": "multiply", "description": "Multiply two integers", "parameters": ARGS_SCHEMA}
]
MESSAGES_TOOLS = [{"name": "multiply", "description": "Multiply two integers", "input_schema": ARGS_SCHEMA}]
RATES = {"input": 0.33e-6, "cached": 0.009e-6, "output": 1.32e-6}
DIRECT_BASE = "https://api.nodecode.ai/v1"
COOLDOWN_WAIT = 6
KEPT_HEADERS = ("x-litellm-response-cost", "x-litellm-model-id", "x-litellm-call-id", "content-type")


def redact(text: str) -> str:
    master = os.environ.get("LITELLM_MASTER_KEY", "")
    out = re.sub(r"sk-nc-[A-Za-z0-9_-]+", "sk-nc-REDACTED", text)
    return out.replace(master, "<LITELLM_MASTER_KEY>") if master else out


def parse_sse(raw: str) -> list:
    events, name = [], None
    for line in raw.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                parsed = data
            events.append({"event": name, "data": parsed})
            name = None
    return events


class Recorder:
    def __init__(self, base: str, out: Path, family: str):
        self.base, self.out, self.family, self.records = base.rstrip("/"), out, family, []
        self.client = httpx.Client(timeout=180)

    def post(self, case: str, path: str, body: dict, *, direct: bool = False, extra_headers: dict | None = None):
        if direct:
            url = f"{DIRECT_BASE}{path.removeprefix('/v1')}"
            headers = {"Authorization": f"Bearer {os.environ['NODECODE_API_KEY']}"}
        else:
            url = f"{self.base}{path}"
            headers = {"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}"}
        if path.endswith("/messages"):
            headers["anthropic-version"] = "2023-06-01"
        headers.update(extra_headers or {})
        resp = self.client.post(url, json=body, headers=headers)
        text = resp.text
        streamed = body.get("stream") is True and resp.status_code == 200
        payload = parse_sse(text) if streamed else _json_or_text(text)
        record = {
            "case": case,
            "request": {"method": "POST", "url": url, "body": body},
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if k.lower() in KEPT_HEADERS},
            "body" if not streamed else "events": payload,
        }
        self.records.append(record)
        return record

    def save(self):
        self.out.mkdir(parents=True, exist_ok=True)
        path = self.out / f"{self.family}.json"
        path.write_text(redact(json.dumps(self.records, indent=2)))
        print(f"saved {len(self.records)} captures to {path}")


def _json_or_text(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def cost_header(rec: dict) -> str:
    return rec["headers"].get("x-litellm-response-cost", "absent")


def error_line(rec: dict) -> str:
    body = rec.get("body")
    msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else str(body)
    return f"HTTP {rec['status']} {msg[:220]!r}"


# ---------- summaries ----------


def chat_summary(rec: dict) -> str:
    if rec["status"] != 200:
        return error_line(rec)
    if "events" in rec:
        text = "".join(
            (c.get("delta", {}).get("content") or "")
            for e in rec["events"]
            if isinstance(e["data"], dict)
            for c in e["data"].get("choices", [])
        )
        usage = next((e["data"]["usage"] for e in rec["events"] if isinstance(e["data"], dict) and e["data"].get("usage")), None)
        done = any(e["data"] == "[DONE]" for e in rec["events"])
        return f"HTTP 200 stream, {len(rec['events'])} events, text {text.strip()!r}, usage {_usage(usage)}, [DONE] {done}, cost header {cost_header(rec)}"
    msg = rec["body"]["choices"][0]["message"]
    reasoning = msg.get("reasoning_content") or ""
    return (
        f"HTTP 200, text {(msg.get('content') or '').strip()!r}, reasoning {len(reasoning)} chars, "
        f"usage {_usage(rec['body'].get('usage'))}, cost header {cost_header(rec)}"
    )


def _usage(u):
    if not u:
        return None
    if "prompt_tokens" in u:
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
        reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        return f"prompt {u['prompt_tokens']} (cached {cached}), completion {u['completion_tokens']} (reasoning {reasoning})"
    if "input_tokens" in u and "output_tokens" in u:
        details = u.get("input_tokens_details") or {}
        cached = details.get("cached_tokens", u.get("cache_read_input_tokens"))
        return f"input {u['input_tokens']} (cached {cached}), output {u['output_tokens']}"
    return json.dumps(u)


def responses_final(rec: dict) -> dict | None:
    if "events" in rec:
        return next((e["data"]["response"] for e in rec["events"] if e["event"] == "response.completed"), None)
    return rec["body"] if rec["status"] == 200 else None


def responses_text(resp: dict) -> str:
    return "".join(
        part.get("text", "")
        for item in resp.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
    )


def responses_summary(rec: dict) -> str:
    if rec["status"] != 200:
        return error_line(rec)
    final = responses_final(rec)
    if final is None:
        return f"HTTP 200 but no response.completed event in {len(rec['events'])} events"
    types = [item.get("type") for item in final.get("output", [])]
    kind = f"stream, {len(rec['events'])} events, " if "events" in rec else ""
    return f"HTTP 200 {kind}status {final.get('status')}, output {types}, text {responses_text(final).strip()!r}, usage {_usage(final.get('usage'))}, cost header {cost_header(rec)}"


def messages_final(rec: dict) -> dict:
    if "events" not in rec:
        return rec["body"]
    blocks, usage, stop = {}, {}, None
    for e in rec["events"]:
        d = e["data"] if isinstance(e["data"], dict) else {}
        t = d.get("type")
        if t == "message_start":
            usage.update(d["message"].get("usage") or {})
        elif t == "content_block_start":
            blocks[d["index"]] = dict(d["content_block"])
            if blocks[d["index"]].get("type") == "tool_use":
                blocks[d["index"]]["_json"] = ""
        elif t == "content_block_delta":
            b, delta = blocks[d["index"]], d["delta"]
            if delta["type"] == "text_delta":
                b["text"] = b.get("text", "") + delta["text"]
            elif delta["type"] == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + delta["thinking"]
            elif delta["type"] == "input_json_delta":
                b["_json"] += delta["partial_json"]
        elif t == "message_delta":
            usage.update(d.get("usage") or {})
            stop = d.get("delta", {}).get("stop_reason")
    content = []
    for _, b in sorted(blocks.items()):
        if b.get("type") == "tool_use":
            b["input"] = json.loads(b.pop("_json") or "{}")
        content.append(b)
    return {"content": content, "usage": usage, "stop_reason": stop}


def messages_summary(rec: dict) -> str:
    if rec["status"] != 200:
        return error_line(rec)
    final = messages_final(rec)
    types = [b.get("type") for b in final["content"]]
    text = "".join(b.get("text", "") for b in final["content"] if b.get("type") == "text")
    kind = ""
    if "events" in rec:
        names = [e["event"] for e in rec["events"]]
        kind = f"stream, {len(names)} events, {names.count('message_stop')} message_stop, "
    return f"HTTP 200 {kind}blocks {types}, stop {final.get('stop_reason')}, text {text.strip()!r}, usage {_usage(final.get('usage'))}, cost header {cost_header(rec)}"


# ---------- families ----------


def family_basic(r: Recorder):
    chat = {"model": MODEL, "messages": [{"role": "user", "content": QUESTION}], "max_completion_tokens": 512}
    print("chat json:", chat_summary(r.post("chat_json", "/v1/chat/completions", chat)))
    print("chat stream:", chat_summary(r.post("chat_stream", "/v1/chat/completions", {**chat, "stream": True, "stream_options": {"include_usage": True}})))
    resp = {"model": MODEL, "input": QUESTION, "max_output_tokens": 512}
    print("responses json:", responses_summary(r.post("responses_json", "/v1/responses", resp)))
    print("responses stream:", responses_summary(r.post("responses_stream", "/v1/responses", {**resp, "stream": True})))
    msgs = {"model": MODEL, "max_tokens": 512, "messages": [{"role": "user", "content": QUESTION}]}
    print("messages json:", messages_summary(r.post("messages_json", "/v1/messages", msgs)))
    print("messages stream:", messages_summary(r.post("messages_stream", "/v1/messages", {**msgs, "stream": True})))
    work = {**chat, "model": "nodecode-openai-workaround"}
    print("openai/ workaround chat json:", chat_summary(r.post("workaround_chat_json", "/v1/chat/completions", work)))


def _chat_tool_call(rec: dict):
    if "events" not in rec:
        msg = rec["body"]["choices"][0]["message"]
        return msg, msg.get("tool_calls") or []
    calls, content = {}, ""
    for e in rec["events"]:
        d = e["data"] if isinstance(e["data"], dict) else {}
        for c in d.get("choices", []):
            delta = c.get("delta", {})
            content += delta.get("content") or ""
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc["index"], {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
    tool_calls = [calls[i] for i in sorted(calls)]
    return {"role": "assistant", "content": content or None, "tool_calls": tool_calls}, tool_calls


def family_tools(r: Recorder):
    for mode in ("json", "stream"):
        stream = {"stream": True, "stream_options": {"include_usage": True}} if mode == "stream" else {}
        base = {"model": MODEL, "tools": CHAT_TOOLS, "max_completion_tokens": 1024, **stream}
        first_msgs = [{"role": "user", "content": TOOL_PROMPT}]
        rec1 = r.post(f"chat_tool_{mode}_1", "/v1/chat/completions", {**base, "messages": first_msgs})
        if rec1["status"] != 200:
            print(f"chat tool {mode}:", error_line(rec1))
            continue
        assistant, calls = _chat_tool_call(rec1)
        if not calls:
            print(f"chat tool {mode}: no tool call, {chat_summary(rec1)}")
            continue
        call = calls[0]
        args = json.loads(call["function"]["arguments"] or "{}")
        result = str(args.get("a", 0) * args.get("b", 0))
        msgs = [*first_msgs, assistant, {"role": "tool", "tool_call_id": call["id"], "content": result}]
        rec2 = r.post(f"chat_tool_{mode}_2", "/v1/chat/completions", {**base, "messages": msgs})
        print(f"chat tool {mode}: call {call['function']['name']}({call['function']['arguments']}) id {call['id']} -> client returned {result} -> {chat_summary(rec2)}")

    for mode in ("json", "stream"):
        base = {"model": MODEL, "tools": RESPONSES_TOOLS, "max_output_tokens": 1024, **({"stream": True} if mode == "stream" else {})}
        rec1 = r.post(f"responses_tool_{mode}_1", "/v1/responses", {**base, "input": TOOL_PROMPT})
        final = responses_final(rec1)
        if rec1["status"] != 200 or final is None:
            print(f"responses tool {mode}:", responses_summary(rec1))
            continue
        call = next((i for i in final.get("output", []) if i.get("type") == "function_call"), None)
        if call is None:
            print(f"responses tool {mode}: no function_call, {responses_summary(rec1)}")
            continue
        args = json.loads(call.get("arguments") or "{}")
        result = str(args.get("a", 0) * args.get("b", 0))
        output_item = {"type": "function_call_output", "call_id": call["call_id"], "output": result}
        rec2 = r.post(f"responses_tool_{mode}_2_previous_response_id", "/v1/responses", {**base, "previous_response_id": final["id"], "input": [output_item]})
        how = "previous_response_id"
        if rec2["status"] != 200:
            print(f"responses tool {mode}: previous_response_id continuation -> {responses_summary(rec2)}")
            history = [{"role": "user", "content": TOOL_PROMPT}, {k: call[k] for k in ("type", "call_id", "name", "arguments")}, output_item]
            rec2 = r.post(f"responses_tool_{mode}_2_resent_history", "/v1/responses", {**base, "input": history})
            how = "resent history"
        print(f"responses tool {mode}: call {call['name']}({call.get('arguments')}) call_id {call['call_id']} -> client returned {result} via {how} -> {responses_summary(rec2)}")

    for mode in ("json", "stream"):
        base = {"model": MODEL, "tools": MESSAGES_TOOLS, "max_tokens": 1024, **({"stream": True} if mode == "stream" else {})}
        first = [{"role": "user", "content": TOOL_PROMPT}]
        rec1 = r.post(f"messages_tool_{mode}_1", "/v1/messages", {**base, "messages": first})
        if rec1["status"] != 200:
            print(f"messages tool {mode}:", error_line(rec1))
            continue
        final = messages_final(rec1)
        call = next((b for b in final["content"] if b.get("type") == "tool_use"), None)
        if call is None:
            print(f"messages tool {mode}: no tool_use, {messages_summary(rec1)}")
            continue
        result = str(call["input"].get("a", 0) * call["input"].get("b", 0))
        assistant_content = [b for b in final["content"] if b.get("type") in ("text", "tool_use", "thinking")]
        msgs = [*first, {"role": "assistant", "content": assistant_content}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": result}]}]
        rec2 = r.post(f"messages_tool_{mode}_2", "/v1/messages", {**base, "messages": msgs})
        print(f"messages tool {mode}: tool_use {call['name']}({json.dumps(call['input'])}) id {call['id']} -> client returned {result} -> {messages_summary(rec2)}")


def family_reasoning(r: Recorder):
    chat = {"model": MODEL, "messages": [{"role": "user", "content": QUESTION}], "max_completion_tokens": 2048}
    for effort in ("none", "max"):
        print(f"chat reasoning_effort={effort}:", chat_summary(r.post(f"chat_reasoning_{effort}", "/v1/chat/completions", {**chat, "reasoning_effort": effort})))
    msgs = {"model": MODEL, "max_tokens": 2048, "messages": [{"role": "user", "content": QUESTION}]}
    print("messages default:", messages_summary(r.post("messages_default", "/v1/messages", msgs)))
    print("messages thinking disabled:", messages_summary(r.post("messages_thinking_disabled", "/v1/messages", {**msgs, "thinking": {"type": "disabled"}})))
    print("messages thinking disabled + reasoning_effort high:", messages_summary(r.post("messages_thinking_disabled_effort_high", "/v1/messages", {**msgs, "thinking": {"type": "disabled"}, "reasoning_effort": "high"})))
    print("messages reasoning_effort none:", messages_summary(r.post("messages_effort_none", "/v1/messages", {**msgs, "reasoning_effort": "none"})))
    print("messages output_config effort low:", messages_summary(r.post("messages_output_config_low", "/v1/messages", {**msgs, "output_config": {"effort": "low"}})))
    if os.environ.get("PROOF_SKIP_DIRECT") == "1":
        return
    # The same Messages requests sent straight to api.nodecode.ai, as the yardstick
    direct = {**msgs, "model": "nodecode-cyber-v1"}
    print("direct messages thinking disabled + reasoning_effort high:", messages_summary(r.post("direct_messages_thinking_disabled_effort_high", "/v1/messages", {**direct, "thinking": {"type": "disabled"}, "reasoning_effort": "high"}, direct=True)))
    print("direct messages reasoning_effort none:", messages_summary(r.post("direct_messages_effort_none", "/v1/messages", {**direct, "reasoning_effort": "none"}, direct=True)))
    print("direct messages output_config effort low:", messages_summary(r.post("direct_messages_output_config_low", "/v1/messages", {**direct, "output_config": {"effort": "low"}}, direct=True)))


def family_errors(r: Recorder):
    # The router cools a deployment down for 5 seconds after a failed call, so each
    # call waits that out first. Otherwise every call after the first one reads the
    # router's 429 instead of the provider's error.
    for model in ("nodecode-invalid-key", "nodecode-missing-model"):
        time.sleep(COOLDOWN_WAIT)
        print(f"{model} chat:", error_line(r.post(f"{model}_chat", "/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": QUESTION}], "max_completion_tokens": 64})))
        time.sleep(COOLDOWN_WAIT)
        print(f"{model} responses:", error_line(r.post(f"{model}_responses", "/v1/responses", {"model": model, "input": QUESTION, "max_output_tokens": 64})))
        time.sleep(COOLDOWN_WAIT)
        print(f"{model} messages:", error_line(r.post(f"{model}_messages", "/v1/messages", {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": QUESTION}]})))
        time.sleep(COOLDOWN_WAIT)
        stream_rec = r.post(f"{model}_chat_stream", "/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": QUESTION}], "max_completion_tokens": 64, "stream": True})
        print(f"{model} chat stream:", error_line(stream_rec) if stream_rec["status"] != 200 else f"HTTP 200 stream, events {json.dumps(stream_rec['events'])[:300]}")


def _reference_notes(nonce: str) -> str:
    lines = [f"Session {nonce}. You are a terse assistant. Answer with the number or word asked for, nothing else.", "Reference notes for this session:"]
    for i in range(1, 121):
        lines.append(f"Note {i}: shelf {i % 7} holds {i * 3} copper parts, inspected on day {(i * 11) % 28 + 1}, lot code L{i:03d}.")
    return "\n".join(lines)


def _turn_cost(u: dict) -> float:
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return (u["prompt_tokens"] - cached) * RATES["input"] + cached * RATES["cached"] + u["completion_tokens"] * RATES["output"]


def _cache_request(endpoint: str, side: str, nonce: str, notes: str, history: list) -> tuple:
    model = MODEL if side == "proxy" else "nodecode-cyber-v1"
    key = f"proof-{endpoint}-{side}-{nonce}"
    if endpoint == "chat":
        body = {"model": model, "messages": [{"role": "system", "content": notes}, *history], "max_completion_tokens": 256, "reasoning_effort": "none", "prompt_cache_key": key}
        return "/v1/chat/completions", body
    if endpoint == "responses":
        body = {"model": model, "instructions": notes, "input": list(history), "max_output_tokens": 256, "reasoning": {"effort": "none"}, "prompt_cache_key": key}
        return "/v1/responses", body
    body = {"model": model, "system": notes, "messages": list(history), "max_tokens": 256, "thinking": {"type": "disabled"}}
    return "/v1/messages", body


def _cache_turn_usage(endpoint: str, body: dict) -> tuple:
    """Returns (answer, total prompt tokens, cached tokens, output tokens)."""
    u = body["usage"]
    if endpoint == "chat":
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return body["choices"][0]["message"]["content"], u["prompt_tokens"], cached, u["completion_tokens"]
    if endpoint == "responses":
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
        return responses_text(body), u["input_tokens"], cached, u["output_tokens"]
    cached = u.get("cache_read_input_tokens") or 0
    text = "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text")
    return text, u["input_tokens"] + cached, cached, u["output_tokens"]


def family_cache(r: Recorder):
    """A 3-turn conversation with a 2.9k-token stable prefix on each endpoint, history resent
    every turn (api.nodecode.ai keeps no response state, so there is no continuation id).
    The proxy side and the direct side each start from their own cold prefix."""
    questions = ["Reply with the word ready.", QUESTION, "Add 1 to your previous answer. Answer with the number only."]
    sides = os.environ.get("PROOF_CACHE_SIDES", "proxy,direct").split(",")
    for endpoint in ("chat", "responses", "messages"):
        for side in sides:
            nonce = uuid.uuid4().hex[:12]
            notes = _reference_notes(nonce)
            history = []
            for turn, q in enumerate(questions, 1):
                history.append({"role": "user", "content": q})
                path, body = _cache_request(endpoint, side, nonce, notes, history)
                rec = r.post(f"cache_{endpoint}_{side}_turn{turn}", path, body, direct=side == "direct")
                if rec["status"] != 200:
                    print(f"cache {endpoint} {side} turn {turn}:", error_line(rec))
                    break
                answer, prompt, cached, out = _cache_turn_usage(endpoint, rec["body"])
                history.append({"role": "assistant", "content": answer})
                cost = (prompt - cached) * RATES["input"] + cached * RATES["cached"] + out * RATES["output"]
                print(
                    f"cache {endpoint} {side} turn {turn}: answer {answer.strip()!r}, prompt {prompt} (cached {cached}), "
                    f"output {out}, cost at the cost-map rates {cost:.9f}, cost header {cost_header(rec)}"
                )


FAMILIES = {"basic": family_basic, "tools": family_tools, "reasoning": family_reasoning, "errors": family_errors, "cache": family_cache}


def main():
    family, base, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    recorder = Recorder(base, out, family)
    try:
        FAMILIES[family](recorder)
    finally:
        recorder.save()


if __name__ == "__main__":
    main()
