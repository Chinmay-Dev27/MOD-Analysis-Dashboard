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

# Advanced PDF typography and layout components
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

# --- 2. MASTER REFERENCE LEDGER (52 TRANSACTING MSEDCL ASSETS) ---
MOD_REFERENCE_DATA = {
    "SSTPS-I Sipat": {"capacity": 510.0, "rate": 1.4327},
    "SSTPS-II Sipat": {"capacity": 258.0, "rate": 1.4467},
    "Lara": {"capacity": 230.0, "rate": 1.4539},
    "KSTPS-III Korba": {"capacity": 108.2, "rate": 1.5208},
    "KSTPS I AND II Korba": {"capacity": 610.0, "rate": 1.5421},
    "VSTP-IV Vindhyachal": {"capacity": 270.48, "rate": 2.1900},
    "VSTPS-III Vindhyachal": {"capacity": 258.0, "rate": 2.2153},
    "VSTP-II Vindhyachal": {"capacity": 319.0, "rate": 2.2472},
    "VSTPS-V Vindhyachal": {"capacity": 148.89, "rate": 2.2629},
    "VSTP-I Vindhyachal": {"capacity": 410.0, "rate": 2.3563},
    "RattanIndia Power Ltd, Amravati": {"capacity": 1200.0, "rate": 2.3718},
    "GMR-Warora NTPC": {"capacity": 200.0, "rate": 2.5165},
    "Jindal Power Limited,Tamnar (Interstate)": {"capacity": 250.0, "rate": 2.5781},
    "MSTPS-II Mauda": {"capacity": 500.2, "rate": 2.6839},
    "MSTPS-I Mauda": {"capacity": 370.48, "rate": 2.7036},
    "Jindal Power Ltd,Shirpur, Dhule to MSEDCL": {"capacity": 100.0, "rate": 2.7980},
    "SWPGL Unit 1234 -Sai Wardha": {"capacity": 240.0, "rate": 2.8682},
    "Gadarwara": {"capacity": 50.0, "rate": 2.9407},
    "KHTPS-II Kahlgaon": {"capacity": 148.0, "rate": 2.9701},
    "VIPL UNIT-1&2 Vidarbha Industries to MSEDCL (Powerpulse)": {"capacity": 543.0, "rate": 3.0410},
    "Khaperkheda Unit - 05": {"capacity": 500.0, "rate": 3.1890},
    "Koradi Unit - 08 to 10": {"capacity": 1980.0, "rate": 3.2840},
    "IEPL Case-IV Ideal Energy": {"capacity": 180.0, "rate": 3.4150},
    "Bhusawal Unit - 06": {"capacity": 660.0, "rate": 3.4410},
    "VMPL Vidarbha Minerals to MSEDCL Manikaran Power Ltd. (MPL))": {"capacity": 100.0, "rate": 3.5103},
    "Koradi Unit - 06": {"capacity": 210.0, "rate": 3.5200},
    "Khargone": {"capacity": 50.0, "rate": 3.5336},
    "Chandrapur Unit - 08,09": {"capacity": 1000.0, "rate": 3.6260},
    "Khaperkheda Unit - 01 to 04": {"capacity": 840.0, "rate": 3.6490},
    "APML, Unit 2 & 3 (PPA-1320 MW) Adani-Tiroda": {"capacity": 1320.0, "rate": 3.9074},
    "Paras Unit - 03 & 04": {"capacity": 500.0, "rate": 3.9470},
    "Bhusawal Unit - 04 & 05": {"capacity": 1000.0, "rate": 4.0610},
    "Chandrapur Unit - 03 to 07": {"capacity": 1920.0, "rate": 4.1330},
    "Parali Unit -08": {"capacity": 250.0, "rate": 4.1330},
    "Parali Unit - 06 & 07": {"capacity": 500.0, "rate": 4.1340},
    "APML, Unit 1,4 & 5 (PPA-125 MW) Adani-Tiroda": {"capacity": 125.0, "rate": 4.5127},
    "APML, Unit 1,4 & 5 (PPA-1200 MW) Adani-Tiroda": {"capacity": 1200.0, "rate": 4.5127},
    "Solapur STPS": {"capacity": 616.04, "rate": 4.5246},
    "APML, Unit 1,4 & 5 (PPA-440 MW)": {"capacity": 440.0, "rate": 4.5727},
    "JSW U1, Jaigad": {"capacity": 300.0, "rate": 4.6424},
    "CGPL Coastal Gujarat": {"capacity": 760.0, "rate": 4.8760},
    "Bhusawal Unit - 03": {"capacity": 210.0, "rate": 4.9470},
    "Nasik Unit - 03 to 05": {"capacity": 630.0, "rate": 5.9800},
    "Uran GTPS (Combined cycle operation)": {"capacity": 672.0, "rate": 6.4290},
    "JGPS (APM GAS)-Gandhar": {"capacity": 200.0, "rate": 7.2593},
    "KAWAS (APM GAS)": {"capacity": 204.0, "rate": 7.2904},
    "Uran GTPS (Open cycle operation)": {"capacity": 0.0, "rate": 8.9690},
    "KAWAS (RLNG)": {"capacity": 0.0, "rate": 9.5398},
    "JGPS (RLNG) Gandhar": {"capacity": 0.0, "rate": 10.0521},
    "JGPS (NAPM-COM GAS)-Gandhar": {"capacity": 0.0, "rate": 17.3000},
    "KAWAS (NAPM-COM GAS)": {"capacity": 0.0, "rate": 18.1763},
    "KAWAS (LQ)": {"capacity": 0.0, "rate": 24.8621}
}

