#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书自定义机器人推送 - MACD 背离每日播报
读取 macd_data.json 汇总背离情况，推送富文本卡片到飞书群。

Webhook 来源（按顺序）：
  1) 环境变量 FEISHU_WEBHOOK
  2) 当前目录 .env 文件里的 FEISHU_WEBHOOK=...
未配置则打印摘要并退出（不报错），便于本地调试。
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_webhook():
    v = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if v:
        return v
    envp = os.path.join(HERE, ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8"):
            line = line.strip()
            if line.startswith("FEISHU_WEBHOOK="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_data():
    p = os.path.join(HERE, "data", "macd_data.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def summarize(d):
    periods = ["daily", "weekly", "monthly"]
    plabel = {"daily": "日线", "weekly": "周线", "monthly": "月线"}
    counts = {p: {"top": 0, "bottom": 0} for p in periods}
    resonance = []  # 周线信号 + 日线同方向
    details = {p: [] for p in periods}
    for r in d["indices"]:
        ps = r["periods"]
        st = {}
        for p in periods:
            if p in ps:
                st[p] = ps[p]["status"]
                if ps[p]["status"] != "none":
                    counts[p][ps[p]["status"]] += 1
                    div = ps[p]["divs"][-1] if ps[p]["divs"] else None
                    conf = ""
                    if div:
                        conf = "（已确认）" if div["confirmed"] else "（待确认）"
                    details[p].append(f"{r['name']}{conf}")
        if "weekly" in st and "daily" in st:
            ws, ds = st["weekly"], st["daily"]
            if ws != "none" and ds != "none" and ws == ds:
                resonance.append(f"{r['name']}：周线{'顶' if ws=='top' else '底'}背离 + 日线{'顶' if ds=='top' else '底'}背离")
    return periods, plabel, counts, resonance, details


def build_card(d):
    periods, plabel, counts, resonance, details = summarize(d)
    end = d["meta"].get("end_date", "")
    gen = d["meta"].get("generated_at", "")
    total = d["meta"]["counts"]["total"]

    lines = []
    lines.append(f"**数据截止**：{end}（生成于 {gen}）")
    lines.append(f"**监控范围**：{total} 个指数（宽基 + Wind 一级行业）")
    lines.append("")
    for p in periods:
        c = counts[p]
        lines.append(f"**{plabel[p]}**：🔴 顶背离 {c['top']} 个 ｜ 🟢 底背离 {c['bottom']} 个")
        if details[p]:
            lines.append("　" + "、".join(details[p]))
    lines.append("")

    if resonance:
        lines.append("**⚠️ 周线+日线共振（重点警惕）**：")
        for x in resonance:
            lines.append("　• " + x)
    else:
        lines.append("**周线+日线共振**：暂无")

    url = "https://autumn-go.github.io/macd-divergence-monitor/"
    lines.append("")
    lines.append(f"[📊 查看完整监控面板]({url})")

    # 头部配色：有共振用 red，有顶背离用 orange，否则 blue
    if resonance:
        tmpl = "red"
    elif counts["weekly"]["top"] or counts["daily"]["top"]:
        tmpl = "orange"
    else:
        tmpl = "blue"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"MACD 背离日报 · {end}"},
            "template": tmpl,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "数据来源：Wind 万得金融数据服务 · MACD(12,26,9)"}]},
        ],
    }
    return {"msg_type": "interactive", "card": card}


def main():
    wh = load_webhook()
    d = load_data()
    payload = build_card(d)
    if not wh:
        print("[feishu] 未配置 FEISHU_WEBHOOK，仅打印摘要：")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(wh, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            print("[feishu] 推送结果:", body)
    except Exception as e:
        print("[feishu] 推送失败:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
