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

from tracker import track_all_routes


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

    # Track all routes (evaluating per-route active hours) and log to CSV
    results = track_all_routes(config, api_key, check_active_hours=True)

    # Build a human-readable response
    lines = []
    tracked_count = 0
    for r in results:
        if r["status"] == "ok":
            lines.append(f"✓ {r['route_id']}: {r['data']['duration_in_traffic_text']}")
            tracked_count += 1
        elif r["status"] == "skipped":
            lines.append(f"- {r['route_id']}: Skipped ({r.get('reason', 'outside active hours')})")
        else:
            lines.append(f"✗ {r['route_id']}: {r.get('error')}")

    status_header = f"[{tracked_count}/{len(results)} routes tracked]"
    return (f"{status_header}\n" + "\n".join(lines), 200)
