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

# Define the zones globally so they are never lost during CSV saves
ZONE_LABELS = [
    'Level 1: 0-5k MW (Base Load)', 'Level 2: 5k-10k MW (Safe)', 
    'Level 3: 10k-15k MW (Moderate Merit)', 'Level 4: 15k-20k MW (High Merit)', 
    'Level 5: 20k-25k MW (RSD Risk)', 'Level 6: 25k-30k MW (High Curtailment)', 
    'Level 7: >30k MW (Peaking/Emergency)'
]

# --- 2. AUTOMATED SCRAPING (WORKS LOCALLY, BLOCKED ON CLOUD) ---
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

# --- 3. ROBUST DATA PARSING (ANCHOR PIVOT METHOD) ---
def parse_pdf_text(file_obj):
    text = ""
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted: text += extracted + "\n"
            
    data = []
    for line in text.split('\n'):
        line = line.strip()
        if not line: continue
        
        # Use Fuel Type as the center anchor
        match = re.search(r'\s+(Coal/Oil/Gas|Coal|Gas)\s+', line, re.IGNORECASE)
        if match:
            left_part = line[:match.start()].strip()
            right_part = line[match.end():].strip()
            left_tokens = left_part.split()
            right_tokens = right_part.split()
            
            if len(left_tokens) >= 2 and len(right_tokens) >= 1:
                capacity = left_tokens[-1]
                station = " ".join(left_tokens[1:-1])
                total_vc_str = right_tokens[-1]
                
                try:
                    vc_clean = re.search(r'([\d\.]+)', total_vc_str)
                    if vc_clean:
                        total_vc = float(vc_clean.group(1))
                        if total_vc > 0:
                            data.append({'Generating_Station': station, 'Capacity_MW': capacity, 'Total_VC': total_vc})
                except ValueError:
                    continue
    return pd.DataFrame(data)

def process_dataframe(df):
    def extract_share(mw_string):
        if pd.isna(mw_string): return 0.0
        mw_str = str(mw_string).strip()
        if mw_str.lower() in ['-', 'xxx', '']: return 0.0
        target_str = mw_str.split('/')[1] if '/' in mw_str else mw_str
        target_str = target_str.replace(',', '')
        match = re.search(r'[\d\.]+', target_str)
        return float(match.group()) if match else 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    df = df[df['Capacity_MW'] > 0].copy()
    
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    
    bins = [0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')]
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=bins, labels=ZONE_LABELS)
    return df

# --- 4. SIDEBAR UPLOAD & DATA PERSISTENCE ---
DATA_FILE = "saved_mod_stack.csv"

with st.sidebar:
    st.header("⚙️ Data Source")
    st.info("Upload the latest SLDC MOD Stack. The app will remember it for future visits.")
    uploaded_file = st.file_uploader("Upload PDF or Excel", type=["pdf", "xlsx"])
    
df = pd.DataFrame()

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        raw_df = parse_pdf_text(uploaded_file)
        df = process_dataframe(raw_df)
    elif file_ext.endswith('.xlsx'):
        raw_df = pd.read_excel(uploaded_file, skiprows=7, header=None)
        raw_df.columns = ['Sr_No', 'Generating_Station', 'Owner_Type', 'Capacity_MW', 'Fuel_Type', 'Approved_VC', 'Impact_Change', 'Total_VC']
        raw_df = raw_df.dropna(subset=['Total_VC'])
        df = process_dataframe(raw_df)
        
    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Data processed and saved! You can now safely refresh the page.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    # Fix the missing category type after loading from CSV
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
    st.sidebar.success("📂 Loaded previously saved MOD data.")

