# Hebei Four-Carrier Tariff Monitor

🟢 Live: <https://starrry365.github.io/hebei-tariff-monitor/>

Daily at 05:07 Beijing time scrapes public tariffs from Hebei Mobile, Unicom, Telecom and CBN (~14k entries, on-sale + delisted), diffs changes, and deploys a PWA query page with region/type/channel filters, favorites and change history.

Workflows run on schedule/dispatch only — snapshots are committed back, so a `push` trigger would self-loop.

Local checks: `tools/push-and-run.sh` · Ops: [`cloud/tariff/`](cloud/tariff/) · Notify: [docs/NOTIFY_SETUP.md](docs/NOTIFY_SETUP.md)
