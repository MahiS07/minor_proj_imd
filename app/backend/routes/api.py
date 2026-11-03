from flask import Blueprint, request, jsonify, send_file, render_template, Response
import os
import uuid
import base64
from datetime import datetime,timedelta
import re
from werkzeug.utils import secure_filename
from app.backend.utils import decode_metar_to_csv, extract_data_from_file_with_day_and_wind, compare_weather_data, OgimetAPI, extract_day_month_year_from_filename,extract_month_year_from_date,fetch_upper_air_data,circular_difference,interpolate_temperature_only,generate_upper_air_verification_xlsx,parse_forecast_pdf,validate_forecast_weather_with_metar
from app.backend.utils.AD_warn import parse_warning_file
from app.backend.utils.generate_warning_report import generate_warning_report, generate_aerodrome_warnings_table
from app.backend.utils.extract_metar_features import extract_metar_features
from app.backend.utils.validation import validate_files,extract_icao_from_warning
from app.backend.utils.adwrn_ml import predict as predict_adwrn
from pathlib import Path
import json
from app.backend.config import METAR_DATA_DIR, UPPER_AIR_DATA_DIR, DOCKER_VOLUME_MOUNT_POINT, AD_WARN_DIR
import pandas as pd
import numpy as np
from urllib.parse import quote
import math
from app.backend.auth import require_role, log_activity, get_verification_params
from app.backend.models import UserActivity, User
import io
import plotly.express as px
import plotly.graph_objects as go
import shutil, time
import csv
from io import StringIO

 

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/adwrn/prediction', methods=['POST'])
def adwrn_prediction():
    """Forecast using the saved experimental hazard model."""
    payload = request.get_json(silent=True) or {}
    station_code = str(payload.get('station', '')).strip().upper()
    issue = str(payload.get('issue', ''))
    try:
        observation_lookback = int(payload.get('observation_lookback_minutes', 60))
    except (TypeError, ValueError):
        return jsonify({'error': 'Observation lookback must be minutes.'}), 400
    if not re.fullmatch(r'[A-Z]{4}', station_code):
        return jsonify({'error': 'A valid four-character ICAO station is required.'}), 400
    try:
        datetime.strptime(issue, '%Y%m%d%H%M')
    except ValueError:
        return jsonify({'error': 'Issue time must use UTC YYYYMMDDHHMM.'}), 400
    result = predict_adwrn(station_code, issue, '', '', observation_lookback)
    if result.get('status') == 'setup_required':
        issue_dt = datetime.strptime(issue, '%Y%m%d%H%M')
        history_start = '2024-01-01'
        history_end = '2025-12-31'
        training = predict_adwrn(
            station_code, issue, history_start, history_end,
            observation_lookback, refresh=True
        )
        if training.get('status') == 'ok':
            result = predict_adwrn(station_code, issue, '', '', observation_lookback)
        else:
            result = training
    if result.get('status') == 'ok':
        issue_dt = datetime.strptime(issue, '%Y%m%d%H%M')
        preamble = (
            f"AI-GENERATED AERODROME WARNING OUTLOOK\n"
            f"{station_code} — {issue_dt.strftime('%B %Y').upper()}\n\n"
            f"LIGHT AIRCRAFT WARNING FOR {station_code} - {issue_dt:%Y%m%d %H}\n\n---"
        )
        result['outlook'] = preamble + '\n\n' + (
            '\n\n'.join(result['warnings']) if result['warnings'] else result['message']
        )
    return jsonify(result)


@api_bp.route('/adwrn/refresh', methods=['POST'])
def adwrn_refresh():
    payload = request.get_json(silent=True) or {}
    station = str(payload.get('station', '')).strip().upper()
    issue = str(payload.get('issue', ''))
    try:
        datetime.strptime(issue, '%Y%m%d%H%M')
        result = predict_adwrn(station, issue, str(payload.get('history_start', '')), str(payload.get('history_end', '')), 60, refresh=True)
    except ValueError:
        return jsonify({'error': 'Provide an issue time and ordered UTC history dates.'}), 400
    return jsonify(result), (200 if result.get('status') == 'ok' else 400)


@api_bp.route('/adwrn/model-status', methods=['GET'])
def adwrn_model_status():
    path = Path(AD_WARN_DIR) / 'adwrn_ml_setup.json'
    try:
        setup = json.loads(path.read_text(encoding='utf-8'))
        return jsonify({'available': bool(setup.get('models')), 'record_count': len(setup.get('records', [])), 'history_start': setup.get('history_start'), 'history_end': setup.get('history_end')})
    except (OSError, ValueError):
        return jsonify({'available': False, 'message': 'Historical data will be collected and the model trained automatically when you generate a forecast.'})

METAR_UPLOADS_DIR = os.path.join(METAR_DATA_DIR, 'uploads')
METAR_DOWNLOADS_DIR = os.path.join(METAR_DATA_DIR, 'downloads')
UPPER_AIR_UPLOADS_DIR = os.path.join(UPPER_AIR_DATA_DIR, 'uploads')
UPPER_AIR_DOWNLOADS_DIR = os.path.join(UPPER_AIR_DATA_DIR, 'downloads')

os.makedirs(METAR_UPLOADS_DIR, exist_ok=True)
os.makedirs(METAR_DOWNLOADS_DIR, exist_ok=True)
os.makedirs(UPPER_AIR_UPLOADS_DIR, exist_ok=True)
os.makedirs(UPPER_AIR_DOWNLOADS_DIR, exist_ok=True)

station = None
start_dt = None
end_dt = None
month_year = None

def encode_file_path(file_path):
    """Encode a file path to a secure token"""
    token = f"{uuid.uuid4()}:{file_path}"
    encoded = base64.urlsafe_b64encode(token.encode()).decode()
    return encoded

def decode_file_path(encoded_path):
    """Decode a secure token back to a file path"""
    try:
        decoded = base64.urlsafe_b64decode(encoded_path.encode()).decode()
        _, file_path = decoded.split(':', 1)
        return file_path
    except Exception:
        return None

def parse_validity_to_month_year(validity_str):
    """Parse validity string like '202506010000' to month and year format"""
    try:
        validity_str = validity_str.rstrip('Z')
        
        if len(validity_str) == 12:
            year = int(validity_str[:4])
            month = int(validity_str[4:6])
            day = int(validity_str[6:8])
            hour = int(validity_str[8:10])
            minute = int(validity_str[10:12])
            
            date_obj = datetime(year, month, day, hour, minute)
            
        elif len(validity_str) == 6:
            day = int(validity_str[:2])
            hour = int(validity_str[2:4])
            minute = int(validity_str[4:6])
            
            year = 2024  # Based on the data context
            month = 1  # January
            
            try:
                date_obj = datetime(year, month, day, hour, minute)
            except ValueError:
                month = 2
                date_obj = datetime(year, month, day, hour, minute)
            
        elif len(validity_str) == 8:
            day = int(validity_str[:2])
            hour = int(validity_str[2:4])
            minute = int(validity_str[4:6])
            year = int(validity_str[6:8])
            
            if year < 50:  
                year += 2000
            else: 
                year += 1900
            
            current_date = datetime.now()
            date_obj = datetime(year, current_date.month, day, hour, minute)
            
        elif len(validity_str) == 10:
            day = int(validity_str[:2])
            hour = int(validity_str[2:4])
            minute = int(validity_str[4:6])
            year = int(validity_str[6:10])
            
            current_date = datetime.now()
            date_obj = datetime(year, current_date.month, day, hour, minute)
            
        else:
            return validity_str
        
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        
        month_name = month_names[date_obj.month - 1]
        year = date_obj.year
        
        return f"{month_name} {year}"
        
    except Exception as e:
        print(f"Error parsing validity string '{validity_str}': {e}")
        return validity_str

