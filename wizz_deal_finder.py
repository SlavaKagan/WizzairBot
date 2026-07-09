"""
Wizzair Deal Finder
-------------------
Checks all Wizzair routes departing Tel Aviv (TLV) for one-way fares
under MAX_PRICE_EUR in the next SEARCH_MONTHS months, and emails a
report when deals are found.

Designed to run on GitHub Actions every 30 minutes (see .github/workflows/check.yml),
but works anywhere: python wizz_deal_finder.py

Required environment variables:
    GMAIL_ADDRESS       - your Gmail address (sender)
    GMAIL_APP_PASSWORD  - Gmail App Password (not your normal password!)
    REPORT_EMAIL        - recipient (default: slavamerch92@gmail.com)

Optional:
    MAX_PRICE_EUR       - default 30
    SEARCH_MONTHS       - how many months ahead to scan (default 4)
    ALWAYS_EMAIL        - "1" to email even when no deals found (default: only on deals)
"""

import os
import re
import sys
import json
import time
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

# ---------------------------------------------------------------- config ----
ORIGIN = "TLV"
MAX_PRICE_EUR = float(os.environ.get("MAX_PRICE_EUR", "30"))
SEARCH_MONTHS = int(os.environ.get("SEARCH_MONTHS", "12"))
ALWAYS_EMAIL = os.environ.get("ALWAYS_EMAIL", "0") == "1"

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
REPORT_EMAIL = os.environ.get("REPORT_EMAIL", "slavamerch92@gmail.com")

WIZZ_ROOT = "https://be.wizzair.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://wizzair.com",
    "Referer": "https://wizzair.com/",
    "Content-Type": "application/json",
}

session = requests.Session()
session.headers.update(HEADERS)


# ------------------------------------------------------------- wizz api ----
def get_api_version() -> str:
    """Wizzair's API base path includes a build version that changes often.
    Scrape it from the site (the /buildnumber endpoint no longer returns a
    plain version — it serves the app HTML), then fall back to a known-good
    recent version. The app HTML embeds the current path as
    "be.wizzair.com/<major>.<minor>.<patch>", so match that."""
    for url in ("https://wizzair.com/buildnumber", "https://wizzair.com/en-gb"):
        try:
            r = session.get(url, timeout=20)
            if r.ok:
                m = re.search(r"be\.wizzair\.com/(\d+\.\d+\.\d+)", r.text)
                if m:
                    return m.group(1)
        except requests.RequestException:
            pass

    # Fallback: known-good recent version (update if requests start 404ing)
    return "29.5.0"


def get_tlv_destinations(api_ver: str) -> list[dict]:
    """Return list of {'iata': 'XXX', 'name': 'City'} reachable from TLV."""
    url = f"{WIZZ_ROOT}/{api_ver}/Api/asset/map?languageCode=en-gb"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    cities = r.json().get("cities", [])
    by_iata = {c["iata"]: c for c in cities}

    tlv = by_iata.get(ORIGIN)
    if not tlv:
        raise RuntimeError("TLV not found in Wizzair route map")

    dests = []
    for conn in tlv.get("connections", []):
        iata = conn.get("iata")
        city = by_iata.get(iata, {})
        dests.append({"iata": iata, "name": city.get("shortName", iata).strip()})
    return dests


def get_timetable_fares(api_ver: str, dest: str, date_from: dt.date, date_to: dt.date) -> list[dict]:
    """Query the monthly timetable endpoint: returns cheapest fare per day.

    Wizzair's anti-bot sets a cookie on the first timetable response; replaying it
    on the next request is rejected with 400 {"handlerError":"InvalidProtocol"}.
    From datacenter IPs (e.g. GitHub Actions) the throttling is stricter, so we
    give every request its own short-lived, cookie-free session and retry a few
    times with backoff before giving up on a route."""
    url = f"{WIZZ_ROOT}/{api_ver}/Api/search/timetable"
    body = {
        "flightList": [
            {
                "departureStation": ORIGIN,
                "arrivalStation": dest,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            }
        ],
        "priceType": "regular",
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
    }
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            with requests.Session() as s:
                s.headers.update(HEADERS)
                r = s.post(url, json=body, timeout=30)
            if r.status_code == 404:
                raise RuntimeError("API version outdated (404)")
            if r.status_code in (429, 503) and attempt < 3:
                # Datacenter IPs (e.g. GitHub Actions) get throttled after a burst.
                # Honor Retry-After if sent, else back off hard: 4s, 8s, 16s.
                wait = float(r.headers.get("Retry-After") or 0) or 4 * 2 ** attempt
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            return r.json().get("outboundFlights", [])
        except RuntimeError:
            raise  # version problem — retrying won't help
        except requests.RequestException as e:
            last_exc = e
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))  # 2s, 4s, 6s backoff
    if last_exc:
        raise last_exc
    raise RuntimeError("throttled (429/503) after retries")


