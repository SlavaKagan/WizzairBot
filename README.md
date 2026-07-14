# Wizzair Deal Agent ✈️

Checks once a day at **22:00 Israel time** for Wizzair one-way flights from
**Tel Aviv (TLV)** to **anywhere**, on **any date in the next 12 months**, priced
**under €50**, and emails a report to `slavamerch92@gmail.com` when deals are found.

Runs for free on GitHub Actions — no server needed.

## Setup (5 minutes)

### 1. Create a Gmail App Password
You can't use your normal Gmail password for SMTP.

1. Go to https://myaccount.google.com/apppasswords (requires 2FA enabled)
2. Create an app password named `wizz-agent`
3. Copy the 16-character password

### 2. Create the repo and push

```powershell
cd wizz-deal-agent
git init
git add .
git commit -m "Wizzair deal agent"
gh repo create wizz-deal-agent --private --source=. --push
```

### 3. Add secrets

```powershell
gh secret set GMAIL_ADDRESS --body "your-gmail@gmail.com"
gh secret set GMAIL_APP_PASSWORD --body "xxxx xxxx xxxx xxxx"
```

(or via GitHub UI: Settings → Secrets and variables → Actions)

### 4. Test it

Actions tab → "Wizzair Deal Check" → **Run workflow**. Tick **"Send a report email
even if no deals are found"** to force a test email so you can confirm the Gmail
credentials work, then check the logs and your inbox.
That's it — it now runs automatically once a day at 22:00 Israel time.

## Configuration

Edit env vars in `.github/workflows/check.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_PRICE_EUR` | 50 | price threshold |
| `SEARCH_MONTHS` | 12 | how far ahead to scan |
| `ALWAYS_EMAIL` | off | email even when nothing found |
| `REPORT_EMAIL` | slavamerch92@gmail.com | recipient |

## Local run (for testing)

```powershell
pip install requests
$env:GMAIL_ADDRESS="you@gmail.com"
$env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
python wizz_deal_finder.py
```

## Known limitations

- Uses Wizzair's **unofficial** backend API (the same one their website calls).
  It can change or temporarily block automated requests. The script scrapes the
  current API version live from the site (with a hard-coded fallback in
  `get_api_version()`); if runs start failing with 404s, update that fallback.
- Wizzair sets an anti-bot cookie on each fare response, so every request uses a
  fresh, cookie-free session (replaying the cookie returns
  `400 InvalidProtocol`).
- **Datacenter IPs get throttled.** From GitHub Actions, Wizzair returns `503`
  under load; the script retries with backoff, but a few routes may still be
  skipped on a busy run (logged, not fatal). It runs cleanest from a residential
  IP — if you want 100% coverage, run it locally on a schedule (Task Scheduler)
  instead of GitHub Actions.
- Fares are returned already in EUR for TLV; if a route ever prices in ILS it is
  converted with a live rate (frankfurter.app) before filtering.
- Timetable prices are "cheapest of the day" cached values; always verify on the
  booking page (each row in the email links directly to it).
- GitHub Actions cron isn't exact — runs may drift a few minutes.
