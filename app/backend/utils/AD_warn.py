import pandas as pd
import re
import os

def parse_warning_file(filepath, station_code=None):
    pd.set_option('display.max_rows', None)

    with open(filepath, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    data = []
    warning_header = re.compile(
        r"^(?P<station>[A-Z]{4})\s*(?P<issue>\d{6})Z?\s*"
        r"AD\s*(?:WRNG|WARNING)\s*\d{1,2}\b(?P<header>.*)$",
        re.IGNORECASE,
    )

    # Build each warning from its header through the next warning header. This
    # accepts wrapped bodies without changing the stored warning fields.
    header_indexes = [
        index for index, line in enumerate(lines)
        if warning_header.match(re.sub(r"\s+", " ", line).strip())
    ]
    for header_position, header_index in enumerate(header_indexes):
        header_line = re.sub(r"\s+", " ", lines[header_index]).strip()
        header_match = warning_header.match(header_line)
        if not header_match:
            continue

        next_header = (
            header_indexes[header_position + 1]
            if header_position + 1 < len(header_indexes)
            else len(lines)
        )
        record_text = " ".join(lines[header_index:next_header])
        main_line = header_line
        station = header_match.group("station").upper()
        issue_time = f"{header_match.group('issue')}Z"
        validity_from, validity_to = "", ""
        valid_match = re.search(
            r"\bVALID\s*=??\s*(\d{6,8})\s*/\s*(\d{6,8})\s*Z?\b",
            record_text,
            re.IGNORECASE,
        )
        if valid_match:
            validity_from = f"{valid_match.group(1)}Z"
            validity_to = f"{valid_match.group(2)}Z"

        body_match = re.search(r"\bVALID\b.*?\s+(.*)$", record_text, re.IGNORECASE)
        wx_line = body_match.group(1) if body_match else record_text
        wx_line = re.sub(r"\s+", " ", wx_line).strip().upper()

        try:
            wind_dir_dict = {
                "N": 0, "NNE": 20, "NE": 50, "ENE": 70,
                "E": 90, "ESE": 110, "SE": 140, "SSE": 160,
                "S": 180, "SSW": 200, "SW": 230, "WSW": 250,
                "W": 270, "WNW": 290, "NW": 320, "NNW": 340,
                "WEST": 270, "EAST": 90, "SOUTH": 180, "NORTH": 0
            }

            wind_speed_match = re.search(r"\bSFC\s*WSPD\s*(\d+)\s*KT\b", wx_line)
            wind_speed = f"{wind_speed_match.group(1)}KT" if wind_speed_match else ""

            gust_match = re.search(r"\bMAX\s*(\d+)\s*(?:KT)?\b", wx_line)
            gust = f"{gust_match.group(1)}KT" if gust_match else ""

            wind_dir_match = re.search(r"\bFROM\s*([A-Z0-9]+)\b", wx_line)
            wind_dir_str = wind_dir_match.group(1) if wind_dir_match else ""
            wind_dir_num = (
                int(wind_dir_str)
                if wind_dir_str.isdigit()
                else wind_dir_dict.get(wind_dir_str, "")
            )

            sig_wx = ""
            wx_patterns = {
                "HVY TSRA": "+TSRA", "FBL TSRA": "-TSRA", "MOD TSRA": "TSRA", "TSRA": "TSRA",
                "HVY TS": "+TS", "FBL TS": "-TS", "MOD TS": "TS", "TS": "TS"
            }
            for pattern, code in wx_patterns.items():
                if re.search(r"\b" + pattern.replace(" ", r"\s+") + r"\b", wx_line):
                    sig_wx = code
                    break

            # Keep dust and sand warnings separate from existing TS parsing.
            dust_match = re.search(r"\bDS\b", wx_line, re.IGNORECASE)
            sand_match = re.search(r"\bSS\b", wx_line, re.IGNORECASE)
            dust_storm = dust_match.group(0).upper() if dust_match else ""
            sand_storm = sand_match.group(0).upper() if sand_match else ""

            fcst_obs = "FCST" if re.search(r"\bFCST\b", wx_line) else "OBS" if re.search(r"\bOBS(?:D)?\b", wx_line) else ""

            data.append({
                "Station": station,
                "Issue date/time": issue_time,
                "Validity from": validity_from,
                "Validity To": validity_to,
                "Wind dir (deg)": wind_dir_num,
                "Wind Speed": wind_speed,
                "Gust": gust,
                "Significant Wx": sig_wx,
                "Dust Storm": dust_storm,
                "Sand Storm": sand_storm,
                "FCST/OBS": fcst_obs
            })
        except (TypeError, ValueError):
            continue

    df = pd.DataFrame(data)

    def round_down_to_half_hour(timestr):
        if not isinstance(timestr, str) or not re.fullmatch(r"\d{4,8}Z?", timestr):
            return ""
        z = 'Z' if timestr.endswith('Z') else ''
        timestr = timestr.rstrip('Z')
        if len(timestr) < 4: return timestr + z
        try:
            prefix, hhmm = timestr[:-4], timestr[-4:]
            hour, minute = int(hhmm[:2]), int(hhmm[2:])
        except ValueError:
            return ""
        minute = 0 if minute < 30 else 30
        return f"{prefix}{hour:02d}{minute:02d}{z}"

    def round_up_to_next_half_hour(timestr):
        if not isinstance(timestr, str) or not re.fullmatch(r"\d{4,8}Z?", timestr):
            return ""
        z = 'Z' if timestr.endswith('Z') else ''
        timestr = timestr.rstrip('Z')
        if len(timestr) < 4: return timestr + z
        
        try:
            if len(timestr) >= 6:  # Format like "132345" or "132345Z"
                day = int(timestr[-6:-4])
                hour, minute = int(timestr[-4:-2]), int(timestr[-2:])
            else:  # Format like "2345" or "2345Z"
                day = 1  # Default day
                hour, minute = int(timestr[-4:-2]), int(timestr[-2:])
        except ValueError:
            return ""
        
        if minute == 0 or minute == 30:
            return timestr + z
        elif minute < 30:
            minute = 30
        else:
            hour = (hour + 1) % 24
            minute = 0
            # If hour rolled over to 00, increment the day
            if hour == 0:
                day = (day % 30) + 1  # Assuming 30 days max, adjust if needed
        
        # Format the result based on original format
        if len(timestr) >= 6:
            prefix = timestr[:-6] if len(timestr) > 6 else ""
            return f"{prefix}{day:02d}{hour:02d}{minute:02d}{z}"
        else:
            return f"{hour:02d}{minute:02d}{z}"

    def fix_2400(timestr):
        if not isinstance(timestr, str) or not timestr:
            return ""
        z = 'Z' if timestr.endswith('Z') else ''
        timestr = timestr.rstrip('Z')
        
        # Handle case where time is "2400" (should become "0000" of next day)
        if timestr.endswith('2400'):
            if len(timestr) >= 6:  # Format like "132400"
                day = int(timestr[:2])
                # Extract prefix (everything before the day)
                prefix = timestr[:-6]
                new_day = (day % 30) + 1  # Increment day, assuming 30 days max
                return f"{prefix}{new_day:02d}0000{z}"
            else:  # Format like "2400"
                return f"0000{z}"
        
        return timestr + z

    df["Validity from"] = df["Validity from"].astype(str).apply(round_down_to_half_hour).apply(fix_2400)
    df["Validity To"] = df["Validity To"].astype(str).apply(round_up_to_next_half_hour).apply(fix_2400)
    df["Issue date/time"] = df["Issue date/time"].apply(
    lambda val: f"{val[:2]}/{val[2:-1]}" if isinstance(val, str) and val.endswith('Z') else (
        f"{val[:2]}/{val[2:]}" if isinstance(val, str) else val
    )
)
    # Only filter by station code if it's provided
    if station_code:
        df = df[df["Station"] == station_code].reset_index(drop=True)
    df["Wind dir (deg)"] = pd.to_numeric(df["Wind dir (deg)"], errors="coerce").astype("Int64")

    # Save to a file in the same directory as the input file
    output_path = os.path.join(os.path.dirname(filepath), 'AD_warn_output.csv')
    df.to_csv(output_path, index=True)
    return df