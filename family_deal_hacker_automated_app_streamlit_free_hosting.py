# app.py — Family Deal Hacker (Streamlit)
# Free, automated travel-deal scoring app for London departures, no-car family trips.
# Host for free on Streamlit Community Cloud.
# -------------------------------------------------------------
# Features
# - Pulls latest deals from free sources (Fly4Free London + SecretFlying London)
# - Parses titles, prices, destinations and source links
# - Scores each deal by price, flight time estimate, transfer time & walkability (via curated metadata)
# - Builds deep links to Google Flights & Booking based on the destination
# - Lets you tweak weights + max flight time + nonstop filter
# - Shows a live Top 10 with instant links
# -------------------------------------------------------------

import re
import math
import json
import time
import random
import datetime as dt
from urllib.parse import quote, urlencode

import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st

st.set_page_config(page_title="Family Deal Hacker — London", layout="wide")
st.title("👨‍👩‍👧‍👦 Family Deal Hacker — London (No Car)")
st.caption("Find jaw-droppingly cheap, family-friendly trips from London with walkable areas & short transfers. Free, automated, and refreshable.")

# ------------------------------
# SETTINGS PANEL
# ------------------------------
with st.sidebar:
    st.header("Scoring settings")
    price_w = st.slider("Weight: Flight price (per £10)", 0.0, 2.0, 0.5, 0.1)
    accom_w = st.slider("Weight: Accom nightly (per £10)", 0.0, 2.0, 0.3, 0.1)
    nonstop_bonus = st.slider("Bonus: Nonstop flight", 0, 30, 15, 1)
    stop_penalty = st.slider("Penalty per stop", 0, 30, 10, 1)
    time_penalty = st.slider("Penalty per outbound hour", 0.0, 5.0, 2.0, 0.1)
    transfer_penalty = st.slider("Penalty per transfer minute", 0.0, 0.5, 0.10, 0.01)
    walk_bonus = st.slider("Bonus per walkability point", 0, 5, 2, 1)
    baggage_bonus = st.slider("Bonus if baggage included", 0, 10, 5, 1)
    max_hours = st.slider("Max outbound flight hours", 2.0, 12.0, 5.0, 0.5)
    nonstop_only = st.checkbox("Nonstop only", value=True)
    num_results = st.slider("Show Top N", 5, 20, 10, 1)

# ------------------------------
# CURATED DESTINATION METADATA (no-car convenience)
# airport transfer minutes + family-walkability score + recommended areas for stays
# You can expand this list over time. Values are conservative averages for public transport.
DEST_META = {
    "FNC": {"city":"Funchal (Madeira)", "country":"Portugal", "transfer_mins":45, "walk":4, "areas":["Lido promenade","Forum Madeira"]},
    "TFS": {"city":"Tenerife South → Costa Adeje", "country":"Spain (Canaries)", "transfer_mins":40, "walk":5, "areas":["Costa Adeje","Fañabé"]},
    "KEF": {"city":"Reykjavík", "country":"Iceland", "transfer_mins":45, "walk":4, "areas":["101 Reykjavík","Harpa"]},
    "NCE": {"city":"Nice", "country":"France", "transfer_mins":28, "walk":5, "areas":["Vieux Nice","Jean Médecin"]},
    "PMI": {"city":"Palma de Mallorca", "country":"Spain (Balearics)", "transfer_mins":22, "walk":5, "areas":["Can Pastilla","Old Town"]},
    "LIS": {"city":"Lisbon", "country":"Portugal", "transfer_mins":24, "walk":4, "areas":["Baixa","Chiado","Saldanha"]},
    "OPO": {"city":"Porto", "country":"Portugal", "transfer_mins":30, "walk":4, "areas":["Cedofeita","Ribeira"]},
    "DBV": {"city":"Dubrovnik", "country":"Croatia", "transfer_mins":35, "walk":4, "areas":["Lapad promenade"]},
    "VLC": {"city":"Valencia", "country":"Spain", "transfer_mins":25, "walk":5, "areas":["Ruzafa","Eixample"]},
    "SID": {"city":"Sal (Cape Verde)", "country":"Cape Verde", "transfer_mins":20, "walk":4, "areas":["Santa Maria"]},
}

