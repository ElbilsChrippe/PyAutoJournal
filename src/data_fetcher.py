import logging
import psycopg2
import requests
import concurrent.futures
import uuid
from datetime import datetime
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

class DataFetcher:
    """
    Hanterar all extern kommunikation för att hämta resedata och koordinater.

    Denna klass agerar som ett abstraktionslager mot externa källor
    (t.ex. PostgreSQL för TeslaMate eller REST API för Traccar). Den
    ansvarar för nätverksprotokoll, databasanslutningar och rådata-parsning,
    men lämnar lagring och affärslogik till DataManager.
    """

    TESLAMATE_DRIVES_QUERY = """
            SELECT
                d.id,
                d.start_date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm' as start_t,
                (SELECT date FROM positions WHERE drive_id = d.id ORDER BY date DESC LIMIT 1)
                    AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm' as actual_end_t,
                COALESCE(g1.name, a1.name, a1.city, 'Okänd') as från,
                COALESCE(g2.name, a2.name, a2.city, 'Okänd') as till,
                round(d.distance::numeric, 2) as km,
                p1.battery_level as start_soc,
                p2.battery_level as end_soc,
                (SELECT odometer FROM positions WHERE drive_id = d.id ORDER BY date ASC LIMIT 1) as start_odometer,
                (SELECT odometer FROM positions WHERE drive_id = d.id ORDER BY date DESC LIMIT 1) as end_odometer,
                (SELECT AVG(speed) FROM positions WHERE drive_id = d.id) as avg_speed
            FROM drives d
            LEFT JOIN addresses a1 ON d.start_address_id = a1.id
            LEFT JOIN addresses a2 ON d.end_address_id = a2.id
            LEFT JOIN geofences g1 ON d.start_geofence_id = g1.id
            LEFT JOIN geofences g2 ON d.end_geofence_id = g2.id
            LEFT JOIN positions p1 ON d.start_position_id = p1.id
            LEFT JOIN positions p2 ON d.end_position_id = p2.id
            WHERE d.car_id = %s
              AND d.start_date >= %s
              AND d.start_date <= %s
              AND d.distance > 0.1
            ORDER BY d.start_date ASC;
        """

    def __init__(self, config, address_lookup, trip_extractor, map_callback, zone_callback):
        """
        Initierar DataFetcher med nödvändiga verktyg och konfiguration.

        Args:
            config (dict): Systemets konfiguration.
            address_lookup (AddressLookup): Instans för adressuppslagning.
            trip_extractor (TripExtractor): Instans för att extrahera resor från råpunkter.
            map_callback (function): Metod i DataManager för att skapa kartbilder.
            zone_callback (function): Metod i DataManager för att applicera auto-zoner.
        """
        self.config = config
        self.db_config = config.get("teslamate_db", {})
        self.address_lookup = address_lookup
        self.trip_extractor = trip_extractor
        self.generate_map_snapshot = map_callback
        self.apply_auto_zones = zone_callback

    def fetch_traccar_parallel(self, source, selected_car, start_dt, end_dt, progress_queue, use_auto=True):
        """
        Hämtar och bearbetar Traccar-resor asynkront.

        Args:
            source (dict): Källa med anslutningsdetaljer.
            selected_car (dict): Bilens metadata (regnr, modell, enhets-ID).
            start_dt (str): Startdatum i ISO-format.
            end_dt (str): Slutdatum i ISO-format.
            progress_queue (Queue): Kö för att skicka status och data till GUI.
            use_auto (bool): Om auto-zoner ska appliceras automatiskt.
        """
        details = source.get("details", {})
        try:
            config_to_use = {
                "base_url": details.get("url"),
                "username": details.get("user"),
                "password": details.get("pass"),
                "deviceId": selected_car.get("device_id", "1"),
            }
            raw_points = self._fetch_raw_route(config_to_use, start_dt, end_dt)
            self.trip_extractor.reset_counter()
            raw_trips = self.trip_extractor.extract_trips(raw_points)
        except Exception as e:
            logger.error(f"Kunde inte hämta rådata från Traccar: {e}")
            progress_queue.put(("ERROR", 0, f"Hämtningsfel: {str(e)}"))
            return

        if not raw_trips:
            progress_queue.put(("DONE", 100, None))
            return

        self._process_parallel(raw_trips, selected_car, progress_queue, use_auto, "Traccar")

    def fetch_teslamate_parallel(self, source, selected_car, start_dt, end_dt, progress_queue, use_auto=True):
        """
        Hämtar rådata från TeslaMate-databasen och bearbetar resor parallellt.

        Metoden ansluter direkt till TeslaMates PostgreSQL-databas, extraherar körningar
        baserat på tidsintervall och bil-ID, och använder sedan en ThreadPoolExecutor
        för att parallellt generera kartbilder och formatera resedata.

        Args:
            source (dict): Innehåller anslutningsdetaljer ("details") såsom host, db, user, pass.
            selected_car (dict): Den valda bilen från config. Innehåller "car_id" eller "device_id".
            start_dt (datetime): Startdatum för hämtningen.
            end_dt (datetime): Slutdatum för hämtningen.
            progress_queue (queue.Queue): Kö för att kommunicera framsteg och data tillbaka till GUI.
            use_auto (bool): Om automatiska zoner/kategorisering ska appliceras. Standard är True.

        Processflöde:
            1. Validerar Bil-ID (mappar device_id -> car_id om nödvändigt).
            2. Upprättar anslutning till PostgreSQL.
            3. Kör 'TESLAMATE_DRIVES_QUERY' för att få en lista på alla körningar.
            4. För varje körning startas en asynkron tråd som:
                a. Hämtar detaljerade ruttpunkter (positions).
                b. Genererar en statisk kartbild (snapshot).
                c. Beräknar adresser och kategorier.
            5. Rapporterar löpande framsteg (%) via progress_queue.
            6. Skickar "DONE" när alla trådar är klara.

        Raises:
            psycopg2.Error: Vid databasrelaterade problem.
            Exception: Fångar upp och loggar oväntade fel för att förhindra att huvudtråden dör.
        """
        try:
            logger.debug(f"STARTAR TESLAMATE: Car: {selected_car.get('reg')}, Period: {start_dt} -> {end_dt}")

            details = source.get("details", {})
            car_id = int(selected_car.get('device_id', 1))

            if not car_id:
                logger.error(f"FEL: Inget 'car_id' hittades för {selected_car.get('reg')}. Kolla din config.json!")
                progress_queue.put(("ERROR", 0, "Saknar TeslaMate Bil-ID"))
                return

            db_params = {
                "host": details.get("host"),
                "database": details.get("db"),
                "user": details.get("user", "teslamate"),
                "password": details.get("pass"),
                "port": details.get("port", "5432"),
                "connect_timeout": 5
            }

            conn = psycopg2.connect(**db_params)
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Logga den faktiska frågan som körs för att kunna kopiera den till pgAdmin
            logger.debug(f"Kör SQL med: car_id={car_id}, start={start_dt}, end={end_dt}")

            cur.execute(self.TESLAMATE_DRIVES_QUERY, (car_id, start_dt, end_dt))
            rows = cur.fetchall()
            conn.close()

            logger.info(f"TeslaMate DB returnerade {len(rows)} resor.")

            if not rows:
                logger.warning("Inga resor hittades i TeslaMate-databasen för detta intervall.")
                progress_queue.put(("DONE", 100, None))
                return

            formatted_raw_trips = []
            for row in rows:
                drive_id = row["id"]
                # Hämta ruttpunkter för kartan
                coords = self._fetch_tesla_route_points(drive_id, db_params)

                # Paketera exakt allt som fanns i din gamla logik
                trip_data = {
                    "id": drive_id,
                    "start_time": row["start_t"],
                    "end_time": row["actual_end_t"],
                    "km": float(row["km"] or 0),
                    "start_odometer": float(row["start_odometer"] or 0),
                    "end_odometer": float(row["end_odometer"] or 0),
                    "avg_speed_raw": float(row.get("avg_speed") or 0),
                    "från_db": row.get("från"), # Adress från TeslaMates egna geocoder
                    "till_db": row.get("till"),
                    "soc_start": row.get("start_soc"),
                    "soc_end": row.get("end_soc"),
                    "route_coords": coords
                }
                formatted_raw_trips.append(trip_data)

            self._process_parallel(formatted_raw_trips, selected_car, progress_queue, use_auto, "TeslaMate")

        except Exception as e:
            logger.error(f"TeslaMate-fel: {e}")
            progress_queue.put(("ERROR", 0, str(e)))

    def _process_parallel(self, raw_trips, selected_car, progress_queue, use_auto, source_name):
        """
        Orkestrerar parallell bearbetning av insamlade resor.

        Denna metod tar en lista med rådata (från antingen Traccar eller TeslaMate)
        och fördelar arbetet på flera trådar för att snabba upp tunga operationer
        som adressuppslag och kartgenerering.

        Args:
            raw_trips (list): Lista med dictionaries innehållande rå resedata.
            progress_queue (queue.Queue): Kö för kommunikation med GUI.
            use_auto (bool): Om automatisk zon-taggning ska köras.

        Process:
            1. Beräknar totalt antal resor för framstegsrapportering.
            2. Startar en ThreadPoolExecutor (standard 5-10 trådar).
            3. Skickar varje resa till '_process_single'.
            4. Rapporterar successivt tillbaka till GUI:t i takt med att resor blir klara.
        """
        total_trips = len(raw_trips)
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_trip = {
                executor.submit(self._process_single, t, selected_car, use_auto, source_name): t
                for t in raw_trips
            }
            for future in concurrent.futures.as_completed(future_to_trip):
                enriched_trip = future.result()
                completed += 1
                percent = int((completed / total_trips) * 100)
                # Skicka den färdiga resan till GUI (som sedan ger den till DataManager)
                progress_queue.put(("DATA", percent, enriched_trip))

        progress_queue.put(("DONE", 100, None))

    def _process_single(self, trip, selected_car, use_auto, source_name):
        """
        Transformerar en enskild råresa till ett standardiserat körjournalsformat.

        Detta är den mest kritiska metoden för datakvalitet. Den utför "data enrichment"
        genom att kombinera råa koordinater med externa tjänster.

        Args:
            trip (dict): En enskild resa med råvärden (t.ex. start_lat, end_lat).
            use_auto (bool): Om automatisk zon-taggning ska köras.

        Arbetssteg:
            1. Unikt ID: Genererar ett 'temp_id' (UUID) om permanent ID saknas.
            2. Tidsformatering: Konverterar tidsstämplar till läsbart format (ÅÅÅÅ-MM-DD HH:MM).
            3. Beräkningar: Räknar ut 'Total_Tid' och 'duration_min' (viktigt för DetailView).
            4. Kartgenerering: Anropar 'map_callback' för att skapa en PNG-miniatyr av rutten.
            5. Adressuppslag: Om start/stopp-namn saknas, anropas 'address_lookup'.
            6. Zon-analys: Om 'use_auto' är True, körs 'zone_callback' för att se om
               resan startade/slutade vid en känd kund eller hemma.
            7. Standardisering: Returnerar en dict med exakt de nycklar som
               JournalTable och DetailView förväntar sig.

        Returns:
            dict: En komplett, formaterad resa redo för visning och lagring.
        """
        coords = trip.get("route_coords", [])

        # 1. ID och Grund-metadata
        trip["temp_id"] = str(uuid.uuid4())
        trip["car_name"] = selected_car.get("model", "Okänd bil")
        trip["reg_nr"] = selected_car.get("reg", "???")
        trip["source_type"] = source_name

        # --- NYHET: Smart tids-tolkare för att hantera både DB-datum och API-strängar ---
        def parse_dt(dt_val):
            if isinstance(dt_val, datetime):
                return dt_val
            if isinstance(dt_val, str):
                try:
                    return datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
                except Exception:
                    pass
            return None

        start_dt = parse_dt(trip.get("start_time") or trip.get("Start"))
        end_dt = parse_dt(trip.get("end_time") or trip.get("Slut"))

        # 2. Tider och Total Tid
        if start_dt and end_dt:
            trip["Start"] = start_dt.strftime("%Y-%m-%d %H:%M")
            trip["Slut"] = end_dt.strftime("%Y-%m-%d %H:%M")

            duration = end_dt - start_dt
            total_seconds = int(duration.total_seconds())
            total_min = total_seconds // 60
            trip["duration_min"] = total_min
            trip["Total_Tid"] = f"{total_min // 60}h {total_min % 60}m"
        else:
            trip["Start"] = trip.get("Start", "Okänd tid")
            trip["Slut"] = trip.get("Slut", "Okänd tid")
            trip["Total_Tid"] = "0m"
            trip["duration_min"] = 0

        # 3. Distans och Mätarställning (FIX: Fallback för både Traccar och TeslaMate)
        km_raw = trip.get("km") if trip.get("km") is not None else trip.get("Km", 0)
        trip["Km"] = round(float(km_raw), 2)

        start_odo = trip.get("start_odometer") if trip.get("start_odometer") is not None else trip.get("Start_Odo", 0)
        trip["Start_Odo"] = float(start_odo)

        end_odo = trip.get("end_odometer") if trip.get("end_odometer") is not None else trip.get("End_Odo", 0)
        trip["End_Odo"] = float(end_odo)

        # Hastighet
        if "avg_speed_raw" in trip and trip["avg_speed_raw"] > 0:
            trip["Avg_Speed"] = round(trip["avg_speed_raw"], 1)
        else:
            duration_h = (end_dt - start_dt).total_seconds() / 3600 if start_dt and end_dt else 0
            trip["Avg_Speed"] = round(trip["Km"] / duration_h, 1) if duration_h > 0 else 0

        # 4. SoC (Batteri) - Specifikt för TeslaMate
        s_soc = trip.get("soc_start")
        e_soc = trip.get("soc_end")
        if s_soc is not None and e_soc is not None:
            trip["soc_info"] = f"{int(s_soc)}% -> {int(e_soc)}%"
        else:
            trip["soc_info"] = "-"

        # 5. Adresser (Från/Till)
        trip["Från"] = trip.get("från_db") or "Hämtar..."
        trip["Till"] = trip.get("till_db") or "Hämtar..."

        if coords and (not trip.get("från_db") or trip["från_db"] == "Okänd"):
            trip["Från"] = self.address_lookup.get_address(coords[0]["lat"], coords[0]["lon"])
        if coords and (not trip.get("till_db") or trip["till_db"] == "Okänd"):
            trip["Till"] = self.address_lookup.get_address(coords[-1]["lat"], coords[-1]["lon"])

        # 6. Karta
        if coords:
            real_id = trip.get("id") or trip.get("temp_id") or str(uuid.uuid4())[:8]
            trip["map_image_path"] = self.generate_map_snapshot(coords, drive_id=real_id)

        # 7. Status-stämplar (Viktigt för GUI)
        trip.setdefault("Tjänst", "PRIVAT")
        trip.setdefault("is_work_saved", False)
        trip.setdefault("desc_saved", "")

        # 8. Auto-zoner
        if use_auto:
            trip = self.apply_auto_zones(trip)

        # Synka kategori
        trip["category"] = trip.get("Tjänst", "PRIVAT")

        return trip

    def _fetch_raw_route(self, config_to_use, start_dt, end_dt):
        """
        Utför det faktiska API-anropet mot Traccar för att hämta rå ruttdata.

        Denna metod ansvarar för nätverkskommunikationen och JSON-parsning
        av råa positionspunkter. Den använder de specifika autentiseringsuppgifter
        och enhets-ID som skickas med för den valda bilen.

        Args:
            config_to_use (dict): Ett dictionary innehållande 'base_url',
                'username', 'password' och 'deviceId'.
            start_dt (str): Starttid i ISO-format (t.ex. '2023-10-01T00:00:00Z').
            end_dt (str): Sluttid i ISO-format.

        Returns:
            list: En lista av dictionaries där varje element representerar en
                rå positionspunkt (lat, lon, speed, timestamp etc.).

        Raises:
            requests.exceptions.HTTPError: Om servern svarar med en felkod (t.ex. 401 eller 404).
            requests.exceptions.ConnectionError: Vid nätverksproblem eller felaktig URL.
            requests.exceptions.Timeout: Om servern inte svarar inom angiven tidsram.

        Note:
            Metoden strippar automatiskt eventuella avslutande snedstreck från
            URL:en för att säkerställa att API-stigen blir korrekt formaterad.
        """
        # 1. Använd 'config_to_use' som skickades med (istället för bara 'config')
        url = f"{config_to_use['base_url'].rstrip('/')}/api/positions"

        # 2. Hämta auth från 'config_to_use' (det vi skickade från fetch_traccar_parallel)
        auth = (config_to_use["username"], config_to_use["password"])

        # 3. Parametrar för API-anropet
        params = {"deviceId": config_to_use["deviceId"], "from": start_dt, "to": end_dt}

        # 4. Utför anropet
        response = requests.get(url, auth=auth, params=params, timeout=30)
        response.raise_for_status()

        return response.json()

    def _fetch_tesla_route_points(self, drive_id, db_params):
        """
        Hämtar detaljerade ruttpunkter och telemetri för en specifik körning.

        Metoden frågar TeslaMates 'positions'-tabell efter alla datapunkter som
        hör till en viss 'drive_id'. Detta inkluderar inte bara position,
        utan även hastighet, höjd och batterinivå (SoC).

        Args:
            drive_id (int/str): Det interna ID:t för körningen i TeslaMates databas.
            db_params (dict): Färdiga anslutningsinställningar för PostgreSQL.

        Process:
            1. Upprättar en kortvarig anslutning till databasen.
            2. Kör en SQL-fråga som sorterar punkterna kronologiskt (ORDER BY date ASC).
            3. Konverterar databasens PostgreSQL-objekt (som Decimal och Datetime)
               till standardiserade Python-typer (float, int, ISO-strängar).

        Returns:
            list[dict]: En lista med ruttpunkter. Varje punkt innehåller:
                - 'time': Tidpunkt i ISO-format.
                - 'lat' & 'lon': Geografiska koordinater (float).
                - 'speed': Hastighet avrundad till en decimal.
                - 'alt': Höjd över havet (float).
                - 'soc': Batterinivå i procent (int).

            Returnerar en tom lista [] om inga punkter hittas eller vid anslutningsfel.
        """
        query = """
            SELECT
                date,
                latitude::float AS lat,
                longitude::float AS lon,
                speed::float,
                elevation::float AS alt,
                battery_level::int AS soc
            FROM positions
            WHERE drive_id = %s
            ORDER BY date ASC;
        """
        try:
            # Vi använder RealDictCursor för att slippa hålla koll på kolumn-index
            conn = psycopg2.connect(**db_params)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, (drive_id,))
            rows = cur.fetchall()
            conn.close()

            # Vi mappar om resultatet för att säkerställa korrekt datatyp och ISO-tid
            return [
                {
                    "time": row["date"].isoformat() if row["date"] else None,
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "speed": round(float(row["speed"] or 0), 1),
                    "alt": float(row["alt"] or 0),
                    "soc": row["soc"]
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Kunde inte hämta ruttpunkter för drive {drive_id}: {e}")
            return []