def extract_date_from_metar_file(metar_file_path):
    """Extract date information from METAR file to determine month and year"""
    try:
        if not os.path.exists(metar_file_path):
            return None
            
        with open(metar_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if not lines:
            return None
            
        for line in lines:
            line = line.strip()
            if line and len(line) >= 12:
                if line[:12].isdigit():
                    date_str = line[:12]
                    return parse_validity_to_month_year(date_str)
                    
        for line in lines:
            line = line.strip()
            if line and 'METAR' in line:
                # YYYYMMDDHHMM
                date_match = re.search(r'(\d{12})', line)
                if date_match:
                    date_str = date_match.group(1)
                    return parse_validity_to_month_year(date_str)
                    
        return None
        
    except Exception as e:
        print(f"Error extracting date from METAR file: {e}")
        return None

 

@api_bp.route('/get_metar', methods=['GET'])
def get_metar():
    """
    Fetch METAR data from Ogimet and return the raw text file.
    
    Query parameters:
        start_date: Start date for METAR data in format YYYYMMDDHHMM
        end_date: End date for METAR data in format YYYYMMDDHHMM
        icao: ICAO code for the airport
        
    Returns:
        Raw METAR text file or JSON error response
    """
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        icao = request.args.get('icao')
        
        if not all([start_date, end_date, icao]):
            return jsonify({
                "error": "Missing required parameters. Please provide start_date, end_date, and icao."
            }), 400
        
        icao = re.sub(r'[^a-zA-Z0-9]', '', icao)
        
        try:
            datetime.strptime(start_date, "%Y%m%d%H%M")
            datetime.strptime(end_date, "%Y%m%d%H%M")
        except ValueError:
            return jsonify({
                "error": "Invalid date format. Please use the format YYYYMMDDHHMM."
            }), 400
        
        api = OgimetAPI()
        metar_path = api.save_metar_to_file(
            begin=start_date,
            end=end_date,
            icao=icao
        )
        
        if os.path.exists(metar_path):
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            download_name = secure_filename(f"metar_{icao}_{timestamp}.txt")
            return send_file(
                metar_path,
                mimetype='text/plain',
                as_attachment=True,
                download_name=download_name
            )
        else:
            return jsonify({
                "error": "Failed to retrieve METAR data."
            }), 500
            
    except Exception as e:
        print(f"Error in get_metar: {str(e)}")
        return jsonify({
            "error": f"An error occurred while retrieving METAR data: {str(e)}"
        }), 500

@api_bp.route('/process_metar', methods=['POST'])
def process_metar():
    """
    Process METAR data by fetching observations and comparing with forecast.
    
    Expected JSON body:
    {
        "start_date": "YYYYMMDDHHMM", // Start date for METAR data
        "end_date": "YYYYMMDDHHMM",   // End date for METAR data
        "icao": "VABB",               // ICAO code for the airport
    }
    
    The forecast file should be uploaded as 'forecast_file' in the multipart/form-data.
    
    Returns:
        JSON response with analysis results and paths to generated files
    """
    try:
        # multipart/form-data
        form_data = request.form.to_dict()
        
        global start_dt
        global end_dt
        global station

        start_date = form_data.get('start_date')
        end_date = form_data.get('end_date')
        icao = form_data.get('icao')

        start_dt = start_date
        end_dt = end_date
        station = icao

        is_date_time_provided = start_date and end_date
        observation_file = request.files.get('observation_file')
        is_observation_file_provided = bool(
            observation_file and observation_file.filename
        )

        if not ((start_date and end_date) or is_observation_file_provided) or not icao:
            return jsonify({
                "error": "Missing required parameters. Please provide either (start_date and end_date) or observation file, and icao."
            }), 400
        
        icao = re.sub(r'[^a-zA-Z0-9]', '', icao)
        
        metar_month_year = None
        if is_date_time_provided:
            try:
                _,_, _, metar_month_year = extract_month_year_from_date(start_date)
                datetime.strptime(end_date, "%Y%m%d%H%M")
                if not metar_month_year:
                    return jsonify({
                        "error": "Could not extract month and year from start date."
                    }), 400
            except ValueError:
                return jsonify({
                    "error": "Invalid date format. Please use the format YYYYMMDDHHMM."
                }), 400
            
        # Check forecast file 
        if 'forecast_file' not in request.files:
            return jsonify({
                "error": "No forecast file provided. Please upload a forecast file."
            }), 400
            
        forecast_file = request.files['forecast_file']

        if forecast_file.filename == '':
            return jsonify({
                "error": "Empty forecast file. Please upload a valid forecast file."
            }), 400
        
        _,_, _, forecast_month_year = extract_day_month_year_from_filename(forecast_file.filename)

        if is_observation_file_provided:
            if not metar_month_year:
                _,_, _, metar_month_year = extract_day_month_year_from_filename(observation_file.filename)
        
        if metar_month_year and forecast_month_year and metar_month_year != forecast_month_year:
            return jsonify({
                "error": f"Month/year mismatch between METAR data ({metar_month_year}) and forecast file ({forecast_month_year}). Please ensure both files are for the same month and year."
            }), 200
            
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        if not forecast_month_year and is_date_time_provided:
            forecast_month_year = f"{start_date[4:6]}{start_date[:4]}"
        forecast_filename = secure_filename(f"{forecast_month_year}.txt")
        forecast_path = os.path.join(METAR_UPLOADS_DIR, forecast_filename)
        forecast_file.save(forecast_path)
        df_forecast = extract_data_from_file_with_day_and_wind(forecast_path)
        
        if is_date_time_provided:   
            api = OgimetAPI()
            metar_end_date = (
                datetime.strptime(end_date, "%Y%m%d%H%M") + timedelta(days=1)
            ).strftime("%Y%m%d%H%M")
            metar_path = api.save_metar_to_file(
                begin=start_date,
                end=metar_end_date,
                icao=icao
            )
        else:
           
            observation_filename = secure_filename(f"observation_{icao}_{timestamp}.txt")
            observation_path = os.path.join(METAR_UPLOADS_DIR, observation_filename)
            observation_file.save(observation_path)
            metar_path = observation_path
        
        # Decode METAR data to CSV
        metar_csv_filename = secure_filename(f"decoded_metar_{icao}_{timestamp}.csv")
        metar_csv_path = os.path.join(METAR_DOWNLOADS_DIR, metar_csv_filename)
        df_metar = decode_metar_to_csv(
            metar_path,
            metar_csv_path,
            use_report_dates=True,
            default_month=(
                int(start_date[4:6])
                if is_date_time_provided
                else int(forecast_month_year[2:4])
                if forecast_month_year and forecast_month_year.isdigit()
                else None
            ),
            default_year=(
                int(start_date[:4])
                if is_date_time_provided
                else int(forecast_month_year[2:])
                if forecast_month_year and forecast_month_year.isdigit()
                else None
            ),
        )

        if df_metar is None or df_metar.empty:
            return jsonify({
                "error": "No valid METAR observations were found for the Takeoff verification period."
            }), 400
        
        takeoff_params = get_verification_params("takeoff") or {}

        # Compare weather data
        comparison_df, merged_df = compare_weather_data(df_metar, df_forecast, **takeoff_params)
        if "DAY" in comparison_df.columns and "QNH" in comparison_df.columns:
            icao_requirement_mask = comparison_df["DAY"].eq("ICAO Requirement")
            if icao_requirement_mask.any():
                for requirement_column in [
                    "Wind Direction",
                    "Wind Speed",
                    "Temperature",
                    "QNH",
                    "Overall",
                ]:
                    if requirement_column in comparison_df.columns:
                        comparison_df.loc[icao_requirement_mask, requirement_column] = "90%"
        
        # Store last comparison results globally (so /accuracy_chart can access it)
        global last_comparison_df
        last_comparison_df = comparison_df.copy()

        
        # Save comparison results to CSV with secure filename
        comparison_csv_filename = secure_filename(f"comparison_{icao}_{timestamp}.csv")
        comparison_csv_path = os.path.join(METAR_DOWNLOADS_DIR, comparison_csv_filename)

        # Create header information with period and station details
        with open(comparison_csv_path, 'w', newline='', encoding='utf-8') as f:
            f.write(f"REPORT,")
            f.write(f"{icao},")
            if start_date and end_date:
                format_date = lambda x: datetime.strptime(x, "%Y%m%d%H%M").strftime("%d/%m/%Y %H:%M UTC") if x else ""
                f.write(f"{format_date(start_date)} to {format_date(end_date)},")
            else:
                f.write(f"Observation,")
            f.write("\n")  # Empty line separator

        comparison_df.drop(columns=["Overall"], errors="ignore").to_csv(
            comparison_csv_path, index=False, mode='a'
        )

        # Save merged data to CSV with secure filename
        merged_csv_filename = secure_filename(f"merged_{icao}_{timestamp}.csv")
        merged_csv_path = os.path.join(METAR_DOWNLOADS_DIR, merged_csv_filename)
        merged_df.to_csv(merged_csv_path, index=False)
        
        # Calculate metrics
        total_comparisons = len(comparison_df)
        accurate_predictions = 0
        accuracy_percentage = (accurate_predictions / total_comparisons) * 100 if total_comparisons > 0 else 0
        
        # Encode file paths for security
        encoded_metar_path = encode_file_path(metar_path)
        encoded_metar_csv_path = encode_file_path(metar_csv_path)
        encoded_comparison_csv_path = encode_file_path(comparison_csv_path)
        encoded_merged_csv_path = encode_file_path(merged_csv_path)

        # Prepare response
        response_data = {
            "status": "success",
            "message": "METAR data processed successfully",
            "metrics": {
                "total_comparisons": total_comparisons,
                "accurate_predictions": accurate_predictions,
                "accuracy_percentage": round(accuracy_percentage, 2)
            },
            "file_paths": {
                "metar_file": encoded_metar_path,
                "metar_csv": encoded_metar_csv_path,
                "comparison_csv": encoded_comparison_csv_path,
                "merged_csv": encoded_merged_csv_path
            },
            "metadata": {
                "start_time": datetime.strptime(start_date, "%Y%m%d%H%M").strftime("%d/%m/%Y %H:%M UTC") if start_date else None,
                "end_time": datetime.strptime(end_date, "%Y%m%d%H%M").strftime("%d/%m/%Y %H:%M UTC") if end_date else None,
                "icao": icao,
            },
        }
        
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"Error in process_metar: {str(e)}")
        return jsonify({
            "error": f"An error occurred while processing the METAR data: {str(e)}"
        }), 500
    

