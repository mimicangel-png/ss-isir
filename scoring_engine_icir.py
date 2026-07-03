#!/usr/bin/env python3
"""
SS-ICIR 决策日报 v3.0
======================
ICIR 排名 + SS 评分双列 + 关键指标 + 板块筛选 + 信号追踪

报告设计:
  - ICIR 截面排名作为核心信号
  - SS 技术面/资金面/信息面分项作为辅助决策参考
  - 关键指标列: RSI/量比/MA20偏离/换手率
  - 筛选器: 板块 / 排名区间 / 涨跌方向
  - 信号面板: 🟢新买入 🔴触发卖出 ✅持仓 🟡关注
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
from v11.factor_engine import compute_all_factors, FACTOR_NAMES, calc_ma, calc_rsi, calc_ema
from scoring_engine import get_theme

# ====== ICIR 权重 ======
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

def load_json(path):
    if os.path.exists(path):
        with open(path) as f: return json.load(f)
    return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: json.dump(data, f, ensure_ascii=False)


def compute_ss_score(klines, idx):
    """简化版 SS 评分（技术面+资金面）"""
    if idx < 60: return {"tech": 50, "capital": 50, "info": 50, "total": 50}

    w = klines[:idx+1]
    c = [k['close'] for k in w]
    v = [k['volume'] for k in w]
    h = [k['high'] for k in w]
    l = [k['low'] for k in w]

    tech = 50; capital = 50

    ma5, ma10, ma20 = calc_ma(c, 5), calc_ma(c, 10), calc_ma(c, 20)
    rsi = calc_rsi(c)
    dif, dea = calc_ema(c, 12), calc_ema(c, 26)

    # 技术面
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20: tech += 15
        elif ma5 < ma10 < ma20: tech -= 10
    if dif and dea and dif > dea and dif > 0: tech += 5
    if 40 <= rsi <= 55: tech -= 3
    if rsi > 80: tech += 12
    elif rsi > 75: tech += 10

    # 资金面
    if len(c) >= 20:
        mf_mult = [(c[i] - l[i] - (h[i] - c[i])) / (h[i] - l[i]) if h[i] != l[i] else 0.0
                   for i in range(-20, 0)]
        mf_vol = [m * v[i] for m, i in zip(mf_mult, range(-20, 0))]
        cmf = sum(mf_vol) / sum(v[-20:]) if sum(v[-20:]) > 0 else 0
        if cmf > 0.1: capital += 8
        elif cmf > 0: capital += 3
        elif cmf < -0.1: capital -= 8

    tech = max(5, min(95, tech))
    capital = max(5, min(95, capital))
    total = tech * 0.35 + capital * 0.55 + 50 * 0.10

    return {"tech": tech, "capital": capital, "info": 50, "total": round(total)}


def extract_indicators(klines, idx, extra=None):
    """提取关键指标"""
    w = klines[:idx+1]
    c = [k['close'] for k in w]
    v = [k['volume'] for k in w]
    h = [k['high'] for k in w]
    l = [k['low'] for k in w]

    rsi = calc_rsi(c) if len(c) >= 14 else 50
    ma5, ma10, ma20 = calc_ma(c, 5), calc_ma(c, 10), calc_ma(c, 20)

    vol_ratio = v[-1] / (sum(v[-6:-1]) / 5) if len(v) >= 6 and sum(v[-6:-1]) > 0 else 1.0
    dev_ma20 = (c[-1] / ma20 - 1) * 100 if ma20 else 0
    turnover = extra.get("turnover", 0) if extra else 0
    ret_5d = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0
    pe = extra.get("pe_ttm", 0) if extra else 0

    return {
        "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2),
        "dev_ma20": round(dev_ma20, 1), "turnover": round(turnover, 2),
        "ret_5d": round(ret_5d, 1), "pe": round(pe, 1),
    }


# ========== 邮件 ==========
def send_email(html_path, today, recipient="914110627@qq.com"):
    if not os.environ.get("SMTP_USER"): return False
    import smtplib; from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase; from email import encoders
    try:
        with open(html_path) as f: html = f.read()
    except: return False
    msg = MIMEMultipart()
    msg["Subject"] = f"📊 SS-ICIR 决策日报 {today}"
    msg["From"] = os.environ["SMTP_USER"]; msg["To"] = recipient
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(f"SS-ICIR 决策日报 {today}", "plain", "utf-8"))
    body.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(body)
    attach = MIMEBase("application", "octet-stream")
    attach.set_payload(html.encode("utf-8")); encoders.encode_base64(attach)
    attach.add_header("Content-Disposition", f'attachment; filename="SS-ICIR_{today}.html"')
    msg.attach(attach)
    try:
        s = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT","587")), timeout=30)
        s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.sendmail(os.environ["SMTP_USER"], recipient, msg.as_string()); s.quit()
        return True
    except Exception as e: print(f"  ❌ 邮件: {e}"); return False


# ========== HTML 报告 ==========
def build_html(today, results, sorted_keys, sectors, new_buys, sell_alerts, holdings, watch_list,
               rank_risers, rank_fallers, sector_stats, rank_change):
    """构建完整 HTML 报告"""

    n = len(results)
    top10 = len([k for k in sorted_keys[:max(1,int(n*0.1))]])
    top15 = len([k for k in sorted_keys[:max(1,int(n*0.15))]])

    # ====== 信号面板 ======
    signal_html = ""
    panels = [("🔴 触发卖出", "signal-red", sell_alerts, True),
              ("🟢 新买入信号", "signal-green", new_buys, False),
              ("✅ 持仓中", "signal-blue", holdings, False),
              ("🟡 关注列表", "signal-yellow", watch_list, False)]
    for title, cls, items, is_sell in panels:
        if not items: continue
        signal_html += f'<div class="signal {cls}"><span class="sig-title">{title} · {len(items)}</span>'
        signal_html += '<div class="sig-items">'
        for r in items[:8]:
            pnl = ""; pnl_cls = ""
            if is_sell or "entry_price" in r:
                ep = r.get("entry_price", r.get("price", 0))
                pnl = (r["price"] / ep - 1) * 100 if ep > 0 else 0
                pnl_cls = "red" if pnl > 0 else "green"
                pnl_str = f' <span class="{pnl_cls}">{pnl:+.1f}%</span>'
            else:
                pnl_str = ""
            detail = ""
            if is_sell: detail = f' <span class="dim">{r.get("trigger","")}</span>'
            elif "entry_date" in r: detail = f' <span class="dim">@{r["price"]:.2f}</span>'
            else: detail = f' <span class="dim">{r["sector"]}</span> <span class="{"red" if r["change_pct"]>0 else "green"}">{r["change_pct"]:+.1f}%</span>'
            signal_html += f'<span class="sig-item"><b>{r["code"]} {r["name"]}</b>{detail}{pnl_str}</span>'
        if len(items) > 8:
            signal_html += f'<span class="sig-item dim">+{len(items)-8} 更多</span>'
        signal_html += '</div></div>'

    # ====== 仪表盘 ======
    dashboard = f"""
    <div class="dash">
    <div class="dcard"><div class="dv" style="color:#7c3aed">{top10}</div><div class="dl">top 10%</div></div>
    <div class="dcard"><div class="dv" style="color:#1565c0">{top15}</div><div class="dl">top 15%</div></div>
    <div class="dcard"><div class="dv" style="color:#d32f2f">{len(sell_alerts)}</div><div class="dl">卖出信号</div></div>
    <div class="dcard"><div class="dv" style="color:#2e7d32">{len(new_buys)}</div><div class="dl">新信号</div></div>
    <div class="dcard"><div class="dv">{len(holdings)}</div><div class="dl">持仓</div></div>
    <div class="dcard"><div class="dv" style="color:#f59e0b">{len(watch_list)}</div><div class="dl">关注</div></div>
    </div>"""

    # ====== 筛选器 ======
    all_sectors = sorted(sectors.keys())
    sector_options = '<option value="all">全部板块</option>' + ''.join(
        f'<option value="{s}">{s} ({len(sectors[s])})</option>' for s in all_sectors)

    filters = f"""
    <div class="filters">
    <select id="sectorFilter" onchange="applyFilters()">{sector_options}</select>
    <select id="rankFilter" onchange="applyFilters()">
        <option value="all">全部排名</option><option value="top10">top 10%</option>
        <option value="top15">top 15%</option><option value="top30">top 30%</option>
        <option value="top50">top 50%</option></select>
    <select id="changeFilter" onchange="applyFilters()">
        <option value="all">全部涨跌</option><option value="up">上涨</option><option value="down">下跌</option></select>
    <input type="text" id="searchBox" placeholder="搜索代码/名称..." oninput="applyFilters()" style="padding:4px 8px;border:1px solid #ddd;border-radius:6px;font-size:12px;width:140px">
    <span class="filter-note">{n}只结果显示</span>
    </div>"""

    # ====== 数据表行 ======
    table_rows = ""
    for code in sorted_keys:
        r = results[code]
        ind = r.get("indicators", {})
        ss = r.get("ss_score", {})
        arrow = rank_change.get(code, "●")
        ac = {"↑↑": "#d32f2f", "↑": "#e53935", "→": "#999", "↓": "#388e3c", "↓↓": "#2e7d32", "●": "#1565c0"}.get(arrow, "#999")
        chg_cls = "red" if r["change_pct"] > 0 else "green"
        ret5_cls = "red" if ind.get("ret_5d", 0) > 0 else "green"
        hl = ' class="hl"' if r["rank_pct"] < 0.15 else ""

        # RSI 颜色: 过热红色, 正常灰, 超卖绿
        rsi_v = ind.get("rsi", 50)
        rsi_c = "#d32f2f" if rsi_v > 80 else ("#e65100" if rsi_v > 70 else ("#2e7d32" if rsi_v < 30 else "#666"))

        table_rows += f'<tr{hl} data-sector="{r["sector"]}" data-rank="{r["rank_pct"]:.3f}" data-change="{1 if r["change_pct"]>0 else 0}">'
        table_rows += f'<td>{r["code"]}</td><td>{r["name"]}</td><td class="sector-cell">{r["sector"]}</td>'
        table_rows += f'<td class="rank-cell"><b>top {r["rank_pct"]:.1%}</b> <span style="font-size:10px;color:{ac}">{arrow}</span></td>'
        table_rows += f'<td>{ss.get("total", "-")}</td>'
        table_rows += f'<td>{r["price"]:.2f}</td>'
        table_rows += f'<td class="{chg_cls}">{r["change_pct"]:+.2f}%</td>'
        table_rows += f'<td><span style="color:{rsi_c};font-weight:600">{rsi_v:.0f}</span></td>'
        table_rows += f'<td>{ind.get("vol_ratio", "-")}</td>'
        table_rows += f'<td class="{ret5_cls}">{ind.get("ret_5d", 0):+.1f}%</td>'
        table_rows += f'<td>{ind.get("dev_ma20", 0):+.1f}%</td>'
        table_rows += f'<td>{ind.get("pe", "-")}</td>'
        table_rows += f'</tr>\n'

    # ====== 板块热力 ======
    sector_html = '<div class="card"><h3>板块热力</h3><div class="sector-grid">'
    for ss in sector_stats:
        top3_str = " ".join(f'{c} {n}' for c, n, _ in ss["top3"])
        sector_html += f'<div class="sector-card"><div class="sector-name">{ss["name"]}</div>'
        sector_html += f'<div class="sector-num">{ss["count"]}只</div>'
        sector_html += f'<div>top10%: <b>{ss["top10"]}</b> | top15%: <b>{ss["top15"]}</b></div>'
        sector_html += f'<div class="sector-top3">{top3_str}</div></div>'
    sector_html += '</div></div>'

    # ====== 排名异动 ======
    change_html = ""
    if rank_risers or rank_fallers:
        change_html = '<div class="card"><h3>排名异动</h3>'
        if rank_risers:
            change_html += '<div style="margin-bottom:6px"><b style="color:#d32f2f">↑ 上升</b> '
            change_html += ' '.join(f'<span style="font-size:11px;margin-right:4px">{r["code"]}</span>' for r in rank_risers[:12])
            change_html += '</div>'
        if rank_fallers:
            change_html += '<div><b style="color:#2e7d32">↓ 下滑</b> '
            change_html += ' '.join(f'<span style="font-size:11px;margin-right:4px">{r["code"]}</span>' for r in rank_fallers[:12])
            change_html += '</div>'
        change_html += '</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SS-ICIR {today}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;background:#eef0f5;color:#1a1a2e}}
.container{{max-width:1200px;margin:0 auto}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 24px}}
.header h1{{font-size:18px;margin-bottom:4px}} .header h1 span{{color:#e94560}}
.header p{{font-size:12px;color:#94a3b8;margin-bottom:6px}}
.header .meta{{display:flex;gap:14px;font-size:11px;color:#64748b;flex-wrap:wrap}}
.body-pad{{padding:12px 16px}}
.signal{{margin-bottom:6px;padding:10px 14px;border-radius:8px}}
.signal-red{{background:#fff5f5;border:1px solid #fecaca}}
.signal-green{{background:#f0fdf4;border:1px solid #bbf7d0}}
.signal-blue{{background:#eff6ff;border:1px solid #bfdbfe}}
.signal-yellow{{background:#fffbeb;border:1px solid #fde68a}}
.sig-title{{font-size:13px;font-weight:700;display:block;margin-bottom:6px}}
.sig-items{{display:flex;flex-wrap:wrap;gap:4px 12px}}
.sig-item{{font-size:11px;white-space:nowrap}}
.sig-item .dim{{color:#888;font-size:10px;margin-left:3px}}
.dash{{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}}
.dcard{{flex:1;min-width:80px;background:#fff;border-radius:8px;padding:12px;text-align:center;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.dcard .dv{{font-size:22px;font-weight:700}}
.dcard .dl{{font-size:10px;color:#888;margin-top:2px}}
.filters{{display:flex;gap:6px;align-items:center;padding:8px 0;flex-wrap:wrap}}
.filters select,.filters input{{padding:5px 10px;border:1px solid #d0d0d0;border-radius:6px;font-size:12px;background:#fff}}
.filter-note{{font-size:11px;color:#999;margin-left:auto}}
.card{{background:#fff;border-radius:10px;padding:14px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.card h3{{font-size:13px;margin-bottom:8px;color:#333}}
.sector-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}}
.sector-card{{padding:8px 10px;background:#f8f9fa;border-radius:6px;font-size:11px}}
.sector-name{{font-weight:700;font-size:12px;margin-bottom:2px}}
.sector-num{{color:#7c3aed;font-size:16px;font-weight:700}}
.sector-top3{{color:#888;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th,td{{padding:5px 8px;text-align:center;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
th{{background:#f5f6f8;font-weight:600;color:#666;font-size:10px;position:sticky;top:0;z-index:1;cursor:pointer}}
th:hover{{background:#e8eaed}}
td:first-child,th:first-child{{text-align:left;font-weight:600}}
.sector-cell{{font-size:10px;color:#888;max-width:60px;overflow:hidden;text-overflow:ellipsis}}
tr.hl{{background:#fefce8}}
tr:hover:not(.hl){{background:#f5f7ff}}
tr.hl:hover{{background:#fef3c7}}
.red{{color:#d32f2f}}.green{{color:#2e7d32}}.purple{{color:#7c3aed}}.dim{{color:#999}}
.footer{{text-align:center;padding:10px;font-size:10px;color:#aaa}}
.rank-cell{{min-width:70px}}
</style>
<script>
function applyFilters(){{
var sector=document.getElementById('sectorFilter').value;
var rank=document.getElementById('rankFilter').value;
var change=document.getElementById('changeFilter').value;
var search=document.getElementById('searchBox').value.toLowerCase();
var rows=document.querySelectorAll('tbody tr');
var visible=0;
rows.forEach(function(r){{
var s=r.getAttribute('data-sector');
var rk=parseFloat(r.getAttribute('data-rank')||1);
var ch=r.getAttribute('data-change');
var txt=r.textContent.toLowerCase();
var show=true;
if(sector!=='all'&&s!==sector)show=false;
if(rank==='top10'&&rk>=0.1)show=false;
if(rank==='top15'&&rk>=0.15)show=false;
if(rank==='top30'&&rk>=0.3)show=false;
if(rank==='top50'&&rk>=0.5)show=false;
if(change==='up'&&ch!=='1')show=false;
if(change==='down'&&ch!=='0')show=false;
if(search&&!txt.includes(search))show=false;
r.style.display=show?'':'none';
if(show)visible++;
}});
document.getElementById('filterCount').textContent=visible+'/'+rows.length+' 只';
}}
var sc=3,sa=false;
function sortTable(col){{
var tbody=document.querySelector('tbody');
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
<p><b>{today}</b> &nbsp;|&nbsp; {n}只A股 &nbsp;|&nbsp; ICIR截面排名 + SS辅助评分 &nbsp;|&nbsp; 分级止损</p>
<div class="meta">
<span>回测: 209笔/61.2%胜率/+5.9%均</span><span>买入: top 15%</span>
<span>止损: top10%=-12%, 10-20%=-8%, 20-30%=-5%</span>
</div>
</div>
<div class="body-pad">
{signal_html}
{dashboard}
{filters}
<div class="card" style="padding:8px 0 0 0">
<div style="max-height:60vh;overflow:auto">
<table><thead><tr>
<th onclick="sortTable(0)">代码</th><th>名称</th><th>板块</th>
<th onclick="sortTable(3)">ICIR排名</th>
<th onclick="sortTable(4)">SS分</th>
<th>收盘价</th><th>日涨跌</th>
<th>RSI</th><th>量比</th><th>5日涨跌</th><th>MA20偏离</th><th>PE</th>
</tr></thead><tbody>{table_rows}</tbody></table>
</div></div>
{change_html}
{sector_html}
<div class="card" style="font-size:11px;color:#666;line-height:1.6">
<b>使用说明</b><br>
· <b>ICIR排名</b>: 33因子有效性加权后的截面排名，top 15% 触发买入信号<br>
· <b>SS分</b>: 传统技术面+资金面评分(基分50)，辅助判断动量强弱<br>
· <b>RSI</b>: <span style="color:#d32f2f">&gt;80过热</span> · <span style="color:#2e7d32">&lt;30超卖</span> · 40-55弱势<br>
· <b>量比</b>: &gt;1.5放量 · &lt;0.5缩量<br>
· <b>MA20偏离</b>: &gt;+10%远离均线 · &lt;-10%超跌<br>
· 筛选器支持板块/排名/涨跌/搜索组合筛选
</div>
<div class="footer">SS-ICIR v3.0 | ICIR权重标定 | 自动生成 {datetime.now().strftime('%H:%M')}</div>
</div></div></body></html>"""
    return html


