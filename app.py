import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
import pdfplumber
import io
import re

# --- 1. SCRAPE LIVE STATE DEMAND ---
@st.cache_data(ttl=300) # Cache for 5 minutes
def get_live_demand():
    try:
        url = "https://mahasldc.in/"
        response = requests.get(url, verify=False, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Scrape the homepage text to find the "MW State Demand" line
        text = soup.get_text()
        match = re.search(r'(\d+)\s*MW State Demand', text)
        if match:
            return int(match.group(1))
        return None
    except Exception as e:
        st.error(f"Failed to fetch live demand: {e}")
        return None

# --- 2. FETCH & PARSE LATEST MOD PDF ---
@st.cache_data(ttl=86400) # Cache for 24 hours
def get_mod_data(pdf_url):
    # NOTE: PDF table extraction requires exact bounds matching. 
    # For robust automation, a fallback to a clean CSV is highly recommended if the PDF format shifts.
    try:
        response = requests.get(pdf_url, verify=False)
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            # Logic to extract tables from the first few pages
            # pdfplumber.extract_tables() would go here.
            pass
            
        # --- FALLBACK / DEMO DATA ---
        # Representing the cleaned data pipeline established previously
        csv_data = """Generating_Station,Capacity_MW,Total_VC
        SSTPS-I Sipat,510,1.4179
        RattanIndia Power Ltd Amravati,1200,2.3761
        APML Unit 1 4 & 5 Adani-Tiroda (1200),1200,3.8729
        Paras Unit - 03 & 04,500,3.9880
        Parali Unit - 06 & 07,500,4.0010
        Parali Unit -08,250,4.0060
        Bhusawal Unit - 04 & 05,1000,4.0140
        CGPL Coastal Gujarat,760,4.1473
        Chandrapur Unit - 03 to 07,300,4.2836
        Uran GTPS (Combined cycle operation),672,5.8900
        """
        df = pd.read_csv(io.StringIO(csv_data))
        df = df.sort_values(by='Total_VC').reset_index(drop=True)
        df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
        return df
    except Exception as e:
        st.error("Error parsing MOD PDF.")
        return pd.DataFrame()

# --- 3. BUILD THE DASHBOARD ---
st.set_page_config(page_title="Mahagenco Grid & MOD Monitor", layout="wide")
st.title("⚡ Grid Demand & MOD RSD Risk Dashboard")

# User inputs
col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("Grid Parameters")
    pdf_link = st.text_input("MOD Stack PDF URL", "https://mahasldc.in/assets/shared/reports/dr3_032026.pdf")
    
    live_demand = get_live_demand()
    if live_demand:
        st.success(f"Live State Demand: **{live_demand} MW**")
    else:
        st.warning("Could not fetch live demand. Using manual input.")
        
    # Slider to simulate demand drops
    simulated_demand = st.slider("Simulate State Demand (MW)", min_value=1000, max_value=35000, value=live_demand if live_demand else 20000)

with col2:
    df = get_mod_data(pdf_link)
    
    if not df.empty:
        # Find Parli's cumulative threshold
        parli_67_threshold = df[df['Generating_Station'].str.contains('Parali Unit - 06')]['Cumulative_MW'].max()
        parli_8_threshold = df[df['Generating_Station'].str.contains('Parali Unit -08')]['Cumulative_MW'].max()
        
        st.subheader("RSD Risk Assessment")
        # Logic: If state demand drops below the cumulative MW stacked up to Parli, it is at risk of being backed down.
        if simulated_demand <= parli_67_threshold:
            st.error(f"🚨 **HIGH RISK**: Simulated demand ({simulated_demand} MW) is below the MOD threshold for Parali 6 & 7 ({parli_67_threshold} MW). Likely Load Curtailment/RSD.")
        elif simulated_demand <= parli_8_threshold:
            st.warning(f"⚠️ **MODERATE RISK**: Demand is dropping close to Parali Unit 8 limits.")
        else:
            st.info(f"✅ **SAFE**: State demand is well above Parali's dispatch threshold.")

# --- 4. VISUALIZE THE STEP CURVE ---
if not df.empty:
    fig = px.line(df, x='Cumulative_MW', y='Total_VC', shape='hv', title='MOD Stack Curve vs. Current Demand')
    
    # Add a vertical line for the current state demand
    fig.add_vline(x=simulated_demand, line_dash="dash", line_color="red", annotation_text="State Demand")
    
    fig.add_scatter(x=df['Cumulative_MW'], y=df['Total_VC'], mode='markers', 
                    hovertemplate="%{customdata[0]}<br>Cum. MW: %{x}<br>VC: ₹%{y}", customdata=df[['Generating_Station']])
    fig.update_layout(template='plotly_dark')
    st.plotly_chart(fig, use_container_width=True)