if not df.empty:
    with st.sidebar.expander("🔍 View Raw Extraction Data"):
        st.dataframe(df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC']], hide_index=True)

# --- 5. MAIN DASHBOARD ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if df.empty:
    st.warning("👈 Please upload the MOD stack file in the sidebar to generate intelligence.")
else:
    # Attempt to grab live demand, fallback to slider if blocked by WAF
    live_demand = get_live_demand()
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Online Capacity", f"{df['Capacity_MW'].sum():,.0f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.2f}")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.2f}")
    col_kpi4.metric("Total Generating Units", f"{len(df)}")

    st.markdown("---")
    
    # State Demand Control
    st.subheader("Current Grid Conditions")
    if live_demand:
        st.success(f"📡 Live SLDC Demand Connected: **{live_demand:,.0f} MW**")
        simulated_demand = st.slider("Adjust State Demand (MW) for Simulation:", min_value=1000, max_value=35000, value=live_demand, step=100)
    else:
        st.info("🌐 Cloud IP blocked by SLDC WAF. Live data disabled. Using manual simulation.")
        simulated_demand = st.slider("Simulate State Demand (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")
    tab1, tab2 = st.tabs(["🎯 Unit Deep Dive (Specific Plant Focus)", "📊 Grid Demand Zones (Macro View)"])

    # --- TAB 1: SPECIFIC UNIT TRACKER ---
    with tab1:
        parli_search = df.index[df['Generating_Station'].str.contains('Parali Unit - 06', case=False, na=False)].tolist()
        default_idx = int(parli_search[0]) if parli_search else 0
        
        selected_plant = st.selectbox("Select Generating Station:", df['Generating_Station'].unique(), index=default_idx)
        plant_data = df[df['Generating_Station'] == selected_plant].iloc[0]
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Position in Stack", f"#{plant_data['MOD_Rank']} of {len(df)}")
        sc2.metric("Variable Charge", f"₹{plant_data['Total_VC']:.4f}/kWh")
        sc3.metric("Cheaper Power Ahead", f"{plant_data['MW_Ahead_In_Queue']:,.0f} MW")
        sc4.metric("Safety Zone", str(plant_data['Demand_Zone']).split(' (')[0])

        # Risk Alert Logic
        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **HIGH RISK**: Grid demand ({simulated_demand} MW) is lower than the capacity stacked ahead of this unit ({plant_data['MW_Ahead_In_Queue']:,.0f} MW). Probable RSD or Curtailment.")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL**: Unit is actively operating on the dispatch margin.")
        else:
            st.success(f"✅ **SAFE**: State demand clears this unit's dispatch threshold.")

        colors = ['#ff4b4b' if name == selected_plant else 'rgba(100, 110, 130, 0.4)' for name in df['Generating_Station']]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df['Cumulative_MW'] - (df['Capacity_MW']/2),
            y=df['Total_VC'], width=df['Capacity_MW'], marker_color=colors, marker_line_width=0,
            text=df['Generating_Station'], hovertemplate="<b>%{text}</b><br>Total VC: ₹%{y}/kWh<br>Cumulative MW: %{customdata:.0f} MW<extra></extra>", customdata=df['Cumulative_MW']
        ))
        
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#ffcc00", annotation_text="Current Demand", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#ff4b4b", annotation_text="Unit Dispatch Threshold", annotation_position="bottom right")

        fig.update_layout(xaxis_title="Cumulative Grid Demand (MW)", yaxis_title="Total VC (₹/kWh)", template="plotly_dark", bargap=0, height=450)
        st.plotly_chart(fig, use_container_width=True)

    # --- TAB 2: MACRO ZONE ANALYSIS ---
    with tab2:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Total Capacity per 5,000 MW Demand Block", text_auto='.0f', color_discrete_sequence=px.colors.sequential.Viridis)
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total MW in Zone")
        st.plotly_chart(fig_zones, use_container_width=True)

        # FIXED: Iterating over the static ZONE_LABELS list to prevent CSV parsing errors
        for zone in ZONE_LABELS:
            zone_df = df[df['Demand_Zone'] == zone]
            if not zone_df.empty:
                with st.expander(f"📂 {zone} (Total: {zone_df['Capacity_MW'].sum():,.0f} MW)"):
                    st.dataframe(zone_df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW']], use_container_width=True, hide_index=True)
