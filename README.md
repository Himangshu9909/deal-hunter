# Deal Hunter backend

Polls public deal sources every ~10 minutes (GitHub Actions cron), filters for big price drops, de-dupes, and pushes
the best new ones to the Deal Hunter app through Firebase Cloud Messaging (FCM, free).

Sources (each isolated - one failing never stops the others): DesiDime deal cards, public Telegram deal channels
(`t.me/s/<channel>`), r/dealsforindia RSS, Amazon India search (>=70% off filter), Flipkart search (sorted by discount).

Run locally: `pip install -r requirements-dev.txt && pytest && python -m dealhunter.main --dry-run --data-dir /tmp/dh --config config.json`

Config (`config.json`): `min_push_pct`, `min_feed_pct`, `price_floor`, `price_ceiling`, `scan_min_pct`, `min_drop_rs`, `max_push_per_run`,
`telegram_channels`, `amazon_queries`, `flipkart_queries`, `sources`. Secrets: only the GitHub secret `FCM_SERVICE_ACCOUNT` (never commit it).
