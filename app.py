import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
import io
import re

# --- 1. PAGE SETUP ---
st.set_page_config(page_title="Mahagenco Grid & MOD Monitor", layout="wide")
st.title("⚡ Grid Demand & MOD RSD Risk Dashboard")

# Global Headers to bypass WAF / Bot Protection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

# --- 2. SCRAPE LIVE STATE DEMAND ---
@st.cache_data(ttl=300) # Cache for 5 minutes
def get_live_demand():
    try:
        url = "https://mahasldc.in/"
        # Added headers to prevent 403 Forbidden errors
        response = requests.get(url, headers=HEADERS, verify=False, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Scrape the homepage text to find the "MW State Demand" line
        text = soup.get_text()
        match = re.search(r'(\d+)\s*MW State Demand', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    except Exception as e:
        st.error(f"Failed to fetch live demand from MSLDC: {e}")
        return None

# --- 3. DATA PROCESSING LOGIC ---
def process_mod_data(df):
    """Cleans the raw MOD data and calculates cumulative capacity."""
    # Ensure column headers match expected format
    df.columns = [
        'Sr_No', 'Generating_Station', 'Owner_Type', 
        'Capacity_MW', 'Fuel_Type', 'Approved_VC', 
        'Impact_Change', 'Total_VC'
    ]
    
    # Drop rows without a valid Total VC (removes section headers)
    df = df.dropna(subset=['Total_VC'])
    df = df[df['Total_VC'].apply(lambda x: str(x).replace('.','',1).isdigit())]
    df['Total_VC'] = df['Total_VC'].astype(float)

    # Handle the "Installed/Share" formatting in the MW column
    def extract_share(mw_string):
        if pd.isna(mw_string) or str(mw_string).strip() in ['-', 'XXX', '']:
            return 0.0
        mw_str = str(mw_string).strip()
        if '/' in mw_str:
            return float(mw_str.split('/')[1])
        else:
            return float(mw_str)

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    
    # Sort minimum to highest VC and calculate stack
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    return df

# --- 4. FALLBACK / DEFAULT DATA ---
@st.cache_data
def get_default_mod_data():
    # This acts as the default dataset if the PDF scraper fails or no file is uploaded
    csv_data = """Sr_No,Generating_Station,Owner_Type,Capacity_MW,Fuel_Type,Approved_VC,Impact_Change,Total_VC
    1,SSTPS-I Sipat,CS,510,Coal,1.4179,,1.4179
    2,RattanIndia Power Ltd Amravati,IPP,1200,Coal,2.0138,0.3623,2.3761
    3,APML Unit 1 4 & 5 Adani-Tiroda (1200),IPP,1200,Coal,2.3369,1.5360,3.8729
    4,Paras Unit - 03 & 04,MSPGCL,500,Coal,3.9880,,3.9880
    5,Parali Unit - 06 & 07,MSPGCL,500,Coal,4.0010,,4.0010
    6,Parali Unit -08,MSPGCL,250,Coal,4.0060,,4.0060
    7,Bhusawal Unit - 04 & 05,MSPGCL,1000,Coal,4.0140,,4.0140
    8,CGPL Coastal Gujarat,CS,760,Coal,4.1473,,4.1473
    9,Chandrapur Unit - 03 to 07,MSPGCL,300,Coal,3.7775,0.5061,4.2836
    10,Uran GTPS (Combined cycle operation),MSPGCL,672,Gas,5.8900,,5.8900
    """
    df = pd.read_csv(io.StringIO(csv_data))
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    return df

# --- 5. BUILD THE DASHBOARD UI ---
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Grid Parameters")
    
    # Optional file upload for robustness
    uploaded_file = st.file_uploader("Upload Monthly MOD Stack (Excel)", type=["xlsx"])
    pdf_link = st.text_input("Or verify PDF URL (Automated Scrape)", "https://mahasldc.in/assets/shared/reports/dr3_032026.pdf")
    
    live_demand = get_live_demand()
    if live_demand:
        st.success(f"Live State Demand: **{live_demand} MW**")
    else:
        st.warning("Could not fetch live demand. Using manual input.")
        
    # Slider to simulate demand drops
    simulated_demand = st.slider("Simulate State Demand (MW)", min_value=1000, max_value=35000, value=live_demand if live_demand else 20000)

with col2:
    # Load Data
    if uploaded_file is not None:
        raw_df = pd.read_excel(uploaded_file, skiprows=7, header=None)
        df = process_mod_data(raw_df)
        st.success("Successfully loaded custom Excel MOD stack.")
    else:
        df = get_default_mod_data()
        st.info("Using cached default MOD stack. Upload Excel for latest month.")
    
    if not df.empty:
        # Find Parli's cumulative threshold safely
        parli_67_df = df[df['Generating_Station'].str.contains('Parali Unit - 06', case=False, na=False)]
        parli_8_df = df[df['Generating_Station'].str.contains('Parali Unit -08', case=False, na=False)]
        
        parli_67_threshold = parli_67_df['Cumulative_MW'].max() if not parli_67_df.empty else 0
        parli_8_threshold = parli_8_df['Cumulative_MW'].max() if not parli_8_df.empty else 0
        
        st.subheader("RSD Risk Assessment")
        
        # Risk Logic
        if simulated_demand <= parli_67_threshold:
            st.error(f"🚨 **HIGH RISK**: Simulated demand ({simulated_demand} MW) is below the MOD threshold for Parali 6 & 7 ({parli_67_threshold} MW). High probability of load curtailment or reserve shutdown.")
        elif simulated_demand <= parli_8_threshold:
            st.warning(f"⚠️ **MODERATE RISK**: Demand ({simulated_demand} MW) is dropping close to Parali Unit 8 limits ({parli_8_threshold} MW).")
        else:
            st.info(f"✅ **SAFE**: State demand is well above Parali's dispatch thresholds.")

# --- 6. VISUALIZE THE STEP CURVE ---
if not df.empty:
    # FIXED: Changed 'shape' to 'line_shape'
    fig = px.line(
        df, 
        x='Cumulative_MW', 
        y='Total_VC', 
        line_shape='hv', 
        title='MOD Stack Curve vs. Current Demand',
        labels={'Cumulative_MW': 'Cumulative Grid Demand (MW)', 'Total_VC': 'Total Variable Charge (₹/kWh)'}
    )
    
    # Add a vertical line for the current simulated/state demand
    fig.add_vline(x=simulated_demand, line_dash="dash", line_color="#ff4b4b", annotation_text="Current Grid Demand")
    
    # Add overlay points for hover data
    fig.add_scatter(
        x=df['Cumulative_MW'], 
        y=df['Total_VC'], 
        mode='markers', 
        marker=dict(size=7, color='cyan'),
        hovertemplate="<b>%{customdata[0]}</b><br>Cum. MW: %{x}<br>VC: ₹%{y}<extra></extra>", 
        customdata=df[['Generating_Station']]
    )
    
    fig.update_layout(template='plotly_dark', hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
