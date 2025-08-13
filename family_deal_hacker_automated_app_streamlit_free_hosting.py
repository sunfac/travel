import re
import datetime as dt
from urllib.parse import urlencode

import streamlit as st
import pandas as pd
import feedparser
import requests

st.set_page_config(page_title="Family Deal Hacker — London", layout="wide")
st.title("👨‍👩‍👧‍👦 Family Deal Hacker — London (No Car)")
st.caption("Find jaw-droppingly cheap, family-friendly trips from London with walkable areas & short transfers.")

# Sidebar settings
with st.sidebar:
    st.header("Scoring settings")
    price_w = st.slider("Weight: Flight price (per £10)", 0.0, 2.0, 0.5, 0.1)
    walk_bonus = st.slider("Bonus per walkability point", 0, 5, 2, 1)
    nonstop_bonus = st.slider("Bonus: Nonstop flight", 0, 30, 15, 1)
    max_hours = st.slider("Max outbound flight hours", 2.0, 12.0, 5.0, 0.5)
    num_results = st.slider("Show Top N", 5, 20, 10, 1)

DEST_META = {
    "FNC": {"city":"Funchal (Madeira)","country":"Portugal","transfer_mins":45,"walk":4},
    "TFS": {"city":"Tenerife South","country":"Spain","transfer_mins":40,"walk":5},
    "PMI": {"city":"Palma de Mallorca","country":"Spain","transfer_mins":22,"walk":5},
    "LIS": {"city":"Lisbon","country":"Portugal","transfer_mins":24,"walk":4},
}

def build_google_flights_link(dest_code, start_date=None, nights=5):
    q = f"Flights to {dest_code} from London"
    return f"https://www.google.com/travel/flights?{urlencode({'q': q})}"

def build_booking_link(city):
    return f"https://www.booking.com/searchresults.html?{urlencode({'ss': city})}"

# Sources that allow access
RSS_URLS = [
    "https://www.holidaypirates.com/rss",
]

# Optional Kiwi Tequila API for live prices
TEQUILA_API_KEY = st.secrets.get('TEQUILA_API_KEY', None)
TEQUILA_SEARCH = "https://api.tequila.kiwi.com/v2/search"

def fetch_rss():
    deals = []
    for url in RSS_URLS:
        feed = feedparser.parse(url)
        for e in feed.entries:
            if any(k in e.title.lower() for k in ['london','uk','from london']):
                deals.append({"title": e.title, "link": e.link})
    return deals

def fetch_tequila(max_hours, nonstop_only):
    if not TEQUILA_API_KEY:
        return []
    today = dt.date.today()
    date_from = today.strftime('%d/%m/%Y')
    date_to = (today + dt.timedelta(days=60)).strftime('%d/%m/%Y')
    params = {
        'fly_from': 'LON',
        'date_from': date_from,
        'date_to': date_to,
        'nights_in_dst_from': 4,
        'nights_in_dst_to': 8,
        'curr': 'GBP',
        'adults': 2,
        'children': 2,
        'max_stopovers': 0 if nonstop_only else 1,
        'limit': 50,
        'sort': 'price',
    }
    headers = {'apikey': TEQUILA_API_KEY}
    try:
        r = requests.get(TEQUILA_SEARCH, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json().get('data', [])
        out = []
        for d in data:
            city_to = d.get('cityTo')
            iata_to = d.get('flyTo')
            price = d.get('price')
            hours = (d.get('duration', {}).get('total', 0) / 3600.0)
            out.append({
                'title': f"{city_to} from London £{price}",
                'link': d.get('deep_link'),
                'iata': iata_to,
                'price': price,
                'hours': hours,
                'nonstop': (0 if d.get('has_stopovers') else 1)
            })
        out = [x for x in out if x.get('hours') is None or x['hours'] <= max_hours]
        return out
    except Exception as e:
        st.warning(f"Kiwi Tequila error: {e}")
        return []

raw_deals = fetch_rss() + fetch_tequila(max_hours, True)
if not raw_deals:
    st.warning("No live deals fetched — using placeholder data.")
    raw_deals = [
        {"title": "London to Funchal for £46 return","link":"https://example.com"},
        {"title": "London to Tenerife South for £28 return","link":"https://example.com"},
    ]

rows = []
for deal in raw_deals:
    title = deal['title']
    code = deal.get('iata')
    for k, meta in DEST_META.items():
        if (code and code == k) or k in title or meta['city'].lower() in title.lower():
            price = deal.get('price', 100)
            score = 100 - (price/10.0)*price_w + meta['walk']*walk_bonus + nonstop_bonus
            rows.append({
                'score': score,
                'title': title,
                'city': meta['city'],
                'country': meta['country'],
                'price': price,
                'Flights Link': build_google_flights_link(k),
                'Accommodation Link': build_booking_link(meta['city']),
                'source': deal['link']
            })

rows = sorted(rows, key=lambda x: x['score'], reverse=True)[:num_results]
st.dataframe(pd.DataFrame(rows), use_container_width=True)