def get_ils_to_eur_rate() -> float:
    """Live ILS->EUR rate; falls back to a conservative static rate."""
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=ILS&to=EUR", timeout=15
        )
        r.raise_for_status()
        return float(r.json()["rates"]["EUR"])
    except Exception:
        return 0.25  # ~4 ILS per EUR, safe fallback


def to_eur(amount: float, currency: str, ils_eur: float) -> float:
    if currency == "EUR":
        return amount
    if currency == "ILS":
        return amount * ils_eur
    return amount  # unknown currency: pass through, better noisy than silent


# ---------------------------------------------------------------- search ----
def find_deals() -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    api_ver = get_api_version()
    print(f"Using Wizzair API version: {api_ver}")

    dests = get_tlv_destinations(api_ver)
    print(f"Found {len(dests)} destinations from TLV")

    ils_eur = get_ils_to_eur_rate()
    print(f"ILS->EUR rate: {ils_eur:.4f}")

    today = dt.date.today()
    horizon = today + dt.timedelta(days=SEARCH_MONTHS * 30)

    deals: list[dict] = []
    for d in dests:
        # scan month-by-month windows (endpoint caps ranges to ~42 days)
        start = today
        while start < horizon:
            end = min(start + dt.timedelta(days=41), horizon)
            try:
                flights = get_timetable_fares(api_ver, d["iata"], start, end)
            except Exception as e:
                errors.append(f"{ORIGIN}->{d['iata']} {start}: {e}")
                break  # skip this destination on repeated failure
            for f in flights:
                price = f.get("price") or {}
                amount = price.get("amount")
                if amount is None or amount <= 0:
                    continue
                eur = to_eur(float(amount), price.get("currencyCode", ""), ils_eur)
                if eur < MAX_PRICE_EUR:
                    dep = (f.get("departureDates") or [None])[0] or f.get("departureDate")
                    deals.append(
                        {
                            "dest_iata": d["iata"],
                            "dest_name": d["name"],
                            "date": (dep or "")[:10],
                            "price_eur": round(eur, 2),
                            "orig_amount": amount,
                            "orig_currency": price.get("currencyCode", ""),
                        }
                    )
            start = end + dt.timedelta(days=1)
            time.sleep(0.4)  # be polite, avoid rate limiting

    # dedupe + sort by price
    seen = set()
    unique = []
    for deal in sorted(deals, key=lambda x: x["price_eur"]):
        key = (deal["dest_iata"], deal["date"])
        if key not in seen:
            seen.add(key)
            unique.append(deal)
    return unique, errors


# ----------------------------------------------------------------- email ----
def build_html(deals: list[dict], errors: list[str]) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not deals:
        body = "<p>No flights under the threshold this run.</p>"
    else:
        rows = "".join(
            f"<tr><td>{d['dest_name']} ({d['dest_iata']})</td>"
            f"<td>{d['date']}</td>"
            f"<td><b>€{d['price_eur']}</b></td>"
            f"<td>{d['orig_amount']} {d['orig_currency']}</td>"
            f"<td><a href='https://wizzair.com/en-gb/booking/select-flight/"
            f"{ORIGIN}/{d['dest_iata']}/{d['date']}/null/1/0/0/null'>Book</a></td></tr>"
            for d in deals
        )
        body = f"""
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
          <tr style="background:#c6007e;color:#fff">
            <th>Destination</th><th>Date</th><th>Price (EUR)</th><th>Original</th><th>Link</th>
          </tr>
          {rows}
        </table>"""
    err_html = ""
    if errors:
        err_html = f"<p style='color:#888;font-size:12px'>{len(errors)} route(s) failed to check.</p>"
    return f"""
    <html><body style="font-family:Arial,sans-serif">
      <h2>✈️ Wizzair deals from Tel Aviv — under €{MAX_PRICE_EUR:.0f}</h2>
      <p>Checked: {now} | Next {SEARCH_MONTHS} months | One-way</p>
      {body}
      {err_html}
    </body></html>"""


def send_email(subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = REPORT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [REPORT_EMAIL], msg.as_string())


# ------------------------------------------------------------------ main ----
def main() -> int:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD env vars / secrets.")
        return 1

    deals, errors = find_deals()
    print(f"Found {len(deals)} deals under €{MAX_PRICE_EUR:.0f}; {len(errors)} errors")
    for e in errors[:8]:  # surface a sample so failures are visible in CI logs
        print(f"  err: {e}")

    if deals or ALWAYS_EMAIL:
        subject = (
            f"✈️ {len(deals)} Wizzair deals from TLV under €{MAX_PRICE_EUR:.0f}"
            if deals
            else "Wizzair check: no deals this run"
        )
        send_email(subject, build_html(deals, errors))
        print(f"Email sent to {REPORT_EMAIL}")
    else:
        print("No deals — skipping email (set ALWAYS_EMAIL=1 to get every report).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
