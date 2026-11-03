"""Dated OGIMET/archive METAR collection and lightweight AD WRNG hazard models."""
import calendar
import csv
import io
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from app.backend.config import AD_WARN_DIR

UTC = timezone.utc
METAR_RE = re.compile(r'^(\d{12})\s+((?:METAR|SPECI)\s+)?([A-Z]{4})\s+(.*)$')
REPORT_STATION_RE = re.compile(r'^(?:(?:METAR|SPECI)\s+)?([A-Z]{4})\b')
REPORT_TIME_RE = re.compile(r'\b(\d{6})Z\b')
WIND_RE = re.compile(r'\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b')
MIN_ROWS = 500
MIN_EVENTS = 20
MAX_HISTORY_DAYS = 1095
OGIMET_URL = 'http://www.ogimet.com/cgi-bin/getmetar'
OGIMET_REQUEST_INTERVAL = 1.2
OGIMET_TIMEOUT = (5, 30)
_request_lock = threading.Lock()
_last_request_at = 0.0


def _valid_station(station):
    return isinstance(station, str) and re.fullmatch(r'[A-Z]{4}', station) is not None


def _parse_timestamped_report(timestamp, report, station):
    """Validate ICAO and report day/time before accepting an explicitly dated row."""
    if not _valid_station(station) or not report:
        return None
    station_match = REPORT_STATION_RE.match(report.strip().upper())
    if (not station_match or station_match.group(1) != station
            or re.search(r'\bNIL\b', report, re.I) or not WIND_RE.search(report)):
        return None
    time_match = REPORT_TIME_RE.search(report)
    if not time_match or time_match.group(1) != timestamp.strftime('%d%H%M'):
        return None
    return report.strip()


def _parse_archive(station):
    reports = {}
    if not _valid_station(station):
        return []
    for path in Path(AD_WARN_DIR).glob('*.txt'):
        try:
            with path.open(encoding='utf-8', errors='ignore') as stream:
                for line in stream:
                    match = METAR_RE.match(line.strip())
                    if not match or match.group(3) != station:
                        continue
                    try:
                        when = datetime.strptime(match.group(1), '%Y%m%d%H%M').replace(tzinfo=UTC)
                    except ValueError:
                        continue
                    report_text = f"{match.group(2) or 'METAR '}{station} {match.group(4)}"
                    report = _parse_timestamped_report(when, report_text, station)
                    if report:
                        reports[when] = report
        except OSError:
            continue
    return sorted(reports.items())


def _parse_ogimet_csv(body, station):
    """Parse OGIMET's dated CSV, including its Spanish header aliases."""
    if not _valid_station(station):
        return []
    parsed = {}
    reader = csv.DictReader(io.StringIO(body.lstrip('\ufeff')))
    aliases = {'ESTACION': 'ICAOIND', 'ANO': 'YEAR', 'MES': 'MONTH', 'DIA': 'DAY',
               'HORA': 'HOUR', 'MINUTO': 'MIN'}
    normalized_headers = [aliases.get(str(value or '').strip().lstrip('\ufeff').upper(),
                                      str(value or '').strip().lstrip('\ufeff').upper())
                          for value in (reader.fieldnames or [])]
    required = {'ICAOIND', 'YEAR', 'MONTH', 'DAY', 'HOUR', 'MIN'}
    headers = set(normalized_headers)
    if not required.issubset(headers) or not ({'REPORT', 'PARTE'} & headers):
        raise ValueError('OGIMET response did not contain the documented dated METAR CSV columns.')
    for raw in reader:
        row = {aliases.get(str(key or '').strip().lstrip('\ufeff').upper(),
                           str(key or '').strip().lstrip('\ufeff').upper()): (value or '').strip()
               for key, value in raw.items()}
        if row.get('ICAOIND', '').upper() != station:
            continue
        try:
            when = datetime(
                int(row['YEAR']), int(row['MONTH']), int(row['DAY']),
                int(row['HOUR']), int(row['MIN']), tzinfo=UTC,
            )
        except (KeyError, TypeError, ValueError):
            continue
        report = _parse_timestamped_report(when, row.get('REPORT') or row.get('PARTE'), station)
        if report:
            parsed[when] = report
    return sorted(parsed.items())


