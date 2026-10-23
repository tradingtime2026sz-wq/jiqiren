# 机器人（jiqiren）

老虎证券期权小账户的复盘与盘中提醒，代码目录名 `tiger-review`。

| 部分 | 入口 | 说明 |
|---|---|---|
| 收盘复盘 | `python -m tiger_review auto` / `review` / `day` | 同步老虎订单，复盘交易逻辑与模式，见下文 |
| 插针与恐慌研究 | `research/` | 热点票插针、盘中恐慌前半段的回测与报告脚本 |
| 盘中提醒 | `python -m tiger_review alerts` | Telegram 机器人推送恐慌 Put、接针提醒，见 `docs/盘中提醒-v0.md`（WQ 主会话维护） |
| 部署 | `deploy/launchd/` | 两个定时任务的模板 |

个人数据（成交、持仓、账户、备注、报告）和凭据都不在仓库里：凭据在 `~/.config/tiger/`、`~/.config/telegram/`，数据在本机 `data/` 与 `reports/`。

---

## tiger-review：老虎期权账户复盘

复盘的是**交易逻辑和模式**，不是对照规则打分。每笔回答四件事：

1. 方向看对没有（分「持有期间」和「到期为止」两个口径，差得多说明错在时点而不是观点）
2. 盈亏来自哪：方向 / 时间损耗 / 隐波变化和价差 / 费用（按开平仓时刻的标的价格反推隐含波动率拆解，四项相加等于实际盈亏）
3. 在什么盘面进的场：前 5 天走势（按该标的自身波动标准化）、日内位置、隐波相对实际波动贵不贵、Delta、到期天数
4. 过程和离场：最大浮盈浮亏、拿到了多少、离场后 5 个交易日行情又走了多少（卖早了 / 走对了）

再按这些标签分组，找出「在赚的打法」和「在漏的打法」。只有去掉组内最极端一笔后仍然成立、且不超过全部交易一半的分组才算模式。

## 第一次使用：开通老虎 Open API（需要你本人操作）

1. 用 Chrome 打开 https://developer.itigerup.com/profile ，手机号加验证码登录，登记开发者信息。
   开通前提：老虎账户已入金，并签署 API 授权协议。
2. 页面会生成私钥，**只显示一次**，刷新前保存。
3. 导出 `tiger_openapi_config.properties`，放到本机：

```bash
mkdir -p ~/.config/tiger && chmod 700 ~/.config/tiger
```

```bash
mv ~/Downloads/tiger_openapi_config.properties ~/.config/tiger/ && chmod 600 ~/.config/tiger/tiger_openapi_config.properties
```

   牌照是 TBHK 的账户还需要同目录放 `tiger_openapi_token.properties`。
4. 自检（只读，会显示账户尾号和近 30 天成交单数）：

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review doctor
```

## 日常命令

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review sync --since 2026-01-01
```

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review review --from 2026-07-01
```

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review day --date 2026-10-19
```

- `sync`：拉订单（89 天一窗、翻页、按证券类型各拉一遍去重）、持仓、净值历史到 `data/raw/`。增量合并，可反复跑。
- `review`：期间复盘，输出 `reports/期权复盘-起-止.html`（同名 `.json` 是全部明细）。
- `day`：当天有动作的交易单独出一页。
- `demo`：真实标的行情 + 模拟成交的演示报告，用来看页面长什么样。

## 每天收盘后自动跑

本机定时任务 `~/Library/LaunchAgents/com.vock.tiger-review-daily.plist`：周一到周五美东 16:20 运行 `python -m tiger_review auto`，Mac 睡着时醒来补跑。

- 当天有期权成交：同步订单 → 生成今日复盘和策略累计复盘 → 弹系统通知（今天几笔、净盈亏、累计）。
- 最新报告固定在 `reports/最新-今日复盘.html` 和 `reports/最新-策略累计复盘.html`，带日期的版本也会留着。
- 当天没有期权成交：只写日志，不打扰。
- 当天已经手动复盘过（`python -m tiger_review auto`，不带 `--scheduled`）：收盘定时任务直接跳过，连同步都不做。
- 出错（凭据失效、老虎接口报错）弹「老虎复盘失败」；富途 OpenD 没开时照常出报告，但通知里会提示盈亏拆解缺行情。
- 每天同步顺带续期老虎 token（签发满 7 天就换新）。
- 复盘起点是 `data/settings.json` 里的 `strategy_start`（默认 2026-10-16），换策略时改这一项。
- 日志：`data/logs/auto.log`。

```bash
launchctl kickstart -k gui/$(id -u)/com.vock.tiger-review-daily
```

```bash
launchctl bootout gui/$(id -u)/com.vock.tiger-review-daily
```

第一条是立刻手动跑一次，第二条是停用自动任务。

## 插针读数与选票前提

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review spike SPCX
```

```bash
cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review note "SPCX 2026-10-21 09:44" --premise "当天的边际变化" --logic "热点票插针去接"
```

- `spike`：某只票当前（或 `--at 09:44 --date 2026-10-21`）这一分钟的插针读数：回落深度（几倍日均振幅）、相对开盘、开盘后第几分钟、QQQ 同期、打分 0 到 4，以及历史同类针的胜算。
- `note`：记下一笔的选票前提和进场逻辑，复盘卡片会放在最前面。
- 回测与研究：`research/spike_backtest.py`（事件）→ `research/spike_stats.py`（统计）→ `research/spike_lookup.py`（查询表）→ `research/spike_report.py`（报告 `reports/插针量化-*.html`）。数据读 option-ab-lab 的 1 分钟线，只读。

## 数据与隐私

- 只调用老虎的查询接口，没有任何下单、改单、撤单代码。
- 标的行情走本机富途 OpenD（历史 K 线额度按 30 天内不同代码数计，只拉标的不拉期权），全部缓存在 `data/cache/`。
- `data/` 权限 700，原始数据文件 600；报告里账户只显示尾号。本项目不进团队仓库、不推任何远端。

## 口径与局限

- 一笔交易 = 同一标的、同一方向（买 Call/卖 Put 为看涨，买 Put/卖 Call 为看跌）从开仓到全部了结。15 分钟内同向换行权价或到期日算移仓。
- 期权价格路径（最大浮盈浮亏、离场后估值）按开仓或离场时的隐含波动率不变估算，是近似。
- 到期未平的合约按到期日收盘价判断作废或按内在价值了结；被指派交割的正股按到期收盘价入账，差额留在期权那笔里。
- 少于 3 笔（组合分组少于 4 笔）的分组只列不下结论。

## 代码结构

| 文件 | 作用 |
|---|---|
| `tiger_review/fetch.py` | 老虎只读取数 |
| `tiger_review/ledger.py` | 订单拆成逐腿成交 |
| `tiger_review/campaigns.py` | 串成一笔笔交易、到期结算、结构识别 |
| `tiger_review/market.py` | 富途行情与缓存 |
| `tiger_review/attribution.py` | 盘面、方向、盈亏拆解、过程、离场后 |
| `tiger_review/patterns.py` | 打标签、分组、诊断原因 |
| `tiger_review/report.py` | 单文件 HTML（无 JS） |
| `tests/` | 用确定性假行情的端到端测试 |
