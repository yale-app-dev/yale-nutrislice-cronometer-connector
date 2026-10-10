# Yale Nutrislice → Cronometer

Every 15 minutes a GitHub Actions job pulls every Yale Hospitality menu from
[Nutrislice](https://yalehospitality.nutrislice.com/) and mirrors each dish into
your Cronometer account as a **custom food** with the app's exact numbers. Search
"Yale -" in Cronometer and log dishes **by the ounce**. The job runs on GitHub's
servers, so your computer can be off.

- Food names show the portion and calories the app shows, e.g.
  `Yale - Sour Cream (1 oz, 60 cal)`. Yale serves some dishes at different
  portions on different menus, and each one is its own food, matching what
  the app shows for that menu.
- Calories, macros, fiber, sugar, sodium, saturated/trans fat, cholesterol,
  potassium, calcium, iron and vitamin C come from the app's nutrition label.
- **Same-day changes:** Yale sometimes edits menus mid-meal. A new dish shows up
  on the next run. If Yale changes a dish's nutrition or name, the existing
  Cronometer food is updated in place, not duplicated. If you deleted the food
  in Cronometer, it's re-created.
- Dishes Yale removes stay in Cronometer, since you may have eaten them.
- The job commits its sync history to `state/synced.json`. Don't delete or
  edit that file, or the job will create duplicates.
- The job logs in to Cronometer only when there is something to write.
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

GitHub doesn't guarantee schedule times. Runs usually start within a few
minutes, but can be late or occasionally skipped when GitHub is busy. To sync
right away, use **Run workflow**.

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
