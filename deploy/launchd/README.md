# launchd 任务（部署模板）

本目录是本机 `~/Library/LaunchAgents/` 里两个任务的副本，换机器时照着装。路径写死为 `/Users/vock/...`，用户名不同要改；时间按本机时区，机器必须设成美东时区。

| 任务 | 时间 | 做什么 | 维护 |
|---|---|---|---|
| `com.vock.tiger-review-daily` | 周一到周五 16:20 | 收盘复盘：同步订单 → 今日复盘 + 策略累计复盘 → 通知（当天已手动复盘则跳过） | 复盘会话 |
| `com.vock.tiger-review-alerts` | 周一到周五 09:25 启动，16:00 退出 | 盘中提醒：恐慌 Put、接针，经 Telegram 发送 | WQ 主会话（改之前先知会） |
| `com.vock.card-telegram` | 周一到周五 08:36 起盯卡文件，最晚 09:25 | 两张盘前卡（多日作战卡、日内卡）出一张发一张到 Telegram 群 | WQ 主会话 |
| `com.vock.leader-watch` | 全天每 5 分钟 | 时代领袖发言采集：抓 Google News，川普 / 黄仁勋 / 马斯克本人涉市场的原话记到当天 `leaders_raw.jsonl`，不推群；盘前裁判浓缩成 `leaders_brief.json` 进作战卡（WQ 10-25 定） | 复盘会话 |

新机器一条命令装完（建 .venv、装依赖、按本机路径生成并装载三个任务、自检）：

```bash
bash deploy/install_macstudio.sh
```

手动装则：

```bash
cp deploy/launchd/*.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vock.tiger-review-daily.plist
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vock.tiger-review-alerts.plist
```

迁移细节见 `docs/迁移清单-复盘侧.md` 和 `docs/盘中提醒-v0.md`。