# ========== 主流程 ==========
def run_daily(codes_file=None, output_dir=None, recipient=None):
    today = datetime.now().strftime("%Y-%m-%d")
    if codes_file is None: codes_file = os.path.join(PROJECT_DIR, "uploaded-stock-codes.txt")
    if output_dir is None: output_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    print(f"╔══════════════════════════════════╗")
    print(f"║  SS-ICIR v3.0 决策日报 [{today}]  ║")
    print(f"╚══════════════════════════════════╝")

    with open(codes_file) as f: codes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    klines = _db.get_klines(codes, days=130)
    extra = _db.get_extra_info(codes)
    for c in extra: extra[c]["_sector"] = get_theme(c)

    # ====== ICIR + SS + 指标 ======
    results = {}
    raw = {}
    for code in codes:
        kl = klines.get(code)
        if not kl or len(kl) < 60: continue
        ex = extra.get(code, {})
        fv = compute_all_factors({code: kl}, {code: ex}, today_str=today).get(code, {})
        if not fv: continue

        icir_score = sum(ICIR.get(fn, 0.01) * fv.get(fn, 0) for fn in FACTOR_NAMES)
        ss = compute_ss_score(kl, len(kl)-1)
        ind = extract_indicators(kl, len(kl)-1, ex)

        results[code] = {
            "code": code, "name": ex.get("name", ""), "price": ex.get("price", 0),
            "change_pct": ex.get("change_pct", 0), "sector": ex.get("_sector", get_theme(code)),
            "icir_raw": icir_score, "ss_score": ss, "indicators": ind,
        }
        raw[code] = icir_score

    sorted_keys = sorted(raw.keys(), key=lambda k: -raw[k])
    n = len(sorted_keys)
    for rank, code in enumerate(sorted_keys):
        results[code]["rank_pct"] = rank / n
        results[code]["rank"] = rank + 1

    # ====== 排名变化 ======
    prev_ranks = load_json(RANK_FILE)
    rank_change = {}
    for code, r in results.items():
        prev = prev_ranks.get(code, {})
        prev_rp = prev.get("rank_pct")
        if prev_rp is not None:
            delta = prev_rp - r["rank_pct"]
            if abs(delta) < 0.01: rank_change[code] = "→"
            elif delta > 0.05: rank_change[code] = "↑↑"
            elif delta > 0.02: rank_change[code] = "↑"
            elif delta < -0.05: rank_change[code] = "↓↓"
            elif delta < -0.02: rank_change[code] = "↓"
            else: rank_change[code] = "→"
        else: rank_change[code] = "●"
    save_json(RANK_FILE, {c: {"rank_pct": results[c]["rank_pct"], "date": today} for c in results})

    # ====== 板块 ======
    sectors = defaultdict(list)
    for r in results.values(): sectors[r["sector"]].append(r)
    sector_stats = []
    for sec, stocks in sorted(sectors.items(), key=lambda x: -len([s for s in x[1] if s["rank_pct"] < 0.15])):
        top10 = len([s for s in stocks if s["rank_pct"] < 0.10])
        top15 = len([s for s in stocks if s["rank_pct"] < 0.15])
        top3 = sorted(stocks, key=lambda s: s["rank_pct"])[:3]
        sector_stats.append({"name": sec, "count": len(stocks), "top10": top10, "top15": top15,
                             "top3": [(s["code"], s["name"], s["rank_pct"]) for s in top3]})

    # ====== 信号追踪 ======
    signal_history = load_json(HISTORY_FILE)
    new_buys, sell_alerts, holdings, watch_list = [], [], [], []
    rank_risers, rank_fallers = [], []
    top15 = [c for c in sorted_keys[:max(1, int(n*0.15))]]

    for code, r in results.items():
        if code not in signal_history: signal_history[code] = {"buy_dates": [], "alerts": []}
        hist = signal_history[code]
        has_pos = len(hist["buy_dates"]) > 0
        last_buy = hist["buy_dates"][-1] if has_pos else None

        if r["rank_pct"] < 0.15:
            if not has_pos:
                hist["buy_dates"].append({"date": today, "price": r["price"], "rank_pct": r["rank_pct"]})
                new_buys.append(r)
            else:
                ep = last_buy["price"]; pnl = (r["price"]/ep-1)*100 if ep > 0 else 0
                days = (datetime.strptime(today,"%Y-%m-%d")-datetime.strptime(last_buy["date"],"%Y-%m-%d")).days
                holdings.append({**r, "entry_date": last_buy["date"], "entry_price": ep, "pnl": round(pnl,1), "days_held": days})
        elif has_pos and last_buy:
            ep = last_buy["price"]; pnl = (r["price"]/ep-1)*100 if ep > 0 else 0
            days = (datetime.strptime(today,"%Y-%m-%d")-datetime.strptime(last_buy["date"],"%Y-%m-%d")).days
            entry_rank = last_buy.get("rank_pct", 0.15)
            stop = -12 if entry_rank < 0.10 else (-8 if entry_rank < 0.20 else -5)
            tracking = {**r, "entry_date": last_buy["date"], "entry_price": ep, "pnl": round(pnl,1), "days_held": days}
            if r["rank_pct"] > 0.5 or pnl <= stop:
                trigger = "排名崩溃" if r["rank_pct"]>0.5 else f"止损({pnl:+.1f}%≤{stop}%)"
                hist["alerts"].append({"date":today,"reason":"sell","trigger":trigger})
                sell_alerts.append({**tracking, "trigger": trigger})
            else:
                watch_list.append(tracking)

        # 排名异动
        if r["rank_pct"] >= 0.15 and rank_change.get(code) in ("↑↑","↑"): rank_risers.append(r)
        if r["rank_pct"] < 0.15 and rank_change.get(code) in ("↓↓","↓"): rank_fallers.append(r)

    for code in signal_history:
        signal_history[code]["buy_dates"] = signal_history[code]["buy_dates"][-3:]
        signal_history[code]["alerts"] = signal_history[code]["alerts"][-20:]
    save_json(HISTORY_FILE, signal_history)

    # ====== HTML ======
    html = build_html(today, results, sorted_keys, sectors, new_buys, sell_alerts,
                      holdings, watch_list, rank_risers, rank_fallers, sector_stats, rank_change)

    html_path = os.path.join(output_dir, f"SS-ICIR_{today}.html")
    with open(html_path, "w") as f: f.write(html)

    json_path = os.path.join(output_dir, f"SS-ICIR_{today}.json")
    with open(json_path, "w") as f:
        json.dump({"date": today, "model": "SS-ICIR v3.0", "total": n,
                   "new_buys": len(new_buys), "sell_alerts": len(sell_alerts),
                   "results": [results[c] for c in sorted_keys]}, f, ensure_ascii=False, indent=2)

    if os.environ.get("SMTP_USER"): send_email(html_path, today, recipient)

    print(f"  📊 {n}只 | 🟢{len(new_buys)} 🔴{len(sell_alerts)} ✅{len(holdings)} 🟡{len(watch_list)}")
    return results


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
