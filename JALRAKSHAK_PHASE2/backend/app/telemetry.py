"""Field data: SCADA / sensor readings, meter-data import, and alerts from field signals.

Devices (a reservoir level sensor, a zone bulk-flow meter) are registered by an administrator, which
returns a secret device key ONCE; only its SHA-256 hash is stored. A device (or a SCADA gateway acting for
it) sends readings to POST /telemetry/readings with the header X-Device-Key.

  * reservoir_level: the value is litres in the reservoir. With LEVEL_FROM_TELEMETRY=1 the newest reading
    becomes the reservoir's current water level (inside a transaction that locks the reservoir row, the
    same lock allocation plans use, so a plan never works with a half-updated level).
  * zone_flow: the value is litres delivered to the zone since the previous reading. Readings are summed per
    day into `consumption` with source 'meter' (real data, which the ML job prefers over synthetic data).

A device that has not reported for TELEMETRY_STALE_MINUTES is "offline": a live alert tells staff to enter
the value by hand (the Settings tab keeps working without any sensor).
"""
import csv
import hashlib
import io
import secrets
from datetime import date, datetime, timedelta, timezone

from . import audit, config
from .errors import ConflictError, InvalidInput, NotFoundError, NotSignedIn

KINDS = ("reservoir_level", "zone_flow")
DEVICE_COLUMNS = ("d.device_id, d.name, d.kind, d.reservoir_id, d.zone_id, d.active, d.last_seen_at, d.last_value, "
                  "d.created_at, r.name AS reservoir_name, z.name AS zone_name")


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def list_devices(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {DEVICE_COLUMNS}, (d.active AND (d.last_seen_at IS NULL OR d.last_seen_at < now() - "
                    "(%s * interval '1 minute'))) AS stale FROM devices d "
                    "LEFT JOIN reservoirs r ON r.reservoir_id = d.reservoir_id LEFT JOIN zones z ON z.zone_id = d.zone_id "
                    "ORDER BY d.device_id", (config.TELEMETRY_STALE_MINUTES,))
        return cur.fetchall()


def create_device(conn, actor, name, kind, reservoir_id=None, zone_id=None):
    name = (name or "").strip()
    if not name:
        raise InvalidInput("the device needs a name")
    if kind not in KINDS:
        raise InvalidInput(f"kind must be one of {', '.join(KINDS)}")
    if kind == "reservoir_level" and reservoir_id is None:
        raise InvalidInput("a level sensor belongs to a reservoir")
    if kind == "zone_flow" and zone_id is None:
        raise InvalidInput("a flow meter belongs to a zone")
    key = "jdk_" + secrets.token_urlsafe(32)
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM devices WHERE name = %s", (name,))
            if cur.fetchone():
                raise ConflictError("a device with this name already exists")
            cur.execute("INSERT INTO devices (name, kind, reservoir_id, zone_id, key_hash) VALUES (%s, %s, %s, %s, %s) "
                        "RETURNING device_id",
                        (name, kind, reservoir_id if kind == "reservoir_level" else None,
                         zone_id if kind == "zone_flow" else None, _hash(key)))
            did = cur.fetchone()["device_id"]
            audit.write(cur, actor, "device.create", "device", did, {"name": name, "kind": kind})
            cur.execute(f"SELECT {DEVICE_COLUMNS} FROM devices d LEFT JOIN reservoirs r ON r.reservoir_id = d.reservoir_id "
                        "LEFT JOIN zones z ON z.zone_id = d.zone_id WHERE d.device_id = %s", (did,))
            row = cur.fetchone()
    return dict(row, device_key=key)  # shown once; only the hash is stored


def set_device_active(conn, actor, device_id, active):
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE devices SET active = %s WHERE device_id = %s RETURNING device_id", (bool(active), device_id))
            if cur.fetchone() is None:
                raise NotFoundError(f"device {device_id} not found")
            audit.write(cur, actor, "device.enable" if active else "device.disable", "device", device_id)
    return {"device_id": device_id, "active": bool(active)}


def _parse_time(value):
    if value is None:
        return datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise InvalidInput(f"measured_at must be an ISO 8601 time, not {value!r}")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if t > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise InvalidInput("measured_at is in the future")
    return t


def ingest(conn, device_key, readings):
    """Store readings from one device. readings: [{"measured_at": ISO time (optional), "value": number}].
    Duplicate (device, time) readings are ignored, so a gateway can safely resend."""
    if not device_key:
        raise NotSignedIn("send the device key in the X-Device-Key header")
    if not readings:
        raise InvalidInput("send at least one reading")
    if len(readings) > 1000:
        raise InvalidInput("send at most 1000 readings at a time")
    parsed = []
    for r in readings:
        try:
            value = float(r["value"])
        except (KeyError, TypeError, ValueError):
            raise InvalidInput("every reading needs a numeric value")
        if value < 0:
            raise InvalidInput("a reading cannot be negative")
        parsed.append((_parse_time(r.get("measured_at")), value))
    parsed.sort()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT device_id, kind, reservoir_id, zone_id, active, name FROM devices WHERE key_hash = %s "
                        "FOR UPDATE", (_hash(device_key),))
            dev = cur.fetchone()
            if dev is None or not dev["active"]:
                raise NotSignedIn("unknown or disabled device key")
            stored = 0
            for t, v in parsed:
                cur.execute("INSERT INTO readings (device_id, measured_at, value) VALUES (%s, %s, %s) "
                            "ON CONFLICT (device_id, measured_at) DO NOTHING RETURNING reading_id",
                            (dev["device_id"], t.isoformat(), v))
                stored += len(cur.fetchall())
            last_t, last_v = parsed[-1]
            cur.execute("UPDATE devices SET last_seen_at = now(), last_value = %s WHERE device_id = %s",
                        (last_v, dev["device_id"]))
            level = None
            if dev["kind"] == "reservoir_level" and config.LEVEL_FROM_TELEMETRY:
                cur.execute("SELECT capacity_litres FROM reservoirs WHERE reservoir_id = %s FOR UPDATE", (dev["reservoir_id"],))
                cap = float(cur.fetchone()["capacity_litres"])
                if last_v > cap:
                    raise InvalidInput(f"level {last_v:,.0f} L is above the reservoir capacity {cap:,.0f} L "
                                       "(check the sensor calibration)")
                cur.execute("UPDATE reservoirs SET available_litres = %s, updated_at = now() WHERE reservoir_id = %s",
                            (last_v, dev["reservoir_id"]))
                level = last_v
                audit.write(cur, {"account_id": None, "email": f"device:{dev['name']}"}, "telemetry.level",
                            "reservoir", dev["reservoir_id"], {"litres": last_v, "measured_at": last_t.isoformat()})
            if dev["kind"] == "zone_flow":
                days = sorted({t.date() for t, _ in parsed})
                for d in days:
                    _aggregate_day(cur, dev["zone_id"], d)
    return {"device_id": dev["device_id"], "stored": stored, "duplicates": len(parsed) - stored,
            "reservoir_level": level}


def _aggregate_day(cur, zone_id, day):
    """consumption(zone, day) = sum of all zone_flow readings of that zone on that day (source 'meter')."""
    cur.execute("SELECT COALESCE(SUM(r.value), 0) AS litres FROM readings r JOIN devices d ON d.device_id = r.device_id "
                "WHERE d.zone_id = %s AND d.kind = 'zone_flow' AND (r.measured_at AT TIME ZONE 'UTC')::date = %s",
                (zone_id, str(day)))
    litres = float(cur.fetchone()["litres"])
    cur.execute("INSERT INTO consumption (zone_id, recorded_on, litres, source) VALUES (%s, %s, %s, 'meter') "
                "ON CONFLICT (zone_id, recorded_on) DO UPDATE SET litres = EXCLUDED.litres, source = 'meter', "
                "is_injected_leak = FALSE", (zone_id, str(day), litres))


def recent_readings(conn, device_id, limit=200):
    with conn.cursor() as cur:
        cur.execute("SELECT measured_at, value, received_at FROM readings WHERE device_id = %s "
                    "ORDER BY measured_at DESC LIMIT %s", (device_id, limit))
        return cur.fetchall()


def import_consumption_csv(conn, actor, text):
    """Import daily metered consumption: CSV with columns zone_id (or zone name), date (YYYY-MM-DD), litres.
    Existing rows for the same zone and day are replaced. All-or-nothing: one bad line rejects the file."""
    if not text or not text.strip():
        raise InvalidInput("the file is empty")
    reader = csv.DictReader(io.StringIO(text.strip()))
    fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
    if not {"date", "litres"} <= fields or not ({"zone_id", "zone"} & fields):
        raise InvalidInput("the CSV needs the columns: zone_id (or zone), date, litres")
    rows = []
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT zone_id, lower(name) AS name FROM zones")
            zones = cur.fetchall()
            by_id = {z["zone_id"] for z in zones}
            by_name = {z["name"]: z["zone_id"] for z in zones}
            for n, raw in enumerate(reader, start=2):
                line = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
                try:
                    if line.get("zone_id"):
                        zid = int(line["zone_id"])
                    else:
                        zid = by_name[line.get("zone", "").lower()]
                    if zid not in by_id:
                        raise KeyError
                    day = date.fromisoformat(line["date"])
                    litres = float(line["litres"])
                except (KeyError, ValueError):
                    raise InvalidInput(f"line {n}: unknown zone, bad date (use YYYY-MM-DD) or bad litres")
                if litres < 0:
                    raise InvalidInput(f"line {n}: litres cannot be negative")
                if day > date.today():
                    raise InvalidInput(f"line {n}: the date is in the future")
                rows.append((zid, day, litres))
            if not rows:
                raise InvalidInput("the file has no data lines")
            for zid, day, litres in rows:
                cur.execute("INSERT INTO consumption (zone_id, recorded_on, litres, source) VALUES (%s, %s, %s, 'import') "
                            "ON CONFLICT (zone_id, recorded_on) DO UPDATE SET litres = EXCLUDED.litres, "
                            "source = 'import', is_injected_leak = FALSE", (zid, str(day), litres))
            audit.write(cur, actor, "data.import", "consumption", None,
                        {"rows": len(rows), "zones": len({r[0] for r in rows}),
                         "from": str(min(r[1] for r in rows)), "to": str(max(r[1] for r in rows))})
    return {"rows": len(rows)}


def data_summary(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT source, COUNT(*) AS days, MIN(recorded_on) AS first, MAX(recorded_on) AS last "
                    "FROM consumption GROUP BY source ORDER BY source")
        return cur.fetchall()


# ---------------------------------------------------------------- live alerts from field signals
def field_alerts(cur):
    """Alerts computed from the current state (never stored): offline sensors and complaint clusters."""
    out = []
    cur.execute("SELECT device_id, name, last_seen_at FROM devices WHERE active AND "
                "(last_seen_at IS NULL OR last_seen_at < now() - (%s * interval '1 minute'))",
                (config.TELEMETRY_STALE_MINUTES,))
    for d in cur.fetchall():
        seen = "never reported" if d["last_seen_at"] is None else f"last reading at {d['last_seen_at']}"
        out.append({"alert_id": None, "zone_id": None, "alert_type": "telemetry", "severity": "warning",
                    "message": f"Sensor '{d['name']}' is offline ({seen}). Enter the value by hand in Settings "
                               "until it reports again.", "resolved": False, "created_at": d["last_seen_at"], "live": True})
    cur.execute("SELECT i.zone_id, z.name, COUNT(*) AS n FROM issues i JOIN zones z ON z.zone_id = i.zone_id "
                "WHERE i.category IN ('leak', 'no_supply', 'low_pressure') AND i.status IN ('open', 'in_progress') "
                "AND i.created_at >= now() - (%s * interval '1 hour') GROUP BY i.zone_id, z.name HAVING COUNT(*) >= %s",
                (config.COMPLAINT_CLUSTER_HOURS, config.COMPLAINT_CLUSTER_SIZE))
    for c in cur.fetchall():
        out.append({"alert_id": None, "zone_id": c["zone_id"], "alert_type": "complaints", "severity": "warning",
                    "message": f"{c['n']} leak / supply complaints from {c['name']} in the last "
                               f"{config.COMPLAINT_CLUSTER_HOURS:.0f} hours. Possible main break or leak: send a team.",
                    "resolved": False, "created_at": None, "live": True})
    return out
