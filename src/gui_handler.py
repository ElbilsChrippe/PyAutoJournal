import tkinter as tk
import tkintermapview
import os
import sys
import webbrowser
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import platform
import threading
import queue
from datetime import datetime

# Importer från src-paketet
from src.data_manager import DataManager
from src.journal_table import JournalTable
from src.detail_view import DetailView
from src.auto_category_window import AutoCategoryWindow
from src.logger_setup import get_logger
from src.version import __version__

# Initiera loggern för den här filen
logger = get_logger(__name__)

def resource_path(relative_path):
    """
    Hämtar absolut sökväg till resurser.
    Fungerar både vid 'python main.py' (dev) och PyInstaller (binär).
    """
    try:
        # PyInstaller skapar en temporär mapp (_MEIPASS) när binären körs
        base_path = sys._MEIPASS
    except AttributeError:
        # Om vi kör lokalt i utvecklingsmiljön
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class PyAutoJournal:
    """
    Huvudklassen för PyAutoJournals användargränssnitt.

    Hanterar fönstret, flikar, användarinteraktion och koordinering
    mellan tabellvyn och databashanteraren.

    Attributes:
        root: Tkinter rot-objektet.
        data_manager: Instans av DataManager för datahantering.
    """

    def __init__(self, root):
        """
        Initierar applikationen och bygger upp GUI:t.

        Args:
            root (tk.Tk): Huvudfönstret för applikationen.
        """
        self.root = root
        # --- Övervakning av osparat arbete ---
        self.config_unsaved = False

        self.root.title(f"PyAutoJournal v{__version__}")
        self.root.geometry("1600x900")
        self.source_var = tk.StringVar(value="Traccar")

        # Fånga krysset (X) i huvudfönstret
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.data_manager = DataManager()
        self.current_json_path = None
        self.current_source = {"type": "data"}
        self.data_context = None
        self.fetch_queue = queue.Queue()

        self._build_main_layout()

        self.load_config_to_fields()
        self.update_status_display()

    def _build_main_layout(self):
        """
        Initierar applikationens huvudlayout och visuella hierarki.

        Metoden ansvarar för att:
        1. Reservera plats för statusraden i botten av fönstret.
        2. Skapa den centrala Notebook-behållaren för fliknavigering.
        3. Koordinera uppbyggnaden av innehållet i varje specifik flik.
        4. Initiera DetailView-modulen för karta och trippdetaljer.

        Viktigt: Statusraden packas först för att säkerställa att den
        alltid är synlig och inte döljs när Notebooken expanderar.
        """
        # 1. Skapa statusbar
        self._setup_status_bar()

        # 2. Skapa Notebooken SEDAN (den tar upp RESTEN av platsen)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self._setup_tabs()

        # Anropa setup
        self.setup_config_ui()
        self.setup_journal_ui()
        self.setup_export_ui()
        self.setup_about_ui()

        # Initiera DetailView
        self.paned = ttk.PanedWindow(self.tab_details, orient="horizontal")
        self.paned.pack(fill="both", expand=True)
        self.detail_view = DetailView(
            self.paned,
            self.data_manager,
            self.refresh_journal_table,
            self.notebook,
            self.table,
            switch_to_journal_callback=self.show_table_tab,
            parent_app=self,
        )

        self.refresh_source_tree()
        self.update_settings_zones_preview()

    def _setup_tabs(self):
        """
        Initierar och organiserar applikationens fliksystem (Notebook).

        Metoden skapar de individuella flik-ramarna och lägger till dem i
        huvudbehållaren (self.notebook). Varje flik tilldelas en unik ram
        som sedan används som förälder (parent) för respektive moduls GUI.

        Flikar som skapas:
            - tab_config: För systeminställningar och datakällor.
            - tab_journal: För huvudtabellen med körningar.
            - tab_details: För detaljerad info och kartvy (använder PanedWindow).
            - tab_export: För generering av PDF-rapporter.
            - tab_about: För versionsinformation och dokumentation.

        Notera: Ordningen i denna metod styr i vilken ordning flikarna visas
        för användaren från vänster till höger.
        """
        self.tab_journal = ttk.Frame(self.notebook)
        self.tab_map = ttk.Frame(self.notebook)
        self.tab_details = ttk.Frame(self.notebook)
        self.tab_export = ttk.Frame(self.notebook)
        self.tab_config = ttk.Frame(self.notebook)
        self.tab_about = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_journal, text="Körjournal")
        self.notebook.add(self.tab_details, text="Ruttöversikt")
        self.notebook.add(self.tab_export, text="Förhandsgranska & Export")

        self.notebook.add(self.tab_config, text="Inställningar")
        self.notebook.add(self.tab_about, text=" Om ")

        # Lyssna på när användaren byter flik
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def refresh_journal_table(self):
        """
        Hämtar kördata från vald källa och uppdaterar huvudtabellen.

        Metoden utför följande steg:
        1. Identifierar vald datakälla (Traccar, TeslaMate eller JSON).
        2. Rensar befintliga rader i tabellvyn (self.table).
        3. Anropar DataManager för att hämta resor baserat på valda filter 
           (tidsperiod och fordon).
        4. Formaterar rådata till läsbara rader och fyller tabellen.
        5. Uppdaterar statusraden med information om antal hämtade resor.

        Används både vid programmets start och när användaren manuellt 
        klickar på "Uppdatera" eller byter filter.

        Exceptions:
            Loggar fel via systemets logger om anslutning till källa misslyckas,
            och visar ett felmeddelande för användaren via messagebox.
        """
        # Hämta den senaste datan från DataManager (den är alltid uppdaterad i minnet)
        trips_in_memory = self.data_manager.trips
        logger.debug(
            f"DEBUG: Antal resor i minnet innan omritning: {len(trips_in_memory)}"
        )

        # Om vi har en fil, ladda om från disk för att vara säkra
        if self.data_manager.current_json_path:
            logger.info("Laddar om från fil för att säkerställa synk...")
            trips_in_memory = self.data_manager.load_from_file(
                self.data_manager.current_json_path
            )
        else:
            logger.info(
                "Ingen fil vald, ritar om tabellen baserat på minnesdata (API-läge)."
            )

        # Uppdatera tabellen oavsett om datan kom från disk eller minne
        self.table.refresh_data(trips_in_memory)

    def show_table_tab(self, trip_id=None):
        """
        Växlar användargränssnittet till fliken för körjournalstabellen.

        Denna metod används ofta som en callback från andra vyer (t.ex. DetailView)
        för att navigera användaren tillbaka till huvudlistan med resor efter att
        en redigering eller granskning avslutats.

        Note:
            Metoden förutsätter att 'notebook' (ttk.Notebook) har initierats och
            att fliken på index 0 är tabellvyn.
        """
        self.notebook.select(self.tab_journal)

        if trip_id:
            # Eftersom vi redan har ID:t, skicka det direkt!
            # Vi behöver ingen trip_data här.
            self.root.after(100, lambda: self._delayed_focus(trip_id))

    def _delayed_focus(self, trip_id):
        """
        Sätter fokus och markerar en specifik resa i journaltabellen.

        Metoden letar upp en rad i Treeview-komponenten baserat på dess unika
        ID, rullar vyn så att raden blir synlig och sätter den som aktiv markering.
        Om ID:t inte hittas loggas felsökningsinformation om tabellens nuvarande tillstånd.

        Args:
            trip_id (str|int): Det unika ID (UUID eller databas-ID) för den resa
                som ska fokuseras i tabellen.

        Returns:
            None

        Note:
            Denna metod anropas ofta via `after()` i Tkinter för att säkerställ
            att tabellen hunnit populeras med data innan fokusering sker.
        """
        target_id = str(trip_id)

        # 1. Kolla om raden finns i trädet
        if self.table.tree.exists(target_id):
            self.table.tree.selection_set(target_id)
            self.table.tree.see(target_id)
            self.table.tree.focus(target_id)
        else:
            # 2. Om vi inte hittar det, logga för felsökning
            children = self.table.tree.get_children()
            logger.warning(
                f"JournalTable: Hittade inte trip_id {target_id}. "
                f"Antal rader i tabellen: {len(children)}. "
                f"Första 3 ID:n: {children[:3]}"
            )

    def setup_journal_ui(self):
        """
        Skapar och konfigurerar användargränssnittet för körjournalstabellen.

        Metoden initierar en `ttk.Treeview` med definierade kolumner för resedata
        (start/slut-tid, adress, distans, typ och noteringar). Den konfigurerar
        även stilmallen för radhöjd och händelsehanterare för att visa
        resedetaljer vid klick.

        Returns:
            None

        Note:
            Skapar även en 'Journal.Treeview'-stil som kräver att TTK-temat
            stödjer `rowheight`.
        """
        # Toolbar
        tools = ttk.LabelFrame(
            self.tab_journal, text=" Kontroller & Filter ", padding=5
        )
        tools.pack(fill="x", padx=10, pady=5)

        # Datumväljare (vänster sida) - denna skapar sina egna fält i 'tools'
        self.setup_date_filters(tools)

        # Separator efter datum
        ttk.Separator(tools, orient="vertical").pack(side="left", padx=10, fill="y")

        # Bilväljare (nu kopplad till 'tools' istället för 'filter_frame')
        ttk.Label(tools, text="Bil:").pack(side="left", padx=5)
        self.car_selector_var = tk.StringVar()
        self.car_selector = ttk.Combobox(
            tools, textvariable=self.car_selector_var, state="readonly", width=20
        )
        self.car_selector.pack(side="left", padx=5)

        # Fyll dropdownen med bilar från config
        self.refresh_car_selector()

        # Separator efter bilväljaren
        ttk.Separator(tools, orient="vertical").pack(side="left", padx=10, fill="y")

        # Funktionsknappar
        self.auto_tag_var = tk.BooleanVar(value=True)
        self.autotag_cb = ttk.Checkbutton(
            tools, text="Auto-kategorisera", variable=self.auto_tag_var
        )
        self.autotag_cb.pack(side="left", padx=5)
        ttk.Button(tools, text="🌐 Hämta API", command=self.on_fetch_api).pack(
            side="left", padx=5
        )
        ttk.Button(tools, text="📂 Öppna", command=self.on_load_file).pack(
            side="left", padx=5
        )
        ttk.Button(tools, text="💾 Spara", command=self.on_save_as_file).pack(
            side="left", padx=5
        )

        # EXPORT-KNAPPEN (längst till höger)
        self.btn_prepare_export = ttk.Button(
            tools, text="Granska & Export ➔", command=self.prepare_export_view
        )
        self.btn_prepare_export.pack(side="right", padx=10)

        # Vald källa och bil info
        source_info = ttk.LabelFrame(self.tab_journal, text=" Vald bil ", padding=5)
        source_info.pack(fill="x", padx=10, pady=5)

        #self.setup_source_info(source_info)
        self.setup_source_info(source_info)

        # Separator efter källinfo
        ttk.Separator(tools, orient="vertical").pack(side="left", padx=10, fill="y")

        # Initiera JournalTable och koppla ihop callbacken
        self.table = JournalTable(
            self.tab_journal,
            raw_callback=self.visa_detaljer,
            data_manager=self.data_manager,
            get_source_callback=lambda: self.active_source.get()
            .replace("Källa: ", "")
            .strip(),
            parent_app=self,
        )

    def setup_config_ui(self):
        """
        Bygger och konfigurerar användargränssnittet för applikationens inställningar.

        Denna metod skapar ett formulär där användaren kan mata in API-nycklar,
        databassökvägar och företagsinformation. Den kopplar även inmatningsfält
        till respektive variabler och skapar spar-knappar som anropar
        DataManager för att lagra ändringar permanent i konfigurationsfilen.

        Returns:
            None

        Note:
            Layouten använder ett `ttk.Notebook` eller en separat frame. Ändringar
            i formuläret sparas inte automatiskt; användaren måste explicit
            trycka på en spar-knapp för att uppdatera `config.json`.
        """
        main_frame = ttk.Frame(self.tab_config)
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # --- RAD 1: DATAKÄLLOR OCH FORDON (Sida vid sida) ---
        row1_frame = ttk.Frame(main_frame)
        row1_frame.pack(fill="x", pady=5)

        # SEKTION 1: DATAKÄLLOR (Vänster)
        source_labelframe = ttk.LabelFrame(
            row1_frame,
            text=" 1. Datakällor (Traccar Servrar / TeslaMate DBs) ",
            padding=10,
        )
        source_labelframe.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.source_tree = ttk.Treeview(
            source_labelframe,
            columns=("name", "type", "address"),
            show="headings",
            height=5,
        )
        self.source_tree.heading("name", text="Namn")
        self.source_tree.heading("type", text="Typ")
        self.source_tree.heading("address", text="Adress/Host")
        self.source_tree.column("name", width=100)
        self.source_tree.column("type", width=80)
        self.source_tree.column("address", width=180)
        self.source_tree.pack(fill="both", side="left", expand=True)

        source_btns = ttk.Frame(source_labelframe)
        source_btns.pack(side="right", padx=5)
        ttk.Button(source_btns, text="＋", width=3, command=self.add_source_popup).pack(
            pady=2
        )
        ttk.Button(source_btns, text="✎", width=3, command=self.edit_source_popup).pack(
            pady=2
        )
        ttk.Button(source_btns, text="－", width=3, command=self.delete_source).pack(
            pady=2
        )

        # SEKTION 2: FORDON (Höger)
        car_labelframe = ttk.LabelFrame(
            row1_frame, text=" 2. Dina Fordon (Kopplade till källor) ", padding=10
        )
        car_labelframe.pack(side="left", fill="both", expand=True, padx=(5, 0))

        self.car_tree = ttk.Treeview(
            car_labelframe,
            columns=("reg", "model", "source", "id"),
            show="headings",
            height=5,
        )
        self.car_tree.heading("reg", text="Reg-nr")
        self.car_tree.heading("model", text="Modell")
        self.car_tree.heading("source", text="Källa")
        self.car_tree.heading("id", text="ID")
        self.car_tree.column("reg", width=80)
        self.car_tree.column("model", width=120)
        self.car_tree.column("source", width=100)
        self.car_tree.column("id", width=40)
        self.car_tree.pack(fill="both", side="left", expand=True)

        car_btns = ttk.Frame(car_labelframe)
        car_btns.pack(side="right", padx=5)
        ttk.Button(car_btns, text="＋", width=3, command=self.add_car_popup).pack(
            pady=2
        )
        ttk.Button(car_btns, text="✎", width=3, command=self.edit_car_popup).pack(
            pady=2
        )
        ttk.Button(car_btns, text="－", width=3, command=self.delete_car).pack(pady=2)

        # --- SEKTION 3: AUTO-KATEGORISERING (Ny ram till höger) ---
        auto_cat_settings_frame = ttk.LabelFrame(
            row1_frame, text=" 3. Auto-Kategorisering ", padding=15
        )
        auto_cat_settings_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))

        # Beskrivning
        ttk.Label(
            auto_cat_settings_frame,
            text="Aktiva zoner för automatisk kategorisering:",
            font=("Arial", 9),
        ).pack(anchor="w")

        # En liten förhandstitt på zoner (Tabell)
        # Vi gör den ganska låg (height=4) så den inte tar för mycket plats på höjden
        columns = ("namn", "kat")
        self.zones_mini_tree = ttk.Treeview(
            auto_cat_settings_frame, columns=columns, show="headings", height=4
        )
        self.zones_mini_tree.heading("namn", text="Zonnamn")
        self.zones_mini_tree.heading("kat", text="Kategori")
        self.zones_mini_tree.column("namn", width=120)
        self.zones_mini_tree.column("kat", width=80)
        self.zones_mini_tree.pack(fill="both", expand=True, pady=10)

        # Bind klick för minitabellen
        self.zones_mini_tree.bind("<Button-1>", self.on_mini_tree_click)
        self.zones_mini_tree.bind("<Double-1>", self.on_mini_tree_double_click)

        # Knapp för att öppna det stora fönstret
        ttk.Button(
            auto_cat_settings_frame,
            text="⚙️ Öppna zonhanterare & karta",
            command=self.open_auto_category_window,
        ).pack(fill="x")

        # --- RAD 2: EXTERNA TJÄNSTER OCH RAPPORTINSTÄLLNINGAR (Sida vid sida) ---
        row2_frame = ttk.Frame(main_frame)
        row2_frame.pack(fill="x", pady=5)

        # SEKTION 4: EXTERNA TJÄNSTER (Vänster)
        api_frame = ttk.LabelFrame(row2_frame, text=" 4. Externa Tjänster ", padding=15)
        api_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        ttk.Label(api_frame, text="Geoapify API-nyckel:").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.entry_geoapify = ttk.Entry(api_frame, width=35, show="*")
        self.entry_geoapify.grid(row=0, column=1, sticky="w", padx=10, pady=2)
        ttk.Label(
            api_frame,
            text="(Används för snabb adresshämtning)",
            font=("Arial", 8, "italic"),
        ).grid(row=1, column=1, sticky="w", padx=10)

        # SEKTION 5: RAPPORT- & SYSTEMINSTÄLLNINGAR (Höger)
        report_frame = ttk.LabelFrame(
            row2_frame, text=" 5. Rapport- & Systeminställningar ", padding=15
        )
        report_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))

        # Rad 0: Företagsnamn
        ttk.Label(report_frame, text="Företagsnamn:").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.entry_company = ttk.Entry(report_frame, width=30)
        self.entry_company.grid(row=0, column=1, sticky="w", padx=10, pady=2)
        self.entry_company.bind("<KeyRelease>", self.mark_config_dirty)

        # Rad 1: Org.nr
        ttk.Label(report_frame, text="Org.nr:").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.entry_org = ttk.Entry(report_frame, width=30)
        self.entry_org.grid(row=1, column=1, sticky="w", padx=10, pady=2)
        self.entry_org.bind("<KeyRelease>", self.mark_config_dirty)

        # Rad 2: Logotyp
        ttk.Label(report_frame, text="Logotyp (.png):").grid(
            row=2, column=0, sticky="w", pady=2
        )
        logo_sub = ttk.Frame(report_frame)
        logo_sub.grid(row=2, column=1, sticky="ew", padx=10, pady=2)
        self.entry_logo = ttk.Entry(logo_sub, width=22)
        self.entry_logo.pack(side="left", fill="x", expand=True)
        ttk.Button(logo_sub, text="...", width=3, command=self.on_browse_logo).pack(
            side="right", padx=2
        )
        # Rad 3: Loggnivå (NYTT)
        ttk.Separator(report_frame, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=10
        )

        ttk.Label(report_frame, text="Systemets Loggnivå:").grid(
            row=4, column=0, sticky="w", pady=2
        )
        self.log_level_var = tk.StringVar()
        self.log_level_combo = ttk.Combobox(
            report_frame,
            textvariable=self.log_level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            state="readonly",
            width=28
        )
        self.log_level_combo.grid(row=4, column=1, sticky="w", padx=10, pady=2)
        self.log_level_combo.bind("<<ComboboxSelected>>", self.mark_config_dirty)
        ttk.Label(
            report_frame,
            text="(DEBUG ger mest detaljer, ERROR ger minst)",
            font=("Arial", 8, "italic")
        ).grid(row=5, column=1, sticky="w", padx=10)

        # Rad 3: FÖRHANDSGRANSKNING (Nu i kolumn 2 av report_frame) ---

        # Vi lägger till en vertikal separator för att det ska se snyggt ut
        ttk.Separator(report_frame, orient="vertical").grid(
            row=0, column=2, rowspan=6, sticky="ns", padx=15
        )

        preview_inner_frame = ttk.LabelFrame(
            report_frame, text=" Förhandsgranskning Logotyp ", padding=10
        )
        # Vi placerar den i kolumn 3 (efter separatorn) och låter den spänna över alla rader
        preview_inner_frame.grid(row=0, column=3, rowspan=6, sticky="nsew", padx=5, pady=5)

        # Viktigt: Behåll det namn som din update_logo_preview använder (t.ex. self.logo_preview eller self.logo_image_label)
        self.logo_preview = ttk.Label(preview_inner_frame, text="[ Ingen bild laddad ]")
        self.logo_preview.pack(expand=True)

        # Denna rad ser till att kolumnen med bilden kan växa om det behövs
        report_frame.columnconfigure(3, weight=1)

        # SPARA-KNAPP
        ttk.Button(
            main_frame,
            text="💾 Spara alla inställningar",
            command=self.save_config_fields,
        ).pack(side="bottom", pady=10)

    def setup_details_ui(self):
        """
        Bygger och konfigurerar användargränssnittet för resedetaljer.

        Denna metod skapar layouten för att visa fördjupad information om en
        vald resa, inklusive kartvisualisering, start- och slutpunkt samt
        möjlighet att ändra kategori och lägga till anteckningar. Den initierar
        även de widgets som krävs för att rendera ruttdata och SoC-grafer
        (för Tesla-resor).

        Returns:
            None

        Note:
            Denna vy uppdateras dynamiskt när användaren väljer en ny resa i
            huvudtabellen. Metoden förutsätter att nödvändiga kart-bibliotek
            finns tillgängliga.
        """
        self.detailed_text = scrolledtext.ScrolledText(
            self.tab_details, font=("Courier", 10)
        )
        self.detailed_text.pack(fill="both", expand=True, padx=10, pady=10)

    def setup_export_ui(self):
        """
        Bygger och konfigurerar användargränssnittet för export-fliken.

        Metoden skapar kontrollpaneler där användaren kan välja tidsperiod,
        filtrera resor och initiera export till PDF eller HTML-format. Den
        konfigurerar även knappar för att spara rapporter lokalt och ställa in
        metadata som krävs för en professionell körjournal (t.ex. fordonets
        registreringsnummer och företagsinformation).

        Returns:
            None

        Note:
            Exporten förlitar sig på externa beroenden som `pdfkit` för
            PDF-konvertering. Om dessa bibliotek saknas i miljön kommer
            export-knapparna vara inaktiverade eller dolda.
        """
        frame = ttk.Frame(self.tab_export, padding=30)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Generera Rapport", font=("Arial", 16, "bold")).pack(
            pady=(0, 20)
        )

        # Rapportinfo / Sammanfattning
        info_box = ttk.LabelFrame(
            frame, text=" Rapportdetaljer & Sammanfattning ", padding=15
        )
        info_box.pack(fill="x", pady=10)

        # Dessa två labels är de som prepare_export_view kommer att uppdatera
        self.lbl_export_car = ttk.Label(
            info_box, text="Fordon: -", font=("Arial", 10, "bold")
        )
        self.lbl_export_car.pack(anchor="w", pady=2)

        self.lbl_export_stats = ttk.Label(
            info_box, text="Ingen data laddad. Gå till Körjournal och hämta data först."
        )
        self.lbl_export_stats.pack(anchor="w", pady=2)

        # Inställningar
        opt_frame = ttk.LabelFrame(frame, text=" Inställningar ", padding=15)
        opt_frame.pack(fill="x", pady=10)

        self.export_only_work = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_frame,
            text="Exportera endast tjänsteresor",
            variable=self.export_only_work,
        ).pack(anchor="w")

        # Knappar
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=30)

        ttk.Button(
            btn_frame,
            text="🔍 Förhandsgranska i Webbläsare",
            width=30,
            command=self.on_preview_html,
        ).pack(pady=5)
        ttk.Button(
            btn_frame, text="💾 Spara som färdig HTML", width=30, command=self.on_export
        ).pack(pady=5)
        ttk.Button(
            btn_frame, text="📄 Spara som PDF", width=30, command=self.on_export_pdf
        ).pack(pady=5)

    def setup_about_ui(self):
        """
        Bygger och konfigurerar användargränssnittet för 'Om'-fliken.

        Metoden skapar en informationsvy som visar applikationens namn, version,
        upphovsrättsinformation samt renderar projektets logotyp. Den ansvarar för
        att ladda bilden från 'assets/'-katalogen och skala om den till en
        passande storlek för vyn.

        Returns:
            None

        Note:
            Om bildfilen inte hittas loggas en varning, men användargränssnittet
            fortsätter att rendera textinformationen utan avbrott.
        """
        # Centrerad ram för innehållet
        frame = ttk.Frame(self.tab_about)
        frame.place(relx=0.5, rely=0.4, anchor="center")

        # --- LOGO / ICON ---
        # Vi använder resource_path för att hitta bilden oavsett om vi kör script eller binär
        logo_path = resource_path(os.path.join("assets", "logo.png"))

        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                pil_img = Image.open(logo_path)
                pil_img = pil_img.resize((120, 120), Image.Resampling.LANCZOS)

                # Spara referensen i self för att undvika Garbage Collection
                self.about_logo = ImageTk.PhotoImage(pil_img)

                logo_label = ttk.Label(frame, image=self.about_logo)
                logo_label.pack(pady=10)
            except Exception as e:
                logger.error(f"Kunde inte ladda logon: {e}")
        else:
            # Nu kommer loggen visa den temporära sökvägen om vi kör som binär
            logger.warning(f"Logon hittades inte på: {logo_path}")

        # Titel
        title_lbl = ttk.Label(
            frame, text="Traccar Journal Enterprise", font=("Arial", 18, "bold")
        )
        title_lbl.pack(pady=10)

        version_text = f"Version {__version__}"
        version_lbl = ttk.Label(
            frame, text=version_text, font=("Arial", 10, "italic")
        )
        version_lbl.pack(pady=2)

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=15)

        # Info-box
        info_text = (
            "Detta verktyg är utvecklat för att automatisera körjournaler\n"
            "genom att hämta ruttdata direkt från en Traccar-server.\n\n"
            "Funktioner i V2:\n"
            "• Modulär Python-arkitektur\n"
            "• Live-uppdatering av adresser (Nominatim)\n"
            "• Interaktiv kartvy med rutt-centrering\n"
            "• Rådata-inspektion (JSON)\n"
            "• Export till Skatteverks-kompatibel CSV"
        )

        details = ttk.Label(frame, text=info_text, justify="center", font=("Arial", 10))
        details.pack(pady=10)

        # Credits / Systeminfo
        credits_frame = ttk.LabelFrame(frame, text=" Systemstatus ")
        credits_frame.pack(pady=20, fill="x")

        sys_info = f"System: {platform.system()} {platform.release()}\nPython: {platform.python_version()}"
        ttk.Label(credits_frame, text=sys_info, font=("Courier New", 8)).pack(
            padx=10, pady=5
        )

        footer = ttk.Label(
            frame, text="© 2026 Christer @ Ubuntu-NAS", foreground="gray"
        )
        footer.pack(pady=20)

    def _setup_status_bar(self):
        """
        Initierar och konfigurerar applikationens statusrad.

        Metoden ansvarar för:
        1. Definition av ttk-stilar (Info, Working, Status, Warning) för
           enhetlig färgkodning och typografi i hela GUI:t.
        2. Uppbyggnad av layouten med fyra dedikerade fält:
           - Fält 1: Allmän systeminformation och processmeddelanden.
           - Fält 2: Konfigurationsstatus (visar om inställningar är sparade).
           - Fält 3: Datastatus (visar aktiv fil eller sessionsstatus).
           - Fält 4: Progressbar för långvariga nätverks- eller filoperationer.
        3. Konfigurering av grid-vikter för att säkerställa att statusraden
           expanderar korrekt när fönstret ändrar storlek.
        """
        style = ttk.Style()

        # 1. Neutral stil (Standardtext)
        style.configure("Info.TLabel", foreground="#333333", font=("Arial", 9))

        # 2. Arbets-stil (Kursiv text istället för skrikiga färger för att visa aktivitet)
        style.configure("Working.TLabel", foreground="#333333", font=("Arial", 9, "italic"))

        # 3. Status OK (Mörkgrön, mjukare för ögonen)
        style.configure("Status.TLabel", foreground="#107C10", font=("Arial", 9))

        # 4. Varning/Osparat (Mörkröd och fet)
        style.configure("Warning.TLabel", foreground="#A80000", font=("Arial", 9, "bold"))

        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(side="bottom", fill="x", padx=5, pady=2)

        # 1. INFO/API STATUS (Vänster - Fast bredd för korta systemmeddelanden)
        self.status_label = ttk.Label(
            self.status_frame, text="Systemet redo", relief="sunken", anchor="w", width=20
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        # 2. KONFIG STATUS (Fast bredd så texten inte hoppar)
        self.config_status_label = ttk.Label(
            self.status_frame, text="Konfig OK ✅", relief="sunken", anchor="center", width=25
        )
        self.config_status_label.grid(row=0, column=1, sticky="ew", padx=2)

        # 3. DATA / JSON FIL STATUS (Expanderande - tar upp resten av platsen)
        self.data_status_label = ttk.Label(
            self.status_frame, text="Ingen data laddad 📥", relief="sunken", anchor="w", width=40
        )
        self.data_status_label.grid(row=0, column=2, sticky="new", padx=2)

        # 4. PROGRESSBAR (Längst till höger)
        # ÄNDRAT: Bytt namn från self.progress till self.progress_bar
        self.progress_bar = ttk.Progressbar(
            self.status_frame, orient="horizontal", mode="determinate", length=150
        )
        self.progress_bar.grid(row=0, column=3, sticky="e", padx=(2, 0))

        # Inställning för expansion:
        self.status_frame.columnconfigure(2, weight=1)

    def update_status_display(self, info_msg=None, progress_val=None, is_working=False, is_error=False):
        """
        Enhetlig och trådsäker metod för att uppdatera hela statusbaren.

        Args:
            info_msg (str): Text för fält 1 (API/Info). Om None, ändras den inte.
            progress_val (int): Värde för progressbaren (0-100). Om None, ändras den inte.
            is_working (bool): Sätter texten i kursiv stil för att visa pågående process.
        """
        """
        Enhetlig och trådsäker metod för att uppdatera hela statusbaren.
        """
        def _apply_updates():
            # --- 1. INFO/API STATUS & PROGRESSBAR ---
            if info_msg is not None:
                self.status_label.config(text=info_msg)

                # Sätt rätt stil baserat på status
                if is_error:
                    current_style = "Warning.TLabel"
                elif is_working:
                    current_style = "Working.TLabel"
                else:
                    current_style = "Info.TLabel"

                self.status_label.config(style=current_style)

            if progress_val is not None:
                self.progress_bar["value"] = progress_val

            # --- 2. KONFIG STATUS ---
            if getattr(self, 'config_unsaved', False):
                self.config_status_label.config(text="Inställningar ändrade ⚙️*", style="Warning.TLabel")
            else:
                self.config_status_label.config(text="Konfig OK ✅", style="Status.TLabel")

            # --- 3. DATA / JSON FIL STATUS ---
            active_file = getattr(self.data_manager, 'current_json_path', None)
            has_data = len(getattr(self.data_manager, 'trips', [])) > 0

            if active_file:
                fname = os.path.basename(active_file)
                self.data_status_label.config(text=f" Fil: {fname} ✅", style="Status.TLabel")
            elif has_data:
                self.data_status_label.config(text=" Osparad session (API) ⚠️*", style="Warning.TLabel")
            else:
                self.data_status_label.config(text=" Ingen data laddad 📥", style="Info.TLabel")

            self.root.update_idletasks() # Tvinga GUI:t att rita om omedelbart

        # Använd root.after för att garantera trådsäkerhet från kön
        self.root.after(0, _apply_updates)

    def mark_config_saved(self):
        """
        Markerar applikationens konfiguration som synkroniserad med disk.

        Metoden utför följande steg:
        1. Sätter 'config_unsaved'-flaggan till False.
        2. Triggar en omedelbar uppdatering av statusbaren för att ändra 
           konfigurationsfältet från 'Varning' (röd/fet) till 'OK' (grön).
        3. Loggar händelsen för att underlätta felsökning av inställningshanteringen.
        """
        self.config_unsaved = False; self.update_status_display()

    def mark_config_dirty(self, *args):
        """
        Markerar att konfigurationen har ändrats och behöver sparas.

        Denna metod anropas typiskt av händelselyssnare (events) på 
        inmatningsfält, kryssrutor och valmenyer.

        Logik:
        1. Sätter 'config_unsaved'-flaggan till True.
        2. Triggar en omedelbar uppdatering av statusbaren för att visa 
           varningsstilen (röd text/fetstil) i konfigurationsfältet.
        3. Säkerställer att användaren blir påmind om att spara vid 
           eventuell avslutning av programmet.

        Args:
            event (tkinter.Event, optional): Skickas med automatiskt om 
                                             metoden är bunden till en widget.
        """
        if not getattr(self, "config_unsaved", False):
            self.config_unsaved = True
            self.update_status_display()

    def on_closing(self):
        """
        Hanterar avslutning av applikationen och förhindrar dataförlust.

        Metoden anropas när användaren försöker stänga fönstret (via X eller meny).
        Den kontrollerar 'config_unsaved'-flaggan och om det finns ossparad 
        trippdata i sessionen. Om ändringar detekteras presenteras en 
        varningsdialog.

        Logik:
        1. Om data är osparad (vilket indikeras med röd text i statusbaren),
           visas en bekräftelsevy (Yes/No/Cancel).
        2. Vid 'Yes': Avbryter stängning så användaren kan spara manuellt.
        3. Vid 'No': Stänger programmet utan att spara.
        4. Om allt är sparat: Rensar resurser och stänger omedelbart.
        """
        # Kontrollera om NÅGON av flaggorna är True
        unsaved_config = getattr(self, 'config_unsaved', False)
        unsaved_data = getattr(self, 'data_unsaved', False)
        unsaved_journal = getattr(self, 'journal_unsaved', False)

        if unsaved_config or unsaved_data or unsaved_journal:
            msg = "Du har osparat arbete:\n"
            if unsaved_config:  msg += "• Ändrade inställningar\n"
            if unsaved_journal: msg += "• Ändringar i journalen (anteckningar/kategori)\n"
            if unsaved_data:    msg += "• Nyhämtad data som ej exporterats till PDF\n"

            if not messagebox.askyesno("Varning", msg + "\nVill du verkligen avsluta utan att spara?", icon='warning'):
                return # Avbryt stängning

        self.root.destroy()

    def update_settings_zones_preview(self):
        """
        Uppdaterar den visuella förhandsgranskningen av zoner på kartan.

        Metoden rensar befintliga markörer och cirklar från kartvyn och ritar
        om dem baserat på den aktuella listan med konfigurerade zoner. Varje zon
        representeras av en markör i mitten och en cirkulär radie som visar
        det aktiva området för automatisk kategorisering.

        Returns:
            None

        Note:
            Denna metod bör anropas varje gång en zon läggs till, tas bort eller
            när en zons radie eller koordinat ändras i inställningslistan för att
            hålla kartan synkroniserad med konfigurationen.
        """
        for item in self.zones_mini_tree.get_children():
            self.zones_mini_tree.delete(item)

        config = self.data_manager.get_config()
        zones = config.get("auto_zones", [])

        for i, zone in enumerate(zones):
            cat_val = zone.get("category", "PRIVAT")
            cat_text = "☑ TJÄNST" if cat_val == "TJÄNST" else "☐ PRIVAT"

            # Använd iid=str(i) för att hålla koll på index
            self.zones_mini_tree.insert(
                "", "end", iid=str(i), values=(zone.get("name", "Namnlös"), cat_text)
            )

    def on_mini_tree_click(self, event):
        """
        Hanterar användarens klick på en rad i minitabellen (Treeview).

        Metoden identifierar vilken rad som blivit vald baserat på koordinaterna
        i klick-eventet. Om en giltig rad hittas, hämtas resans unika ID
        och vyn uppdateras för att visa detaljerad information om den specifika
        resan i detaljvyn (DetailView).

        Args:
            event (tk.Event): Klick-händelsen från Tkinter som innehåller
                information om muspekarens position (x, y).

        Returns:
            None

        Note:
            Metoden använder `identify_row` för att översätta musens position till
            ett specifikt Treeview-objekt. Om klicket sker utanför en rad (t.ex.
            i tomt utrymme) avbryts exekveringen.
        """
        item = self.zones_mini_tree.identify_row(event.y)
        col = self.zones_mini_tree.identify_column(event.x)
        if not item:
            return

        if col == "#2":  # Kategori
            idx = int(item)
            config = self.data_manager.get_config()
            zones = config.get("auto_zones", [])

            if idx < len(zones):
                current_cat = zones[idx].get("category", "PRIVAT")
                zones[idx]["category"] = (
                    "TJÄNST" if current_cat == "PRIVAT" else "PRIVAT"
                )

                # Spara till fil och uppdatera tabellen
                self.data_manager.save_config(config)
                self.update_settings_zones_preview()

    def on_mini_tree_double_click(self, event):
        """
        Hanterar dubbelklick på en rad i körjournalstabellen.

        Metoden identifierar vilken rad som användaren har dubbelklickat på och
        aktiverar därefter detaljvyn (DetailView) för den specifika resan. Detta
        är en genväg för användaren för att snabbt gå från översiktslistan till
        redigeringsläget för en vald resa.

        Args:
            event (tk.Event): Klick-händelsen från Tkinter som innehåller
                information om muspekarens position.

        Returns:
            None

        Note:
            Dubbelklicket triggar `identify_row` för att hitta resans unika ID.
            Om inget giltigt ID hittas (t.ex. vid klick på rubrikraden),
            avbryts exekveringen för att undvika fel.
        """
        item = self.zones_mini_tree.identify_row(event.y)
        col = self.zones_mini_tree.identify_column(event.x)
        if not item:
            return

        if col == "#1":  # Namn
            self.edit_mini_tree_cell(item, 0)
            # Spara till fil och uppdatera tabellen
            self.data_manager.save_config(config)
            self.update_settings_zones_preview()

    def edit_mini_tree_cell(self, row_id, col_index):
        """
        Aktiverar in-place redigering för en specifik cell i tabellen.

        Metoden skapar en temporär `ttk.Entry`-widget exakt över den valda cellens
        position i Treeview-objektet. När användaren trycker på Enter eller flyttar
        fokus, sparas värdet och widgeten tas bort.

        Args:
            row_id (str): Det unika ID för den rad som ska redigeras.
            col_index (int): Index för den kolumn (0-baserat) som ska redigeras.

        Returns:
            None

        Note:
            Metoden använder `tree.bbox(row_id, col_id)` för att beräkna
            koordinaterna för inmatningsfältet. Om cellen inte är synlig
            (utanför viewporten) avbryts redigeringen för att undvika att
            Entry-widgeten placeras felaktigt.
        """
        col_id = f"#{col_index + 1}"
        bbox = self.zones_mini_tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, width, height = bbox

        idx = int(row_id)
        config = self.data_manager.get_config()
        zones = config.get("auto_zones", [])
        if idx >= len(zones):
            return

        old_value = zones[idx].get("name", "")

        editor = ttk.Entry(self.zones_mini_tree)
        editor.insert(0, old_value)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        self._is_saving_zone = False

        def save(event=None):
            if self._is_saving_zone:
                return
            self._is_saving_zone = True
            new_val = editor.get().strip()
            editor.destroy()

            if new_val and new_val != old_value:
                zones[idx]["name"] = new_val
                self.data_manager.save_config(config)
                self.update_settings_zones_preview()

        editor.bind("<Return>", save)
        editor.bind("<FocusOut>", save)
        editor.bind("<Escape>", lambda e: editor.destroy())

    def open_auto_category_window(self):
        """
        Öppnar inställningsfönstret för automatisk kategorisering av resor.

        Metoden initierar och visar ett modalt `AutoCategoryWindow` där användaren
        kan definiera geografiska zoner (t.ex. hem, kontor) på en karta. När
        användaren stänger fönstret anropas `DataManager` för att spara den
        uppdaterade zonlistan till konfigurationsfilen.

        Returns:
            None

        Note:
            Fönstret är "modalt" (`grab_set`), vilket innebär att användaren
            måste stänga zon-inställningarna innan hen kan interagera med
            huvudfönstret igen.
        """
        # Skapa fönstret
        win = AutoCategoryWindow(self.root, self.data_manager)

        # Vänta tills fönstret stängs (eftersom det är modalt med grab_set i din kod)
        self.root.wait_window(win)

        # När fönstret stängts, uppdatera förhandsvisningen i inställningarna och spara
        self.update_settings_zones_preview()
        self.data_manager.save_config(config)


    def visa_detaljer(self, trip_id):
        """
        Hämtar och visar detaljerad information för en specifik resa.

        Metoden anropar DataManager för att hämta all metadata om den valda
        resan, ritar upp ruttkartan, uppdaterar SoC-grafer (om tillgängligt)
        och visar resans start- och slutpunkt i detaljvyn (DetailView).

        Args:
            trip_id (str): Det unika ID:t för den resa som ska visas.

        Returns:
            None

        Note:
            Metoden är asynkron till sin natur om den behöver hämta ruttdata
            från ett API (t.ex. Traccar), vilket innebär att gränssnittet
            uppdateras först när datan har tagits emot och bearbetats.
        """
        # Sök efter matchning på antingen 'id' eller 'temp_id'
        trip_data = next(
            (
                t
                for t in self.data_manager.trips
                if str(t.get("id")) == str(trip_id)
                or str(t.get("temp_id")) == str(trip_id)
            ),
            None,
        )

        if not trip_data:
            logger.error(f"Kunde inte hitta resa med ID: {trip_id}")
            return

        # Sätt referensen och uppdatera vyn (bara en gång!)
        self.detail_view.current_data = trip_data
        self.detail_view.update_view(trip_data)

        # DEBUG
        print(
            f"DEBUG: Antal ruttpunkter i data: {len(trip_data.get('route_coords', []))}"
        )

        # Byt till detalj-fliken
        self.notebook.select(self.tab_details)

    def next_trip(self):
        """
        Navigerar till nästa resa i körjournalstabellen.

        Metoden hämtar nästa tillgängliga rad-ID i tabellen efter den aktuella
        markeringen, sätter fokus på denna rad och uppdaterar detaljvyn.
        Om sista raden redan är markerad görs ingenting.

        Returns:
            None

        Note:
            Använder `tree.next()` för att hitta nästa element. Om inga resor
            finns laddade i tabellen avbryts anropet.
        """
        if not self.data_manager.trips:
            return

        # 1. Hitta indexet för den nuvarande resan
        current_id = self.current_detailed_trip_id
        # Hitta index i listan baserat på ID
        current_idx = next(
            (
                i
                for i, t in enumerate(self.data_manager.trips)
                if str(t.get("id")) == str(current_id)
            ),
            -1,
        )

        # 2. Räkna ut nästa index
        new_index = (current_idx + 1) % len(self.data_manager.trips)

        # 3. Hämta nästa resa och skicka dess ID till visa_detaljer
        next_trip = self.data_manager.trips[new_index]
        self.visa_detaljer(str(next_trip.get("id")))

    def previous_trip(self):
        """
        Navigerar till föregående resa i körjournalstabellen.

        Metoden hämtar föregående rad-ID i tabellen före den aktuella
        markeringen, sätter fokus på denna rad och uppdaterar detaljvyn.
        Om första raden redan är markerad görs ingenting.

        Returns:
            None

        Note:
            Använder `tree.prev()` för att hitta föregående element. Praktisk
            för att snabbt gå bakåt i tiden när man granskar resedetaljer.
        """
        if not self.data_manager.trips:
            return

        # 1. Hitta indexet för den nuvarande resan
        current_id = self.current_detailed_trip_id
        current_idx = next(
            (
                i
                for i, t in enumerate(self.data_manager.trips)
                if str(t.get("id")) == str(current_id)
            ),
            0,
        )

        # 2. Räkna ut föregående index
        new_index = (current_idx - 1) % len(self.data_manager.trips)

        # 3. Hämta föregående resa och skicka dess ID till visa_detaljer
        prev_trip = self.data_manager.trips[new_index]
        self.visa_detaljer(str(prev_trip.get("id")))

    def refresh_car_tree(self):
        """
        Uppdaterar Treeview-komponenten som listar alla konfigurerade fordon.

        Metoden rensar den nuvarande listan med fordon och hämtar färsk data från
        DataManager. Den används för att säkerställa att vyn speglar aktuella
        ändringar, såsom tillagda eller borttagna fordon i konfigurationen.

        Returns:
            None

        Note:
            Denna metod bör anropas efter varje framgångsrik operation som
            modifierar bil-listan (t.ex. add, delete eller edit).
        """
        for item in self.car_tree.get_children():
            self.car_tree.delete(item)

        cars = self.data_manager.config.get("cars", [])
        for c in cars:
            self.car_tree.insert(
                "",
                "end",
                values=(c["reg"], c["model"], c["source_name"], c["device_id"]),
            )

    def delete_car(self):
        """
        Tar bort det valda fordonet från konfigurationen.

        Metoden identifierar det fordon som är markerat i UI:t, ber användaren om
        bekräftelse via en dialogruta, och uppdaterar därefter DataManager
        för att permanent ta bort fordonet från konfigurationen.

        Returns:
            None

        Raises:
            Exception: Om radering av data mot DataManager misslyckas.
        """
        selected = self.car_tree.selection()
        if not selected:
            return
        index = self.car_tree.index(selected[0])
        if messagebox.askyesno("Ta bort", "Ta bort markerad bil?"):
            self.data_manager.config["cars"].pop(index)
            self.data_manager.save_config()
            self.refresh_car_tree()

    def show_car_popup(self, edit_index=None, initial_data=None):
        """
        Visar ett popup-fönster för att lägga till eller redigera fordon.

        Metoden instansierar ett fönster (TopLevel) som tillåter användaren att
        ange fordonets namn, ID och andra inställningar. Fönstret är modalt och
        validerar inmatad data innan den skickas vidare till DataManager för sparning.

        Returns:
            None

        Note:
            Vid stängning av popup-fönstret anropas `refresh_car_tree()` automatiskt
            för att uppdatera huvudvyn med den nya eller ändrade informationen.
        """
        config = self.data_manager.get_config()
        sources = config.get("sources", [])

        if not sources:
            messagebox.showwarning(
                "Inga källor", "Du måste lägga till en datakälla först."
            )
            return

        popup = tk.Toplevel(self.root)
        popup.title("Ändra bil" if edit_index is not None else "Lägg till ny bil")
        popup.geometry("400x350")
        popup.grab_set()

        main_vbox = ttk.Frame(popup, padding=20)
        main_vbox.pack(fill="both", expand=True)

        # Reg-nr
        ttk.Label(main_vbox, text="Registreringsnummer:").pack(anchor="w")
        reg_ent = ttk.Entry(main_vbox)
        if initial_data:
            reg_ent.insert(0, initial_data["reg"])
        reg_ent.pack(fill="x", pady=(0, 10))

        # Modell
        ttk.Label(main_vbox, text="Bilmärke/Modell:").pack(anchor="w")
        brand_ent = ttk.Entry(main_vbox)
        if initial_data:
            brand_ent.insert(0, initial_data["model"])
        brand_ent.pack(fill="x", pady=(0, 10))

        # Källa
        ttk.Label(main_vbox, text="Koppla till källa:").pack(anchor="w")
        source_names = [s["name"] for s in sources]
        source_var = tk.StringVar()
        source_combo = ttk.Combobox(
            main_vbox, textvariable=source_var, values=source_names, state="readonly"
        )
        source_combo.pack(fill="x", pady=(0, 10))

        if initial_data:
            source_combo.set(initial_data["source_name"])
        elif source_names:
            source_combo.current(0)

        # Device ID / Car ID
        ttk.Label(main_vbox, text="CarID (TeslaMate)/Device ID (Traccar):").pack(
            anchor="w"
        )
        id_ent = ttk.Entry(main_vbox)
        if initial_data:
            id_ent.insert(0, initial_data.get("device_id", ""))
        id_ent.pack(fill="x", pady=(0, 10))

        def save_car():
            """
            Validerar och sparar information om ett fordon till konfigurationen.

            Metoden läser av inmatningsfält i fordon-popup-fönstret, validerar att
            obligatoriska fält (t.ex. fordonets namn och ID) är ifyllda, och
            anropar därefter DataManager för att uppdatera konfigurationsfilen
            permanent på disk.

            Returns:
                None

            Raises:
                ValueError: Om obligatoriska fält saknas eller är ogiltiga.

            Note:
                Efter lyckat sparande anropas `refresh_car_tree()` automatiskt
                för att återspegla ändringarna i huvudgränssnittet. Om sparandet
                misslyckas visas en felmeddelanderuta för användaren.
            """

            if not reg_ent.get() or not source_var.get():
                messagebox.showerror("Fel", "Reg-nr och Källa krävs!")
                return

            new_car = {
                "reg": reg_ent.get().strip().upper(),
                "model": brand_ent.get().strip(),
                "source_name": source_var.get(),
                "device_id": id_ent.get().strip(),
            }

            if "cars" not in self.data_manager.config:
                self.data_manager.config["cars"] = []

            if edit_index is not None:
                # ERSÄTT befintlig bil
                self.data_manager.config["cars"][edit_index] = new_car
            else:
                # LÄGG TILL ny bil
                self.data_manager.config["cars"].append(new_car)

            self.data_manager.save_config()
            self.refresh_car_tree()
            self.refresh_car_selector()
            popup.destroy()

        ttk.Button(
            main_vbox,
            text="Spara ändringar" if edit_index is not None else "Spara bil",
            command=save_car,
        ).pack(pady=20)

    def edit_car_popup(self):
        """
        Öppnar ett popup-fönster för att redigera ett befintligt fordon.

        Hämtar data från det markerade fordonet i listan och förifyller
        inmatningsfälten. När ändringarna sparas uppdateras motsvarande
        post i konfigurationen via `DataManager`.

        Returns:
            None
        """
        selected = self.car_tree.selection()
        if not selected:
            messagebox.showwarning("Ändra", "Välj en bil i listan att ändra.")
            return

        index = self.car_tree.index(selected[0])
        car_to_edit = self.data_manager.config["cars"][index]

        # Vi återanvänder logiken från add_car_popup men skickar med data
        self.show_car_popup(edit_index=index, initial_data=car_to_edit)

    def add_car_popup(self):
        """
        Öppnar ett popup-fönster för att lägga till ett nytt fordon.

        Skapar en instans av ett inmatningsfönster där användaren kan ange
        fordonsuppgifter. Vid bekräftelse skickas informationen till
        `DataManager` för att utöka den befintliga fordonslistan.

        Returns:
            None
        """
        self.show_car_popup()  # Kallar på samma popup men utan data

    def edit_source_popup(self):
        """
        Öppnar ett popup-fönster för att redigera en befintlig datakälla.

        Låter användaren ändra anslutningsparametrar för en vald källa.
        Efter uppdatering triggas en omstart eller en refresh av anslutningen
        mot datakällan.

        Returns:
            None
        """
        selected = self.source_tree.selection()
        if not selected:
            messagebox.showwarning("Ändra", "Välj en källa i listan att ändra.")
            return

        index = self.source_tree.index(selected[0])
        source_to_edit = self.data_manager.config["sources"][index]
        self.show_source_popup(edit_index=index, initial_data=source_to_edit)

    def add_source_popup(self):
        """
        Öppnar ett popup-fönster för att lägga till en ny datakälla.

        Användaren kan här konfigurera anslutningsdetaljer (t.ex. URL, API-nycklar
        eller databasinloggning). Metoden validerar anslutningen innan
        inställningarna sparas till `config.json`.

        Returns:
            None
        """
        self.show_source_popup()

    def show_source_popup(self, edit_index=None, initial_data=None):
        """
        Öppnar ett popup-fönster för att hantera inställningar för en datakälla.

        Metoden skapar ett modalt fönster där användaren kan välja typ av
        datakälla och fylla i nödvändiga anslutningsparametrar. När källtyp
        ändras i fönstret anropas `update_fields` för att visa relevanta
        inmatningsfält.

        Returns:
            None

        Note:
            Fönstret använder en `StringVar` för att lyssna på ändringar i
            källtypen, vilket triggar en dynamisk uppdatering av gränssnittet.
        """
        popup = tk.Toplevel(self.root)
        popup.title(
            "Ändra datakälla" if edit_index is not None else "Lägg till ny datakälla"
        )
        popup.geometry("450x500")
        popup.grab_set()

        main_vbox = ttk.Frame(popup, padding=20)
        main_vbox.pack(fill="both", expand=True)

        ttk.Label(main_vbox, text="Namnge källan (t.ex. 'NAS-Server'):").pack(
            anchor="w"
        )
        name_ent = ttk.Entry(main_vbox)
        if initial_data:
            name_ent.insert(0, initial_data["name"])
        name_ent.pack(fill="x", pady=(0, 15))

        ttk.Label(main_vbox, text="Typ av källa:").pack(anchor="w")
        type_var = tk.StringVar(
            value=initial_data["type"] if initial_data else "Traccar"
        )
        type_combo = ttk.Combobox(
            main_vbox,
            textvariable=type_var,
            values=["Traccar", "TeslaMate"],
            state="readonly",
        )
        type_combo.pack(fill="x", pady=(0, 15))

        detail_frame = ttk.LabelFrame(
            main_vbox, text=" Anslutningsdetaljer ", padding=10
        )
        detail_frame.pack(fill="both", expand=True)

        fields = {}

        def update_fields(*args):
            """
            Anpassar gränssnittet dynamiskt baserat på vald datakälla.

            Eftersom Traccar (API) och TeslaMate (Direkt databasanslutning) kräver
            olika typer av parametrar, växlar denna metod synligheten på
            inmatningsfälten i realtid när användaren ändrar i rullistan.

            Logik:
            - Vid 'Traccar': Visar fält för Host, Port, Användarnamn och Lösenord.
              Döljer fältet för Databasnamn (eftersom API:et sköter detta internt).
            - Vid 'TeslaMate': Visar samtliga fält inklusive Databasnamn, då en
              direktanslutning mot PostgreSQL kräver att den specifika databasen anges.

            Metoden rensar även eventuella gamla felmeddelanden och återställer
            'Testa anslutning'-knappen till sitt ursprungsläge vid byte av källa.
            """
            for widget in detail_frame.winfo_children():
                widget.destroy()
            fields.clear()

            current_details = initial_data.get("details", {}) if initial_data else {}

            if type_var.get() == "Traccar":
                ttk.Label(detail_frame, text="Server URL (http://...):").pack(
                    anchor="w"
                )
                fields["url"] = ttk.Entry(detail_frame)
                fields["url"].insert(0, current_details.get("url", "http://"))
                fields["url"].pack(fill="x")

                ttk.Label(detail_frame, text="E-post:").pack(anchor="w", pady=(5, 0))
                fields["user"] = ttk.Entry(detail_frame)
                fields["user"].insert(0, current_details.get("user", ""))
                fields["user"].pack(fill="x")

                ttk.Label(detail_frame, text="Lösenord:").pack(anchor="w", pady=(5, 0))
                fields["pass"] = ttk.Entry(detail_frame, show="*")
                fields["pass"].insert(0, current_details.get("pass", ""))
                fields["pass"].pack(fill="x")

            else:
                ttk.Label(detail_frame, text="Postgres Host (IP):").pack(anchor="w")
                fields["host"] = ttk.Entry(detail_frame)
                fields["host"].insert(0, current_details.get("host", ""))
                fields["host"].pack(fill="x")

                ttk.Label(detail_frame, text="DB Namn:").pack(anchor="w", pady=(5, 0))
                fields["db"] = ttk.Entry(detail_frame)
                fields["db"].insert(0, current_details.get("db", "teslamate"))
                fields["db"].pack(fill="x")

                ttk.Label(detail_frame, text="DB Lösenord:").pack(
                    anchor="w", pady=(5, 0)
                )
                fields["pass"] = ttk.Entry(detail_frame, show="*")
                fields["pass"].insert(0, current_details.get("pass", ""))
                fields["pass"].pack(fill="x")
            # --- GEMENSAM TESTKNAPP FÖR ALLA KÄLLOR ---
            test_conn_btn = tk.Button(
                detail_frame,
                text="Testa anslutning",
                command=lambda: self.on_test_connection(
                    type_var.get(),
                    (fields.get("url") or fields.get("host") or tk.StringVar(value="")).get().strip(),
                    (fields.get("user") or fields.get("db") or tk.StringVar(value="")).get().strip(),
                    (fields.get("pass") or tk.StringVar(value="")).get().strip(),
                    parent_win=popup
                ),
                bg="#add8e6",
                fg="black",
                relief="raised",
                cursor="hand2"
            )
            test_conn_btn.pack(fill="x", pady=(15, 5))

        type_combo.bind("<<ComboboxSelected>>", update_fields)
        update_fields()

        def save_source():
            """
            Validerar och sparar inställningar från popup-fönstret.

            Metoden hämtar värden från fönstrets inmatningsfält, validerar
            att nödvändig data finns (t.ex. API-nycklar), och anropar
            sedan DataManager för att uppdatera konfigurationsfilen.

            Returns:
                None
            """
            s_name = name_ent.get().strip()
            if not s_name:
                messagebox.showerror("Fel", "Namn saknas")
                return

            new_source = {
                "name": s_name,
                "type": type_var.get(),
                "details": {k: v.get() for k, v in fields.items()},
            }

            if "sources" not in self.data_manager.config:
                self.data_manager.config["sources"] = []

            if edit_index is not None:
                self.data_manager.config["sources"][edit_index] = new_source
            else:
                self.data_manager.config["sources"].append(new_source)

            self.data_manager.save_config(self.data_manager.config)
            self.refresh_source_tree()
            popup.destroy()
            self.mark_config_saved()
            self.popup_saved = True
            popup.destroy()

        ttk.Button(
            main_vbox,
            text="Spara ändringar" if edit_index is not None else "Spara källa",
            command=save_source,
        ).pack(pady=20)

    def on_test_connection(self, s_type, host_url, user_or_db, password, parent_win=None):
        """
        Utför ett anslutningstest mot den valda datakällan i realtid.

        Metoden hämtar aktuella värden direkt från GUI-fälten (host, port, user, pwd, db)
        och försöker upprätta en kortvarig anslutning för att verifiera att:
        1. Servern är nåbar (nätverkskontakt).
        2. Inloggningsuppgifterna är korrekta (autentisering).
        3. Databasen eller API-ändpunkten existerar.

        Logik per källa:
        - TeslaMate: Använder 'psycopg2' för att göra ett anslutningsförsök mot
          PostgreSQL. Har en timeout på 5 sekunder för att förhindra GUI-frysning.
        - Traccar: Använder 'requests' med Basic Auth för att anropa '/api/devices'.
          Ett lyckat svar (status 200) bekräftar att API-nycklar/lösenord fungerar.

        Feedback:
        Visar en 'messagebox.showinfo' vid framgång eller 'messagebox.showerror'
        med felmeddelande vid misslyckande. Knappen inaktiveras under pågående test.
        """
        if not host_url or not user_or_db:
            messagebox.showwarning("Test", "Fyll i alla fält först.", parent=parent_win)
            return

        self.update_status_display(info_msg=f"Testar anslutning till {s_type}...", is_working=True)

        try:
            if "TeslaMate" in s_type:
                import psycopg2
                host = host_url
                port = "5432"
                if ":" in host_url:
                    host, port = host_url.split(":")

                # Här använder vi user_or_db till båda eftersom du sa
                # att de inställningarna fungerar för dig
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    database=user_or_db,
                    user=user_or_db,
                    password=password,
                    connect_timeout=5
                )
                conn.close()
                messagebox.showinfo("Test", "Anslutning till TeslaMate lyckades! 🎉", parent=parent_win)
                self.update_status_display(info_msg="Systemet redo", is_working=False)

            else:
                # Traccar logik
                import requests
                from requests.auth import HTTPBasicAuth
                url = host_url.rstrip('/')
                if not url.startswith("http"): url = "http://" + url
                if "/api/" not in url: url += "/api/devices"

                res = requests.get(url, auth=HTTPBasicAuth(user_or_db, password), timeout=7)
                if res.status_code == 200:
                    messagebox.showinfo("Test", "Anslutning till Traccar lyckades! 🎉", parent=parent_win)
                    self.update_status_display(info_msg="Systemet redo", is_working=False)
                else:
                    messagebox.showerror("Test", f"Misslyckades (Kod: {res.status_code})", parent=parent_win)
                    self.update_status_display(info_msg="Anslutningstest misslyckades", is_error=True)

        except Exception as e:
            self.update_status_display(info_msg="Anslutningstest misslyckades", is_error=True)
            messagebox.showerror("Test", f"Ett fel uppstod:\n{str(e)}", parent=parent_win)

    def refresh_source_tree(self):
        """
        Uppdaterar Treeview-komponenten som visar alla konfigurerade datakällor.

        Metoden rensar listan i gränssnittet och läser in aktuell information
        från DataManager. Den säkerställer att användaren ser korrekta
        anslutningsstatusar och typer för alla tillagda källor.

        Returns:
            None

        Note:
            Bör anropas efter varje operation som ändrar konfigurationen för
            datakällor, såsom tillägg, borttagning eller redigering.
        """
        # Rensa trädet först
        for item in self.source_tree.get_children():
            self.source_tree.delete(item)

        # Hämta config från data_manager
        config = self.data_manager.get_config()
        sources = config.get("sources", [])

        for s in sources:
            # Hämta adressen beroende på typ
            details = s.get("details", {})
            addr = details.get("url") or details.get("host") or "N/A"
            self.source_tree.insert("", "end", values=(s["name"], s["type"], addr))

    def delete_source(self):
        """
        Tar bort en vald datakälla från konfigurationen.

        Metoden identifierar källan markerad i listan, visar en bekräftelsedialog
        för att förhindra oavsiktliga raderingar, och anropar därefter DataManager
        för att permanent ta bort källan från konfigurationsfilen.

        Returns:
            None

        Raises:
            Exception: Om raderingen misslyckas på grund av filbehörigheter eller
            att konfigurationsfilen är låst.
        """
        selected = self.source_tree.selection()
        if not selected:
            return

        index = self.source_tree.index(selected[0])
        if messagebox.askyesno("Ta bort", "Vill du ta bort denna källa?"):
            self.data_manager.config["sources"].pop(index)
            self.data_manager.save_config(self.data_manager.config)
            self.refresh_source_tree()
            self.mark_config_saved()

    def on_tab_changed(self, event):
        """
        Hanterar händelsen när användaren byter flik i huvudgränssnittet.

        Metoden triggar en uppdatering av innehållet i den nya fliken. Detta
        är kritiskt för att t.ex. ladda om reselistan vid flikbyte till
        'Journal' eller uppdatera zon-kartan vid byte till 'Settings'.

        Args:
            event (tk.Event): Händelseobjektet från Notebook-widgeten.

        Returns:
            None
        """
        selected_tab_id = self.notebook.select()
        tab_text = self.notebook.tab(selected_tab_id, "text")

        if tab_text == "Detaljer":
            # Kolla vad som är valt i tabellen just nu
            selected_items = self.journal_table.tree.selection()
            if selected_items:
                # Uppdatera detaljvyn med det senaste från databasen/minnet
                self.visa_detaljer(selected_items[0])

    def on_browse_logo(self):
        """
        Öppnar en filutforskare för val av logotyp.

        Låter användaren välja en bildfil (t.ex. .png eller .jpg) från
        lokala disken. När en fil valts anropas `update_logo_preview` för att
        visa bilden i inställningsfönstret.

        Returns:
            None

        Note:
            Filformaten begränsas till vanliga bildformat för att säkerställa
            kompatibilitet med `PIL` (Pillow) eller Tkinter-bildhantering.
        """
        path = filedialog.askopenfilename(filetypes=[("Bilder", "*.png *.gif")])
        if path:
            self.entry_logo.delete(0, tk.END)
            self.entry_logo.insert(0, path)
            self.update_logo_preview()
            self.mark_config_dirty()

    def update_logo_preview(self):
        """
        Renderar en förhandsgranskning av den valda logotypen.

        Skalar om den valda bilden till en fast storlek (t.ex. 100x100 pixlar)
        och uppdaterar den visuella widgeten i inställningsfönstret så att
        användaren ser vilken logo som kommer att användas i rapporter.

        Args:
            file_path (str): Sökvägen till bildfilen som ska visas.

        Returns:
            None

        Note:
            Om bilden är korrupt eller formatet inte stöds, visas en
            platshållar-ikon istället för att applikationen kraschar.
        """
        path = self.entry_logo.get().strip()
        if os.path.exists(path):
            try:
                self.preview_img = tk.PhotoImage(file=path)
                # Skala ner om bilden är hög
                if self.preview_img.height() > 80:
                    ratio = self.preview_img.height() // 80
                    self.preview_img = self.preview_img.subsample(ratio, ratio)
                self.logo_preview.config(image=self.preview_img, text="")
            except:
                self.logo_preview.config(image="", text="[ Fel bildformat ]")
        else:
            self.logo_preview.config(image="", text="[ Bild saknas ]")

    def load_config_to_fields(self):
        """
        Laddar konfigurationsdata från DataManager till inställningsfönstrets fält.

        Metoden hämtar aktuella inställningar (t.ex. API-nycklar, filvägar,
        företagsinformation) och fyller i motsvarande inmatningsfält i
        inställningsfliken eller popup-fönstret.

        Returns:
            None

        Note:
            Om en inställning saknas i konfigurationsfilen (t.ex. vid en
            första körning) sätts fältet till ett standardvärde eller lämnas
            tomt för att undvika fel.
        """
        config = self.data_manager.get_config()

        # Företagsnamn
        self.entry_company.delete(0, tk.END)
        self.entry_company.insert(0, config.get("company_name", ""))

        self.entry_org.delete(0, tk.END)
        self.entry_org.insert(0, config.get("org_nr", ""))

        # Logotyp-sökväg
        self.entry_logo.delete(0, tk.END)
        self.entry_logo.insert(0, config.get("company_logo", ""))

        # API-nyckel (Geoapify)
        api_keys = config.get("api_keys", {})
        self.entry_geoapify.delete(0, tk.END)
        self.entry_geoapify.insert(0, api_keys.get("geoapify", ""))

        # Ladda Loggnivå (Standard är INFO om inget valts)
        log_level = config.get("log_level", "INFO")
        self.log_level_combo.set(log_level)

        self.update_logo_preview()
        self.refresh_source_tree()
        self.refresh_car_tree()

    def save_config_fields(self):
        """
        Läser inmatningsfält från inställningsfönstret och sparar till DataManager.

        Metoden validerar innehållet i inmatningsfälten, packar ner dem i ett
        dictionary-format och anropar DataManager för att skriva över
        `config.json` på disk.

        Returns:
            None

        Raises:
            IOError: Om konfigurationsfilen inte kan skrivas till på grund
                av låsta rättigheter eller diskfel.

        Note:
            Efter ett lyckat anrop till DataManager triggas en omstart eller
            en uppdatering av applikationslogiken för att aktivera de nya
            inställningarna omedelbart.
        """
        current_config = self.data_manager.get_config()

        # Uppdatera Rapport-info
        current_config["company_name"] = self.entry_company.get().strip()
        current_config["org_nr"] = self.entry_org.get().strip()
        current_config["company_logo"] = self.entry_logo.get().strip()

        # Uppdatera API-nycklar
        if "api_keys" not in current_config:
            current_config["api_keys"] = {}
        current_config["api_keys"]["geoapify"] = self.entry_geoapify.get().strip()

        # Uppdatera Loggnivå
        selected_level_str = self.log_level_var.get()
        current_config["log_level"] = selected_level_str

        try:
            self.data_manager.save_config(current_config)
            self.mark_config_saved()

            # --- HOT RELOAD AV LOGGNIVÅ ---
            import logging
            # Konvertera sträng ("DEBUG") till numeriskt logging-värde (10)
            numeric_level = getattr(logging, selected_level_str.upper(), logging.INFO)
            # Sätt den nya nivån på root-loggern så det påverkar ALLA klasser
            logging.getLogger().setLevel(numeric_level)
            logger.log(numeric_level, f"Loggnivå dynamiskt ändrad till: {selected_level_str}")

            # UPPDATERA AddressLookup direkt!
            # Detta gör att ändringen biter direkt utan omstart.
            from src.address_lookup import AddressLookup

            self.data_manager.address_lookup = AddressLookup(current_config)

            self.config_unsaved = False
            self.update_status_display()
            messagebox.showinfo("Klart", "Inställningarna har sparats och aktiverats.")
        except Exception as e:
            messagebox.showerror("Fel", f"Kunde inte spara: {e}")

    def refresh_car_selector(self):
        """
        Uppdaterar rullistan (Combobox/Selector) med tillgängliga fordon.

        Metoden rensar nuvarande värden i väljarkomponenten och hämtar en
        uppdaterad lista över konfigurerade bilar från DataManager. Detta
        säkerställer att användaren alltid kan välja bland korrekta och
        aktuella fordon vid filtrering eller rapportering.

        Returns:
            None

        Note:
            Denna metod bör anropas varje gång fordonslistan ändras i
            inställningsfönstret för att undvika att användaren försöker
            filtrera på ett fordon som inte längre existerar eller saknar
            konfiguration.
        """
        cars = self.data_manager.config.get("cars", [])
        # Skapa en snygg sträng: "DBY34H (Xpeng G6 Performance)"
        car_list = [
            f"{c['reg']} ({c['model']}) [ID: {c.get('device_id', '??')}]" for c in cars
        ]

        self.car_selector["values"] = car_list
        if car_list:
            self.car_selector.current(0)  # Välj första bilen automatiskt
        else:
            self.car_selector_var.set("Inga bilar hittades")

    def setup_source_info(self, parent):
        """
        Initierar gränssnittskomponenter för visning av datakällans status.

        Skapar etiketter och indikatorer som visar om applikationen är
        ansluten till t.ex. Traccar eller en lokal databas. Metoden
        placerar ut dessa i huvudvyn för att ge användaren omedelbar
        feedback på systemets status.

        Returns:
            None
        """

        # Variabler för att hålla koll på vald info
        self.active_source = tk.StringVar(value="")
        self.active_device = tk.StringVar(value="")

        # Label för källa
        tk.Label(
            parent,
            textvariable=self.active_source,
            font=("Arial", 10, "bold"),
            foreground="blue",
        ).pack(side="left", padx=10)

        # Label för bil/device
        tk.Label(
            parent,
            textvariable=self.active_device,
            font=("Arial", 10, "bold"),
            foreground="green",
        ).pack(side="left", padx=10)

    def setup_date_filters(self, parent):
        """
        Konfigurerar datumväljare och filter för körjournalen.

        Initierar kalender- eller textfält (t.ex. `DateEntry` eller `Entry`)
        där användaren kan begränsa den tidsperiod som visas i tabellen.
        Kopplar även "Filtrera"-knappen till metoden som hämtar ny data.

        Returns:
            None
        """
        years = ["2024", "2025", "2026", "2027"]
        months = [f"{i:02d}" for i in range(1, 13)]

        # Från
        ttk.Label(parent, text="Från:").pack(side="left", padx=2)
        self.from_year = ttk.Combobox(parent, values=years, width=6)
        self.from_year.set("2026")
        self.from_year.pack(side="left", padx=2)

        self.from_month = ttk.Combobox(parent, values=months, width=4)
        self.from_month.set("01")
        self.from_month.pack(side="left", padx=2)

        # Till
        ttk.Label(parent, text=" Till:").pack(side="left", padx=2)
        self.to_year = ttk.Combobox(parent, values=years, width=6)
        self.to_year.set("2026")
        self.to_year.pack(side="left", padx=2)

        self.to_month = ttk.Combobox(parent, values=months, width=4)
        self.to_month.set("03")
        self.to_month.pack(side="left", padx=2)

    def prepare_export_view(self):
        """
        Förbereder export-fliken med valbara format och destinationsmappar.

        Skapar gränssnittselement för att välja exportformat (t.ex. CSV, Excel,
        PDF) och låter användaren ange var den exporterade filen ska sparas.
        Sätter upp förinställda filvägar baserat på användarens profil.

        Returns:
            None
        """
        trips = self.data_manager.trips
        if not trips:
            messagebox.showwarning("Export", "Hämta data först!")
            return

        # Räkna statistik
        total_trips = len(trips)
        work_trips = len([t for t in trips if t.get("is_work_saved")])

        # Hämta fordonsinfo från config
        config = self.data_manager.get_config()
        car_info = f"{config.get('brand', 'Okänd bil')} ({config.get('reg_nr', 'Inget regnr')})"

        # Uppdatera texterna i Export-fliken
        self.lbl_export_car.config(text=f"Fordon: {car_info}")
        self.lbl_export_stats.config(
            text=f"Totalt antal resor i listan: {total_trips} st\n"
            f"Varav markerade som tjänst: {work_trips} st"
        )

        # Hoppa till tredje fliken (index 2 eftersom vi börjar på 0)
        self.notebook.select(2)

    def load_config_to_ui(self):
        """
        Läser in konfigurationsdata och applicerar dem på UI-komponenter.

        Hämtar inställningar från DataManager och fyller i alla formulärfält
        i inställningsfönstret. Detta anropas vid uppstart eller när
        användaren öppnar inställningsmenyn för att säkerställa att
        gränssnittet speglar sparad konfiguration.

        Returns:
            None
        """
        try:
            with open(self.data_manager.config_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.config_text.delete(1.0, tk.END)
                self.config_text.insert(tk.END, content)
        except Exception as e:
            messagebox.showerror("Fel", f"Kunde inte ladda config: {e}")

    def save_config_from_ui(self):
        """
        Validerar och sparar användarens inställningar från UI till DataManager.

        Metoden samlar in all data från inställningsfliken (t.ex. sökvägar,
        API-nycklar och användarpreferenser), utför grundläggande validering
        för att säkerställa att inga fält är felaktigt ifyllda, och anropar
        sedan DataManager för att uppdatera konfigurationsfilen (config.json).

        Returns:
            bool: True om sparandet lyckades, annars False.

        Raises:
            ValueError: Om valideringen av inmatad data (t.ex. ogiltig sökväg)
                misslyckas.
        """
        try:
            content = self.config_text.get(1.0, tk.END).strip()
            # Validera att det är korrekt JSON innan vi sparar
            json.loads(content)

            with open(self.data_manager.config_path, "w", encoding="utf-8") as f:
                f.write(content)

            messagebox.showinfo("Success", "Inställningarna har sparats!")
        except json.JSONDecodeError:
            messagebox.showerror(
                "JSON Fel",
                "Ogiltigt JSON-format! Kontrollera citationstecken och komman.",
            )
        except Exception as e:
            messagebox.showerror("Fel", f"Kunde inte spara: {e}")

    def on_fetch_api(self):
        """
        Triggar hämtning av färsk resedata från den konfigurerade API-källan.

        Metoden fungerar som huvudbrygga mellan GUI:t och DataManager. Den
        initierar en asynkron eller blockerande process för att hämta reseloggar
        från t.ex. Traccar, validerar inkommande data och uppdaterar
        huvudtabellen (Treeview) med de senaste posterna.

        Returns:
            None

        Raises:
            ConnectionError: Om API-anropet misslyckas (t.ex. vid nätverksproblem).
            ValueError: Om den mottagna datan inte kan tolkas eller saknar
                obligatoriska fält.

        Note:
            Vid start av metoden bör ett "laddningsläge" aktiveras i UI:t
            för att ge användaren feedback på att data hämtas. Efter
            genomförd hämtning anropas `refresh_journal_tree()` för att
            visualisera resultatet.
        """
        # 1. Hämta inställningar från GUI
        self.update_status_display(info_msg="Ansluter och hämtar från API...", progress_val=0, is_working=True)
        selected_idx = self.car_selector.current()
        if selected_idx < 0:
            messagebox.showwarning("Varning", "Du måste välja en bil i listan först!")
            return

        selected_car = self.data_manager.config["cars"][selected_idx]
        source_name = selected_car["source_name"]
        source = next(
            (
                s
                for s in self.data_manager.config["sources"]
                if s["name"] == source_name
            ),
            None,
        )
        self.current_source = source

        if not source or not selected_car:
            messagebox.showerror("Fel", "Källa eller bil saknas i konfigurationen.")
            return

        # Spara context för eventuell export/spara-funktion
        self.data_context = {
            "source": {"name": source.get("name"), "type": source.get("type")},
            "car": {
                "name": selected_car.get("reg"),
                "device_id": selected_car.get("device_id"),
            },
        }

        # Sätt UI info
        source_display_name = source.get("name", "Okänd")
        source_display_type = source.get("type", "Okänd")
        self.active_source.set(f"Källa: {source_display_name} ({source_display_type})")
        self.active_device.set(
            f"Bil: {selected_car.get('reg')} (ID: {selected_car.get('device_id')})"
        )

        # Datum-logik
        fy, fm = self.from_year.get(), self.from_month.get()
        ty, tm = self.to_year.get(), self.to_month.get()
        start_dt = f"{fy}-{fm}-01T00:00:00Z"

        last_day = "30" if tm in ["04", "06", "09", "11"] else "31"
        if tm == "02":
            last_day = "28"
        end_dt = f"{ty}-{tm}-{last_day}T23:59:59Z"

        # 2. Förbered UI & Minne
        self.data_manager.clear_trips()  # Rensa gamla resor
        self.table.clear()  # Rensa tabellen helt

        self.fetch_queue = queue.Queue()

        use_auto = self.auto_tag_var.get()

        # 3. Välj rätt metod beroende på källa
        source_type = source.get("type")
        if source_type == "Traccar":
            target_func = self.data_manager.fetch_traccar_parallel
        elif source_type == "TeslaMate":
            target_func = self.data_manager.fetch_teslamate_parallel
        else:
            messagebox.showerror("Fel", f"Okänd källtyp: {source_type}")
            self.progress_bar.pack_forget()
            return

        # Starta tråden
        threading.Thread(
            target=target_func,
            args=(source, selected_car, start_dt, end_dt, self.fetch_queue, use_auto),
            daemon=True,
        ).start()

        # 4. Starta lyssnaren i GUI-tråden
        self.root.after(100, self.process_fetch_queue)

        self.data_manager.current_json_path = None
        self.update_status_display()

    def process_fetch_queue(self):
        """
        Hanterar kön av API-anrop för att undvika överbelastning.

        Bearbetar väntande förfrågningar sekventiellt och ser till att
        UI-statusen uppdateras för varje steg.
        """
        try:
            # Hämta alla meddelanden som ligger i kön just nu
            while not self.fetch_queue.empty():
                msg_type, percent, data = self.fetch_queue.get_nowait()

                if msg_type == "DATA":
                    trip_data = data
                    # Spara resan i DataManagers minne så den kan sparas till JSON sen
                    self.data_manager.trips.append(trip_data)
                    # Uppdatera tabellen i fönstret
                    self.table.add_single_row(trip_data)
                    display_id = trip_data.get('id') or trip_data.get('temp_id', '?')[:8]
                    self.update_status_display(info_msg=f"Bearbetar resa {display_id}...", progress_val=percent, is_working=True)

                elif msg_type == "DONE":
                    self.update_status_display(info_msg="Hämtning klar!", progress_val=100)

                    # 1. Sortera minnet för säkerhets skull
                    self.data_manager.trips.sort(
                        key=lambda x: x.get("Start", ""), reverse=True
                    )

                    # 2. Sortera GUI:t blixtsnabbt!
                    if hasattr(self, "table"):
                        self.table.sort_table_chronologically()

                    # Nollställ efter 3 sekunder
                    self.root.after(3000, lambda: self.update_status_display(info_msg="Systemet redo", progress_val=0))
                    return

                elif msg_type == "ERROR":
                    # Använd den enhetliga metoden för att visa felet i statusbaren direkt
                    self.update_status_display(
                        info_msg=f"FEL: {data}",
                        progress_val=0,
                        is_working=False,
                        is_error=True
                    )
                    messagebox.showerror("Fel", f"Ett fel uppstod: {data}")
                    return

            # Fortsätt lyssna om vi inte är klara
            self.root.after(100, self.process_fetch_queue)

        except queue.Empty:
            self.root.after(100, self.process_fetch_queue)

    def on_save_as_file(self):
        """
        Öppnar en fildialog för att spara den aktuella konfigurationen som en ny fil.

        Metoden låter användaren välja en specifik plats och ett filnamn för
        att exportera sin nuvarande konfiguration. Detta är användbart för
        användare som vill ha olika konfigurationsprofiler (t.ex. en för
        jobbet och en för privat bruk).

        Returns:
            None

        Note:
            Efter ett lyckat sparande uppdateras applikationens sökväg för
            standardfilen till den nya valda filen.
        """
        # 1. Skapa filnamnsförslag baserat på datum och källa
        source_name = self.current_source.get("type", "data").lower()
        from_date = f"{self.from_year.get()}-{self.from_month.get()}"
        to_date = f"{self.to_year.get()}-{self.to_month.get()}"
        suggested_name = f"{source_name}_{from_date}_till_{to_date}.json"

        # 2. Öppna dialogen
        path = filedialog.asksaveasfilename(
            initialfile=suggested_name,
            defaultextension=".json",
            filetypes=[("JSON-filer", "*.json"), ("Alla filer", "*.*")],
            title=f"Spara {source_name}-data",
        )

        # 3. Om användaren valde en fil, sätt den som nuvarande och spara
        if path:
            self.current_json_path = path
            self.data_manager.current_json_path = path
            self._execute_save(path)

    def _execute_save(self, path):
        """
        Utför den faktiska skrivningen av trippdata och metadata till disk.

        Metoden koordinerar sparprocessen genom att:
        1. Uppdatera statusbaren till 'Arbetar'-läge för visuell feedback.
        2. Samla in aktuell trippdata och kontextuell metadata.
        3. Anropa DataManager för seriell skrivning till JSON-format.
        4. Hantera eventuella serialiseringsfel (t.ex. datetime-objekt) och
           presentera dessa för användaren via statusbar och dialogruta.
        5. Vid framgång: Markera data som sparad och uppdatera GUI-status.

        Args:
            path (str): Den absoluta filnamnssökvägen där JSON-filen ska skapas.

        Raises:
            Exception: Om skrivrätten saknas, diskutrymmet är slut eller om
                      datan innehåller icke-serialiserbara objekt som inte
                      hanteras av DataManagers default-str-fallback.
        """
        # 1. Sätt status till "Jobbar" direkt
        self.update_status_display(
            info_msg=f"Sparar till {os.path.basename(path)}...",
            is_working=True
        )

        try:
            # Förbered data
            aktuell_data = self.data_manager.trips
            metadata = {"source_type": getattr(self, "data_context", "Blandat")}

            # 2. Kör själva skrivningen via DataManager
            # VIKTIGT: Se till att data_manager.save_to_file använder default=str i json.dump
            self.data_manager.save_to_file(path, aktuell_data, metadata)

            logger.info(f"Fil sparad lyckat: {path}")

            # 3. Uppdatera status till framgång (is_working=False återställer stilen)
            # Denna kommer nu automatiskt visa "Fil: namn.json ✅" i fält 3
            self.update_status_display(info_msg="Fil sparad ✅", is_working=False)

            # Nollställ flaggan för osparade ändringar (om metoden finns)
            if hasattr(self, 'mark_data_saved'):
                self.mark_data_saved()

            messagebox.showinfo("Spara", f"Datan har sparats i:\n{path}")

        except Exception as e:
            # 4. Hantera felet (t.ex. datetime-serialisering) visuellt
            logger.error(f"Kunde inte spara: {e}")

            self.update_status_display(
                info_msg=f"FEL vid sparande!",
                is_error=True,
                is_working=False
            )

            messagebox.showerror(
                "Fel vid sparande",
                f"Kunde inte skriva till filen.\n\nTekniskt fel:\n{str(e)}"
            )

    def on_save_file(self):
        """
        Sparar den aktuella konfigurationen till standard-konfigurationsfilen.

        Denna metod läser av alla inställningsfält i UI:t, validerar datan
        via `save_config_from_ui()` och skriver ner den till den förvalda
        `config.json`-filen. Om ingen fil har satts som standard öppnas
        `on_save_as_file` automatiskt.

        Returns:
            None

        Note:
            Denna metod används som ett "snabbalternativ" (t.ex. Ctrl+S) för
            att snabbt spara ändringar utan att behöva välja filväg varje gång.
        """
        # Om vi inte har en sökväg än, tvinga fram en dialog
        if not self.data_manager.current_json_path:
            return self.on_save_as_file()

        try:
            # 1. Förbered metadata (säkra mot None-värden)
            ctx = self.data_context if self.data_context else {}
            src = ctx.get("source", {})
            car = ctx.get("car", {})

            metadata = {
                "source_type": src.get("type", "Traccar"),
                "source_name": src.get("name", "NAS"),
                "car_name": car.get("name", "Bil"),
                "device_id": car.get("device_id", "1"),
                "timestamp": datetime.now().isoformat(),
            }

            # 2. VIKTIGAST: Hämta den levande listan från DataManager
            # Detta garanterar att vi inte sparar gammal "cachead" data
            aktuell_data = self.data_manager.trips

            # 3. Skriv till disk (DataManager.save_to_file använder 'w' som rensar filen)
            self.data_manager.save_to_file(
                self.current_json_path, aktuell_data, metadata
            )

            logger.info(f"Överskrev filen: {self.current_json_path}")
            messagebox.showinfo("Klart", "Filen har uppdaterats!")
            self.update_status_display()

        except Exception as e:
            logger.error(f"Kunde inte spara: {e}")
            messagebox.showerror(
                "Fel", f"Ett fel uppstod när filen skulle skrivas: {e}"
            )

    def on_load_file(self):
        """
        Öppnar en fildialog för att läsa in en extern konfigurations- eller datafil.

        Metoden använder `tkinter.filedialog` för att låta användaren välja en
        existerande fil (vanligtvis .json eller .csv). Efter valet valideras
        filens struktur och innehållet skickas till DataManager för att
        ersätta eller komplettera den nuvarande sessionens data.

        Returns:
            None

        Note:
            Om inläsningen lyckas anropas `load_config_to_fields()` och
            `refresh_car_tree()` för att omedelbart uppdatera gränssnittet.
        """
        path = filedialog.askopenfilename(filetypes=[("JSON-filer", "*.json")])
        if not path:
            return

        self.update_status_display(info_msg="Läser in fil och bearbetar data...", progress_val=50, is_working=True)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 1. Extrahera data och metadata
            if isinstance(data, dict) and "metadata" in data:
                metadata = data.get("metadata", {})
                trips = data.get("trips", [])

                # Här kollar vi om 'source_type' är det nästlade objektet från din logg
                source_info = metadata.get("source_type")

                if isinstance(source_info, dict):
                    self.data_context = {
                        "source": {
                            "name": source_info.get("source", {}).get("name", "Okänd"),
                            "type": source_info.get("source", {}).get("type", "Okänd")
                        },
                        "car": {
                            "name": source_info.get("car", {}).get("name", "Okänd"),
                            "device_id": source_info.get("car", {}).get("device_id", "N/A")
                        }
                    }
                else:
                    # Fallback om metadata saknar rätt struktur
                    self.data_context = None
                    trips = data

                logger.debug(f"DEBUG LOAD: Metadata laddad och GUI uppdaterat: {self.data_context}")

            # 3. Uppdatera DataManager och GUI-tillstånd
            self.data_manager.trips = trips
            self.data_manager.current_json_path = path # Spara sökvägen för autosave

            self.table.clear()
            self.update_status_display()

            # --- 2. Uppdatera datumväljarna ---
            if trips and len(trips) > 0:
                try:
                    # Hämta år och månad från första resan
                    first_start = str(trips[0].get("Start", ""))
                    if len(first_start) >= 7:
                        self.from_year.set(first_start[:4])
                        self.from_month.set(first_start[5:7])

                    # Hämta år och månad från sista resan
                    last_start = str(trips[-1].get("Start", ""))
                    if len(last_start) >= 7:
                        self.to_year.set(last_start[:4])
                        self.to_month.set(last_start[5:7])
                except Exception as e:
                    logger.error(f"Kunde inte sätta datumväljare: {e}")

            # 3. Uppdatera GUI-texter för källa/bil direkt från filens metadata
            if self.data_context:
                s_name = self.data_context["source"].get("name", "Okänd")
                s_type = self.data_context["source"].get("type", "Okänd")
                c_name = self.data_context["car"].get("name", "Okänd")
                c_id = self.data_context["car"].get("device_id", "N/A")

                self.active_source.set(f"Källa: {s_name} ({s_type})")
                self.active_device.set(f"Bil: {c_name} (ID: {c_id})")
            else:
                self.active_source.set("Källa: Okänd")
                self.active_device.set("Bil: Okänd")

            # 4. Rita ut raderna i tabellen
            for index, trip in enumerate(trips):
                if isinstance(trip, dict):
                    self.table.add_row(trip, index)
                    if trip.get("map_image_path"):
                        trip_id = str(trip.get("id"))
                        self.table.add_or_update_image(trip_id, trip["map_image_path"])

            self.update_status_display(info_msg="Systemet redo", progress_val=0)
            messagebox.showinfo(
                "Öppnat",
                f"Hittade {len(trips)} sparade resor.\nKälla: {metadata.get('source_type', 'Okänd')}",
            )

        except Exception as e:
            messagebox.showerror("Fel", f"Kunde inte läsa filen: {e}")
            logger.error(f"Error loading file: {e}", exc_info=True)

    def show_map(self, trip):
        """
        Renderar en visuell karta baserat på en rutt av koordinater.

        Metoden tar en lista med latitud- och longitudpar och ritar upp resvägen
        på kartkomponenten (t.ex. tkintermapview). Den sätter automatiskt
        zoomnivå och centrerar kartan så att hela rutten blir synlig för användaren.

        Args:
            coordinates (list of tuple): En lista med (lat, lon) som definierar
                resans rutt.

        Returns:
            None

        Note:
            Om koordinatlistan är tom eller ogiltig visas en standardvy (t.ex.
            över hela Sverige eller användarens sparade hemposition).
        """
        # 1. Kolla om vi redan har punkter (Traccar)
        points = trip.get("route_points", [])

        # 2. Om inga punkter finns, men vi har ett ID, hämta från TeslaMate
        if not points and trip.get("id"):
            # Vi behöver veta vilken källa som används för att få DB-lösenordet
            selected_idx = self.car_selector.current()
            selected_car = self.data_manager.config["cars"][selected_idx]
            source_name = selected_car["source_name"]
            source = next(
                (
                    s
                    for s in self.data_manager.config["sources"]
                    if s["name"] == source_name
                ),
                None,
            )

            if source and source["type"] == "TeslaMate":
                self.root.update()
                points = self.data_manager.get_tesla_route_points(trip["id"], source)
                # Spara ner dem i trip-objektet så vi slipper hämta igen om man klickar igen
                trip["route_points"] = points

        if not points:
            messagebox.showwarning("Karta", "Inga ruttpunkter hittades för denna resa.")
            return

        # --- RESTEN AV DIN BEFINTLIGA KOD (Rensa, Rita, Centrera) ---
        self.map_view.delete_all_path()
        self.map_view.delete_all_marker()
        self.map_view.set_path(points)

        start_node = points[0]
        end_node = points[-1]
        self.map_view.set_marker(
            start_node[0], start_node[1], text="START", marker_color_circle="green"
        )
        self.map_view.set_marker(
            end_node[0], end_node[1], text="SLUT", marker_color_circle="red"
        )

        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        self.map_view.fit_bounding_box((max(lats), min(lons)), (min(lats), max(lons)))
        self.notebook.select(self.tab_map)

    def on_preview_html(self):
        """
        Genererar och visar en temporär HTML-förhandsgranskning av körjournalen.

        Metoden sammanställer aktuell resedata till ett HTML-format baserat på
        en fördefinierad mall. Filen sparas temporärt och öppnas sedan i
        systemets standardwebbläsare för att låta användaren granska rapportens
        layout före slutgiltig export.

        Returns:
            None
        """
        # 1. Skapa en temporär fil i din säkra konfigurationsmapp
        temp_path = os.path.join(self.data_manager.config_dir, "preview_report.html")

        # 2. Generera
        if self._create_report_file(temp_path):
            # 3. Öppna i webbläsaren med absolut sökväg
            webbrowser.open_new(f"file://{os.path.abspath(temp_path)}")

    def on_export(self):
        """
        Huvudmetod för att initiera exportprocessen av körjournalen.

        Denna metod fungerar som en kontrollstation som först kontrollerar att
        det finns data att exportera. Den hämtar användarens valda inställningar
        för tidsperiod och format, och delegerar sedan det faktiska arbetet
        till specifika exportmetoder som `on_export_pdf` eller `_create_report_file`.

        Returns:
            None
        """
        # 1. Hämta info för filnamn
        config = self.data_manager.get_config()
        reg_nr = config.get("reg_nr", "okänd_bil")

        from_p = f"{self.from_year.get()}-{self.from_month.get()}"
        to_p = f"{self.to_year.get()}-{self.to_month.get()}"

        # Skapa ett proffsigt filnamn
        default_filename = f"Körjournal_{reg_nr}_{from_p}_till_{to_p}.html"

        # 1. Fråga användaren var filen ska sparas
        file_path = filedialog.asksaveasfilename(
            defaultextension=".html",
            initialfile=default_filename,
            filetypes=[("HTML-fil", "*.html")],
        )

        # 2. Generera direkt till vald plats
        if file_path:
            if self._create_report_file(file_path):
                messagebox.showinfo("Export", "Körjournal sparad!")

    def _create_report_file(self, file_path):
        """
        Privat hjälpmetod som utför den faktiska skrivningen av rapportdata till disk.

        Metoden hanterar den tekniska logiken för att omvandla en Python-struktur
        till det valda filformatet. Den ser till att teckenkodning (UTF-8)
        blir rätt och att filen stängs korrekt efter skrivning.

        Args:
            data (list/dict): Den bearbetade resedatan som ska sparas.
            file_path (str): Den fullständiga sökvägen till målfilen.
            file_format (str): Formatet på filen (t.ex. 'CSV', 'XLSX', 'JSON').

        Returns:
            bool: True om filen sparades utan fel, annars False.

        Raises:
            PermissionError: Om programmet saknar skrivrättigheter i målmappen.
        """
        # 1. Hämta en FRÄSCH lista varje gång metoden anropas
        all_trips = self.data_manager.trips

        # 2. Skapa en ny tom lista (här startar vi om från noll)
        final_data = []

        # 3. Filtrera (Endast tjänst om checkboxen är ikryssad)
        only_work = self.export_only_work.get()

        for t in all_trips:
            # Om vi bara vill ha tjänst, hoppa över privatresor
            if only_work and not t.get("is_work_saved", False):
                continue

            final_data.append(t)

        if not final_data:
            messagebox.showwarning("Export", "Inga resor hittades att exportera.")
            return False

        # 4. Period-info
        from_p = f"{self.from_year.get()}-{self.from_month.get()}"
        to_p = f"{self.to_year.get()}-{self.to_month.get()}"
        period_str = f"{from_p} till {to_p}"

        # 5. Generera HTML
        from src.exporter import generate_html_report

        # NYTT: Vi skickar med map_cache_dir som ett extra argument
        generate_html_report(
            final_data,
            self.data_manager.get_config(),
            file_path,
            period_str,
            map_cache_dir=self.data_manager.map_cache_dir
        )
        return True

    def on_export_pdf(self):
        """
        Genererar en professionellt formaterad PDF-rapport av körjournalen.

        Metoden skapar ett PDF-dokument som inkluderar företagets logotyp,
        sammanställning av körsträckor, detaljerade reseloggar och eventuella
        skatteberäkningar. Den använder bibliotek som FPDF eller ReportLab
        för att bygga upp tabeller och sidhuvuden enligt gällande krav
        för körjournaler.

        Returns:
            None

        Note:
            Innan PDF:en skapas anropas ofta en dialogruta där användaren
            får välja var filen ska sparas.
        """
        config = self.data_manager.get_config()
        reg_nr = config.get("reg_nr", "okand")
        from_p = f"{self.from_year.get()}-{self.from_month.get()}"
        to_p = f"{self.to_year.get()}-{self.to_month.get()}"

        default_name = f"Korjournal_{reg_nr}_{from_p}_till_{to_p}.pdf"

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF-fil", "*.pdf")],
        )

        if file_path:
            # 1. Skapa en temporär HTML-fil i din säkra konfigurationsmapp
            temp_html = os.path.join(self.data_manager.config_dir, "temp_export.html")
            self._create_report_file(temp_html)

            # 2. Konvertera till PDF
            try:
                import pdfkit

                options = {
                    "enable-local-file-access": None,  # Viktigt för att bilderna ska synas!
                    "encoding": "UTF-8",
                    "quiet": "",
                }
                pdfkit.from_file(temp_html, file_path, options=options)
                messagebox.showinfo("Success", f"PDF skapad:\n{file_path}")
                self.mark_data_saved()

                # Städa upp
                if os.path.exists(temp_html):
                    os.remove(temp_html)
            except Exception as e:
                messagebox.showerror(
                    "PDF Fel",
                    f"Kunde inte skapa PDF.\n\nHar du kört 'sudo apt install wkhtmltopdf'?\n\nFelmeddelande: {e}",
                )
