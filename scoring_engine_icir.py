#!/usr/bin/env python3
"""
SS-ICIR v2.1 — 决策驱动报告
=============================
去掉"强烈买入/逢低买入"分档，改为操作台风格：

信号面板: 🟢 新买入 / 🔴 触发卖出 / 🟡 排名下滑
持仓追踪: 之前推荐的 → 现在怎么样了
排名变化: vs 昨日的排名升降
板块热力: 哪个板块机会最多
"""

import sys, os, json, math
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

def _load_env():
    env_path = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

from stock_db import StockDB
_db = StockDB()
from v11.factor_engine import compute_all_factors, FACTOR_NAMES
from scoring_engine import get_theme

ICIR_W = {
    "turnover_z": 0.451, "log_mcap": 0.162, "mfi": 0.153, "pct_52w": 0.091,
    "pe_percentile": 0.078, "pb_percentile": 0.078, "gap_open": 0.072,
    "max_dd_20d": 0.052, "ma_trend": 0.030, "rsi_signal": 0.028,
    "macd_signal": 0.026, "cmf": 0.025, "vwap_premium": 0.022,
    "event_score": 0.020, "event_count": 0.018, "sector_rsi": 0.015,
    "sector_momentum": 0.012, "inflow_rate": 0.010, "main_flow_5d": 0.008,
    "main_flow_20d": 0.006, "amplitude_z": 0.005, "ret_20d": 0.004,
    "volatility_20d": 0.003, "ma_bull": 0.029, "vol_price": 0.025,
    "dev_ma20": 0.024, "vol_ratio_5d": 0.023, "ret_5d": 0.021,
    "streak": 0.020, "roe_rank": 0.001, "gross_margin_rank": 0.001,
    "ocf_ratio_rank": 0.001,
}
_total = sum(ICIR_W.values())
ICIR = {k: v/_total for k, v in ICIR_W.items()}

HISTORY_FILE = os.path.join(PROJECT_DIR, "output", "icir_signal_history.json")
RANK_FILE = os.path.join(PROJECT_DIR, "output", "icir_rank_history.json")


def score(fv): return sum(ICIR.get(fn, 0.01) * fv.get(fn, 0) for fn in FACTOR_NAMES)

def load_json(path):
    if os.path.exists(path):
        with open(path) as f: return json.load(f)
    return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: json.dump(data, f, ensure_ascii=False)

def send_email(html_path, today, recipient="914110627@qq.com"):
    if not os.environ.get("SMTP_USER"): return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders
    try:
        with open(html_path) as f: html = f.read()
    except: return False
    msg = MIMEMultipart()
    msg["Subject"] = f"📊 SS-ICIR 决策日报 {today}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = recipient
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(f"SS-ICIR 决策日报 {today}\n详情见附件。", "plain", "utf-8"))
    body.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(body)
    attach = MIMEBase("application", "octet-stream")
    attach.set_payload(html.encode("utf-8"))
    encoders.encode_base64(attach)
    attach.add_header("Content-Disposition", f'attachment; filename="SS-ICIR_{today}.html"')
    msg.attach(attach)
    try:
        s = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT","587")), timeout=30)
        s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.sendmail(os.environ["SMTP_USER"], recipient, msg.as_string()); s.quit()
        return True
    except Exception as e:
        print(f"  ❌ 邮件: {e}"); return False


