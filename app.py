import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pdfplumber
import re
import os
import io
import requests
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup

# ReportLab imports for executive-level PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

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

# --- 2. AUTOMATED SYSTEM DEMAND SCRAPER ---
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

# --- 3. ADVANCED CELL-MATRIX EXTRACTION ENGINE ---
def robust_matrix_parser(raw_rows):
    """
    Parses messy mixed cell grids, explodes grouped multi-line vertical strings,
    and cleanly matches text elements to their numerical variables.
    """
    data = []
    for row in raw_rows:
        if not row or len(row) < 3:
            continue
        
        row_strs = [str(c) if c is not None else "" for c in row]
        station_text, capacity_text, vc_text = "", "", ""
        
        # 1. Identify station column dynamically by character density
        for c in row_strs:
            c_clean = c.strip()
            if len(c_clean) > 5 and not re.match(r'^[\d\.\s\-\/:]+$', c_clean):
                if not any(x in c_clean.upper() for x in ['OWNER TYPE', 'GENERATING STATION', 'TYPE OF FUEL', 'TOTAL']):
                    station_text = c
                    break
                    
        # 2. Identify variable charge column by decimal presence
        decimal_cols = []
        for i, c in enumerate(row_strs):
            if re.search(r'\b\d+\.\d{2,4}\b', c):
                decimal_cols.append(c)
        if decimal_cols:
            vc_text = decimal_cols[-1]
            
        # 3. Identify capacity column from intermediate residual strings
        for c in row_strs:
            if c != station_text and c != vc_text:
                if re.search(r'\b\d{2,4}\b', c) and not '.' in c:
                    capacity_text = c
                    break
        
        # 4. Extract tokens and explode aligned arrays
        if station_text and vc_text:
            st_lines = [line.strip() for line in station_text.split('\n') if len(line.strip()) > 2]
            cap_lines = re.findall(r'\b\d+\b', capacity_text)
            vc_lines = re.findall(r'\b\d+\.\d{2,4}\b', vc_text)
            
            # Clean out structural leaks
            vc_lines = [v for v in vc_lines if float(v) < 30.0]
            st_lines = [s for s in st_lines if not any(k in s.upper() for k in ['GENERATING STATION', 'OWNER TYPE'])]
            
            if st_lines and vc_lines:
                max_len = max(len(st_lines), len(vc_lines))
                while len(st_lines) < max_len:
                    st_lines.append(st_lines[-1] if st_lines else "Unknown Station")
                while len(vc_lines) < max_len:
                    vc_lines.append(vc_lines[-1] if vc_lines else "0.0")
                while len(cap_lines) < max_len:
                    cap_lines.append("0")
                    
                for i in range(max_len):
                    data.append({
                        'Generating_Station': st_lines[i],
                        'Capacity_MW': cap_lines[i],
                        'Total_VC': float(vc_lines[i])
                    })
    return pd.DataFrame(data)

def parse_pdf_text(file_obj):
    extracted_rows = []
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    extracted_rows.extend(table)
    return robust_matrix_parser(extracted_rows)

def process_dataframe(df):
    def extract_share(mw_val):
        if pd.isna(mw_val): return 0.0
        s = str(mw_val).strip().replace(',', '')
        if s.lower() in ['-', 'xxx', '', 'nan'] or any(alpha in s.lower() for alpha in ['coal', 'gas', 'liquid']): return 0.0
        s = s.split('/')[1] if '/' in s else s
        match = re.search(r'[\d\.]+', s)
        return float(match.group()) if match else 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    df = df[df['Total_VC'] > 0].copy()
    
    # Sort strictly by economic merit rank order
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    
    bins = [0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')]
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=bins, labels=ZONE_LABELS)
    return df

