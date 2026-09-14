"""
Cloud Function Entry Point

HTTP-triggered function that reads route config from Google Cloud Storage,
queries current travel times via the Directions API, and logs results to CSV.
"""

import json
import os

from datetime import datetime

import functions_framework
from google.cloud import storage

from tracker import get_timezone, is_within_active_hours, track_all_routes


@functions_framework.http
def track_routes(request):
    """
    HTTP Cloud Function entry point.

    Triggered by Cloud Scheduler every N minutes. Reads route configuration
    from a JSON file in Cloud Storage, checks active hours, queries real-time
    travel times for each configured route, and appends results to CSV in GCS.

    Required environment variables:
        GOOGLE_MAPS_API_KEY  — Directions API key.
        GCS_BUCKET           — Cloud Storage bucket name.
        CONFIG_FILENAME      — Config file name in the bucket (default: config.json).
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return ("GOOGLE_MAPS_API_KEY environment variable not set", 500)

    bucket_name = os.environ.get("GCS_BUCKET")
    config_filename = os.environ.get("CONFIG_FILENAME", "config.json")

    if not bucket_name:
        return ("GCS_BUCKET environment variable not set", 500)

    # Read config from Cloud Storage
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(config_filename)
        config_text = blob.download_as_text(encoding="utf-8")
        config = json.loads(config_text)
    except Exception as e:
        return (f"Failed to read config from gs://{bucket_name}/{config_filename}: {e}", 500)

    # Ensure the bucket is set in the config for GCS logging
    config["gcs_bucket"] = bucket_name

    # Check active hours before querying Maps API
    sched = config.get("schedule", {})
    active_hours = sched.get("active_hours")
    tz_spec = sched.get("timezone", "Asia/Kolkata")
    tz = get_timezone(tz_spec)

    if active_hours and not is_within_active_hours(active_hours, tz=tz):
        now_str = datetime.now(tz).strftime("%H:%M")
        msg = f"Outside active hours ({active_hours} {tz_spec}). Current time: {now_str}. Skipped."
        print(f"  [SKIPPED] {msg}")
        return (msg, 200)

    # Track all routes and log to CSV
    results = track_all_routes(config, api_key)

    # Build a human-readable response
    lines = []
    for r in results:
        if r["status"] == "ok":
            lines.append(f"✓ {r['route_id']}: {r['data']['duration_in_traffic_text']}")
        else:
            lines.append(f"✗ {r['route_id']}: {r['error']}")

    return ("\n".join(lines), 200)
