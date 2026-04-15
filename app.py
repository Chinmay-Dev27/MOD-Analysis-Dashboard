import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup
import io
import re
import pdfplumber

# --- 1. PAGE SETUP ---
st.set_page_config(page_title="Mahagenco Grid & MOD Monitor", layout="wide")
st.title("⚡ Grid Demand & MOD RSD Risk Dashboard")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- 2. DATA EXTRACTION LOGIC (PDF & EXCEL) ---
def parse_pdf_text(file_obj):
    """Extracts text from PDF and parses the dirty MOD stack format using regex."""
    text = ""
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
            
    data = []
    # More forgiving Regex: Makes capacity optional in case spacing is completely lost in PDF extraction
    pattern = re.compile(r'(.*?)\s+([\d\.\/]+|-|xxx)?\s*(Coal|Gas|Coal/Oil/Gas)\s+([\d\.\-]+)\s+([\d\.\-]+)?\s*([\d\.\-]+)$', re.IGNORECASE)
    
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        match = pattern.search(line)
        if match:
            station_raw = match.group(1).strip()
            # Strip the leading Sr. No from the station name
            station = re.sub(r'^\d+\s+', '', station_raw)
            
            capacity = match.group(2) if match.group(2) else "0"
            
            try:
                total_vc = float(match.group(6))
                data.append({
                    'Generating_Station': station, 
                    'Capacity_MW': capacity, 
                    'Total_VC': total_vc
                })
            except ValueError:
                continue
            
    return pd.DataFrame(data)

def clean_capacity_and_calculate(df):
    """Processes the capacity column and builds the cumulative stack."""
    def extract_share(mw_string):
        if pd.isna(mw_string): return 0.0
        mw_str = str(mw_string).strip()
        if mw_str.lower() in ['-', 'xxx', '']: return 0.0
        
        # Handle "Installed/Share" format
        target_str = mw_str.split('/')[1] if '/' in mw_str else mw_str
        target_str = target_str.replace(',', '')
        match = re.search(r'[\d\.]+', target_str)
        return float(match.group()) if match else 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    df = df[df['Capacity_MW'] > 0] # Drop 0 MW entries
    
    # Sort minimum to highest VC and calculate stack
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    
    # Calculate the center point for the bar chart blocks
    df['Bar_Center'] = df['Cumulative_MW'] - (df['Capacity_MW'] / 2)
    return df

# --- 3. BUILD THE DASHBOARD UI ---
col1, col2 = st.columns([1, 2.5])

with col1:
    st.subheader("Data Upload")
    uploaded_file = st.file_uploader("Upload Monthly MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    
    # Dummy live demand for cloud fallback
    st.info("Live MSLDC Scrape disabled on cloud to prevent IP blocks. Using manual simulation.")
    simulated_demand = st.slider("Simulate State Demand (MW)", min_value=1000, max_value=35000, value=20000, step=100)

with col2:
    df = pd.DataFrame()
    if uploaded_file is not None:
        # Normalize the file extension to lowercase to prevent silent failures
        file_ext = uploaded_file.name.lower()
        
        if file_ext.endswith('.pdf'):
            raw_df = parse_pdf_text(uploaded_file)
            df = clean_capacity_and_calculate(raw_df)
            st.success("Successfully parsed PDF data.")
            
        elif file_ext.endswith('.xlsx'):
            raw_df = pd.read_excel(uploaded_file, skiprows=7, header=None)
            raw_df.columns = ['Sr_No', 'Generating_Station', 'Owner_Type', 'Capacity_MW', 'Fuel_Type', 'Approved_VC', 'Impact_Change', 'Total_VC']
            raw_df = raw_df.dropna(subset=['Total_VC'])
            df = clean_capacity_and_calculate(raw_df)
            st.success("Successfully parsed Excel data.")

    if not df.empty:
        # Check specific risk thresholds
        parli_67 = df[df['Generating_Station'].str.contains('Parali Unit - 06', case=False, na=False)]
        parli_8 = df[df['Generating_Station'].str.contains('Parali Unit -08', case=False, na=False)]
        
        p67_thresh = parli_67['Cumulative_MW'].max() if not parli_67.empty else 0
        p8_thresh = parli_8['Cumulative_MW'].max() if not parli_8.empty else 0
        
        if simulated_demand <= p67_thresh:
            st.error(f"🚨 **HIGH RISK**: Demand ({simulated_demand} MW) is below Parali 6&7 threshold ({p67_thresh:.1f} MW).")
        elif simulated_demand <= p8_thresh:
            st.warning(f"⚠️ **MODERATE RISK**: Demand ({simulated_demand} MW) is dropping near Parali 8 limits ({p8_thresh:.1f} MW).")
        else:
            st.success(f"✅ **SAFE**: State demand is above your unit's dispatch thresholds.")

# --- 4. THE PRO MOD STACK VISUALIZATION ---
if not df.empty:
    # Color code to make Parli stand out instantly on the chart
    colors = ['#ff4b4b' if 'Parali' in str(name) else '#1f77b4' for name in df['Generating_Station']]
    
    fig = go.Figure()
    
    # Create the variable-width bar chart
    fig.add_trace(go.Bar(
        x=df['Bar_Center'],
        y=df['Total_VC'],
        width=df['Capacity_MW'],
        marker_color=colors,
        marker_line_color='rgba(255, 255, 255, 0.2)',
        marker_line_width=1,
        text=df['Generating_Station'],
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Capacity: %{width} MW<br>"
            "Total VC: ₹%{y}/kWh<br>"
            "Cumulative Stack: %{customdata:.1f} MW"
            "<extra></extra>" # Removes the secondary box
        ),
        customdata=df['Cumulative_MW']
    ))

    # Add the current demand line
    fig.add_vline(x=simulated_demand, line_dash="dash", line_color="#ffcc00", 
                  annotation_text=f"Current Demand ({simulated_demand} MW)", 
                  annotation_position="top left")

    fig.update_layout(
        title='Merit Order Despatch (MOD) Block Stack',
        xaxis_title='Cumulative Grid Demand (MW)',
        yaxis_title='Total Variable Charge (₹/kWh)',
        template='plotly_dark',
        bargap=0, # Removes gaps between bars so it looks like a continuous curve
        hovermode="closest",
        height=600
    )
    
    st.plotly_chart(fig, use_container_width=True)
elif uploaded_file is None:
    st.info("👈 Upload a PDF or Excel MOD Stack to view the dashboard.")
