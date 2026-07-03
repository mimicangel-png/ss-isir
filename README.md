# SS-ICIR 股票评分系统

基于因子有效性（ICIR）加权的 A 股量化评分引擎，截面排名驱动决策，每日自动生成报告并邮件推送。

## 模型

**SS-ICIR** = 因子 ICIR 加权 + 截面排名

- **33 个连续因子**：技术面 14 + 资金面 7 + 估值面 3 + 行业面 2 + 信息面 2 + 风险面 2 + 基本面 3
- **ICIR 权重**：基于 Walk-Forward 回测标定的因子有效性权重，自动淘汰弱势因子
- **截面排名**：每天在全股票池内排序，top 15% = 买入信号
- **分级止损**：top10%=-12%, 10-20%=-8%, 20-30%=-5%

## 回测验证

| 方案 | 交易数 | 胜率 | 均收益 | 止损率 |
|------|:---:|:---:|:---:|:---:|
| ICIR加权 | 209 | **61.2%** | **+5.9%** | 22% |
| GLM集成 | 323 | 53.6% | +3.6% | 40% |
| 等权组合 | 307 | 50.5% | +4.0% | 40% |

> Walk-Forward: 2窗口 (train=200天, test=50天), 271只A股, 分级止损

## 因子权重 (ICIR 标定)

| 因子 | ICIR | 权重 |
|------|:---:|:---:|
| turnover_z (换手率异常) | 0.451 | 🥇 最强 |
| log_mcap (市值规模) | 0.162 | 🥈 |
| mfi (资金流) | 0.153 | 🥉 |
| pct_52w (52周位置) | 0.091 | |
| pe_percentile (PE分位) | 0.078 | |
| ... | | |

## 快速开始

```bash
# 1. 环境
pip install numpy

# 2. 配置邮箱
cp .env.example .env
# 编辑 .env 填入 QQ邮箱 SMTP 信息

# 3. 运行评分
python scoring_engine_icir.py

# 4. 报告输出
# output/SS-ICIR_评分_YYYY-MM-DD.html
```

## 项目结构

```
ss-icir/
├── scoring_engine_icir.py    # SS-ICIR 每日评分引擎 + HTML报告 + 邮件
├── uploaded-stock-codes.txt  # 股票池 (~271只)
├── .env.example              # 邮箱配置模板
├── output/                   # 报告输出
├── v11/                      # V11 回测框架
│   ├── factor_engine.py      # 连续因子引擎 + IC追踪 + 因子墓地
│   ├── trade_sim.py          # 组合级交易模拟器 (分级止损)
│   ├── glm_model.py          # 多周期 GLM 模型
│   ├── wfo_runner.py         # Purge Gap Walk-Forward 回测
│   ├── scheme_comparison.py  # 多方案对比
│   ├── entry_exit_compare.py # 买卖点优化对比
│   ├── analyzer.py           # 分数标定 + 分析
│   ├── report_generator.py   # 回测HTML报告生成
│   ├── data_builder.py       # PoT 数据构建器
│   ├── finance_db.py         # 财报数据缓存
│   └── run.py                # V11 完整回测入口
└── output/
    ├── scheme_comparison.json # 方案对比结果
    └── quick_backtest.json   # 快速回测结果
```

## 定时运行

```bash
# 每个交易日 15:30
python scoring_engine_icir.py uploaded-stock-codes.txt your_email@qq.com
```

## License

MIT
