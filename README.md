# MACD 背离监控面板

基于《MACD背离形态在牛市中的应用与启示》的核心方法，每日监控主要宽基指数与 Wind 一级行业指数的 **日线 / 周线 / 月线** MACD 顶底背离。

## 方法（复现文章）
- **顶背离**：价格创新高，但 DIF 快线 / 柱状图峰值未同步新高；严格顶背离需 DIF 下穿 DEA（死叉）确认。
- **底背离**：价格创新低，但 DIF 谷值未同步新低；需 DIF 上穿 DEA（金叉）确认。
- **周期差异**：日线为短期信号（伪信号较多），周线为中期拐点前瞻（胜率最高），**周线 + 日线共振最有价值**。

## 数据
- 来源：Wind 万得金融数据服务（`wind-mcp-skill`）
- 指数：9 个宽基 + 11 个 Wind 一级行业指数
- MACD 参数：快线 12 / 慢线 26 / 信号 9

## 本地运行
```bash
# 1. 拉取数据并计算背离（生成 data/macd_data.js / .json）
python3 macd_monitor.py

# 2. 浏览器直接打开 index.html 即可（无需服务器）
```

## 每日自动化
通过定时任务每日重跑 `macd_monitor.py` + `refill.py`，并自动部署到 GitHub Pages、推送飞书日报。

## 部署
```bash
bash deploy.sh   # 推送到 GitHub Pages
```
