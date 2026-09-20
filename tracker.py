"""
Route Travel Time Tracker - Core Logic

Periodically queries the Google Maps Directions API for real-time travel times
on a specific pinned route and logs results to CSV.
"""

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "timestamp",
    "route_id",
    "route_name",
    "origin",
    "destination",
    "distance_km",
    "duration_seconds",
    "duration_in_traffic_seconds",
    "duration_text",
    "duration_in_traffic_text",
    "route_summary_returned",
]

DIRECTIONS_API_URL = "https://maps.googleapis.com/maps/api/directions/json"


# ---------------------------------------------------------------------------
# Polyline Utilities
# ---------------------------------------------------------------------------


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """
    Decodes a Google Maps encoded polyline string into a list of (lat, lng) tuples.

    Pure Python implementation of Google's Encoded Polyline Algorithm.
    Reference: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
    """
    index, lat, lng = 0, 0, 0
    coordinates: list[tuple[float, float]] = []
    length = len(encoded)

    while index < length:
        # Decode latitude
        shift, result = 0, 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += ~(result >> 1) if (result & 1) else (result >> 1)

        # Decode longitude
        shift, result = 0, 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lng += ~(result >> 1) if (result & 1) else (result >> 1)

        coordinates.append((lat / 1e5, lng / 1e5))

    return coordinates


def sample_pin_points(encoded_polyline: str, count: int = 3) -> list[list[float]]:
    """
    Decodes a polyline and samples ``count`` evenly-spaced intermediate
    coordinates.  These are used as ``via:`` waypoints to pin a specific
    route corridor when re-querying for travel time.

    Args:
        encoded_polyline: Google Maps encoded polyline string.
        count: Number of intermediate points to sample (default 3).

    Returns:
        List of [lat, lng] pairs.
    """
    coords = decode_polyline(encoded_polyline)
    n = len(coords)

    if n < count + 2:
        # Not enough points to sample; use all intermediate ones
        return [[lat, lng] for lat, lng in coords[1:-1]]

    # Sample at evenly spaced positions (excluding start and end)
    pin_points: list[list[float]] = []
    for i in range(1, count + 1):
        idx = int(n * i / (count + 1))
        lat, lng = coords[idx]
        pin_points.append([lat, lng])

    return pin_points


# ---------------------------------------------------------------------------
# Google Maps Directions API
# ---------------------------------------------------------------------------


