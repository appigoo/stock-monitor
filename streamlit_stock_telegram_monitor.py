import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime, timezone

st.set_page_config(page_title="交易計劃｜每分鐘 Telegram 監控", layout="wide")

# ============================================================
# 交易計劃
# 可直接修改；入場區間用 (low, high)
# stop 可用單一數字或 (low, high)
# targets 用 list
# ============================================================
PLANS = {
    "META": {"direction": "LONG", "entry": (710, 725), "stop": 665, "targets": [739, 793], "rr": "0.4～1.8"},
    "AMD":  {"direction": "LONG", "entry": (600, 612), "stop": 554, "targets": [639, 652], "rr": "0.6～1.0"},
    "NVDA": {"direction": "LONG", "entry": (225, 229), "stop": 204, "targets": [236], "rr": "0.3～0.5"},
    "AAPL": {"direction": "LONG", "entry": (330, 334), "stop": 279, "targets": [344], "rr": "0.2～0.3"},
    "MSFT": {"direction": "LONG", "entry": (505, 513), "stop": 475, "targets": [537, 550], "rr": "0.8～1.4"},
    "XOM":  {"direction": "LONG", "entry": (162.7, 165), "stop": 158.4, "targets": [169.5, 174.1], "rr": "0.9～2.0"},
    "TSLA": {"direction": "LONG", "entry": (345, 355), "stop": (322, 328), "targets": [367.7, 382.8], "rr": "0.7～1.5"},
    "INTC": {"direction": "LONG", "entry": (116, 121), "stop": (74, 75), "targets": [128.7, 142.4], "rr": "低"},
}

# ============================================================
# Telegram
# Streamlit Cloud: Settings -> Secrets
#
# [telegram]
# bot_token = "123456:ABC..."
# chat_id = "123456789"
# ============================================================
def get_telegram_config():
    try:
        token = st.secrets["telegram"]["bot_token"]
        chat_id = st.secrets["telegram"]["chat_id"]
        return token, str(chat_id)
    except Exception:
        return None, None

def send_telegram(message: str) -> tuple[bool, str]:
    token, chat_id = get_telegram_config()
    if not token or not chat_id:
        return False, "未設定 Telegram Secrets"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        if r.ok:
            return True, "Telegram 已發送"
        return False, f"Telegram HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Telegram error: {e}"

# ============================================================
# Session state
# ============================================================
if "triggered" not in st.session_state:
    st.session_state.triggered = set()

if "last_prices" not in st.session_state:
    st.session_state.last_prices = {}

if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = None

def today_key(symbol):
    # 讓每天重新允許一次入場通知
    return datetime.now().strftime("%Y-%m-%d") + "_" + symbol

def price_in_entry(price, entry):
    lo, hi = entry
    return lo <= price <= hi

def format_stop(stop):
    if isinstance(stop, tuple):
        return f"{stop[0]:.2f}–{stop[1]:.2f}"
    return f"{stop:.2f}"

def calculate_rr(entry_price, stop, targets):
    if isinstance(stop, tuple):
        stop_price = sum(stop) / 2
    else:
        stop_price = float(stop)

    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return []
    return [round(abs(t - entry_price) / risk, 2) for t in targets]

# ============================================================
# 取得 1 分鐘價格
# ============================================================
@st.cache_data(ttl=45, show_spinner=False)
def get_latest_price(symbol):
    df = yf.download(
        symbol,
        period="1d",
        interval="1m",
        auto_adjust=False,
        progress=False,
        prepost=False,
    )

    if df.empty:
        return None, None, None

    # yfinance 有時回傳 MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        close = df["Close"].iloc[:, 0]
        volume = df["Volume"].iloc[:, 0]
    else:
        close = df["Close"]
        volume = df["Volume"]

    close = pd.to_numeric(close, errors="coerce").dropna()
    volume = pd.to_numeric(volume, errors="coerce").dropna()

    if close.empty:
        return None, None, None

    latest_time = close.index[-1]
    latest_price = float(close.iloc[-1])
    latest_volume = int(volume.iloc[-1]) if not volume.empty else 0

    return latest_price, latest_time, latest_volume

# ============================================================
# Telegram 訊息
# ============================================================
def build_alert(symbol, price, plan, timestamp):
    entry_lo, entry_hi = plan["entry"]
    stop = format_stop(plan["stop"])
    targets = " / ".join(f"{x:.2f}" for x in plan["targets"])
    rrs = calculate_rr(price, plan["stop"], plan["targets"])
    rr_text = " / ".join(f"{x:.2f}R" for x in rrs) if rrs else plan["rr"]

    return (
        f"🚨【入場區觸發】{symbol}\n\n"
        f"方向：🟢 做多\n"
        f"現價：${price:.2f}\n"
        f"入場區：${entry_lo:.2f}–${entry_hi:.2f}\n"
        f"止損：${stop}\n"
        f"目標：${targets}\n"
        f"即時計算 R:R：{rr_text}\n\n"
        f"⏰ {timestamp}\n"
        f"⚠️ 價格已進入預設入場區，請自行確認盤勢後交易。"
    )