@api_bp.route('/download/<file_type>', methods=['GET'])
def download_file(file_type):
    """
    Download generated files.
    
    Parameters:
        file_type: Type of file to download ('metar', 'metar_csv', 'comparison_csv')
        file_path: Path to the file (from the process_metar response)
    """
    try:
        encoded_path = request.args.get('file_path')
        if not encoded_path:
            return jsonify({
                "error": "No file path provided. Please provide the file_path parameter."
            }), 400
        
        # Decode the file path
        file_path = decode_file_path(encoded_path)
        if not file_path:
            return jsonify({
                "error": "Invalid file path token."
            }), 400
        
            
        # Validate file path to prevent directory traversal
        normalized_path = os.path.normpath(file_path)
        valid_prefixes = ['uploads', 'downloads']
        
        if not any(normalized_path.startswith(prefix) for prefix in valid_prefixes):
            # Also check for absolute paths that might contain our valid directories
            if not any(os.sep + prefix in normalized_path for prefix in valid_prefixes):
                return jsonify({
                    "error": "Invalid file path. Access denied."
                }), 403
            
        if not os.path.exists(file_path):
            return jsonify({
                "error": f"File not found at path: {file_path}"
            }), 404
        
        if file_type == 'metar':
            mime_type = 'text/plain'
            filename = secure_filename(os.path.basename(file_path))
            return send_file(
                file_path,
                mimetype=mime_type,
                as_attachment=True,
                download_name=filename
            )
        elif file_type in ['metar_csv', 'comparison_csv']:
            mime_type = 'text/csv'
            filename = secure_filename(os.path.basename(file_path))
            return send_file(
                file_path,
                mimetype=mime_type,
                as_attachment=True,
                download_name=filename
            )
        elif file_type == 'merged_csv':
            
            # Read the CSV file and add description line at the top
            with open(file_path, 'r', encoding='utf-8') as f:
                csv_content = f.read()
            
            # Get global variables for description
            global station, start_dt, end_dt
            
            # Format dates for display
            start_date_str = "N/A"
            end_date_str = "N/A"
            if start_dt:
                try:
                    start_date_obj = datetime.strptime(start_dt, "%Y%m%d%H%M")
                    start_date_str = start_date_obj.strftime("%d/%m/%Y %H:%M UTC")
                except:
                    start_date_str = start_dt
            if end_dt:
                try:
                    end_date_obj = datetime.strptime(end_dt, "%Y%m%d%H%M")
                    end_date_str = end_date_obj.strftime("%d/%m/%Y %H:%M UTC")
                except:
                    end_date_str = end_dt
            
            station_code = station if station else "N/A"
            
            # Create new content with description line
            description = f"Detailed takeoff forecast verification results for station {station_code} for date {start_date_str} to {end_date_str}."
            new_content = f"{description}\n{csv_content}"
            
            # Create a BytesIO object with the modified content
            output = io.BytesIO()
            output.write(new_content.encode('utf-8'))
            output.seek(0)
            
            return send_file(
                output,
                mimetype='text/csv',
                as_attachment=True,
                download_name='detailed_takeoff_results.csv'
            )
        else:
            return jsonify({
                "error": f"Invalid file type: {file_type}. Valid types are 'metar', 'metar_csv', 'comparison_csv', and 'merged_csv'."
            }), 400
        
    except Exception as e:
        print(f"Error in download_file: {str(e)}")
        return jsonify({
            "error": f"An error occurred while downloading the file: {str(e)}"
        }), 500