def fetch_travel_time(
    origin: str,
    destination: str,
    pin_points: list[list[float]],
    api_key: str,
) -> dict:
    """
    Calls the Google Maps Directions API with ``via:`` waypoints to get
    the traffic-aware travel time for a specific pinned route.

    Args:
        origin: Origin address or ``lat,lng``.
        destination: Destination address or ``lat,lng``.
        pin_points: List of [lat, lng] intermediate waypoints.
        api_key: Google Maps API key.

    Returns:
        Dictionary with travel time data.

    Raises:
        RuntimeError: If the API returns an error status.
    """
    # Format via: waypoints — these force the route corridor without
    # creating extra legs or stops.
    via_str = "|".join(f"via:{pt[0]:.6f},{pt[1]:.6f}" for pt in pin_points)

    params = {
        "origin": origin,
        "destination": destination,
        "waypoints": via_str,
        "departure_time": "now",
        "traffic_model": "best_guess",
        "mode": "driving",
        "key": api_key,
    }

    response = requests.get(DIRECTIONS_API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        raise RuntimeError(
            f"Directions API error: {data.get('status')} — "
            f"{data.get('error_message', 'No details')}"
        )

    route = data["routes"][0]
    leg = route["legs"][0]
    distance_km = round(leg["distance"]["value"] / 1000, 2)

    return {
        "route_summary_returned": route.get("summary", ""),
        "distance_km": distance_km,
        "duration_seconds": leg["duration"]["value"],
        "duration_text": leg["duration"]["text"],
        "duration_in_traffic_seconds": leg.get("duration_in_traffic", {}).get("value"),
        "duration_in_traffic_text": leg.get("duration_in_traffic", {}).get("text", "N/A"),
    }


def fetch_alternative_routes(origin: str, destination: str, api_key: str) -> list[dict]:
    """
    Fetches alternative routes between *origin* and *destination*.

    Returns:
        List of route dictionaries from the Directions API response.
    """
    params = {
        "origin": origin,
        "destination": destination,
        "alternatives": "true",
        "departure_time": "now",
        "mode": "driving",
        "key": api_key,
    }

    response = requests.get(DIRECTIONS_API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        raise RuntimeError(
            f"Directions API error: {data.get('status')} — "
            f"{data.get('error_message', 'No details')}"
        )

    return data.get("routes", [])


# ---------------------------------------------------------------------------
# CSV Logging
# ---------------------------------------------------------------------------


def get_timezone(tz_spec: str | None = None) -> timezone:
    """
    Returns a timezone object.
    Supports names (e.g. 'Asia/Kolkata', 'UTC'), offsets (e.g. '+05:30'),
    and falls back gracefully to IST (+05:30) or UTC.
    """
    if not tz_spec:
        tz_spec = os.environ.get("TRACKER_TIMEZONE", "Asia/Kolkata")

    if tz_spec.upper() == "UTC":
        return timezone.utc

    if tz_spec.startswith(("+", "-")) and ":" in tz_spec:
        try:
            sign = 1 if tz_spec[0] == "+" else -1
            parts = tz_spec[1:].split(":")
            h, m = int(parts[0]), int(parts[1])
            return timezone(sign * timedelta(hours=h, minutes=m))
        except Exception:
            pass

    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_spec)
    except Exception:
        pass

    lower = tz_spec.lower()
    if any(k in lower for k in ("kolkata", "ist", "india", "calcutta")):
        return timezone(timedelta(hours=5, minutes=30))

    return timezone.utc


def build_csv_row(route_config: dict, travel_data: dict, tz: timezone | None = None) -> dict:
    """Builds a CSV row dictionary from route config and API response data."""
    if tz is None:
        tz = get_timezone()
    return {
        "timestamp": datetime.now(tz).isoformat(),
        "route_id": route_config["id"],
        "route_name": route_config["name"],
        "origin": route_config["origin"],
        "destination": route_config["destination"],
        "distance_km": travel_data["distance_km"],
        "duration_seconds": travel_data["duration_seconds"],
        "duration_in_traffic_seconds": travel_data.get("duration_in_traffic_seconds", ""),
        "duration_text": travel_data["duration_text"],
        "duration_in_traffic_text": travel_data["duration_in_traffic_text"],
        "route_summary_returned": travel_data["route_summary_returned"],
    }


def append_row_local(csv_path: str, row: dict) -> None:
    """Appends a row to a local CSV file, creating it with headers if needed."""
    file_exists = os.path.exists(csv_path)

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def append_row_gcs(bucket_name: str, csv_filename: str, row: dict) -> None:
    """
    Appends a row to a CSV file stored in Google Cloud Storage.

    Downloads the existing CSV (if any), appends the new row, and re-uploads.
    """
    from google.cloud import storage  # imported here so local mode works without it

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(csv_filename)

    # Download existing content or start fresh
    existing_content = ""
    if blob.exists():
        existing_content = blob.download_as_text(encoding="utf-8")

    # Write the new row
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS)
    if not existing_content:
        writer.writeheader()
    writer.writerow(row)
    new_line = output.getvalue()

    # Append and upload
    updated_content = existing_content + new_line
    blob.upload_from_string(updated_content, content_type="text/csv")


# ---------------------------------------------------------------------------
# Main Tracker
# ---------------------------------------------------------------------------


def get_effective_route_schedule(route_cfg: dict, global_config: dict) -> dict:
    """
    Resolves the effective schedule for a route by combining route-level
    settings with top-level global schedule defaults.
    """
    global_sched = global_config.get("schedule", {})
    route_sched = route_cfg.get("schedule", {})

    effective = dict(global_sched)
    effective.update(route_sched)
    return effective


