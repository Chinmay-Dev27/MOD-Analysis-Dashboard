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

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ============================================================
# 1. PAGE SETUP & GLOBALS
# ============================================================
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

# Reference ledger is used ONLY for two safe, non-destructive purposes:
#  (1) filling in a missing/blank capacity figure (e.g. "-" for gas peakers)
#  (2) normalising a station's display name across monthly filings
# It is NEVER used to override a rate that was actually parsed from the
# uploaded file. Rates always come from the file, not from this dict.
MOD_REFERENCE_CAPACITY = {
    "SSTPS-I Sipat": 510.0, "SSTPS-II Sipat": 258.0, "Lara": 230.0,
    "KSTPS-III Korba": 108.2, "KSTPS I AND II Korba": 610.0,
    "VSTP-IV Vindhyachal": 270.48, "VSTPS-III Vindhyachal": 258.0,
    "VSTP-II Vindhyachal": 319.0, "VSTPS-V Vindhyachal": 148.89,
    "VSTP-I Vindhyachal": 410.0, "RattanIndia Power Ltd, Amravati": 1200.0,
    "GMR-Warora NTPC": 200.0, "Jindal Power Limited,Tamnar (Interstate)": 250.0,
    "MSTPS-II Mauda": 500.2, "MSTPS-I Mauda": 370.48,
    "Jindal Power Ltd,Shirpur, Dhule to MSEDCL": 100.0,
    "SWPGL Unit 1234 -Sai Wardha": 240.0, "Gadarwara": 50.0,
    "KHTPS-II Kahlgaon": 148.0,
    "VIPL UNIT-1&2 Vidarbha Industries to MSEDCL (Powerpulse)": 543.0,
    "Khaperkheda Unit - 05": 500.0, "Koradi Unit - 08 to 10": 1980.0,
    "IEPL Case-IV Ideal Energy": 180.0, "Bhusawal Unit - 06": 660.0,
    "VMPL Vidarbha Minerals to MSEDCL Manikaran Power Ltd. (MPL))": 100.0,
    "Koradi Unit - 06": 210.0, "Khargone": 50.0,
    "Chandrapur Unit - 08,09": 1000.0, "Khaperkheda Unit - 01 to 04": 840.0,
    "APML, Unit 2 & 3 (PPA-1320 MW) Adani-Tiroda": 1320.0,
    "Paras Unit - 03 & 04": 500.0, "Bhusawal Unit - 04 & 05": 1000.0,
    "Chandrapur Unit - 03 to 07": 1920.0, "Parali Unit -08": 250.0,
    "Parali Unit - 06 & 07": 500.0,
    "APML, Unit 1,4 & 5 (PPA-125 MW) Adani-Tiroda": 125.0,
    "APML, Unit 1,4 & 5 (PPA-1200 MW) Adani-Tiroda": 1200.0,
    "Solapur STPS": 616.04, "APML, Unit 1,4 & 5 (PPA-440 MW)": 440.0,
    "JSW U1, Jaigad": 300.0, "CGPL Coastal Gujarat": 760.0,
    "Bhusawal Unit - 03": 210.0, "Nasik Unit - 03 to 05": 630.0,
    "Uran GTPS (Combined cycle operation)": 672.0,
    "JGPS (APM GAS)-Gandhar": 200.0, "KAWAS (APM GAS)": 204.0,
}

# ============================================================
# 2. LIVE GRID SLDC DEMAND SCRAPER
# ============================================================
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

# ============================================================
# 3. UNIVERSAL COLUMN DETECTION
# ============================================================
def _norm(s):
    return re.sub(r'\s+', ' ', str(s).strip().lower()) if s is not None else ""

