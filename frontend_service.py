"""Minimal Streamlit display for the read-only demo API."""
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(page_title="Trading Copilot V2", layout="wide")
st.title("Trading Copilot V2 — Demo Research")
st.caption("Manual execution only. Reference-market data is not yet validated against Binomo/Crypto IDX.")

backend_url = st.sidebar.text_input("Backend URL", "http://127.0.0.1:8000")
symbol = st.sidebar.selectbox("Reference symbol", ["ETHUSDT", "BTCUSDT"])
timeframe = st.sidebar.selectbox("Reference timeframe", ["1m", "3m", "5m", "15m", "1h"])

if st.button("Get research signal", type="primary"):
    try:
        response = requests.get(f"{backend_url}/predict",
                                params={"symbol": symbol, "tf": timeframe}, timeout=10)
        response.raise_for_status()
        data = response.json()
        signal = data["signal"]
        left, middle, right = st.columns(3)
        left.metric("Reference price", f'{data["reference_price"]:.6f}')
        middle.metric("Signal", signal["signal"])
        right.metric("Signal strength", f'{signal["signal_strength"]:.3f}')
        st.write(f'Reason: `{signal["reason"]}`')
        st.write(f'Data age: `{data["data_age_seconds"]:.1f}s`')
        st.warning(data["warning"])
        st.caption(f'Retrieved {datetime.now(timezone.utc).isoformat()}')
        with st.expander("Raw response"):
            st.json(data)
    except requests.RequestException as exc:
        st.error(f"Backend request failed: {exc}")

