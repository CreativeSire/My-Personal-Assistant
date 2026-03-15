"""
Project Avatar (Mission Control)
Visual Dashboard for Victor OS.
Run with: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import json
import os
import networkx as nx
import plotly.graph_objects as go
from pathlib import Path

st.set_page_config(page_title="Victor OS Mission Control", layout="wide", page_icon="🤖")

# --- Sidebar ---
st.sidebar.title("Victor OS 3.0")
mode = st.sidebar.radio("System View", ["Overview", "Cognito (Brain)", "Signal (Influence)", "Vitality (Health)"])

# --- Overview ---
if mode == "Overview":
    st.title("🤖 System Status: ONLINE")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Sentinel", "Active", delta="Secure")
    col2.metric("Ghost Vision", "Watching", delta="Recording")
    col3.metric("Forge", "Standby", delta="0 Repairs Needed")
    
    st.subheader("Recent Activity")
    # Mock log
    st.code("""
    [10:42:15] Signal: Drafted tweet about AI Agents.
    [10:40:00] Vitality: HRV 72 (Prime State).
    [10:35:22] Fabricator: New skill 'pdf_merger' compiled.
    """, language="bash")

# --- Cognito ---
elif mode == "Cognito (Brain)":
    st.title("🧠 Knowledge Graph")
    
    graph_path = Path("memory_store/cognito_graph.json")
    if graph_path.exists():
        with open(graph_path, "r") as f:
            data = json.load(f)
            G = nx.node_link_graph(data)
            
        st.write(f"**Nodes:** {len(G.nodes)} | **Edges:** {len(G.edges)}")
        
        # Simple Visual (node list)
        st.dataframe(pd.DataFrame(G.edges(data=True), columns=["Source", "Target", "Relation"]))
    else:
        st.warning("No Knowledge Graph found. Run 'manage.py cognito' to seed.")

# --- Signal ---
elif mode == "Signal (Influence)":
    st.title("📡 Signal Command")
    
    st.subheader("Draft Queue")
    st.info("Draft: 'AI is not replacing you. A person using AI is.' (Platform: LinkedIn)")
    if st.button("Approve & Publish"):
        st.success("Published to LinkedIn!")

# --- Vitality ---
elif mode == "Vitality (Health)":
    st.title("❤️ Human Optimization")
    
    col1, col2 = st.columns(2)
    col1.radial_chart = st.progress(85)
    col1.caption("Readiness Score: 85/100")
    
    col2.write("**Recommendation:**")
    col2.success("High Intensity Deep Work Recommended.")
    
    st.line_chart({"HRV": [60, 65, 70, 72, 68, 75]})