def detect_header_columns(matrix, scan_rows=15, header_window=4):
    """
    Locates the true tabular header by first finding the row that contains
    'Generating Station' in one of its own cells (not a narrative/title row),
    then builds a combined header signature only from that row plus the next
    few rows -- this handles multi-row / merged headers like the official
    SLDC template, where 'Total Variable Charge' sits one row below
    'Generating Station'. Restricting the window to right around the anchor
    (rather than scanning the whole top of the sheet) avoids false matches
    from narrative title lines like "DISCOM WISE MOD STACK OF VARIABLE
    CHARGES...", which contains the word 'discom' but isn't a column header.
    Column layout (8 vs 9 columns, rate in column H vs I, etc.) no longer
    matters because columns are matched by header text, not position.
    """
    if not matrix:
        return None, None, None, None, 0

    scan_window = matrix[:scan_rows]
    ncols = max(len(r) for r in scan_window) if scan_window else 0

    header_anchor = None
    for r_idx, row in enumerate(scan_window):
        for cell in row:
            if 'generating station' in _norm(cell):
                header_anchor = r_idx
                break
        if header_anchor is not None:
            break

    if header_anchor is None:
        return None, None, None, None, 0

    window = matrix[header_anchor:header_anchor + header_window]
    combined = ["" for _ in range(ncols)]
    for row in window:
        for i in range(ncols):
            cell = row[i] if i < len(row) else None
            t = _norm(cell)
            if t and t != 'nan':
                combined[i] += " " + t

    def find(*keywords):
        for i, t in enumerate(combined):
            if all(k in t for k in keywords):
                return i
        return None

    station_col = find('generating station')
    capacity_col = find('installed capacity') or find('capacity')
    # Universal rule: the rate always lives in the *rightmost* rate-type
    # column. Prefer "Total Variable Charge" explicitly; only fall back to
    # "Approved Variable Charge" if no Total column exists at all.
    rate_col = find('total', 'variable charge')
    if rate_col is None:
        rate_col = find('approved', 'variable charge')
    if rate_col is None:
        # last-resort universal fallback: the last populated header column
        nonempty = [i for i, t in enumerate(combined) if t.strip()]
        rate_col = nonempty[-1] if nonempty else ncols - 1

    discom_col = find('discom')

    return station_col, capacity_col, rate_col, discom_col, header_anchor

def detect_column_indices_fallback(matrix):
    """Old heuristic, kept only as a last-resort fallback if header text
    can't be found at all (e.g. a badly OCR'd PDF)."""
    col_scores = {i: {'floats': 0, 'text': 0, 'cap_ints': 0} for i in range(len(matrix[0]))}
    for row in matrix:
        for idx, cell in enumerate(row):
            if idx >= len(matrix[0]):
                continue
            cell_str = str(cell).strip()
            if not cell_str:
                continue
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

def explode_raw_matrix(rows_list):
    """Only needed for PDF-table extraction, where pdfplumber sometimes
    merges multiple stations into one multi-line cell."""
    normalized_rows = []
    for row in rows_list:
        if not row:
            continue
        cells_split = [str(cell).split('\n') if cell is not None else [""] for cell in row]
        max_splits = max(len(c) for c in cells_split)
        for c in cells_split:
            while len(c) < max_splits:
                c.append(c[-1] if len(c) == 1 else "")
        for i in range(max_splits):
            sub_row = [c[i].strip() for c in cells_split]
            normalized_rows.append(sub_row)
    return normalized_rows

# ============================================================
# 4. DATA INGESTION & HEALING
# ============================================================
def parse_capacity_cell(raw_cap, station_name):
    """Handles '-', blank, plain '630', or 'ISGS_total/DISCOM_share' formats."""
    cap_str = "" if raw_cap is None else str(raw_cap).strip()
    if cap_str in ("", "-", "nan", "None"):
        return MOD_REFERENCE_CAPACITY.get(station_name, 0.0)
    if '/' in cap_str:
        cap_str = cap_str.split('/')[-1]
    cap_match = re.search(r'[\d.]+', cap_str)
    if cap_match:
        return float(cap_match.group())
    return MOD_REFERENCE_CAPACITY.get(station_name, 0.0)

