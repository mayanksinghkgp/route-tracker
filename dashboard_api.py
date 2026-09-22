"""
Dashboard Data API

Provides functions to read route tracking data from Google Cloud Storage
and return it as JSON-serializable structures for the mobile dashboard.
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone


def get_csv_data(
    bucket_name: str,
    csv_filename: str = "travel_times.csv",
    route_id: str | None = None,
    days: int | None = None,
) -> list[dict]:
    """
    Downloads the tracking CSV from GCS and returns parsed rows as dicts.

    Args:
        bucket_name: GCS bucket name.
        csv_filename: CSV file name in the bucket.
        route_id: If provided, only return rows matching this route ID.
        days: If provided, only return rows from the last N days.

    Returns:
        List of dicts with original CSV fields plus computed fields:
        travel_minutes, hour_decimal, date, weekday.
    """
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(csv_filename)

    if not blob.exists():
        return []

    content = blob.download_as_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(content))

    # Compute cutoff if days filter is active
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows: list[dict] = []
    for row in reader:
        # Filter by route_id
        if route_id and row.get("route_id") != route_id:
            continue

        # Parse timestamp
        ts_raw = row.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue

        # Filter by date cutoff
        if cutoff is not None:
            # Normalize to UTC for comparison
            ts_utc = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if ts_utc < cutoff:
                continue

        # Compute travel time in minutes (prefer traffic duration)
        traffic_secs = row.get("duration_in_traffic_seconds", "")
        baseline_secs = row.get("duration_seconds", "")
        if traffic_secs and traffic_secs not in ("", "None"):
            travel_minutes = round(float(traffic_secs) / 60.0, 1)
        elif baseline_secs and baseline_secs not in ("", "None"):
            travel_minutes = round(float(baseline_secs) / 60.0, 1)
        else:
            travel_minutes = None

        # Compute baseline minutes for comparison
        baseline_minutes = None
        if baseline_secs and baseline_secs not in ("", "None"):
            baseline_minutes = round(float(baseline_secs) / 60.0, 1)

        hour_decimal = round(ts.hour + ts.minute / 60.0, 2)
        date_str = ts.strftime("%Y-%m-%d")
        weekday = ts.strftime("%A")  # Monday, Tuesday, ...
        weekday_num = ts.isoweekday()  # 1=Mon ... 7=Sun

        rows.append({
            "timestamp": ts_raw,
            "route_id": row.get("route_id", ""),
            "route_name": row.get("route_name", ""),
            "origin": row.get("origin", ""),
            "destination": row.get("destination", ""),
            "distance_km": _safe_float(row.get("distance_km", "")),
            "duration_seconds": _safe_float(baseline_secs),
            "duration_in_traffic_seconds": _safe_float(traffic_secs),
            "duration_text": row.get("duration_text", ""),
            "duration_in_traffic_text": row.get("duration_in_traffic_text", ""),
            "route_summary_returned": row.get("route_summary_returned", ""),
            # Computed fields
            "travel_minutes": travel_minutes,
            "baseline_minutes": baseline_minutes,
            "hour_decimal": hour_decimal,
            "date": date_str,
            "weekday": weekday,
            "weekday_num": weekday_num,
        })

    return rows


def get_route_list(bucket_name: str, config_filename: str = "config.json") -> list[dict]:
    """
    Reads the route configuration from GCS and returns metadata for each route.

    Args:
        bucket_name: GCS bucket name.
        config_filename: Config file name in the bucket.

    Returns:
        List of dicts with keys: id, name, origin, destination, active_hours.
    """
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(config_filename)

    if not blob.exists():
        return []

    content = blob.download_as_text(encoding="utf-8")
    config = json.loads(content)

    routes = []
    for route_cfg in config.get("routes", []):
        sched = route_cfg.get("schedule", {})
        routes.append({
            "id": route_cfg.get("id", ""),
            "name": route_cfg.get("name", ""),
            "origin": route_cfg.get("origin", ""),
            "destination": route_cfg.get("destination", ""),
            "active_hours": sched.get("active_hours", ""),
        })

    return routes


def _safe_float(value: str) -> float | None:
    """Safely convert a string to float, returning None on failure."""
    if not value or value in ("", "None"):
        return None
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None
