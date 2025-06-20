import streamlit as st
import feedparser
import requests
import pandas as pd
import os
import chardet
from datetime import datetime, timedelta
import pytz
from timezonefinder import TimezoneFinder
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
import streamlit.components.v1 as components

# =============== Helpers ===============

def detect_encoding(file_path):
    with open(file_path, "rb") as f:
        result = chardet.detect(f.read(100_000))
        return result['encoding']

def get_ip_location():
    try:
        response = requests.get("https://ipinfo.io/json")
        data = response.json()
        city = data.get("city", "Unknown")
        region = data.get("region", "")
        country = data.get("country", "")
        loc = data.get("loc", "0,0")
        lat, lon = map(float, loc.split(","))
        return city, region, country, lat, lon
    except:
        return "Hamburg", "BE", "DE", 52.52, 13.405

def get_timezone(lat, lon):
    tf = TimezoneFinder()
    return tf.timezone_at(lat=lat, lng=lon) or "UTC"

def get_local_time(tz_name, time_format_24h=True):
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    if time_format_24h:
        return now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return now.strftime("%Y-%m-%d %I:%M:%S %p")

def get_weather(city):
    try:
        response = requests.get(f"https://wttr.in/{city}?format=3")
        if response.status_code == 200:
            return response.text.strip()
    except:
        return "Weather unavailable"
    return "Weather unavailable"