# --- 4. EXECUTIVE PDF REPORT GENERATOR ENGINE ---
def generate_pdf_report(plant_name, df, simulated_demand):
    plant_data = df[df['Generating_Station'] == plant_name].iloc[0]
    buffer = io.BytesIO()
    
    # Standard printable parameters configuration (540 points wide layout)
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('RepTitle', fontName='Helvetica-Bold', fontSize=20, textColor=colors.HexColor('#1F4E78'), spaceAfter=4, alignment=1)
    subtitle_style = ParagraphStyle('RepSub', fontName='Helvetica-Oblique', fontSize=9, textColor=colors.HexColor('#595959'), spaceAfter=15, alignment=1)
    section_heading = ParagraphStyle('RepSec', fontName='Helvetica-Bold', fontSize=13, textColor=colors.HexColor('#1F4E78'), spaceBefore=14, spaceAfter=6, borderColor=colors.HexColor('#1F4E78'), borderWidth=0.5, borderPadding=4)
    body_style = ParagraphStyle('RepBody', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#333333'), spaceAfter=8)
    head_style = ParagraphStyle('RepHead', fontName='Helvetica-Bold', fontSize=10, textColor=colors.white)
    
    # 1. Document Banner Header
    story.append(Paragraph("STRATEGIC DISPATCH & MERIT ORDER (MOD) ANALYSIS", title_style))
    story.append(Paragraph("State Grid Regulation Compliance Ledger | System Loading Risk Assessment", subtitle_style))
    
    # 2. Executive Assessment Breakdown
    story.append(Paragraph("1. Executive Operational Summary", section_heading))
    status, status_color, status_desc = "SAFE DISPATCH PROFILE", "#2E7D32", f"The generating unit comfortably clears the system loading baseline of {simulated_demand:,} MW and operates continuously under normal scheduling orders."
    if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
        status, status_color, status_desc = "HIGH CURTAILMENT / RSD RISK", "#C62828", f"System loading load parameter ({simulated_demand:,} MW) is insufficient to clear cheaper capacity stacked ahead of this unit ({plant_data['MW_Ahead_In_Queue']:,.0f} MW). Expected Status: forced Reserve Shut Down (RSD) / Curtailment."
    elif simulated_demand <= plant_data['Cumulative_MW']:
        status, status_color, status_desc = "MARGINAL CLEARING NODE", "#EF6C00", "The generating asset is riding the dispatch boundary margin. Minor real-time load cycling will result in direct output schedule adjustments."
        
    summary_text = f"This strategic evaluation covers the operational status of <b>{plant_name}</b> under simulated grid loading constraints of <b>{simulated_demand:,} MW</b>. The asset is categorized as holding a <b><font color='{status_color}'>{status}</font></b> posture. {status_desc}"
    story.append(Paragraph(summary_text, body_style))
    
    # 3. Technical Parameters Table
    story.append(Paragraph("2. Operational Matrix Parameters", section_heading))
    table_data = [
        [Paragraph("Parameter Attribute Key", head_style), Paragraph("Value Metric Mapping", head_style)],
        ["Generating Utility Station Name", str(plant_data['Generating_Station'])],
        ["Merit Order Queue Rank Positioning", f"Rank #{int(plant_data['MOD_Rank'])} of {len(df)} units"],
        ["Asset Variable Charge (VC Rate)", f"Rs. {plant_data['Total_VC']:.4f} / kWh"],
        ["Net Capacity Block Contribution", f"{plant_data['Capacity_MW']:.1f} MW"],
        ["Lower-Cost Grid Backlog Ahead", f"{plant_data['MW_Ahead_In_Queue']:,.1f} MW"],
        ["Cumulative Grid Anchor Clearance", f"{plant_data['Cumulative_MW']:,.1f} MW"],
        ["System Dispatch Clearance Category", str(plant_data['Demand_Zone']).split(':')[1].strip() if ':' in str(plant_data['Demand_Zone']) else str(plant_data['Demand_Zone'])]
    ]
    t = Table(table_data, colWidths=[180, 360])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (1,0), colors.HexColor('#1F4E78')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F9FAFB')])
    ]))
    story.append(t)
    
    # 4. Integrated Graphic Analysis
    story.append(Paragraph("3. Visualized Dispatch Curve Positioning", section_heading))
    fig, ax = plt.subplots(figsize=(6.5, 3.0), facecolor='#F9FAFB')
    ax.set_facecolor('#FFFFFF')
    ax.step(df['Cumulative_MW'], df['Total_VC'], where='post', color='#1F4E78', linewidth=1.8, label='Grid MOD Curve')
    ax.axvline(x=simulated_demand, color='#EF6C00', linestyle='-', linewidth=1.2, label=f'Demand Line ({simulated_demand:,} MW)')
    ax.axvline(x=plant_data['Cumulative_MW'], color='#C62828', linestyle='--', linewidth=1.2, label=f"Target Unit Anchor")
    ax.set_title("System Merit Order Integration Vector", fontsize=9, fontweight='bold', color='#1F4E78')
    ax.set_xlabel("Cumulative System Capacity (MW)", fontsize=8)
    ax.set_ylabel("Variable Cost Rate (Rs./kWh)", fontsize=8)
    ax.grid(True, linestyle=':', alpha=0.4, color='#BBBBBB')
    ax.legend(fontsize=7, loc='upper left')
    plt.tight_layout()
    
    chart_buf = io.BytesIO()
    plt.savefig(chart_buf, format='png', dpi=300)
    chart_buf.seek(0)
    plt.close()
    story.append(Image(chart_buf, width=460, height=212))
    
    # 5. Market Neighborhood Context
    story.append(Paragraph("4. Competitive Dispatch Neighborhood (Market Context)", section_heading))
    idx = df[df['Generating_Station'] == plant_name].index[0]
    n_df = df.iloc[max(0, idx - 2):min(len(df), idx + 3)].copy()
    
    n_rows = [[Paragraph("Rank", head_style), Paragraph("Generating Station Context", head_style), Paragraph("VC (Rs./kWh)", head_style), Paragraph("Capacity (MW)", head_style)]]
    for _, r in n_df.iterrows():
        label = f"{r['Generating_Station']} (Target)" if r['Generating_Station'] == plant_name else str(r['Generating_Station'])
        n_rows.append([f"#{int(r['MOD_Rank'])}", label, f"{r['Total_VC']:.4f}", f"{r['Capacity_MW']:.1f}"])
        
    nt = Table(n_rows, colWidths=[50, 270, 120, 100])
    nt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4A777A')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E0E0E0')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(nt)
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- 5. SIDEBAR CONTROL & PERSISTENCE ---
DATA_FILE = "saved_mod_stack.csv"
FALLBACK_RAW = "2026-07-16T14-01_export.csv"

