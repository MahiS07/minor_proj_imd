import metar.Metar as mt
import pandas as pd
import re
from datetime import datetime, timedelta
import os
import calendar

def clean_metar_inplace(file_path):
    """
    Cleans METAR data by removing trailing '=' characters and newlines.

    Args:
        file_path (str): Path to the METAR file to clean.
    """
    with open(file_path, "r") as infile:
        lines = infile.readlines()

    with open(file_path, "w") as outfile:
        for line in lines:
            outfile.write(line.rstrip("=\n") + "\n")

    print("METAR data cleaned in place.")


def decode_metar_to_csv(
    input_file,
    output_file,
    use_report_dates=False,
    default_month=None,
    default_year=None,
):
    try:
        with open(input_file, "r") as file:
            metar_text = file.read().strip()

        report_dates = []
        if use_report_dates:
            timestamped_reports = re.findall(
                r"(?<!\d)(\d{12})\s+(METAR\s+.*?=)(?=\s+\d{12}\s+METAR\s+|$)",
                metar_text,
                flags=re.DOTALL,
            )
            metar_reports = [report for _, report in timestamped_reports]
            report_dates = [
                datetime.strptime(timestamp, "%Y%m%d%H%M")
                for timestamp, _ in timestamped_reports
            ]
            if not metar_reports:
                metar_reports = [
                    report for report in re.split(r"\r?\n(?=METAR )", metar_text)
                    if report.strip()
                ]
                previous_report_day = None
                current_report_date = (
                    datetime(int(default_year), int(default_month), 1)
                    if default_year and default_month
                    else None
                )
                for metar_code in metar_reports:
                    time_match = re.search(r"\b(\d{2})(\d{4})Z\b", metar_code)
                    if not time_match or current_report_date is None:
                        report_dates.append(None)
                        continue
                    report_day = int(time_match.group(1))
                    if previous_report_day is not None and report_day < previous_report_day:
                        current_report_date += timedelta(days=1)
                        current_report_date = current_report_date.replace(day=1)
                    current_report_date = current_report_date.replace(day=report_day)
                    report_dates.append(current_report_date)
                    previous_report_day = report_day
        else:
            metar_reports = re.split(r"\nMETAR ", metar_text)
        data_list = []

        for report_index, metar_code in enumerate(metar_reports):
            metar_code = metar_code.strip()
            if not metar_code:
                continue

            if not metar_code.startswith("METAR"):
                metar_code = "METAR " + metar_code

            metar_code = metar_code.replace("NOSIG", "")

            try:
                report_date = report_dates[report_index] if use_report_dates else None
                report_month = report_date.month if report_date else 9
                report = mt.Metar(metar_code, month=report_month)

                data = {
                    # "Station": getattr(report, "station", "Mumbai/Chhatrapati Shivaji Intl"),
                    # "Location": getattr(report, "name", "India 19.07N 072.51E"),
                    "DAY": report.time.strftime("%d"),
                    "_METAR_DATE": report_date.strftime("%Y-%m-%d") if report_date else None,
                    "TIME": f"{report.time.hour:02}{report.time.minute:02}Z",
                    # "Wind Speed (m/s)": report.wind_speed.value("MPS") if report.wind_speed else "N/A",
                    "WIND_DIR": report.wind_dir.value() if report.wind_dir else "N/A",
                    "WIND_SPEED": (
                        report.wind_speed.value("KT") if report.wind_speed else "N/A"
                    ),
                    # "Visibility (m)": report.vis.value() if report.vis else "N/A",
                    # "Present Weather": report.present_weather() if report.present_weather() else "None",
                    # "Clouds": report.sky_conditions() if report.sky_conditions() else "No Significant Cloud",
                    "TEMP": report.temp.value("C") if report.temp else "N/A",
                    # "Dew-Point Temperature (°C)": report.dewpt.value("C") if report.dewpt else "N/A",
                    "QNH": report.press.value("hPa") if report.press else "N/A",
                    # "Significant Change": "No significant change" if nosig_present else metar_code.split()[-1],
                }

                data_list.append(data)

            except Exception as e:
                pass

        df = pd.DataFrame(data_list)
        df.to_csv(output_file, index=False)
        print(f"Decoded METAR data saved to {output_file}")
        return df
    except Exception as e:
        print(f"Error processing METAR file: {e}")


