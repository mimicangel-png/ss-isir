#!/usr/bin/env python3
"""
SS-ICIR 每日评分引擎
======================
基于 V11 回测验证的最优方案：
  - ICIR 加权评分（因子有效性驱动）
  - 截面排名（不是绝对分数）
  - 分级止损规则（top10%=-12%, 10-20%=-8%, 20-30%=-5%）

每天 15:30 运行，生成 HTML 报告并邮件推送。
"""

import sys, os, json, math, urllib.request
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

# ========== 环境变量 ==========
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

# ========== 数据库 ==========
from stock_db import StockDB
_db = StockDB()

# ========== V11 因子引擎 ==========
from v11.factor_engine import (
    compute_all_factors, FACTOR_NAMES, FACTOR_REGISTRY,
)

# ========== ICIR 权重（V11 回测标定）==========
ICIR_WEIGHTS_RAW = {
    "turnover_z": 0.451, "log_mcap": 0.162, "mfi": 0.153,
    "pct_52w": 0.091, "pe_percentile": 0.078, "pb_percentile": 0.078,
    "gap_open": 0.072, "max_dd_20d": 0.052, "ma_trend": 0.030,
    "rsi_signal": 0.028, "macd_signal": 0.026, "cmf": 0.025,
    "vwap_premium": 0.022, "event_score": 0.020, "event_count": 0.018,
    "sector_rsi": 0.015, "sector_momentum": 0.012, "inflow_rate": 0.010,
    "main_flow_5d": 0.008, "main_flow_20d": 0.006, "amplitude_z": 0.005,
    "ret_20d": 0.004, "volatility_20d": 0.003, "ma_bull": 0.029,
    "vol_price": 0.025, "dev_ma20": 0.024, "vol_ratio_5d": 0.023,
    "ret_5d": 0.021, "streak": 0.020,
    "roe_rank": 0.001, "gross_margin_rank": 0.001, "ocf_ratio_rank": 0.001,
}
_total = sum(ICIR_WEIGHTS_RAW.values())
ICIR_WEIGHTS = {k: v/_total for k, v in ICIR_WEIGHTS_RAW.items()}

# ========== 板块分类 ==========
from scoring_engine import get_theme

# ========== 核心评分 ==========
def score_icir(factor_values):
    """ICIR 加权评分"""
    return sum(ICIR_WEIGHTS.get(fn, 0.01) * factor_values.get(fn, 0)
               for fn in FACTOR_NAMES)


def get_suggestion(rank_pct):
    """截面排名 → 建议"""
    if rank_pct < 0.10:  return "🔥强烈买入"
    elif rank_pct < 0.20: return "🟢逢低买入"
    elif rank_pct < 0.40: return "🟡持有"
    elif rank_pct < 0.60: return "⚪观望"
    else: return "🔴回避"

def get_sug_action(rank_pct):
    if rank_pct < 0.10:  return "strong_buy"
    elif rank_pct < 0.20: return "buy"
    elif rank_pct < 0.40: return "hold"
    elif rank_pct < 0.60: return "watch"
    else: return "avoid"

def get_stop_level(rank_pct):
    if rank_pct < 0.10: return -12
    elif rank_pct < 0.20: return -8
    else: return -5


# ========== 邮件发送（复用原版）==========
def send_email(html_path, today, recipient):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders

    if recipient is None:
        recipient = "914110627@qq.com"

    smtp_config = {
        "host": os.environ.get("SMTP_HOST", "smtp.qq.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
    }

    if not smtp_config["user"] or not smtp_config["password"]:
        print("  ⚠️ 未配置邮箱，跳过发送")
        return False

    try:
        with open(html_path) as f: html_content = f.read()
    except Exception as e:
        print(f"  [ERROR] 读取报告失败: {e}")
        return False

    msg = MIMEMultipart()
    msg["Subject"] = f"📊 SS-ICIR 股票评分日报 {today}"
    msg["From"] = smtp_config["user"]
    msg["To"] = recipient

    # 邮件正文
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(f"SS-ICIR 股票评分日报 {today}\n\n详情见附件 HTML 报告。", "plain", "utf-8"))
    body.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(body)

    # 附件
    attach = MIMEBase("application", "octet-stream")
    attach.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(attach)
    attach.add_header("Content-Disposition",
                      f'attachment; filename="SS-ICIR_{today}.html"')
    msg.attach(attach)

    try:
        server = smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=30)
        server.starttls()
        server.login(smtp_config["user"], smtp_config["password"])
        server.sendmail(smtp_config["user"], recipient, msg.as_string())
        server.quit()
        print(f"  ✅ 邮件已发送至 {recipient}")
        return True
    except Exception as e:
        print(f"  ❌ 邮件发送失败: {e}")
        return False


