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

# Advanced PDF components configuration
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
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
    data = []
    for row in raw_rows:
        if not row or len(row) < 3:
            continue
        
        row_strs = [str(c) if c is not None else "" for c in row]
        station_text, capacity_text, vc_text = "", "", ""
        
        # Identify station column dynamically by text character density
        for c in row_strs:
            c_clean = c.strip()
            if len(c_clean) > 5 and not re.match(r'^[\d\.\s\-\/:]+$', c_clean):
                if not any(x in c_clean.upper() for x in ['OWNER TYPE', 'GENERATING STATION', 'TYPE OF FUEL', 'TOTAL']):
                    station_text = c
                    break
                    
        # Identify variable charge column by decimal presence
        decimal_cols = []
        for c in row_strs:
            if re.search(r'\b\d+\.\d{2,4}\b', c):
                decimal_cols.append(c)
        if decimal_cols:
            vc_text = decimal_cols[-1]
            
        # Identify capacity column from intermediate residual strings
        for c in row_strs:
            if c != station_text and c != vc_text:
                if re.search(r'\b\d{2,4}\b', c) and not '.' in c:
                    capacity_text = c
                    break
        
        if station_text and vc_text:
            st_lines = [line.strip() for line in station_text.split('\n') if len(line.strip()) > 2]
            cap_lines = re.findall(r'\b\d+\b', capacity_text)
            vc_lines = re.findall(r'\b\d+\.\d{2,4}\b', vc_text)
            
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
    df.columns = ['Generating_Station', 'Capacity_MW', 'Total_VC']
    df['Generating_Station'] = df['Generating_Station'].astype(str).str.strip()
    
    # Drop structural metadata text artifacts
    df = df[~df['Generating_Station'].str.upper().str.contains('TOTAL|GENERATING|STATION|OWNER|DISCOM|NOTE|SARAH|READING', na=False)]
    
    # CRITICAL FIX: Explicitly exclude separate private/municipal city distribution stacks from the main grid stack
    private_discom_tags = ['AEML', 'TATA-D', 'BEST', 'TPOL', 'ADTPS', 'DHARIWAL', 'TPC U-', 'IDEAL ENERGY TO']
    df = df[~df['Generating_Station'].str.upper().str.contains('|'.join(private_discom_tags), na=False)].copy()
    
    def extract_share(mw_val):
        if pd.isna(mw_val): return 0.0
        s = str(mw_val).strip().replace(',', '')
        if s.lower() in ['-', 'xxx', '', 'nan'] or any(alpha in s.lower() for alpha in ['coal', 'gas', 'liquid']): return 0.0
        s = s.split('/')[1] if '/' in s else s
        match = re.search(r'[\d\.]+', s)
        return float(match.group()) if match else 0.0

    df['Capacity_MW'] = df['Capacity_MW'].apply(extract_share)
    df['Total_VC'] = pd.to_numeric(df['Total_VC'], errors='coerce')
    df = df[df['Total_VC'] > 0].copy()
    
    # Establish precise Merit Order sorting baseline
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    
    bins = [0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')]
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=bins, labels=ZONE_LABELS)
    return df