def _month_start(year, month):
    return datetime(year, month, 1, tzinfo=UTC)


def _cache_dir():
    path = Path(AD_WARN_DIR) / 'adwrn_ml_ogimet_cache'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_fresh(path, year, month, now):
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('complete') is not True or not isinstance(payload.get('records'), list):
            return None
        fetched = datetime.fromisoformat(payload['fetched_at'].replace('Z', '+00:00'))
        ttl = timedelta(hours=6) if (year, month) == (now.year, now.month) else timedelta(days=30)
        if now - fetched > ttl:
            return None
        return _parse_ogimet_records(payload['records'], 'VABB')
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _parse_ogimet_records(records, station):
    parsed = {}
    for row in records:
        try:
            when = datetime.strptime(row['timestamp'], '%Y%m%d%H%M').replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            continue
        report = _parse_timestamped_report(when, row.get('report', ''), station)
        if report:
            parsed[when] = report
    return sorted(parsed.items())


def _request_ogimet(begin, end, station):
    """Request one bounded UTC interval, spacing outbound calls to avoid load."""
    global _last_request_at
    with _request_lock:
        wait = OGIMET_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(
            OGIMET_URL,
            params={'begin': begin.strftime('%Y%m%d%H%M'), 'end': end.strftime('%Y%m%d%H%M'),
                    'icao': station, 'lang': 'eng', 'header': 'yes'},
            timeout=OGIMET_TIMEOUT,
        )
        _last_request_at = time.monotonic()
    response.raise_for_status()
    return _parse_ogimet_csv(response.text, station)


def _get_month(station, year, month, now):
    path = _cache_dir() / f'{station}_{year:04d}{month:02d}.json'
    cached = _cache_fresh(path, year, month, now)
    if cached is not None:
        return cached, 'cache', None
    begin = _month_start(year, month)
    end = datetime(year, month, calendar.monthrange(year, month)[1], 23, 59, tzinfo=UTC)
    stale = []
    try:
        stale_payload = json.loads(path.read_text(encoding='utf-8'))
        stale = _parse_ogimet_records(stale_payload.get('records', []), station)
    except (OSError, ValueError, TypeError):
        pass
    try:
        records = _request_ogimet(begin, end, station)
        payload = {
            'station': station, 'begin_utc': begin.strftime('%Y%m%d%H%M'),
            'end_utc': end.strftime('%Y%m%d%H%M'),
            'fetched_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'complete': True,
            'records': [{'timestamp': when.strftime('%Y%m%d%H%M'), 'report': report}
                        for when, report in records],
        }
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        temporary.replace(path)
        return records, 'ogimet', None
    except (requests.RequestException, OSError, ValueError) as exc:
        return stale, 'stale_cache' if stale else 'failed', str(exc)


def _collect_training_history(station, start, end, issue):
    archive = _parse_archive(station)
    archive = [(when, report) for when, report in archive if start <= when <= end and when < issue]
    merged = {when: (report, 'archive') for when, report in archive}
    counts = {'archive_valid': len(merged), 'ogimet_valid': 0, 'cached_months': 0,
              'fetched_months': 0, 'requested_months': 0, 'failed_months': 0, 'errors': []}
    cache_now = datetime.now(UTC)
    cursor = _month_start(start.year, start.month)
    while cursor <= end:
        counts['requested_months'] += 1
        records, source, error = _get_month(station, cursor.year, cursor.month, cache_now)
        if source in ('cache', 'stale_cache'):
            counts['cached_months'] += 1
        if source == 'ogimet':
            counts['fetched_months'] += 1
        if error:
            counts['failed_months'] += 1
            counts['errors'].append(f'{cursor:%Y-%m}: {error[:180]}')
        for when, report in records:
            if start <= when <= end and when < issue:
                counts['ogimet_valid'] += 1
                # Prefer the source's explicit timestamped record over archive copies.
                merged[when] = (report, 'ogimet')
        if cursor.month == 12:
            cursor = _month_start(cursor.year + 1, 1)
        else:
            cursor = _month_start(cursor.year, cursor.month + 1)
    rows = [(when, value[0]) for when, value in sorted(merged.items())]
    counts['combined_valid'] = len(rows)
    counts['start_utc'] = start.strftime('%Y-%m-%d')
    counts['end_utc'] = end.strftime('%Y-%m-%d')
    counts['end_cutoff_utc'] = end.strftime('%Y-%m-%d %H:%MZ')
    return rows, counts


