import tkinter as tk
import os
from tkinter import ttk
from src.logger_setup import get_logger

# Initiera loggern för den här filen
logger = get_logger(__name__)


class JournalTable:
    """
    Hanterar den visuella tabellvyn (Treeview) för körjournalen.

    JournalTable ansvarar för att rita upp listan med resor, hantera miniatyrkartor,
    sortering och användarinteraktion (som dubbelklick för detaljer). Den kommunicerar
    tillbaka till GUI-hanteraren via callbacks när en resa väljs eller ändras.

    Attributes:
        tree (ttk.Treeview): Själva tabellkomponenten.
        _img_cache (dict): Håller referenser till PhotoImage-objekt för att förhindra
                           att Python raderar dem ur minnet (Garbage Collection).
        _id_map (dict): Mappar tabellens interna rad-ID mot resornas faktiska ID.
    """
    def __init__(
        self, parent, raw_callback, data_manager, get_source_callback, parent_app=None
    ):
        """
        Initierar tabellen och konfigurerar kolumner och stil.

        Args:
            parent: Tkinter-containern som tabellen ska ligga i.
            raw_callback (function): Funktion som anropas vid dubbelklick (visar detaljvy).
            data_manager: Instans av DataManager för att hämta/spara data.
            get_source_callback (function): Hämtar information om vald datakälla.
            parent_app: Referens till huvudapplikationen för koordinering.
        """
        self.parent_app = parent_app  # Sparar referensen till appen!
        self.get_source_callback = get_source_callback
        self.parent = parent
        self.raw_callback = raw_callback
        self.data_manager = data_manager
        self.selected_id = None
        self._id_map = {}

        # Container för tabell och scroll
        self.container = ttk.Frame(parent)
        self.container.pack(fill="both", expand=True, padx=10, pady=5)

        # 1. DEFINIERA KOLUMNER (inkl. 'map' för TeslaFi-looken)
        self.tree_style = ttk.Style()
        self.tree_style.configure("Journal.Treeview", rowheight=60)
        columns = ("start", "slut", "från", "till", "km", "typ", "notering")
        self.tree = ttk.Treeview(
            self.container,
            style="Journal.Treeview",
            columns=columns,
            show=["headings", "tree"],
            height=15,
        )
        self.tree.column("#0", width=120, minwidth=120, stretch=tk.NO)
        self.tree.heading("#0", text="Karta")
        self.tree.tag_configure("locked", foreground="#888888", background="#F9F9F9")
        # Ofta kräver ttk att man sätter stilen explictit om man använder ett tema
        style = ttk.Style()
        # Om du använder ett tema som 'clam', 'alt' eller 'default',
        # kan du behöva lägga till detta:
        style.map("Journal.Treeview", foreground=[("disabled", "#888888")])

        # 2. STICKY HEADERS & KOLUMNBREDDER
        headings = {
            "start": "Starttid",
            "slut": "Sluttid",
            "från": "Från",
            "till": "Till",
            "km": "Km",
            "typ": "Kategori",
            "notering": "Notering/Syfte",
        }

        for col, text in headings.items():
            self.tree.heading(col, text=text)
            # Sätt standardbredd, men gör 'map' och 'notering' lite rymligare
            width = 130
            if col == "km":
                width = 60
            if col == "notering":
                width = 250
            self.tree.column(col, width=width, anchor="w" if col != "km" else "center")

        # 3. SCROLLBARS (Vertical & Horizontal)
        vsb = ttk.Scrollbar(self.container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(
            self.container, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Layout med grid för att få scroll-listerna att sitta snyggt
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)

        # Skapa en stil för att öka radhöjden så bilderna får plats
        style = ttk.Style()
        style.configure("Treeview", rowheight=60)
        self.tree.configure(style="Treeview")

        # 4. MUSHJULS-SUPPORT (Ubuntu/Linux fokus)
        self.tree.bind("<Button-4>", lambda e: self.tree.yview_scroll(-1, "units"))
        self.tree.bind("<Button-5>", lambda e: self.tree.yview_scroll(1, "units"))

        # Enkelklick för att editera
        self.tree.bind("<Button-1>", self.on_tree_click)

        # Dubbelklick för att gå till ruttöversikt
        self.tree.bind("<Double-1>", self.on_double_click)

        self.tree.bind("<Motion>", self.update_cursor)

    def update_cursor(self, event):
        """
        Ändrar muspekarens utseende beroende på var användaren håller musen i tabellen.

        Denna metod triggas vid 'Motion'-events (när musen rör sig över tabellen).
        Dess syfte är att indikera för användaren att raderna är klickbara eller
        att vissa kolumner har specifika funktioner.

        Args:
            event (tk.Event): Tkinter-eventet som innehåller musens position (x, y).

        Logik:
            1. Identifierar vilken del av tabellen (region) musen befinner sig över
               med hjälp av 'identify_region'.
            2. Om musen är över en datacell ('cell') eller trädstrukturen ('tree'):
               - Ändrar pekaren till en "hand" (likt en länk i en webbläsare).
            3. Om musen är utanför eller på en rubrik:
               - Återställer pekaren till standardpilen.
        """
        column = self.tree.identify_column(event.x)
        if column == "#7":
            # Prova 'xterm' istället för 'ibeam'
            self.tree.configure(cursor="xterm")
        else:
            # Använd 'arrow' eller en tom sträng för att återställa
            self.tree.configure(cursor="arrow")

    def clear(self):
        """
        Rensar tabellen helt från rader och återställer interna mappar.

        Används vid byte av källfil, vid ny hämtning från API eller när
        användaren väljer att tömma vyn.

        Logik:
            1. Loopar igenom alla existerande rader i Treeview och raderar dem.
            2. Tömmer '_id_map' (kopplingen mellan tabell-ID och resans ID).
            3. Rensar '_img_cache' så att gamla kartbilder inte ligger kvar i minnet.
        """
        for item in self.tree.get_children():
            self.tree.delete(item)
        # TÖM CACHEN
        self._img_paths = {}
        self._img_cache = {}

    def refresh_data(self, new_data):
        """
        Totaluppdaterar tabellen utifrån en ny lista med resor.

        Args:
            trips (list): En lista med rese-dictionaries (från DataManager).

        Process:
            1. Anropar 'clear()' för att tömma tabellen.
            2. Loopar igenom den inskickade listan.
            3. Anropar 'add_single_row' för varje resa för att rita upp dem på nytt.
            4. Ser till att tabellen visar den senaste informationen efter t.ex. en sortering.
        """
        # 1. Rensa befintlig data
        self.clear()

        # 2. Iterera genom den nya datan och lägg till rader
        # Vi använder enumerate för att behålla ett unikt index (iid)
        for i, trip in enumerate(new_data):
            self.add_row(trip, i)

        logger.info(f"Tabellen uppdaterad med {len(new_data)} rader.")

    def refresh_table(self):
        """
        Tvingar fram en visuell uppdatering av Treeview-komponenten.

        Används när mindre ändringar har skett (som t.ex. att en bild laddats klart
        i bakgrunden) för att säkerställa att ändringen faktiskt syns för användaren.
        Metoden triggar omritning av tabellens 'update_idletasks'.
        """
        # 1. Nollställ mappen och rensa Treeview
        self._id_map = {}
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 2. Hämta data
        trips = self.data_manager.trips

        # 3. SORTERA LISTAN KRONOLOGISKT
        trips.sort(key=lambda x: x.get("start_t") or x.get("Start", ""), reverse=True)

        # 4. Fyll på med den sorterade datan
        for trip in trips:
            unique_id = str(trip.get("temp_id") or trip.get("id"))

            typ = "☑ TJÄNST" if trip.get("is_work_saved") else "☐ PRIVAT"

            self.tree.insert(
                "",
                "end",
                iid=unique_id,
                values=(
                    trip.get("Start", ""),
                    trip.get("Slut", ""),
                    trip.get("Från", ""),
                    trip.get("Till", ""),
                    trip.get("Km", ""),
                    typ,
                    trip.get("desc_saved", ""),
                ),
            )
            self._id_map[unique_id] = trip

            if "map_image_path" in trip:
                self.force_load_image(unique_id, trip["map_image_path"])

        self.tree.tag_raise("locked")

    def sort_table_chronologically(self):
        """
        Sorterar alla rader i tabellen baserat på starttid.

        Eftersom resor kan läsas in asynkront eller från olika källor, används
        denna metod för att tvinga fram en kronologisk ordning. Den påverkar
        endast den visuella ordningen i Treeview-komponenten.

        Args:
            reverse (bool): Om True sorteras resorna med den nyaste överst.
                            Standard är False (äldsta resan högst upp).

        Logik:
            1. Hämtar alla rad-ID:n (items) från Treeview.
            2. Skapar en lista med tupler innehållande (starttid, rad-ID).
            3. Sorterar listan baserat på tidsstämpeln.
            4. Flyttar om raderna i tabellen ('self.tree.move') till deras
               nya positioner baserat på den sorterade ordningen.
        """
        try:
            # 1. Hämta alla rader som en lista av tupler: (Startdatum, Rad-ID)
            # Vi hämtar värdet från kolumnen "start"
            items = [(self.tree.set(k, "start"), k) for k in self.tree.get_children("")]

            # 2. Sortera listan i STIGANDE ordning (reverse=False)
            # Det gör att t.ex. '2025-01-01' kommer före '2025-01-31'
            items.sort(reverse=False)

            # 3. Möblera om raderna i Treeview
            # Vi lägger in dem i ordning från index 0 och uppåt
            for index, (val, k) in enumerate(items):
                self.tree.move(k, "", index)

            logger.debug("Tabellen sorterad: Äldsta resan högst upp.")
        except Exception as e:
            logger.error(f"Kunde inte sortera tabellen: {e}")

    def add_or_update_image(self, trip_id, img_path):
        """
        Uppdaterar kartbilden för en specifik rad i tabellen.

        Eftersom kartor genereras parallellt kan en rad skapas innan bilden
        är klar. Denna metod tillåter applikationen att "skjuta in" bilden
        i efterhand utan att störa användarens interaktion med tabellen.

        Args:
            trip_id (str): Det unika ID:t för resan som ska uppdateras.
            image_path (str): Sökvägen till den genererade PNG-filen.

        Process:
            1. Kontrollerar först om raden (trip_id) fortfarande existerar i tabellen.
            2. Anropar 'force_load_image' för att läsa in, skala om och cacha bilden.
            3. Tvingar fram en omritning av just den raden för att visa bilden direkt.
        """
        str_id = str(trip_id)

        # 1. Bildvalidering
        if not img_path or not os.path.exists(img_path):
            logger.warning(f"Bild saknas eller hittas ej: {img_path}")
            return

        try:
            from PIL import Image, ImageTk

            # 2. Skapa bilden
            img = Image.open(img_path)
            img.thumbnail((100, 50))
            photo = ImageTk.PhotoImage(img)

            # 3. Spara i cachen (Viktigt: Dictionary håller referensen vid liv!)
            if not hasattr(self, "_img_cache"):
                self._img_cache = {}
            self._img_cache[str_id] = photo

            # 4. Uppdatera raden i Treeview
            if self.tree.exists(str_id):
                self.tree.item(str_id, image=photo)

            # Spara även sökvägen
            if not hasattr(self, "_img_paths"):
                self._img_paths = {}
            self._img_paths[str_id] = img_path

        except Exception as e:
            logger.error(f"Kunde inte ladda bild för {str_id}: {e}")

    def add_single_row(self, trip):
        """
        Lägger till en enskild resa som en ny rad i tabellen.

        Metoden extraherar relevanta fält, hanterar visuella indikatorer (ikoner)
        och ser till att eventuella kartbilder ritas ut korrekt.

        Args:
            trip_data (dict): En dictionary med resans data (start, slut, km, etc.).

        Logik:
            1. Hämtar unikt ID (temp_id eller id) för att identifiera raden.
            2. Formaterar strängar för start/slut-tid och adresser.
            3. Kontrollerar om resan har en sparad notering; lägger i så fall till
               en bock-symbol (☑) i noteringsfältet.
            4. Infogar raden i Treeview med 'self.tree.insert'.
            5. Om 'map_image_path' finns, anropas 'force_load_image' för att
               visa kartan i den första kolumnen (#0).
        """
        try:
            # --- SMART ID-HANTERING ---
            # Vi hämtar det unika ID:t (temp_id för nya, id för gamla)
            unique_id = str(trip.get("temp_id", trip.get("id", "")))

            typ = "☑ TJÄNST" if trip.get("is_work_saved") else "☐ PRIVAT"
            vals = (
                trip.get("Start", ""),
                trip.get("Slut", ""),
                trip.get("Från", ""),
                trip.get("Till", ""),
                trip.get("Km", ""),
                typ,
                trip.get("desc_saved", ""),
            )

            # Tvinga Tkinter att använda vårt unika ID som radens iid
            item_id = self.tree.insert("", "end", iid=unique_id, values=vals)
            # ---------------------------

            img_path = trip.get("map_image_path", "")
            self._id_map[item_id] = img_path  # Nu är item_id == unique_id

            if img_path and os.path.exists(img_path):
                self.parent.after(50, lambda: self.force_load_image(item_id, img_path))

            self.tree.yview_moveto(1.0)
        except Exception as e:
            logger.error(f"Kunde inte lägga till rad: {e}")

    def add_row(self, trip, index):
        """
        Infogar en rad i tabellen med specifika värden och valfri bild.

        Denna metod fungerar som en lägre nivå av 'add_single_row' och tillåter
        direkt kontroll över exakt vad som skrivs in i varje kolumn.

        Args:
            values (tuple/list): En samling värden som matchar tabellens kolumner
                                 (start, slut, från, till, km, typ, notering).
            image_path (str, optional): Sökväg till en kartbild för raden.
            trip_id (str, optional): Ett unikt ID. Om det saknas skapas ett internt ID.

        Returns:
            str: Det ID som raden fick i Treeview-komponenten.
        """
        # --- SMART ID-HANTERING ---
        # Tvinga fram UUID:t, fallback på id, fallback på index
        unique_id = str(trip.get("temp_id") or trip.get("id") or f"auto_{index}")

        # Spara i din interna map
        if not hasattr(self, "_id_map"):
            self._id_map = {}
        self._id_map[unique_id] = unique_id

        # 1. BILDHANTERING & CACHE-UPPDATERING
        img_path = trip.get("map_image_path", "")
        self.add_or_update_image(unique_id, img_path)

        # 2. DATATVÄTT (behåll din gamla logik för clean här)
        def clean(val):
            return str(val).strip() if val is not None else ""

        km_clean = clean(str(trip.get("Km", "0")).replace("km", "").strip()) + " km"
        typ_raw = clean(trip.get("Tjänst", trip.get("typ", "PRIVAT")))
        status_text = "☑ TJÄNST" if "TJÄNST" in typ_raw.upper() else "☐ PRIVAT"

        row_values = (
            clean(trip.get("Start", "")),
            clean(trip.get("Slut", "")),
            clean(trip.get("Från", "")),
            clean(trip.get("Till", "")),
            km_clean,
            status_text,
            clean(trip.get("desc_saved", "")),
        )

        # 3. SKAPA RADEN OCH TVINGA IID TILL VÅRT UNIKA ID
        try:
            self.tree.insert(
                "", "end", iid=unique_id, values=row_values  # Här använder vi unique_id
            )

            # 4. Hämta och visa bilden i ett separat steg
            img_path = trip.get("map_image_path")
            if img_path:
                # FIX: Ändra trip_id -> unique_id här också!
                self.add_or_update_image(unique_id, img_path)

        except Exception as e:
            # Här använder vi unique_id korrekt i loggen
            logger.error(f"Fel vid insättning av rad {unique_id}: {e}")

    def select_row_by_id(self, trip_id):
        """
        Letar upp och markerar en specifik resa i tabellen baserat på dess ID.

        Används t.ex. efter att en användare har sparat en ändring i detaljvyn
        eller vid sökningar, för att visuellt fokusera på rätt rad.

        Args:
            trip_id (str): Det unika ID:t (UUID eller databas-ID) för resan.

        Logik:
            1. Kontrollerar om trip_id finns i tabellen.
            2. Om den finns, används 'selection_set' för att markera raden blå.
            3. Anropar 'see(trip_id)' för att automatiskt scrolla tabellen så
               att den markerade raden blir synlig för användaren.
        """
        str_id = str(trip_id)

        # Kolla om ID:t finns i trädet
        if self.tree.exists(str_id):
            # Rensa tidigare val
            self.tree.selection_remove(self.tree.selection())
            # Välj den nya raden
            self.tree.selection_set(str_id)
            self.tree.see(str_id)
            self.tree.focus(str_id)
            logger.debug(f"JournalTable: Markerade rad {str_id} framgångsrikt.")
        else:
            # Om den inte finns, logga vad som finns i trädet (för debugging)
            all_items = self.tree.get_children()
            logger.error(
                f"JournalTable: Hittade INTE raden med ID '{str_id}'. "
                f"Tillgängliga ID:n i trädet: {all_items[:10]}..."
            )

    def get_item_by_id(self, trip_id):
        """
        Hämtar all data för en specifik resa baserat på dess ID i tabellen.

        Denna metod fungerar som en brygga: den tar det tekniska ID:t från
        Tkinter (t.ex. 'I001') och returnerar den kompletta datan för resan.

        Args:
            item_id (str): Det unika ID:t för raden i Treeview.

        Returns:
            dict/None: En dictionary med resans alla fält, eller None om ID:t inte finns.
        """
        str_id = str(trip_id)

        # 1. Kolla om det är ett direkt matchande UUID (vanligaste fallet)
        if self.tree.exists(str_id):
            return str_id

        # 2. Om inte, försök hitta via vårt id_map (om du har ett sånt)
        # eller logga ett tydligt varningsmeddelande istället för att krascha
        logger.warning(f"JournalTable: Hittade inte trip_id {trip_id} direkt i trädet.")
        return None

    def get_next_id(self, current_id):
        """
        Hittar ID för nästa resa i tabellens nuvarande vy.

        Metoden tar hänsyn till hur användaren har sorterat tabellen. Om man
        står på den sista resan returneras None.

        Args:
            current_id (str): ID för resan som visas just nu.

        Returns:
            str/None: ID för nästa rad i listan.
        """
        all_items = self.tree.get_children()
        try:
            idx = all_items.index(str(current_id))
            if idx < len(all_items) - 1:
                return all_items[idx + 1]
        except ValueError:
            return None
        return None

    def get_prev_id(self, current_id):
        """
        Hittar ID för föregående resa i tabellens nuvarande vy.

        Används för att backa i listan utan att behöva gå tillbaka till huvudmenyn.
        Om man står på den första resan returneras None.

        Args:
            current_id (str): ID för resan som visas just nu.

        Returns:
            str/None: ID för föregående rad i listan.
        """
        all_items = self.tree.get_children()
        # Logga för att se om vårt ID ens finns i listan
        print(f"DEBUG: get_prev_id letar efter {current_id} i {len(all_items)} rader.")

        try:
            # Vi konverterar till strängar för att vara säkra på matchning
            idx = [str(item) for item in all_items].index(str(current_id))
            if idx > 0:
                print(f"DEBUG: Hittade index {idx}, returnerar {all_items[idx - 1]}")
                return all_items[idx - 1]
            else:
                print("DEBUG: Vi är vid första raden (index 0).")
        except ValueError:
            print(
                f"DEBUG: ERROR! {current_id} hittades inte i tabellens ID-lista: {all_items}"
            )
            return None
        return None

    def on_select(self, event):
        """
        Hanterar enkelklick och markering av en rad i tabellen.

        Syftet är att hålla reda på vilken resa som är aktiv i användargränssnittet.

        Args:
            event (tk.Event): Själva klick-händelsen.

        Logik:
            1. Hämtar listan över markerade rader (selection).
            2. Om en rad är markerad, sparar den radens ID i 'self.selected_id'.
            3. Detta ID används senare om användaren t.ex. väljer att
               ta bort resan eller exportera just den valda raden.
        """
        selection = self.tree.selection()
        if not selection:
            return

        item_id = self.tree.identify_row(event.y)
        if item_id:
            # Hämta ID från din datastruktur (använd t.ex. en dict: {item_id: trip_id})
            self.selected_id = self._id_map.get(item_id)
            logger.info(f"Selected id {self.selected_id} är nu vald.")

    def on_double_click(self, event):
        """
        Hanterar dubbelklick på en rad för att öppna detaljvyn.

        Detta är standardvägen för användaren att se rutt-detaljer,
        ändra kategorier eller skriva noteringar.

        Args:
            event (tk.Event): Dubbelklick-händelsen.

        Logik:
            1. Identifierar vilken rad som musen befann sig över vid klicket.
            2. Hämtar resans fullständiga data via 'get_item_by_id'.
            3. Anropar 'raw_callback' (som pekar på DetailView) med
               resans data som argument.
        """
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        item_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)  # returnerar t.ex. "#7"

        if not item_id:
            return

        # Vi tillåter bara editering av kolumn #7 (Notering/syfte)
        if column == "#7":
            # Skicka kolumn-index 6 (eftersom values-arrayen är 0-baserad: 0=start, 6=notering)
            self.edit_cell(item_id, 6)
        else:
            # Om man dubbelklickar på vilken annan kolumn som helst
            self.tree.selection_set(item_id)
            self.tree.focus(item_id)

            logger.debug(f"DEBUG: Dubbelklickade på rad med ID: {item_id}")

            # Anropa callbacken med det unika ID:t (hoppa till detail_view)
            self.raw_callback(item_id)

    def on_tree_click(self, event):
        """
        Hanterar klick specifikt i träd-kolumnen (#0).

        I en Treeview är den första kolumnen speciell. Denna metod ser till
        att klick på t.ex. miniatyrkartan eller utfällningspilen (om sådan finns)
        resulterar i att raden markeras korrekt, precis som i de andra kolumnerna.

        Args:
            event (tk.Event): Klick-händelsen.

        Logik:
            1. Använder 'identify_region' för att se om klicket skedde i 'tree'-regionen.
            2. Säkerställer att fokus hamnar på rätt rad även om användaren
               missar texten och klickar direkt på bilden.
        """
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        if col == "#6":  # Kolumnen för Tjänst/Privat
            val = self.tree.item(item, "values")[5]
            is_now_work = "☐" in val  # Om den var privat, blir den tjänst

            new_val = "☑ TJÄNST" if is_now_work else "☐ PRIVAT"
            self.tree.set(item, column="typ", value=new_val)

            # --- HÄR ÄR FIXEN: Synka till DataManager ---
            # Hitta resan i den stora listan med hjälp av UUID (item)
            trip_data = next(
                (
                    t
                    for t in self.data_manager.trips
                    if str(t.get("temp_id")) == str(item)
                    or str(t.get("id")) == str(item)
                ),
                None,
            )

            if trip_data:
                trip_data["is_work_saved"] = is_now_work
                trip_data["Tjänst"] = "TJÄNST" if is_now_work else "PRIVAT"
                logger.info(f"Synkade tabelländring till minnet för rutt {item}")
                if self.parent_app:
                    self.parent_app.mark_journal_unsaved()

    def edit_cell(self, row_id, col_index):
        """
        Möjliggör direktredigering av en cell i tabellen (In-place editing).

        Metoden identifierar vilken cell användaren klickade på, skapar en
        temporär inmatningsruta (Entry) ovanpå cellen och hanterar sparandet
        av det nya värdet.

        Args:
            event (tk.Event): Klick-händelsen som triggade redigeringen.

        Process:
            1. Identifiering: Bestämmer rad (item) och kolumn (column) via muspositionen.
            2. Validering: Kontrollerar att kolumnen är redigerbar (t.ex. 'typ' eller 'notering').
            3. Positionering: Beräknar cellens exakta koordinater (x, y, bredd, höjd).
            4. Skapande: Skapar en 'tk.Entry' eller 'ttk.Combobox' som placeras exakt över cellen.
            5. Fokus: Sätter fokus på inmatningsrutan och binder 'Return' (spara) och 'Escape' (avbryt).
            6. Avslut: När användaren trycker Enter eller klickar utanför, uppdateras DataManager
               och det temporära elementet tas bort.
        """
        col_id = f"#{col_index + 1}"
        bbox = self.tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, width, height = bbox

        current_values = list(self.tree.item(row_id, "values"))
        old_value = current_values[col_index]

        editor = ttk.Entry(self.tree)
        editor.insert(0, old_value)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        # Flagga för att förhindra att vi sparar två gånger (Enter + FocusOut)
        self._is_saving = False

        def save(event=None):
            if self._is_saving:
                return
            self._is_saving = True

            new_val = editor.get()
            # Förstör editorn direkt så den försvinner från UI
            editor.destroy()

            # 1. Uppdatera trädet visuellt
            current_values[col_index] = new_val
            self.tree.item(row_id, values=current_values)

            # 2. Logga och synka
            logger.debug(f"Anropar _sync_to_manager för ID {row_id}")
            self._sync_to_manager(row_id, "notering", new_val)
            if self.parent_app:
                self.parent_app.mark_journal_unsaved()

        editor.bind("<Return>", save)
        editor.bind("<FocusOut>", save)
        editor.bind("<Escape>", lambda e: editor.destroy())

    def _sync_to_manager(self, row_id, col_name, value):
        """
        Synkroniserar en ändring från tabellen direkt till DataManager och JSON-filen.

        Denna metod anropas så fort en cell har redigerats färdigt (via 'edit_cell').
        Den ser till att ändringen inte bara är visuell, utan permanent sparad.

        Args:
            trip_id (str): Det unika ID:t för resan som ändrats.
            field (str): Namnet på kolumnen/fältet som uppdaterats (t.ex. 'typ' eller 'notering').
            new_value (str): Det nya värdet som användaren matat in.

        Logik:
            1. Hämtar den aktuella resans fullständiga data från '_id_map'.
            2. Uppdaterar det specifika fältet i resans dictionary.
            3. Om fältet är 'notering', rensas eventuella symboler (som ☑) bort
               innan lagring för att hålla rådatan ren.
            4. Anropar 'self.data_manager.update_trip_in_json' för att skriva
               ner hela den uppdaterade listan till hårddisken.
            5. Loggar händelsen för att underlätta felsökning.
        """
        # 1. Hämta från mappen
        trip = self._id_map.get(str(row_id))  # Tvinga row_id till sträng

        # FELSÖKNING: Vad är 'trip' egentligen?
        logger.debug(f"DEBUG: Typen på trip för ID {row_id} är {type(trip)}")

        if not trip or isinstance(trip, str):
            # Om mappen svek, sök i DataManager som reserv
            trip = self.data_manager.get_trip_by_id(row_id)

        if not trip or isinstance(trip, str):
            logger.error(
                f"Kunde inte hitta ett giltigt objekt för {row_id}. Fick: {trip}"
            )
            return

        # 2. Här kör vi uppdateringen
        try:
            if col_name == "notering":
                trip["desc_saved"] = value
            elif col_name == "typ":
                trip["is_work_saved"] = "TJÄNST" in value
                trip["Tjänst"] = "TJÄNST" if trip["is_work_saved"] else "PRIVAT"

            # 3. Spara
            self.data_manager.save_trip_data(trip.get("map_image_path"), trip)

            # 4. Uppdatera DetailView
            if self.parent_app and hasattr(self.parent_app, "detail_view"):
                dv = self.parent_app.detail_view
                if dv.current_data:
                    dv_id = str(
                        dv.current_data.get("temp_id") or dv.current_data.get("id")
                    )
                    if dv_id == str(row_id):
                        dv.update_view(trip)
        except Exception as e:
            logger.error(f"Krasch vid tilldelning: {e}. Objektet var: {trip}")

    def get_all_data(self):
        """
        Extraherar all aktuell data från tabellens rader till en lista med objekt.

        Används främst vid export till PDF eller Excel, samt vid slutgiltig
        lagring av hela sessionen. Metoden läser av de faktiska värdena som
        användaren ser i GUI:t.

        Returns:
            list[dict]: En lista där varje element är en resa med fält som
                        'start', 'slut', 'km', 'typ' och 'notering'.

        Logik:
            1. Loopar igenom varje rad (item) i Treeview.
            2. Hämtar värdena för varje kolumn.
            3. 'Tvättar' datan genom att rensa bort GUI-specifika symboler
               (som ☑ eller ☐) från noteringsfältet för att få ren text.
            4. Paketerar om datan till ett standardiserat format.
        """
        all_data = []
        for item_id in self.tree.get_children():
            # values hämtar hela radens data som en tuple:
            # (map, start, slut, från, till, km, typ, notering)
            values = self.tree.item(item_id, "values")

            # Samt sökväg till bild
            img_path = getattr(self, "_img_paths", {}).get(item_id, "")

            row_data = {
                "map_image_path": img_path,
                "Start": values[0],
                "Slut": values[1],
                "Från": values[2],
                "Till": values[3],
                "Km": values[4],
                "Tjänst": values[5]
                .replace("☑ ", "")
                .replace("☐ ", ""),  # Rensa symboler
                "desc_saved": values[6],
            }
            all_data.append(row_data)
        return all_data

    def force_load_image(self, trip_id, img_path):
        """
        Laddar, skalar om och visar en kartbild för en specifik rad.

        Metoden hanterar hela kedjan från bildfil på hårddisken till renderad
        pixel i tabellen, med strikt fokus på minneshantering.

        Args:
            trip_id (str): ID för raden som ska få bilden.
            img_path (str): Sökvägen till bildfilen (PNG).

        Process:
            1. Validering: Kontrollerar att filen faktiskt existerar på hårddisken.
            2. Bearbetning (PIL): Öppnar bilden och skapar en miniatyr (thumbnail)
               på 100x50 pixlar för att passa radhöjden.
            3. Image-to-Tkinter: Konverterar PIL-bilden till ett 'ImageTk.PhotoImage'.
            4. Cache-lagring: Sparar objektet i 'self._img_cache[trip_id]'. Detta är
               avgörande då Python annars raderar bilden ur RAM-minnet (Garbage Collection).
            5. Rendering: Uppdaterar Treeview-objektet med den nya bilden i kolumn #0.

        Raises:
            Loggar fel (via logger.error) om bilden är korrupt eller inte kan läsas,
            men låter programmet fortsätta köras.
        """
        # 1. Kolla om filen faktiskt finns innan vi gör något
        if not img_path or not os.path.exists(img_path):
            return

        try:
            from PIL import Image, ImageTk

            # 2. Öppna och bearbeta bilden
            # Vi använder en lokal variabel för att undvika problem med globala referenser
            img = Image.open(img_path)
            img.thumbnail((100, 50))  # Skala om för tabellraden
            photo = ImageTk.PhotoImage(img)

            # 3. Spara en referens i en cache (VIKTIGT!)
            # Om du inte sparar 'photo' i en lista/dict kommer Python's garbage collector
            # att radera bilden och den kommer inte synas i tabellen.
            if not hasattr(self, "_img_cache"):
                self._img_cache = {}
            self._img_cache[trip_id] = photo

            # 4. Uppdatera trädet om raden fortfarande finns kvar
            if self.tree.exists(trip_id):
                self.tree.item(trip_id, image=photo)

        except Exception as e:
            # Vi loggar felet men kraschar inte applikationen
            logger.warning(
                f"Kunde inte ladda kartbild för {trip_id} från {img_path}: {e}"
            )
