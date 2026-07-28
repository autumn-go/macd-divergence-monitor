#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 背离监控数据管线
===================
依据《MACD背离形态在牛市中的应用与启示》的核心方法：
  - 顶背离：价格创新高，但 DIF 快线(或柱状图)峰值未同步新高 -> 上涨动能衰减；
            严格顶背离需 DIF 下穿 DEA(死叉)确认。
  - 底背离：价格创新低，但 DIF/柱状图谷值未同步新低 -> 下跌动能衰竭；
            严格底背离需 DIF 上穿 DEA(金叉)确认。
  - 周期：日线(短期, 伪信号多) / 周线(中期拐点前瞻, 胜率高) / 月线(长周期)。
  - 周线+日线共振最有信号价值。

数据来源：Wind 万得金融数据服务 (通过 wind-mcp-skill CLI)。
输出：data/macd_data.json  (含完整 OHLC + MACD 序列 与 背离标注，供网页渲染)
"""

import subprocess
import json
import os
import sys
import time
import datetime

# ---------- 路径与运行时 ----------
SKILL_DIR = "/Users/beanpaper/.workbuddy/skills/wind-mcp-skill"
NODE = "/Users/beanpaper/.workbuddy/binaries/node/versions/22.22.2/bin/node"
CLI = "scripts/cli.mjs"
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(WORKSPACE, "data")
OUT_FILE = os.path.join(OUT_DIR, "macd_data.json")

# ---------- 监控标的 ----------
# 宽基指数 (9)
BROAD = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
    ("000016.SH", "上证50"),
    ("881001.WI", "万得全A"),
]
# Wind 一级行业指数 (11)
INDUSTRY = [
    ("882100.WI", "能源"),
    ("882200.WI", "材料"),
    ("000930.SH", "工业"),
    ("882400.WI", "可选消费"),
    ("882500.WI", "日常消费"),
    ("882600.WI", "医疗保健"),
    ("882701.WI", "金融"),
    ("000935.SH", "信息技术"),
    ("000936.SH", "电信服务"),
    ("000937.SH", "公用事业"),
    ("000948.SH", "房地产"),
]

# ---------- 周期配置 ----------
# period: Wind K线周期(10日/11周/12月); begin: 起始日; keep: 网页保留条数; order: 拐点识别窗口
PERIODS = {
    "daily":   {"period": "10", "begin": "20240101", "keep": 420, "order": 4, "min_rows": 580},
    "weekly":  {"period": "11", "begin": "20220101", "keep": 210, "order": 3, "min_rows": 230},
    "monthly": {"period": "12", "begin": "20180101", "keep": 130, "order": 2, "min_rows": 95},
}

MACD_PARAMS = {"fast": 12, "slow": 26, "signal": 9}


# ---------- 指标计算 ----------
def ema(vals, n):
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = []
    prev = vals[0]
    for i, v in enumerate(vals):
        prev = v if i == 0 else v * k + prev * (1 - k)
        out.append(prev)
    return out


def calc_macd(close):
    e12 = ema(close, MACD_PARAMS["fast"])
    e26 = ema(close, MACD_PARAMS["slow"])
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, MACD_PARAMS["signal"])
    hist = [2 * (d - x) for d, x in zip(dif, dea)]  # Wind 红绿柱 = 2*(DIF-DEA)
    return dif, dea, hist


def find_pivots(vals, order, kind):
    """返回局部拐点(峰值/谷值)的索引列表。"""
    piv = []
    n = len(vals)
    for i in range(order, n - order):
        left = vals[i - order:i]
        right = vals[i + 1:i + order + 1]
        if kind == "high":
            if vals[i] > vals[i - 1] and vals[i] >= max(left) and vals[i] >= max(right):
                piv.append(i)
        else:
            if vals[i] < vals[i - 1] and vals[i] <= min(left) and vals[i] <= min(right):
                piv.append(i)
    return piv


def death_cross(dif, dea, start, end):
    for i in range(max(1, start), end):
        if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
            return i
    return None


def golden_cross(dif, dea, start, end):
    for i in range(max(1, start), end):
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
            return i
    return None


def detect_divergence(dates, close, dif, dea, hist, order):
    """返回该序列检测到的背离列表 (顶/底)。"""
    n = len(close)
    divs = []

    # 顶背离：近两次峰值，价格更高但 DIF 更低
    peaks = find_pivots(close, order, "high")
    if len(peaks) >= 2:
        p2, p1 = peaks[-1], peaks[-2]
        price_higher = close[p2] > close[p1]
        dif_lower = dif[p2] < dif[p1]
        if price_higher and dif_lower:
            dc = death_cross(dif, dea, p2, n)
            confirmed = dc is not None
            strength = "strong" if confirmed else ("medium" if (close[p2] / close[p1] - 1) > 0.01 else "weak")
            active = p2 >= int(n * 0.65)
            note = (
                f"价格于 {dates[p2]} 创近期新高 {close[p2]:.2f}，但 DIF 未同步新高"
                f"（{dif[p2]:.3f} < 前高 {dif[p1]:.3f}），顶背离形成"
            )
            if confirmed:
                note += f"；DIF 已于 {dates[dc]} 下穿 DEA（死叉）确认，信号可靠性高"
            else:
                note += "；尚未出现 DIF 下穿 DEA 的死叉确认，信号待验证（极强趋势中或为伪信号）"
            divs.append({
                "type": "top", "peak_idx": p2, "prev_idx": p1,
                "confirmed": confirmed, "strength": strength, "active": active,
                "price_change_pct": round((close[p2] / close[p1] - 1) * 100, 2),
                "dif_change": round(dif[p2] - dif[p1], 3),
                "note": note,
            })

    # 底背离：近两次谷值，价格更低但 DIF 更高
    troughs = find_pivots(close, order, "low")
    if len(troughs) >= 2:
        t2, t1 = troughs[-1], troughs[-2]
        price_lower = close[t2] < close[t1]
        dif_higher = dif[t2] > dif[t1]
        if price_lower and dif_higher:
            gc = golden_cross(dif, dea, t2, n)
            confirmed = gc is not None
            strength = "strong" if confirmed else ("medium" if (1 - close[t2] / close[t1]) > 0.01 else "weak")
            active = t2 >= int(n * 0.65)
            note = (
                f"价格于 {dates[t2]} 创近期新低 {close[t2]:.2f}，但 DIF 未同步新低"
                f"（{dif[t2]:.3f} > 前低 {dif[t1]:.3f}），底背离形成"
            )
            if confirmed:
                note += f"；DIF 已于 {dates[gc]} 上穿 DEA（金叉）确认，下跌动能衰竭确认"
            else:
                note += "；尚未出现 DIF 上穿 DEA 的金叉确认，信号待验证"
            divs.append({
                "type": "bottom", "peak_idx": t2, "prev_idx": t1,
                "confirmed": confirmed, "strength": strength, "active": active,
                "price_change_pct": round((close[t2] / close[t1] - 1) * 100, 2),
                "dif_change": round(dif[t2] - dif[t1], 3),
                "note": note,
            })

    # 主状态：优先已确认/近期信号
    status = "none"
    if divs:
        # 已确认优先；其次近期；否则取最后一个
        confirmed_divs = [d for d in divs if d["confirmed"]]
        active_divs = [d for d in divs if d["active"]]
        pick = confirmed_divs or active_divs or divs
        status = pick[-1]["type"]
    return divs, status


# ---------- Wind CLI 调用 ----------
def call_wind(windcode, begin, end, period, tries=6, min_rows=None):
    params = json.dumps({
        "windcode": windcode,
        "begin_date": begin,
        "end_date": end,
        "period": period,
    }, ensure_ascii=False)
    backoff = [2, 4, 8, 16, 32]
    for attempt in range(tries):
        try:
            res = subprocess.run(
                [NODE, CLI, "call", "index_data", "get_index_kline", params],
                cwd=SKILL_DIR, capture_output=True, text=True, timeout=150)
            if res.returncode != 0:
                err = (res.stderr or res.stdout)[:300]
                print(f"  [重试 {attempt+1}/{tries}] CLI 退出码 {res.returncode}: {err}")
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            payload = json.loads(res.stdout)
            if payload.get("isError"):
                print(f"  [重试 {attempt+1}/{tries}] isError: {payload}")
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            text = payload["content"][0]["text"]
            data = json.loads(text)
            if data.get("error"):
                print(f"  [重试 {attempt+1}/{tries}] data.error: {data['error']}")
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            rows = data["data"].get("rows", [])
            if min_rows and len(rows) < min_rows:
                print(f"  [重试 {attempt+1}/{tries}] 疑似限流：仅返回 {len(rows)} 条 (<{min_rows})，丢弃并重试")
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            return data["data"]
        except Exception as e:
            print(f"  [重试 {attempt+1}/{tries}] 异常: {e}")
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None


def _col(cols, *names):
    for n in names:
        if n in cols:
            return cols.index(n)
    return -1


def parse_kline(data):
    cols = [c["name"] for c in data["columns"]]
    rows = data["rows"]
    im = cols.index("TIME")
    io = cols.index("OPEN")
    ic = cols.index("MATCH")
    ih = cols.index("HIGH")
    il = cols.index("LOW")
    iv = _col(cols, "VOLUME", "VOL", "AMOUNT")   # 部分行业指数无成交量字段
    out = {"dates": [], "open": [], "close": [], "high": [], "low": [], "volume": []}
    for r in rows:
        out["dates"].append(r[im][:10])
        out["open"].append(float(r[io]))
        out["close"].append(float(r[ic]))
        out["high"].append(float(r[ih]))
        out["low"].append(float(r[il]))
        if iv >= 0:
            try:
                out["volume"].append(float(r[iv]))
            except Exception:
                out["volume"].append(0.0)
        else:
            out["volume"].append(0.0)
    return out


# ---------- 主流程 ----------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    end_date = today
    print(f"== MACD 背离数据管线启动 == 截止日 {end_date}")

    indices_out = []
    all_defs = [("broad", BROAD), ("industry", INDUSTRY)]

    for category, lst in all_defs:
        for code, name in lst:
            print(f"\n>> [{category}] {name} ({code})")
            rec = {
                "code": code, "name": name, "category": category,
                "periods": {}, "errors": [],
            }
            for pkey, pcfg in PERIODS.items():
                data = call_wind(code, pcfg["begin"], end_date, pcfg["period"], tries=12, min_rows=pcfg.get("min_rows"))
                time.sleep(2.5)  # 调用间留间隔，避免触发 Wind 后端限流
                if data is None:
                    rec["errors"].append(f"{pkey}: 取数失败")
                    print(f"   - {pkey}: 取数失败，跳过")
                    continue
                kl = parse_kline(data)
                if len(kl["close"]) < 60:
                    rec["errors"].append(f"{pkey}: 数据不足({len(kl['close'])}条)")
                    print(f"   - {pkey}: 数据不足，跳过")
                    continue
                dif, dea, hist = calc_macd(kl["close"])
                divs, status = detect_divergence(
                    kl["dates"], kl["close"], dif, dea, hist, pcfg["order"])

                # 截取最近 keep 条用于图表；背离索引需同步平移
                keep = min(pcfg["keep"], len(kl["dates"]))
                start = len(kl["dates"]) - keep
                for d in divs:
                    d["peak_idx"] -= start
                    d["prev_idx"] -= start
                sdates = kl["dates"][start:]
                sopen = [round(x, 2) for x in kl["open"][start:]]
                sclose = [round(x, 2) for x in kl["close"][start:]]
                shigh = [round(x, 2) for x in kl["high"][start:]]
                slow = [round(x, 2) for x in kl["low"][start:]]
                svol = [round(x) for x in kl["volume"][start:]]
                sdif = [round(x, 3) for x in dif[start:]]
                sdea = [round(x, 3) for x in dea[start:]]
                shist = [round(x, 3) for x in hist[start:]]

                rec["periods"][pkey] = {
                    "status": status,
                    "divs": divs,
                    "last_close": sclose[-1],
                    "prev_close": sclose[-2] if len(sclose) > 1 else sclose[-1],
                    "series": {
                        "dates": sdates, "open": sopen, "close": sclose,
                        "high": shigh, "low": slow, "volume": svol,
                        "dif": sdif, "dea": sdea, "macd": shist,
                    },
                }
                tag = "✓背离" if status != "none" else "—"
                print(f"   - {pkey}: {len(sdates)}条, 状态={status} {tag}")
            indices_out.append(rec)

    result = {
        "meta": {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "Wind 万得金融数据服务",
            "end_date": end_date,
            "macd_params": MACD_PARAMS,
            "method": "价格与DIF峰值对比 + 死叉/金叉确认；日线短期、周线中期、月线长周期；周线+日线共振最优",
            "counts": {
                "broad": len(BROAD), "industry": len(INDUSTRY),
                "total": len(BROAD) + len(INDUSTRY),
            },
        },
        "indices": indices_out,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    # 同时输出 .js 版本（window.MACD_DATA），使网页可 file:// 双击直接打开，避免 CORS
    with open(OUT_FILE.replace(".json", ".js"), "w", encoding="utf-8") as f:
        f.write("window.MACD_DATA = ")
        json.dump(result, f, ensure_ascii=False)
        f.write(";")
    print(f"\n== 完成 == 输出 {OUT_FILE} 及 macd_data.js")
    # 简要统计
    top = bot = 0
    for rec in indices_out:
        for p in rec["periods"].values():
            if p["status"] == "top":
                top += 1
            elif p["status"] == "bottom":
                bot += 1
    print(f"背离统计：顶背离 {top} 处 / 底背离 {bot} 处 (跨指数×周期)")


if __name__ == "__main__":
    main()