last_comparison_df = None
        
@api_bp.route("/accuracy_chart", methods=["GET"])
def accuracy_chart():
    global last_comparison_df,start_dt,end_dt
    metric = request.args.get("metric", "Overall")

    format_date = lambda x: datetime.strptime(x, "%Y%m%d%H%M").strftime("%d/%m/%Y %H:%M UTC") if x else ""
    start_dt = format_date(start_dt)
    end_dt = format_date(end_dt)

    if last_comparison_df is None:
        return jsonify({"error": "No comparison data available. Run /process_metar first."}), 400

    # Determine valid metrics that actually exist in the dataframe
    possible_metrics = ["Temperature", "Wind Speed", "Wind Direction", "QNH"]
    valid_metrics = [m for m in possible_metrics if m in last_comparison_df.columns]
    
    if not valid_metrics:
        return jsonify({"error": "No valid parameters found for graph."}), 400

    # Prepare DataFrame for chart
    df = last_comparison_df.copy()
    df = df[~df["DAY"].isin(["Whole Month", "ICAO Requirement"])]
    
    # Ensure metric values are floats
    for m in valid_metrics:
        df[m] = df[m].astype(str).str.extract(r'(\d+\.?\d*)').astype(float)

    fig = go.Figure()
    
    for i, m in enumerate(valid_metrics):
        fig.add_trace(go.Bar(
            x=df["DAY"],
            y=df[m],
            name=m,
            text=df[m],
            texttemplate="%{y:.1f}%",
            textposition="outside",
            hovertemplate=f"<b>{m}</b><br>Day %{{x}}<br>Accuracy: %{{y:.1f}}%<extra></extra>",
            marker=dict(
                color=df[m],
                colorscale=["Blues", "Reds", "Oranges", "Greens"][i],
                showscale=True,
                colorbar=dict(title=f"{m} Accuracy", x=1.02)
            ),
            visible=(i == 0)  # Only the first metric is visible by default
        ))

    # Create dropdown menus
    buttons = []
    for i, m in enumerate(valid_metrics):
        visibility = [j == i for j in range(len(valid_metrics))]
        buttons.append(dict(
            label=m,
            method="update",
            args=[
                {"visible": visibility},
                {"title.text": f"Daily {m} Accuracy from {start_dt} to {end_dt}"}
            ]
        ))

    fig.update_layout(
        updatemenus=[
            dict(
                active=0,
                buttons=buttons,
                x=0.01,
                xanchor="left",
                y=1.15,
                yanchor="top",
                direction="down",
                showactive=True,
            )
        ],
        title={
            'text': f"Daily {valid_metrics[0]} Accuracy from {start_dt} to {end_dt}",
            'x': 0.5,
            'xanchor': 'center',
            'y': 0.95,
            'font': {'size': 18, 'color': 'black'}
        },
        xaxis_title_text="Day",
        yaxis_title_text="Accuracy (%)",
        yaxis_range=[0, 115],
        plot_bgcolor='rgba(128, 128, 128, 0.3)',
        paper_bgcolor='white',
        margin=dict(t=120)  # Add top margin for the dropdown
    )

    return Response(fig.to_html(full_html=False), mimetype="text/html")