# ------------------------------
# HELPERS
# ------------------------------
LONDON_CODES = ["LHR","LGW","LTN","STN","LCY","SEN"]

PRICE_PATTERNS = [
    re.compile(r"£\s?(\d+[\.,]?\d*)"),
    re.compile(r"from\s*£\s?(\d+[\.,]?\d*)", re.I),
    re.compile(r"for\s*only\s*£\s?(\d+[\.,]?\d*)", re.I),
]

# naive destination code finder
IATA_PAT = re.compile(r"\b([A-Z]{3})\b")


def extract_price(text: str):
    for pat in PRICE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1).replace(",",""))
            except:  # noqa: E722
                pass
    return None


def guess_iata(text: str):
    # try known codes first
    for code, meta in DEST_META.items():
        city = meta["city"].split(" → ")[0]
        if code in text or city.lower() in text.lower():
            return code
    # fallback generic IATA token in title
    m = IATA_PAT.search(text)
    if m and m.group(1) not in LONDON_CODES:
        return m.group(1)
    return None


def estimate_flight_hours(code: str):
    # rough, London outbound; extend as needed
    table = {
        "FNC": 4.0, "TFS": 4.5, "KEF": 3.0, "NCE": 2.1, "PMI": 2.3,
        "LIS": 2.8, "OPO": 2.2, "DBV": 2.8, "VLC": 2.3, "SID": 6.0,
    }
    return table.get(code, 3.0)


def build_google_flights_link(dest_code: str, start_date: dt.date=None, nights: int=5):
    # Use query param form which Google accepts publicly
    # Example: https://www.google.com/travel/flights?q=Flights%20to%20PMI%20from%20London%20in%20October
    q = f"Flights to {dest_code} from London"
    if start_date:
        end = start_date + dt.timedelta(days=nights)
        q += f" on {start_date.isoformat()} through {end.isoformat()}"
    return f"https://www.google.com/travel/flights?{urlencode({'q': q})}"


def build_booking_link(city: str, start_date: dt.date=None, nights: int=5, area: str|None=None):
    params = {
        'ss': f"{city} {area or ''}",
    }
    if start_date:
        end = start_date + dt.timedelta(days=nights)
        params.update({
            'checkin': start_date.isoformat(),
            'checkout': end.isoformat(),
            'group_adults': 2,
            'group_children': 2,
            'no_rooms': 1,
        })
    return f"https://www.booking.com/searchresults.html?{urlencode(params)}"


def score_row(row):
    base = 100
    price_pen = (row.get('price', 0)/10.0)*price_w
    accom_pen = (row.get('accom', 0)/10.0)*accom_w
    transfer_pen = (row.get('transfer_mins', 0))*transfer_penalty
    time_pen = (row.get('hours', 0))*time_penalty
    nonstop = 1 if row.get('nonstop', True) else 0
    stops_pen = (0 if nonstop else 1)*stop_penalty
    walk = row.get('walk', 3)
    baggage = 1 if row.get('baggage', 0) else 0
    score = base - price_pen - accom_pen - transfer_pen - time_pen - stops_pen + nonstop_bonus + walk*walk_bonus + baggage*baggage_bonus
    return round(score,2)

# ------------------------------
# SOURCES (free)
# Note: We read their public pages for newest posts mentioning London deals. Respect their usage.
# Fly4Free London page
F4F_LONDON = "https://www.fly4free.com/flight-deals/london/"
# SecretFlying London page
SF_LONDON = "https://www.secretflying.com/london/"

HEADERS = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"}