# --- 4. EXECUTIVE DASHBOARD-STYLE PDF GENERATOR ---
def generate_pdf_report(plant_name, df, simulated_demand):
    plant_data = df[df['Generating_Station'] == plant_name].iloc[0]
    buffer = io.BytesIO()
    
    # Configure production canvas layout (0.5 inch safety margins)
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    # Custom typography configurations
    title_style = ParagraphStyle('HeaderTitle', fontName='Helvetica-Bold', fontSize=18, textColor=colors.white, alignment=1)
    subtitle_style = ParagraphStyle('HeaderSub', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#D9E1F2'), alignment=1)
    section_title = ParagraphStyle('SecTitle', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#1F4E78'), spaceBefore=10, spaceAfter=6)
    body_style = ParagraphStyle('BodyTextCustom', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#262626'))
    kpi_title = ParagraphStyle('KpiTitle', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#595959'), alignment=1)
    kpi_value = ParagraphStyle('KpiVal', fontName='Helvetica-Bold', fontSize=15, textColor=colors.HexColor('#1F4E78'), alignment=1)
    
    # Component A: Premium Dashboard Title Header Block
    header_data = [[Paragraph("MERIT ORDER DISPATCH (MOD) STRATEGIC BRIEF", title_style)],
                   [Paragraph("State Grid Code Regulation Framework | Operational Integration Ledger", subtitle_style)]]
    header_table = Table(header_data, colWidths=[540])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1F4E78')),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('ALIGN', (0,0), (-1,-1), 'CENTER')
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # Component B: Executive Web-Style KPI Metric Widgets Row
    kpi_cells = [
        [
            Paragraph("GRID MERIT RANK", kpi_title),
            Paragraph("VARIABLE COST RATE", kpi_title),
            Paragraph("NET METRIC CAPACITY", kpi_title)
        ],
        [
            Paragraph(f"#{int(plant_data['MOD_Rank'])} of {len(df)}", kpi_value),
            Paragraph(f"₹{plant_data['Total_VC']:.4f}/kWh", kpi_value),
            Paragraph(f"{plant_data['Capacity_MW']:.1f} MW", kpi_value)
        ]
    ]
    kpi_table = Table(kpi_cells, colWidths=[180, 180, 180])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F2F5F9')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#D9E1F2')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D9E1F2')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 15))
    
    # Component C: KeepTogether Block for Summary and Data Matrix
    status, status_color, status_desc = "SAFE DESPATCH PREROGATIVE", "#1B5E20", "The asset safely clears the lower-cost backlog queue and runs continuously under basic grid parameters."
    if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
        status, status_color, status_desc = "CRITICAL RSD / CURTAILED BOUNDARY", "#B71C1C", "System loading fails to clear the economic baseline required for this asset. High risk of forced Reserve Shut Down (RSD)."
    elif simulated_demand <= plant_data['Cumulative_MW']:
        status, status_color, status_desc = "SYSTEM MARGINAL BALANCING NODE", "#E65100", "Asset sets the current clearing price threshold. Unit subject to cyclical regulatory load adjustments."
        
    analysis_paragraph = Paragraph(f"<b>Operational Assessment Summary:</b> Target asset <b>{plant_name}</b> is checked under a system demand threshold of <b>{simulated_demand:,} MW</b>. The unit maintains a <font color='{status_color}'><b>{status}</b></font> posture. {status_desc}", body_style)
    
    matrix_data = [
        ["Operational Parameter Attribute", "Value Mapping Metrics"],
        ["Lower-Cost Supply Backlog Queue", f"{plant_data['MW_Ahead_In_Queue']:,.1f} MW"],
        ["Cumulative Grid Anchor Clearance", f"{plant_data['Cumulative_MW']:,.1f} MW"],
        ["Grid Loading Dispatch Category", str(plant_data['Demand_Zone'])]
    ]
    matrix_table = Table(matrix_data, colWidths=[220, 320])
    matrix_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4A5568')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F7FAFC')])
    ]))
    
    story.append(KeepTogether([
        Paragraph("1. System Dispatch Assessment Matrix", section_title),
        analysis_paragraph,
        Spacer(1, 8),
        matrix_table
    ]))
    story.append(Spacer(1, 15))
    
    # Component D: Mathematically Flawless Merit Order Step Plot Block
    x_steps = [0]
    y_steps = []
    for _, r in df.iterrows():
        x_steps.append(r['Cumulative_MW'])
        y_steps.append(r['Total_VC'])
    y_steps.append(y_steps[-1]) # Close alignment tail
    
    fig, ax = plt.subplots(figsize=(6.5, 2.8), facecolor='#F8FAFC')
    ax.set_facecolor('#FFFFFF')
    ax.step(x_steps, y_steps, where='post', color='#1F4E78', linewidth=2.0, label='MSEDCL Merit Order Stack')
    
    # Highlight precise unit span block
    ax.axvspan(plant_data['MW_Ahead_In_Queue'], plant_data['Cumulative_MW'], color='#E53E3E', alpha=0.4, label='Selected Unit Target')
    ax.axvline(x=simulated_demand, color='#D69E2E', linestyle='-', linewidth=1.5, label=f'Grid Demand ({simulated_demand:,} MW)')
    
    ax.set_title("True Economic Merit Order Step Vector Curve", fontsize=9, fontweight='bold', color='#2D3748')
    ax.set_xlabel("Cumulative System Supply Footprint (MW)", fontsize=8)
    ax.set_ylabel("Variable Charge Fee (₹/kWh)", fontsize=8)
    ax.grid(True, linestyle=':', alpha=0.5, color='#CBD5E0')
    ax.legend(fontsize=7, loc='upper left')
    plt.tight_layout()
    
    chart_buf = io.BytesIO()
    plt.savefig(chart_buf, format='png', dpi=300)
    chart_buf.seek(0)
    plt.close()
    
    story.append(KeepTogether([
        Paragraph("2. Graphical Supply Stack Curve Analysis", section_title),
        Spacer(1, 4),
        Image(chart_buf, width=540, height=232)
    ]))
    story.append(Spacer(1, 15))
    
    # Component E: Competitive Neighboring Node Context
    idx = df[df['Generating_Station'] == plant_name].index[0]
    slice_df = df.iloc[max(0, idx - 1):min(len(df), idx + 2)]
    
    neigh_rows = [["Rank", "Generating Station Context", "VC (₹/kWh)", "Block MW"]]
    for _, r in slice_df.iterrows():
        lbl = f"{r['Generating_Station']} (*Target*)" if r['Generating_Station'] == plant_name else str(r['Generating_Station'])
        neigh_rows.append([f"#{int(r['MOD_Rank'])}", lbl, f"{r['Total_VC']:.4f}", f"{r['Capacity_MW']:.1f}"])
        
    neigh_table = Table(neigh_rows, colWidths=[50, 290, 110, 90])
    neigh_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2C5282')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    
    story.append(KeepTogether([
        Paragraph("3. Immediate Competitive Marginal Context", section_title),
        Spacer(1, 4),
        neigh_table
    ]))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- 5. DATA INGESTION LAYERING ---
