import logging

logger = logging.getLogger(__name__)


class TripExtractor:
    def __init__(self):
        self.trip_counter = 1

    def reset_counter(self):
        self.trip_counter = 1

    def extract_trips(self, points, source="Traccar"):
        """
        Bearbetar råa GPS-punkter.
        source: 'Traccar' eller 'TeslaMate'
        """
        if not isinstance(points, list):
            return []

        trips = []
        current_trip = None

        for i, p in enumerate(points):
            try:
                if not p or not isinstance(p, dict):
                    continue

                attrs = p.get("attributes", {})

                # --- LOGIK FÖR ATT STARTA RESA ---
                # TeslaMate har ofta inte ignition, men SoC-ändring eller speed > 0 indikerar resa
                is_moving = (
                    attrs.get("ignition", False)
                    if source == "Traccar"
                    else (p.get("speed", 0) > 0)
                )

                if is_moving and not current_trip:
                    current_trip = {
                        "id": self.trip_counter,
                        "Start": p.get("deviceTime"),
                        "Från": p.get("address", "Adress saknas"),
                        "Start_Odo": attrs.get("totalDistance", 0)
                        or attrs.get("odometer", 0),
                        "soc_start": attrs.get("soc", None),  # Tesla-specifikt
                        "route_coords": [],
                        "source": source,
                    }

                # --- LOGGA PUNKT ---
                if current_trip:
                    point_data = {
                        "lat": p.get("latitude"),
                        "lon": p.get("longitude"),
                        "time": p.get("deviceTime"),
                        "speed": round(
                            p.get("speed", 0) * (1.852 if source == "Traccar" else 1), 1
                        ),
                        "alt": round(p.get("altitude", 0), 0),
                        "soc": attrs.get("soc", None),  # Tesla-specifikt
                    }
                    current_trip["route_coords"].append(point_data)
                    # Uppdatera löpande SoC-end
                    if attrs.get("soc") is not None:
                        current_trip["soc_end"] = attrs.get("soc")

                # --- AVSLUTA RESA ---
                if not is_moving and current_trip:
                    start_odo = current_trip.get("Start_Odo", 0)
                    end_odo = attrs.get("totalDistance", 0) or attrs.get("odometer", 0)
                    dist = (end_odo - start_odo) / 1000

                    if dist > 0.1:
                        current_trip.update(
                            {
                                "Slut": p.get("deviceTime"),
                                "Till": p.get("address", "Adress saknas"),
                                "Km": round(dist, 2),
                                "End_Odo": end_odo,
                            }
                        )
                        trips.append(current_trip)
                        self.trip_counter += 1
                    current_trip = None

            except Exception as e:
                logger.error(f"Fel vid punkt {i}: {e}")
                continue

        return trips