@api_bp.route('/get_upper_air', methods=['GET'])
def get_upper_air():
    datetime_str = request.args.get('datetime')
    station_id = request.args.get('station_id')
    try:
        file_path = fetch_upper_air_data(datetime_str, station_id)
      
        if os.path.exists(file_path):
            return send_file(
                file_path,
                mimetype='text/csv',
                as_attachment=True,
                download_name=os.path.basename(file_path)
            )
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/process_upper_air', methods=['POST'])
def process_upper_air():
    try:
        station_id = request.form['station_id']
        datetime_str = request.form.get('datetime')

        observation_file = request.files.get('observation_file')
        forecast_file = request.files.get('forecast_file')

        forecast_df = None
        if forecast_file:
            forecast_filename = secure_filename(forecast_file.filename)
            forecast_path = os.path.join(UPPER_AIR_DATA_DIR, 'uploads', forecast_filename)
            forecast_file.save(forecast_path)
            forecast_df,weather,startTime,endTime,icao,validity_code = parse_forecast_pdf(forecast_path)
            if hasattr(forecast_df, 'columns'):
                forecast_df.columns = forecast_df.columns.str.strip()
                forecast_df = forecast_df.map(lambda x: x.strip() if isinstance(x, str) else x)
        # --- Handle Observation File or Fetch ---
        if observation_file:
            obs_path = os.path.join(UPPER_AIR_DOWNLOADS_DIR, secure_filename(observation_file.filename))
            observation_file.save(obs_path)
            actual_df = pd.read_csv(obs_path, skipinitialspace=True)
            actual_df.columns = actual_df.columns.str.strip()
            actual_df = actual_df.map(lambda x: x.strip() if isinstance(x, str) else x)
        else:
            file_path = fetch_upper_air_data(datetime_str, station_id)
            actual_df = pd.read_csv(file_path, skipinitialspace=True)
            actual_df.columns = actual_df.columns.str.strip()
            actual_df = actual_df.applymap(lambda x: x.strip() if isinstance(x, str) else x)


        if forecast_df is not None:
            print("forecast_df columns:", forecast_df.columns.tolist())

        for col in ["geopotential height_m", "temperature_C", "wind speed_m/s"]:
            if col in actual_df.columns:
                actual_df[col] = pd.to_numeric(actual_df[col], errors="coerce")
            else:
                raise KeyError(f"Column '{col}' not found in observation data.")

        for col in ["Altitude (m)", "Temperature (°C)", "Wind Speed (kt)"]:
            if col in forecast_df.columns:
                forecast_df[col] = pd.to_numeric(forecast_df[col], errors="coerce")
            else:
                raise KeyError(f"Column '{col}' not found in forecast data.")

        min_pairs = interpolate_temperature_only(actual_df, forecast_df)

        # Wind speed (converted)
        min_pairs["wind speed_kt_actual"] = min_pairs["actual_wind_speed_m/s"] * 1.94384

        # Accuracy calculations
        min_pairs["temp_diff"] = (min_pairs["Temperature (°C)"] - min_pairs["interp_temperature_C"]).abs()
        min_pairs["wind_diff"] = (min_pairs["Wind Speed (kt)"] - min_pairs["wind speed_kt_actual"]).abs()

        # Wind direction difference (if both columns present)
        if "Wind Direction" in min_pairs.columns and "actual_wind_direction" in min_pairs.columns:
            min_pairs["wind_dir_diff"] = min_pairs.apply(
                lambda row: circular_difference(
                    float(row["actual_wind_direction"]),
                    float(row["Wind Direction"])
                ) if pd.notnull(row["actual_wind_direction"]) and pd.notnull(row["Wind Direction"]) else np.nan,
                axis=1
            )
            local_area_params = get_verification_params("local_area") or {}
            temp_threshold = float(local_area_params.get("temp_threshold", 2))
            wind_speed_threshold = float(local_area_params.get("wind_speed_threshold", 10))
            wind_dir_threshold = float(local_area_params.get("wind_dir_threshold", 30))

            min_pairs["wind_dir_correct"] = min_pairs["wind_dir_diff"] <= wind_dir_threshold
            wind_dir_accuracy = round(min_pairs["wind_dir_correct"].mean() * 100, 2)
        else:
            wind_dir_accuracy = None

            local_area_params = get_verification_params("local_area") or {}
            temp_threshold = float(local_area_params.get("temp_threshold", 2))
            wind_speed_threshold = float(local_area_params.get("wind_speed_threshold", 10))

        min_pairs["temp_correct"] = min_pairs["temp_diff"] <= temp_threshold
        min_pairs["wind_correct"] = min_pairs["wind_diff"] <= wind_speed_threshold

        temp_accuracy = round(min_pairs["temp_correct"].mean() * 100, 2)
        wind_accuracy = round(min_pairs["wind_correct"].mean() * 100, 2)

        weather_check_result = validate_forecast_weather_with_metar(forecast_path)
        weather_accuracy_point = weather_check_result["status"]
        weather_accuracy_percentage= weather_check_result["match_percentage"]

        start_dt = datetime.strptime(startTime, "%Y%m%d%H%M")
        formatted_start = start_dt.strftime("%d/%m/%Y %H:%M UTC")
        end_dt = datetime.strptime(endTime, "%Y%m%d%H%M")
        formatted_end = end_dt.strftime("%d/%m/%Y %H:%M UTC")

        result_xlsx = os.path.join(UPPER_AIR_DOWNLOADS_DIR, f"upper_air_verification_{station_id}.xlsx")

        data_rows = []
        altitude_to_fl = {
        3000: "FL 100 (3000 M)",
        2100: "FL 070 (2100 M)",
        1500: "FL 050 (1500 M)",
        900:  "FL 030 (900 M)",
        600:  "FL 020 (600 M)",
        300:  "FL 010 (300 M)"
        }
         
        for _, row in min_pairs.iterrows():
            raw_altitude = row.get("Altitude (m)", None)

            if pd.isnull(raw_altitude) or not isinstance(raw_altitude, (int, float)) or math.isnan(raw_altitude):
                continue  # Skip rows with invalid or missing altitude

            altitude_m = int(raw_altitude)
            closest_alt = min(altitude_to_fl.keys(), key=lambda x: abs(x - altitude_m))
            fl_label = altitude_to_fl[closest_alt] if altitude_m <= 3000 else None

            data_rows.append({
                "date": formatted_start.split()[0],
                "validity": validity_code,
                "fl": fl_label,
                'weather_forecast': weather_check_result.get("forecast_text", ""),   # string
                'weather_matched': weather_check_result["matched_keywords"],
                "forecast_wind_dir": row.get("Wind Direction", ""),
                "forecast_speed": row.get("Wind Speed (kt)", ""),
                "forecast_temp": row.get("Temperature (°C)", ""),
                "actual_wind_dir": row.get("actual_wind_direction", ""),
                "actual_speed": round(row.get("wind speed_kt_actual", 0), 2),
                "actual_temp": row.get("interp_temperature_C", ""),
                "wind_dir_acc": "CORRECT" if row.get("wind_dir_correct") else "INCORRECT",
                "speed_acc": "CORRECT" if row.get("wind_correct") else "INCORRECT",
                "temp_acc": "CORRECT" if row.get("temp_correct") else "INCORRECT",
                "weather_acc": weather_accuracy_point,
                'temp_accuracy': temp_accuracy,
                'wind_accuracy': wind_accuracy,
                'wind_dir_accuracy': wind_dir_accuracy,
            })

        metadata = {"icao": icao, "month_year": start_dt.strftime("%B %Y")}
        weather_info = {
            f"{formatted_start.split()[0]}_{validity_code}": {
                'weather_forecast': weather_check_result.get("forecast_text", ""),
                "matched": weather_check_result.get("matched_keywords", []),
                "accuracy": weather_accuracy_point
            }
        }

        generate_upper_air_verification_xlsx(data_rows, metadata, result_xlsx, weather_info=weather_info)

        return jsonify({
            'file_path': result_xlsx,
            'temp_accuracy': temp_accuracy,
            'wind_accuracy': wind_accuracy,
            'wind_dir_accuracy': wind_dir_accuracy,
            'weather_accuracy': weather_accuracy_percentage,
            'weather_forecast': weather_check_result.get("forecast_text", ""),   # string
            'weather_matched': weather_check_result["matched_keywords"],
            'data': data_rows,
            'metadata': {
                'station_id': station_id,
                'icao': icao,
                'start_time': formatted_start,
                'end_time': formatted_end
            }
        })

    except Exception as e:
        print(f"[ERROR] Exception in process_upper_air: {e}")
        return jsonify({'error': str(e)}), 500
    
@api_bp.route('download/upper_air_csv')
def download_upper_air_csv():
    file_path = request.args.get('file_path')
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    elif file_path.endswith('.xlsx'):
        return send_file(file_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True)

    return jsonify({'error': 'File not found'}), 400


@api_bp.route('/upload_ad_warning', methods=['POST'])
def upload_ad_warning():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not file.filename.lower().endswith('.txt'):
        return jsonify({'error': 'Only .txt files are allowed'}), 400

    ad_warn_dir = os.path.join(DOCKER_VOLUME_MOUNT_POINT, 'ad_warn_data')
    os.makedirs(ad_warn_dir, exist_ok=True)

    warning_file = os.path.join(ad_warn_dir, 'AD_warning.txt')
    file.save(warning_file)

    # Define METAR source and destination. Do not overwrite an explicitly
    # uploaded METAR observation with an older fetched file.
    metar_source = os.path.join(METAR_DATA_DIR, 'metar.txt')
    metar_dest = os.path.join(ad_warn_dir, 'metar.txt')
    uploaded_metar = os.path.join(ad_warn_dir, 'uploaded_metar.txt')

    # Copy the METAR file safely with retries
    if os.path.exists(metar_source) and not os.path.exists(uploaded_metar):
        for attempt in range(5):
            try:
                shutil.copy2(metar_source, metar_dest)
                break
            except PermissionError:
                time.sleep(1)
        else:
            print(f"[ERROR] Could not copy METAR file after multiple attempts.")

    try:
        # Parse the warning file immediately to validate it
        station_code = extract_icao_from_warning(warning_file)
        if not station_code:
            station_code = "VABB"  # Default fallback
        else:
            print(f"[DEBUG] Extracted station code: {station_code}")

        df = parse_warning_file(warning_file, station_code=station_code)

        # Read the file for preview
        with open(warning_file, 'r', encoding='utf-8') as f:
            preview = f.read()

        return jsonify({
            'message': 'File uploaded and parsed successfully',
            'preview': preview
        })

    except Exception as e:
        print(f"[ERROR] Failed to process warning file: {str(e)}")
        return jsonify({'error': f'Failed to process file: {str(e)}'}), 500