DATA_FILE = "saved_mod_stack.csv"
REPOSITORY_SOURCE = "2026-07-16T14-12_export.csv"

with st.sidebar:
    st.header("⚙️ Data Source Management")
    st.info("Ingest the latest grid source files below.")
    uploaded_file = st.file_uploader("Upload raw MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    
df = pd.DataFrame()

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        df = process_dataframe(parse_pdf_text(uploaded_file))
    elif file_ext.endswith('.xlsx'):
        raw_excel = pd.read_excel(uploaded_file, header=None)
        df = process_dataframe(robust_matrix_parser(raw_excel.values.tolist()))
        
    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Cleaned MSEDCL Stack: {len(df)} stations isolated.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
    st.sidebar.success(f"📂 Operational Stack Cached: {len(df)} units active.")
elif os.path.exists(REPOSITORY_SOURCE):
    # Auto-initialize workspace cleanly if primary source upload cache is clean
    df = pd.read_csv(REPOSITORY_SOURCE)
    df = process_dataframe(df)
    df.to_csv(DATA_FILE, index=False)
    st.sidebar.info(f"📂 Workspace Sync: Running primary MSEDCL stack dataset ({len(df)} units).")

if not df.empty:
    with st.sidebar.expander("🔍 Filtered MSEDCL Merit Ledger"):
        st.dataframe(df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC']], hide_index=True)

# --- 6. INTERACTIVE EXECUTIVE WORKSPACE ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if df.empty:
    st.warning("👈 Please ingest the source grid stack document in the sidebar to generate operational models.")
else:
    live_demand = get_live_demand()
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total MSEDCL Capacity", f"{df['Capacity_MW'].sum():,.1f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.4f}/kWh")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.4f}/kWh")
    col_kpi4.metric("Isolated MSEDCL Plants", f"{len(df)}")

    st.markdown("---")
    
    st.subheader("Grid Load Simulation Controls")
    if live_demand:
        st.success(f"📡 Real-Time Grid Sync Connected: **{live_demand:,.0f} MW State Demand**")
        simulated_demand = st.slider("Adjust State Demand (MW) for Operational Test Simulation:", min_value=1000, max_value=35000, value=live_demand, step=100)
    else:
        st.info("🌐 Manual load control mode active.")
        simulated_demand = st.slider("Simulate Total State Grid Demand (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")
    tab1, tab2 = st.tabs(["🎯 Plant Deep Dive & Report Center", "📊 Macro System Loading Zones"])

    # --- TAB 1: ASSET SPECIFIC INTELLIGENCE & DOWNLOAD GENERATION ---
    with tab1:
        search_match = df.index[df['Generating_Station'].str.contains('Parali', case=False, na=False)].tolist()
        default_idx = int(search_match[0]) if search_match else 0
        
        selected_plant = st.selectbox("Select Target Unit for Deep-Dive Analysis:", df['Generating_Station'].unique(), index=default_idx)
        plant_data = df[df['Generating_Station'] == selected_plant].iloc[0]
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Economic Merit Rank", f"#{plant_data['MOD_Rank']} of {len(df)}")
        sc2.metric("Variable Charge (VC)", f"₹{plant_data['Total_VC']:.4f}/kWh")
        sc3.metric("Cheaper MW Ahead in Queue", f"{plant_data['MW_Ahead_In_Queue']:,.1f} MW")
        sc4.metric("Grid Merit Block", str(plant_data['Demand_Zone']).split(' (')[0])

        # Generate premium dashboard PDF package
        pdf_bytes = generate_pdf_report(selected_plant, df, simulated_demand)
        st.download_button(
            label=f"📥 Download Premium Strategic Analytics PDF Report for {selected_plant}",
            data=pdf_bytes,
            file_name=f"MOD_Executive_Report_{selected_plant.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )
        st.markdown("<br>", unsafe_allow_html=True)

        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **CRITICAL RISK (UNSCHEDULED / OUTSIDE LINE)**: System demand profile ({simulated_demand:,} MW) will fail to clear this unit's rank slot. The plant is blocked out by {plant_data['MW_Ahead_In_Queue'] - simulated_demand:,.1f} MW of lower-cost supply. Expected Status: Reserve Shut Down (RSD).")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL STATE (DISPATCH CROSSING NODE)**: This plant acts as the clearing index node for the grid. Minor systemic load shifts will trigger direct cyclical scheduling adjustments.")
        else:
            st.success(f"✅ **SAFE DESPATCH OPERATION**: System parameters comfortably exceed clearing requirements. Asset operates continuously under standard base scheduling orders.")

        # Interactive High-Fidelity Step Curve Visualization
        colors = ['#ff4b4b' if name == selected_plant else 'rgba(100, 110, 130, 0.3)' for name in df['Generating_Station']]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df['Cumulative_MW'] - (df['Capacity_MW']/2),
            y=df['Total_VC'], 
            width=df['Capacity_MW'], 
            marker_color=colors, 
            marker_line_width=0,
            text=df['Generating_Station'], 
            hovertemplate="<b>%{text}</b><br>Variable Cost: ₹%{y:.4f}/kWh<br>Cumulative Queue Footprint: %{customdata:.0f} MW<extra></extra>", 
            customdata=df['Cumulative_MW']
        ))
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#D69E2E", annotation_text="Simulated Load Boundary", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#E53E3E", annotation_text="Unit Target Anchor", annotation_position="bottom right")
        fig.update_layout(xaxis_title="Cumulative Supply Footprint Matrix (MW)", yaxis_title="Variable Charge Fee (₹/kWh)", template="plotly_dark", bargap=0, height=460)
        st.plotly_chart(fig, use_container_width=True)

    # --- TAB 2: MACRO GRID OVERVIEW ---
    with tab2:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Supply Blocks per 5,000 MW Demand Interval", text_auto='.0f', color_discrete_sequence=px.colors.sequential.Viridis)
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total Block Capacity (MW)")
        st.plotly_chart(fig_zones, use_container_width=True)

        for zone in ZONE_LABELS:
            zone_df = df[df['Demand_Zone'] == zone]
            if not zone_df.empty:
                with st.expander(f"📂 {zone} (Subtotal Volume: {zone_df['Capacity_MW'].sum():,.1f} MW)"):
                    st.dataframe(
                        zone_df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW']], 
                        use_container_width=True, 
                        hide_index=True
                    )
