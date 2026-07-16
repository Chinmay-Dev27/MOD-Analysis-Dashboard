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

# --- 2. AUTOMATED LIVE GRID SCRAPING ---
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

# --- 3. MATRIX EXPLOSION CORRECTION ENGINES ---
def explode_raw_matrix(rows_list):
    """
    Takes a structural list of grid lists (from cell arrays) and explodes columns 
    containing newline line breaks to create perfectly normalized individual table rows.
    """
    normalized_rows = []
    for row in rows_list:
        if not row:
            continue
        # Split individual cell blocks by newline characters
        cells_split = [str(cell).split('\n') if cell is not None else [""] for cell in row]
        max_splits = max(len(c) for c in cells_split)
        
        # Synchronize cell array sizes by padding text columns
        for c in cells_split:
            while len(c) < max_splits:
                c.append(c[-1] if len(c) == 1 else "")
                
        for i in range(max_splits):
            sub_row = [c[i].strip() for c in cells_split]
            normalized_rows.append(sub_row)
            
    return pd.DataFrame(normalized_rows)

def parse_pdf_text(file_obj):
    extracted_rows = []
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if not row or any(c and "DISCOM WISE" in str(c).upper() for c in row):
                            continue
                        extracted_rows.append(row)
                        
    if not extracted_rows:
        return pd.DataFrame(columns=['Generating_Station', 'Capacity_MW', 'Total_VC'])
        
    df_raw = explode_raw_matrix(extracted_rows)
    
    # Dynamically locate structural indices
    station_col, cap_col, vc_col = 0, 1, df_raw.shape[1] - 1
    for col in df_raw.columns:
        col_str = " ".join(df_raw[col].dropna().astype(str)).lower()
        if "parali" in col_str or "chandrapur" in col_str or "nasik" in col_str:
            station_col = col
            break
            
    df_raw = df_raw.rename(columns={station_col: 'Generating_Station', df_raw.columns[station_col+2]: 'Capacity_MW', vc_col: 'Total_VC'})
    return df_raw[['Generating_Station', 'Capacity_MW', 'Total_VC']]

def process_dataframe(df):
    df.columns = ['Generating_Station', 'Capacity_MW', 'Total_VC']
    df['Generating_Station'] = df['Generating_Station'].astype(str).str.strip()
    
    # Drop structural metadata rows
    df = df[~df['Generating_Station'].str.upper().str.contains('TOTAL|GENERATING|STATION|OWNER|DISCOM|NOTE|SARAH|READING', na=False)]
    df = df[df['Generating_Station'] != ""].copy()
    
    def extract_share(mw_val):
        if pd.isna(mw_val): return 0.0
        s = str(mw_val).strip().replace(',', '')
        if s.lower() in ['-', 'xxx', '', 'nan'] or any(alpha in s.lower() for alpha in ['coal', 'gas', 'liquid']): return 0.0
        s = s.split('/')[1] if '/' in s else s
        match = re.search(r'[\d\.]+', s)
        return float(match.group()) if match else 0.0

    def extract_vc(vc_val):
        if pd.isna(vc_val): return 0.0
        s = str(vc_val).strip().replace(',', '')
        match = re.search(r'[\d\.]+', s)
        if match:
            val = float(match.group())
            return val if val < 30.0 else 0.0 # Guard filter against capacity leakage
        return 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    df['Total_VC'] = df['Total_VC'].apply(extract_vc)
    
    # Filter strictly on Variable Charge parameters to preserve zero-capacity configurations
    df = df[df['Total_VC'] > 0].copy()
    
    # Standardize Merit Curve Ranking Order
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    
    bins = [0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')]
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=bins, labels=ZONE_LABELS)
    return df

# --- 4. SIDEBAR INPUT & DATA CACHING ---
DATA_FILE = "saved_mod_stack.csv"