@api_bp.route('/upload_ad_metar', methods=['POST'])
def upload_ad_metar():
    """Store the user-selected Aerodrome METAR as the active observation."""
    if 'file' not in request.files:
        return jsonify({'error': 'No METAR file part in the request'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No METAR file selected'}), 400
    if not file.filename.lower().endswith('.txt'):
        return jsonify({'error': 'Only .txt METAR files are allowed'}), 400

    ad_warn_dir = os.path.join(DOCKER_VOLUME_MOUNT_POINT, 'ad_warn_data')
    os.makedirs(ad_warn_dir, exist_ok=True)
    uploaded_metar = os.path.join(ad_warn_dir, 'uploaded_metar.txt')
    canonical_metar = os.path.join(ad_warn_dir, 'metar.txt')
    file.save(uploaded_metar)
    shutil.copy2(uploaded_metar, canonical_metar)

    return jsonify({
        'message': 'METAR observation uploaded successfully',
        'filename': file.filename,
    }), 200

@api_bp.route('/adwrn_verify', methods=['POST'])
def adwrn_verify():
    try:
        # Define base directory and ensure it exists
        ad_warn_dir = os.path.join(DOCKER_VOLUME_MOUNT_POINT, 'ad_warn_data')
        os.makedirs(ad_warn_dir, exist_ok=True)
        
        # Define input and output paths
        warning_file = os.path.join(ad_warn_dir, 'AD_warning.txt')
        uploaded_metar = os.path.join(ad_warn_dir, 'uploaded_metar.txt')
        canonical_metar = os.path.join(ad_warn_dir, 'metar.txt')
        metar_file = None
        if os.path.exists(uploaded_metar):
            metar_file = uploaded_metar
        elif os.path.exists(canonical_metar):
            metar_file = canonical_metar
        else:
            excluded = {
                'AD_warning.txt',
                'metar_extracted_features.txt',
            }
            candidates = [
                f for f in os.listdir(ad_warn_dir)
                if f.lower().endswith('.txt') and f not in excluded
            ]
            metar_candidates = [f for f in candidates if 'metar' in f.lower()]
            if metar_candidates:
                metar_candidates.sort(
                    key=lambda x: os.path.getmtime(os.path.join(ad_warn_dir, x)),
                    reverse=True,
                )
                metar_file = os.path.join(ad_warn_dir, metar_candidates[0])

        if not metar_file or not os.path.exists(metar_file):
            return jsonify({
                'success': False,
                'error': 'No METAR file found. Please fetch METAR using OGIMET first.'
            }), 404

        try:
            mtime = os.path.getmtime(metar_file)
            internal_month_year = extract_date_from_metar_file(metar_file)
            with open(metar_file, 'r', encoding='utf-8') as mf:
                preview_lines = [next(mf).strip() for _ in range(5)]
        except Exception as diag_e:
            print(f"[WARN] Could not read METAR diagnostics: {diag_e}")

        metar_canonical = os.path.join(ad_warn_dir, 'metar.txt')

        try:
            # Skip if same file
            if os.path.abspath(metar_file) != os.path.abspath(metar_canonical):

                # Retry mechanism for Windows lock
                for attempt in range(3):
                    try:
                        shutil.copy2(metar_file, metar_canonical)
                        break
                    except PermissionError:
                        time.sleep(0.5)
                else:
                    print("[ERROR] Could not copy METAR after retries.")

                metar_file = metar_canonical
            else:
                print("[DEBUG] Source and destination are the same. Skipping copy.")

        except Exception as e:
            print(f"[WARN] Failed to copy selected METAR to canonical path: {e}")

        # remove stale intermediate files so generation is fresh
        stale_files = [
            os.path.join(ad_warn_dir, 'AD_warn_output.csv'),
            os.path.join(ad_warn_dir, 'metar_extracted_features.txt'),
            os.path.join(ad_warn_dir, 'final_warning_report.csv')
        ]
        for p in stale_files:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    print(f"[DEBUG] Removed stale file: {p}")
            except Exception as rem_e:
                print(f"[WARN] Could not remove stale file {p}: {rem_e}")
        
        
        # Check if required files exist
        if not os.path.exists(warning_file):
            return jsonify({'success': False, 'error': 'Warning file not found. Please upload it first.'}), 404
            
        if not os.path.exists(metar_file):
            return jsonify({'success': False, 'error': 'METAR file not found. Please ensure it exists.'}), 404
        
        # Perform validation before processing
        validation_result = validate_files(metar_file, warning_file)
        
        if not validation_result['success']:
            return jsonify({
                'success': False, 
                'error': validation_result['error'],
                'validation_failed': True,
                'metar_code': validation_result.get('metar_code'),
                'warning_code': validation_result.get('warning_code')
            }), 400
        
        # Parse warning file
        df = parse_warning_file(warning_file, station_code=validation_result['metar_code'])

        ad_warn_output = os.path.join(ad_warn_dir, 'AD_warn_output.csv')
        metar_features = os.path.join(ad_warn_dir, 'metar_extracted_features.txt')
        
        
        # Extract METAR features
        try:
            extract_metar_features(ad_warn_output, metar_file, metar_features)
        except Exception as e:
            print(f"[ERROR] Failed to extract METAR features: {str(e)}")
            raise
        
    
        aerodrome_params = get_verification_params("aerodrome") or {}
        final_df, accuracy = generate_warning_report(
            ad_warn_output,
            metar_features,
            wind_dir_tolerance=float(aerodrome_params.get("wind_dir_tolerance", 30)),
            wind_speed_gust_threshold=float(aerodrome_params.get("wind_speed_gust_threshold", 14)),
        )
        
        # Debug accuracy value
        print(f"[DEBUG] Accuracy type: {type(accuracy)}, value: {accuracy}")
        # Read the report content
        report_file = os.path.join(ad_warn_dir, 'final_warning_report.csv')
        
        if not os.path.exists(report_file):
            return jsonify({'success': False, 'error': 'Failed to generate report file'}), 500
             
        # Calculate detailed accuracy percentages
        thunderstorm_accuracy = 0
        wind_accuracy = 0
        dust_storm_accuracy = 0
        sand_storm_accuracy = 0
        overall_accuracy = 0
        
        try:            

            with open(report_file, 'r', encoding='utf-8') as f:
                report_content = f.read()
            
            csv_data = StringIO(report_content)
            csv_reader = csv.DictReader(csv_data)
            
            thunderstorm_count = 0
            thunderstorm_correct = 0
            wind_count = 0
            wind_correct = 0
            dust_storm_count = 0
            dust_storm_correct = 0
            sand_storm_count = 0
            sand_storm_correct = 0
            total_count = 0
            total_correct = 0
            
            for row in csv_reader:
                element = row.get('Elements (Thunderstorm/Surface wind & Gust)', '')
                accuracy_val = row.get('true-1 / false-0', '0')
                
                # For thunderstorm: count all entries containing 'Thunderstorm'
                if 'Thunderstorm' in element:
                    thunderstorm_count += 1
                    total_count += 1
                    if accuracy_val == '1':
                        thunderstorm_correct += 1
                        total_correct += 1

                if element == 'Dust Storm warning':
                    dust_storm_count += 1
                    total_count += 1
                    if accuracy_val == '1':
                        dust_storm_correct += 1
                        total_correct += 1

                if element == 'Sand Storm warning':
                    sand_storm_count += 1
                    total_count += 1
                    if accuracy_val == '1':
                        sand_storm_correct += 1
                        total_correct += 1
                        
                # For gust: count ONLY pure 'Gust warning' entries (not combined entries)
                elif element == 'Gust warning':
                    wind_count += 1
                    total_count += 1
                    if accuracy_val == '1':
                        wind_correct += 1
                        total_correct += 1
            
            # Calculate percentages (same logic as Excel generation)
            if thunderstorm_count > 0:
                thunderstorm_accuracy = int((thunderstorm_correct / thunderstorm_count) * 100)
            
            if wind_count > 0:
                wind_accuracy = int((wind_correct / wind_count) * 100)

            if dust_storm_count > 0:
                dust_storm_accuracy = int((dust_storm_correct / dust_storm_count) * 100)

            if sand_storm_count > 0:
                sand_storm_accuracy = int((sand_storm_correct / sand_storm_count) * 100)
            
            if total_count > 0:
                overall_accuracy = int((total_correct / total_count) * 100)
                
            print(f"  Thunderstorm: {thunderstorm_correct}/{thunderstorm_count} = {thunderstorm_accuracy}%")
            print(f"  Wind (pure Gust warning only): {wind_correct}/{wind_count} = {wind_accuracy}%")
            print(f"  Dust Storm: {dust_storm_correct}/{dust_storm_count} = {dust_storm_accuracy}%")
            print(f"  Sand Storm: {sand_storm_correct}/{sand_storm_count} = {sand_storm_accuracy}%")
            print(f"  Overall: {total_correct}/{total_count} = {overall_accuracy}%")
                
        except Exception as e:
            print(f"Error calculating detailed accuracy: {e}")
        try:
            if isinstance(accuracy, (int, float)):
                accuracy_str = f"{accuracy:.0f}"
            else:
                accuracy_str = str(accuracy)
        except Exception as e:
            print(f"[DEBUG] Error formatting accuracy: {e}")
            accuracy_str = str(accuracy)
        
        station_info = ""
        validity_info = ""
        try:
            # Get station code from validation result
            global station, month_year
            station = validation_result.get('metar_code', 'VABB')
            
            # Extract date information from METAR file
            month_year = extract_date_from_metar_file(metar_file)
            
            if station and month_year:
                station_info = f"Aerodrome warning for station {station} for {month_year}"
            elif station:
                station_info = f"Aerodrome warning for station {station}"
                
            
        except Exception as e:
            print(f"[DEBUG] Error extracting station info: {e}")
        
        if station_info:
            max_retries = 5
            retry_count = 0
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    with open(report_file, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                    
                    time.sleep(0.2)
                    
                    with open(report_file, 'w', encoding='utf-8') as f:
                        f.write(station_info + '\n')
                        f.write(original_content)
                    
                    success = True
                    
                except PermissionError as e:
                    retry_count += 1
                    if retry_count < max_retries:
                        wait_time = 0.5 * (2 ** (retry_count - 1))
                        print(f"[WARN] Report file locked ({retry_count}/{max_retries}), waiting {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"[ERROR] Could not write station info after {max_retries} attempts")
                        break
                        
                except Exception as e:
                    print(f"[ERROR] Unexpected error prepending station info: {e}")
                    break


        response_data = {
            'success': True, 
            'report': report_content, 
            'accuracy': f"{overall_accuracy}",
            'detailed_accuracy': {
                'thunderstorm': thunderstorm_accuracy,
                'wind': wind_accuracy,
                'dust_storm': dust_storm_accuracy,
                'sand_storm': sand_storm_accuracy,
                'overall': overall_accuracy
            },
            'counts': {
                'thunderstorm_count': thunderstorm_count,
                'wind_count': wind_count,
                'dust_storm_count': dust_storm_count,
                'sand_storm_count': sand_storm_count
            },
            'validation': {
                'metar_code': validation_result['metar_code'],
                'warning_code': validation_result['warning_code']
            },
            'station_info': station_info,
            'validity_info': validity_info
        }
        
        print(f"[DEBUG] Sending response with detailed accuracy: {response_data['detailed_accuracy']}")
        
        return jsonify(response_data)
    except Exception as e:
        print(f"[ERROR] Error in adwrn_verify: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/download/adwrn_report', methods=['GET'])
def download_adwrn_report():
    """Download the aerodrome warning report CSV file"""
    try:
        # Look for the generated report file - check both possible locations
        report_file = os.path.join(AD_WARN_DIR, 'final_warning_report.csv')
     
        
        # If not found in METAR_DATA_DIR, check in the root ad_warn_data directory
        if not os.path.exists(report_file):
            # Check in the root directory
            root_ad_warn_dir = AD_WARN_DIR
            report_file = os.path.join(root_ad_warn_dir, 'final_warning_report.csv')
            
            
            if not os.path.exists(report_file):
                # Check for any CSV file in the root ad_warn_data directory
                
                if os.path.exists(root_ad_warn_dir):
                    csv_files = [f for f in os.listdir(root_ad_warn_dir) if f.endswith('.csv')]
                    if csv_files:
                        # Use the most recent CSV file
                        csv_files.sort(key=lambda x: os.path.getmtime(os.path.join(root_ad_warn_dir, x)), reverse=True)
                        report_file = os.path.join(root_ad_warn_dir, csv_files[0])
                    else:
                        return jsonify({"error": "No aerodrome warning report found"}), 404
                else:
                    return jsonify({"error": "Aerodrome warning data directory not found"}), 404
            
        
        if not os.path.exists(report_file):
            return jsonify({"error": "Aerodrome warning report not found"}), 404
        
        with open(report_file, 'r', encoding='utf-8') as f:
            csv_content = f.read()
        
        # Get global variables for description
        global station, month_year
        
        station_code = station if station else "N/A"
        month_year_str = month_year if month_year else "N/A"
        
        # Create new content with description line
        new_content = f"{csv_content}"
        
        # Create a BytesIO object with the modified content
        output = io.BytesIO()
        output.write(new_content.encode('utf-8'))
        output.seek(0)
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name='detailed_aerodrome_wrng_results.csv'
        )
    except Exception as e:
        return jsonify({"error": f"An error occurred while downloading the report: {str(e)}"}), 500


@api_bp.route('/download/adwrn_table', methods=['GET'])
def download_adwrn_table():
    """Download the aerodrome warnings table in the exact format requested"""
    try:
        # Define base directory and ensure it exists
        ad_warn_dir = AD_WARN_DIR
        
        # Define input and output paths
        ad_warn_output = os.path.join(ad_warn_dir, 'AD_warn_output.csv')
        metar_features = os.path.join(ad_warn_dir, 'metar_extracted_features.txt')
        
        # Check if required files exist
        if not os.path.exists(ad_warn_output):
            return jsonify({"error": "Aerodrome warning output file not found. Please run verification first."}), 404
            
        if not os.path.exists(metar_features):
            return jsonify({"error": "METAR features file not found. Please run verification first."}), 404
        
        # Generate the specific table format
        table_file_path = generate_aerodrome_warnings_table(ad_warn_output, metar_features)
        
        if not os.path.exists(table_file_path):
            return jsonify({"error": "Failed to generate aerodrome warnings table"}), 500
        
        return send_file(
            table_file_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Aerodrome_Warnings_Table.xlsx'
        )
    except Exception as e:
        print(f"Error downloading aerodrome warnings table: {str(e)}")
        return jsonify({"error": f"An error occurred while downloading the table: {str(e)}"}), 500


@api_bp.route("/logs/user/<int:user_id>", methods=["GET"])
@require_role("admin")
def get_user_logs(user_id, current_user):
    """Admin endpoint to get specific user's activity logs"""
    try:
        user = User.query.get_or_404(user_id)
        
        if user.role != "user":
            return jsonify({"error": "Can only view user logs"}), 403
        
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        
        logs = UserActivity.query.filter_by(user_id=user_id)\
            .order_by(UserActivity.timestamp.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        
        return jsonify({
            "user": {
                "id": user.id,
                "username": user.username,
                "station": user.station_code
            },
            "logs": [log.to_dict() for log in logs.items],
            "total": logs.total,
            "pages": logs.pages,
            "current_page": page
        }), 200
    except Exception as e:
        print(f"Error fetching user logs: {e}")
        return jsonify({"error": "Failed to fetch logs"}), 500


@api_bp.route("/logs/all", methods=["GET"])
@require_role("admin")
def get_all_logs(current_user):
    """Admin endpoint to get all users' activity logs. Super admin can filter by user_id"""
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 100, type=int)
        activity_type = request.args.get("activity_type", None)  # Optional filter
        username = (request.args.get("username", None) or "").strip()  # Optional filter by username
        user_id = request.args.get("user_id", None)  # Optional filter for super admin
        
        # Build base query and join User so role-based filtering is possible
        query = UserActivity.query.join(User)

        # Admins should only see logs for normal users (role == 'user')
        if current_user.role == "admin":
            query = query.filter(User.role == "user")

        if activity_type:
            query = query.filter(UserActivity.activity_type == activity_type)

        if username:
            query = query.filter(User.username.ilike(f"%{username}%"))

        # Super admins can filter by specific user id
        if user_id and current_user.role == "super_admin":
            try:
                user_id = int(user_id)
                query = query.filter(UserActivity.user_id == user_id)
            except ValueError:
                pass

        logs = query.order_by(UserActivity.timestamp.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            "logs": [log.to_dict() for log in logs.items],
            "total": logs.total,
            "pages": logs.pages,
            "current_page": page
        }), 200
    except Exception as e:
        print(f"Error fetching all logs: {e}")
        return jsonify({"error": "Failed to fetch logs"}), 500


@api_bp.route("/logs/stats", methods=["GET"])
@require_role("admin")
def get_log_stats(current_user):
    """Admin endpoint to get activity statistics"""
    try:
        # Build base query joined with User so we can apply role-based filters
        base_query = UserActivity.query.join(User)
        if current_user.role == "admin":
            # Restrict stats to normal users for admin role
            base_query = base_query.filter(User.role == "user")

        total_logins = base_query.filter(UserActivity.activity_type == "login").count()
        total_logouts = base_query.filter(UserActivity.activity_type == "logout").count()
        total_accesses = base_query.filter(UserActivity.activity_type == "access").count()

        # Unique users (apply same role filter for admins)
        unique_users_query = db.session.query(UserActivity.user_id).join(User)
        if current_user.role == "admin":
            unique_users_query = unique_users_query.filter(User.role == "user")
        unique_users = unique_users_query.distinct().count()

        # Get active users (logged in last 24 hours)
        last_24h = datetime.utcnow() - timedelta(hours=24)
        active_query = db.session.query(UserActivity.user_id).join(User)
        if current_user.role == "admin":
            active_query = active_query.filter(User.role == "user")
        active_users = active_query.filter(
            UserActivity.timestamp >= last_24h
        ).distinct().count()

        return jsonify({
            "total_logins": total_logins,
            "total_logouts": total_logouts,
            "total_accesses": total_accesses,
            "unique_users": unique_users,
            "active_users_24h": active_users
        }), 200
    except Exception as e:
        print(f"Error fetching log stats: {e}")
        return jsonify({"error": "Failed to fetch statistics"}), 500


@api_bp.route("/logs/clear", methods=["POST"])
@require_role("super_admin")
def clear_logs(current_user):
    """Super admin endpoint to clear all logs"""
    try:
        UserActivity.query.delete()
        db.session.commit()
        return jsonify({"message": "All logs cleared successfully"}), 200
    except Exception as e:
        print(f"Error clearing logs: {e}")
        db.session.rollback()
        return jsonify({"error": "Failed to clear logs"}), 500


# @api_bp.route("/logs/log-access", methods=["POST"])
# @require_role("user")
# def log_page_access(current_user):
#     """Log user accessing a page/route"""
#     try:
#         data = request.json
#         page_or_route = data.get("page", None)
#         # tab_id = request.headers.get("X-Tab-ID", "default")
        
#         log_activity(current_user, "access", page_or_route=page_or_route)
        
#         return jsonify({"message": "Activity logged"}), 200
#     except Exception as e:
#         print(f"Error logging page access: {e}")
#         return jsonify({"error": "Failed to log activity"}), 500


@api_bp.route("/logs/log-verification", methods=["POST"])
@require_role("user")
def log_verification_activity(current_user):
    """Log verification activity (METAR processing, upper air, etc.)"""
    try:
        data = request.json
        verification_type = data.get("verification_type", "unknown")
        details = data.get("details", None)
        # tab_id = request.headers.get("X-Tab-ID", "default")
        
        # Create detailed log message
        log_details = f"Verification Type: {verification_type}"
        if details:
            log_details += f" | Details: {details}"
        
        log_activity(current_user, "verification", page_or_route="/api/process_verification", details=log_details)
        
        return jsonify({"message": "Verification logged"}), 200
    except Exception as e:
        print(f"Error logging verification: {e}")
        return jsonify({"error": "Failed to log verification"}), 500


@api_bp.route("/logs/users-list", methods=["GET"])
@require_role("super_admin")
def get_users_list(current_user):
    """Super admin endpoint to get list of all users for filtering"""
    try:
        users = User.query.filter_by(role="user").all()
        return jsonify({
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "station": u.station_code
                }
                for u in users
            ]
        }), 200
    except Exception as e:
        print(f"Error fetching users list: {e}")
        return jsonify({"error": "Failed to fetch users"}), 500


from app.backend.models import db