def track_all_routes(
    config: dict,
    api_key: str,
    local_csv_path: str | None = None,
    check_active_hours: bool = True,
    active_hours_override: str | None = None,
) -> list[dict]:
    """
    Tracks travel time for routes defined in *config*.

    Evaluates active hours per-route (or via active_hours_override).
    Routes outside their active window are skipped without calling Google Maps API.

    Args:
        config: Configuration dictionary containing ``routes`` and storage
            settings (``gcs_bucket``, ``csv_filename``).
        api_key: Google Maps API key.
        local_csv_path: If provided, logs to a local CSV file instead of GCS.
        check_active_hours: If True, evaluates active hours before querying.
        active_hours_override: If provided, overrides route-specific active hours.

    Returns:
        List of result dictionaries (one per route), each containing
        ``route_id``, ``name``, ``status`` (``"ok"``, ``"skipped"``, or ``"error"``),
        and either ``data``, ``reason``, or ``error`` details.
    """
    results: list[dict] = []

    for route_cfg in config["routes"]:
        sched = get_effective_route_schedule(route_cfg, config)
        tz = get_timezone(sched.get("timezone"))
        active_hours = active_hours_override if active_hours_override is not None else sched.get("active_hours")

        if check_active_hours and active_hours and not is_within_active_hours(active_hours, tz=tz):
            now_str = datetime.now(tz).strftime("%H:%M")
            tz_name = sched.get("timezone", "local")
            reason = f"Outside active hours ({active_hours} {tz_name}, now {now_str})"
            results.append({
                "route_id": route_cfg["id"],
                "name": route_cfg["name"],
                "status": "skipped",
                "reason": reason,
                "active_hours": active_hours,
            })
            print(f"  [SKIPPED] {route_cfg['name']}: {reason}")
            continue

        try:
            travel_data = fetch_travel_time(
                origin=route_cfg["origin"],
                destination=route_cfg["destination"],
                pin_points=route_cfg["pin_points"],
                api_key=api_key,
            )

            row = build_csv_row(route_cfg, travel_data, tz=tz)

            if local_csv_path:
                append_row_local(local_csv_path, row)
            else:
                append_row_gcs(
                    config["gcs_bucket"],
                    config.get("csv_filename", "travel_times.csv"),
                    row,
                )

            results.append({
                "route_id": route_cfg["id"],
                "name": route_cfg["name"],
                "status": "ok",
                "data": travel_data,
            })
            print(f"  [OK] {route_cfg['name']}: {travel_data['duration_in_traffic_text']}")

        except Exception as e:
            results.append({
                "route_id": route_cfg["id"],
                "name": route_cfg["name"],
                "status": "error",
                "error": str(e),
            })
            print(f"  [FAIL] {route_cfg['name']}: {e}")

    return results


# ---------------------------------------------------------------------------
# Scheduling Helpers
# ---------------------------------------------------------------------------


def next_boundary(boundary_minutes: int = 15) -> datetime:
    """
    Returns the next clock-aligned boundary.

    E.g. with *boundary_minutes=15*, if the current time is 11:49 the
    function returns a datetime for 12:00.
    """
    now = datetime.now()
    minutes_past = now.minute % boundary_minutes
    if minutes_past == 0 and now.second == 0:
        return now.replace(second=0, microsecond=0)
    wait = boundary_minutes - minutes_past
    target = (now + timedelta(minutes=wait)).replace(second=0, microsecond=0)
    return target


def parse_active_hours(spec: str) -> tuple[float, float]:
    """
    Parses an active-hours specification like ``"7:00-20:00"`` and returns
    (start_hour, end_hour) as floats (e.g. 7.0, 20.0).

    Raises ValueError on invalid format.
    """
    parts = spec.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid active-hours format: '{spec}'. Expected 'HH:MM-HH:MM'.")

    def _parse(t: str) -> float:
        t = t.strip()
        pieces = t.split(":")
        h = int(pieces[0])
        m = int(pieces[1]) if len(pieces) > 1 else 0
        return h + m / 60.0

    return _parse(parts[0]), _parse(parts[1])


def is_within_active_hours(active_hours: str | None, tz: timezone | None = None) -> bool:
    """Returns True if the current time in the specified timezone is within the active window."""
    if not active_hours:
        return True
    start_h, end_h = parse_active_hours(active_hours)
    now = datetime.now(tz) if tz else datetime.now()
    current_h = now.hour + now.minute / 60.0
    return start_h <= current_h < end_h


def seconds_until_active(active_hours: str, tz: timezone | None = None) -> float:
    """
    Returns the number of seconds until the next active-hours window begins.
    If already within the window returns 0.
    """
    start_h, end_h = parse_active_hours(active_hours)
    now = datetime.now(tz) if tz else datetime.now()
    current_h = now.hour + now.minute / 60.0

    if start_h <= current_h < end_h:
        return 0.0

    # Calculate seconds until start_h today or tomorrow
    start_today = now.replace(
        hour=int(start_h), minute=int((start_h % 1) * 60), second=0, microsecond=0
    )
    if start_today > now:
        return (start_today - now).total_seconds()
    # Next day
    start_tomorrow = start_today + timedelta(days=1)
    return (start_tomorrow - now).total_seconds()


def next_active_seconds_for_routes(
    config: dict,
    active_hours_override: str | None = None,
) -> float:
    """
    Calculates the number of seconds until the earliest route active window begins.
    Returns 0.0 if any route is currently active, or if any route has no active_hours.
    """
    waits = []
    for route_cfg in config.get("routes", []):
        sched = get_effective_route_schedule(route_cfg, config)
        ah = active_hours_override if active_hours_override is not None else sched.get("active_hours")
        if not ah:
            return 0.0  # Runs all day
        tz = get_timezone(sched.get("timezone"))
        if is_within_active_hours(ah, tz=tz):
            return 0.0
        waits.append(seconds_until_active(ah, tz=tz))

    return min(waits) if waits else 0.0


