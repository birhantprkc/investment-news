#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py —— 抓 sources.json 里各行业全部源的真实数据,带"最近 N 天"过滤,
时间规整为北京时间"MM-DD HH:MM",每栏按时间倒序,写 ../data.js。纯标准库,零依赖。
"""
import json, os, re, urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PER = 5
CUTOFF = None
REDLINE = []
BEIJING = timezone(timedelta(hours=8))

# 单源抓取超时（秒）。海外源在部分网络下握手就要十几秒，14s 会把大量本来能成功的
# 源判成失败（issue #3）。走代理时建议再放宽。
# urllib 默认就会读取 HTTPS_PROXY / HTTP_PROXY / NO_PROXY 环境变量，无需额外配置。
TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "30"))

def strip_html(s): return re.sub(r"\s+"," ", re.sub(r"<[^>]+>","", s or "")).strip()
def local(tag): return tag.split("}")[-1]

# 只剥「跟踪参数」，不整段砍 query：不少源把文章 id 放在 query 里（?p=123、?id=456），
# 砍掉整段会把不同的文章折叠成同一条 —— 那是丢内容，比重复显示更糟。
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "from", "ref", "referrer", "source", "spm", "share", "share_token",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
}

# 同一条新闻被多家转载时，各家发布时间相近。标题去重限定在这个窗口内，
# 「每周综述」这类**跨周复用的固定栏目名**就不会被误判成重复（去重窗口默认 120 天，
# 不加窗口会把不同周的同名栏目全清成一条）。
DUP_TITLE_WINDOW_S = 48 * 3600


def normalize_url(url):
    """URL 归一化，用于判断是不是同一个链接。剥跟踪参数 + 去锚点 + 去尾斜杠。"""
    if not url:
        return ""
    url = url.strip()
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    # scheme/host 大小写不敏感，path 保持原样（部分服务器 path 区分大小写）
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(),
        parts.path.rstrip("/"), urlencode(kept), "",
    ))


def normalize_title(title):
    """标题归一化：去空白、标点、大小写差异，用来识别转载。"""
    return re.sub(r"[\W_]+", "", (title or "").lower())


def dedup(items):
    """同栏内去重：同一 URL 只留一条；标题相同且发布时间相近的也只留一条。

    传入的 items 需已按时间倒序，保留的即每组里最新的那条。
    标题去重刻意加时间窗（见 DUP_TITLE_WINDOW_S）：不加窗口会把「每周综述」
    这类跨周复用的栏目名整片清掉，属于静默丢内容。
    """
    seen_urls, seen_titles, out = set(), {}, []
    for it in items:
        url_key = normalize_url(it.get("url", ""))
        if url_key and url_key in seen_urls:
            continue
        title_key = normalize_title(it.get("title", ""))
        ts = it.get("ts", 0)
        if title_key:
            prev_ts = seen_titles.get(title_key)
            # ts=0 表示源没给时间，无法判断远近，只按标题相同就算重复
            if prev_ts is not None and (
                not ts or not prev_ts or abs(prev_ts - ts) <= DUP_TITLE_WINDOW_S
            ):
                continue
            seen_titles[title_key] = ts
        if url_key:
            seen_urls.add(url_key)
        out.append(it)
    return out

def parse_dt(s):
    if not s: return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try: dt = datetime.fromisoformat(s.strip().replace("Z","+00:00"))
        except Exception: return None
    if dt is None: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt

def fetch(src):
    try:
        req = urllib.request.Request(src["url"], headers={"User-Agent":UA,
              "Accept":"application/rss+xml,application/atom+xml,application/xml,text/xml,*/*"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        out = []
        for n in [e for e in root.iter() if local(e.tag) in ("item","entry")]:
            if len(out) >= PER: break
            d = {"title":"","url":"","time":"","ts":0,"summary":"","source":src["name"]}
            rawtime = ""
            for c in n:
                t = local(c.tag)
                if t=="title" and not d["title"]: d["title"]=(c.text or "").strip()
                elif t=="link" and not d["url"]: d["url"]=c.get("href") or (c.text or "").strip()
                elif t in ("pubDate","published","updated","date") and not rawtime: rawtime=(c.text or "").strip()
                elif t in ("description","summary","content") and not d["summary"]: d["summary"]=strip_html(c.text or "")[:160]
            if not d["title"]: continue
            blob = (d["title"] + " " + d["summary"]).lower()   # 红线过滤(赌博/加密/预测市场)
            if any(k in blob for k in REDLINE): continue
            dt = parse_dt(rawtime)
            if dt is not None:
                if CUTOFF and dt < CUTOFF: continue          # 旧文剔除
                d["time"] = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
                d["ts"] = int(dt.timestamp())
            else:
                d["time"] = "—"
            out.append(d)
        return out
    except Exception as e:   # 不再静默吞掉:打出失败源名+原因(超时/非法XML/编码)
        print("  ⚠️ 源抓取失败 %s: %s" % (src.get("name","?"), str(e)[:80]))
        return None          # None=出错(区别于 []=成功但近 N 天无新内容)

def main():
    global CUTOFF, REDLINE
    cfg = json.load(open(os.path.join(ROOT,"sources.json"), encoding="utf-8"))
    days = cfg.get("fetch",{}).get("recent_days", 120)
    CUTOFF = datetime.now(timezone.utc) - timedelta(days=days)
    REDLINE = [k.lower() for k in cfg.get("redline_keywords", [])]
    inds = cfg["industries"]
    byhint = {}
    for s in cfg["sources"]:
        byhint.setdefault(s["hint"], []).append(s)

    industries, tasks = [], []
    for i, ind in enumerate(inds):
        pool = byhint.get(ind["key"], [])
        industries.append({"key":ind["key"],"name":ind["name"],"accent":ind["accent"],
                           "total":len(pool),"items":[]})
        for s in pool: tasks.append((i, s))

    with ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(lambda t: (t[0], t[1].get("name",""), fetch(t[1])), tasks))
    failed_sources = []
    for idx, name, items in results:
        if items is None:          # 该源抓取出错
            failed_sources.append(name); continue
        industries[idx]["items"].extend(items)
    # 每栏按时间倒序(新→旧)，再做条目层去重(同链接/同标题的转载,#1)
    dup_removed = 0
    for ind in industries:
        ind["items"].sort(key=lambda x: x.get("ts",0), reverse=True)
        before = len(ind["items"])
        ind["items"] = dedup(ind["items"])
        dup_removed += before - len(ind["items"])

    data = {"generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
            "recent_days":days, "industries":industries,
            "stats":{"industries":len(inds),"total_sources":len(cfg["sources"])}}
    with open(os.path.join(ROOT,"data.js"),"w",encoding="utf-8") as f:
        f.write("// data.js —— 各行业源真实数据(最近%d天,北京时间,新→旧)。fetch.py 抓取 → digest.py 补 AI 要点+翻译。\n"%days)
        f.write("window.DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print("最近 %d 天 · 行业 | 源数 | 条数" % days)
    for ind in industries:
        print("  %-16s %3d 源 → %d 条" % (ind["name"], ind["total"], len(ind["items"])))
    print("总源:", len(cfg["sources"]), "· 生成:", data["generated_at"])
    if dup_removed:
        print("去重: 剔除 %d 条重复(同链接或同标题转载)" % dup_removed)
    if failed_sources:
        print("⚠️  %d/%d 个源抓取失败:%s%s" % (len(failed_sources), len(cfg["sources"]),
              "、".join(failed_sources[:15]), " …" if len(failed_sources) > 15 else ""))

if __name__ == "__main__":
    main()