@st.cache_data(show_spinner=False)
def get_link_preview(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        def og_prop(prop):
            tag = soup.find("meta", property=prop)
            return tag["content"] if tag and tag.has_attr("content") else None

        title = og_prop("og:title") or (soup.title.string if soup.title else url)
        description = og_prop("og:description")
        image = og_prop("og:image")

        return {"title": title, "description": description, "image": image}
    except Exception:
        return None

@st.cache_data(ttl=900, show_spinner=False)
def fetch_feed(url):
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        # Convert feedparser.FeedParserDict to plain dict for caching
        feed_dict = {
            "feed": dict(feed.feed) if feed.feed else {},
            "entries": [dict(entry) for entry in feed.entries]
        }
        return feed_dict
    except Exception:
        return None

def filter_recent_entries(entries, minutes=30):
    now = datetime.utcnow()
    recent = []
    for entry in entries:
        published = entry.get("published") or entry.get("updated")
        if not published:
            continue
        try:
            published_dt = date_parser.parse(published)
            if published_dt.tzinfo:
                published_dt = published_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            if now - published_dt <= timedelta(minutes=minutes):
                recent.append(entry)
        except Exception:
            continue
    return recent

def speak(text, lang="en-US"):
    safe_text = text.replace("\\", "\\\\").replace("`", "\\`").replace("\n", " ")
    js_code = f"""
    <script>
    var msg = new SpeechSynthesisUtterance();
    msg.text = `{safe_text}`;
    msg.lang = '{lang}';
    window.speechSynthesis.speak(msg);
    </script>
    """
    components.html(js_code, height=0, width=0)

# =============== Main App ===============

st.set_page_config(page_title="Time, Weather & News with Voice", layout="wide")
st.title("🌦️ Weather & 📰 News")

# --- Sidebar settings ---
st.sidebar.title("🔧 Settings")

time_format_24h = st.sidebar.checkbox("Use 24-hour time format", value=True)
speech_lang = st.sidebar.selectbox(
    "Speech language",
    ["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT", "ru-RU", "zh-CN", "ja-JP"],
    index=0
)

csv_path = st.sidebar.text_input("Path to feeds CSV file:", value="cleaned_news_feeds.csv")
feed_interval_minutes = st.sidebar.slider(
    "Feed refresh interval (minutes) for new articles",
    min_value=5,
    max_value=120,
    value=30,
    step=5
)

# --- Location and time ---
city_ip, region, country, lat, lon = get_ip_location()
timezone_name = get_timezone(lat, lon)
current_time = get_local_time(timezone_name, time_format_24h=time_format_24h)
weather_info = get_weather(city_ip)

st.subheader(f"📍 Location: {city_ip}, {country}")
st.markdown(f"🕒 **Local Time:** {current_time}")
st.markdown(f"🌦️ **Weather:** {weather_info}")
st.divider()

# --- Load or init feed DataFrame ---
if os.path.exists(csv_path):
    try:
        encoding = detect_encoding(csv_path)
        df = pd.read_csv(csv_path, encoding=encoding, sep='\t')
        if "city" not in df.columns or "url" not in df.columns or "category" not in df.columns or "name" not in df.columns:
            st.error("CSV must include columns: city, country, category, name, url")
            st.stop()
    except Exception as e:
        st.error(f"Failed to load CSV: {e}")
        st.stop()
else:
    df = pd.DataFrame(columns=["city", "country", "category", "name", "url"])

cities = df["city"].dropna().unique().tolist()
if not cities:
    cities = [city_ip]

selected_city = st.sidebar.selectbox(
    "Select City",
    options=sorted(cities),
    index=cities.index(city_ip) if city_ip in cities else 0
)

city_feeds = df[df["city"].str.lower() == selected_city.lower()]

if city_feeds.empty:
    st.info(f"No feeds found for {selected_city}")

# --- Add new feed container ---
with st.sidebar.expander("➕ Add / Edit / Delete Feeds", expanded=False):
    new_city = st.text_input("City", value=selected_city)
    new_country = st.text_input("Country (2-letter code)", max_chars=2)
    new_category = st.text_input("Category")
    new_name = st.text_input("Feed Name")
    new_url = st.text_input("Feed URL")

    st.write("---")

    edit_feed_options = city_feeds["name"].tolist()
    selected_feed_to_edit = st.selectbox("Select a feed to edit/delete", options=[""] + edit_feed_options)

    if selected_feed_to_edit:
        feed_row = city_feeds[city_feeds["name"] == selected_feed_to_edit].iloc[0]
        st.write(f"Editing feed: **{selected_feed_to_edit}**")
        edit_city = st.text_input("City (Edit)", value=feed_row["city"])
        edit_country = st.text_input("Country (Edit)", value=feed_row["country"], max_chars=2)
        edit_category = st.text_input("Category (Edit)", value=feed_row["category"])
        edit_name = st.text_input("Feed Name (Edit)", value=feed_row["name"])
        edit_url = st.text_input("Feed URL (Edit)", value=feed_row["url"])

        if st.button("Update feed"):
            idx = df[(df["city"] == feed_row["city"]) & (df["url"] == feed_row["url"])].index
            if len(idx) == 1:
                df.loc[idx[0], ["city", "country", "category", "name", "url"]] = [
                    edit_city, edit_country, edit_category, edit_name, edit_url
                ]
                df.to_csv(csv_path, sep='\t', index=False, encoding='utf-8')
                st.success("Feed updated! Reload to see changes.")
            else:
                st.error("Could not find feed to update.")

        if st.button("Delete feed"):
            idx = df[(df["city"] == feed_row["city"]) & (df["url"] == feed_row["url"])].index
            if len(idx) == 1:
                df.drop(idx[0], inplace=True)
                df.to_csv(csv_path, sep='\t', index=False, encoding='utf-8')
                st.success("Feed deleted! Reload to see changes.")
            else:
                st.error("Could not find feed to delete.")

    st.write("---")

    if st.button("Add new feed"):
        if new_city and new_country and new_category and new_name and new_url:
            if new_url in df["url"].values:
                st.error("Feed URL already exists.")
            else:
                new_row = {
                    "city": new_city,
                    "country": new_country,
                    "category": new_category,
                    "name": new_name,
                    "url": new_url
                }
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                df.to_csv(csv_path, sep='\t', index=False, encoding='utf-8')
                st.success("New feed added! Reload to see changes.")
        else:
            st.error("Please fill all fields before adding.")

if city_feeds.empty:
    st.stop()

# --- Calculate new entries counts for categories and feeds ---

grouped_cats = city_feeds.groupby("category")

category_counts = {}
feed_counts = {}

for category, feeds_in_cat in grouped_cats:
    total_new = 0
    for _, feed_row in feeds_in_cat.iterrows():
        feed_url = feed_row["url"]
        feed_data = fetch_feed(feed_url)
        if feed_data:
            recent_entries = filter_recent_entries(feed_data["entries"], minutes=feed_interval_minutes)
            count_new = len(recent_entries)
            feed_counts[feed_url] = count_new
            total_new += count_new
        else:
            feed_counts[feed_url] = 0
    category_counts[category] = total_new

category_labels = [f"{cat} ({category_counts.get(cat, 0)})" for cat in grouped_cats.groups.keys()]

selected_category_label = st.radio(
    "Select Category",
    options=category_labels,
    index=0,
    horizontal=True
)

selected_category = selected_category_label.split(" (")[0]

feeds_in_cat = city_feeds[city_feeds["category"] == selected_category]

if feeds_in_cat.empty:
    st.info(f"No feeds found in category '{selected_category}'")
    st.stop()

feed_labels = [f"{row['name']} ({feed_counts.get(row['url'], 0)})" for idx, row in feeds_in_cat.iterrows()]

selected_feed_label = st.radio(
    "Select Feed",
    options=feed_labels,
    index=0,
    key="feed_vertical_tabs"
)

selected_feed_idx = feed_labels.index(selected_feed_label)
selected_feed_row = feeds_in_cat.iloc[selected_feed_idx]
selected_feed_url = selected_feed_row["url"]

feed_data = fetch_feed(selected_feed_url)
if feed_data is None:
    st.warning("Failed to load feed.")
    st.stop()

recent_entries = filter_recent_entries(feed_data["entries"], minutes=feed_interval_minutes)
st.markdown(f"### 🕑 Recent articles (last {feed_interval_minutes} minutes): {len(recent_entries)}")

entries_to_show = recent_entries[:30]

cols_per_row = 6
for i in range(0, len(entries_to_show), cols_per_row):
    cols = st.columns(cols_per_row)
    for col_idx, entry in enumerate(entries_to_show[i:i+cols_per_row]):
        with cols[col_idx]:
            entry_title = entry.get("title", "No title")
            entry_link = entry.get("link", "#")
            entry_date = entry.get("published", "No date")

            preview = get_link_preview(entry_link)

            st.markdown(f"**🗞️ [{entry_title}]({entry_link})**")
            st.markdown(f"⏱️ _{entry_date}_")

            if st.button(f"🔊 Read aloud title", key=f"voice_title_{entry_link}"):
                speak(entry_title, lang=speech_lang)

            if preview and preview.get("description"):
                st.markdown(f"> {preview['description']}")
                if st.button(f"🔊 Read aloud description", key=f"voice_desc_{entry_link}"):
                    speak(preview['description'], lang=speech_lang)

            if preview and preview.get("image"):
                st.image(preview["image"], use_container_width=True)