def run_daemon(
    config: dict,
    api_key: str,
    csv_path: str,
    interval_minutes: int,
    start_now: bool,
    max_sessions: int | None,
    max_days: int | None,
    active_hours: str | None,
) -> None:
    """
    Runs the tracker in a continuous loop.

    Args:
        config: Route configuration.
        api_key: Google Maps API key.
        csv_path: Path to the local CSV file.
        interval_minutes: Minutes between each poll.
        start_now: If False, waits until the next 15-minute clock boundary.
        max_sessions: Stop after this many polls (None = unlimited).
        max_days: Stop after this many calendar days (None = unlimited).
        active_hours: Only poll during this window, e.g. ``"7:00-20:00"``.
    """
    # ---- Wait for aligned start ----
    if not start_now:
        target = next_boundary(15)
        wait_secs = (target - datetime.now()).total_seconds()
        if wait_secs > 0:
            print(f"  Waiting until {target.strftime('%H:%M')} "
                  f"(next 15-min mark, {wait_secs:.0f}s)...")
            time.sleep(wait_secs)

    session_count = 0
    start_date = datetime.now()

    print(f"\n  Daemon started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Interval:       every {interval_minutes} min")
    print(f"  Max sessions:   {max_sessions or 'unlimited'}")
    print(f"  Max days:       {max_days or 'unlimited'}")
    print(f"  Active hours:   {active_hours or 'per route / config'}")
    print(f"  CSV file:       {csv_path}")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        while True:
            # ---- Check day limit ----
            if max_days is not None:
                elapsed_days = (datetime.now() - start_date).total_seconds() / 86400
                if elapsed_days >= max_days:
                    print(f"\n  Reached {max_days}-day limit. Stopping.")
                    break

            # ---- Check session limit ----
            if max_sessions is not None and session_count >= max_sessions:
                print(f"\n  Reached {max_sessions}-session limit. Stopping.")
                break

            # ---- Check active hours ----
            if active_hours:
                default_tz = get_timezone(config.get("schedule", {}).get("timezone"))
                if not is_within_active_hours(active_hours, tz=default_tz):
                    wait = seconds_until_active(active_hours, tz=default_tz)
                    resume = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
                    print(f"  [{datetime.now().strftime('%H:%M')}] Outside active hours ({active_hours}). "
                          f"Sleeping until {resume}...")
                    time.sleep(wait)
                    continue
            else:
                wait = next_active_seconds_for_routes(config)
                if wait > 0:
                    resume = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
                    print(f"  [{datetime.now().strftime('%H:%M')}] All routes currently outside active hours. "
                          f"Sleeping until {resume}...")
                    time.sleep(wait)
                    continue

            # ---- Poll ----
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{ts}] Polling ({session_count + 1}"
                  f"{f'/{max_sessions}' if max_sessions else ''})...")
            track_all_routes(
                config,
                api_key,
                local_csv_path=csv_path,
                check_active_hours=True,
                active_hours_override=active_hours,
            )
            session_count += 1

            # ---- Sleep until next interval ----
            if max_sessions is not None and session_count >= max_sessions:
                continue  # will break at top of loop
            print(f"  Next poll in {interval_minutes} min.\n")
            time.sleep(interval_minutes * 60)

    except KeyboardInterrupt:
        print(f"\n\n  Stopped by user after {session_count} session(s).")

    print(f"  Total sessions logged: {session_count}")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Route Travel Time Tracker -- log travel times to CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  Single shot (local):
    python tracker.py --local

  Single shot, one route only:
    python tracker.py --local --routes home-to-office

  Repeat using schedule from config.json:
    python tracker.py --local --repeat

  Override config schedule from CLI:
    python tracker.py --local --repeat --start-now --interval 15 --max-sessions 48

  Only track 7 AM - 8 PM, for 7 days:
    python tracker.py --local --repeat --active-hours 7:00-20:00 --max-days 7

  List configured routes:
    python tracker.py --list-routes
