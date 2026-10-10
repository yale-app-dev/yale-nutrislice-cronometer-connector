# Yale Nutrislice → Cronometer

Every 15 minutes a GitHub Actions job pulls every Yale Hospitality menu from
[Nutrislice](https://yalehospitality.nutrislice.com/) and adds each dish to your
Cronometer account as a **custom food**. Search "Yale -" in Cronometer and log
dishes **by the ounce**. Each food also has Yale's posted portion as a serving.
The job runs on GitHub's servers, so your computer can be off.

- Calories, macros, fiber, sugar, sodium, saturated/trans fat, cholesterol,
  potassium, calcium, iron and vitamin C come from Yale's nutrition data.
- A dish is created once and updated only if Yale changes its nutrition. The
  job commits its sync history to `state/synced.json`, so don't delete that file.
- The job logs in to Cronometer only when there is something new to save, so
  most runs never log in.
- Dishes without nutrition info or a weight-based serving are skipped.

> Cronometer has no official API. This uses the same private API as the
> Cronometer mobile app, the approach used by
> [cronometer-api-mcp](https://github.com/rwestergren/cronometer-api-mcp).
> It could break if Cronometer changes that API.

## Setup

1. Publish this repo to GitHub. In GitHub Desktop: **Publish repository**.
   Public repos get unlimited free Actions minutes. A private repo on the free
   plan gets 2,000 minutes a month, but a run every 15 minutes uses about 2,900.
2. On GitHub, open **Settings → Secrets and variables → Actions → New repository
   secret** and add `CRONOMETER_EMAIL` and `CRONOMETER_PASSWORD`. Secrets stay
   hidden, even in a public repo.
3. Open the **Actions** tab, enable workflows if GitHub asks, then open
   **Sync Yale menus to Cronometer → Run workflow** to start the first sync.
   The first run creates about 400 foods and takes a few minutes. After that
   the job runs every 15 minutes on its own.

GitHub can start scheduled runs late, by a few minutes up to about half an hour
when its servers are busy.

## Config

`config.json`:

| key | meaning |
| --- | --- |
| `halls` | `["all"]`, or slugs from `python3 yale_cronometer_sync.py --list-halls` |
| `menus` | `[]` for all, or e.g. `["breakfast", "lunch", "dinner"]` |
| `days_ahead` | `0` = today only, `1` = today + tomorrow |
| `name_prefix` | text put before each food name in Cronometer |

## Running locally

```bash
python3 yale_cronometer_sync.py --dry-run     # preview, no Cronometer login
```

Don't run a real (non-dry-run) sync locally while the GitHub job is active.
Each copy has its own sync history, so you would get duplicate foods.

**Two-factor auth:** the GitHub job can't type a 2FA code. If your Cronometer
account uses 2FA, it can't log in.