def parse_rate_cell(raw_rate):
    if raw_rate is None:
        return None
    rate_str = str(raw_rate).strip()
    match = re.search(r'\d+\.\d+|\d+', rate_str)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None

def normalize_name(name):
    """Match a parsed station name to a canonical reference name (for
    consistent display only — never touches the rate)."""
    clean = re.sub(r'[\s\-\(\)\.&_]', '', name.lower())
    for ref_key in MOD_REFERENCE_CAPACITY:
        ref_clean = re.sub(r'[\s\-\(\)\.&_]', '', ref_key.lower())
        if ref_clean == clean or (len(clean) > 4 and (ref_clean in clean or clean in ref_clean)):
            return ref_key
    return name

def parse_and_heal_data(raw_rows, target_discom="MSEDCL", is_pdf_source=False):
    if not raw_rows:
        return pd.DataFrame()

    matrix = explode_raw_matrix(raw_rows) if is_pdf_source else [
        [("" if c is None else c) for c in row] for row in raw_rows
    ]

    station_col, capacity_col, rate_col, discom_col, header_row = detect_header_columns(matrix)
    if station_col is None or rate_col is None:
        station_col, capacity_col, rate_col = detect_column_indices_fallback(matrix)
        discom_col, header_row = None, 0

    processed_data = []
    current_section = target_discom  # assume first block belongs to target unless told otherwise
    section_locked = discom_col is not None  # if there's an explicit DISCOM column, section tracking is unnecessary

    for row_idx, row in enumerate(matrix[header_row + 1:], start=header_row + 1):
        if rate_col >= len(row) or station_col >= len(row):
            continue

        station_raw = str(row[station_col]).strip()
        row_text_all = " ".join(str(c) for c in row if c not in (None, "")).lower()

        # Track section markers like "DECENTRALISED MOD STACK FOR TPCL-D"
        if not section_locked and 'mod stack for' in row_text_all:
            m = re.search(r'mod stack for\s+([a-z0-9\-]+)', row_text_all)
            if m:
                current_section = m.group(1).upper()
            continue

        if not station_raw or station_raw.lower() in ("nan", "none") or len(station_raw) <= 2:
            continue
        if any(x in station_raw.upper() for x in ['TOTAL', 'GENERATING STATION', 'OWNER TYPE', 'DISCOM', 'NOTE', 'MOD STACK']):
            continue

        parsed_rate = parse_rate_cell(row[rate_col])
        if parsed_rate is None:
            continue  # not a real data row (blank/section/footnote row) — universal filter

        # DISCOM filter: prefer an explicit DISCOM column; else use tracked section
        if section_locked:
            discom_val = str(row[discom_col]).strip().upper() if discom_col < len(row) else ""
            if target_discom and discom_val and target_discom.upper() not in discom_val:
                continue
        else:
            if target_discom and target_discom.upper() not in current_section:
                continue

        final_name = normalize_name(station_raw)
        raw_cap = row[capacity_col] if capacity_col is not None and capacity_col < len(row) else None
        final_cap = parse_capacity_cell(raw_cap, final_name)

        processed_data.append({
            'Generating_Station': final_name,
            'Capacity_MW': final_cap,
            'Total_VC': parsed_rate,
        })

    df = pd.DataFrame(processed_data).drop_duplicates(subset=['Generating_Station', 'Total_VC'])
    if df.empty:
        return df
    df = df.sort_values(by='Total_VC').reset_index(drop=True)
    df['MOD_Rank'] = df.index + 1
    df['Cumulative_MW'] = df['Capacity_MW'].cumsum()
    df['MW_Ahead_In_Queue'] = df['Cumulative_MW'] - df['Capacity_MW']
    df['Demand_Zone'] = pd.cut(df['Cumulative_MW'], bins=[0, 5000, 10000, 15000, 20000, 25000, 30000, float('inf')], labels=ZONE_LABELS)
    return df