def _cache_issue_observation(station, issue, lookback_minutes):
    path = _cache_dir() / f'{station}_obs_{issue:%Y%m%d%H%M}_{lookback_minutes}.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        fetched = datetime.fromisoformat(payload['fetched_at'].replace('Z', '+00:00'))
        ttl = timedelta(minutes=10) if issue.date() == datetime.now(UTC).date() else timedelta(days=30)
        if datetime.now(UTC) - fetched <= ttl:
            records = _parse_ogimet_records(payload.get('records', []), station)
            available = [(when, report) for when, report in records
                         if issue - timedelta(minutes=lookback_minutes) <= when <= issue]
            if available:
                return available[-1], 'cache', None
            return None, 'cache', 'No matching METAR in the cached OGIMET response.'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    begin = issue - timedelta(minutes=lookback_minutes)
    try:
        records = _request_ogimet(begin, issue, station)
        payload = {
            'fetched_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'records': [{'timestamp': when.strftime('%Y%m%d%H%M'), 'report': report}
                        for when, report in records],
        }
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        temporary.replace(path)
        available = [(when, report) for when, report in records
                     if begin <= when <= issue]
        return (available[-1], 'ogimet', None) if available else (None, 'ogimet', 'No matching METAR in OGIMET response.')
    except (requests.RequestException, OSError, ValueError) as exc:
        return None, 'failed', str(exc)


def _features(when, report):
    wind = WIND_RE.search(report)
    speed = float(wind.group(2)) if wind else 0.0
    gust = float(wind.group(3)) if wind and wind.group(3) else speed
    direction = float(wind.group(1)) if wind and wind.group(1).isdigit() and speed > 0 else None
    visibility_match = re.search(r'\b(\d{4})\b', report)
    visibility = min(float(visibility_match.group(1)), 10000.0) / 1000 if visibility_match else 0.0
    temp = re.search(r'\b(M?\d{2})/(M?\d{2})\b', report)
    temp_spread = 0.0
    if temp:
        def signed(value):
            return -float(value[1:]) if value.startswith('M') else float(value)
        temp_spread = signed(temp.group(1)) - signed(temp.group(2))
    minute_of_day = when.hour * 60 + when.minute
    day_of_year = when.timetuple().tm_yday
    return np.array([
        np.sin(2 * np.pi * minute_of_day / 1440), np.cos(2 * np.pi * minute_of_day / 1440),
        np.sin(2 * np.pi * day_of_year / 365.25), np.cos(2 * np.pi * day_of_year / 365.25),
        speed / 50, gust / 60,
        np.sin(np.deg2rad(direction)) if direction is not None else 0.0,
        np.cos(np.deg2rad(direction)) if direction is not None else 0.0,
        visibility / 10, temp_spread / 20,
        float(bool(re.search(r'\b(?:[-+]SHRA|[-+]RA|DZ|SHRA|RA)\b', report))),
        float(bool(re.search(r'(?:CB|TCU)\b', report))),
        float(visibility_match is not None), float(temp is not None),
    ], dtype=float), (speed, gust, direction, wind.group(1) if wind else None)


def _sigmoid(value):
    return 1 / (1 + np.exp(-np.clip(value, -30, 30)))


def _fit_logistic(x, y, steps=1200, rate=0.05, balance=True):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1
    z = (x - mean) / scale
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1])
    pos = max(int(y.sum()), 1)
    neg = max(len(y) - pos, 1)
    weights = np.where(y > 0, len(y) / (2 * pos), len(y) / (2 * neg)) if balance else np.ones(len(y))
    for _ in range(steps):
        grad = design.T @ ((_sigmoid(design @ beta) - y) * weights) / len(y)
        grad[1:] += 0.01 * beta[1:]
        beta -= rate * grad
    return mean, scale, beta


def _prob(model, x):
    mean, scale, beta = model
    return _sigmoid(np.column_stack([np.ones(len(x)), (x - mean) / scale]) @ beta)


