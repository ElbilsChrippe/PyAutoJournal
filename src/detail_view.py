import tkinter as tk
from tkinter import ttk
import tkintermapview
from src.logger_setup import get_logger

# Initiera loggern för den här filen
logger = get_logger(__name__)


class DetailView:
    """
    Hanterar detaljvyn för en enskild resa med interaktiv karta och telemetri.

    DetailView är programmets sekundära huvudvy. Den presenterar en vald resa
    med hjälp av ett interaktivt kart-widget, tabeller för råa koordinater
    och en instrumentpanel för fordonsdata. Den tillåter även användaren
    att kategorisera resan och spara anteckningar.

    Attributes:
        map_widget (TkinterMapView): Den interaktiva kartan för ruttvisning.
        current_data (dict): Innehåller all data för den resa som visas just nu.
        telemetry_labels (dict): Referenser till UI-etiketter för snabb uppdatering.
        coord_tree (ttk.Treeview): Tabell som visar detaljerade ruttpunkter (lat/lon/hastighet).
    """
    def __init__(
        self,
        parent,
        data_manager,
        update_table_callback,
        notebook,
        journal_table,
        switch_to_journal_callback=None,
        parent_app=None,
    ):
        """
        Initierar detaljvyn och bygger upp det komplexa gränssnittet.

        Bygger en PanedWindow-layout som delar fönstret i:
        1. En karta (vänster/center).
        2. En infopanel med statisk ruttinfo och redigerbara fält (höger).
        3. En koordinattabell för teknisk granskning (botten).
        4. En telemetripanel för fordonsstatus som SoC och Odometer (längst till höger).

        Args:
            parent: Tkinter-containern (oftast en flik i ett Notebook).
            data_manager: Instans för hantering av läsning/skrivning av data.
            update_table_callback: Funktion för att uppdatera huvudtabellen vid ändringar.
            notebook: Referens till huvudfönstrets fliksystem.
            journal_table: Referens till JournalTable för navigering (nästa/föregående).
            switch_to_journal_callback: Funktion för att hoppa tillbaka till listvyn.
            parent_app: Referens till huvudapplikationen.
        """
        self.parent = parent
        self.data_manager = data_manager
        self.update_table_callback = update_table_callback
        self.notebook = notebook
        self.journal_table = journal_table
        self.switch_to_journal_callback = switch_to_journal_callback
        self.parent_app = parent_app

        self.current_data = None
        self.current_trip_id = None
        self.current_index = 0

        # Huvudcontainer: Horisontell delning (75/25)
        self.paned = ttk.PanedWindow(parent, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        # HÄR: Skapa en riktig karta som går att zooma!
        self.map_widget = tkintermapview.TkinterMapView(self.paned, corner_radius=0)
        self.paned.add(self.map_widget, weight=3)

        # Info-delen
        self.info_frame = ttk.Frame(self.paned)
        self.paned.add(self.info_frame, weight=1)

        # Statisk information (Read-only)
        self.static_frame = ttk.LabelFrame(self.info_frame, text="Ruttinformation")
        self.static_frame.pack(fill="x", padx=5, pady=5)
        self.static_label = ttk.Label(self.static_frame, text="", justify="left")
        self.static_label.pack(anchor="w", padx=5, pady=5)

        # Förädlad information (Redigerbar)
        self.edit_frame = ttk.LabelFrame(self.info_frame, text="Användardata")
        self.edit_frame.pack(fill="both", expand=True, padx=5, pady=5)

        category_frame = ttk.Frame(self.edit_frame)
        category_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(category_frame, text="Kategori:").pack(side="left", padx=(0, 10))

        self.tjanst_var = tk.StringVar(value="☐ PRIVAT")
        self.tjanst_button = tk.Button(
            category_frame,
            textvariable=self.tjanst_var,
            command=self.toggle_tjanst,
            bg="white",
            width=12,
            relief="solid",
            bd=1,
        )
        self.tjanst_button.pack(side="left")

        ttk.Label(self.edit_frame, text="Anteckningar:").pack(anchor="w", padx=5)
        self.details_text = tk.Text(self.edit_frame, height=8, bg="white")
        self.details_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Koppla ändringar till markera osparat
        if self.parent_app:
            self.details_text.bind("<KeyRelease>", lambda e: self.parent_app.mark_journal_unsaved())

        # Knapp-layout och tripinfo
        self.setup_buttons_tripinfo(self.edit_frame)

        # Gör textrutan redigerbar
        self.details_text.config(state="normal")

        # Textbox för mer info
        self.setup_additional_box(parent)
        self.setup_telemetry_panel()

        # Bind FocusOut (när man klickar utanför textrutan) till save
        self.details_text.bind("<FocusOut>", lambda e: self.save())

        self.btn_up = None
        self.btn_down = None

    def setup_additional_box(self, parent):
        """
        Bygger upp panelen för primär ruttinformation.

        Skapar ett strukturerat rutnät (grid) som visar de viktigaste mätvärdena
        från resan i ett lättläst format.

        Args:
            parent: Den container (Frame) där boxen ska placeras.

        Innehåll:
            - Start- och Slutadresser (från/till).
            - Total distans (km).
            - Körtid (formaterad i timmar och minuter).
            - Medelhastighet (beräknad utifrån distans/tid).

        Teknisk detalj:
            Använder 'ttk.Label' med specifika stilar för att skilja på
            rubriker (t.ex. "Distans:") och värden (t.ex. "12.5 km").
        """
        # 1. Huvudcontainer
        self.main_paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill="both", expand=True)

        # --- VÄNSTER SIDA (75%) ---
        self.left_side = ttk.Frame(self.main_paned)
        self.main_paned.add(self.left_side, weight=1)

        # OM DU VILL ATT TABELLEN SKA KUNNA ÄNDRA STORLEK:
        # Använd en PanedWindow HÄR också för att dela upp vänstersidan
        self.left_inner_paned = ttk.PanedWindow(self.left_side, orient=tk.VERTICAL)
        self.left_inner_paned.pack(fill="both", expand=True)

        # --- KOORDINATTABELL ---
        self.coord_frame = ttk.LabelFrame(
            self.left_inner_paned, text="Detaljerade ruttpunkter"
        )
        self.left_inner_paned.add(self.coord_frame, weight=1)

        cols = ("tid", "lat", "lon", "speed", "alt")
        self.coord_tree = ttk.Treeview(
            self.coord_frame, columns=cols, show="headings", height=8
        )
        self.coord_tree.heading("tid", text="Tidstämpel")
        self.coord_tree.heading("lat", text="Latitud")
        self.coord_tree.heading("lon", text="Longitud")
        self.coord_tree.heading("speed", text="km/h")
        self.coord_tree.heading("alt", text="Höjd (m)")

        # Sätt smalare bredd på koordinaterna
        for col in ["lat", "lon", "speed", "alt"]:
            self.coord_tree.column(col, width=80, anchor="center")

        self.coord_tree.pack(fill="both", expand=True)

        # --- HÖGER SIDA (25%): Telemetri & Kontroller ---
        self.right_side = ttk.Frame(self.main_paned)
        self.main_paned.add(self.right_side, weight=1)

    def setup_telemetry_panel(self):
        """
        Skapar panelen för fordons-telemetri och batteristatus.

        Denna panel är särskilt viktig för elbilar (Tesla) då den visualiserar
        energiförbrukning och mätarställning.

        Args:
            parent: Den container (Frame) där telemetrin ska visas.

        Funktion:
            1. Initierar 'self.telemetry_labels', en dictionary som håller
               referenser till alla värdefält så att de kan uppdateras dynamiskt
               när användaren bläddrar mellan resor.
            2. Skapar sektioner för:
               - SoC (State of Charge): Batterinivå vid start och slut (%).
               - Odometer: Bilens totala mätarställning (km).
               - Höjdskillnad: Om datan finns, visas start- och sluthöjd (meter).

        UX-design:
            Panelen placeras oftast längst till höger för att ge en snabb
            teknisk översikt utan att ta fokus från kartan eller anteckningarna.
        """
        # En huvud-scroll för om panelen blir lång i framtiden
        container = ttk.Frame(self.right_side)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # 1. Grundläggande Information (LabelFrame)
        basic_info = ttk.LabelFrame(container, text="Resans sammanfattning", padding=10)
        basic_info.pack(fill="x", pady=(0, 10))

        # Vi skapar en lista på de fält vi vill visa
        self.telemetry_labels = {}
        fields = [
            ("Start", "Starttid"),
            ("Slut", "Sluttid"),
            ("Km", "Distans (km)"),
            ("Tid", "Total körtid"),
            ("Snitt", "Medelhastighet"),
        ]

        for key, label_text in fields:
            row = ttk.Frame(basic_info)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{label_text}:", font=("Helvetica", 9, "bold")).pack(
                side="left"
            )
            val_lbl = ttk.Label(row, text="-")
            val_lbl.pack(side="right")
            self.telemetry_labels[key] = val_lbl

        # 2. Fordonsdata (SoC, ODO etc) - Förberett för Teslamate
        vehicle_info = ttk.LabelFrame(container, text="Fordonsdata", padding=10)
        vehicle_info.pack(fill="x", pady=10)

        v_fields = [
            ("Odo_Start", "Mätarställning Start"),
            ("Odo_Slut", "Mätarställning Slut"),
            ("SoC_Start", "Batteri Start"),
            ("SoC_Slut", "Batteri Slut"),
        ]

        for key, label_text in v_fields:
            row = ttk.Frame(vehicle_info)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{label_text}:", font=("Helvetica", 9, "bold")).pack(
                side="left"
            )
            val_lbl = ttk.Label(row, text="-")
            val_lbl.pack(side="right")
            self.telemetry_labels[key] = val_lbl

    def setup_raw_data_box(self, parent):
        """
        Skapar tabellvyn för rådata och GPS-koordinater.

        Denna sektion är viktig för teknisk verifiering. Den visar en
        detaljerad lista över alla loggade punkter under resan.

        Args:
            parent: Den container (Frame) där tabellen ska placeras.

        Funktion:
            1. Skapar en 'ttk.Treeview' med kolumner för:
               - Tid (när punkten loggades).
               - Latitud/Longitud (GPS-position).
               - Speed (hastighet i km/h).
               - SoC (batterinivå vid just den tidpunkten).
            2. Lägger till en vertikal scrollbar för att hantera långa resor
               med tusentals loggpunkter.
            3. Binder tabellen till 'self.coord_tree' för att kunna populeras
               dynamiskt i 'update_view'.
        """
        self.raw_data_frame = ttk.LabelFrame(parent, text="Rådata (Ruttpunkter)")
        self.raw_data_frame.pack(
            side="bottom", fill="both", expand=True, padx=5, pady=5
        )

        # Skapa en scrollbar för tabellen
        scrollbar = ttk.Scrollbar(self.raw_data_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        # Skapa Treeview
        columns = ("tid", "lat", "lon", "alt")
        self.raw_data_tree = ttk.Treeview(
            self.raw_data_frame,
            columns=columns,
            show="headings",
            yscrollcommand=scrollbar.set,
            height=5,
        )
        scrollbar.config(command=self.raw_data_tree.yview)

        # Definiera rubriker och bredd
        for col in columns:
            self.raw_data_tree.heading(col, text=col.capitalize())
            self.raw_data_tree.column(col, width=100)  # Justera bredd efter behov

        self.raw_data_tree.pack(side="left", fill="both", expand=True)

    def setup_buttons_tripinfo(self, parent_frame):
        """
        Bygger upp navigationskontroller och inmatningsfält.

        Denna metod skapar det interaktiva gränssnittet där användaren
        redigerar resans status och bläddrar i journalen.

        Args:
            parent: Den container (Frame) där kontrollerna ska sitta.

        Innehåll:
            - Navigationsknappar: "Föregående" och "Nästa" (anropar
              'show_prev'/'show_next').
            - Kategoriväljare: En Toggle-knapp för att växla mellan
              PRIVAT och TJÄNST.
            - Noteringsfält: En 'tk.Text'-ruta för fritext-anteckningar.
            - Statusindikator: Visar om ändringar har sparats.

        Teknisk detalj:
            Binder 'FocusOut' på textrutan till 'self.save()', vilket gör
            att ändringar sparas automatiskt så fort användaren lämnar fältet.
        """
        # Huvudcontainer för hela bottenraden
        button_row = ttk.Frame(parent_frame)
        button_row.pack(side="bottom", fill="x", pady=10, padx=5)

        # 1. Knappar-container (Ligger till vänster)
        buttons_frame = ttk.Frame(button_row)
        buttons_frame.pack(side="left", padx=5)

        self.btn_up = ttk.Button(
            buttons_frame, text="▲", width=3, command=self.show_prev
        )
        self.btn_up.pack(side="left", padx=(20, 2))

        ttk.Button(buttons_frame, text="Listvy", command=self.go_to_journal).pack(
            side="left", padx=2
        )

        self.btn_down = ttk.Button(
            buttons_frame, text="▼", width=3, command=self.show_next
        )
        self.btn_down.pack(side="left", padx=2)

        # 2. Info-container (Ligger till höger om knapparna)
        info_frame = ttk.Frame(button_row)
        info_frame.pack(side="left", fill="x", expand=True, padx=20)

        # Label "Rutt:"
        ttk.Label(info_frame, text="Rutt:").pack(side="left", padx=5)
        self.tripinfo_text = tk.Text(info_frame, height=2, width=40, bg="#f0f0f0")
        self.tripinfo_text.pack(side="left", fill="x", expand=True)
        self.tripinfo_text.config(state="disabled")  # Startar skrivskyddad

    def update_nav_buttons(self):
        """
        Uppdaterar tillståndet (av/på) för navigeringsknapparna i detaljvyn.

        Metoden frågar 'JournalTable' om det finns en resa före eller efter
        den nuvarande i den sorterade listan. Om man befinner sig vid
        listans slut eller början inaktiveras motsvarande knapp.

        Logik:
            1. Hämtar det aktuella ID:t för resan som visas.
            2. Anropar 'journal_table.get_prev_id' och 'get_next_id'.
            3. Om 'get_prev_id' returnerar None:
               - Sätter knappen "Föregående" till 'disabled'.
            4. Om 'get_next_id' returnerar None:
               - Sätter knappen "Nästa" till 'disabled'.
            5. Annars sätts knapparna till 'normal'.
        """
        # --- FIXEN HÄR: Kolla att knapparna faktiskt är skapade ---
        if self.btn_up is None or self.btn_down is None:
            return

        if not self.journal_table or not self.current_data:
            return

        current_id = str(self.current_data.get("temp_id", self.current_data.get("id")))

        # Kolla om vi kan gå bakåt eller framåt
        has_prev = self.journal_table.get_prev_id(current_id) is not None
        has_next = self.journal_table.get_next_id(current_id) is not None

        # Nu är det säkert att konfigurera
        self.btn_up.config(state="normal" if has_prev else "disabled")
        self.btn_down.config(state="normal" if has_next else "disabled")

    def load_trip(self, index):
        """
        Laddar in och visualiserar en specifik resa i detaljvyn.

        Detta är den centrala metoden som anropas när en användare väljer en resa
        (t.ex. via dubbelklick i JournalTable). Den nollställer den gamla vyn
        och populera alla komponenter med den nya resans data.

        Args:
            trip_data (dict): Den fullständiga datastrukturen för resan som ska visas.

        Processflöde:
            1. Tillståndshantering: Sparar 'trip_data' som 'self.current_data' och
               extraherar unikt ID.
            2. UI-återställning: Rensar kartmarkeringar, gamla rutter och tömmer
               koordinattabellen ('coord_tree').
            3. Datapopulering:
               - Uppdaterar textfält (noteringar, adresser, tider).
               - Uppdaterar telemetri-etiketter (SoC, mätarställning).
               - Fyller koordinattabellen med råa loggpunkter.
            4. Kartvisualisering: Anropar 'update_view' för att rita rutten
               och zooma in på rätt område.
            5. Navigation: Anropar 'update_nav_buttons' för att kontrollera
               om det finns resor före/efter i listan.
        """
        if 0 <= index < len(self.data_manager.trips):
            self.current_index = index
            trip = self.data_manager.trips[index]

            # Uppdatera din text-widget istället för en label som inte finns
            info_str = (
                f"Resa: {trip.get('id')} - {trip.get('Från')} till {trip.get('Till')}"
            )

            self.tripinfo_text.config(state="normal")
            self.tripinfo_text.delete("1.0", "end")
            self.tripinfo_text.insert("1.0", info_str)
            self.tripinfo_text.config(state="disabled")

            # Uppdatera även resten av vyn med den nya datan
            self.update_view(trip)

    def show_prev(self):
        """
        Byter vy till föregående resa i listan.

        Anropas när användaren klickar på 'Föregående'-knappen. Metoden
        frågar tabellen efter föregående ID och laddar sedan in den datan.

        Logik:
            1. Hämtar föregående ID via 'journal_table.get_prev_id'.
            2. Om ett ID finns:
               - Hämtar resans data med 'get_item_by_id'.
               - Anropar 'load_trip' för att uppdatera hela vyn.
            3. Om inget ID finns (början av listan) händer ingenting.
        """
        if not self.current_data or not self.journal_table:
            return

        # Hämta nuvarande ID
        current_id = str(self.current_data.get("temp_id", self.current_data.get("id")))

        # Hämta föregående ID från tabellen
        prev_id = self.journal_table.get_prev_id(current_id)

        if prev_id:
            # Be gui_handler att byta resa
            if self.parent_app:
                self.parent_app.visa_detaljer(prev_id)

            # Snyggt: Markera raden i tabellen också
            self.journal_table.tree.selection_set(prev_id)
            self.journal_table.tree.see(prev_id)

    def show_next(self):
        """
        Byter vy till nästa resa i listan.

        Anropas när användaren klickar på 'Nästa'-knappen. Fungerar precis
        som 'show_prev' men rör sig nedåt i tabellens sortering.
        """
        if not self.current_data or not self.journal_table:
            return

        current_id = str(self.current_data.get("temp_id", self.current_data.get("id")))

        # Hämta nästa ID från tabellen
        next_id = self.journal_table.get_next_id(current_id)

        if next_id:
            if self.parent_app:
                self.parent_app.visa_detaljer(next_id)

            self.journal_table.tree.selection_set(next_id)
            self.journal_table.tree.see(next_id)

    def go_to_journal(self):
        """
        Stänger detaljvyn och återgår till huvudlistan (JournalTable).

        Denna metod ser till att användaren hamnar på rätt ställe i
        huvudfönstret och att den senast visade resan blir markerad i tabellen.

        Process:
            1. Kontrollerar om en callback ('switch_to_journal_callback') är definierad.
            2. Om ja: Anropar den för att byta flik/vy i huvudapplikationen.
            3. Synkronisering: Anropar 'journal_table.select_row_by_id' så att
               tabellen automatiskt scrollar till och markerar den resa man
               just tittade på.
        """
        if self.current_data:
            # Hämta samma ID-logik som används i show_next
            # Vi prioriterar temp_id eftersom Treeview oftast använder det
            trip_id = str(self.current_data.get("temp_id", self.current_data.get("id")))

            if self.switch_to_journal_callback:
                self.switch_to_journal_callback(trip_id)

    def toggle_tjanst(self):
        """
        Växlar resans kategori mellan 'PRIVAT' och 'TJÄNST'.

        Metoden uppdaterar både det visuella tillståndet i knappen och
        resans underliggande data. Den triggar även ett automatiskt sparande.

        Logik:
            1. Läser av nuvarande text på knappen (via 'tjanst_var').
            2. Om texten är 'PRIVAT':
               - Ändrar till '☑ TJÄNST'.
               - Sätter en visuell flagga (t.ex. grön färg eller bock).
            3. Om texten är 'TJÄNST':
               - Ändrar tillbaka till '☐ PRIVAT'.
            4. Uppdaterar 'self.current_data' med det nya valet.
            5. Anropar 'self.save()' för att skriva ändringen till JSON-filen
               och uppdatera huvudtabellen omedelbart.
        """
        current = self.tjanst_var.get()

        # 1 & 2. Bestäm nytt läge
        if "TJÄNST" in current:
            new_label = "☐ PRIVAT"
            is_work = False
        else:
            new_label = "☑ TJÄNST"
            is_work = True

        # 3. Uppdatera GUI-variabeln (knappens text)
        self.tjanst_var.set(new_label)

        # 4. VIKTIGT: Uppdatera resans faktiska data i minnet innan save()
        if self.current_data:
            self.current_data["is_work_saved"] = is_work
            self.current_data["Tjänst"] = "TJÄNST" if is_work else "PRIVAT"

        # 5. Nu kan vi anropa save() – den kommer nu läsa de uppdaterade värdena
        self.save()

        # 6. Flagga som sparat i statusbaren
        if self.parent_app:
            self.parent_app.mark_journal_saved()

    def save_changes(self):
        """
        Hämtar ändringar från användargränssnittet och sparar dem i databasen/filen.

        Denna metod fungerar som en 'commit'-funktion. Den läser av textfälten
        och statusknapparna i DetailView, uppdaterar resans dataobjekt och
        tvingar DataManager att skriva ner ändringarna till JSON-filen.

        Logik:
            1. Validering: Kontrollerar att det finns en aktiv resa laddad ('current_data').
            2. Datainsamling:
               - Hämtar text från 'details_text' (noteringar).
               - Kontrollerar om 'TJÄNST' är valt i 'tjanst_var'.
            3. Objektuppdatering: Uppdaterar fälten 'desc_saved' och 'is_work_saved'
               i resans dictionary.
            4. Permanent lagring: Anropar 'data_manager.save_trip_data' som skriver
               ner hela den uppdaterade datastrukturen till disken.
            5. GUI-synkronisering: Uppdaterar raden i 'JournalTable' så att
               ändringen syns direkt i huvudlistan utan att användaren behöver
               ladda om programmet.
        """
        if not self.current_data:
            return

        # Uppdatera data-objektet
        self.current_data["desc_saved"] = self.details_text.get("1.0", "end-1c")
        self.current_data["Tjänst"] = self.tjanst_var.get()

        # Spara till JSON via DataManager
        self.data_manager.save_trip_data(
            self.current_data["map_image_path"], self.current_data
        )

        # Uppdatera tabell-vyn i körjournalen
        self.update_table_callback()
        logger.info(f"Resa {self.current_data.get('map_image_path')} sparad.")

        self.parent_app.mark_data_saved()
        self.parent_app.mark_journal_saved()

    def update_view(self, data):
        """
        Den centrala renderingsmotorn i DetailView.
        Aktualiserar kartan, ritar rutten och fyller alla informationsfält.

        Denna metod ansvarar för att synkronisera det visuella gränssnittet
        med den data som finns i 'trip_data'. Den hanterar allt från
        geografisk rittning till formatering av telemetri.

        Args:
            trip_data (dict): Den kompletta datastrukturen för resan.

        Huvudmoment:
            1. Rensning: Tar bort gamla rutter och markörer från kart-widgeten.
            2. Kartritning:
               - Extraherar 'path'-koordinater.
               - Ritar röd linje för rutt och blå för eventuella 'offline'-sträckor.
               - Placerar start- (A) och stopp-markörer (B).
            3. Auto-Zoom: Beräknar en bounding box och sätter kartans fokus
               så att hela resan syns perfekt.
            4. Telemetri-uppdatering: Formaterar och skriver ut värden för
               SoC, Odometer, hastighet och adresser i infopanelerna.
            5. Koordinatlistan: Tömmer och fyller 'coord_tree' med råladda punkter.
        """
        self.current_data = data

        # Snyggare header med den nya datan
        bil_info = f"{data.get('car_name', 'Okänd')} ({data.get('reg_nr', '-')})"
        källa_info = (
            f"{data.get('source_type', 'Okänd')} - {data.get('source_name', '-')}"
        )
        print(f"DEBUG: car_name är: {data.get('car_name')}")

        static_info = (
            f"START/SLUT: {data.get('Start')}  >>  {data.get('Slut')}\n"
            f"FRÅN: {data.get('Från')}\n"
            f"TILL: {data.get('Till')}\n"
            f"BIL: {bil_info}\n"
            f"KÄLLA: {källa_info}"
        )
        self.static_label.config(text=static_info)

        # Redigerbar info
        self.tjanst_var.set(data.get("Tjänst", "☐ PRIVAT"))
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", data.get("desc_saved", ""))

        # Label med resans information
        trip_info_str = f"Resa: {data.get('Start')} | ID: {data.get('id')}"

        self.tripinfo_text.config(state="normal")  # Gör den skrivbar
        self.tripinfo_text.delete("1.0", "end")  # Rensa gammal text
        self.tripinfo_text.insert("1.0", trip_info_str)  # Skriv in ny text
        self.tripinfo_text.config(state="disabled")  # Gör den skrivskyddad (valfritt)

        # 1. Uppdatera Kartan
        coords = data.get("route_coords", [])
        # Om det är v5-data (list av dicts), konvertera till list av lists för kartan
        map_points = []
        if coords and isinstance(coords[0], dict):
            map_points = [[p["lat"], p["lon"]] for p in coords]
        else:
            map_points = coords

        self.map_widget.delete_all_path()
        self.map_widget.delete_all_marker()
        if map_points:
            self.map_widget.set_path(map_points)
            # Zooma in (Bounding box logik...)
            lats = [p[0] for p in map_points]
            lons = [p[1] for p in map_points]
            self.map_widget.fit_bounding_box(
                (max(lats), min(lons)), (min(lats), max(lons))
            )

            # --- RITA AUTO-ZONER (Som ligger i närheten) ---
            config = self.data_manager.get_config()
            auto_zones = config.get("auto_zones", [])

            # Använd 'coords' (som du definierade högst upp i metoden)
            if auto_zones and coords:
                # Vi har redan räknat ut lats/lons för map_points tidigare i metoden,
                # så vi kan återanvända dem för att skapa vår marginal/box.
                margin = 0.05
                min_lat, max_lat = min(lats) - margin, max(lats) + margin
                min_lon, max_lon = min(lons) - margin, max(lons) + margin

                for zone in auto_zones:
                    z_lat = zone.get("lat")
                    z_lon = zone.get("lon")

                    # Kolla om zonen ligger inom vår uträknade box
                    if min_lat <= z_lat <= max_lat and min_lon <= z_lon <= max_lon:
                        # Rita Cirkel
                        z_rad = zone.get("radius", 200)
                        circle_path = self._get_circle_points(z_lat, z_lon, z_rad)
                        self.map_widget.set_path(circle_path, color="#a0c8e0", width=2)

                        # Rita Markör
                        self.map_widget.set_marker(z_lat, z_lon, text=zone.get("name"))

        # BRUTAL DEBUG:
        print(f"DEBUG: Typen på coords är: {type(coords)}")
        print(f"DEBUG: Längden på coords är: {len(coords)}")

        if len(coords) > 0:
            print(f"DEBUG: Första elementet i coords är: {coords[0]}")
            print(f"DEBUG: Typen på första elementet är: {type(coords[0])}")

        # 2. Fyll Koordinattabellen (v5 nyhet!)
        for item in self.coord_tree.get_children():
            self.coord_tree.delete(item)

        # Sätt färgen en gång för alla
        self.coord_tree.tag_configure("locked", foreground="#888888")

        if coords and isinstance(coords[0], dict):
            for p in coords:
                # Snygga till tiden
                tid = str(p.get("time", "")).replace("T", " ").replace("Z", "")[:19]

                self.coord_tree.insert(
                    "",
                    "end",
                    tags=("locked",),
                    values=(
                        tid,
                        f"{p.get('lat'):.5f}",
                        f"{p.get('lon'):.5f}",
                        p.get("speed", 0),
                        p.get("alt", 0),
                    ),
                )

        # 3. Uppdatera Telemetri-labels (Högerpanelen)
        self.update_telemetry_display(data)

        # VIKTIGT: Uppdatera status-indikatorn i detaljvyn
        is_work = data.get("is_work_saved", False)
        status_text = "☑ TJÄNST" if is_work else "☐ PRIVAT"
        self.tjanst_var.set(status_text)

        # Uppdatera även navigeringsknapparna (så de blir disabled vid start/slut)
        self.update_nav_buttons()

    def update_telemetry_display(self, data):
        """
        Uppdaterar alla värden i telemetripanelen med formaterad data.

        Metoden tar de råa värdena från 'trip_data' (ofta i meter, sekunder eller
        råa decimaler), konverterar dem till lämpliga enheter och skriver ut
        dem i de förberedda UI-etiketterna.

        Args:
            trip_data (dict): Resans dataobjekt innehållande telemetrifält.

        Logik och formatering:
            1. Distans: Konverterar meter till kilometer (m / 1000) med 1 decimal.
            2. SoC (Batteri): Hämtar start- och slutnivå och lägger till '%'.
            3. Odometer: Visar mätarställning i hela kilometer.
            4. Tid: Omvandlar sekunder till ett format som '22 min' eller '1h 5min'.
            5. Höjd: Visar höjdskillnad i meter (m) om GPS-data inkluderar höjd.

        Felhantering:
            Om ett värde saknas (None) i datan, skriver metoden ut '--'
            istället för att krascha, vilket ser snyggare ut i gränssnittet.
        """
        # Hjälpfunktion för att snygga till tider
        def format_dt(dt_str):
            if not dt_str:
                return "-"
            return str(dt_str).replace("T", " ").split(".")[0]

        # Uppdatera bas-info
        self.telemetry_labels["Start"].config(text=format_dt(data.get("Start")))
        self.telemetry_labels["Slut"].config(text=format_dt(data.get("Slut")))
        self.telemetry_labels["Km"].config(text=f"{data.get('Km', 0):.2f} km")

        # Försök hämta siffran duration_min, annars fallback till 0
        minuter = data.get("duration_min", 0)

        # Om duration_min inte fanns (t.ex. gammal data), försök konvertera Total_Tid
        # men bara om det råkar vara en siffra där
        if isinstance(minuter, str):
            try:
                # En nödlösning om det råkar ligga en sträng med bara siffror där
                minuter = int(minuter)
            except ValueError:
                minuter = 0

        if minuter >= 60:
            h = minuter // 60
            m = minuter % 60
            tids_str = f"{h}h {m}m"
        else:
            tids_str = f"{minuter} min"
        print(f"DEBUG: tids_str är: {tids_str}")
        self.telemetry_labels["Tid"].config(text=tids_str)

        avr_speed = data.get("Avg_Speed", 0)
        print(f"DEBUG: avr_speed är: {avr_speed}")
        self.telemetry_labels["Snitt"].config(text=f"{avr_speed:.1f} km/h")

        # Uppdatera Odometer (Traccar data vi la till i Step 1)
        odo_start = data.get("Start_Odo", 0)
        odo_end = data.get("End_Odo", 0)
        print(f"DEBUG: Hela data-objektet innehåller: {data.keys()}")
        print(f"DEBUG: Start_Odo är: {odo_start}")

        # Om ODO är i meter (Traccar), gör om till km
        if odo_start > 100000:  # Enkel check om det är meter
            self.telemetry_labels["Odo_Start"].config(text=f"{odo_start/1000:.1f} km")
            self.telemetry_labels["Odo_Slut"].config(text=f"{odo_end/1000:.1f} km")
        else:
            self.telemetry_labels["Odo_Start"].config(text=f"{odo_start:.2f} km")
            self.telemetry_labels["Odo_Slut"].config(text=f"{odo_end:.2f} km")

        # Uppdatera SoC
        has_soc = data.get("soc_start") is not None
        # Visa/dölj eller sätt text baserat på källan
        if has_soc:
            self.telemetry_labels["SoC_Start"].config(
                text=f"{data.get('soc_start', '-')}%"
            )
            self.telemetry_labels["SoC_Slut"].config(
                text=f"{data.get('soc_end', '-')}%"
            )
        else:
            # Om det är en Traccar-resa, dölj raderna eller sätt till "N/A"
            self.telemetry_labels["SoC_Start"].config(text="N/A")
            self.telemetry_labels["SoC_Slut"].config(text="N/A")

        # Uppdatera Radiobuttons
        self.tjanst_var.set(data.get("Tjänst", "PRIVAT"))

    def refresh_view(self):
        """
        Tvingar fram en total omritning av den nuvarande resan i detaljvyn.

        Till skillnad från 'load_trip' (som används vid byte av resa) används
        'refresh_view' för att uppdatera visningen av den resa som redan är
        laddad. Detta är användbart om t.ex. koordinater har hämtats klart
        i bakgrunden eller om adresser har blivit tillgängliga.

        Logik:
            1. Kontrollerar om det finns en aktiv resa i 'self.current_data'.
            2. Om data finns, anropas 'update_view' med den nuvarande resans data.
            3. Detta triggar omritning av kartan, uppdatering av telemetri
               och återställning av tabellen med rådata.
        """
        if not self.current_data:
            return

        # Hitta den senaste versionen av resan i DataManager baserat på bild-vägen
        current_path = self.current_data.get("map_image_path")
        updated_trip = next(
            (
                t
                for t in self.data_manager.trips
                if t.get("map_image_path") == current_path
            ),
            None,
        )

        if updated_trip:
            self.current_data = updated_trip
            # Uppdatera GUI-elementen
            self.details_text.delete("1.0", tk.END)
            self.details_text.insert("1.0", self.current_data.get("desc_saved", ""))

            # Uppdatera även Tjänst/Privat-checkboxen/knappen
            status = (
                "☑ TJÄNST" if self.current_data.get("is_work_saved") else "☐ PRIVAT"
            )
            self.tjanst_var.set(status)

            # Uppdatera headern
            self.update_view(self.current_data)

    def _get_circle_points(self, lat, lon, radius_m):
        """
        Genererar en lista med GPS-koordinater som bildar en cirkel.

        Används för att rita ut zoner eller markörer på kartan med en fast
        radie i meter, oavsett kartans zoomnivå.

        Args:
            lat (float): Latitud för cirkelns centrum.
            lon (float): Longitud för cirkelns centrum.
            radius_meters (float): Cirkelns radie i meter.
            num_points (int): Antal punkter som ska bilda cirkeln (högre = rundare).

        Returns:
            list[tuple]: En lista med (lat, lon)-tupler som kan skickas till
                         map_widget.set_path för att rita cirkeln.

        Matematisk logik:
            1. Omvandlar meter till grader (approximativt då jorden inte är platt).
            2. Använder trigonometri (sinus/cosinus) för att räkna ut
               punkter längs omkretsen.
            3. Justerar för longitud-kompression baserat på latituden
               (eftersom avståndet mellan longituder minskar ju närmare polerna man kommer).
        """
        import math

        points = []
        lat_step = radius_m / 111111.0
        lon_step = radius_m / (111111.0 * math.cos(math.radians(lat)))
        for i in range(37):
            angle = math.radians(i * 10)
            p_lat = lat + lat_step * math.cos(angle)
            p_lon = lon + lon_step * math.sin(angle)
            points.append((p_lat, p_lon))
        return points

    def save(self):
        """
        Den centrala 'Commit'-metoden som sparar ändringar från GUI till fil.

        Metoden läser av de interaktiva fälten i DetailView (textrutor och knappar),
        uppdaterar resans dataobjekt och instruerar DataManager att utföra
        en permanent lagring på hårddisken.

        Process:
            1. Kontroll: Verifierar att det faktiskt finns en aktiv resa laddad
               ('self.current_data').
            2. Datainsamling:
               - Hämtar text från 'details_text' (noteringen).
               - Kontrollerar om 'TJÄNST' är valt i 'tjanst_var'.
            3. Objektuppdatering: Sparar de nya värdena i 'current_data' under
               nycklarna 'desc_saved', 'is_work_saved' och 'Tjänst'.
            4. Lagring: Anropar 'data_manager.save_trip_data' för att skriva
               till JSON-filen.
            5. Realtidssynk: Uppdaterar omedelbart motsvarande rad i
               'JournalTable' så att ändringen syns i huvudlistan utan omstart.

        Returns:
            bool: True om sparandet lyckades, annars False.
        """
        if not self.current_data:
            return

        # 1. Hämta värden från GUI:t
        note = self.details_text.get("1.0", "end-1c")
        is_work = "TJÄNST" in self.tjanst_var.get()

        # 2. Uppdatera objektet
        self.current_data["desc_saved"] = note
        self.current_data["is_work_saved"] = is_work
        self.current_data["Tjänst"] = "TJÄNST" if is_work else "PRIVAT"

        # Punkt 3: Autospara till disk ENDAST om en fil är aktiv
        if getattr(self.data_manager, 'current_json_path', None):
            success = self.data_manager.save_trip_data(
                self.current_data.get("map_image_path"), self.current_data
            )
            if success:
                logger.debug("Autosave utförd till aktiv fil.")
        else:
            logger.debug("Ingen aktiv fil - ändring sparad endast i minnet.")

        # 4. Uppdatera tabellen i GUI:t så det syns där direkt
        if success and self.journal_table:
            # Vi hämtar det ID som tabellen faktiskt använder just nu
            gui_id = self.current_data.get("temp_id") or self.current_data.get("id")
            if self.journal_table.tree.exists(str(gui_id)):
                status_text = "☑ TJÄNST" if is_work else "☐ PRIVAT"
                self.journal_table.tree.set(str(gui_id), "typ", status_text)
                self.journal_table.tree.set(str(gui_id), "notering", note)

        # messagebox.showinfo("Sparat", "Ändringar sparade i minne och fil!")
        print(
            f"DEBUG SAVE: Ändringarna har sparats i minnet. Glöm inte att spara filen!"
        )