def get_available_discoms(raw_rows, is_pdf_source=False):
    """Scan section markers / DISCOM column so the UI can offer a picker."""
    matrix = explode_raw_matrix(raw_rows) if is_pdf_source else [
        [("" if c is None else c) for c in row] for row in raw_rows
    ]
    station_col, capacity_col, rate_col, discom_col, header_row = detect_header_columns(matrix)
    found = set()
    if discom_col is not None:
        for row in matrix[header_row + 1:]:
            if discom_col < len(row):
                v = str(row[discom_col]).strip().upper()
                if v and v not in ("NAN", "NONE"):
                    found.add(v)
    else:
        for row in matrix:
            row_text = " ".join(str(c) for c in row if c not in (None, "")).lower()
            m = re.search(r'mod stack for\s+([a-z0-9\-]+)', row_text)
            if m:
                found.add(m.group(1).upper())
    return sorted(found) if found else ["MSEDCL"]

# ============================================================
# 5. PREMIUM DASHBOARD PDF ENGINE
# ============================================================
def generate_pdf_report(plant_name, df, simulated_demand):
    plant_data = df[df['Generating_Station'] == plant_name].iloc[0]
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []

    title_style = ParagraphStyle('TText', fontName='Helvetica-Bold', fontSize=16, textColor=colors.white, alignment=1)
    subtitle_style = ParagraphStyle('SText', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#D9E1F2'), alignment=1)
    section_title = ParagraphStyle('SecT', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#1F4E78'), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle('BStyle', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#262626'))
    kpi_title = ParagraphStyle('KTitle', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#595959'), alignment=1)
    kpi_value = ParagraphStyle('KVal', fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#1F4E78'), alignment=1)

    header_data = [[Paragraph("MERIT ORDER DISPATCH (MOD) STRATEGIC BRIEF", title_style)],
                   [Paragraph("State Grid Code Regulation Framework | Operational Integration Ledger", subtitle_style)]]
    header_table = Table(header_data, colWidths=[540])
    header_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#1F4E78')), ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10)]))
    story.append(header_table)
    story.append(Spacer(1, 12))

    kpi_cells = [
        [Paragraph("GRID MERIT RANK", kpi_title), Paragraph("VARIABLE COST RATE", kpi_title), Paragraph("NET METRIC CAPACITY", kpi_title)],
        [Paragraph(f"#{int(plant_data['MOD_Rank'])} of {len(df)}", kpi_value), Paragraph(f"₹{plant_data['Total_VC']:.4f}/kWh", kpi_value), Paragraph(f"{plant_data['Capacity_MW']:.1f} MW", kpi_value)]
    ]
    kpi_table = Table(kpi_cells, colWidths=[180, 180, 180])
    kpi_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F2F5F9')), ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#D9E1F2')), ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D9E1F2')), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
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
    matrix_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4A5568')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7FAFC')])]))

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

# ============================================================
# 6. DATA HANDLING LAYER
# ============================================================
DATA_FILE = "saved_mod_stack.csv"
df = pd.DataFrame()

with st.sidebar:
    st.header("⚙️ Data Source Management")
    uploaded_file = st.file_uploader("Upload raw MOD Stack (PDF or Excel)", type=["pdf", "xlsx"])
    discom_choice = st.session_state.get("discom_choice", "MSEDCL")