# ========== HTML 报告 ==========
def generate_html(results, today, output_dir):
    """生成互动版 HTML 报告"""

    # 统计
    strong_buy = [r for r in results if r["sug_action"] == "strong_buy"]
    buy_list = [r for r in results if r["sug_action"] == "buy"]
    hold_list = [r for r in results if r["sug_action"] == "hold"]
    watch_list = [r for r in results if r["sug_action"] == "watch"]
    avoid_list = [r for r in results if r["sug_action"] == "avoid"]

    sectors = {}
    for r in results:
        s = r.get("sector", "其他")
        sectors[s] = sectors.get(s, 0) + 1
    top_sectors = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:8]

    sector_groups = {}
    for r in results:
        s = r.get("sector", "其他")
        if s not in sector_groups: sector_groups[s] = []
        sector_groups[s].append(r)

    n = len(results)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>SS-ICIR 股票评分日报 {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#f8f9fb;color:#1a1a2e;padding:16px}}
h1{{font-size:20px;margin-bottom:4px}}h2{{font-size:15px;margin:20px 0 10px}}
.subtitle{{font-size:12px;color:#888;margin-bottom:16px}}
.card{{background:#fff;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.stat{{text-align:center;padding:12px;background:#f0f2f5;border-radius:8px}}
.stat .v{{font-size:24px;font-weight:700}}.stat .l{{font-size:11px;color:#888}}
.red{{color:#d32f2f}}.green{{color:#2e7d32}}.purple{{color:#7c3aed}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:6px 10px;text-align:center;border-bottom:1px solid #eee;white-space:nowrap}}
th{{background:#f5f6f8;font-weight:600;color:#555;cursor:pointer;position:sticky;top:0;z-index:1}}
tr:hover{{background:#f0f4ff}}
td:first-child,th:first-child{{text-align:left}}
.tag{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600}}
.tag-buy{{background:#ffebee;color:#c62828}}.tag-green{{background:#e8f5e9;color:#2e7d32}}
.tag-yellow{{background:#fff8e1;color:#f57f17}}.tag-red{{background:#ffebee;color:#c62828}}
.tab{{display:inline-block;padding:6px 14px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;margin-right:2px;background:#eee;color:#666}}
.tab.active{{background:#fff;color:#1a1a2e;border:1px solid #e0e0e0;border-bottom:1px solid #fff}}
.tab-content{{display:none}}.tab-content.active{{display:block}}
.detail-row{{display:none;background:#fafbff}}.detail-row.show{{display:block}}
.clickable{{cursor:pointer}}
</style></head>
<body>
<h1>📊 SS-ICIR 股票评分日报</h1>
<div class="subtitle"><strong>{today}</strong> &nbsp;|&nbsp; SS-ICIR V1 &nbsp;|&nbsp; {n}只A股 &nbsp;|&nbsp; 板块覆盖: {len(sectors)}个</div>

<div class="card">
<p style="font-size:13px;margin-bottom:8px">
<span class="tag tag-purple" style="background:#ede9fe;color:#7c3aed;padding:2px 8px">SS-ICIR</span>
<b>因子有效性加权评分</b>：基于 V11 回测标定的因子 ICIR 权重，截面排名驱动决策。<br>
回测验证：209笔交易 | 胜率 61.2% | 均收益 +5.9% | 分级止损 (top10%=-12%, 10-20%=-8%, 20-30%=-5%)</p>
</div>

<div class="grid">
<div class="stat"><div class="v red">{len(strong_buy)}</div><div class="l">🔥强烈买入(top10%)</div></div>
<div class="stat"><div class="v" style="color:#e65100">{len(buy_list)}</div><div class="l">🟢逢低买入(10-20%)</div></div>
<div class="stat"><div class="v" style="color:#f9a825">{len(hold_list)}</div><div class="l">🟡持有(20-40%)</div></div>
<div class="stat"><div class="v" style="color:#999">{len(watch_list)}</div><div class="l">⚪观望(40-60%)</div></div>
<div class="stat"><div class="v green">{len(avoid_list)}</div><div class="l">🔴回避(60%+)</div></div>
</div>

<div style="font-size:12px;color:#888;margin-top:4px">
主力板块: {', '.join(f'{s}({c})' for s,c in top_sectors)}
</div>
"""

    # ==== TOP 5 ====
    html += '<div class="card"><h2>🏆 强烈买入 TOP 10</h2><table>'
    html += '<tr><th>代码</th><th>名称</th><th>板块</th><th>排名</th><th>收盘价</th><th>日涨跌</th><th>建议</th><th>止损</th></tr>'
    for r in results[:10]:
        stop = get_stop_level(r["rank_pct"])
        html += f'<tr><td>{r["code"]}</td><td>{r["name"]}</td><td>{r.get("sector","")}</td>'
        html += f'<td><b>top {r["rank_pct"]:.0%}</b></td>'
        html += f'<td>{r["price"]:.2f}</td><td class="{"red" if r["change_pct"]>0 else "green"}">{r["change_pct"]:+.2f}%</td>'
        html += f'<td>{r["suggestion"]}</td><td style="color:#888">止损 {stop}%</td></tr>'
    html += '</table></div>'

    # ==== 板块视角 ====
    html += '<div class="card"><h2>📂 板块排名</h2>'
    for sector, stocks in sorted(sector_groups.items(), key=lambda x: -max(r["rank_pct"] for r in x[1])):
        top = sorted(stocks, key=lambda r: r["rank_pct"])[:5]
        html += f'<div style="margin-bottom:10px"><b style="font-size:13px">{sector}</b> ({len(stocks)}只) '
        html += ' '.join(f'<span style="font-size:11px;margin-right:6px">{s["code"]} {s["name"]}</span>' for s in top)
        html += '</div>'
    html += '</div>'

    # ==== 完整表格 ====
    html += '<div class="card"><h2>📋 完整列表</h2>'
    html += '<table id="stockTable"><thead><tr>'
    for h in ["代码","名称","板块","排名","收盘价","日涨跌","建议","止损"]:
        html += f'<th onclick="sortTable({list(["代码","名称","板块","排名","收盘价","日涨跌","建议","止损"]).index(h)})">{h}</th>'
    html += '</tr></thead><tbody>'
    for r in results:
        stop = get_stop_level(r["rank_pct"])
        color = "red" if r["change_pct"] > 0 else "green"
        html += f'<tr><td>{r["code"]}</td><td>{r["name"]}</td><td>{r.get("sector","")}</td>'
        html += f'<td>top {r["rank_pct"]:.0%}</td>'
        html += f'<td>{r["price"]:.2f}</td><td class="{color}">{r["change_pct"]:+.2f}%</td>'
        html += f'<td>{r["suggestion"]}</td><td>止损 {stop}%</td></tr>'
    html += '</tbody></table></div>'

    html += f"""<p style="font-size:11px;color:#aaa;text-align:center;margin-top:16px">
自动生成 | SS-ICIR V1 | ICIR权重标定 | 分级止损</p>
<script>
function sortTable(col) {{
    var table = document.getElementById("stockTable");
    var tbody = table.tBodies[0];
    var rows = Array.from(tbody.rows);
    rows.sort(function(a,b) {{
        var va = a.cells[col].textContent.replace(/[%top￥¥\\s]/g,'');
        var vb = b.cells[col].textContent.replace(/[%top￥¥\\s]/g,'');
        return parseFloat(va) - parseFloat(vb);
    }});
    if(col==3) rows.reverse();
    tbody.innerHTML = '';
    rows.forEach(function(r){{tbody.appendChild(r)}});
}}
</script></body></html>"""

    html_path = os.path.join(output_dir, f"SS-ICIR_评分_{today}.html")
    with open(html_path, "w") as f:
        f.write(html)

    return html_path


# ========== 主流程 ==========
def run_daily(codes_file=None, output_dir=None, recipient=None):
    today = datetime.now().strftime("%Y-%m-%d")

    if codes_file is None:
        codes_file = os.path.join(PROJECT_DIR, "uploaded-stock-codes.txt")
    if output_dir is None:
        output_dir = os.path.join(PROJECT_DIR, "output")

    os.makedirs(output_dir, exist_ok=True)

    print(f"╔══════════════════════════════════════╗")
    print(f"║  SS-ICIR 每日评分 [{today}]      ║")
    print(f"╚══════════════════════════════════════╝")

    # ====== 1. 加载数据 ======
    print("\n[1/3] 加载数据...")
    with open(codes_file) as f:
        codes = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    print(f"  股票池: {len(codes)} 只")

    klines = _db.get_klines(codes, days=130)
    extra = _db.get_extra_info(codes)

    # 补板块信息
    for code in extra:
        extra[code]["_sector"] = get_theme(code)

    print(f"  K线: {len(klines)}只 | 行情: {len(extra)}只")

    # ====== 2. 因子 + 评分 ======
    print("\n[2/3] SS-ICIR 评分...")
    results = []
    raw_scores = {}

    for code in codes:
        kl = klines.get(code)
        if not kl or len(kl) < 60: continue
        ex = extra.get(code, {})

        # 构建当天快照
        snapshot = {code: kl}
        fvals_all = compute_all_factors(snapshot, {code: ex}, today_str=today)
        fvals = fvals_all.get(code, {})
        if not fvals: continue

        score = score_icir(fvals)
        raw_scores[code] = score

    # 截面排名
    if raw_scores:
        sorted_codes = sorted(raw_scores.items(), key=lambda x: -x[1])
        n_total = len(sorted_codes)

        for rank, (code, score) in enumerate(sorted_codes):
            kl = klines.get(code, [])
            ex = extra.get(code, {})
            rank_pct = rank / n_total
            suggestion = get_suggestion(rank_pct)
            sug_action = get_sug_action(rank_pct)

            ret_5d = (kl[-1]['close']/kl[-6]['close']-1)*100 if len(kl)>=6 else 0
            ret_10d = (kl[-1]['close']/kl[-11]['close']-1)*100 if len(kl)>=11 else 0

            results.append({
                "code": code,
                "name": ex.get("name", ""),
                "price": ex.get("price", 0),
                "change_pct": ex.get("change_pct", 0),
                "sector": ex.get("_sector", get_theme(code)),
                "rank_pct": round(rank_pct, 4),
                "rank": rank + 1,
                "raw_score": round(score, 2),
                "ret_5d": round(ret_5d, 1),
                "ret_10d": round(ret_10d, 1),
                "suggestion": suggestion,
                "sug_action": sug_action,
                "stop_level": get_stop_level(rank_pct),
            })

    print(f"  评分完成: {len(results)} 只")

    # ====== 3. 报告 + 邮件 ======
    print("\n[3/3] 生成报告...")
    html_path = generate_html(results, today, output_dir)
    print(f"  HTML: {html_path}")

    # JSON 备份
    json_path = os.path.join(output_dir, f"SS-ICIR_{today}.json")
    with open(json_path, "w") as f:
        json.dump({"date": today, "model": "SS-ICIR V1", "total": len(results), "results": results},
                  f, ensure_ascii=False, indent=2)

    # 发送邮件
    if os.environ.get("SMTP_USER"):
        send_email(html_path, today, recipient)

    # 打印 TOP 5
    print(f"\n{'='*50}")
    for r in results[:5]:
        stop = get_stop_level(r["rank_pct"])
        print(f"  {r['code']} {r['name']}: top {r['rank_pct']:.0%} {r['suggestion']} (止损{stop}%)")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SS-ICIR 每日评分")
    parser.add_argument("codes", nargs="?", default="uploaded-stock-codes.txt", help="股票代码文件")
    parser.add_argument("recipient", nargs="?", default=None, help="收件人邮箱")
    parser.add_argument("--output", default="output", help="输出目录")
    args = parser.parse_args()

    codes_file = args.codes if os.path.isabs(args.codes) else os.path.join(PROJECT_DIR, args.codes)
    output_dir = args.output if os.path.isabs(args.output) else os.path.join(PROJECT_DIR, args.output)

    run_daily(codes_file=codes_file, output_dir=output_dir, recipient=args.recipient)
