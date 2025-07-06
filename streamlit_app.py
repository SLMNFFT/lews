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
from gtts import gTTS
import tempfile
import urllib.parse
import re
import json
import time
import concurrent.futures

# =============== Updated Global Constants ===============
WEATHER_MAP = {
    0: ("☀️", "Clear sky"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Depositing rime fog"),
    51: ("🌦️", "Drizzle: Light"),
    53: ("🌦️", "Drizzle: Moderate"),
    55: ("🌧️", "Drizzle: Heavy"),
    56: ("🌧️❄️", "Freezing Drizzle: Light"),
    57: ("🌧️❄️", "Freezing Drizzle: Heavy"),
    61: ("🌧️", "Rain: Slight"),
    63: ("🌧️", "Rain: Moderate"),
    65: ("🌧️", "Rain: Heavy"),
    66: ("🌧️❄️", "Freezing Rain: Light"),
    67: ("🌧️❄️", "Freezing Rain: Heavy"),
    71: ("❄️", "Snow fall: Slight"),
    73: ("❄️", "Snow fall: Moderate"),
    75: ("❄️", "Snow fall: Heavy"),
    77: ("🌨️", "Snow grains"),
    80: ("🌧️", "Rain showers: Slight"),
    81: ("🌧️", "Rain showers: Moderate"),
    82: ("🌧️", "Rain showers: Violent"),
    85: ("🌨️", "Snow showers: Slight"),
    86: ("🌨️", "Snow showers: Heavy"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm with slight hail"),
    99: ("⛈️", "Thunderstorm with heavy hail"),
}

# =============== Proxy Management ===============
PROXY_PROVIDERS = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all",
    "https://api.proxyscrape.com/?request=displayproxies&protocol=http&timeout=10000&country=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"
]

@st.cache_data(ttl=600)
def fetch_proxy_list():
    """Fetch and cache proxies from multiple providers"""
    proxies = []
    for url in PROXY_PROVIDERS:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                proxies.extend([p.strip() for p in response.text.split('\n') if p.strip()])
        except Exception:
            continue
    return list(set(proxies))  # Remove duplicates

def test_proxy(proxy, test_url="https://ipinfo.io/json", timeout=3):
    """Test if a proxy is working"""
    try:
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        start = time.time()
        response = requests.get(test_url, proxies=proxies, timeout=timeout)
        latency = time.time() - start
        if response.status_code == 200 and "ip" in response.json():
            return True, latency
    except Exception:
        pass
    return False, None

def get_best_proxy():
    """Get the fastest working proxy with automatic rotation"""
    if "proxy_cache" not in st.session_state:
        st.session_state.proxy_cache = {
            "proxies": [],
            "index": 0,
            "last_refresh": 0,
            "working_proxies": []
        }
    
    # Refresh proxy list every 10 minutes
    if time.time() - st.session_state.proxy_cache["last_refresh"] > 600:
        st.session_state.proxy_cache["proxies"] = fetch_proxy_list()
        st.session_state.proxy_cache["index"] = 0
        st.session_state.proxy_cache["working_proxies"] = []
        st.session_state.proxy_cache["last_refresh"] = time.time()
    
    # Find working proxies if none available
    if not st.session_state.proxy_cache["working_proxies"]:
        proxies = st.session_state.proxy_cache["proxies"][st.session_state.proxy_cache["index"]:st.session_state.proxy_cache["index"]+20]
        if not proxies:
            return None  # No proxies available
        
        # Test proxies in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(test_proxy, proxies))
        
        # Collect working proxies with their latencies
        working = []
        for i, (success, latency) in enumerate(results):
            if success:
                working.append((proxies[i], latency))
        
        # Sort by latency (fastest first)
        working.sort(key=lambda x: x[1])
        st.session_state.proxy_cache["working_proxies"] = [p[0] for p in working]
        st.session_state.proxy_cache["index"] += 20
    
    # Return next proxy in rotation
    if st.session_state.proxy_cache["working_proxies"]:
        proxy = st.session_state.proxy_cache["working_proxies"].pop(0)
        return {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    return None

def smart_request(url, max_retries=3, timeout=5):
    """Make requests with automatic proxy rotation and geo-bypass"""
    headers = {"User-Agent": "Mozilla/5.0"}
    retries = 0
    
    while retries < max_retries:
        try:
            # First try without proxy
            if retries == 0:
                response = requests.get(url, headers=headers, timeout=timeout)
            else:
                # Use proxy for subsequent attempts
                proxies = get_best_proxy()
                if proxies:
                    response = requests.get(url, headers=headers, 
                                          proxies=proxies, timeout=timeout)
                else:
                    response = requests.get(url, headers=headers, timeout=timeout)
            
            # Check for geo-block indicators
            content = response.text.lower()
            geo_blocked = any(term in content for term in 
                             ["geoblocked", "not available in your region", 
                              "content restricted", "geo-restricted"])
            
            if response.status_code == 200 and not geo_blocked:
                return response
            
            # If geo-blocked, force proxy usage next time
            if geo_blocked and retries == 0:
                retries = max_retries - 1  # Immediately try with proxy
            else:
                retries += 1
                
        except Exception as e:
            retries += 1
    
    return None

# =============== Helpers ===============

def detect_encoding(file_path):
    with open(file_path, "rb") as f:
        result = chardet.detect(f.read(100_000))
        return result['encoding']

def get_ip_location():
    """Always return a tuple with 5 values - fallback to Hamburg, Germany"""
    try:
        response = smart_request("https://ipinfo.io/json", timeout=5)
        if response and response.status_code == 200:
            data = response.json()
            city = data.get("city", "Hamburg")
            region = data.get("region", "BE")
            country = data.get("country", "DE")
            loc = data.get("loc", "52.52,13.405")
            try:
                if loc:
                    lat, lon = map(float, loc.split(','))
                else:
                    lat, lon = 52.52, 13.405
                return city, region, country, lat, lon
            except:
                return "Hamburg", "BE", "DE", 52.52, 13.405
    except Exception:
        pass
    # Fallback to Hamburg, Germany coordinates
    return "Hamburg", "BE", "DE", 52.52, 13.405

def get_timezone(lat, lon):
    tf = TimezoneFinder()
    tz = tf.timezone_at(lat=lat, lng=lon)
    return tz or "UTC"

def get_local_time(tz_name, time_format_24h=True):
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC
    now = datetime.now(tz)
    if time_format_24h:
        return now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return now.strftime("%Y-%m-%d %I:%M:%S %p")

def get_link_preview(url):
    try:
        response = smart_request(url, timeout=5)
        if not response or response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")

        def og_prop(prop):
            tag = soup.find("meta", property=prop)
            return tag["content"] if tag and tag.has_attr("content") else None

        title = og_prop("og:title") or (soup.title.string if soup.title else url)
        description = og_prop("og:description")
        image = og_prop("og:image")

        return {"title": title, "description": description, "image": image}
    except Exception:
        return None

@st.cache_data(ttl=600)
def search_youtube_video(query):
    """Search YouTube and return the first video URL or None."""
    try:
        # Use YouTube search URL with urllib.parse.quote
        query_encoded = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={query_encoded}"
        response = smart_request(url, timeout=5)
        if not response or response.status_code != 200:
            return None
        
        # Parse video IDs from page HTML using regex
        video_ids = re.findall(r"watch\?v=(\S{11})", response.text)
        if not video_ids:
            return None
        
        # Return embed url for first video
        first_video_id = video_ids[0]
        embed_url = f"https://www.youtube.com/embed/{first_video_id}"
        return embed_url
    except Exception:
        return None

@st.cache_data(ttl=900, show_spinner=False)
def fetch_feed(url):
    try:
        response = smart_request(url, timeout=5)
        if not response or response.status_code != 200:
            return None
        feed = feedparser.parse(response.text)
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

def speak(text, lang="de-DE"):
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

@st.cache_data(ttl=1800)
def fetch_14day_forecast(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=weathercode,temperature_2m_max,temperature_2m_min"
        f"&timezone=auto&forecast_days=14"
    )
    try:
        response = smart_request(url, timeout=10)
        if response and response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None

@st.cache_data(ttl=1800)
def fetch_hourly_forecast(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,weathercode"
        f"&timezone=auto"
    )
    try:
        response = smart_request(url, timeout=10)
        if response and response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None

# =============== Updated Weather Display Functions ===============
def display_weather_forecast(forecast_json, unit="Celsius"):
    if forecast_json and "daily" in forecast_json:
        daily = forecast_json["daily"]
        dates = daily.get("time", [])
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        weather_codes = daily.get("weathercode", [])
        
        # Create columns for the carousel
        cols = st.columns(7)  # 7 days per row
        for i in range(len(dates)):
            date = dates[i]
            max_temp = tmax[i]
            min_temp = tmin[i]
            code = weather_codes[i]
            icon, desc = WEATHER_MAP.get(code, ("🌈", "Unknown weather"))
            date_fmt = datetime.strptime(date, "%Y-%m-%d").strftime("%a\n%b %d")
            
            # Convert to Fahrenheit if needed
            if unit == "Fahrenheit":
                max_temp = round((max_temp * 9/5) + 32)
                min_temp = round((min_temp * 9/5) + 32)
            else:
                max_temp = round(max_temp)
                min_temp = round(min_temp)
                
            unit_symbol = "°F" if unit == "Fahrenheit" else "°C"
            
            with cols[i % 7]:
                with st.container():
                    st.markdown(f"""
                    <div class="glass-panel" style="text-align: center; padding: 10px; margin: 5px; border-radius: 16px;">
                        <div style="font-weight: 600; font-size: 16px; margin-bottom: 8px;">{date_fmt}</div>
                        <div style="font-size: 32px; margin-bottom: 8px;">{icon}</div>
                        <div style="color: #00ff9d; margin-bottom: 4px; font-size: 12px;">{desc}</div>
                        <div style="display: flex; justify-content: center; gap: 10px; font-size: 14px;">
                            <div>↑ <b>{max_temp}{unit_symbol}</b></div>
                            <div>↓ <b>{min_temp}{unit_symbol}</b></div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

def display_hourly_forecast(hourly_json, unit="Celsius"):
    if hourly_json and "hourly" in hourly_json:
        hourly = hourly_json["hourly"]
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        codes = hourly.get("weathercode", [])
        
        # Filter daytime hours
        filtered_data = []
        for i, time_str in enumerate(times):
            try:
                time_fmt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
                hour = time_fmt.hour
                if 8 <= hour <= 20:
                    temp = temps[i]
                    # Convert to Fahrenheit if needed
                    if unit == "Fahrenheit":
                        temp = round((temp * 9/5) + 32)
                    else:
                        temp = round(temp)
                    filtered_data.append({
                        "time_str": time_str,
                        "temp": temp,
                        "code": codes[i],
                        "hour": hour
                    })
            except Exception:
                continue
        filtered_data = filtered_data[:12]
        
        unit_symbol = "°F" if unit == "Fahrenheit" else "°C"
        
        # Create columns for the carousel
        cols = st.columns(len(filtered_data))
        for i, data in enumerate(filtered_data):
            time_fmt = datetime.strptime(data["time_str"], "%Y-%m-%dT%H:%M")
            label = time_fmt.strftime("%H:%M")
            icon, desc = WEATHER_MAP.get(data["code"], ("🌈", "Unknown"))
            
            with cols[i]:
                with st.container():
                    st.markdown(f"""
                    <div class="glass-panel" style="text-align: center; padding: 10px; margin: 5px; border-radius: 16px; min-width: 100px;">
                        <div style="font-weight: 600; margin-bottom: 8px; font-size: 14px;">{label}</div>
                        <div style="font-size: 24px; margin-bottom: 8px;">{icon}</div>
                        <div style="color: #00ff9d; margin-bottom: 4px; font-size: 12px;">{desc}</div>
                        <div style="font-size: 16px; font-weight: 600;">{data['temp']}{unit_symbol}</div>
                    </div>
                    """, unsafe_allow_html=True)

# =============== Multi-Grid Viewer Functions ===============
def convert_to_embed_url(url):
    """Convert URLs to embeddable URLs or direct URLs for iframes"""
    url = url.strip()
    
    # YouTube handling
    if "youtube.com" in url or "youtu.be" in url:
        if "youtube.com/embed" in url:
            return url, "youtube"
        m = re.search(r"channel/([A-Za-z0-9_\-]+)", url)
        if m:
            return f"https://www.youtube.com/embed/live_stream?channel={m.group(1)}", "youtube"
        m = re.search(r"[?&]v=([A-Za-z0-9_\-]+)", url)
        if m:
            return f"https://www.youtube.com/embed/{m.group(1)}", "youtube"
        m = re.search(r"youtu\.be/([A-Za-z0-9_\-]+)", url)
        if m:
            return f"https://www.youtube.com/embed/{m.group(1)}", "youtube"
    
    # Generic webpage handling
    return url, "webpage"

def get_stream_title(url, stream_type):
    """Get title for different stream types"""
    if stream_type == "youtube":
        try:
            if "live_stream?channel=" in url:
                channel_id = re.search(r"channel=([\w-]+)", url).group(1)
                return f"Channel Live: {channel_id}"
            video_id = re.search(r"embed/([\w-]+)", url)
            if video_id:
                video_id = video_id.group(1)
                api_url = f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}"
                response = smart_request(api_url, timeout=5)
                if response and response.status_code == 200:
                    data = response.json()
                    return data.get('title', 'Unknown Title')
        except Exception:
            return "Unknown YouTube Title"
    
    # For generic webpages
    try:
        response = smart_request(url, timeout=5)
        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            return soup.title.string if soup.title else "Untitled Webpage"
    except Exception:
        pass
    
    return "Unknown Title"

def init_grids():
    if "grids" not in st.session_state:
        st.session_state.grids = {
            "Default": [
                {"url": "https://www.youtube.com/embed/live_stream?channel=UCLXo7UDZvByw2ixzpQCufnA", "title": "Vox Live", "type": "youtube"},
                {"url": "https://www.youtube.com/embed/live_stream?channel=UCBR8-60-B28hp2BmDPdntcQ", "title": "YouTube Spotlight", "type": "youtube"}
            ]
        }
    if "active_grid" not in st.session_state:
        st.session_state.active_grid = "Default"
    if "unmuted_index" not in st.session_state:
        st.session_state.unmuted_index = None
    if "new_stream_url" not in st.session_state:
        st.session_state.new_stream_url = ""
    if "streams_loaded" not in st.session_state:
        st.session_state.streams_loaded = {}
    if "video_titles" not in st.session_state:
        st.session_state.video_titles = {}
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = str(time.time())
    if "current_slide" not in st.session_state:
        st.session_state.current_slide = 0
    if "weather_slide" not in st.session_state:
        st.session_state.weather_slide = 0
    if "hourly_slide" not in st.session_state:
        st.session_state.hourly_slide = 0
    if "last_slide_change" not in st.session_state:
        st.session_state.last_slide_change = time.time()

# =============== UI Helpers ===============
def create_video_card(stream, index, grid_name):
    # Truncate title for better display
    title = stream['title']
    full_title = title
    
    if len(title) > 40:
        title = title[:37] + "..."
    
    with st.container():
        st.markdown(f"""
        <div class="video-card">
            <div class="video-header" title="{full_title}">
                <b>{title}</b>
            </div>
        """, unsafe_allow_html=True)

        # Video player
        mute_state = 1 if st.session_state.unmuted_index != index else 0
        
        if stream['type'] == "youtube":
            components.html(f"""
            <script>
            function checkGeoBlock() {{
                const iframe = document.getElementById('yt-iframe-{index}');
                try {{
                    window.addEventListener('message', function(event) {{
                        if (event.origin !== 'https://www.youtube.com') return;
                        if (event.data === 'GEO_BLOCKED') {{
                            iframe.contentWindow.postMessage('USE_PROXY', '*');
                        }}
                    }});
                    iframe.contentWindow.postMessage('CHECK_GEO', '*');
                }} catch (e) {{}}
            }}
            document.getElementById('yt-iframe-{index}').addEventListener('load', checkGeoBlock);
            </script>
            <div style="border-radius: 8px; overflow: hidden; margin-bottom: 10px;">
                <iframe id="yt-iframe-{index}" width="100%" height="200" 
                        src="{stream['url']}?autoplay=1&mute={mute_state}" 
                        frameborder="0" 
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; presentation" 
                        allowfullscreen
                        sandbox="allow-scripts allow-same-origin allow-presentation">
                </iframe>
            </div>
            """, height=220)
        else:
            components.html(f"""
                <div style="border-radius: 8px; overflow: hidden; margin-bottom: 10px; height: 200px;">
                    <iframe 
                        src="{stream['url']}" 
                        width="100%" 
                        height="200"
                        frameborder="0"
                        sandbox="allow-same-origin allow-scripts allow-presentation">
                    </iframe>
                </div>
            """, height=220)
        
        col1, col2 = st.columns(2)
        with col1:
            if stream['type'] == "youtube":
                if st.button(f"{'🔇 Unmute' if st.session_state.unmuted_index != index else '🔊 Mute'}", 
                            key=f"unmute_{index}_{grid_name}", 
                            use_container_width=True):
                    if st.session_state.unmuted_index == index:
                        st.session_state.unmuted_index = None
                    else:
                        st.session_state.unmuted_index = index
                    st.rerun()
            else:
                st.button("🎥 Web Stream", disabled=True, 
                         key=f"web_{index}_{grid_name}", 
                         use_container_width=True)
        with col2:
            if st.button("Remove", 
                        key=f"remove_{index}_{grid_name}", 
                        use_container_width=True):
                st.session_state.grids[st.session_state.active_grid].pop(index)
                if st.session_state.unmuted_index == index:
                    st.session_state.unmuted_index = None
                current_count = len(st.session_state.grids[st.session_state.active_grid])
                current_loaded = st.session_state.streams_loaded[st.session_state.active_grid]
                st.session_state.streams_loaded[st.session_state.active_grid] = min(current_loaded, current_count)
                st.rerun()
        
        st.markdown("</div>", unsafe_allow_html=True)

def display_multi_grid_viewer():
    st.header("📺 MyVü - Multi-Stream Viewer")
    
    if st.session_state.active_grid not in st.session_state.streams_loaded:
        st.session_state.streams_loaded[st.session_state.active_grid] = 1
    
    # Grid Management
    grid_col1, grid_col2, grid_col3 = st.columns([1.5, 1, 2])
    
    with grid_col1:
        grid_names = list(st.session_state.grids.keys())
        if st.session_state.active_grid not in grid_names:
            st.session_state.active_grid = grid_names[0] if grid_names else "Default"
        try:
            current_index = grid_names.index(st.session_state.active_grid)
        except ValueError:
            current_index = 0
        active_grid = st.selectbox("Select Grid", grid_names, index=current_index, key="grid_selector")
        if active_grid != st.session_state.active_grid:
            st.session_state.active_grid = active_grid
            st.rerun()
    
    with grid_col2:
        if st.button("➕ Add New Grid", use_container_width=True, key="add_grid_btn"):
            new_grid_name = f"Grid {len(st.session_state.grids) + 1}"
            st.session_state.grids[new_grid_name] = []
            st.session_state.active_grid = new_grid_name
            st.session_state.unmuted_index = None
            st.rerun()
    
    with grid_col3:
        new_name = st.text_input("✏️ Rename Current Grid", value=st.session_state.active_grid, key="rename_grid")
        if new_name and new_name != st.session_state.active_grid:
            grids = st.session_state.grids
            if new_name not in grids:
                grids[new_name] = grids.pop(st.session_state.active_grid)
                st.session_state.active_grid = new_name
                st.rerun()
    
    # Stream Management
    stream_col1, stream_col2 = st.columns([4, 1])
    with stream_col1:
        new_stream_url = st.text_input("🌐 Add YouTube or Web Stream URL", value=st.session_state.new_stream_url,
                                      placeholder="Paste YouTube or webpage URL here", key="new_stream_input")
    with stream_col2:
        if st.button("➕ Add Stream", use_container_width=True, key="add_stream_btn"):
            embed_url, stream_type = convert_to_embed_url(new_stream_url)
            if embed_url:
                if any(stream['url'] == embed_url for stream in st.session_state.grids[st.session_state.active_grid]):
                    st.warning("Stream already added.")
                else:
                    title = get_stream_title(embed_url, stream_type)
                    st.session_state.grids[st.session_state.active_grid].append({
                        "url": embed_url, 
                        "title": title,
                        "type": stream_type
                    })
                    st.session_state.new_stream_url = ""
                    st.rerun()
            else:
                st.error("Invalid URL")
    
    # Export/Import
    with st.expander("📥 Export/Import Configuration", expanded=True):
        exp_col1, exp_col2 = st.columns(2)
        with exp_col1:
            st.subheader("Export Grids")
            csv_data = []
            for grid_name, streams in st.session_state.grids.items():
                for stream in streams:
                    csv_data.append({
                        "grid_name": grid_name,
                        "stream_url": stream['url'],
                        "stream_title": stream['title'],
                        "stream_type": stream['type']
                    })
            if csv_data:
                df_export = pd.DataFrame(csv_data)
                csv_export = df_export.to_csv(index=False).encode('utf-8')
                st.download_button("💾 Download Grids CSV", csv_export, file_name="myvu_grids.csv",
                                  mime="text/csv", use_container_width=True, key="export_grids_btn")
            else:
                st.info("No grids to export")
        with exp_col2:
            st.subheader("Import Grids")
            uploaded = st.file_uploader("Upload Grids CSV", type=["csv"], key=f"file_uploader_{st.session_state.uploader_key}")
            if uploaded:
                try:
                    df_import = pd.read_csv(uploaded)
                    required_cols = ["grid_name", "stream_url", "stream_title", "stream_type"]
                    if not all(col in df_import.columns for col in required_cols):
                        st.error("Invalid CSV format. Required columns: grid_name, stream_url, stream_title, stream_type")
                    else:
                        new_grids = {}
                        for _, row in df_import.iterrows():
                            grid_name = row['grid_name']
                            if grid_name not in new_grids:
                                new_grids[grid_name] = []
                            new_grids[grid_name].append({
                                "url": row['stream_url'],
                                "title": row['stream_title'],
                                "type": row['stream_type']
                            })
                        st.session_state.grids = new_grids
                        grid_names = list(new_grids.keys())
                        if grid_names:
                            st.session_state.active_grid = grid_names[0]
                        st.session_state.streams_loaded = {}
                        st.session_state.unmuted_index = None
                        st.session_state.uploader_key = str(time.time())
                        st.success(f"Imported {len(df_import)} streams across {len(new_grids)} grids!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error importing CSV: {str(e)}")
    
    # Stream Display
    st.subheader(f"🎬 Active Grid: {st.session_state.active_grid}")
    streams = st.session_state.grids.get(st.session_state.active_grid, [])
    streams_loaded = st.session_state.streams_loaded[st.session_state.active_grid]
    
    if streams_loaded < len(streams):
        if st.button(f"🔄 Load Next Stream ({streams_loaded+1}/{len(streams)})", 
                    use_container_width=True, key="load_next_stream_btn"):
            st.session_state.streams_loaded[st.session_state.active_grid] += 1
            st.rerun()
    elif streams:
        st.success("✅ All streams loaded!")
    
    loaded_streams = streams[:streams_loaded]
    if loaded_streams:
        columns = st.columns(6)
        for idx, stream in enumerate(loaded_streams):
            with columns[idx % 6]:
                create_video_card(stream, idx, st.session_state.active_grid)
    else:
        st.info("ℹ️ No streams added to this grid yet. Add YouTube or web streams above.")

# =============== Main App ===============
st.set_page_config(page_title="Time, Weather & News with Voice", layout="wide", page_icon="🌐")

# Apply global CSS with updated theme (black backgrounds)
st.markdown("""
    <style>
    /* Updated Theme */
    :root {
        --primary: #00ff9d;
        --primary-dark: #00cc7d;
        --secondary: #ff6b6b;
        --glass-bg: rgba(10, 15, 12, 0.85);
        --glass-border: rgba(255, 255, 255, 0.1);
        --glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
        --neon-glow: 0 0 15px var(--primary), 0 0 30px rgba(0, 255, 157, 0.3);
    }
    
    body {
        background: linear-gradient(135deg, #050a07, #0c120f);
        color: #ffffff;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-size: 16px;
        line-height: 1.6;
        overflow-x: hidden;
    }
    
    h1, h2, h3, h4, h5, h6 {
        font-weight: 600;
        letter-spacing: -0.5px;
        color: #ffffff;
        text-shadow: 0 0 10px rgba(0, 255, 157, 0.3);
    }
    
    .glass-panel {
        background: var(--glass-bg);
        backdrop-filter: blur(12px);
        border: 1px solid var(--glass-border);
        border-radius: 16px;
        box-shadow: var(--glass-shadow);
        padding: 20px;
        margin-bottom: 24px;
        transition: all 0.4s ease;
    }
    
    .glass-panel:hover {
        transform: translateY(-5px);
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.3), var(--neon-glow);
    }
    
    .stTextInput>div>div>input {
        height: 42px !important;
        font-size: 16px !important;
        border-radius: 12px !important;
        background: rgba(20, 25, 22, 0.8) !important;
        color: #ffffff !important;
        border: 1px solid rgba(0, 255, 157, 0.3) !important;
        box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.2);
    }
    
    .stButton>button {
        width: 100% !important;
        margin-top: 8px !important;
        margin-bottom: 8px !important;
        border-radius: 12px !important;
        font-size: 16px !important;
        font-weight: 500;
        padding: 10px 16px;
        transition: all 0.3s ease;
        background: rgba(10, 15, 12, 0.9) !important;
        color: var(--primary) !important;
        border: 1px solid var(--primary) !important;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.5);
    }
    
    .stButton>button:hover {
        background: rgba(15, 25, 20, 0.9) !important;
        transform: translateY(-3px);
        box-shadow: 0 6px 20px rgba(0, 255, 157, 0.3);
    }
    
    .stTabs [role="tab"] {
        font-size: 18px;
        padding: 12px 20px;
        border-radius: 12px 12px 0 0;
        background: var(--glass-bg);
        color: #aaaaaa;
        border: 1px solid var(--glass-border);
        margin-right: 8px;
        transition: all 0.3s ease;
    }
    
    .stTabs [aria-selected="true"] {
        background: rgba(15, 25, 20, 0.9) !important;
        color: var(--primary) !important;
        font-weight: 600;
        border-bottom: 1px solid var(--primary);
    }
    
    .video-card {
        background: var(--glass-bg);
        border: 1px solid var(--glass-border);
        border-radius: 16px;
        padding: 16px;
        margin-bottom: 24px;
        box-shadow: var(--glass-shadow);
        transition: all 0.3s ease;
        height: 100%;
    }
    
    .video-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3), var(--neon-glow);
        border: 1px solid var(--primary);
    }
    
    .video-header {
        padding: 12px 0;
        margin-bottom: 12px;
        border-bottom: 1px solid var(--glass-border);
        font-size: 18px;
        font-weight: 600;
        text-align: center;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        padding: 10px 8px;
        color: #ffffff;
    }
    
    [data-testid="column"] {
        padding: 0 12px;
    }
    
    .stSubheader, .stMarkdown h2, .stMarkdown h3 {
        padding-bottom: 0.75rem;
        border-bottom: 2px solid var(--primary);
        margin-top: 1.8rem !important;
        font-size: 24px !important;
        color: #ffffff;
        position: relative;
    }
    
    .stSubheader:after, .stMarkdown h2:after, .stMarkdown h3:after {
        content: '';
        position: absolute;
        bottom: -2px;
        left: 0;
        width: 100px;
        height: 2px;
        background: var(--secondary);
    }
    
    .proxy-status {
        padding: 10px 16px;
        border-radius: 12px;
        font-weight: 500;
        margin: 15px 0;
        text-align: center;
        background: rgba(10, 25, 20, 0.8);
        color: #ffffff;
        border: 1px solid var(--primary);
        box-shadow: 0 0 10px rgba(0, 255, 157, 0.3);
    }
    
    .footer {
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid var(--glass-border);
        text-align: center;
        color: #aaaaaa;
        font-size: 14px;
    }
    
    .news-card {
        width: 100%;
        height: 100%;
        border-radius: 20px;
        padding: 25px;
        background: var(--glass-bg);
        backdrop-filter: blur(10px);
        box-shadow: var(--glass-shadow);
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        position: relative;
        border: 1px solid rgba(255, 255, 255, 0.1);
        transition: all 0.4s ease;
        margin-bottom: 25px;
    }
    
    .news-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.3), var(--neon-glow);
    }
    
    .news-card img {
        width: 100%;
        height: 200px;
        object-fit: cover;
        border-radius: 12px;
        margin-bottom: 15px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        display: block;
        margin-left: auto;
        margin-right: auto;
        max-width: 90%;
    }
    
    .news-card h4 {
        margin-top: 0;
        color: var(--primary);
        padding-bottom: 10px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
        display: block;
        font-size: 22px;
        font-weight: 700;
        text-shadow: 0 0 10px rgba(0, 255, 157, 0.3);
        text-align: center;
    }
    
    .news-card p {
        font-size: 16px;
        color: #e0ffe0;
        margin-bottom: 10px;
        flex-grow: 1;
        line-height: 1.7;
        text-align: center;
    }
    
    .news-card small {
        color: #aaaaaa;
        display: block;
        margin-bottom: 15px;
        font-size: 14px;
        background: rgba(0, 0, 0, 0.3);
        padding: 6px 12px;
        border-radius: 20px;
        display: inline-block;
        text-align: center;
        margin: 0 auto;
    }
    
    .news-card a {
        color: var(--secondary) !important;
        text-decoration: none;
        font-weight: 600;
        display: block;
        text-align: center;
        margin-top: 10px;
        transition: all 0.3s ease;
        position: relative;
        padding-right: 20px;
    }
    
    .news-card a:after {
        content: '→';
        position: absolute;
        right: -15px;
        top: 50%;
        transform: translateY(-50%);
        transition: all 0.3s ease;
    }
    
    .news-card a:hover {
        color: #ff9d9d !important;
        padding-right: 25px;
    }
    
    .news-card a:hover:after {
        right: -20px;
    }
    
    /* Horizontal news scroller */
    .news-scroller {
        display: flex;
        overflow-x: auto;
        gap: 20px;
        padding: 15px 0;
        scrollbar-width: thin;
        scrollbar-color: var(--primary) rgba(10, 15, 12, 0.3);
    }
    
    .news-scroller::-webkit-scrollbar {
        height: 8px;
    }
    
    .news-scroller::-webkit-scrollbar-track {
        background: rgba(10, 15, 12, 0.3);
        border-radius: 10px;
    }
    
    .news-scroller::-webkit-scrollbar-thumb {
        background: var(--primary);
        border-radius: 10px;
    }
    
    .news-scroller::-webkit-scrollbar-thumb:hover {
        background: var(--primary-dark);
    }
    
    .news-item {
        min-width: 300px;
        max-width: 350px;
        flex: 0 0 auto;
    }
    
    /* Holographic effect */
    .holographic {
        position: relative;
        overflow: hidden;
    }
    
    .holographic:before {
        content: '';
        position: absolute;
        top: -50%;
        left: -50%;
        width: 200%;
        height: 200%;
        background: linear-gradient(45deg, transparent, rgba(255,255,255,0.05), transparent);
        transform: rotate(45deg);
        animation: hologram 4s linear infinite;
        pointer-events: none;
    }
    
    @keyframes hologram {
        0% { transform: rotate(45deg) translate(-25%, -25%); }
        100% { transform: rotate(45deg) translate(25%, 25%); }
    }
    
    /* Floating animation */
    @keyframes float {
        0% { transform: translateY(0px); }
        50% { transform: translateY(-10px); }
        100% { transform: translateY(0px); }
    }
    
    .floating {
        animation: float 6s ease-in-out infinite;
    }
    
    /* Scrollbar styling */
    .news-card::-webkit-scrollbar {
        width: 8px;
    }
    
    .news-card::-webkit-scrollbar-track {
        background: rgba(10, 15, 12, 0.3);
        border-radius: 10px;
    }
    
    .news-card::-webkit-scrollbar-thumb {
        background: var(--primary);
        border-radius: 10px;
    }
    
    .news-card::-webkit-scrollbar-thumb:hover {
        background: var(--primary-dark);
    }
    </style>
