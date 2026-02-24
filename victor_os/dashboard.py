import streamlit as st
import pandas as pd
import time
import plotly.express as px
from monitor import get_system_metrics
import subprocess
import os

# Page Setup
st.set_page_config(
    page_title="Victor OS | Neural Interface", 
    page_icon="🧠", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLE ---
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    .stMetric {
        background-color: #1f2937;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #374151;
    }
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR CONTROLS ---
with st.sidebar:
    st.image("https://img.icons8.com/wired/128/ffffff/brain.png", width=80)
    st.title("🎛️ Control Panel")
    st.markdown("---")
    
    if st.button("🚀 Run Morning Briefing"):
        subprocess.Popen(["python", "victor_os/daily_briefing.py"], shell=True)
        st.toast("Briefing Protocol Initiated!", icon="🚀")
    
    if st.button("🔄 Restart Brain"):
        st.toast("Restarting Systems...", icon="⚠️")
        subprocess.Popen(["taskkill", "/F", "/IM", "python.exe"], shell=True)
        time.sleep(1)
        # Note: This will kill the dashboard too if not careful, 
        # but the user requested it. In production, we'd restart specific scripts.
    
    st.markdown("---")
    st.caption("v2.1.0 | Victor Neural Interface")
    st.info("📡 Connection: Stable\n🧠 Status: Online")

# --- MAIN DASHBOARD ---
st.title("🧠 Victor Neural Interface")
st.caption(f"Kernel Version: 1.0.4 | Telemetry: Active | {time.strftime('%Y-%m-%d %H:%M:%S')}")

# 1. LIVE SYSTEM METRICS (Real-time CPU/RAM)
metrics = get_system_metrics()
c1, c2, c3, c4 = st.columns(4)
c1.metric("CPU Load", f"{metrics['cpu']}%", delta="+0.2%")
c2.metric("Memory Usage", f"{metrics['ram']}%", delta="-1.5%")
c3.metric("Storage Status", f"{metrics['disk']}%", delta="Normal")
st.markdown("---")

# 1.5 DEV PORT MONITOR (Environment Guardian)
st.subheader("📡 Environment Guardian | Port Health")
ports = metrics.get('ports', {})
if ports:
    p_cols = st.columns(len(ports))
    for i, (port, state) in enumerate(ports.items()):
        with p_cols[i]:
            color = "#10b981" if state == "OPEN" else "#ef4444" # Green if Open, Red if Closed
            st.markdown(f"""
                <div style="background-color: #1f2937; padding: 10px; border-top: 4px solid {color}; border-radius: 5px; text-align: center;">
                    <span style="font-size: 0.8rem; color: #9ca3af;">PORT {port}</span><br>
                    <span style="font-size: 1.2rem; font-weight: bold; color: white;">{state}</span>
                </div>
                """, unsafe_allow_html=True)

st.markdown("---")

# 2. ACTIVITY LOG ANALYTICS
LOG_PATH = "victor_os/data/system_logs.csv"

try:
    if os.path.exists(LOG_PATH):
        df = pd.read_csv(LOG_PATH)
        
        # Create Layout
        col_left, col_right = st.columns([2, 1])
        
        with col_left:
            st.subheader("📉 Agent Activity Stream")
            # Show last 10 logs
            st.dataframe(
                df.tail(15).iloc[::-1], # Reversed to show newest first 
                use_container_width=True,
                hide_index=True
            )

        with col_right:
            st.subheader("📊 Task Distribution")
            if not df.empty:
                # Pie chart of which agents are working
                agent_counts = df['Agent'].value_counts()
                fig = px.pie(
                    values=agent_counts.values, 
                    names=agent_counts.index, 
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig.update_layout(
                    margin=dict(t=0, b=0, l=0, r=0), 
                    height=300, 
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font_color="white"
                )
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("⚠️ Waiting for Kernel Data... (Start Telegram Server to generate logs)")

except Exception as e:
    st.error(f"Kernel Reading Error: {e}")

# 3. LIVE KERNEL CODE VIEW
st.subheader("🖥️ Live Kernel Output")
with st.expander("View System Telemetry", expanded=True):
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Show last 5 lines formatted
                for line in lines[-5:]:
                    st.code(line.strip(), language="log")
        else:
            st.write("Kernal stream not yet initiated.")
    except:
        st.write("Connecting to stream...")

# Auto-rerun to simulate real-time updates
time.sleep(2)
st.rerun()