if uploaded_file is not None:
    file_ext = uploaded_file.name.lower()
    is_pdf = file_ext.endswith('.pdf')

    if is_pdf:
        with pdfplumber.open(uploaded_file) as pdf:
            raw_rows = []
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        raw_rows.extend(table)
    else:
        raw_excel = pd.read_excel(uploaded_file, header=None)
        raw_rows = raw_excel.values.tolist()

    available_discoms = get_available_discoms(raw_rows, is_pdf_source=is_pdf)
    with st.sidebar:
        discom_choice = st.selectbox("DISCOM / Section to isolate:", available_discoms,
                                      index=available_discoms.index("MSEDCL") if "MSEDCL" in available_discoms else 0)
        st.session_state["discom_choice"] = discom_choice

    df = parse_and_heal_data(raw_rows, target_discom=discom_choice, is_pdf_source=is_pdf)

    if not df.empty:
        df.to_csv(DATA_FILE, index=False)
        st.sidebar.success(f"✅ Isolated {discom_choice} Stack: {len(df)} stations verified, rates read directly from the file's rate column.")
    else:
        st.sidebar.error("⚠️ No rows parsed. Check that the file has a 'Generating Station' and a 'Variable Charge' header within the first 10 rows.")
elif os.path.exists(DATA_FILE):
    df = pd.read_csv(DATA_FILE)
    df['Demand_Zone'] = pd.Categorical(df['Demand_Zone'], categories=ZONE_LABELS, ordered=True)
else:
    st.sidebar.info("📂 Upload a MOD stack file (PDF or Excel) to begin. No cached data found.")

# ============================================================
# 7. DASHBOARD WORKSPACE
# ============================================================
st.title("⚡ MOD Grid Strategy & Risk Dashboard")