def extract_wind_data(wind_str):
    """
    Extracts wind direction and speed from a wind string, handling various formats.
    """


    match = re.match(r"(\d{3})/(\d{2})KT", wind_str)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.match(r"(\d{3})(\d{2})KT", wind_str)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.match(r"(\d{3})\d{2}G(\d{2})KT", wind_str)
    if match:
        return int(match.group(1)), int(match.group(2))

    match_vrb_kt = re.match(r"(VRB)(\d{2})KT", wind_str)
    if match_vrb_kt:
        return "N/A", int(match_vrb_kt.group(2))

    match_vrb_no_kt = re.match(r"(VRB)(\d{2})", wind_str)
    if match_vrb_no_kt:
        return "N/A", int(match_vrb_no_kt.group(2))

    match_vrb_slash_kt = re.match(r"(VRB)/(\d{2})KT", wind_str)
    if match_vrb_slash_kt:
        return "N/A", int(match_vrb_slash_kt.group(2))

    match_vrb_slash_no_kt = re.match(r"(VRB)/(\d{2})", wind_str)
    if match_vrb_slash_no_kt:
        return "N/A", int(match_vrb_slash_no_kt.group(2))

    if "VRB" == wind_str:
        return "N/A", None

    # New format with slash and no KT (e.g., "320/07") - Keep this for numeric directions
    match_numeric_slash = re.match(r"(\d{3})/(\d{2})", wind_str)
    if match_numeric_slash:
        return int(match_numeric_slash.group(1)), int(match_numeric_slash.group(2))

    return None, None  

def extract_data_from_file_with_day_and_wind(file_path):
    """
    Extracts data from a file, including day (from filename or file content), time,
    and separated wind direction/speed.

    Supports both daily and monthly forecast files.
    """
    data = []

    print(f"Processing file: {file_path}")

    filename = os.path.basename(file_path)
    print(f"Processing file: {filename}")
    day_from_name, month_from_name, year_from_name, _ = extract_day_month_year_from_filename(filename)
    print(f"Extracted from filename: Day={day_from_name}, Month={month_from_name}, Year={year_from_name}")

    use_day_from_filename = bool(day_from_name and month_from_name and year_from_name and day_from_name != "01")
    current_day = int(day_from_name) if use_day_from_filename else None
    previous_time = None
    midnight_rollover = False

    try:
        with open(file_path, "r") as file:
            lines = file.readlines()

            if (
                not use_day_from_filename
                and lines
                and not re.match(r"^\d{1,2}$", lines[0].strip())
            ):
                lines = lines[1:]

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if not use_day_from_filename and re.match(r"^\d{1,2}$", line):
                    current_day = int(line)
                    previous_time = None
                    midnight_rollover = False
                    continue

                match = re.match(r"(\d{4}Z)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)", line)
                if match:
                    time, wind_str, temp, qfe, qnh = match.groups()
                    wind_dir, wind_speed = extract_wind_data(wind_str)
                    if "VRB" in wind_str:
                        wind_dir = "VRB"
                    metar_day = current_day if current_day else 1
                    forecast_date = None
                    if month_from_name and year_from_name and current_day:
                        forecast_date = datetime(
                            int(year_from_name), int(month_from_name), current_day
                        )
                    if time == "0000Z" and (
                        previous_time == "2300Z" or midnight_rollover
                    ):
                        if month_from_name and year_from_name and current_day:
                            metar_date = forecast_date + timedelta(days=1)
                            metar_day = metar_date.day
                        else:
                            metar_day += 1
                            metar_date = None
                        midnight_rollover = True
                    elif time != "0000Z":
                        midnight_rollover = False
                        metar_date = forecast_date
                    else:
                        metar_date = forecast_date
                    data.append({
                        "DAY": current_day if current_day else 1,
                        "_METAR_DAY": metar_day,
                        "_METAR_DATE": metar_date.strftime("%Y-%m-%d") if metar_date else None,
                        "MONTH": month_from_name,
                        "YEAR": year_from_name,
                        "TIME": time,
                        "WIND_DIR": wind_dir,
                        "WIND_SPEED": wind_speed,
                        "TEMP": int(temp),
                        "QFE": int(qfe),
                        "QNH": int(qnh),
                    })
                    previous_time = time

        return pd.DataFrame(data)

    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        return pd.DataFrame()
    except Exception as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()