def _metrics(y, p, threshold):
    pred = p >= threshold
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    tn = int(np.sum(~pred & (y == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        'samples': int(len(y)), 'events': int(y.sum()),
        'accuracy': round((tp + tn) / len(y), 3), 'precision': round(precision, 3),
        'recall': round(recall, 3),
        'f1': round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        'brier': round(float(np.mean((p - y) ** 2)), 4),
    }


def _train_and_score(x, y):
    n = len(y)
    a, b = int(n * 0.70), int(n * 0.85)
    if a < 200 or b - a < 50 or n - b < 50:
        return None
    if y[:a].sum() < MIN_EVENTS or y[a:b].sum() < 3 or y[b:].sum() < 3:
        return None
    model = _fit_logistic(x[:a], y[:a])
    val_raw = _prob(model, x[a:b])
    logits = np.log(np.clip(val_raw, 1e-5, 1 - 1e-5) / np.clip(1 - val_raw, 1e-5, 1))
    calibration = _fit_logistic(logits[:, None], y[a:b], steps=1500, rate=0.02, balance=False)

    def calibrated(raw):
        score = np.log(np.clip(raw, 1e-5, 1 - 1e-5) / np.clip(1 - raw, 1e-5, 1))
        return _prob(calibration, score[:, None])

    val_probability = calibrated(val_raw)
    thresholds = np.linspace(0.1, 0.9, 17)
    threshold = max(thresholds, key=lambda value: _metrics(y[a:b], val_probability, value)['f1'])
    test_probability = calibrated(_prob(model, x[b:]))
    metrics = _metrics(y[b:], test_probability, threshold)
    baseline = float(np.mean(y[b:]))
    baseline_brier = float(np.mean((y[b:] - baseline) ** 2))
    brier_skill = 1 - metrics['brier'] / baseline_brier if baseline_brier else 0.0
    metrics.update({'baseline_brier': round(baseline_brier, 4), 'brier_skill': round(brier_skill, 3),
                    'alert_threshold': round(float(threshold), 3)})
    return model, calibration, float(threshold), metrics, brier_skill > 0


def _make_samples(rows, issue, excluded_observation):
    parsed = [(when, report, *_features(when, report)) for when, report in rows]
    samples = []
    for i, (when, report, feature, current) in enumerate(parsed[:-1]):
        future_time, future_report, _, next_values = parsed[i + 1]
        delta = (future_time - when).total_seconds() / 60
        if (delta < 25 or delta > 40 or future_time >= issue or when == excluded_observation
                or future_time == excluded_observation):
            continue
        ts = int(bool(re.search(r'(?<![A-Z])[-+]?TSRA(?![A-Z])', future_report)))
        gust_match = WIND_RE.search(future_report)
        gust_event = int(bool(gust_match and int(gust_match.group(3) or 0) >= 25))
        samples.append((when, feature, ts, gust_event, current, next_values))
    return samples


def _fit_regressor(x, y):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    beta = np.linalg.lstsq(design, y, rcond=None)[0]
    return mean, scale, beta


def _regress(model, x):
    mean, scale, beta = model
    return np.column_stack([np.ones(len(x)), (x - mean) / scale]) @ beta


def predict(station, issue, history_start, history_end, observation_lookback_minutes=60, refresh=False):
    if not _valid_station(station) or station != 'VABB':
        return {'status': 'invalid_station', 'message': 'Only the configured VABB station is supported.', 'warnings': [], 'metrics': None}
    try:
        issue_dt = datetime.strptime(issue, '%Y%m%d%H%M').replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return {'status': 'invalid_range', 'message': 'Issue time must use UTC YYYYMMDDHHMM.', 'warnings': [], 'metrics': None}
    saved = None
    if not refresh:
        try:
            saved = json.loads((Path(AD_WARN_DIR) / 'adwrn_ml_setup.json').read_text(encoding='utf-8'))
            history_start, history_end = saved['history_start'], saved['history_end']
        except (OSError, ValueError, KeyError):
            return {'status': 'setup_required', 'message': 'One-time setup is required. Set a UTC history range and select Refresh History & Retrain before generating forecasts.', 'warnings': [], 'metrics': None}
    try:
        start = datetime.strptime(history_start, '%Y-%m-%d').replace(tzinfo=UTC)
        end_date = datetime.strptime(history_end, '%Y-%m-%d').date()
        end = datetime.combine(end_date, datetime.max.time(), tzinfo=UTC).replace(second=0, microsecond=0)
    except (TypeError, ValueError):
        return {'status': 'invalid_range', 'message': 'Provide valid UTC history start/end dates for refresh.', 'warnings': [], 'metrics': None}
    if end < start or (end.date() - start.date()).days + 1 > MAX_HISTORY_DAYS:
        return {'status': 'invalid_range', 'message': f'History range must be ordered and no longer than {MAX_HISTORY_DAYS} days.', 'warnings': [], 'metrics': None}
    if refresh and start >= issue_dt:
        return {'status': 'invalid_range', 'message': 'Historical training start must precede the issue time.', 'warnings': [], 'metrics': None}
    if observation_lookback_minutes < 30 or observation_lookback_minutes > 180:
        return {'status': 'invalid_range', 'message': 'Observation lookback must be between 30 and 180 minutes.', 'warnings': [], 'metrics': None}
    end = min(end, issue_dt - timedelta(minutes=1))
    if saved and saved.get('records'):
        rows = [(datetime.fromisoformat(item[0]).astimezone(UTC), item[1]) for item in saved['records']]
        source_stats = saved['data_stats']
    else:
        rows, source_stats = _collect_training_history(station, start, end, issue_dt)
    archive_all = _parse_archive(station)
    observation = [(when, report) for when, report in archive_all
                   if issue_dt - timedelta(minutes=observation_lookback_minutes) <= when <= issue_dt]
    observation_source = 'archive'
    observation_error = None
    if observation:
        observation_time, observation_report = observation[-1]
    else:
        result, observation_source, observation_error = _cache_issue_observation(
            station, issue_dt, observation_lookback_minutes
        )
        if result:
            observation_time, observation_report = result
        else:
            observation_time = observation_report = None
    if refresh:
        observation_time = observation_report = None
    if observation_time is None and not refresh:
        source_stats['issue_observation_valid'] = 0
        source_stats['issue_observation_error'] = observation_error or 'No matching fully timestamped report.'
        return {'status': 'stale_observation', 'message': f"No {station} report found within the configured {observation_lookback_minutes}-minute lookback. {source_stats['issue_observation_error']} Historical training records: {source_stats['combined_valid']} valid (archive {source_stats['archive_valid']}, OGIMET {source_stats['ogimet_valid']}).", 'warnings': [], 'metrics': None, 'data_stats': source_stats}
    observation_age = None
    if observation_time is not None:
        observation_age = int((issue_dt - observation_time).total_seconds() // 60)
        source_stats['issue_observation_valid'] = 1
        source_stats['issue_observation_source'] = observation_source
        source_stats['issue_observation_timestamp_utc'] = observation_time.strftime('%Y-%m-%d %H:%MZ')
        source_stats['issue_observation_age_minutes'] = observation_age
    if observation_age is not None and observation_age > min(observation_lookback_minutes, 30):
        return {'status': 'stale_observation', 'message': f'The {observation_age}-minute-old report exceeds the 30-minute forecast-input limit and was rejected.', 'warnings': [], 'metrics': None, 'data_stats': source_stats}
    if observation_time is not None and observation_time not in [when for when, _ in rows]:
        rows.append((observation_time, observation_report))
        rows.sort(key=lambda item: item[0])
    samples = _make_samples(rows, issue_dt, observation_time)
    source_stats['training_pairs'] = len(samples)
    if len(samples) < MIN_ROWS:
        return {'status': 'insufficient_data', 'message': f"Only {len(samples)} chronological training pairs are available from {source_stats['combined_valid']} valid historical reports in {history_start}–{history_end} UTC; at least {MIN_ROWS} pairs are required. OGIMET valid records: {source_stats['ogimet_valid']} (failed month requests: {source_stats['failed_months']}).", 'warnings': [], 'metrics': None, 'data_stats': source_stats}
    x = np.vstack([row[1] for row in samples])
    targets = {'TSRA': np.array([row[2] for row in samples]), 'GUST25KT': np.array([row[3] for row in samples])}
    models, metrics = {}, {}
    if saved and saved.get('models'):
        for hazard, item in saved['models'].items():
            models[hazard] = (tuple(np.array(part) for part in item['model']), tuple(np.array(part) for part in item['calibration']), item['threshold'], item['metrics'], item['meaningful'])
            metrics[hazard] = item['metrics']
    else:
        for hazard, target in targets.items():
            trained = _train_and_score(x, target)
            if trained:
                models[hazard] = trained
                metrics[hazard] = trained[3]
    if not saved:
        source_stats['evaluation_cutoff_utc'] = issue_dt.strftime('%Y-%m-%d %H:%MZ')
        source_stats['chronological_split'] = {'train': 'first 70%', 'calibration': 'next 15%', 'test': 'final 15% before issue'}
    if not models:
        return {'status': 'insufficient_data', 'message': f"{source_stats['combined_valid']} valid reports yielded {len(samples)} pairs, but there are too few hazard events in the chronological train/calibration/test splits to evaluate a model. OGIMET valid records: {source_stats['ogimet_valid']}.", 'warnings': [], 'metrics': None, 'data_stats': source_stats}
    notes = ['Experimental event definitions: next METAR TS code; gust >=25 kt is a model target only, not an IMD threshold.', 'Intensity is unavailable because the archive has no matching warning-intensity labels.']
    if source_stats['failed_months']:
        error_detail = '; '.join(source_stats['errors'][:3])
        notes.append(f"OGIMET supplied {source_stats['ogimet_valid']} valid records; {source_stats['failed_months']}/{source_stats['requested_months']} monthly requests failed. {error_detail}")
    for hazard, values in metrics.items():
        if values['brier_skill'] <= 0:
            notes.append(f'{hazard} warning generation is suppressed because chronological test Brier skill is not positive.')
    for hazard in targets.keys() - models.keys():
        notes.append(f'{hazard} was evaluated as unsupported because chronological train/calibration/test event counts were insufficient.')
    wind_y = np.array([[row[5][0], row[5][1],
                        np.sin(np.deg2rad(row[5][2])) if row[5][2] is not None else 0.0,
                        np.cos(np.deg2rad(row[5][2])) if row[5][2] is not None else 0.0]
                       for row in samples])
    split = int(len(samples) * 0.85)
    # Test records are never fitted into the serving wind model.
    wind_model = tuple(np.array(part) for part in saved['wind_model']) if saved and saved.get('wind_model') else _fit_regressor(x[:split], wind_y[:split])
    wind_test_prediction = _regress(wind_model, x[split:])
    wind_mae = np.mean(np.abs(wind_test_prediction[:, :2] - wind_y[split:, :2]), axis=0)
    wind_metrics = {'test_samples': len(wind_y) - split, 'speed_mae_kt': round(float(wind_mae[0]), 2), 'gust_mae_kt': round(float(wind_mae[1]), 2)}
    test_directions = [row[5][2] for row in samples[split:]]
    direction_mask = np.array([value is not None for value in test_directions])
    if direction_mask.any():
        predicted_degrees = np.degrees(np.arctan2(wind_test_prediction[:, 2], wind_test_prediction[:, 3])) % 360
        differences = np.abs(predicted_degrees[direction_mask] - np.array(test_directions, dtype=object)[direction_mask].astype(float))
        differences = np.minimum(differences, 360 - differences)
        wind_metrics['direction_test_samples'] = int(direction_mask.sum())
        wind_metrics['direction_mae_degrees'] = round(float(np.mean(differences)), 1)
    gust_train_mask = targets['GUST25KT'][:split] == 1
    gust_test_mask = targets['GUST25KT'][split:] == 1
    conditional_wind_model = tuple(np.array(part) for part in saved['conditional_wind_model']) if saved and saved.get('conditional_wind_model') else None
    if not saved and gust_train_mask.sum() >= 20 and gust_test_mask.sum() >= 5:
        conditional_wind_model = _fit_regressor(x[:split][gust_train_mask], wind_y[:split][gust_train_mask])
        conditional_test = _regress(conditional_wind_model, x[split:][gust_test_mask])
        conditional_mae = np.mean(np.abs(conditional_test[:, :2] - wind_y[split:][gust_test_mask, :2]), axis=0)
        wind_metrics['gust_event_test_samples'] = int(gust_test_mask.sum())
        wind_metrics['gust_event_speed_mae_kt'] = round(float(conditional_mae[0]), 2)
        wind_metrics['gust_event_max_mae_kt'] = round(float(conditional_mae[1]), 2)
    if refresh:
        pack = lambda model: [part.tolist() for part in model] if model else None
        setup = {'history_start': history_start, 'history_end': history_end,
                 'records': [[when.isoformat(), report] for when, report in rows],
                 'data_stats': source_stats,
                 'models': {name: {'model': pack(item[0]), 'calibration': pack(item[1]), 'threshold': item[2], 'metrics': item[3], 'meaningful': item[4]} for name, item in models.items()},
                 'wind_model': pack(wind_model), 'conditional_wind_model': pack(conditional_wind_model)}
        path = Path(AD_WARN_DIR) / 'adwrn_ml_setup.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(setup), encoding='utf-8')
        temporary.replace(path)
        return {'status': 'ok', 'warnings': [], 'metrics': {'hazards': metrics, 'wind_regression': wind_metrics}, 'notes': notes, 'data_stats': source_stats, 'message': 'Historical dataset and model saved.'}
    feature, _ = _features(observation_time, observation_report)
    xnow = feature.reshape(1, -1)
    compass = ('N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW')
    valid_start = max(observation_time + timedelta(minutes=25), issue_dt)
    valid_end = observation_time + timedelta(minutes=40)
    if (valid_end - issue_dt) < timedelta(minutes=10):
        return {'status': 'stale_observation', 'message': 'The observation forecast window has less than 10 minutes remaining at the requested issue time; no warning was generated.', 'warnings': [], 'metrics': metrics, 'data_stats': source_stats}
    sequence = 1
    warnings = []
    for hazard, (model, calibration, threshold, _, meaningful) in models.items():
        raw = _prob(model, xnow)
        logit = np.log(np.clip(raw, 1e-5, 1 - 1e-5) / np.clip(1 - raw, 1e-5, 1))
        probability = float(_prob(calibration, logit[:, None])[0])
        if not meaningful or probability < threshold:
            continue
        selected_wind_model = conditional_wind_model if hazard == 'GUST25KT' and conditional_wind_model else wind_model
        predicted_wind = _regress(selected_wind_model, xnow)[0]
        speed = max(0, int(round(predicted_wind[0])))
        gust = max(speed, int(round(predicted_wind[1])))
        direction_magnitude = float(np.hypot(predicted_wind[2], predicted_wind[3]))
        if direction_magnitude < 0.15:
            direction = 'UNAVAILABLE'
        else:
            direction_degrees = np.degrees(np.arctan2(predicted_wind[2], predicted_wind[3])) % 360
            direction = compass[int((direction_degrees + 11.25) / 22.5) % 16]
        gust_field = f'{gust}' if gust > speed else ' UNAVAILABLE'
        hazard_text = 'TSRA' if hazard == 'TSRA' else 'STRONG WIND/GUST'
        confidence = f'\nAI FCST CONFIDENCE {round(probability * 100):02d}%'
        warnings.append(
            f'{station} {issue_dt:%d%H%M}Z AD WRNG AI-{sequence:02d} VALID {valid_start:%d%H%M}/{valid_end:%d%H%M}\n'
            f'UNAVAILABLE {hazard_text} WITH SFC WSPD {speed}KT MAX{gust_field} FROM {direction}{confidence}'
        )
        sequence += 1
    return {
        'status': 'ok', 'station': station,
        'observation_time': observation_time.strftime('%Y%m%d%H%M'), 'validity': f'{valid_start:%d%H%M}/{valid_end:%d%H%M}',
        'warnings': warnings,
        'message': 'No evaluated hazard exceeded its validated alert threshold.' if not warnings else '',
        'metrics': {'hazards': metrics, 'wind_regression': wind_metrics},
        'notes': notes, 'data_stats': source_stats,
    }