# ========== 主流程 ==========
def run_daily(codes_file=None, output_dir=None, recipient=None):
    today = datetime.now().strftime("%Y-%m-%d")
    if codes_file is None: codes_file = os.path.join(PROJECT_DIR, "uploaded-stock-codes.txt")
    if output_dir is None: output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    print(f"╔══════════════════════════════╗")
    print(f"║  SS-ICIR 决策日报 [{today}]  ║")
    print(f"╚══════════════════════════════╝")

    # ====== 数据 ======
    with open(codes_file) as f: codes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    klines = _db.get_klines(codes, days=130)
    extra = _db.get_extra_info(codes)
    for c in extra: extra[c]["_sector"] = get_theme(c)

    # ====== 评分 + 排名 ======
    raw = {}
    for code in codes:
        kl = klines.get(code)
        if not kl or len(kl) < 60: continue
        ex = extra.get(code, {})
        fv = compute_all_factors({code: kl}, {code: ex}, today_str=today).get(code, {})
        if not fv: continue
        raw[code] = score(fv)

    sorted_codes = sorted(raw.items(), key=lambda x: -x[1])
    n = len(sorted_codes)

    results = {}
    for rank, (code, s) in enumerate(sorted_codes):
        kl = klines.get(code, []); ex = extra.get(code, {})
        rp = rank / n  # 0=best
        results[code] = {
            "code": code, "name": ex.get("name", ""), "price": ex.get("price", 0),
            "change_pct": ex.get("change_pct", 0), "sector": ex.get("_sector", get_theme(code)),
            "rank_pct": rp, "rank": rank + 1, "raw_score": round(s, 2),
            "ret_5d": round((kl[-1]['close']/kl[-6]['close']-1)*100, 1) if len(kl)>=6 else 0,
        }

    # ====== 排名变化 vs 昨日 ======
    prev_ranks = load_json(RANK_FILE)
    rank_change = {}
    for code, r in results.items():
        prev = prev_ranks.get(code, {})
        prev_rp = prev.get("rank_pct")
        if prev_rp is not None:
            delta = prev_rp - r["rank_pct"]  # 正=变好（排名上升）
            if abs(delta) < 0.01: rank_change[code] = "→"
            elif delta > 0.05: rank_change[code] = "↑↑"
            elif delta > 0.02: rank_change[code] = "↑"
            elif delta < -0.05: rank_change[code] = "↓↓"
            elif delta < -0.02: rank_change[code] = "↓"
            else: rank_change[code] = "→"
        else:
            rank_change[code] = "●"
        results[code]["rank_change"] = rank_change[code]
        results[code]["prev_rank_pct"] = prev_rp

    # 保存今日排名
    save_json(RANK_FILE, {c: {"rank_pct": r["rank_pct"], "date": today} for c, r in results.items()})

    # ====== 信号追踪 ======
    signal_history = load_json(HISTORY_FILE)

    new_buys = []       # 首次进入 top 15%
    sell_alerts = []    # 触发卖出
    holdings = []       # 之前推荐的，现在仍 top 15%
    watch_list = []     # 之前推荐的，已跌出 top 15% 但未触发卖出
    rank_risers = []    # 排名上升显著的（非推荐股票中）
    rank_fallers = []   # 排名下降显著的

    top15_codes = {c for c, r in results.items() if r["rank_pct"] < 0.15}
    top30_codes = {c for c, r in results.items() if r["rank_pct"] < 0.30}

    for code, r in results.items():
        if code not in signal_history: signal_history[code] = {"buy_dates": [], "alerts": []}
        hist = signal_history[code]
        has_position = len(hist["buy_dates"]) > 0
        last_buy = hist["buy_dates"][-1] if has_position else None

        if code in top15_codes:
            if not has_position:
                # 新买入
                hist["buy_dates"].append({
                    "date": today, "price": r["price"], "rank_pct": r["rank_pct"]
                })
                new_buys.append(r)
            else:
                # 持续持有
                entry_price = last_buy["price"]
                pnl = (r["price"] / entry_price - 1) * 100 if entry_price > 0 else 0
                holdings.append({**r, "entry_date": last_buy["date"],
                                "entry_price": entry_price, "pnl": round(pnl, 1),
                                "days_held": (datetime.strptime(today, "%Y-%m-%d") -
                                             datetime.strptime(last_buy["date"], "%Y-%m-%d")).days})
        elif has_position and last_buy:
            # 不在 top 15% 了
            entry_price = last_buy["price"]
            pnl = (r["price"] / entry_price - 1) * 100 if entry_price > 0 else 0
            days_held = (datetime.strptime(today, "%Y-%m-%d") -
                        datetime.strptime(last_buy["date"], "%Y-%m-%d")).days

            entry_rank = last_buy.get("rank_pct", 0.15)
            stop = -12 if entry_rank < 0.10 else (-8 if entry_rank < 0.20 else -5)

            tracking = {**r, "entry_date": last_buy["date"], "entry_price": entry_price,
                       "pnl": round(pnl, 1), "days_held": days_held}

            if r["rank_pct"] > 0.5 or pnl <= stop:
                trigger = "排名崩溃" if r["rank_pct"] > 0.5 else f"止损 ({pnl:+.1f}% ≤ {stop}%)"
                hist["alerts"].append({"date": today, "reason": "sell", "trigger": trigger})
                sell_alerts.append({**tracking, "trigger": trigger})
            elif r["rank_pct"] > 0.30:
                watch_list.append({**tracking, "trigger": f"排名跌至 {r['rank_pct']:.0%}"})
            else:
                watch_list.append(tracking)

        # 排名变化（非持仓股票）
        if code not in top15_codes and rank_change.get(code) in ("↑↑", "↑"):
            rank_risers.append(r)
        if code in top15_codes and rank_change.get(code) in ("↓↓", "↓"):
            rank_fallers.append(r)

    # 清理历史
    for code in signal_history:
        signal_history[code]["buy_dates"] = signal_history[code]["buy_dates"][-5:]
        signal_history[code]["alerts"] = signal_history[code]["alerts"][-20:]
    save_json(HISTORY_FILE, signal_history)

    # ====== 板块 ======
    sectors = defaultdict(list)
    for r in results.values():
        sectors[r["sector"]].append(r)

    sector_stats = []
    for sec, stocks in sorted(sectors.items(), key=lambda x: -len([s for s in x[1] if s["rank_pct"] < 0.15])):
        top10 = len([s for s in stocks if s["rank_pct"] < 0.10])
        top15 = len([s for s in stocks if s["rank_pct"] < 0.15])
        avg_rank = sum(s["rank_pct"] for s in stocks) / len(stocks)
        top3 = sorted(stocks, key=lambda s: s["rank_pct"])[:3]
        sector_stats.append({
            "name": sec, "count": len(stocks), "top10": top10,
            "top15": top15, "avg_rank": avg_rank,
            "top3": [(s["code"], s["name"], s["rank_pct"]) for s in top3],
        })

    # ====== HTML ======
    html = _build_html(today, n, results, new_buys, sell_alerts, holdings, watch_list,
                       rank_risers, rank_fallers, sector_stats, rank_change)

    html_path = os.path.join(output_dir, f"SS-ICIR_{today}.html")
    with open(html_path, "w") as f: f.write(html)

    # JSON
    json_path = os.path.join(output_dir, f"SS-ICIR_{today}.json")
    with open(json_path, "w") as f:
        json.dump({
            "date": today, "model": "SS-ICIR v2.1", "total": n,
            "new_buys": len(new_buys), "sell_alerts": len(sell_alerts),
            "holdings": len(holdings), "watch": len(watch_list),
            "results": [r for r in sorted(results.values(), key=lambda x: x["rank_pct"])],
        }, f, ensure_ascii=False, indent=2)

    if os.environ.get("SMTP_USER"):
        send_email(html_path, today, recipient)

    print(f"\n  📊 {n}只 | 🟢新买入{len(new_buys)} | 🔴卖出{len(sell_alerts)} | ✅持有{len(holdings)} | 🟡关注{len(watch_list)}")
    if new_buys:
        print(f"  🟢 新信号: " + " | ".join(f"{r['code']} {r['name']} (top{r['rank_pct']:.0%})" for r in new_buys[:5]))
    if sell_alerts:
        print(f"  🔴 卖出: " + " | ".join(f"{r['code']} {r['name']} — {r['trigger']}" for r in sell_alerts[:5]))
    return results


