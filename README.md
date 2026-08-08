# Taiwan Event Radar

一個 scheduler-agnostic 的台灣活動開賣雷達。

目標是每天掃描公開售票平台、KKTIX / OPENTIX / Ticket Plus / tixCraft 等來源，以及公開網路/新聞搜尋層，產出固定格式的 `output/latest.json`，讓其他系統可以再整理成通知。

## Run

```bash
python -m taiwan_event_radar.radar
```

執行後會自動：

- 掃描所有 discovery source
- 讀取前一次 `output/state.json`
- 產生 daily diff
- 更新 `output/state.json`
- 產生 `output/latest.json`

不需要互動輸入，也不依賴特定 scheduler。Codex automation、GitHub Actions 或其他排程系統都應使用同一個命令。

## Output

主要機器可讀輸出：

```text
output/latest.json
```

結構包含：

- `generated_at`
- `scan_success`
- `coverage_ok`
- `new_events`
- `new_sale_info`
- `changed_events`
- `source_health`

每日 diff 所需狀態：

```text
output/state.json
```

若部署在 GitHub Actions 這類 ephemeral runner，必須把 `output/state.json` 和 `output/latest.json` commit 回 repository，下一次執行才能比較前次結果。

## Source Health

`source_health` 會保留各來源的狀態，方便除錯：

- `ok`: discovery 與主要資料取得正常
- `degraded`: 可 discovery，但部分 detail 或售票資訊失敗，或用了 fallback
- `failed`: 無法可靠 discovery 新活動

`coverage_ok` 是整體 discovery coverage 判定，不會因單一售票平台失敗就自動變成 `false`。

## Repository Layout

```text
.
├── .github/workflows/radar.yml
├── data/
│   ├── announced_no_sale.json
│   ├── discovery_fallback_events.json
│   ├── manual_events.json
│   └── sources.json
├── output/
│   ├── latest.json
│   └── state.json
├── taiwan_event_radar/
│   ├── __init__.py
│   └── radar.py
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.txt
```

## GitHub Actions

The included workflow is manual-only via `workflow_dispatch`. It does not include a cron schedule yet.

The workflow:

1. checks out the repository
2. sets up Python
3. installs dependencies
4. runs `python -m taiwan_event_radar.radar`
5. verifies `output/latest.json`
6. commits `output/latest.json` and `output/state.json` back to the repository if they changed

No secrets or API keys are required.