""",
    )

    parser.add_argument(
        "--local",
        action="store_true",
        help="Save to local CSV instead of Google Cloud Storage",
    )
    parser.add_argument(
        "--csv",
        default="travel_times.csv",
        help="Local CSV file path (default: travel_times.csv)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.json (default: config.json next to this script)",
    )
    parser.add_argument(
        "--routes",
        type=str,
        default=None,
        metavar="ID[,ID,...]",
        help="Comma-separated route IDs to track (default: all routes)",
    )
    parser.add_argument(
        "--list-routes",
        action="store_true",
        help="List all configured routes and exit",
    )

    # ---- Repeat / daemon mode ----
    # Defaults are None so we can fall back to config.json values.
    parser.add_argument(
        "--repeat",
        action="store_true",
        help="Run continuously, polling at a fixed interval",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Minutes between polls (default: from config.json, or 30)",
    )
    parser.add_argument(
        "--start-now",
        action="store_true",
        default=None,
        help="Start polling immediately instead of waiting for the next 15-min boundary",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        help="Stop after N polling sessions (default: from config.json, or unlimited)",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=None,
        help="Stop after N calendar days (default: from config.json, or unlimited)",
    )
    parser.add_argument(
        "--active-hours",
        type=str,
        default=None,
        metavar="HH:MM-HH:MM",
        help="Override all routes to poll only during this daily window",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Track all routes immediately, ignoring active-hours restrictions",
    )

    args = parser.parse_args()

    # ---- Config file ----
    config_path = args.config
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        print("Run 'python setup_route.py' first to create it.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if not config.get("routes"):
        print("No routes configured. Run 'python setup_route.py' first.")
        sys.exit(1)

    # ---- List routes ----
    if args.list_routes:
        print(f"Configured routes ({len(config['routes'])}):")
        for r in config["routes"]:
            r_sched = get_effective_route_schedule(r, config)
            r_ah = r_sched.get("active_hours")
            schedule_info = f" [active: {r_ah}]" if r_ah else " [active: all day]"
            print(f"  {r['id']:20s}  {r['name']}{schedule_info}")
        sched = config.get("schedule", {})
        if sched:
            ah = sched.get('active_hours', '')
            tz = sched.get('timezone', 'Asia/Kolkata')
            md = sched.get('max_days')
            ms = sched.get('max_sessions')
            print(f"\nDefault schedule: every {sched.get('interval_minutes', 30)} min ({tz})"
                  f"{f', default active: {ah}' if ah else ''}"
                  f"{f', max {md}d' if md else ''}"
                  f"{f', max {ms} sessions' if ms else ''}")
        sys.exit(0)

    # ---- Filter routes ----
    if args.routes:
        requested_ids = [rid.strip() for rid in args.routes.split(",")]
        all_ids = {r["id"] for r in config["routes"]}
        unknown = set(requested_ids) - all_ids
        if unknown:
            print(f"Error: Unknown route ID(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(all_ids)}")
            sys.exit(1)
        config["routes"] = [r for r in config["routes"] if r["id"] in requested_ids]
        print(f"Tracking {len(config['routes'])} of {len(all_ids)} route(s): "
              f"{', '.join(requested_ids)}")

    # ---- Merge schedule: CLI flags override config.json defaults ----
    sched = config.get("schedule", {})

    interval = args.interval if args.interval is not None else sched.get("interval_minutes", 30)
    start_now = args.start_now if args.start_now is not None else not sched.get("start_aligned", True)
    max_sessions = args.max_sessions if args.max_sessions is not None else sched.get("max_sessions")
    max_days = args.max_days if args.max_days is not None else sched.get("max_days")
    active_hours_override = args.active_hours

    # ---- Validate active-hours format ----
    if active_hours_override:
        try:
            parse_active_hours(active_hours_override)
        except ValueError as e:
            parser.error(str(e))

    # ---- API key ----
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("Error: Set the GOOGLE_MAPS_API_KEY environment variable.")
        sys.exit(1)

    # ---- Run ----
    local_path = args.csv if args.local else None

    if args.repeat:
        # Daemon / repeat mode
        if not args.local:
            print("Error: --repeat mode currently requires --local. "
                  "For cloud mode, use Cloud Scheduler.")
            sys.exit(1)
        run_daemon(
            config=config,
            api_key=api_key,
            csv_path=args.csv,
            interval_minutes=interval,
            start_now=start_now,
            max_sessions=max_sessions,
            max_days=max_days,
            active_hours=active_hours_override,
        )
    else:
        # Single shot
        print(f"Tracking {len(config['routes'])} route(s)...")
        results = track_all_routes(
            config,
            api_key,
            local_csv_path=local_path,
            check_active_hours=not args.force,
            active_hours_override=active_hours_override,
        )
        ok_count = sum(1 for r in results if r["status"] == "ok")
        skipped_count = sum(1 for r in results if r["status"] == "skipped")
        print(f"\nDone: {ok_count}/{len(results)} tracked, {skipped_count} skipped.")
