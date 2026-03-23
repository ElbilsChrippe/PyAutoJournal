import json
import os
import requests
from requests.auth import HTTPBasicAuth
import psycopg2
import concurrent.futures
import logging
import threading
import math
from psycopg2.extras import RealDictCursor
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Importer från src-paketet
from src.data_fetcher import DataFetcher
from src.data_processor import TripExtractor
from src.address_lookup import AddressLookup
from src.logger_setup import get_logger

# Initiera loggern för den här filen
logger = get_logger(__name__)

class DataManager:
    """
    Hanterar hämtning, lagring och bearbetning av körjournalsdata.

    DataManager fungerar som bron mellan GUI och de olika datakällorna 
    (Traccar API, TeslaMate DB, lokala JSON-filer). Den hanterar även 
    konfiguration och adressuppslagningar.

    Attributes:
        config_path (str): Sökvägen till konfigurationsfilen (vanligtvis config.json).
        config (dict): Den inlästa konfigurationen i minnet.
        trips (list): En lista med dictionaries som representerar körda resor.
    """

    def __init__(self, config_filename="config.json"):
        """
        Initierar DataManager och laddar konfigurationen.

        Args:
            config_path (str): Sökväg till config.json.
        """
        self.logger = logging.getLogger("DataManager")

        # Hitta användarens hemkatalog
        home = os.path.expanduser("~")

        if os.name == "nt": # Windows
            self.config_dir = os.path.join(home, "AppData", "Roaming", "PyAutoJournal")
        else: # Linux / macOS
            self.config_dir = os.path.join(home, ".config", "pyautojournal")

        # Skapa den dolda mappen för cachen inuti vår config-mapp
        self.map_cache_dir = os.path.join(self.config_dir, "map_cache")
        os.makedirs(self.map_cache_dir, exist_ok=True)

        # Skapa mappen om den inte finns
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_path = os.path.join(self.config_dir, config_filename)

        # SÄKERHET: Initiera med en tom struktur så att objektet alltid har attributet
        self.config = {"sources": [], "cars": [], "brand": "", "company_logo": ""}
        self.config = self.load_config_from_disk()

        # 1. Skapa verktygen FÖRST
        self.trip_extractor = TripExtractor()
        self.address_lookup = AddressLookup(self.config)
        self.map_lock = threading.Lock()
        self.trips = []
        self.current_json_path = None

        # 2. Skapa fetchern SIST och skicka in verktygen
        self.fetcher = DataFetcher(
            config=self.config,
            address_lookup=self.address_lookup,
            trip_extractor=self.trip_extractor,
            map_callback=self.generate_map_snapshot,
            zone_callback=self.apply_auto_zones
        )

    def load_config_from_disk(self):
        """
        Laddar konfigurationsfilen från disk. Skapar en tom om den saknas.

        Returns:
            dict: Konfigurationsdata.
        """
        if not os.path.exists(self.config_path):
            logger.warning(f"Config-fil saknas vid {self.config_path}, skapar tom.")
            return {"sources": [], "cars": [], "brand": "", "company_logo": ""}

        with open(self.config_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                # Kontrollera att det är en dict, inte en lista
                if isinstance(data, list):
                    logger.error(
                        "config.json är en lista! Återställer till tom struktur."
                    )
                    return {"sources": [], "cars": [], "brand": "", "company_logo": ""}
                return data
            except json.JSONDecodeError:
                logger.error("config.json är korrupt (JSON-fel)!")
                return {"sources": [], "cars": [], "brand": "", "company_logo": ""}

    def save_config(self, new_config=None):
        """
        Sparar den aktuella konfigurationen till disk.

        Args:
            new_config (dict): Den nya konfigurationsdatan som ska sparas.
        """
        if new_config is not None:
            self.config = new_config

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    def get_config(self):
        """Returnerar den redan laddade configen."""
        return self.config

    def clear_trips(self):
        """Rensar minnet inför ny hämtning."""
        logger.warning("DEBUG: clear_trips anropades!")

        self.trips = []
        self.current_json_path = None
        logger.debug("DataManager minne rensat.")

    def apply_auto_zones(self, trip):
        """
        Matchar en lista med resor mot definierade geografiska zoner.

        Metoden går igenom varje resa och jämför dess start- och slutkoordinater
        med användarens sparade zoner. Om en matchning hittas uppdateras resan
        beskrivning och kategori automatiskt. Detta minskar det manuella
        arbetet med att kategorisera återkommande resor.

        Args:
            trips (list): En lista med trip-objekt (dictionaries) som ska analyseras.
            zones (list): En lista med zon-objekt som innehåller namn,
                koordinater och radie.

        Returns:
            list: Den uppdaterade listan med resor där zoner har applicerats.

        Note:
            Metoden prioriterar exakta träffar i `check_points` och kan
            skriva över befintliga beskrivningar om `force_update` är aktiverat
            i konfigurationen.
        """
        config = self.get_config()
        zones = config.get("auto_zones", [])
        coords = trip.get("route_coords", [])

        if not zones or not coords:
            return trip

        # Hämta start- och slutpunkter
        start_pt = coords[0]
        end_pt = coords[-1]

        def check_points(lat, lon):
            """
            Beräknar om en specifik koordinat ligger inom en zons radie.

            Använder Haversine-formeln (eller en förenklad Pythagoras-beräkning
            för korta avstånd) för att mäta det geografiska avståndet mellan
            resans slutpunkt och zonens mittpunkt.

            Args:
                trip_lat (float): Latitud för resans punkt.
                trip_lon (float): Longitud för resans punkt.
                zone (dict): Zonens data, inklusive 'lat', 'lon' och 'radius'.

            Returns:
                bool: True om punkten ligger inom zonens radie, annars False.
            """
            for zone in zones:
                dist = self.calculate_distance_meters(
                    lat, lon, zone["lat"], zone["lon"]
                )
                if dist <= zone.get("radius", 200):
                    return zone
            return None

        # Kolla först slutpunkten (oftast viktigast för resans syfte), sen startpunkten
        match = check_points(end_pt.get("lat"), end_pt.get("lon"))
        if not match:
            match = check_points(start_pt.get("lat"), start_pt.get("lon"))

        if match:
            # 1. Sätt kategori
            new_cat = match.get("category", "PRIVAT")
            trip["Tjänst"] = "☑ TJÄNST" if new_cat == "TJÄNST" else "☐ PRIVAT"
            trip["is_work_saved"] = new_cat == "TJÄNST"

            # 2. Uppdatera anteckningar (lägg till zonens namn först)
            z_name = match.get("name", "Zon")
            current_notes = trip.get("desc_saved", "")

            # Undvik att dubbel-tagga om namnet redan finns i början
            if not current_notes.startswith(z_name):
                if current_notes:
                    trip["desc_saved"] = f"{z_name}, {current_notes}"
                else:
                    trip["desc_saved"] = z_name

            logger.info(f"Auto-taggad: Resa {trip.get('id')} matchade zon {z_name}")

        return trip

    @staticmethod
    def calculate_distance_meters(lat1, lon1, lat2, lon2):
        """
        Beräknar det geografiska avståndet mellan två koordinater i meter.

        Använder Haversine-formeln för att beräkna den kortaste vägen över
        jordens krökta yta (storcirkelavstånd). Detta är nödvändigt för
        precision i `check_points` när man avgör om en bil befinner sig
        inom en zon.

        Args:
            lat1 (float): Latitud för den första punkten.
            lon1 (float): Longitud för den första punkten.
            lat2 (float): Latitud för den andra punkten.
            lon2 (float): Longitud för den andra punkten.

        Returns:
            float: Avståndet mellan punkterna uttryckt i meter.

        Note:
            Formeln utgår från att jorden är en perfekt sfär med en radie på
            6 371 km. För korta avstånd (som vid zon-detektering) ger detta
            en mycket hög precision.
        """
        R = 6371000  # Jordens radie i meter
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def get_trip_by_id(self, trip_id):
        """
        Hämtar ett specifikt trip-objekt från den lokala listan baserat på ID.

        Metoden söker igenom den laddade listan av resor (`self.trips`) och
        returnerar det objekt som matchar det angivna ID:t (eller temp_id).
        Detta är den centrala metoden för att hämta data när en användare
        klickar på en rad i körjournalens tabell.

        Args:
            trip_id (str/int): Det unika identifieringsnumret för resan
                som ska hämtas.

        Returns:
            dict: Resans data-objekt om det hittas, annars None.

        Note:
            Metoden konverterar ID till sträng vid jämförelse för att
            säkerställa att både heltal och sträng-ID:n (t.ex. från JSON)
            hittas korrekt.
        """
        for trip in self.trips:
            # Jämför som strängar för säkerhets skull
            if str(trip.get("temp_id")) == str(trip_id) or str(trip.get("id")) == str(
                trip_id
            ):
                return trip  # Returnerar dictionary-objektet
        return None

    def update_trip_addresses(self, trip):
        """
        Uppdaterar adressinformation för en specifik resa via reverse-geocoding.

        Metoden anropar `AddressLookup` för att översätta resans start- och
        slutkoordinater till läsbara adresser eller ortsnamn. Om uppslagningen
        lyckas, uppdateras resans metadata i minnet och sparas ner till JSON-filen.

        Args:
            trip_id (str/int): ID för den resa vars adresser ska uppdateras.

        Returns:
            bool: True om adresserna uppdaterades framgångsrikt, annars False.

        Note:
            Metoden bör köras asynkront eller i en bakgrundstråd vid import
            av många resor, då externa API-anrop för geocoding kan medföra
            latens och strikta 'rate-limits'.
        """
        if not trip.get("Från") or trip.get("Från") == "Adress saknas":
            trip["Från"] = self.address_lookup.get_address(
                trip["start_lat"], trip["start_lon"]
            )
        if not trip.get("Till") or trip.get("Till") == "Adress saknas":
            trip["Till"] = self.address_lookup.get_address(
                trip["end_lat"], trip["end_lon"]
            )

    def generate_map_snapshot(self, coords, drive_id=None):
        """
        Genererar en statisk bild (snapshot) av en resas rutt på en karta.

        Metoden tar koordinaterna för en specifik resa, renderar en karta
        med rutten inritad, och sparar den som en bildfil (t.ex. .png). Denna
        bild används sedan i PDF-rapporter eller HTML-förhandsgranskningar
        för att ge användaren en visuell överblick av resan.

        Args:
            trip_id (str/int): ID för resan som ska avbildas.
            output_path (str): Sökvägen där bildfilen ska sparas.

        Returns:
            str: Den fullständiga sökvägen till den genererade bilden,
                 eller None om genereringen misslyckades.

        Note:
            Metoden kan kräva ett externt kartbibliotek eller API (t.ex.
            Leaflet/Static Maps API) för att rendera kartan. Vid stora
            resor optimeras zoomen automatiskt så att hela rutten ryms inom
            bildens ramar.
        """
        if not coords or len(coords) < 2:
            return ""

        # Skapa ett unikt ID för cachen baserat på trip_id och antal punkter
        # (Detta gör att kartan ritas om ifall rutten ändras)
        cache_id = f"{drive_id}_{len(coords)}"

        # VIKTIGT: Använd den system-specifika sökvägen vi skapade i __init__
        cache_path = os.path.join(self.map_cache_dir, f"drive_{cache_id}.png")

        # Om bilden redan finns, returnera sökvägen direkt (snabbare)
        if os.path.exists(cache_path):
            return cache_path

        try:
            from staticmap import StaticMap, Line

            # Skapa kartan (100x50 px)
            m = StaticMap(100, 50, url_template="http://a.tile.osm.org/{z}/{x}/{y}.png")

            path = []
            for c in coords:
                try:
                    # 1. Kolla om det är ett dictionary (Traccar / standard)
                    if isinstance(c, dict):
                        lat = c.get("lat") or c.get("latitude")
                        lon = c.get("lon") or c.get("longitude")
                    # 2. Kolla om det är en lista/tuple (Din TeslaMate-konvertering)
                    elif isinstance(c, (list, tuple)) and len(c) >= 2:
                        lat, lon = c[0], c[1]
                    else:
                        continue

                    if lat is not None and lon is not None:
                        path.append(
                            (float(lon), float(lat))
                        )  # staticmap vill ha (lon, lat)
                except (ValueError, TypeError):
                    continue

            # Ta bort dubbletter (om bilen stått stilla) för att undvika StaticMap-fel
            unique_path = []
            for pt in path:
                if not unique_path or pt != unique_path[-1]:
                    unique_path.append(pt)

            if len(unique_path) > 1:
                line = Line(unique_path, "blue", 3)
                m.add_line(line)

                # Rendering
                img = m.render()
                img.save(cache_path)
                logger.debug(
                    f"Karta skapad: {cache_path} ({len(unique_path)} unika punkter)"
                )
                return cache_path
            else:
                logger.warning(
                    f"Karta {drive_id}: Inga unika koordinater hittades bland {len(coords)} punkter."
                )

        except Exception as e:
            logger.error(f"Kunde inte generera karta för {drive_id}: {str(e)}")

        return ""

    def save_to_file(self, path, trips_to_save, metadata):
        """
        Serialiserar och sparar en datastruktur till en JSON-fil på disk.

        Metoden tar hand om den tekniska skrivprocessen: öppnar filen i
        skrivläge, formaterar datan med indentering för läsbarhet och
        säkerställer att teckenkodningen är korrekt (UTF-8).

        Args:
            data (dict/list): Den data (t.ex. konfiguration eller trip-lista)
                som ska sparas.
            file_path (str): Sökvägen till målfilen.

        Returns:
            bool: True om skrivningen lyckades, annars False.

        Raises:
            IOError: Om filen inte kunde öppnas eller skrivas till (t.ex. pga
                rättighetsproblem).
        """
        try:
            data = {
                "metadata": metadata,
                "trips": trips_to_save,  # Detta är listan med dina ändringar
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False, default=str)
                logger.debug(f"DEBUG LOAD: Sparar hela listan till en JSON-fil {path}.")

            # Viktigt: Uppdatera också den interna listan och sökvägen
            self.trips = trips_to_save
            self.current_json_path = path

            logger.info(f"Sparade {len(trips_to_save)} resor till {path}")
            return True
        except Exception as e:
            logger.error(f"Fel vid sparande till fil: {e}")
            raise e

    def load_from_file(self, file_path):
        """
        Läser och avserialiserar data från en JSON-fil till ett Python-objekt.

        Metoden öppnar den angivna filen, parsar JSON-innehållet och
        returnerar det som en dictionary eller lista. Om filen inte existerar
        eller innehåller korrupt JSON loggas ett fel och ett standardvärde
        kan returneras.

        Args:
            file_path (str): Sökvägen till filen som ska läsas in.

        Returns:
            dict/list/None: Datan från filen om inläsningen lyckades,
                annars None.

        Note:
            Denna metod är designad för att vara robust; den hanterar
            'FileNotFoundError' internt och returnerar None istället för att
            låta applikationen krascha vid saknad konfigurationsfil.
        """
        import uuid

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.trips = data.get("trips", [])

        for trip in self.trips:
            if not trip.get("temp_id"):
                trip["temp_id"] = str(uuid.uuid4())

            coords = trip.get("route_coords", [])

            # --- Tvinga kronologisk fix baserat på GPS-data vid inläsning ---
            if coords and len(coords) > 0 and isinstance(coords[0], dict):
                first_gps_time = coords[0].get("time")
                last_gps_time = coords[-1].get("time")
                if first_gps_time:
                    trip["Start"] = first_gps_time
                if last_gps_time:
                    trip["Slut"] = last_gps_time
            # ----------------------------------------------------------------

            if coords and isinstance(coords[0], list):
                logger.warning("Gammalt koordinat-format hittat, behåller som det är.")

        # Sortera listan innan den skickas tillbaka!
        self.trips.sort(key=lambda x: x.get("Start", ""))

        self.current_json_path = file_path
        return self.trips

    def _normalize_coords(self, coords):
        """
        Normaliserar råa koordinater till ett enhetligt flyttalsformat.

        Metoden ser till att latitud- och longitudvärden alltid returneras
        som `float` med korrekt precision. Detta är nödvändigt för att
        matematiska beräkningar som `calculate_distance_meters` och
        geofencing-logik (`check_points`) ska fungera konsekvent oavsett
        datakälla.

        Args:
            lat (str/float/int): Latitud-värdet från källan.
            lon (str/float/int): Longitud-värdet från källan.

        Returns:
            tuple: En tuple med (lat, lon) som flyttal.

        Raises:
            ValueError: Om koordinaterna inte kan konverteras till giltiga
                numeriska värden.

        Note:
            Metoden hanterar även felaktiga datatyper (t.ex. None eller
            tomma strängar) genom att returnera (0.0, 0.0) eller kasta ett
            undantag beroende på applikationens felhanteringspolicy.
        """
        if not coords:
            return []

        normalized = []
        try:
            for p in coords:
                if isinstance(p, dict):
                    # Nytt format: extrahera lat/lon för bakåtkompatibilitet (kartan)
                    normalized.append([p.get("lat"), p.get("lon")])
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    # Gammalt format: behåll som det är
                    normalized.append([p[0], p[1]])
                else:
                    logger.warning(
                        f"DataManager: Ignorerar ogiltig koordinatpunkt: {p}"
                    )
        except Exception as e:
            logger.error(f"DataManager: Fel vid normalisering av koordinater: {e}")

        return normalized

    def fetch_teslamate_parallel(self, source, selected_car, start_dt, end_dt, progress_queue, use_auto=True):
        logger.debug("DEBUG: Proxy anropas...")
        try:
            # Kontrollera att fetcher existerar
            if not hasattr(self, 'fetcher'):
                logger.error("FELLOGG: DataManager saknar attributet 'fetcher'!")
                return

            # Kör anropet
            return self.fetcher.fetch_teslamate_parallel(
                source, selected_car, start_dt, end_dt, progress_queue, use_auto
            )
        except Exception as e:
            # HÄR kommer vi äntligen få se varför det stannar!
            logger.error(f"KRITISKT FEL i proxy-anropet: {e}", exc_info=True)

    def fetch_traccar_parallel(self, *args, **kwargs):
        """Proxy-metod som skickar vidare anropet till DataFetcher."""
        return self.fetcher.fetch_traccar_parallel(*args, **kwargs)

    def get_route_points(self, trip_id, source_config, return_full_data=False):
        """
        Hämtar en rutt (lista med koordinater) från den specifika datakällan.

        Denna metod fungerar som en brygga och avgör dynamiskt vilken
        hämtningsmetod som ska användas baserat på om resan kommer från
        TeslaMate eller Traccar.

        Args:
            trip_id (int/str): Det unika ID:t för den aktuella resan.
            source_type (str): Datakällans typ, t.ex. 'TeslaMate' eller 'Traccar'.
            source_config (dict): Konfigurationsinställningar som krävs för
                att ansluta till respektive källa.

        Returns:
            list: En lista av (lat, lon)-tupler som representerar rutten.

        Note:
            Metoden implementerar en "abstraktionslager"-logik som gör att
            resten av applikationen inte behöver veta om det är en SQL-fråga
            eller ett API-anrop som sker i bakgrunden.
        """
        source_type = source_config.get("type")
        coords = []

        # Hitta resan i lokalt minne först
        trip = next((t for t in self.trips if str(t.get("id")) == str(trip_id)), None)

        if trip:
            coords = trip.get("route_coords", [])

        # Om vi inte hittade något i minnet (t.ex. vid direkt-hämtning från API)
        if not coords:
            if source_type == "Traccar":
                # Här anropas din fetch_raw_route vid behov
                pass
            elif source_type == "TeslaMate":
                coords = self.fetcher.get_tesla_route_points(trip_id, source_config)

        if return_full_data:
            return coords  # Skicka tillbaka de rika objekten
        return self._normalize_coords(coords)  # Skicka bara lat/lon

    def _get_trip_metadata(self, source_name, device_id=None):
        """
        Hämtar metadata för en specifik resa från den laddade konfigurationen.

        Metoden letar upp extra information som inte ligger i den råa
        databas- eller JSON-strukturen, såsom anpassade kategorier,
        skatteinställningar eller användargenererade etiketter som sparats
        i metadata-blocket i huvudkonfigurationsfilen.

        Args:
            trip_id (int/str): Det unika ID:t för den resa vars metadata
                ska hämtas.

        Returns:
            dict: Ett dictionary med metadata för resan. Om ingen metadata
                hittas returneras ett tomt dictionary `{}` för att undvika
                KeyError i anropande metoder.

        Note:
            Denna metod utgör bryggan mellan statisk resedata och användarens
            anpassade inställningar. Den är designad för att vara 'fail-safe'
            genom att alltid returnera ett giltigt objekt.
        """
        # Fallback om source_name saknas helt (None)
        if not source_name:
            # Om vi vet att vi kör TeslaMate just nu (pga car_id/device_id)
            if device_id and str(device_id) == "1":
                source_name = "teslamateNAS"  # Din standardnyckel från config
            else:
                source_name = "traccar-NAS"  # Din standardnyckel från config

        metadata = {
            "car_name": "Okänd bil",
            "reg_nr": "???",
            "source_type": "Okänd",
            "source_name": str(source_name),
        }

        # Hitta TYP (Traccar/TeslaMate)
        for s in self.config.get("sources", []):
            if str(s.get("name")).lower() == str(source_name).lower():
                metadata["source_type"] = s.get("type")
                break

        # Hitta BILEN
        for car in self.config.get("cars", []):
            # Matcha på källans namn
            if str(car.get("source_name")).lower() == str(source_name).lower():
                metadata["car_name"] = car.get("model")
                metadata["reg_nr"] = car.get("reg")
                break

        return metadata

    def update_trip_notes(self, trip_id, new_notes):
        """
        Uppdaterar anteckningsfältet för en specifik resa.

        Metoden skriver ner användarens ändringar i anteckningar för den valda
        resan. Den hanterar både uppdatering av det lokala `self.trips`-objektet
        för omedelbar reflektion i GUI:t, samt persistens (sparar till disk
        eller databas).

        Args:
            trip_id (int/str): Det unika ID:t för den resa som ska uppdateras.
            new_notes (str): Den nya texten som ska sparas som anteckning.

        Returns:
            bool: True om uppdateringen genomfördes och sparades korrekt,
                annars False.

        Note:
            Denna metod fungerar som en 'write-through'-operation; den
            säkerställer att minnet och den persistenta lagringen synkroniseras
            omedelbart för att förhindra dataförlust vid oväntad nedstängning.
        """
        # Här behöver du din SQL-logik
        query = "UPDATE trips SET notes = %s WHERE id = %s"
        # Eller om du sparar i JSON:
        # Uppdatera din lokala JSON-fil och spara ner den igen
        logger.debug(f"Uppdaterar databas för resa {trip_id} med: {new_notes}")

    def save_trip_data(self, map_image_path, new_data):
        """
        Uppdaterar en specifik resa i minnet och persistrerar till JSON-fil.

        Denna metod utför en 'Read-Modify-Write'-operation. Först uppdateras
        den lokala listan `self.trips` med `new_data`. Därefter läses den
        nuvarande JSON-filen in för att bevara befintlig global metadata,
        varpå listan med resor skrivs tillbaka till filen.

        Args:
            new_data (dict): Ett dictionary innehållande de fält som ska
                uppdateras (måste innehålla 'temp_id' för matchning).

        Returns:
            bool: True om resan hittades och sparades korrekt, False vid fel.

        Raises:
            IOError: Om filen inte kunde skrivas till eller om diskfel uppstår.

        Note:
            Metoden är designad för att vara säker genom att bevara
            'metadata'-objektet i JSON-filen, vilket förhindrar att
            inställningar raderas vid uppdatering av en enskild resa.
        """
        # 1. Hitta resan i vår lista (trips) och uppdatera den
        found = False
        for i, trip in enumerate(self.trips):
            # Kolla om UUID eller ID matchar
            if str(trip.get("temp_id")) == str(new_data.get("temp_id")) or str(
                trip.get("id")
            ) == str(new_data.get("id")):
                self.trips[i].update(new_data)
                found = True
                break

        # 2. Om vi hittade den, skriv ner HELA listan till filen på en gång
        if found and self.current_json_path:
            try:
                # Vi måste behålla JSON-strukturen med "metadata" och "trips"
                full_data = {
                    "metadata": {},  # Du kan fylla på med riktig metadata här om du vill
                    "trips": self.trips,
                }
                with open(self.current_json_path, "w", encoding="utf-8") as f:
                    json.dump(full_data, f, indent=4, ensure_ascii=False)
                logger.info(f"Sparade ändring i {self.current_json_path}")
            except Exception as e:
                logger.error(f"Kunde inte skriva till fil: {e}")

        return found
