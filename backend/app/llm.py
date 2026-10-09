"""Optional LLM wording layer. Off by default (ARES_USE_LLM=1 enables). It may only rephrase a message;
numbers, modes and decisions always come from the rules. Any failure returns the rule text, labelled 'rule-fallback'."""
import os


def phrase(agent_name, goal, text):
    if os.environ.get("ARES_USE_LLM") != "1" or not os.environ.get("ANTHROPIC_API_KEY"):
        return text, "rule"
    try:
        import httpx
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=float(os.environ.get("ARES_LLM_TIMEOUT", "8")),
                       headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": os.environ.get("ARES_LLM_MODEL", "claude-sonnet-5-5"), "max_tokens": 200,
                             "system": f"You are the {agent_name} agent of a Mars colony council. Goal: {goal}. Rewrite the message in first person in at most 2 sentences. Keep every number, mode name and decision exactly unchanged. Output only the message.",
                             "messages": [{"role": "user", "content": text}]})
        out = "".join(b.get("text", "") for b in r.json()["content"]).strip()
        return (out, "llm") if out else (text, "rule-fallback")
    except Exception:
        return text, "rule-fallback"
