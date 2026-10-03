# Hebei Four-Carrier Tariff Daily Monitor

🟢 Live page: <https://starrry365.github.io/hebei-tariff-monitor/>

Daily at 06:00 this repo scrapes public tariffs from Hebei Mobile, Unicom, Telecom and CBN (on-sale plus delisted, ~14k entries, Hebei + nationwide scope), diffs online/offline changes, and redeploys the query page. The page filters by region, tariff type, channel and personal/business, keeps a daily change history, supports favorites (localStorage), and installs as a PWA.

Workflows: `tariff-daily.yml` daily run + deploy · `ci.yml` lint + tests · `fresh-check.yml` freshness check. Schedule/dispatch triggers only — snapshots are committed back, so a `push` trigger would self-loop.

After local edits: `tools/push-and-run.sh` (`--no-watch` skips waiting; exit 0 ok / 1 push failed / 2 checks failed). Ops guide: [`cloud/tariff/`](cloud/tariff/) · notify setup: [docs/NOTIFY_SETUP.md](docs/NOTIFY_SETUP.md).