with st.sidebar:
    st.header("⚙️ Data Source Management")
    st.info("Upload the primary grid source file below.")
    uploaded_file = st.file_uploader("Upload MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    
df = pd.DataFrame()

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        raw_df = parse_pdf_text(uploaded_file)
        df = process_dataframe(raw_df)
    elif file_ext.endswith('.xlsx'):
        raw_excel = pd.read_excel(uploaded_file, header=None)
        df = process_dataframe(robust_matrix_parser(raw_excel.values.tolist()))
        
    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Extracted and verified {len(df)} units successfully.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
    st.sidebar.success(f"📂 Operational Stack Restored: {len(df)} units active.")
elif os.path.exists(FALLBACK_RAW):
    # Standardize data instantly using high-fidelity fallback arrays if empty
    df = pd.read_csv(FALLBACK_RAW)
    df = process_dataframe(df.rename(columns={'Generating_Station': 'Generating_Station', 'Capacity_MW': 'Capacity_MW', 'Total_VC': 'Total_VC'}))
    df.to_csv(DATA_FILE, index=False)
    st.sidebar.info(f"📂 Workspace Initialized using standard repository ledger ({len(df)} units).")

if not df.empty:
    with st.sidebar.expander("🔍 Operational Merit Order Ledger"):
        st.dataframe(df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC']], hide_index=True)

# --- 6. EXECUTIVE ANALYTICS DASHBOARD ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if df.empty:
    st.warning("👈 Please upload the primary grid dispatch dataset in the sidebar to initialize analytics.")
else:
    live_demand = get_live_demand()
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Capacity Tracked", f"{df['Capacity_MW'].sum():,.1f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.4f}/kWh")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.4f}/kWh")
    col_kpi4.metric("Total Operating Units", f"{len(df)}")

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

    # --- TAB 1: INDIVIDUAL GENERATING STATION RISK ANALYTICS ---
    with tab1:
        # Prioritize local contextual matching elements cleanly
        search_match = df.index[df['Generating_Station'].str.contains('Parali', case=False, na=False)].tolist()
        default_idx = int(search_match[0]) if search_match else 0
        
        selected_plant = st.selectbox("Select Target Generating Unit:", df['Generating_Station'].unique(), index=default_idx)
        plant_data = df[df['Generating_Station'] == selected_plant].iloc[0]
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Merit Rank Position", f"#{plant_data['MOD_Rank']} of {len(df)}")
        sc2.metric("Variable Charge Rate", f"₹{plant_data['Total_VC']:.4f}/kWh")
        sc3.metric("Lower-Cost Capacity Ahead", f"{plant_data['MW_Ahead_In_Queue']:,.1f} MW")
        sc4.metric("Grid Safety Tier", str(plant_data['Demand_Zone']).split(' (')[0])

        # Actionable Download Interface for Executive Reports
        pdf_bytes = generate_pdf_report(selected_plant, df, simulated_demand)
        st.download_button(
            label=f"📥 Download Strategic Analytics Report for {selected_plant} (PDF)",
            data=pdf_bytes,
            file_name=f"MOD_Strategic_Report_{selected_plant.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # Dispatch Status Alerts
        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **CRITICAL RISK (UNSCHEDULED / CURTAILED)**: Current system demand ({simulated_demand:,} MW) is lower than the cheaper capacity ahead of this unit ({plant_data['MW_Ahead_In_Queue']:,.1f} MW). Unit is forced into Reserve Shut Down (RSD).")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL DISPATCH BOUNDARY**: This unit is currently riding the grid margin. Small system load changes will trigger immediate cyclic output changes.")
        else:
            st.success(f"✅ **SAFE DESPATCH TIER**: System load comfortably clears this rank threshold. Unit runs under base schedule requirements.")

        # Interactive Plotly Step Chart
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
        
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#ffcc00", annotation_text="Simulated Demand", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#ff4b4b", annotation_text="Unit Clearance Limit", annotation_position="bottom right")
        fig.update_layout(xaxis_title="Cumulative System Loading Block (MW)", yaxis_title="Variable Charge Rate (₹/kWh)", template="plotly_dark", bargap=0, height=480)
        st.plotly_chart(fig, use_container_width=True)

    # --- TAB 2: MACRO OVERVIEW & ZONE ANALYSIS ---
    with tab2:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Capacity Blocks per 5,000 MW Demand Interval", text_auto='.0f', color_discrete_sequence=px.colors.sequential.Viridis)
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total Block Capacity (MW)")
        st.plotly_chart(fig_zones, use_container_width=True)

        for zone in ZONE_LABELS:
            zone_df = df[df['Demand_Zone'] == zone]
            if not zone_df.empty:
                with st.expander(f"📂 {zone} (Subtotal: {zone_df['Capacity_MW'].sum():,.1f} MW)"):
                    st.dataframe(
                        zone_df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW']], 
                        use_container_width=True, 
                        hide_index=True
                    )
