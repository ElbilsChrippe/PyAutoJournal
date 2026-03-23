import requests
import logging
import time


class AddressLookup:
    """
    Hanterar översättning av GPS-koordinater till läsbara gatuadresser (Reverse Geocoding).

    Klassen använder i första hand Geoapify via en API-nyckel för hög prestanda
    och tillförlitlighet. Om nyckel saknas eller om tjänsten fallerar, faller den
    automatiskt tillbaka på OpenStreetMaps gratis-API (Nominatim). Klassen
    hanterar även nödvändig 'rate-limiting' (hastighetsbegränsning) för att
    undvika att applikationen blir blockerad av externa leverantörer.
    """
    def __init__(self, config):
        """
        Initierar AddressLookup med nödvändiga API-konfigurationer och headers.

        Args:
            config (dict): Huvudkonfigurationen för applikationen, innehållande
                eventuella API-nycklar under 'api_keys'-noden.
        """
        self.logger = logging.getLogger("AddressLookup")

        # Hämta API-nycklar från konfigurationen
        api_keys = config.get("api_keys", {})
        self.geo_key = api_keys.get("geoapify")

        # Headers för Nominatim-fallback (viktigt för att inte bli bannlyst)
        self.headers = {
            "User-Agent": "PyAutoJournal_v7_Christer (christer@example.com)"
        }
        self.last_call_time = 0

    def get_address(self, lat, lon):
        """
        Hämtar en formaterad gatuadress baserat på latitud och longitud.

        Detta är huvudmetoden som anropas av resten av applikationen. Den
        dirigerar automatiskt förfrågan till den primära tjänsten (Geoapify)
        och använder reservtjänsten (Nominatim) om det första försöket misslyckas.

        Args:
            lat (float/str): Latitud för platsen.
            lon (float/str): Longitud för platsen.

        Returns:
            str: En läsbar adress (t.ex. "Storgatan 1, Stockholm"), eller en
                 fallback-sträng (t.ex. "Okänd plats") om uppslagningen misslyckas.
        """
        if not lat or not lon:
            return "Okänd plats"

        # 1. Försök med Geoapify (Huvudtjänst - ingen rate limit behövs här)
        if self.geo_key:
            address = self._fetch_geoapify(lat, lon)
            if address:
                return address

        # 2. Fallback till Nominatim (Endast om Geoapify misslyckas)
        # Vi lägger in en liten paus här för säkerhets skull
        return self._fetch_nominatim(lat, lon)

    def _fetch_geoapify(self, lat, lon):
        """
        Utför ett API-anrop mot Geoapify för reverse geocoding.

        Denna metod fungerar som den primära uppslagstjänsten. Den innehåller
        en inbyggd 'retry'-mekanism som automatiskt väntar och gör ett nytt
        försök om det första anropet resulterar i en nätverks-timeout.

        Args:
            lat (float/str): Latitud.
            lon (float/str): Longitud.

        Returns:
            str: Den formaterade adressen om anropet lyckas, annars None.
        """
        url = "https://api.geoapify.com/v1/geocode/reverse"
        params = {"lat": lat, "lon": lon, "format": "json", "apiKey": self.geo_key}

        for i in range(2):  # Försök 2 gånger
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        return results[0].get("formatted", "Adress saknas")
            except requests.exceptions.Timeout:
                time.sleep(1)  # Vänta en sekund vid timeout och försök igen
                continue
        return None

    def _fetch_nominatim(self, lat, lon):
        """
        Utför ett API-anrop mot OpenStreetMaps Nominatim-tjänst som fallback.

        Eftersom Nominatim är en gratistjänst med strikta användarvillkor
        (Terms of Use), implementerar denna metod en tvingande fördröjning
        ('rate-limit') på minst 1.1 sekunder mellan varje anrop. Detta
        förhindrar att applikationens IP-adress blir permanent blockerad.

        Args:
            lat (float/str): Latitud.
            lon (float/str): Longitud.

        Returns:
            str: Den formaterade adressen, eller ett felmeddelande om även
                 reservtjänsten misslyckas.
        """
        # Nominatim kräver 1 anrop per sekund
        elapsed = time.time() - self.last_call_time
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)

        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        try:
            response = requests.get(url, headers=self.headers, timeout=3)
            self.last_call_time = time.time()
            if response.status_code == 200:
                return response.json().get("display_name", "Okänd plats")
        except Exception as e:
            self.logger.error(f"Nominatim-fallback-fel: {e}")
        return "Adress ej tillgänglig"
