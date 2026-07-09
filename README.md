# Wizzair Deal Agent ✈️

Checks every 30 minutes for Wizzair one-way flights from **Tel Aviv (TLV)** to
**anywhere**, on **any date in the next 12 months**, priced **under €30**, and
emails a report to `slavamerch92@gmail.com` when deals are found.

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

Actions tab → "Wizzair Deal Check" → **Run workflow**. Check the logs and your inbox.
That's it — it now runs automatically every 30 minutes.

## Configuration

Edit env vars in `.github/workflows/check.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_PRICE_EUR` | 30 | price threshold |
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
  It can change or temporarily block automated requests. The script auto-detects
  the API version, but if runs start failing with 404s, check the logs and update
  the fallback version in `get_api_version()`.
- Fares from TLV are returned in ILS — converted to EUR with a live rate
  (frankfurter.app) before filtering.
- Timetable prices are "cheapest of the day" cached values; always verify on the
  booking page (each row in the email links directly to it).
- GitHub Actions cron isn't exact — runs may drift a few minutes.