""", unsafe_allow_html=True)

# Initialize session state
init_grids()

# Create tabs
tab1, tab2 = st.tabs(["Home: Weather & News", "MyVü Multi-Stream"])

# Proxy status indicator
if 'proxy_cache' in st.session_state and st.session_state.proxy_cache.get('working_proxies'):
    proxy_count = len(st.session_state.proxy_cache['working_proxies'])
    st.sidebar.markdown(f"""
        <div class="proxy-status">
            🌐 Using Proxy: {proxy_count} active proxies
        </div>
    """, unsafe_allow_html=True)
else:
    st.sidebar.markdown(f"""
        <div class="proxy-status" style="background-color: #4a235a;">
            🌐 Direct connection (no proxy)
        </div>
    """, unsafe_allow_html=True)

# Proxy management in sidebar
st.sidebar.title("🌐 Smart Proxy Settings")
if st.sidebar.button("🔄 Refresh Proxy Pool", use_container_width=True, key="refresh_proxy_btn"):
    if "proxy_cache" in st.session_state:
        st.session_state.proxy_cache["last_refresh"] = 0
    st.rerun()

proxy_debug = st.sidebar.checkbox("Show proxy debug info", key="proxy_debug")
if proxy_debug and "proxy_cache" in st.session_state:
    st.sidebar.write("**Proxy Cache Status:**")
    st.sidebar.json({
        "total_proxies": len(st.session_state.proxy_cache["proxies"]),
        "working_proxies": len(st.session_state.proxy_cache["working_proxies"]),
        "last_refresh": datetime.fromtimestamp(
            st.session_state.proxy_cache["last_refresh"]
        ).strftime("%Y-%m-%d %H:%M:%S")
    })

with tab1:
    st.title("LEWS Beta.1.0 Local 🌦️ Weather & 📰 News")

    # Sidebar controls
    st.sidebar.title("🔧 Settings")

    time_format_24h = st.sidebar.checkbox("Use 24-hour time format", value=True, key="time_format")
    speech_lang = st.sidebar.selectbox(
        "Speech language",
        ["de-DE", "en-US", "en-GB", "fr-FR", "es-ES", "it-IT", "ru-RU", "zh-CN", "ja-JP"],
        index=0,
        key="speech_lang"
    )
    
    # Add temperature unit selector
    temp_unit = st.sidebar.radio("Temperature Unit", ["Celsius", "Fahrenheit"], index=0, key="temp_unit")

    csv_path = st.sidebar.text_input("Path to feeds CSV file:", value="cleaned_news_feeds.csv", key="csv_path")
    feed_interval_minutes = st.sidebar.slider(
        "Feed refresh interval (minutes) for new articles",
        min_value=5,
        max_value=120,
        value=30,
        step=5,
        key="feed_interval"
    )

    # Load feeds CSV
    if os.path.exists(csv_path):
        try:
            encoding = detect_encoding(csv_path)
            df = pd.read_csv(csv_path, encoding=encoding, sep='\t')
            required_cols = ["city", "country", "category", "name", "url"]
            for col in required_cols:
                if col not in df.columns:
                    st.error(f"CSV must include column: {col}")
                    st.stop()
            if "lat" not in df.columns:
                df["lat"] = None
            if "lon" not in df.columns:
                df["lon"] = None
        except Exception as e:
            st.error(f"Failed to load CSV: {e}")
            st.stop()
    else:
        df = pd.DataFrame(columns=["city", "country", "category", "name", "url", "lat", "lon"])

    # City Selection
    cities = sorted(df["city"].dropna().unique().tolist())
    if not cities:
        city_ip, region, country, lat, lon = get_ip_location()
        cities = [city_ip]
    
    selected_city = st.sidebar.selectbox("Select city", options=cities, index=0, key="city_selector")
    
    # Filter dataframe by selected city
    df_city = df[df["city"] == selected_city]
    
    # Get coordinates - always fall back to Hamburg if needed
    valid_coords = False
    if not df_city.empty:
        try:
            lat = float(df_city.iloc[0]["lat"])
            lon = float(df_city.iloc[0]["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                valid_coords = True
            else:
                # Coordinates are out of range
                city_ip, region, country, lat, lon = get_ip_location()
        except (TypeError, ValueError):
            # Parsing failed
            city_ip, region, country, lat, lon = get_ip_location()

    if not valid_coords:
        city_ip, region, country, lat, lon = get_ip_location()

    # Add New Feed
    with st.sidebar.expander("➕ Add a New Feed"):
        new_city = st.text_input("City", key="new_feed_city")
        new_country = st.text_input("Country", key="new_feed_country")
        new_category = st.text_input("Category", key="new_feed_category")
        new_name = st.text_input("Feed Name", key="new_feed_name")
        new_url = st.text_input("Feed URL", key="new_feed_url")
        new_lat = st.text_input("Latitude", key="new_feed_lat")
        new_lon = st.text_input("Longitude", key="new_feed_lon")

        if st.button("Add Feed", key="add_feed_btn"):
            try:
                new_lat_f = float(new_lat)
                new_lon_f = float(new_lon)
            except Exception:
                st.warning("Latitude and Longitude must be valid numbers.")
                new_lat_f = None
                new_lon_f = None

            if all([new_city.strip(), new_country.strip(), new_category.strip(), new_name.strip(), new_url.strip()]) and new_lat_f is not None and new_lon_f is not None:
                new_row = pd.DataFrame([{
                    "city": new_city.strip(),
                    "country": new_country.strip(),
                    "category": new_category.strip(),
                    "name": new_name.strip(),
                    "url": new_url.strip(),
                    "lat": new_lat_f,
                    "lon": new_lon_f,
                }])
                df = pd.concat([df, new_row], ignore_index=True)
                try:
                    df.to_csv(csv_path, sep='\t', index=False, encoding="utf-8")
                    st.success(f"Feed added for {new_city}. Please refresh to see it in the list.")
                except Exception as e:
                    st.error(f"Failed to save feed: {e}")
            else:
                st.warning("Please fill in all fields with valid data.")

    # Display current local time
    tz_name = get_timezone(lat, lon)
    local_time_str = get_local_time(tz_name, time_format_24h)
    st.markdown(f"### Current local time in **{selected_city}** ({tz_name}): <span style='color:#00ff9d'>{local_time_str}</span>", unsafe_allow_html=True)
    
    # ========== BREAKING NEWS GRID ==========
    st.markdown("### 📰 Breaking News Feed")

    # Get news entries
    all_entries = []
    if not df_city.empty:
        for _, feed_row in df_city.iterrows():
            url = feed_row["url"]
            feed_data = fetch_feed(url)
            if feed_data and "entries" in feed_data:
                entries = filter_recent_entries(feed_data["entries"], minutes=feed_interval_minutes)
                for entry in entries:
                    entry["feed_name"] = feed_row["name"]
                    all_entries.append(entry)

    if all_entries:
        all_entries.sort(key=lambda x: date_parser.parse(x.get("published") or datetime.min), reverse=True)
        
        # Show only 3 articles (one row)
        news_items = all_entries[:3]
        
        # Create grid layout with 3 columns
        cols = st.columns(3)
        for idx, entry in enumerate(news_items):
            title = entry.get("title", "No title")
            summary = entry.get("summary") or entry.get("description") or ""
            link = entry.get("link", "#")
            published = entry.get("published") or entry.get("updated") or ""
            feed_name = entry.get("feed_name", "Unknown")
        
            # Get video preview
            video_url = search_youtube_video(title)
            
            # Format published date
            try:
                published_dt = date_parser.parse(published)
                published_str = published_dt.strftime("%b %d, %H:%M")
            except:
                published_str = published
            
            # Truncate summary
            if len(summary) > 200:
                summary = summary[:200] + "..."
            
            # Create card in grid
            with cols[idx]:
                # Display video preview if available
                if video_url:
                    st.markdown(f"""
                        <div class="news-card glass-panel" style="margin-bottom: 25px;">
                            <div style="border-radius: 10px; overflow: hidden; margin-bottom: 15px;">
                                <iframe width="100%" height="200" 
                                        src="{video_url}?autoplay=1&mute=1" 
                                        frameborder="0" 
                                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; presentation" 
                                        allowfullscreen
                                        sandbox="allow-scripts allow-same-origin allow-presentation">
                                </iframe>
                            </div>
                            <h4 style="margin-top: 0; margin-bottom: 10px; font-size: 18px;">{title}</h4>
                            <small style="display: block; margin-bottom: 10px; color: #aaaaaa;">{feed_name} • {published_str}</small>
                            <p style="font-size: 14px; margin-bottom: 15px; color: #e0e0e0;">{summary}</p>
                            <a href="{link}" target="_blank" style="display: inline-block; padding: 8px 15px; background: rgba(0, 255, 157, 0.1); border-radius: 8px; color: #00ff9d !important; text-decoration: none; font-weight: 600; transition: all 0.3s;">Read Full Article</a>
                        </div>
                    """, unsafe_allow_html=True)
                else:
                    # Fallback to image if no video found
                    image_url = None
                    if "media_content" in entry:
                        for media in entry["media_content"]:
                            if media.get("type", "").startswith("image/"):
                                image_url = media.get("url")
                                break
                    if not image_url and "enclosures" in entry:
                        for enc in entry["enclosures"]:
                            if enc.get("type", "").startswith("image/"):
                                image_url = enc.get("href")
                                break
                    if not image_url:
                        content = entry.get("content", [{}])[0].get("value", "") if "content" in entry else ""
                        if content:
                            soup = BeautifulSoup(content, "html.parser")
                            img_tag = soup.find("img")
                            if img_tag and img_tag.get("src"):
                                image_url = img_tag["src"]
                    if not image_url:
                        image_url = "https://via.placeholder.com/600x300.png?text=No+Preview"
                    
                    st.markdown(f"""
                        <div class="news-card glass-panel" style="margin-bottom: 25px;">
                            <img src="{image_url}" alt="{title}" style="width: 100%; height: 180px; object-fit: cover; border-radius: 12px; margin-bottom: 15px;">
                            <h4 style="margin-top: 0; margin-bottom: 10px; font-size: 18px;">{title}</h4>
                            <small style="display: block; margin-bottom: 10px; color: #aaaaaa;">{feed_name} • {published_str}</small>
                            <p style="font-size: 14px; margin-bottom: 15px; color: #e0e0e0;">{summary}</p>
                            <a href="{link}" target="_blank" style="display: inline-block; padding: 8px 15px; background: rgba(0, 255, 157, 0.1); border-radius: 8px; color: #00ff9d !important; text-decoration: none; font-weight: 600; transition: all 0.3s;">Read Full Article</a>
                        </div>
                    """, unsafe_allow_html=True)
        
        # "View More" button if there are more articles
        if len(all_entries) > 3:
            if st.button("🔍 View More News Articles", use_container_width=True):
                st.session_state.show_all_news = not getattr(st.session_state, "show_all_news", False)
                st.rerun()
    
        # Show all articles in horizontal scroller if requested
        if getattr(st.session_state, "show_all_news", False):
            st.markdown("#### 🔍 All News Articles")
            st.markdown('<div class="news-scroller">', unsafe_allow_html=True)
            
            for idx, entry in enumerate(all_entries):
                title = entry.get("title", "No title")
                summary = entry.get("summary") or entry.get("description") or ""
                link = entry.get("link", "#")
                published = entry.get("published") or entry.get("updated") or ""
                feed_name = entry.get("feed_name", "Unknown")
                
                # Get video preview for horizontal scroller
                video_url = search_youtube_video(title)
                
                # Get image fallback
                image_url = None
                if "media_content" in entry:
                    for media in entry["media_content"]:
                        if media.get("type", "").startswith("image/"):
                            image_url = media.get("url")
                            break
                if not image_url and "enclosures" in entry:
                    for enc in entry["enclosures"]:
                        if enc.get("type", "").startswith("image/"):
                            image_url = enc.get("href")
                            break
                if not image_url:
                    content = entry.get("content", [{}])[0].get("value", "") if "content" in entry else ""
                    if content:
                        soup = BeautifulSoup(content, "html.parser")
                        img_tag = soup.find("img")
                        if img_tag and img_tag.get("src"):
                            image_url = img_tag["src"]
                if not image_url:
                    image_url = "https://via.placeholder.com/600x300.png?text=No+Preview"
                
                # Format published date
                try:
                    published_dt = date_parser.parse(published)
                    published_str = published_dt.strftime("%b %d, %H:%M")
                except:
                    published_str = published
                
                # Truncate summary
                if len(summary) > 200:
                    summary = summary[:200] + "..."
                
                # Create card for horizontal scroller
                st.markdown(f"""
                    <div class="news-item" style="min-width: 300px; margin-right: 20px; display: inline-block;">
                        <div class="news-card glass-panel" style="width: 300px; margin-bottom: 25px;">
                            {f'''
                            <div style="border-radius: 10px; overflow: hidden; margin-bottom: 15px; height: 180px;">
                                <iframe width="100%" height="180" 
                                        src="{video_url}?autoplay=1&mute=1" 
                                        frameborder="0" 
                                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; presentation" 
                                        allowfullscreen
                                        sandbox="allow-scripts allow-same-origin allow-presentation">
                                </iframe>
                            </div>
                            ''' if video_url else f'''
                            <img src="{image_url}" alt="{title}" style="width: 100%; height: 180px; object-fit: cover; border-radius: 12px; margin-bottom: 15px;">
                            '''}
                            <h4 style="margin-top: 0; margin-bottom: 10px; font-size: 18px;">{title}</h4>
                            <small style="display: block; margin-bottom: 10px; color: #aaaaaa;">{feed_name} • {published_str}</small>
                            <p style="font-size: 14px; margin-bottom: 15px; color: #e0e0e0;">{summary}</p>
                            <a href="{link}" target="_blank" style="display: inline-block; padding: 8px 15px; background: rgba(0, 255, 157, 0.1); border-radius: 8px; color: #00ff9d !important; text-decoration: none; font-weight: 600; transition: all 0.3s;">Read Full Article</a>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
            
            # "Show Less" button
            if st.button("▲ Show Less", use_container_width=True):
                st.session_state.show_all_news = False
                st.rerun()
    else:
        st.info("ℹ️ No recent news found. Try adding more feeds or adjusting the refresh interval")
    
    # ========== WEATHER FORECAST ==========
    st.markdown("### 🌦️ Weather Forecasts")
    
    # Fetch weather data
    forecast_json = fetch_14day_forecast(lat, lon)
    hourly_json = fetch_hourly_forecast(lat, lon)
    
    # 14-Day Forecast Carousel
    st.markdown("#### 🗓️ 14-Day Forecast")
    display_weather_forecast(forecast_json, temp_unit)
    
    # Hourly Forecast Carousel
    st.markdown("#### ⏰ Daytime Forecast (8 AM - 8 PM)")
    display_hourly_forecast(hourly_json, temp_unit)
    
    # Speak forecast functionality
    if hourly_json and "hourly" in hourly_json:
        hourly = hourly_json["hourly"]
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        codes = hourly.get("weathercode", [])
        
        # Filter daytime hours
        filtered_data = []
        for i, time_str in enumerate(times):
            try:
                time_fmt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
                hour = time_fmt.hour
                if 8 <= hour <= 20:
                    temp = temps[i]
                    # Convert to Fahrenheit if needed
                    if temp_unit == "Fahrenheit":
                        temp = round((temp * 9/5) + 32)
                    else:
                        temp = round(temp)
                    filtered_data.append({
                        "time_str": time_str,
                        "temp": temp,
                        "code": codes[i],
                        "hour": hour
                    })
            except Exception:
                continue
        filtered_data = filtered_data[:12]
        
        if st.button("🔊 Speak Next 12h Weather Forecast", use_container_width=True, key="speak_weather_btn"):
            summary_lines = []
            unit_str = "degrees Fahrenheit" if temp_unit == "Fahrenheit" else "degrees Celsius"
            for data in filtered_data:
                time_fmt = datetime.strptime(data["time_str"], "%Y-%m-%dT%H:%M")
                hour = time_fmt.strftime("%H:%M")
                icon, desc = WEATHER_MAP.get(data["code"], ("🌈", "Unknown"))
                summary_lines.append(f"At {hour}, {desc.lower()} with {data['temp']} {unit_str}.")
            full_summary = "Next 12 hours: " + " ".join(summary_lines)
            speak(full_summary, lang=speech_lang)

    # News Feed Display
    if not df_city.empty:
        cat_feeds = df_city.groupby("category")
        feed_categories = list(cat_feeds.groups.keys())
        feed_tabs = st.tabs(feed_categories)

        for fidx, cat in enumerate(feed_categories):
            with feed_tabs[fidx]:
                feed_rows = cat_feeds.get_group(cat)
                st.markdown(f"## 📰 {cat} News")

                for feed_idx, feed_row in feed_rows.iterrows():
                    with st.expander(f"### {feed_row['name']}", expanded=True):
                        url = feed_row["url"]
                        entries = []
                        feed_data = fetch_feed(url)
                        if feed_data and "entries" in feed_data:
                            entries = filter_recent_entries(feed_data["entries"], minutes=feed_interval_minutes)

                        if not entries:
                            st.info("No recent news found.")
                            continue
                        
                        # Display entries in grid
                        cols_per_row = min(3, len(entries))
                        rows = (len(entries) + cols_per_row - 1) // cols_per_row
                        for row in range(rows):
                            cols = st.columns(cols_per_row)
                            for col_idx in range(cols_per_row):
                                idx = row * cols_per_row + col_idx
                                if idx < len(entries):
                                    entry = entries[idx]
                                    with cols[col_idx]:
                                        title = entry.get("title", "No title")
                                        summary = entry.get("summary") or entry.get("description") or ""
                                        link = entry.get("link", "#")
                                        published = entry.get("published") or entry.get("updated") or ""
                                        published_str = published if published else ""
                                        
                                        with st.container():
                                            st.markdown(f"### [{title}]({link})")
                                            st.markdown(f"<span style='color:#aaaaaa'>{published_str}</span>", unsafe_allow_html=True)
                                            st.markdown(f"<div style='color:#cccccc'>{summary[:200] + '...' if len(summary) > 200 else summary}</div>", unsafe_allow_html=True)

                                            video_url = search_youtube_video(title)
                                            if video_url:
                                                st.markdown("#### ▶️ Related Video Preview")
                                                components.html(f"""
                                                    <div style="border-radius: 10px; overflow: hidden; margin-bottom: 15px;">
                                                        <iframe width="100%" height="200" 
                                                                src="{video_url}" 
                                                                frameborder="0" 
                                                                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; presentation" 
                                                                allowfullscreen
                                                                sandbox="allow-scripts allow-same-origin allow-presentation">
                                                        </iframe>
                                                    </div>
                                                """, height=240)

                                            if st.button(f"🔊 Speak: {title[:20]}...", 
                                                       key=f"speak_{feed_idx}_{idx}", 
                                                       use_container_width=True):
                                                speak(f"{title}. {summary}", lang=speech_lang)

                        # Summarize all articles
                        all_texts = []
                        for entry in entries:
                            title = entry.get("title", "No Title")
                            summary = entry.get("summary") or entry.get("description") or ""
                            all_texts.append(f"{title}. {summary}")

                        combined_text = "\n\n".join(all_texts)

                        if st.button(f"🔊 Summarize All Articles in {feed_row['name']}", 
                                    key=f"summarize_all_{feed_idx}", 
                                    use_container_width=True):
                            st.text_area("Summary of all articles", combined_text, height=150)
                            speak(combined_text, lang=speech_lang)
    else:
        st.warning("No news feeds available for selected city")

    # City-Wide Summary
    st.markdown("## 📢 City-Wide News Summary")

    if st.button("🔊 Summarize All Feeds in City + Download MP3", key="summarize_city_all", use_container_width=True):
        all_texts = []
        for _, feed_row in df_city.iterrows():
            url = feed_row["url"]
            feed_data = fetch_feed(url)
            if not feed_data or "entries" not in feed_data:
                continue
            entries = filter_recent_entries(feed_data["entries"], minutes=feed_interval_minutes)
            for entry in entries:
                title = entry.get("title", "No Title")
                summary = entry.get("summary") or entry.get("description") or ""
                all_texts.append(f"{title}. {summary}")

        combined_text = "\n\n".join(all_texts)
        if not combined_text.strip():
            st.warning("No recent articles found across all feeds.")
        else:
            st.text_area("🧠 Combined Summary of All Feeds", combined_text, height=300)
            try:
                tts = gTTS(text=combined_text, lang=speech_lang.split("-")[0])
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmpfile:
                    tts.save(tmpfile.name)
                    audio_file_path = tmpfile.name
                audio_bytes = open(audio_file_path, "rb").read()
                st.audio(audio_bytes, format="audio/mp3")
                st.download_button("📥 Download MP3", audio_bytes, 
                                  file_name=f"{selected_city}_news_summary.mp3", 
                                  mime="audio/mpeg",
                                  key="download_mp3_btn")
            except Exception as e:
                st.error(f"Text-to-speech failed: {e}")

    # Footer
    st.markdown("---")
    st.markdown("""
        <div class="footer">
            © 2025 LEWS Beta — Developed with Streamlit | Eco-Friendly Green Theme
        </div>
    """, unsafe_allow_html=True)

with tab2:
    display_multi_grid_viewer()