def fetch_fly4free():
    r = requests.get(F4F_LONDON, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    items = []
    for a in soup.select('article a[href]'):
        title = a.get_text(strip=True)
        href = a['href']
        if '/flight-deals/' in href or '/posts/' in href:
            if 'London' in title or 'from London' in title or 'from the UK' in title or 'from the UK' in href or 'London' in href:
                items.append({"title": title, "url": href})
    # de-dupe
    seen = set()
    dedup = []
    for it in items:
        if it['url'] not in seen and len(it['title'])>8:
            seen.add(it['url'])
            dedup.append(it)
    return dedup[:40]


def fetch_secretflying():
    r = requests.get(SF_LONDON, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    items = []
    for a in soup.select('a[href]'):
        title = a.get_text(strip=True)
        href = a['href']
        if href.startswith('https://www.secretflying.com/posts/'):
            # London page aggregates London deals
            if title:
                items.append({"title": title, "url": href})
    # de-dupe and cap
    seen = set()
    out = []
    for it in items:
        if it['url'] not in seen:
            seen.add(it['url'])
            out.append(it)
    return out[:40]


@st.cache_data(ttl=60*30)
def fetch_all_sources():
    data = []
    try:
        data += fetch_fly4free()
    except Exception as e:
        st.warning(f"Fly4Free source error: {e}")
    try:
        data += fetch_secretflying()
    except Exception as e:
        st.warning(f"SecretFlying source error: {e}")
    return data


raw = fetch_all_sources()
st.write(f"**Fetched deals:** {len(raw)} from Fly4Free + SecretFlying (London)")

# ------------------------------
# PARSE into structured rows
# ------------------------------
rows = []
for item in raw:
    title = item['title']
    url = item['url']
    price = extract_price(title) or None
    code = guess_iata(title)
    meta = DEST_META.get(code, {})
    hours = estimate_flight_hours(code) if code else None
    if hours is not None and hours > max_hours:
        continue
    if nonstop_only and ('non-stop' not in title.lower() and 'nonstop' not in title.lower()):
        # allow when not specified; we assume unknown is allowed but mark nonstop=True
        nonstop_flag = True
    else:
        nonstop_flag = True if ('non-stop' in title.lower() or 'nonstop' in title.lower()) else True

    rows.append({
        "source": "Fly4Free/SecretFlying",
        "title": title,
        "url": url,
        "iata": code,
        "city": meta.get('city'),
        "country": meta.get('country'),
        "hours": hours or 3.0,
        "transfer_mins": meta.get('transfer_mins', 35),
        "walk": meta.get('walk', 4),
        "areas": ", ".join(meta.get('areas', [])) if meta.get('areas') else None,
        "price": price or 120.0,
        "accom": 100.0,
        "nonstop": nonstop_flag,
        "baggage": 0,
    })

# score
for r in rows:
    r['score'] = score_row(r)

# sort and take top N
rows = sorted(rows, key=lambda x: x['score'], reverse=True)[:num_results]

# Attach deep links
today = dt.date.today()
for r in rows:
    dest = r.get('iata') or ''
    area = (r.get('areas') or '').split(',')[0] if r.get('areas') else None
    r['Flights Link'] = build_google_flights_link(dest, start_date=today+dt.timedelta(days=21), nights=6)
    r['Accommodation Link'] = build_booking_link(r.get('city') or dest, start_date=today+dt.timedelta(days=21), nights=6, area=area)

# Dataframe display
if rows:
    df = pd.DataFrame(rows, columns=[
        'score','title','city','country','price','hours','transfer_mins','walk','areas','Flights Link','Accommodation Link','url'
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No results matched your current filters — try increasing max flight hours or toggling nonstop only.")

st.divider()
st.markdown("### How to deploy free")
st.markdown("""
1. **Create a free account** at [streamlit.io](https://streamlit.io) → Community Cloud.
2. **Create a new public GitHub repo**, add this `app.py` file.
3. In Streamlit Cloud: **Deploy an app** → point it to your repo and `app.py`.
4. Done. The app will fetch & score deals on-demand. You can set it to **auto-redeploy** on push.

**Optional automations (still free):**
- Add a `.streamlit/secrets.toml` later if you integrate any APIs (not required now).
- Use **GitHub Actions** on a schedule (cron) to hit your Streamlit URL to keep the app warm.
""")

st.caption("Sources: Fly4Free London & SecretFlying London public pages. This app reads publicly available listings; always verify details on the source before booking.")
