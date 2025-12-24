# feishu.py
import os
import requests
import logging

def get_webhook(config: dict) -> str:
    # Actions secrets 覆盖 config
    return os.getenv("FEISHU_WEBHOOK") or (config.get("feishu", {}) or {}).get("webhook_url", "")

def notify_candidates(config: dict, title: str, candidates: list, max_items=10):
    webhook = get_webhook(config)
    if not webhook or not candidates:
        return

    show = candidates[:max_items]
    lines = []
    for c in show:
        src = ",".join(c.get("sources") or ([c.get("source")] if c.get("source") else []))
        lines.append(f"• **{c['keyword']}**  (score {c['score']}, {src})\n  {c.get('evidence_link','')}")

    content = f"**{title}**\n\n" + "\n".join(lines)
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ]
        }
    }

    for attempt in range(3):
        try:
            r = requests.post(webhook, json=card, timeout=10)
            r.raise_for_status()
            logging.info("Feishu candidates sent.")
            return
        except Exception as e:
            logging.error(f"Feishu send failed: {e}")
