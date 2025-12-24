import os
import json
import requests
import cloudscraper
import yaml
import gzip
import logging
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

from sources.youtube_rss import fetch_youtube_items
from sources.reddit_rss import fetch_reddit_items
from signals import build_signal_candidates
from feishu import notify_candidates

# 设置日志记录
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# -------------------------
# Config
# -------------------------
def load_config(config_path="config.yaml"):
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_feishu_webhook(config: dict) -> str:
    # GitHub Actions 中建议用 secrets.FEISHU_WEBHOOK -> env FEISHU_WEBHOOK 覆盖
    return os.getenv("FEISHU_WEBHOOK") or (config.get("feishu", {}) or {}).get("webhook_url", "")


# -------------------------
# Sitemap processing
# -------------------------
def process_sitemap(url):
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=20)
        response.raise_for_status()

        content = response.content

        # 智能检测 gzip 格式
        if content[:2] == b"\x1f\x8b":  # gzip magic number
            content = gzip.decompress(content)

        if b"<urlset" in content or b"<sitemapindex" in content:
            return parse_xml(content)
        else:
            return parse_txt(content.decode("utf-8", errors="ignore"))

    except requests.RequestException as e:
        logging.error(f"Error processing {url}: {str(e)}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error processing {url}: {str(e)}")
        return []


def parse_xml(content):
    urls = []
    soup = BeautifulSoup(content, "xml")
    # 兼容 sitemapindex / urlset：都用 loc
    for loc in soup.find_all("loc"):
        u = loc.get_text().strip()
        if u:
            urls.append(u)
    return urls


def parse_txt(content):
    return [line.strip() for line in content.splitlines() if line.strip()]


def save_latest(site_name, urls):
    latest_dir = Path("latest")
    latest_dir.mkdir(parents=True, exist_ok=True)

    latest_file = latest_dir / f"{site_name}.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        f.write("\n".join(urls))


def save_diff(site_name, new_urls):
    today = datetime.now().strftime("%Y%m%d")
    date_dir = Path("diff") / today
    date_dir.mkdir(parents=True, exist_ok=True)

    file_path = date_dir / f"{site_name}.json"
    mode = "a" if file_path.exists() else "w"
    with open(file_path, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write("\n--------------------------------\n")
        f.write("\n".join(new_urls) + "\n")


def compare_data(site_name, current_urls):
    latest_file = Path("latest") / f"{site_name}.json"
    if not latest_file.exists():
        return []

    with open(latest_file, encoding="utf-8") as f:
        last_urls = set(x.strip() for x in f.read().splitlines() if x.strip())

    return [u for u in current_urls if u not in last_urls]


def send_feishu_notification(new_urls, config, site_name):
    if not new_urls:
        return

    webhook_url = get_feishu_webhook(config)
    if not webhook_url:
        logging.warning("FEISHU webhook missing. Skip notification.")
        return

    message = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🎮 {site_name} 游戏上新通知"},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**今日新增 {len(new_urls)} 条**\n\n"
                                   + "\n".join(f"• {url}" for url in new_urls[:10]),
                    },
                }
            ],
        },
    }

    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json=message, timeout=10)
            resp.raise_for_status()
            logging.info("飞书通知发送成功")
            return
        except requests.RequestException as e:
            logging.error(f"飞书通知发送失败: {str(e)}")
            if attempt < 2:
                logging.info("重试发送通知...")


# -------------------------
# Signals: Trend Radar
# -------------------------
def run_signals(config):
    sig = config.get("signals") or {}
    if not sig.get("enabled"):
        return []

    items = []

    yt = sig.get("youtube_search_rss") or {}
    if yt.get("active"):
        items.extend(fetch_youtube_items(yt.get("queries", []), yt.get("max_items", 30)))

    rd = sig.get("reddit_rss") or {}
    if rd.get("active"):
        items.extend(fetch_reddit_items(rd.get("feeds", []), rd.get("max_items", 50)))

    rules = config.get("rules") or {}
    candidates = build_signal_candidates(items, rules)
    return candidates


def save_signals_diff(candidates):
    today = datetime.now().strftime("%Y%m%d")
    date_dir = Path("diff") / today
    date_dir.mkdir(parents=True, exist_ok=True)

    out_path = date_dir / "signals.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    return out_path


# -------------------------
# Cleanup
# -------------------------
def cleanup_old_data(config):
    data_dir = Path("diff")
    if not data_dir.exists():
        return

    retention_days = (config.get("storage") or {}).get("retention_days", 7)
    cutoff = datetime.now() - timedelta(days=retention_days)

    for date_dir in data_dir.glob("*"):
        if not date_dir.is_dir():
            continue

        try:
            dir_date = datetime.strptime(date_dir.name, "%Y%m%d")
            if dir_date < cutoff:
                # 删除目录下所有文件（包括 jsonl）
                for f in date_dir.glob("*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
                date_dir.rmdir()
                logging.info(f"已删除过期文件夹: {date_dir.name}")
        except ValueError:
            continue
        except Exception as e:
            logging.error(f"删除文件夹时出错: {str(e)}")


# -------------------------
# Main
# -------------------------
def main(config_path="config.yaml"):
    config = load_config(config_path)

    # 1) Sitemap monitors
    for site in (config.get("sites") or []):
        if not site.get("active"):
            continue

        site_name = site.get("name", "Unknown")
        logging.info(f"处理站点: {site_name}")

        all_urls = []
        for sitemap_url in site.get("sitemap_urls", []):
            urls = process_sitemap(sitemap_url)
            all_urls.extend(urls)

        # 去重（保持顺序）
        unique_urls = list(dict.fromkeys(all_urls))
        new_urls = compare_data(site_name, unique_urls)

        save_latest(site_name, unique_urls)

        if new_urls:
            save_diff(site_name, new_urls)
            send_feishu_notification(new_urls, config, site_name)

    # 2) Signals: Trend Radar (run once per workflow run)
    candidates = run_signals(config)
    if candidates:
        out_path = save_signals_diff(candidates)
        logging.info(f"Signals saved to: {out_path}")

        today = datetime.now().strftime("%Y%m%d")
        notify_candidates(
            config,
            f"🔥 Trend Radar 候选词（{today}）",
            candidates,
            max_items=10,
        )

    # 3) Cleanup old diff folders
    cleanup_old_data(config)


if __name__ == "__main__":
    main()