def _build_html(today, n, results, new_buys, sell_alerts, holdings, watch_list,
                rank_risers, rank_fallers, sector_stats, rank_change):
    """构建决策日报 HTML"""

    strong = len([r for r in results.values() if r["rank_pct"] < 0.10])
    top15 = len([r for r in results.values() if r["rank_pct"] < 0.15])

    # 信号面板
    signal_html = ""
    if sell_alerts:
        signal_html += '<div class="signal signal-red"><h3>🔴 触发卖出</h3><div class="sig-items">'
        for a in sell_alerts[:8]:
            signal_html += f'<div class="sig-item"><b>{a["code"]} {a["name"]}</b> <span class="dim">买入 {a["entry_date"]} @{a["entry_price"]:.2f}→现价{a["price"]:.2f}</span> <span class="pnl {'red' if a['pnl']>0 else 'green'}">{a["pnl"]:+.1f}%</span> — {a["trigger"]}</div>'
        signal_html += '</div></div>'

    if new_buys:
        signal_html += '<div class="signal signal-green"><h3>🟢 新买入信号</h3><div class="sig-items">'
        for r in new_buys[:12]:
            chg = f'{r["change_pct"]:+.1f}%' if r["change_pct"] else ""
            chg_cls = "red" if r["change_pct"] > 0 else "green"
            signal_html += f'<div class="sig-item"><b>{r["code"]} {r["name"]}</b> <span class="rank">top {r["rank_pct"]:.0%}</span> <span class="dim">{r["sector"]}</span> <span class="{chg_cls}">{chg}</span></div>'
        signal_html += '</div></div>'

    if holdings:
        signal_html += '<div class="signal signal-blue"><h3>✅ 持仓中</h3><div class="sig-items">'
        for r in sorted(holdings, key=lambda x: x["rank_pct"])[:6]:
            signal_html += f'<div class="sig-item"><b>{r["code"]} {r["name"]}</b> <span class="dim">买入{r["entry_date"]} @{r["entry_price"]:.2f}</span> <span class="pnl {'red' if r['pnl']>0 else 'green'}">{r["pnl"]:+.1f}%</span> <span class="dim">{r["days_held"]}天</span> <span class="rank">top {r["rank_pct"]:.0%}</span></div>'
        if len(holdings) > 6:
            signal_html += f'<div class="sig-item dim">...共 {len(holdings)} 只</div>'
        signal_html += '</div></div>'

    if watch_list:
        signal_html += '<div class="signal signal-yellow"><h3>🟡 关注列表</h3><div class="sig-items">'
        for r in sorted(watch_list, key=lambda x: -x["rank_pct"])[:6]:
            signal_html += f'<div class="sig-item"><b>{r["code"]} {r["name"]}</b> <span class="dim">买入{r["entry_date"]} @{r["entry_price"]:.2f}</span> <span class="pnl {'red' if r['pnl']>0 else 'green'}">{r["pnl"]:+.1f}%</span> <span class="dim">→top {r["rank_pct"]:.0%}</span></div>'
        signal_html += '</div></div>'

    # 持仓明细
    pos_table = ""
    all_positions = holdings + watch_list
    if all_positions:
        pos_table = '<div class="card"><h3>📋 持仓追踪</h3><table>'
        pos_table += '<tr><th>代码</th><th>名称</th><th>买入日</th><th>买入价</th><th>现价</th><th>盈亏</th><th>持天数</th><th>当前排名</th><th>状态</th></tr>'
        for r in sorted(all_positions, key=lambda x: x["rank_pct"]):
            in_hold = r in holdings
            status = "✅持有" if in_hold else ("⚠️关注" if r["rank_pct"] < 0.5 else "🔴预警")
            pos_table += f'<tr class="{"hold-row" if in_hold else "watch-row"}"><td>{r["code"]}</td><td>{r["name"]}</td>'
            pos_table += f'<td>{r["entry_date"]}</td><td>{r["entry_price"]:.2f}</td><td>{r["price"]:.2f}</td>'
            pos_table += f'<td class="{"red" if r["pnl"]>0 else "green"}">{r["pnl"]:+.1f}%</td>'
            pos_table += f'<td>{r["days_held"]}天</td><td>top {r["rank_pct"]:.0%}</td><td>{status}</td></tr>'
        pos_table += '</table></div>'

    # 排名变化
    change_table = ""
    if rank_risers or rank_fallers:
        change_table += '<div class="card"><h3>📈 排名异动</h3>'
        if rank_risers:
            change_table += '<div style="margin-bottom:8px"><b style="color:#d32f2f">↑ 排名上升</b> '
            change_table += ' '.join(f'<span style="margin-right:6px;font-size:12px">{r["code"]} {r["name"]}</span>' for r in rank_risers[:10])
            change_table += '</div>'
        if rank_fallers:
            change_table += '<div><b style="color:#2e7d32">↓ 排名下滑</b> '
            change_table += ' '.join(f'<span style="margin-right:6px;font-size:12px">{r["code"]} {r["name"]}</span>' for r in rank_fallers[:10])
            change_table += '</div>'
        change_table += '</div>'

    # 分桶统计
    buckets = {"top 10%": strong, "10-20%": top15 - strong, "20-40%": len([r for r in results.values() if 0.2 <= r["rank_pct"] < 0.4]),
               "40-60%": len([r for r in results.values() if 0.4 <= r["rank_pct"] < 0.6]),
               "60%+": len([r for r in results.values() if r["rank_pct"] >= 0.6])}
    bucket_html = '<div class="buckets">'
    for label, cnt in buckets.items():
        pct = cnt / n * 100
        bucket_html += f'<div class="bucket"><div class="b-num">{cnt}</div><div class="b-label">{label}</div><div class="b-bar"><div style="width:{pct}%"></div></div></div>'
    bucket_html += '</div>'

    # 完整排名表
    all_rows = ""
    for r in sorted(results.values(), key=lambda x: x["rank_pct"]):
        arrow = rank_change.get(r["code"], "")
        arrow_color = {"↑↑": "#d32f2f", "↑": "#e53935", "→": "#999", "↓": "#388e3c", "↓↓": "#2e7d32", "●": "#1565c0"}.get(arrow, "#999")
        chg_cls = "red" if r["change_pct"] > 0 else "green"
        highlight = "highlight" if r["rank_pct"] < 0.15 else ""
        all_rows += f'<tr class="{highlight}"><td>{r["code"]}</td><td>{r["name"]}</td><td>{r["sector"]}</td>'
        all_rows += f'<td><b>top {r["rank_pct"]:.0%}</b> <span style="color:{arrow_color}">{arrow}</span></td>'
        all_rows += f'<td>{r["price"]:.2f}</td><td class="{chg_cls}">{r["change_pct"]:+.2f}%</td>'
        all_rows += f'<td class="{chg_cls}">{r["ret_5d"]:+.1f}%</td></tr>'

    # 板块热力
    sector_html = '<div class="card"><h3>🔥 板块热力</h3><table>'
    sector_html += '<tr><th>板块</th><th>总数</th><th>top10%</th><th>top15%</th><th>均排名</th><th>最强3只</th></tr>'
    for ss in sector_stats:
        sector_html += f'<tr><td><b>{ss["name"]}</b></td><td>{ss["count"]}</td><td>{ss["top10"]}</td><td>{ss["top15"]}</td>'
        sector_html += f'<td>top {ss["avg_rank"]:.0%}</td>'
        sector_html += f'<td>{" ".join(f"{c} {n}" for c,n,_ in ss["top3"])}</td></tr>'
    sector_html += '</table></div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SS-ICIR {today}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.6}}