# ============================================================
# UI
# ============================================================
st.title("📡 股票交易計劃｜每分鐘價格監控 + Telegram")

st.caption(
    "監控邏輯：當股價進入你設定的參考入場區間，就發送一次 Telegram。"
    " 每日每隻股票最多通知一次，避免價格在區間內來回造成洗版。"
)

with st.sidebar:
    st.header("⚙️ 監控設定")

    selected = st.multiselect(
        "監控股票",
        list(PLANS.keys()),
        default=list(PLANS.keys()),
    )

    auto_refresh = st.checkbox("自動每 60 秒更新", value=True)

    if st.button("🧪 發送 Telegram 測試"):
        ok, msg = send_telegram("✅ Streamlit 股票監控 Telegram 測試成功")
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    if st.button("🔄 重置今日通知狀態"):
        st.session_state.triggered = {
            x for x in st.session_state.triggered
            if not x.startswith(datetime.now().strftime("%Y-%m-%d"))
        }
        st.success("今日通知狀態已重置。")

st.subheader("📋 交易計劃")

plan_rows = []
for symbol in selected:
    p = PLANS[symbol]
    plan_rows.append({
        "代碼": symbol,
        "方向": "🟢 做多" if p["direction"] == "LONG" else "🔴 做空",
        "參考入場": f"{p['entry'][0]:.2f}–{p['entry'][1]:.2f}",
        "止損": format_stop(p["stop"]),
        "目標": " / ".join(f"{x:.2f}" for x in p["targets"]),
        "R:R 約": p["rr"],
    })

st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)

st.divider()
st.subheader("📡 即時監控")

status_placeholder = st.empty()

def monitor():
    rows = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol in selected:
        plan = PLANS[symbol]
        price, data_time, volume = get_latest_price(symbol)

        if price is None:
            rows.append({
                "代碼": symbol,
                "現價": "—",
                "入場區": f"{plan['entry'][0]:.2f}–{plan['entry'][1]:.2f}",
                "狀態": "❌ 無法取得價格",
                "Telegram": "—",
            })
            continue

        in_zone = price_in_entry(price, plan["entry"])
        key = today_key(symbol)

        telegram_status = "—"

        if in_zone and key not in st.session_state.triggered:
            message = build_alert(symbol, price, plan, now)
            ok, msg = send_telegram(message)

            if ok:
                st.session_state.triggered.add(key)
                telegram_status = "✅ 已通知"
            else:
                telegram_status = f"❌ {msg}"

        elif in_zone:
            telegram_status = "已通知（今日不重複）"

        if in_zone:
            state = "🟢 到達入場區"
        elif price < plan["entry"][0]:
            state = "⏳ 等待價格上來"
        else:
            state = "⏳ 等待回踩"

        rows.append({
            "代碼": symbol,
            "現價": f"${price:.2f}",
            "入場區": f"${plan['entry'][0]:.2f}–${plan['entry'][1]:.2f}",
            "狀態": state,
            "止損": format_stop(plan["stop"]),
            "目標": " / ".join(f"${x:.2f}" for x in plan["targets"]),
            "Telegram": telegram_status,
            "資料時間": str(data_time),
        })

    st.session_state.last_refresh = now
    status_placeholder.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )

# 第一次立即執行
monitor()

st.caption(f"最後更新：{st.session_state.last_refresh}")

if auto_refresh:
    # Streamlit 新版支援 fragment run_every。
    # 如果你的 Streamlit 版本太舊，請升級至較新的版本。
    @st.fragment(run_every="60s")
    def auto_monitor():
        monitor()
        st.caption(f"自動監控中｜最後更新：{st.session_state.last_refresh}")

    auto_monitor()

st.divider()
st.subheader("🧠 計算原則")

st.markdown(
    """
- **多單止損**：最近支撐 − 0.5 ATR
- **空單止損**：最近阻力 + 0.5 ATR
- **目標**：優先採用最近阻力／支撐
- **R:R** = 潛在獲利 ÷ 潛在風險
- 明顯不適合追價時，使用「回踩／突破確認」入場，而不是直接追現價。
- 本版本的 Telegram 只負責**提醒價格進入你設定的交易區**，不會自動下單。
"""
)

st.warning(
    "重要：Streamlit Cloud 的程式只有在 App 實際運行／頁面活躍時才能可靠地執行前端每分鐘刷新。"
    " 如果你要求 24/7 即使手機關閉 App 仍持續監控，應把監控程式部署到 VPS／雲端背景服務，而不是只依靠 Streamlit。"
)