def compare_wind_by_time(df1, df2):
    """
    Compares wind data from two DataFrames based on matching *first* 'TIME' values.
    Handles potential duplicates by keeping only the first occurrence of each time.

    Args:
        df1 (pd.DataFrame): Actual (METAR) data with 'TIME', 'WIND_DIR', 'WIND_SPEED'.
        df2 (pd.DataFrame): Forecast data with 'TIME', 'WIND_DIR', 'WIND_SPEED'.

    Returns:
        pd.DataFrame: Merged DataFrame with 'Accuracy' column.
    """

    if not isinstance(df1, pd.DataFrame) or not isinstance(df2, pd.DataFrame):
        print("Error: Input arguments must be Pandas DataFrames.")
        return pd.DataFrame()

    if "TIME" not in df1.columns or "TIME" not in df2.columns:
        print("Error: Both DataFrames must contain a 'TIME' column.")
        return pd.DataFrame()

    # Remove duplicate times, keeping the first occurrence
    df1_unique = df1.drop_duplicates(subset="TIME", keep="first")
    df2_unique = df2.drop_duplicates(subset="TIME", keep="first")

    merged_df = pd.merge(
        df1_unique,
        df2_unique,
        on="TIME",
        suffixes=("_actual", "_forecast"),
        how="inner",
    )

    if merged_df.empty:
        print("No matching times found between the DataFrames.")
        return pd.DataFrame()

    accuracy = []
    merged_df.to_csv("merged_df.csv", index=False)
    for _, row in merged_df.iterrows():
        actual_dir = row["WIND_DIR_actual"]
        actual_speed = row["WIND_SPEED_actual"]
        forecast_dir = row["WIND_DIR_forecast"]
        forecast_speed = row["WIND_SPEED_forecast"]

        dir_accurate = False
        speed_accurate = False

        if (
            actual_dir == "VRB"
            or forecast_dir == "VRB"
            or actual_dir is None
            or forecast_dir is None
        ):
            dir_accurate = True
        else:
            try:
                dir_diff = abs(int(forecast_dir) - int(actual_dir))
                dir_accurate = dir_diff <= 30 or dir_diff >= 330
            except (ValueError, TypeError):
                print("Invalid wind direction: ", forecast_dir, actual_dir)

        if actual_speed is not None and forecast_speed is not None:
            try:
                speed_accurate = abs(int(forecast_speed) - int(actual_speed)) <= 1
            except (ValueError, TypeError):
                print("Invalid wind speed: ", forecast_speed, actual_speed)
        else:
            print(f"Warning: Missing wind speed for TIME {row['TIME']}.")

        accuracy.append(
            "Accurate" if dir_accurate and speed_accurate else "Not Accurate"
        )

    merged_df["Accuracy"] = accuracy
    return merged_df


def circular_difference(dir1, dir2):
    """
    Calculates the minimum angular difference between two directions, considering circular wrap-around.
    """
    if (
        dir1 is None
        or dir2 is None
        or not isinstance(dir1, (int, float))
        or not isinstance(dir2, (int, float))
    ):
        return None  

    return min(abs(dir1 - dir2), 360 - (abs(dir1 - dir2)))


def extract_day_month_year_from_filename(filename):
    """
    Accepts names like:
      - TAKEOFF_Forecast_12092023.txt  (DDMMYYYY)
      - TAKEOFF_Forecast_12_09_2023.txt (DD_MM_YYYY)
      - TAKEOFF_Forecast_092023.txt     (MMYYYY) -> day defaults to "01"
      - 12092023.txt / 12_09_2023.txt / 092023.txt, etc.

    Returns (day, month, year, "DDMMYYYY") or (None, None, None, None) if no date found.
    """
    import os, re
    name = os.path.basename(filename or "")
    print(f"filename = {name}")

    # 1) DDMMYYYY anywhere
    m = re.search(r'(?<!\d)(\d{2})(\d{2})(\d{4})(?=\.txt$|$)', name)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        return day, month, year, f"{day}{month}{year}"

    # 2) DD_MM_YYYY anywhere
    m = re.search(r'(?<!\d)(\d{2})_(\d{2})_(\d{4})(?=\.txt$|$)', name)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        return day, month, year, f"{day}{month}{year}"

    # 3) Monthly: MMYYYY anywhere (default day "01")
    m = re.search(r'(?<!\d)(\d{2})(\d{4})(?=\.txt$|$)', name)
    if m:
        month, year = m.group(1), m.group(2)
        day = "01"
        return day, month, year, f"{day}{month}{year}"

    return None, None, None, None



