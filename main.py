import os
import requests
import cloudscraper
import yaml
import gzip
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from bs4 import BeautifulSoup

# ================= 基础设置 =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ================= 配置 =================
def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def get_feishu_webhook(config):
    return os.getenv("FEISHU_WEBHOOK") or config.get("feishu", {}).get("webhook_url")

# ================= URL 规范化 =================
def normalize_url(url: str) -> str:
    """
    只保留 scheme + domain + path
    去掉 ?query 和 #fragment
    """
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    except Exception:
        return url

# ================= URL 过滤（核心降噪） =================
EXCLUDE_KEYWORDS = [
    "/tag/", "/tags/",
    "/category/", "/categories/",
    "/about", "/privacy", "/terms",
    "/contact", "/faq", "/policy",
    "/search", "/sitemap", "/wp-",
]

def is_valid_game_url(url: str) -> bool:
    u = url.lower()

    # 黑名单关键词
    for k in EXCLUDE_KEYWORDS:
        if k in u:
            return False

    # 必须是 http(s)
    if not u.startswith("http"):
        return False

    # 路径太短的一般不是游戏页
    if urlsplit(u).path.count("/") < 2:
        return False

    return True

# ================= Sitemap 处理 =================
def process_sitemap(url):
    try:
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=20)
        resp.raise_for_status()

        content = resp.content
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)

        if b"<urlset" in content or b"<sitemapindex" in content:
            return parse_xml(content)
        else:
            return parse_txt(content.decode("utf-8", errors="ignore"))

    except Exception as e:
        logging.error(f"Sitemap error {url}: {e}")
        return []

def parse_xml(content):
    soup = BeautifulSoup(content, "xml")
    urls = []

    for loc in soup.find_all("loc"):
        raw = loc.get_text().strip()
        if not raw:
            continue

        u = normalize_url(raw)
        if is_valid_game_url(u):
            urls.append(u)

    return urls

def parse_txt(text):
    urls = []

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("http"):
            continue

        u = normalize_url(line)
        if is_valid_game_url(u):
            urls.append(u)

    return urls

# ================= 数据存储 =================
def save_latest(site, urls):
    Path("latest").mkdir(exist_ok=True)
    with open(f"latest/{site}.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(urls))

def load_latest(site):
    path = Path(f"latest/{site}.txt")
    if not path.exists():
        return set()
    return set(x.strip() for x in path.read_text(encoding="utf-8").splitlines())

def save_diff(site, urls):
    today = datetime.now().strftime("%Y%m%d")
    folder = Path("diff") / today
    folder.mkdir(parents=True, exist_ok=True)

    with open(folder / f"{site}.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(urls))

# ================= 飞书通知 =================
def send_feishu(site, urls, config):
    if not urls:
        return

    webhook = get_feishu_webhook(config)
    if not webhook:
        return

    content = "\n".join(f"• {u}" for u in urls[:10])
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🎮 {site} 新增游戏"},
                "template": "green"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**今日新增 {len(urls)} 条**\n\n{content}"
                    }
                }
            ]
        }
    }

    requests.post(webhook, json=payload, timeout=10)

# ================= 清理历史 =================
def cleanup(config):
    days = config.get("storage", {}).get("retention_days", 7)
    cutoff = datetime.now() - timedelta(days=days)

    for d in Path("diff").glob("*"):
        try:
            date = datetime.strptime(d.name, "%Y%m%d")
            if date < cutoff:
                for f in d.glob("*"):
                    f.unlink()
                d.rmdir()
        except Exception:
            pass

# ================= 主流程 =================
def main():
    config = load_config()

    for site in config.get("sites", []):
        if not site.get("active"):
            continue

        name = site["name"]
        logging.info(f"Processing {name}")

        all_urls = []
        for sm in site.get("sitemap_urls", []):
            all_urls.extend(process_sitemap(sm))

        # 去重（保持顺序）
        current = list(dict.fromkeys(all_urls))
        last = load_latest(name)

        new_urls = [u for u in current if u not in last]

        save_latest(name, current)

        if new_urls:
            save_diff(name, new_urls)
            send_feishu(name, new_urls, config)

    cleanup(config)

if __name__ == "__main__":
    main()