.container{{max-width:1000px;margin:0 auto;padding:16px}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:20px 24px 16px;border-radius:12px;margin-bottom:16px}}
.header h1{{font-size:18px;margin-bottom:4px;letter-spacing:-0.5px}}
.header h1 span{{color:#e94560}}
.header p{{font-size:12px;color:#94a3b8}}
.header .meta{{display:flex;gap:16px;margin-top:8px;font-size:11px;color:#64748b}}
.signal{{border-radius:10px;padding:14px 18px;margin-bottom:10px}}
.signal h3{{font-size:14px;margin-bottom:8px}}
.signal-red{{background:#fff5f5;border:1px solid #fecaca}}
.signal-green{{background:#f0fdf4;border:1px solid #bbf7d0}}
.signal-blue{{background:#eff6ff;border:1px solid #bfdbfe}}
.signal-yellow{{background:#fffbeb;border:1px solid #fde68a}}
.sig-items{{display:flex;flex-wrap:wrap;gap:6px 14px}}
.sig-item{{font-size:12px;white-space:nowrap}}
.sig-item .dim{{color:#888;font-size:11px;margin-left:4px}}
.sig-item .rank{{font-weight:600;color:#7c3aed;margin-left:4px}}
.sig-item .pnl{{font-weight:600;margin-left:4px}}
.dashboard{{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}}
.stat-card{{flex:1;min-width:100px;background:#fff;border-radius:8px;padding:14px;text-align:center;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.stat-card .v{{font-size:26px;font-weight:700}}
.stat-card .l{{font-size:11px;color:#888;margin-top:2px}}
.buckets{{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}}
.bucket{{flex:1;min-width:80px;text-align:center;background:#fff;border-radius:8px;padding:10px 6px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.bucket .b-num{{font-size:20px;font-weight:700}}
.bucket .b-label{{font-size:10px;color:#888}}
.bucket .b-bar{{height:4px;background:#eee;border-radius:2px;margin-top:4px;overflow:hidden}}
.bucket .b-bar div{{height:100%;background:#7c3aed;border-radius:2px}}
.card{{background:#fff;border-radius:10px;padding:16px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.card h3{{font-size:14px;margin-bottom:10px;color:#333}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:6px 10px;text-align:center;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
th{{background:#f8f9fb;font-weight:600;color:#666;font-size:11px;position:sticky;top:0;cursor:pointer}}
th:first-child,td:first-child{{text-align:left}}
tr:hover:not(th){{background:#f5f7ff}}
tr.highlight{{background:#fefce8}}
tr.hold-row{{background:#f0fdf4}}
tr.watch-row{{background:#fff7ed}}
.red{{color:#d32f2f}}.green{{color:#2e7d32}}.purple{{color:#7c3aed}}.dim{{color:#999}}
.footer{{text-align:center;padding:12px;font-size:11px;color:#aaa}}
@media(max-width:600px){{.container{{padding:8px}}.header{{padding:14px}}}}
</style>
<script>
var sc=3,sa=false;
function sortTable(col,tableId){{
var tbody=document.getElementById(tableId||'mainTable').tBodies[0];
var rows=Array.from(tbody.rows);
if(col===sc)sa=!sa;else{{sc=col;sa=false}}
rows.sort(function(a,b){{
var ca=(a.cells[col]||{{}}).textContent||'';
var cb=(b.cells[col]||{{}}).textContent||'';
var va=parseFloat(ca.replace(/[^0-9.-]/g,''));
var vb=parseFloat(cb.replace(/[^0-9.-]/g,''));
if(!isNaN(va)&&!isNaN(vb))return sa?va-vb:vb-va;
return sa?ca.localeCompare(cb):cb.localeCompare(ca)
}});
rows.forEach(function(r){{tbody.appendChild(r)}})
}}
</script>
</head>
<body>
<div class="container">

<div class="header">
<h1><span>SS-ICIR</span> 决策日报</h1>
<p><b>{today}</b> &nbsp;|&nbsp; 271只A股 &nbsp;|&nbsp; ICIR 加权 &nbsp;|&nbsp; 截面排名 &nbsp;|&nbsp; 分级止损</p>
<div class="meta">
<span>回测: 209笔/61.2%胜率/+5.9%均</span>
<span>买入: top 15%</span>
<span>止损: top10%=-12%, 10-20%=-8%, 20-30%=-5%</span>
</div>
</div>

{signal_html}

<div class="dashboard">
<div class="stat-card"><div class="v" style="color:#7c3aed">{strong}</div><div class="l">top 10%</div></div>
<div class="stat-card"><div class="v" style="color:#1565c0">{top15}</div><div class="l">top 15% (买入)</div></div>
<div class="stat-card"><div class="v" style="color:#d32f2f">{len(sell_alerts)}</div><div class="l">🔴 卖出信号</div></div>
<div class="stat-card"><div class="v" style="color:#2e7d32">{len(new_buys)}</div><div class="l">🟢 新信号</div></div>
<div class="stat-card"><div class="v">{len(holdings)}</div><div class="l">✅ 持仓中</div></div>
<div class="stat-card"><div class="v" style="color:#f59e0b">{len(watch_list)}</div><div class="l">🟡 关注</div></div>
</div>

{bucket_html}
{change_table}
{pos_table}
{sector_html}

<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<h3 style="margin:0">📋 完整排名</h3>
<span style="font-size:11px;color:#999">点击表头排序 | ●新入池 ↑上升 ↓下滑</span>
</div>
<div style="max-height:500px;overflow:auto">
<table id="mainTable"><thead><tr>
<th onclick="sortTable(0,'mainTable')">代码</th><th>名称</th><th>板块</th>
<th onclick="sortTable(3,'mainTable')">排名</th><th>收盘价</th><th>日涨跌</th><th>5日涨跌</th>
</tr></thead><tbody>{all_rows}</tbody></table>
</div>
</div>

<div class="footer">
自动生成 | SS-ICIR v2.1 | 因子 ICIR 权重标定 | 分级止损 | {datetime.now().strftime('%H:%M')}
</div>
</div>
</body></html>"""
    return html


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("codes", nargs="?", default="uploaded-stock-codes.txt")
    p.add_argument("recipient", nargs="?", default=None)
    p.add_argument("--output", default="output")
    a = p.parse_args()
    cf = a.codes if os.path.isabs(a.codes) else os.path.join(PROJECT_DIR, a.codes)
    od = a.output if os.path.isabs(a.output) else os.path.join(PROJECT_DIR, a.output)
    run_daily(codes_file=cf, output_dir=od, recipient=a.recipient)
