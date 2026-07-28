#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补齐 / 校正 macd_data.json 中缺失或被限流截断的周期数据。
设计目标：在 Wind 后端偶发抽风（NER 随机返回 NOT_FOUND / 限流返回截断数据）的情况下，
用「大间隔 + 多轮冷却 + 增量落盘」逐步把数据补到 100% 完整且准确。

- 阈值与主管线一致：日>=580 / 周>=230 / 月>=95（低于此值视为截断，丢弃重试）
- 单点最多重试 15 次，指数退避（3s 起，最长 300s）
- 每次调用间隔 4s，避免触发后端限流
- 外层多轮循环（默认 8 轮），每轮之间冷却 60s，等待后端恢复
- 每成功补齐一个周期立即写盘，进度不丢
"""
import subprocess, json, os, time

SKILL_DIR = "/Users/beanpaper/.workbuddy/skills/wind-mcp-skill"
NODE = "/Users/beanpaper/.workbuddy/binaries/node/versions/22.22.2/bin/node"
CLI = "scripts/cli.mjs"
BASE = "/Users/beanpaper/WorkBuddy/2026-07-27-23-08-55"
JSON_FILE = os.path.join(BASE, "data", "macd_data.json")

PERIODS = {
    "daily":   {"period": "10", "begin": "20240101", "keep": 420, "order": 4, "min": 580},
    "weekly":  {"period": "11", "begin": "20220101", "keep": 210, "order": 3, "min": 230},
    "monthly": {"period": "12", "begin": "20180101", "keep": 130, "order": 2, "min": 95},
}
MACD = {"fast": 12, "slow": 26, "signal": 9}
MAX_CYCLES = 8
CALL_GAP = 4          # 每次调用间隔(秒)
CYCLE_COOLDOWN = 60   # 每轮之间冷却(秒)


def ema(vals, n):
    if not vals: return []
    k = 2.0 / (n + 1); out = []; prev = vals[0]
    for i, v in enumerate(vals):
        prev = v if i == 0 else v * k + prev * (1 - k); out.append(prev)
    return out

def calc_macd(close):
    e12 = ema(close, MACD["fast"]); e26 = ema(close, MACD["slow"])
    dif = [a - b for a, b in zip(e12, e26)]; dea = ema(dif, MACD["signal"])
    hist = [2 * (d - x) for d, x in zip(dif, dea)]
    return dif, dea, hist

def find_pivots(vals, order, kind):
    piv = []; n = len(vals)
    for i in range(order, n - order):
        left = vals[i - order:i]; right = vals[i + 1:i + order + 1]
        if kind == "high":
            if vals[i] > vals[i - 1] and vals[i] >= max(left) and vals[i] >= max(right): piv.append(i)
        else:
            if vals[i] < vals[i - 1] and vals[i] <= min(left) and vals[i] <= min(right): piv.append(i)
    return piv

def death_cross(dif, dea, start, end):
    for i in range(max(1, start), end):
        if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]: return i
    return None

def golden_cross(dif, dea, start, end):
    for i in range(max(1, start), end):
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]: return i
    return None

def detect(close, dif, dea, order):
    n = len(close); divs = []
    peaks = find_pivots(close, order, "high")
    if len(peaks) >= 2:
        p2, p1 = peaks[-1], peaks[-2]
        if close[p2] > close[p1] and dif[p2] < dif[p1]:
            dc = death_cross(dif, dea, p2, n); confirmed = dc is not None
            strength = "strong" if confirmed else ("medium" if (close[p2]/close[p1]-1) > .01 else "weak")
            active = p2 >= int(n * .65)
            divs.append({"type": "top", "peak_idx": p2, "prev_idx": p1, "confirmed": confirmed,
                         "strength": strength, "active": active,
                         "price_change_pct": round((close[p2]/close[p1]-1)*100, 2),
                         "dif_change": round(dif[p2]-dif[p1], 3)})
    troughs = find_pivots(close, order, "low")
    if len(troughs) >= 2:
        t2, t1 = troughs[-1], troughs[-2]
        if close[t2] < close[t1] and dif[t2] > dif[t1]:
            gc = golden_cross(dif, dea, t2, n); confirmed = gc is not None
            strength = "strong" if confirmed else ("medium" if (1-close[t2]/close[t1]) > .01 else "weak")
            active = t2 >= int(n * .65)
            divs.append({"type": "bottom", "peak_idx": t2, "prev_idx": t1, "confirmed": confirmed,
                         "strength": strength, "active": active,
                         "price_change_pct": round((close[t2]/close[t1]-1)*100, 2),
                         "dif_change": round(dif[t2]-dif[t1], 3)})
    status = "none"
    if divs:
        confirmed_divs = [d for d in divs if d["confirmed"]]
        active_divs = [d for d in divs if d["active"]]
        pick = confirmed_divs or active_divs or divs
        status = pick[-1]["type"]
    return divs, status

def _col(cols, *names):
    for nm in names:
        if nm in cols: return cols.index(nm)
    return -1

def parse(data):
    cols = [c["name"] for c in data["columns"]]; rows = data["rows"]
    im = cols.index("TIME"); io = cols.index("OPEN"); ic = cols.index("MATCH")
    ih = cols.index("HIGH"); il = cols.index("LOW"); iv = _col(cols, "VOLUME", "VOL", "AMOUNT")
    out = {"dates": [], "open": [], "close": [], "high": [], "low": [], "volume": []}
    for r in rows:
        out["dates"].append(r[im][:10]); out["open"].append(float(r[io]))
        out["close"].append(float(r[ic])); out["high"].append(float(r[ih])); out["low"].append(float(r[il]))
        out["volume"].append(float(r[iv]) if iv >= 0 else 0.0)
    return out

def call_wind(code, begin, end, period):
    params = json.dumps({"windcode": code, "begin_date": begin, "end_date": end, "period": period}, ensure_ascii=False)
    backoff = [3, 6, 12, 20, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 300]
    for att in range(15):
        try:
            res = subprocess.run([NODE, CLI, "call", "index_data", "get_index_kline", params],
                                  cwd=SKILL_DIR, capture_output=True, text=True, timeout=150)
            if res.returncode != 0:
                print(f"    [att{att+1}] exit {res.returncode}"); time.sleep(backoff[min(att, len(backoff)-1)]); continue
            payload = json.loads(res.stdout)
            text = payload["content"][0]["text"]; d = json.loads(text)["data"]
            if d.get("error"):
                code_err = d['error'].get('code')
                print(f"    [att{att+1}] err {code_err}")
                time.sleep(backoff[min(att, len(backoff)-1)]); continue
            return d
        except Exception as e:
            print(f"    [att{att+1}] exc {e}"); time.sleep(backoff[min(att, len(backoff)-1)])
    return None

def save(data):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open(JSON_FILE.replace(".json", ".js"), "w", encoding="utf-8") as f:
        f.write("window.MACD_DATA = "); json.dump(data, f, ensure_ascii=False); f.write(";")

def collect_targets(data):
    targets = []
    for r in data["indices"]:
        for p in ["daily", "weekly", "monthly"]:
            pcfg = PERIODS[p]
            if p not in r["periods"]:
                targets.append((r["code"], r["name"], p))
            else:
                have = len(r["periods"][p]["series"]["dates"])
                if have < pcfg["min"]:
                    targets.append((r["code"], r["name"], p))  # 已有但被截断，需重抓
    return targets

def main():
    with open(JSON_FILE, encoding="utf-8") as f:
        data = json.load(f)
    end_date = data["meta"]["end_date"]

    for cyc in range(1, MAX_CYCLES + 1):
        targets = collect_targets(data)
        if not targets:
            print(f"[轮 {cyc}] 全部周期已完整且达标，无需补齐。")
            break
        print(f"\n===== 第 {cyc}/{MAX_CYCLES} 轮：待补齐 {len(targets)} 个 =====")
        filled = 0
        for code, name, pkey in targets:
            pcfg = PERIODS[pkey]
            print(f">> {name} ({code}) {pkey}")
            payload = call_wind(code, pcfg["begin"], end_date, pcfg["period"])
            time.sleep(CALL_GAP)
            if not payload:
                print("   跳过(本轮持续失败)，下轮再试"); continue
            kl = parse(payload)
            if len(kl["close"]) < pcfg["min"]:
                print(f"   数据疑似截断({len(kl['close'])}条<{pcfg['min']})，下轮再试"); continue
            dif, dea, hist = calc_macd(kl["close"])
            divs, status = detect(kl["close"], dif, dea, pcfg["order"])
            keep = min(pcfg["keep"], len(kl["dates"])); start = len(kl["dates"]) - keep
            for d in divs:
                d["peak_idx"] -= start; d["prev_idx"] -= start
            # 写回对应指数
            for r in data["indices"]:
                if r["code"] == code:
                    r["periods"][pkey] = {
                        "status": status, "divs": divs,
                        "last_close": kl["close"][-1],
                        "prev_close": kl["close"][-2] if len(kl["close"]) > 1 else kl["close"][-1],
                        "series": {
                            "dates": kl["dates"][start:], "open": [round(x,2) for x in kl["open"][start:]],
                            "close": [round(x,2) for x in kl["close"][start:]], "high": [round(x,2) for x in kl["high"][start:]],
                            "low": [round(x,2) for x in kl["low"][start:]], "volume": [round(x) for x in kl["volume"][start:]],
                            "dif": [round(x,3) for x in dif[start:]], "dea": [round(x,3) for x in dea[start:]],
                            "macd": [round(x,3) for x in hist[start:]],
                        },
                    }
                    break
            save(data)  # 增量落盘
            print(f"   补齐成功: {len(r['periods'][pkey]['series']['dates'])}条, 状态={status}")
            filled += 1
        print(f"[轮 {cyc}] 本轮补齐 {filled} 个。")
        remaining = collect_targets(data)
        if not remaining:
            print("全部补齐完成 ✅")
            break
        else:
            print(f"仍缺 {len(remaining)} 个，冷却 {CYCLE_COOLDOWN}s 后进入下一轮…")
            time.sleep(CYCLE_COOLDOWN)

    final = collect_targets(data)
    print(f"\n== 收尾结束 == 仍缺失/截断: {len(final)} 个")
    if final:
        for c, n, p in final:
            print(f"   - {n} ({c}) {p}")

if __name__ == "__main__":
    main()
