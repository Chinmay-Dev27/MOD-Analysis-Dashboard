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