def extract_month_year_from_date(date_str, format_str="%Y%m%d%H%M"):
    """ 
    Extract month and year from a date string
    
    Args:
        date_str (str): Date string to parse
        format_str (str): Format of the date string
        
    Returns:
        tuple: (month, year, month_year_str) where month_year_str is formatted as "MMYYYY"
              Returns (None, None, None) if parsing fails
    """
    try:
        date_obj = datetime.strptime(date_str, format_str)
        print(f"date_obj = {date_obj}")
        day = f"{date_obj.day:02d}"
        print(f"day = {day}")
        month = f"{date_obj.month:02d}"
        year = f"{date_obj.year}"
        return day,month,year,f"{day}{month}{year}"
    except ValueError:
        return None, None, None,None

def compare_weather_data(
    df1,
    df2,
    wind_dir_threshold=30,
    wind_speed_threshold=5,
    temp_threshold=1,
    qnh_threshold=1,
):
    """
    Compares weather data from two DataFrames based on matching 'DAY' and 'TIME'.

    Args:
        df1 (pd.DataFrame): Actual (METAR) data with 'TIME', 'WIND_DIR', 'WIND_SPEED', 'TEMP', 'QNH', and 'DAY'.
        df2 (pd.DataFrame): Forecast data with 'TIME', 'WIND_DIR', 'WIND_SPEED', 'TEMP', 'QNH', and 'DAY'.
        wind_dir_threshold (int): Threshold for wind direction accuracy in degrees.
        wind_speed_threshold (int): Threshold for wind speed accuracy in knots.
        temp_threshold (int): Threshold for temperature accuracy in °C.
        qnh_threshold (int): Threshold for QNH accuracy in hPa.

    Returns:
        pd.DataFrame: Daily accuracy summary with counts in parentheses.
    """

    if not isinstance(df1, pd.DataFrame) or not isinstance(df2, pd.DataFrame):
        print("Error: Input arguments must be Pandas DataFrames.")
        return pd.DataFrame()

    required_columns = ["TIME", "WIND_DIR", "WIND_SPEED", "TEMP", "QNH", "DAY"]

    # Check if all required columns are in df1
    if not all(col in df1.columns for col in required_columns):
        missing_cols = [col for col in required_columns if col not in df1.columns]
        print(f"Error: METAR DataFrame is missing columns: {missing_cols}")
        return pd.DataFrame()

    # Check if all required columns except QNH are in df2
    forecast_required = ["TIME", "WIND_DIR", "WIND_SPEED", "TEMP", "DAY"]
    if not all(col in df2.columns for col in forecast_required):
        missing_cols = [col for col in forecast_required if col not in df2.columns]
        print(f"Error: Forecast DataFrame is missing columns: {missing_cols}")
        return pd.DataFrame()

    # If QNH is not in df2 but QFE is, use QFE as QNH
    if "QNH" not in df2.columns and "QFE" in df2.columns:
        df2["QNH"] = df2["QFE"]
    elif "QNH" not in df2.columns:
        print(
            "Error: Forecast DataFrame is missing QNH column and no QFE column to substitute."
        )
        return pd.DataFrame()

    forecast_days = {
        int(day)
        for day in df2["DAY"].dropna().tolist()
        if str(day).isdigit()
    }
    expected_days = set(forecast_days)
    if "MONTH" in df2.columns and "YEAR" in df2.columns:
        month_values = df2["MONTH"].dropna().unique()
        year_values = df2["YEAR"].dropna().unique()
        if len(month_values) == 1 and len(year_values) == 1:
            try:
                expected_days = set(
                    range(1, calendar.monthrange(int(year_values[0]), int(month_values[0]))[1] + 1)
                )
            except (TypeError, ValueError):
                expected_days = set(forecast_days)

    # Match every forecast row independently. Takeoff forecasts may contain
    # repeated times, and a NIL report is absent from the decoded valid METARs.
    merged_rows = []
    for _, forecast_row in df2.iterrows():
        match_day = forecast_row.get("_METAR_DAY", forecast_row["DAY"])
        forecast_time = str(forecast_row["TIME"])
        try:
            forecast_minutes = int(forecast_time[:2]) * 60 + int(forecast_time[2:4])
        except (ValueError, TypeError):
            continue

        forecast_date = forecast_row.get("_METAR_DATE")
        if forecast_date and "_METAR_DATE" in df1.columns:
            candidates = df1[df1["_METAR_DATE"] == forecast_date]
        else:
            candidates = df1[df1["DAY"].astype(str).str.zfill(2) == str(match_day).zfill(2)]
        if candidates.empty:
            continue

        def time_distance(actual_time):
            try:
                actual_minutes = int(str(actual_time)[:2]) * 60 + int(str(actual_time)[2:4])
                return abs(actual_minutes - forecast_minutes)
            except (ValueError, TypeError):
                return float("inf")

        actual_row = min(candidates.to_dict("records"), key=lambda row: time_distance(row["TIME"]))
        merged_row = {
            "DATETIME": f"{str(forecast_row['DAY']).zfill(2)} {forecast_time}",
        }
        for column, value in actual_row.items():
            if column != "_METAR_DAY":
                merged_row[f"{column}_actual"] = value
        for column, value in forecast_row.to_dict().items():
            if column != "_METAR_DAY":
                merged_row[f"{column}_forecast"] = value
        merged_rows.append(merged_row)

    merged_df = pd.DataFrame(merged_rows)

    if merged_df.empty:
        print("No matching day and times found between the DataFrames.")
        unavailable_rows = []
        for day in sorted(expected_days):
            unavailable_rows.append({
                "DAY": f"{day:02d}",
                "Wind Direction": "Not Given" if day not in forecast_days else "Not Available",
                "Wind Speed": "Not Given" if day not in forecast_days else "Not Available",
                "Temperature": "Not Given" if day not in forecast_days else "Not Available",
                "QNH": "Not Given" if day not in forecast_days else "Not Available",
                "Overall": "Not Given" if day not in forecast_days else "Not Available",
            })
        return pd.DataFrame(unavailable_rows)

    # Track parameter-wise individual accuracies
    dir_accuracy_flags = []
    speed_accuracy_flags = []
    temp_accuracy_flags = []
    qnh_accuracy_flags = []
    overall_accuracy = []
    inaccuracy_reasons = []

    for _, row in merged_df.iterrows():
        actual_dir = row["WIND_DIR_actual"]
        forecast_dir = row["WIND_DIR_forecast"]
        actual_speed = row["WIND_SPEED_actual"]
        forecast_speed = row["WIND_SPEED_forecast"]
        actual_temp = row["TEMP_actual"]
        forecast_temp = row["TEMP_forecast"]
        actual_qnh = row["QNH_actual"]
        forecast_qnh = row["QNH_forecast"]

        dir_accurate = False
        speed_accurate = False
        temp_accurate = False
        qnh_accurate = False
        reasons = []

        # A forecast VRB direction is correct without angular comparison.
        if (
            forecast_dir == "VRB"
            or actual_dir == "VRB"
            or actual_dir == "N/A"
            or forecast_dir == "N/A"
            or pd.isna(actual_dir)
            or pd.isna(forecast_dir)
        ):
            dir_accurate = True
        else:
            try:
                dir_diff = circular_difference(int(forecast_dir), int(actual_dir))
                dir_accurate = dir_diff is not None and dir_diff <= wind_dir_threshold
                if not dir_accurate and dir_diff is not None:
                    reasons.append(f"Wind Direction off by {dir_diff:.1f}°")
            except (ValueError, TypeError):
                print(
                    f"Warning: Invalid wind direction for DATETIME {row['DATETIME']}."
                )
                reasons.append("Wind Direction - Invalid data")

        # Handle speed accuracy
        if (
            pd.notna(actual_speed)
            and pd.notna(forecast_speed)
            and actual_speed != "N/A"
            and forecast_speed != "N/A"
        ):
            try:
                speed_diff = abs(int(forecast_speed) - int(actual_speed))
                speed_accurate = speed_diff <= wind_speed_threshold
                if not speed_accurate:
                    reasons.append(f"Wind Speed off by {speed_diff} knots")
            except (ValueError, TypeError):
                print(f"Warning: Invalid wind speed for DATETIME {row['DATETIME']}.")
                reasons.append("Wind Speed - Invalid data")
        else:
            print(f"Warning: Missing wind speed for DATETIME {row['DATETIME']}.")
            reasons.append("Wind Speed - Missing data")

        # Handle temperature accuracy
        if (
            pd.notna(actual_temp)
            and pd.notna(forecast_temp)
            and actual_temp != "N/A"
            and forecast_temp != "N/A"
        ):
            try:
                temp_diff = abs(float(forecast_temp) - float(actual_temp))
                temp_accurate = temp_diff <= temp_threshold
                if not temp_accurate:
                    reasons.append(f"Temperature off by {temp_diff:.1f}°C")
            except (ValueError, TypeError):
                print(f"Warning: Invalid temperature for DATETIME {row['DATETIME']}.")
                reasons.append("Temperature - Invalid data")
        else:
            print(f"Warning: Missing temperature for DATETIME {row['DATETIME']}.")
            reasons.append("Temperature - Missing data")

        # Handle QNH accuracy
        if (
            pd.notna(actual_qnh)
            and pd.notna(forecast_qnh)
            and actual_qnh != "N/A"
            and forecast_qnh != "N/A"
        ):
            try:
                qnh_diff = abs(float(forecast_qnh) - float(actual_qnh))
                qnh_accurate = qnh_diff <= qnh_threshold
                if not qnh_accurate:
                    reasons.append(f"QNH off by {qnh_diff:.1f} hPa")
            except (ValueError, TypeError):
                print(f"Warning: Invalid QNH for DATETIME {row['DATETIME']}.")
                reasons.append("QNH - Invalid data")
        else:
            print(f"Warning: Missing QNH for DATETIME {row['DATETIME']}.")
            reasons.append("QNH - Missing data")

        # Store individual accuracy flags
        dir_accuracy_flags.append(dir_accurate)
        speed_accuracy_flags.append(speed_accurate)
        temp_accuracy_flags.append(temp_accurate)
        qnh_accuracy_flags.append(qnh_accurate)

        # Overall accuracy
        overall_accuracy.append(
            "Accurate"
            if all([dir_accurate, speed_accurate, temp_accurate, qnh_accurate])
            else "Not Accurate"
        )
        
        # Store inaccuracy reasons
        inaccuracy_reasons.append(" | ".join(reasons) if reasons else "All Accurate")

    # Add accuracy flags to DataFrame
    merged_df["DIR_Accurate"] = dir_accuracy_flags
    merged_df["SPD_Accurate"] = speed_accuracy_flags
    merged_df["TEMP_Accurate"] = temp_accuracy_flags
    merged_df["QNH_Accurate"] = qnh_accuracy_flags
    merged_df["Accuracy"] = overall_accuracy
    merged_df["Inaccuracy_Reason"] = inaccuracy_reasons

    # Group-wise summary per DAY
    merged_df["DAY"] = merged_df["DAY_forecast"].astype(str).str.zfill(2)

    # Calculate daily accuracy percentages with counts
    daily_accuracy = (
        merged_df.groupby("DAY")
        .agg(
            {
                "DIR_Accurate": lambda x: f"{round(100 * x.sum() / len(x), 1)}% ({x.sum()})",
                "SPD_Accurate": lambda x: f"{round(100 * x.sum() / len(x), 1)}% ({x.sum()})",
                "TEMP_Accurate": lambda x: f"{round(100 * x.sum() / len(x), 1)}% ({x.sum()})",
                "QNH_Accurate": lambda x: f"{round(100 * x.sum() / len(x), 1)}% ({x.sum()})",
                "Accuracy": lambda x: f"{round(100 * (x == 'Accurate').sum() / len(x), 1)}% ({(x == 'Accurate').sum()})",
            }
        )
        .rename(
            columns={
                "DIR_Accurate": "Wind Direction",
                "SPD_Accurate": "Wind Speed",
                "TEMP_Accurate": "Temperature",
                "QNH_Accurate": "QNH",
                "Accuracy": "Overall",
            }
        )
        .reset_index()
    )

    matched_days = set(merged_df["DAY"].astype(int).tolist())
    missing_day_rows = []
    for day in sorted(expected_days - matched_days):
        status = "Not Given" if day not in forecast_days else "Not Available"
        missing_day_rows.append({
            "DAY": f"{day:02d}",
            "Wind Direction": status,
            "Wind Speed": status,
            "Temperature": status,
            "QNH": status,
            "Overall": status,
        })
    if missing_day_rows:
        daily_accuracy = pd.concat(
            [daily_accuracy, pd.DataFrame(missing_day_rows)],
            ignore_index=True,
        )
        daily_accuracy["_DAY_SORT"] = pd.to_numeric(daily_accuracy["DAY"], errors="coerce")
        daily_accuracy = (
            daily_accuracy.sort_values("_DAY_SORT", na_position="last")
            .drop(columns="_DAY_SORT")
            .reset_index(drop=True)
        )

    # Calculate whole month accuracy
    total_records = len(merged_df)
    whole_month = {
        "DAY": "Whole Month",
        "Wind Direction": f"{round(100 * merged_df['DIR_Accurate'].sum() / total_records, 1)}% ({merged_df['DIR_Accurate'].sum()})",
        "Wind Speed": f"{round(100 * merged_df['SPD_Accurate'].sum() / total_records, 1)}% ({merged_df['SPD_Accurate'].sum()})",
        "Temperature": f"{round(100 * merged_df['TEMP_Accurate'].sum() / total_records, 1)}% ({merged_df['TEMP_Accurate'].sum()})",
        "QNH": f"{round(100 * merged_df['QNH_Accurate'].sum() / total_records, 1)}% ({merged_df['QNH_Accurate'].sum()})",
        "Overall": f"{round(100 * (merged_df['Accuracy'] == 'Accurate').sum() / total_records, 1)}% ({(merged_df['Accuracy'] == 'Accurate').sum()})",
    }

    # Add ICAO requirements row
    icao_requirements = {
        "DAY": "ICAO Requirement",
        "Wind Direction": "90%",
        "Wind Speed": "90%",
        "Temperature": "90%",
        "QNH": "90%",
        "Overall": "90%",
    }

    # Append whole month and ICAO requirements to daily accuracy
    daily_accuracy = pd.concat(
        [
            daily_accuracy,
            pd.DataFrame([whole_month]),
            pd.DataFrame([icao_requirements]),
        ],
        ignore_index=True,
    )

    return daily_accuracy, merged_df