if not df.empty:
    live_demand = get_live_demand()

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Isolated Capacity", f"{df['Capacity_MW'].sum():,.1f} MW")
    col_kpi2.metric("Cheapest Baseload VC", f"₹{df['Total_VC'].min():.4f}/kWh")
    col_kpi3.metric("Most Expensive Peak VC", f"₹{df['Total_VC'].max():.4f}/kWh")
    col_kpi4.metric("Isolated Plants", f"{len(df)}")

    st.markdown("---")

    st.subheader("Grid Load Simulation Controls")
    if live_demand:
        st.success(f"📡 Real-Time Grid Sync Connected: **{live_demand:,.0f} MW State Demand**")
        simulated_demand = st.slider("Adjust State Demand (MW) for Operational Test Simulation:", min_value=1000, max_value=35000, value=live_demand, step=100)
    else:
        simulated_demand = st.slider("Simulate Total State Grid Demand (MW):", min_value=1000, max_value=35000, value=20000, step=100)

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs([
        "🎯 Plant Deep Dive & Report Center",
        "🗂️ Merit Order Flashcards",
        "📋 Auditable Extracted Data Ledger",
        "📊 Macro System Loading Zones",
    ])

    # ---------------- TAB 1: DEEP DIVE ----------------
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
            st.warning("⚠️ **MARGINAL DISPATCH BOUNDARY**: This plant acts as the clearing index node for the grid. Minor systemic shifts will trigger output scheduling variations.")
        else:
            st.success("✅ **SAFE DESPATCH OPERATION**: System parameters comfortably clear requirements. Unit runs under base scheduling orders.")

        bar_colors = ['#ff4b4b' if name == selected_plant else 'rgba(100, 110, 130, 0.3)' for name in df['Generating_Station']]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df['Cumulative_MW'] - (df['Capacity_MW'] / 2),
            y=df['Total_VC'],
            width=df['Capacity_MW'],
            marker_color=bar_colors,
            marker_line_width=0,
            text=df['Generating_Station'],
            hovertemplate="<b>%{text}</b><br>Variable Cost: ₹%{y:.4f}/kWh<br>Cumulative Queue: %{customdata:.0f} MW<extra></extra>",
            customdata=df['Cumulative_MW']
        ))
        fig.add_vline(x=simulated_demand, line_dash="solid", line_color="#D69E2E", annotation_text="Simulated Load Boundary", annotation_position="top left")
        fig.add_vline(x=plant_data['Cumulative_MW'], line_dash="dash", line_color="#E53E3E", annotation_text="Unit Target Anchor", annotation_position="bottom right")
        fig.update_layout(xaxis_title="Cumulative Supply Footprint Matrix (MW)", yaxis_title="Variable Charge Fee (₹/kWh)", template="plotly_dark", bargap=0, height=460)
        st.plotly_chart(fig, use_container_width=True)

    # ---------------- TAB 2: FLASHCARDS ----------------
    with tab2:
        st.subheader("Merit Order Flashcards — ranked cheapest to most expensive")

        fc1, fc2 = st.columns([2, 1])
        with fc1:
            name_filter = st.text_input("🔍 Filter by station name", "")
        with fc2:
            status_filter = st.selectbox("Filter by dispatch status", ["All", "Safe", "Marginal", "Critical / RSD"])

        card_df = df.copy()
        if name_filter:
            card_df = card_df[card_df['Generating_Station'].str.contains(name_filter, case=False, na=False)]

        def status_of(row):
            if simulated_demand <= row['MW_Ahead_In_Queue']:
                return "Critical / RSD", "#B71C1C", "🚨"
            elif simulated_demand <= row['Cumulative_MW']:
                return "Marginal", "#E65100", "⚠️"
            else:
                return "Safe", "#1B5E20", "✅"

        card_df = card_df.sort_values('MOD_Rank')
        cols_per_row = 4
        rows_of_cards = [card_df.iloc[i:i + cols_per_row] for i in range(0, len(card_df), cols_per_row)]

        rendered_any = False
        for chunk in rows_of_cards:
            cols = st.columns(cols_per_row)
            for col, (_, row) in zip(cols, chunk.iterrows()):
                label, color, icon = status_of(row)
                if status_filter != "All" and label != status_filter:
                    continue
                rendered_any = True
                with col:
                    st.markdown(
                        f"""
                        <div style="border:1px solid {color}44; border-left:6px solid {color};
                                    border-radius:10px; padding:12px 14px; margin-bottom:14px;
                                    background:linear-gradient(135deg, {color}0D, transparent);">
                            <div style="font-size:12px; color:#888; font-weight:600;">RANK #{int(row['MOD_Rank'])} of {len(df)}</div>
                            <div style="font-size:15px; font-weight:700; margin:4px 0 8px 0; min-height:38px;">{row['Generating_Station']}</div>
                            <div style="font-size:13px;">💰 Rate: <b>₹{row['Total_VC']:.4f}/kWh</b></div>
                            <div style="font-size:13px;">⚡ Capacity: <b>{row['Capacity_MW']:,.1f} MW</b></div>
                            <div style="font-size:13px;">📊 Cumulative: {row['Cumulative_MW']:,.0f} MW</div>
                            <div style="font-size:13px; margin-top:6px; color:{color}; font-weight:700;">{icon} {label}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
        if not rendered_any:
            st.info("No plants match the current filters.")

    # ---------------- TAB 3: DATA LEDGER ----------------
    with tab3:
        st.subheader("Absolute Extracted Data Audit Trail")
        st.dataframe(
            df[['MOD_Rank', 'Generating_Station', 'Capacity_MW', 'Total_VC', 'Cumulative_MW', 'Demand_Zone']],
            use_container_width=True,
            hide_index=True
        )

    # ---------------- TAB 4: ZONES ----------------
    with tab4:
        zone_summary = df.groupby('Demand_Zone', observed=True)['Capacity_MW'].sum().reset_index()
        fig_zones = px.bar(zone_summary, x='Demand_Zone', y='Capacity_MW', color='Demand_Zone', title="Aggregated Capacity Blocks per 5,000 MW Demand Interval", text_auto='.0f')
        fig_zones.update_layout(template="plotly_dark", showlegend=False, xaxis_title="", yaxis_title="Total Block Capacity (MW)")
        st.plotly_chart(fig_zones, use_container_width=True)
else:
    st.info("👈 Upload a MOD Rate Stack (PDF or Excel) in the sidebar to build the dashboard.")
