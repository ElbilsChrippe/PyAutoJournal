import requests
import csv
import json
import os
from requests.auth import HTTPBasicAuth

# --- LADDA KONFIGURATION ---
CONFIG_FILE = "config.json"

if not os.path.exists(CONFIG_FILE):
    print(f"Fel: Hittar inte {CONFIG_FILE}. Skapa den först!")
    exit(1)

with open(CONFIG_FILE, "r") as f:
    conf = json.load(f)

USER = conf["user"]
PASS = conf["pass"]
URL = conf["url"]
DEVICE_ID = conf["device_id"]

# Tidsperiod (Februari 2026)
START_DATE = "2026-02-01T00:00:00Z"
END_DATE = "2026-03-01T00:00:00Z"

auth = HTTPBasicAuth(USER, PASS)
headers = {"Accept": "application/json"}
params = {"deviceId": DEVICE_ID, "from": START_DATE, "to": END_DATE}

try:
    print(f"Loggar in som {USER}...")
    print("Hämtar rådata för bearbetning...")

    r = requests.get(URL, params=params, auth=auth, headers=headers, timeout=60)
    r.raise_for_status()
    points = r.json()

    trips = []
    current_trip = None

    for p in points:
        # Hämta tändningsstatus
        attrs = p.get("attributes", {})
        ignition = attrs.get("ignition", False)

        # Starta resa vid tändning PÅ
        if ignition and not current_trip:
            current_trip = {
                "start_time": p.get("deviceTime"),
                "start_addr": p.get("address", "Adress saknas"),
                "start_odo": attrs.get("totalDistance", 0),
            }

        # Avsluta resa vid tändning AV
        elif not ignition and current_trip:
            end_odo = attrs.get("totalDistance", 0)
            # Beräkna distans (hanterar meter -> km)
            dist = (end_odo - current_trip["start_odo"]) / 1000

            if dist > 0.1:  # Filtrera bort "tändning på/av" utan rörelse
                trips.append(
                    {
                        "Start": current_trip["start_time"],
                        "Slut": p.get("deviceTime"),
                        "Från": current_trip["start_addr"],
                        "Till": p.get("address", "Adress saknas"),
                        "Km": round(dist, 2),
                    }
                )
            current_trip = None

    # Spara till CSV
    filename = "korjournal_februari.csv"
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Start", "Slut", "Från", "Till", "Km"])
        writer.writeheader()
        writer.writerows(trips)

    print(f"KLART! {len(trips)} resor extraherade till {filename}")

except Exception as e:
    print(f"Ett fel uppstod: {e}")
