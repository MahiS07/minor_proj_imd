import pandas as pd
import re
import os

WEATHER_PHENOMENA = (
    "TSRA", "VCTS", "BLDU", "DRDU", "VCDU", "VCSS", "BLSA",
    "DU", "DS", "SS", "PO", "TS",
)


def extract_metar_phenomena(metar):
    """Extract actual weather codes without requiring other METAR groups."""
    text = str(metar).upper().replace("=", " ")
    return [
        match.group(0)
        for match in re.finditer(r"(?<![A-Z0-9])(TSRA|VCTS|BLDU|DRDU|VCDU|VCSS|BLSA|DU|DS|SS|PO|TS)(?![A-Z0-9])", text)
    ]

def get_metar_time_group(metar):
    """Return the existing DDHHMM time-group representation when available."""
    metar = str(metar).strip()
    match = re.search(r'\b(\d{6})\s*Z\b', metar, re.IGNORECASE)
    if match:
        return match.group(1)
    # Accept a missing Z when the group follows an ICAO code.
    match = re.search(r'\b[A-Z]{4}\s+(\d{6})(?:\s|$)', metar, re.IGNORECASE)
    if match:
        return match.group(1)
    match_full = re.search(r'(?<!\d)(\d{8})(\d{4})(?!\d)', metar)
    if match_full:
        date_part = match_full.group(1)
        time_part = match_full.group(2)
        dd = date_part[-2:]
        hhmm = time_part
        return f"{dd}{hhmm}"
    return None


def _extract_metar_values(metar):
    """Extract the existing wind/cloud values without requiring optional groups."""
    wind_match = re.search(r'\b(\d{3})(\d{2})(?:G(\d{2,3}))?KT\b', metar, re.IGNORECASE)
    wind_dir = int(wind_match.group(1)) if wind_match else None
    wind_gust = int(wind_match.group(3)) if wind_match and wind_match.group(3) else None
    wind_speed = None if wind_gust else (int(wind_match.group(2)) if wind_match else None)
    clouds = re.findall(
        r'\b(?:FEW|SCT|BKN|OVC)\d{3}(?:CB|TCU)?\b',
        metar,
        re.IGNORECASE,
    )
    return wind_dir, wind_gust, wind_speed, clouds


def _clean_metar_record(metar):
    """Remove only repeated generated labels, preserving the raw METAR body."""
    cleaned = str(metar).strip()
    while re.match(r'^METAR:\s*', cleaned, re.IGNORECASE):
        cleaned = re.sub(r'^METAR:\s*', '', cleaned, count=1, flags=re.IGNORECASE)
    return cleaned

def extract_metar_features(ad_warn_output_path, metar_file_path, output_path):
    """
    Extract METAR features from the METAR file based on warning validity periods.
    
    Args:
        ad_warn_output_path (str): Path to the AD warning output CSV file
        metar_file_path (str): Path to the METAR text file
        output_path (str): Path where to save the extracted features
    """
    # Read warnings
    ad_warn_df = pd.read_csv(ad_warn_output_path)

    # Read METAR lines. A malformed individual line is handled below.
    with open(metar_file_path, 'r', encoding='utf-8', errors='replace') as f:
        metar_lines = [line.strip() for line in f if line.strip()]

    def write_metar(out, metar):
        metar = _clean_metar_record(metar)
        wind_dir, wind_gust, wind_speed, clouds = _extract_metar_values(metar)
        out.write(f'  METAR: {metar}\n')
        out.write(f'    Wind Dir: {wind_dir}, Gust: {wind_gust}, Wind Speed: {wind_speed}, Clouds: {clouds}\n')

    def in_window(metar_time, start, end):
        if not metar_time or not start or not end:
            return False
        try:
            metar_value = int(metar_time)
            start_value = int(start)
            end_value = int(end)
        except (TypeError, ValueError):
            return False
        return (start_value <= metar_value <= end_value) if start_value <= end_value else (
            metar_value >= start_value or metar_value <= end_value
        )

    with open(output_path, 'w', encoding='utf-8') as out:
        for idx, row in enumerate(ad_warn_df.iterrows()):
            fcst_obs = str(row[1].get('FCST/OBS', '')).strip().upper()
            if fcst_obs != 'FCST':
                out.write(f'\nRow {idx+1}: FCST/OBS is {fcst_obs}, skipping extraction.\n')
                continue

            validity_from = re.sub(r'[^0-9]', '', str(row[1].get('Validity from', '')))[-6:]
            validity_to = re.sub(r'[^0-9]', '', str(row[1].get('Validity To', '')))[-6:]
            out.write(f'\nRow {idx+1}: Validity {validity_from} to {validity_to}\n')
            if len(validity_from) != 6 or len(validity_to) != 6:
                continue

            for metar in metar_lines:
                try:
                    metar_time = get_metar_time_group(metar)
                    if in_window(metar_time, validity_from, validity_to):
                        write_metar(out, metar)
                except (TypeError, ValueError, AttributeError):
                    continue
    
    return output_path 