with st.sidebar:
    st.header("⚙️ Data Source Management")
    st.info("Upload the raw SLDC MOD Stack layout file.")
    uploaded_file = st.file_uploader("Upload MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    
df = pd.DataFrame()

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        raw_df = parse_pdf_text(uploaded_file)
        df = process_dataframe(raw_df)
    elif file_ext.endswith('.xlsx'):
        raw_excel = pd.read_excel(uploaded_file, header=None)
        raw_rows = raw_excel.values.tolist()
        df_exploded = explode_raw_matrix(raw_rows)
        
        # Locate matching index tracks dynamically
        st_idx, cap_idx, vc_idx = 1, 3, 7
        for col in df_exploded.columns:
            col_str = " ".join(df_exploded[col].dropna().astype(str)).lower()
            if "parali" in col_str or "bhusawal" in col_str:
                st_idx = col
                break
        
        final_excel_df = df_exploded[[st_idx, df_exploded.columns[st_idx+2], df_exploded.columns[df_exploded.shape[1]-1]]].copy()
        df = process_dataframe(final_excel_df)
        
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

# --- 5. EXECUTIVE ANCHOR ANALYTICS ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if df.empty:
    st.warning("👈 Please upload the primary grid dispatch dataset in the sidebar to initialize analytics.")
else:
    live_demand = get_live_demand()
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Capacity Tracked", f"{df['Capacity_MW'].sum():,.0f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.4f}/kWh")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.4f}/kWh")
    col_kpi4.metric("Total Tracked Units", f"{len(df)}")

    st.markdown("---")
    
    st.subheader("Grid Dispatch Simulation Engine")
    if live_demand:
        st.success(f"📡 Real-Time Grid Connection Active: **{live_demand:,.0f} MW**")
        simulated_demand = st.slider("Adjust State Grid Demand for Simulation (MW):", min_value=1000, max_value=35000, value=live_demand, step=100)
    else:
        st.info("🌐 Manual scheduling profile simulation active.")
        simulated_demand = st.slider("Simulate State Grid Demand Profile (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")
    tab1, tab2 = st.tabs(["🎯 Plant Deep Dive & Dispatch Risk", "📊 Macro Loading Zones"])

    # --- TAB 1: GENERATING STATION INDIVIDUAL ANCHOR TRACKING ---
    with tab1:
        # Prioritize matching regional thermal utilities immediately
        search_match = df.index[df['Generating_Station'].str.contains('Parali', case=False, na=False)].tolist()
        default_idx = int(search_match[0]) if search_match else 0
        
        selected_plant = st.selectbox("Select Target Generating Unit:", df['Generating_Station'].unique(), index=default_idx)
        plant_data = df[df['Generating_Station'] == selected_plant].iloc[0]
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Merit Rank Position", f"#{plant_data['MOD_Rank']} of {len(df)}")
        sc2.metric("Variable Charge Rate", f"₹{plant_data['Total_VC']:.4f}/kWh")
        sc3.metric("Lower-Cost Capacity Ahead", f"{plant_data['MW_Ahead_In_Queue']:,.0f} MW")
        sc4.metric("Grid Safety Tier", str(plant_data['Demand_Zone']).split(' (')[0])

        # Dispatch Safety Zone Assessment
        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **CRITICAL RISK (CURTAILED)**: Current system demand ({simulated_demand:,} MW) is lower than the cheaper capacity ahead of this unit ({plant_data['MW_Ahead_In_Queue']:,.0f} MW). Unit is outside the clearance line and will be forced into Reserve Shut Down (RSD).")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL DISPATCH BOUNDARY**: This unit is currently riding the grid margin. Small system load changes will trigger immediate schedule cycling or load variations.")
        else:
            st.success(f"✅ **SAFE DESPATCH TIER**: System load comfortably clears this rank threshold. Unit runs under base schedule requirements.")

        # Plotly Stack Step Graph
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
        
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#ffcc00", annotation_text="Simulated Demand Line", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#ff4b4b", annotation_text="Unit Clear Line", annotation_position="bottom right")

        fig.update_layout(xaxis_title="Cumulative System Loading Block (MW)", yaxis_title="Variable Charge Rate (₹/kWh)", template="plotly_dark", bargap=0, height=480)
        st.plotly_chart(fig, use_container_width=True)

    # --- TAB 2: MACRO OVERVIEW & HEADROOM LOADING ---
    with tab2:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Capacity Blocks per 5,000 MW Demand Interval", text_auto='.0f', color_discrete_sequence=px.colors.sequential.Viridis)
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
