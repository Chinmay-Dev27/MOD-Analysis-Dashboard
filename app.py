import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pdfplumber
import re
import os
import requests
from bs4 import BeautifulSoup

# --- 1. PAGE SETUP & GLOBALS ---
st.set_page_config(page_title="MOD Strategic Intelligence", layout="wide", initial_sidebar_state="expanded")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

ZONE_LABELS = [
    'Level 1: 0-5k MW (Base Load)', 'Level 2: 5k-10k MW (Safe)', 
    'Level 3: 10k-15k MW (Moderate Merit)', 'Level 4: 15k-20k MW (High Merit)', 
    'Level 5: 20k-25k MW (RSD Risk)', 'Level 6: 25k-30k MW (High Curtailment)', 
    'Level 7: >30k MW (Peaking/Emergency)'
]

# --- 2. AUTOMATED LIVE DEMAND SCRAPING ---
@st.cache_data(ttl=300)
def get_live_demand():
    try:
        url = "https://mahasldc.in/"
        response = requests.get(url, headers=HEADERS, verify=False, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text()
        match = re.search(r'(\d+)\s*MW State Demand', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    except Exception:
        return None

# --- 3. ROBUST DATA PARSING LAYER ---
def parse_pdf_text(file_obj):
    text = ""
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted: 
                text += extracted + "\n"
            
    data = []
    pending_stations = []
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or "DISCOM" in line or "MOD STACK" in line or "Effective from" in line: 
            continue
        
        # Extract all decimal rates matching variable charge patterns (e.g., 24.8621, 4.1330)
        rates = [float(r) for r in re.findall(r'\b\d+\.\d{2,4}\b', line)]
        
        # Clean out numbers, decimals, and structural symbols to isolate station name components
        text_cleanup = re.sub(r'[\d\.\:\-\/,\"\']', '', line).strip()
        words = [w for w in text_cleanup.split() if len(w) > 1 and w.upper() not in ['G', 'CE', 'CS', 'COAL', 'GAS', 'TYPE', 'FUEL']]
        station_name = " ".join(words).strip()
        
        # Scenario A: Multiple rates are combined horizontally on a single line with station text
        if len(rates) > 1:
            # Look for distinctive layout split markers or split text into equal chunks matching the rates
            raw_segs = re.split(r'\b(?:CE|CS|G|COAL|GAS|MSPGCL)\b', line, flags=re.IGNORECASE)
            clean_segs = []
            for seg in raw_segs:
                s_clean = re.sub(r'[\d\.\:\-\/,\"\']', '', seg).strip()
                if len(s_clean) > 3 and s_clean.upper() not in ['OWNER TYPE', 'GENERATING STATION', 'TYPE OF FUEL']:
                    clean_segs.append(s_clean)
            
            # Pair them up sequentially if the token splits match the number of numeric rates found
            if len(clean_segs) == len(rates):
                for s, r in zip(clean_segs, rates):
                    data.append({'Generating_Station': s, 'Capacity_MW': "0", 'Total_VC': r})
            else:
                # Fallback: Treat as a single combined label or assign sequentially to any pending slots
                if len(station_name) > 3:
                    data.append({'Generating_Station': station_name, 'Capacity_MW': "0", 'Total_VC': rates[-1]})
            continue

        # Scenario B: Single line containing both the station description and a variable charge component
        if len(station_name) > 3 and len(rates) == 1:
            pending_stations = [] # Flush tracking queue to avoid data alignment shifts
            capacity_match = re.search(r'\b\d{2,4}\b', line)
            capacity = capacity_match.group() if capacity_match else "0"
            data.append({
                'Generating_Station': station_name, 
                'Capacity_MW': capacity, 
                'Total_VC': rates[0]
            })
            
        # Scenario C: Text-only row indicating a vertically separated column configuration
        elif len(station_name) > 3 and not rates:
            if station_name.upper() not in ['MSPGCL', 'MTOA', 'LTOA', 'STOA', 'OWNER TYPE', 'GENERATING STATION']:
                pending_stations.append(station_name)
                
        # Scenario D: Numeric rate row that maps directly to a name cached in the tracking queue above it
        elif len(rates) == 1 and not station_name and pending_stations:
            current_station = pending_stations.pop(0)
            data.append({
                'Generating_Station': current_station, 
                'Capacity_MW': "0", 
                'Total_VC': rates[0]
            })
            
    return pd.DataFrame(data)

def process_dataframe(df):
    def extract_share(mw_string):
        if pd.isna(mw_string): return 0.0
        mw_str = str(mw_string).strip()
        if mw_str.lower() in ['-', 'xxx', '', 'nan']: return 0.0
        target_str = mw_str.split('/')[1] if '/' in mw_str else mw_str
        target_str = target_str.replace(',', '')
        match = re.search(r'[\d\.]+', target_str)
        return float(match.group()) if match else 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    
    # CRITICAL FIX: Filter by the presence of a valid cost rate instead of capacity size
    # This prevents the removal of units containing zero or blank capacity placeholders
    df = df[df['Total_VC'] > 0].copy()
    
    # Sort strictly by Variable Charge component to calculate merit ranks accurately
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    
    bins = [0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')]
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=bins, labels=ZONE_LABELS)
    return df

# --- 4. SIDEBAR MANAGEMENT & PERSISTENCE ---
DATA_FILE = "saved_mod_stack.csv"

with st.sidebar:
    st.header("⚙️ Data Source Management")
    st.info("Upload the raw layout file. The parsed output will be cached locally.")
    uploaded_file = st.file_uploader("Upload MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    
df = pd.DataFrame()

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        raw_df = parse_pdf_text(uploaded_file)
        df = process_dataframe(raw_df)
    elif file_ext.endswith('.xlsx'):
        # Dynamic boundary indexing to adapt to changing spreadsheet header heights
        raw_df = pd.read_excel(uploaded_file, skiprows=4, header=None)
        if raw_df.shape[1] >= 8:
            raw_df = raw_df.iloc[:, [1, 3, 7]]
            raw_df.columns = ['Generating_Station', 'Capacity_MW', 'Total_VC']
            df = process_dataframe(raw_df)
        
    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Extracted and verified {len(df)} stations successfully.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
    st.sidebar.success(f"📂 Operational Stack Restored: {len(df)} units active.")

if not df.empty:
    with st.sidebar.expander("🔍 Operational Merit Order Ledger"):
        st.dataframe(df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC']], hide_index=True)

# --- 5. EXECUTIVE ANALYTICS DASHBOARD ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if df.empty:
    st.warning("👈 Please upload the primary grid dispatch data sheet in the sidebar to initialize analytics.")
else:
    live_demand = get_live_demand()
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Online Capacity", f"{df['Capacity_MW'].sum():,.0f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.4f}/kWh")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.4f}/kWh")
    col_kpi4.metric("Total Tracked Units", f"{len(df)}")

    st.markdown("---")
    
    st.subheader("Grid Dispatch Simulation Engine")
    if live_demand:
        st.success(f"📡 Real-Time Grid Connection Active: **{live_demand:,.0f} MW**")
        simulated_demand = st.slider("Adjust State Grid Demand for Operational Stress Test (MW):", min_value=1000, max_value=35000, value=live_demand, step=100)
    else:
        st.info("🌐 Live scrape connection unavailable. Manual scheduling profile simulation mode active.")
        simulated_demand = st.slider("Simulate State Grid Demand Profile (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")
    tab1, tab2 = st.tabs(["🎯 Plant Deep Dive & Dispatch Risk", "📊 Macro Loading Zones"])

    # --- TAB 1: INDIVIDUAL GENERATING STATION RISK ANALYTICS ---
    with tab1:
        # Context-aware defaulting to prioritize regional generation assets cleanly
        search_match = df.index[df['Generating_Station'].str.contains('Parali', case=False, na=False)].tolist()
        default_idx = int(search_match[0]) if search_match else 0
        
        selected_plant = st.selectbox("Select Target Generating Unit:", df['Generating_Station'].unique(), index=default_idx)
        plant_data = df[df['Generating_Station'] == selected_plant].iloc[0]
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Merit Rank Position", f"#{plant_data['MOD_Rank']} of {len(df)}")
        sc2.metric("Variable Charge Rate", f"₹{plant_data['Total_VC']:.4f}/kWh")
        sc3.metric("Lower-Cost Backlog Ahead", f"{plant_data['MW_Ahead_In_Queue']:,.0f} MW")
        sc4.metric("Grid Safety Tier", str(plant_data['Demand_Zone']).split(' (')[0])

        # Core Dispatch Threshold Assessment
        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **CRITICAL RISK (UNSCHEDULED / CURTAILED)**: Current system demand ({simulated_demand:,} MW) is insufficient to clear the lower-cost capacity stacked ahead of this unit ({plant_data['MW_Ahead_In_Queue']:,.0f} MW). Expected Status: Reserve Shut Down (RSD).")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL STATE (DISPATCH BOUNDARY)**: This generating asset is currently setting the system marginal price. Small fluctuations in grid parameters will trigger cyclic load changes.")
        else:
            st.success(f"✅ **SAFE DESPATCH TIER**: System loading completely clears the clearing rank threshold. Unit runs under standard schedule requirements.")

        # Merit Order Dispatch Curve Visualization
        colors = ['#ff4b4b' if name == selected_plant else 'rgba(100, 110, 130, 0.4)' for name in df['Generating_Station']]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df['Cumulative_MW'] - (df['Capacity_MW']/2),
            y=df['Total_VC'], 
            width=df['Capacity_MW'], 
            marker_color=colors, 
            marker_line_width=0,
            text=df['Generating_Station'], 
            hovertemplate="<b>%{text}</b><br>Variable Cost: ₹%{y:.4f}/kWh<br>Cumulative Loading: %{customdata:.0f} MW<extra></extra>", 
            customdata=df['Cumulative_MW']
        ))
        
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#ffcc00", annotation_text="Simulated State Demand", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#ff4b4b", annotation_text="Unit Dispatch Anchor", annotation_position="bottom right")

        fig.update_layout(xaxis_title="Cumulative System Loading Block (MW)", yaxis_title="Variable Charge Rate (₹/kWh)", template="plotly_dark", bargap=0, height=480)
        st.plotly_chart(fig, use_container_width=True)

    # --- TAB 2: MACRO BLOCK & HEADROOM DYNAMICS ---
    with tab2:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Generation Blocks per 5,000 MW Demand Interval", text_auto='.0f', color_discrete_sequence=px.colors.sequential.Viridis)
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total Block Capacity (MW)")
        st.plotly_chart(fig_zones, use_container_width=True)

        for zone in ZONE_LABELS:
            zone_df = df[df['Demand_Zone'] == zone]
            if not zone_df.empty:
                with st.expander(f"📂 {zone} (Subtotal: {zone_df['Capacity_MW'].sum():,.0f} MW)"):
                    st.dataframe(
                        zone_df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW']], 
                        use_container_width=True, 
                        hide_index=True
                    )