# --- 3. LIVE GRID SLDC DEMAND SCRAPER ---
@st.cache_data(ttl=300)
def get_live_demand():
    try:
        url = "https://mahasldc.in/"
        response = requests.get(url, headers=HEADERS, verify=False, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text()
        match = re.search(r'(\d+)\s*MW State Demand', text, re.IGNORECASE)
        if match: return int(match.group(1))
        return None
    except Exception:
        return None

# --- 4. DATA INGESTION MATRIX EXPLODER ---
def explode_raw_matrix(rows_list):
    normalized_rows = []
    for row in rows_list:
        if not row: continue
        cells_split = [str(cell).split('\n') if cell is not None else [""] for cell in row]
        max_splits = max(len(c) for c in cells_split)
        
        for c in cells_split:
            while len(c) < max_splits:
                c.append(c[-1] if len(c) == 1 else "")
                
        for i in range(max_splits):
            sub_row = [c[i].strip() for c in cells_split]
            normalized_rows.append(sub_row)
    return normalized_rows

def detect_column_indices(matrix):
    col_scores = {i: {'floats': 0, 'text': 0, 'cap_ints': 0} for i in range(len(matrix[0]))}
    for row in matrix:
        for idx, cell in enumerate(row):
            if idx >= len(matrix[0]): continue
            cell_str = str(cell).strip()
            if not cell_str: continue
            
            col_scores[idx]['text'] += len(cell_str)
            if re.search(r'\b\d+\.\d{3,4}\b', cell_str):
                col_scores[idx]['floats'] += 1
            if '/' in cell_str or any(int(num) > 55 for num in re.findall(r'\b\d+\b', cell_str)):
                col_scores[idx]['cap_ints'] += 1
                
    vc_col = max(col_scores, key=lambda k: col_scores[k]['floats'])
    rem_cols = [k for k in col_scores if k != vc_col]
    st_col = max(rem_cols, key=lambda k: col_scores[k]['text'])
    cap_col = max([k for k in col_scores if k != vc_col and k != st_col], key=lambda k: col_scores[k]['cap_ints'])
    
    return st_col, cap_col, vc_col

def parse_and_heal_data(raw_rows):
    if not raw_rows: return pd.DataFrame()
    exploded = explode_raw_matrix(raw_rows)
    st_col, cap_col, vc_col = detect_column_indices(exploded)
    
    processed_data = []
    for row in exploded:
        station_name = str(row[st_col]).strip()
        if not station_name or len(station_name) <= 2 or any(x in station_name.upper() for x in ['TOTAL', 'GENERATING STATION', 'OWNER TYPE', 'DISCOM', 'NOTE']):
            continue
            
        # FIXED: Protect public sector NTPC plants from being dropped by the private TPC filter tag
        if "NTPC" not in station_name.upper():
            if any(p_tag in station_name.upper() for p_tag in ['AEML', 'TPOL', 'BEST', 'TATA', 'DHARIWAL', 'ADTPS', 'IDEAL ENERGY TO', 'TPC']):
                continue
            
        rate_match = re.search(r'\b\d+\.\d{2,4}\b', str(row[vc_col]))
        if not rate_match: continue
        parsed_rate = float(rate_match.group())
        
        matched_key = None
        for ref_key in MOD_REFERENCE_DATA:
            ref_clean = re.sub(r'[\s\-\(\)\.&_]', '', ref_key.lower())
            st_clean = re.sub(r'[\s\-\(\)\.&_]', '', station_name.lower())
            if ref_clean in st_clean or st_clean in ref_clean:
                matched_key = ref_key
                break
                
        if matched_key:
            final_name = matched_key
            final_cap = MOD_REFERENCE_DATA[matched_key]['capacity']
            final_rate = MOD_REFERENCE_DATA[matched_key]['rate']
        else:
            final_name = station_name
            final_rate = parsed_rate
            cap_str = str(row[cap_col]).split('/')[1] if '/' in str(row[cap_col]) else str(row[cap_col])
            cap_match = re.search(r'[\d\.]+', cap_str)
            final_cap = float(cap_match.group()) if cap_match else 0.0
            
        processed_data.append({
            'Generating_Station': final_name,
            'Capacity_MW': final_cap,
            'Total_VC': final_rate
        })
        
    df = pd.DataFrame(processed_data).drop_duplicates(subset=['Generating_Station', 'Total_VC'])
    df = df[df['Generating_Station'] != '2'].copy()
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=[0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')], labels=ZONE_LABELS)
    return df

# --- 5. PREMIUM DASHBOARD PDF ENGINE ---
def generate_pdf_report(plant_name, df, simulated_demand):
    plant_data = df[df['Generating_Station'] == plant_name].iloc[0]
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TText', fontName='Helvetica-Bold', fontSize=16, textColor=colors.white, alignment=1)
    subtitle_style = ParagraphStyle('SText', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#D9E1F2'), alignment=1)
    section_title = ParagraphStyle('SecT', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#1F4E78'), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle('BStyle', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#262626'))
    kpi_title = ParagraphStyle('KTitle', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#595959'), alignment=1)
    kpi_value = ParagraphStyle('KVal', fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#1F4E78'), alignment=1)
    
    header_data = [[Paragraph("MERIT ORDER DISPATCH (MOD) STRATEGIC BRIEF", title_style)],
                   [Paragraph("State Grid Code Regulation Framework | Operational Integration Ledger", subtitle_style)]]
    header_table = Table(header_data, colWidths=[540])
    header_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#1F4E78')), ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10)]))
    story.append(header_table)
    story.append(Spacer(1, 12))
    
    kpi_cells = [
        [Paragraph("GRID MERIT RANK", kpi_title), Paragraph("VARIABLE COST RATE", kpi_title), Paragraph("NET METRIC CAPACITY", kpi_title)],
        [Paragraph(f"#{int(plant_data['MOD_Rank'])} of {len(df)}", kpi_value), Paragraph(f"₹{plant_data['Total_VC']:.4f}/kWh", kpi_value), Paragraph(f"{plant_data['Capacity_MW']:.1f} MW", kpi_value)]
    ]
    kpi_table = Table(kpi_cells, colWidths=[180, 180, 180])
    kpi_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F2F5F9')), ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#D9E1F2')), ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D9E1F2')), ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6)]))
    story.append(kpi_table)
    story.append(Spacer(1, 12))
    
    status, status_color, status_desc = "SAFE DESPATCH PROFILE", "#1B5E20", "The asset safely clears the lower-cost backlog queue and runs continuously under standard grid parameters."
    if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
        status, status_color, status_desc = "CRITICAL RSD / CURTAILED BOUNDARY", "#B71C1C", "System loading fails to clear the economic baseline required for this asset. High risk of forced Reserve Shut Down (RSD)."
    elif simulated_demand <= plant_data['Cumulative_MW']:
        status, status_color, status_desc = "SYSTEM MARGINAL BALANCING NODE", "#E65100", "Asset sets the current clearing price threshold. Unit subject to cyclical regulatory load adjustments."
        
    analysis_paragraph = Paragraph(f"<b>Operational Assessment Summary:</b> Target asset <b>{plant_name}</b> is evaluated under a system demand threshold of <b>{simulated_demand:,} MW</b>. The unit maintains a <font color='{status_color}'><b>{status}</b></font> posture. {status_desc}", body_style)
    
    matrix_data = [
        ["Operational Parameter Attribute", "Value Mapping Metrics"],
        ["Lower-Cost Supply Backlog Queue", f"{plant_data['MW_Ahead_In_Queue']:,.1f} MW"],
        ["Cumulative Grid Anchor Clearance", f"{plant_data['Cumulative_MW']:,.1f} MW"],
        ["Grid Loading Dispatch Category", str(plant_data['Demand_Zone']).split(':')[-1].strip()]
    ]
    matrix_table = Table(matrix_data, colWidths=[220, 320])
    matrix_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4A5568')), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')), ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5), ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F7FAFC')])]))
    
    story.append(KeepTogether([Paragraph("1. System Dispatch Assessment Matrix", section_title), analysis_paragraph, Spacer(1, 6), matrix_table]))
    story.append(Spacer(1, 12))
    
    x_steps = [0] + list(df['Cumulative_MW'])
    y_steps = list(df['Total_VC']) + [list(df['Total_VC'])[-1]]
    
    fig, ax = plt.subplots(figsize=(6.5, 2.5), facecolor='#F8FAFC')
    ax.set_facecolor('#FFFFFF')
    ax.step(x_steps, y_steps, where='post', color='#1F4E78', linewidth=2.0, label='MSEDCL Stack')
    ax.axvspan(plant_data['MW_Ahead_In_Queue'], plant_data['Cumulative_MW'], color='#E53E3E', alpha=0.4, label='Selected Unit Target')
    ax.axvline(x=simulated_demand, color='#D69E2E', linestyle='-', linewidth=1.5, label=f'Demand ({simulated_demand:,} MW)')
    ax.set_title("True Economic Merit Order Step Vector Curve", fontsize=9, fontweight='bold')
    ax.set_xlabel("Cumulative System Supply Footprint (MW)", fontsize=8)
    ax.set_ylabel("Variable Charge Fee (₹/kWh)", fontsize=8)
    ax.grid(True, linestyle=':', alpha=0.5)
    ax.legend(fontsize=7, loc='upper left')
    plt.tight_layout()
    
    chart_buf = io.BytesIO()
    plt.savefig(chart_buf, format='png', dpi=300)
    chart_buf.seek(0)
    plt.close()
    
    story.append(KeepTogether([Paragraph("2. Graphical Supply Stack Curve Analysis", section_title), Spacer(1, 4), Image(chart_buf, width=540, height=207)]))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- 6. DATA HANDLING LAYERING ---
DATA_FILE = "saved_mod_stack.csv"
df = pd.DataFrame()

with st.sidebar:
    st.header("⚙️ Data Source Management")
    uploaded_file = st.file_uploader("Upload raw MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    if file_ext.endswith('.pdf'):
        with pdfplumber.open(uploaded_file) as pdf:
            raw_rows = []
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables: raw_rows.extend(table)
        df = parse_and_heal_data(raw_rows)
    elif file_ext.endswith('.xlsx'):
        raw_excel = pd.read_excel(uploaded_file, header=None)
        df = parse_and_heal_data(raw_excel.values.tolist())
        
    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Isolated MSEDCL Stack: {len(df)} stations verified.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
else:
    processed_fallback = []
    for k, v in MOD_REFERENCE_DATA.items():
        processed_fallback.append({'Generating_Station': k, 'Capacity_MW': v['capacity'], 'Total_VC': v['rate']})
    df = pd.DataFrame(processed_fallback).sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=[0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')], labels=ZONE_LABELS)
    df.to_csv(DATA_FILE, index=False)
    st.sidebar.info(f"📂 Initialized master reference ledger ({len(df)} units online).")

# --- 7. DASHBOARD WORKSPACE ---
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if not df.empty:
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
        simulated_demand = st.slider("Simulate Total State Grid Demand (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")
    
    tab1, tab2, tab3 = st.tabs(["🎯 Plant Deep Dive & Report Center", "📋 Auditable Extracted Data Ledger", "📊 Macro System Loading Zones"])

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

        pdf_bytes = generate_pdf_report(selected_plant, df, simulated_demand)
        st.download_button(
            label=f"📥 Download Premium Strategic Analytics PDF Report for {selected_plant}",
            data=pdf_bytes,
            file_name=f"MOD_Executive_Report_{selected_plant.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )
        st.markdown("<br>", unsafe_allow_html=True)

        if simulated_demand <= plant_data['MW_Ahead_In_Queue']:
            st.error(f"🚨 **CRITICAL RISK (UNSCHEDULED)**: System demand profile ({simulated_demand:,} MW) is too low. The plant is blocked out by {plant_data['MW_Ahead_In_Queue'] - simulated_demand:,.1f} MW of lower-cost supply. Expected Status: Reserve Shut Down (RSD).")
        elif simulated_demand <= plant_data['Cumulative_MW']:
            st.warning(f"⚠️ **MARGINAL DISPATCH BOUNDARY**: This plant acts as the clearing index node for the grid. Minor systemic shifts will trigger output scheduling variations.")
        else:
            st.success(f"✅ **SAFE DESPATCH OPERATION**: System parameters comfortably clear requirements. Unit runs under base scheduling orders.")

        # Interactive High-Fidelity Step Curve
        colors = ['#ff4b4b' if name == selected_plant else 'rgba(100, 110, 130, 0.3)' for name in df['Generating_Station']]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df['Cumulative_MW'] - (df['Capacity_MW']/2),
            y=df['Total_VC'], 
            width=df['Capacity_MW'], 
            marker_color=colors, 
            marker_line_width=0,
            text=df['Generating_Station'], 
            hovertemplate="<b>%{text}</b><br>Variable Cost: ₹%{y:.4f}/kWh<br>Cumulative Queue: %{customdata:.0f} MW<extra></extra>", 
            customdata=df['Cumulative_MW']
        ))
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#D69E2E", annotation_text="Simulated Load Boundary", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#E53E3E", annotation_text="Unit Target Anchor", annotation_position="bottom right")
        fig.update_layout(xaxis_title="Cumulative Supply Footprint Matrix (MW)", yaxis_title="Variable Charge Fee (₹/kWh)", template="plotly_dark", bargap=0, height=460)
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        st.subheader("Absolute Extracted Data Audit Trail")
        st.dataframe(
            df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW', 'Demand_Zone']], 
            use_container_width=True, 
            hide_index=True
        )

    with tab3:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Capacity Blocks per 5,000 MW Demand Interval", text_auto='.0f')
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total Block Capacity (MW)")
        st.plotly_chart(fig_zones, use_container_width=True)
