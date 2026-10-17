# Hebei Four-Carrier Tariff Monitor

🟢 Live: <https://starrry365.github.io/hebei-tariff-monitor/>

Daily at 05:07 Beijing time scrapes public tariffs from Hebei Mobile, Unicom, Telecom and CBN (~14k entries, on-sale + delisted), diffs changes, and deploys a PWA query page with region/type/channel filters, favorites and change history.

Workflows run on schedule/dispatch only — snapshots are committed back, so a `push` trigger would self-loop.

Local checks: `tools/push-and-run.sh` · Ops: [`cloud/tariff/`](cloud/tariff/) · Notify: [docs/NOTIFY_SETUP.md](docs/NOTIFY_SETUP.md)

## License

Code — build scripts, the page template, probes and tests — is [MIT](LICENSE) © 2026 starrry365.

The tariff data is **not** covered by that licence. Package names, prices, plan copy and carrier marks belong to China Mobile / China Unicom / China Telecom / China Broadcasting Network respectively. Snapshots (`cloud/tariff/snapshots/`), `history.json` and the generated `docs/data/` are committed for transparency and reproducibility only — no right to redistribute them is granted here, and none is implied by the repository's MIT licence.

If you reuse the page or the pipeline, keep the copyright notice and keep the carriers credited as the data source.