import matplotlib.pyplot as plt
import io
import base64

# def plot_accuracy_chart(daily_accuracy, metric="Overall"):
#     """
#     Creates a bar chart for accuracy over days.
    
#     Args:
#         daily_accuracy (pd.DataFrame): DataFrame from compare_weather_data.
#         metric (str): Which metric to plot ("Overall", "Wind Direction", "Wind Speed", "Temperature", "QNH").
    
#     Returns:
#         str: Base64 encoded PNG image (can be sent to frontend).
#     """
#     df = daily_accuracy.copy()

#     # Extract only % number
#     df[metric] = df[metric].str.extract(r'(\d+\.?\d*)').astype(float)

#     # Filter out non-date rows (Whole Month, ICAO Requirement)
#     df = df[~df["DAY"].isin(["Whole Month", "ICAO Requirement"])]

#     # Plot
#     plt.figure(figsize=(10,5))
#     plt.bar(df["DAY"], df[metric], color="skyblue")
#     plt.xticks(rotation=45)
#     plt.ylim(0, 100)
#     plt.ylabel("Accuracy (%)")
#     plt.xlabel("Day")
#     plt.title(f"{metric} Accuracy per Day")

#     # Save to base64
#     buf = io.BytesIO()
#     plt.tight_layout()
#     plt.savefig(buf, format="png")
#     buf.seek(0)
#     img_base64 = base64.b64encode(buf.read()).decode("utf-8")
#     plt.close()
#     return img_base64
