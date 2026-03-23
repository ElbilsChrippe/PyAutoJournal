import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import tkintermapview
import math
from src.logger_setup import get_logger

logger = get_logger(__name__)

class AutoCategoryWindow(tk.Toplevel):
    """
        Ett grafiskt gränssnitt för att visuellt hantera Auto-Zoner (Geofences).

        Detta fönster låter användaren skapa, flytta, ändra storlek på och ta bort
        geografiska zoner via en interaktiv karta (`tkintermapview`). Det synkroniserar
        även en tabellvy (`Treeview`) där användaren kan redigera zonernas namn
        och kategori (t.ex. 'PRIVAT' eller 'TJÄNST').

        Ändringar som görs i detta fönster sparas direkt till applikationens
        huvudkonfiguration via `DataManager`.

        Attributes:
            data_manager (DataManager): Referens till systemets datahanterare.
            zones (list): En lokal arbetskopia av zon-inställningarna.
            markers (dict): Kart-objekt för zonernas mittpunkter (för flytt).
            handles (dict): Kart-objekt för zonernas radie-markörer.
            paths (dict): Kart-objekt som ritar ut själva cirkeln på kartan.
        """
    def __init__(self, parent, data_manager):
        super().__init__(parent)
        self.title("Hantera Auto-Zoner")
        self.geometry("1100x750")
        self.data_manager = data_manager

        self.transient(parent)
        self.grab_set()

        self.zones = self.data_manager.get_config().get("auto_zones", [])
        self.markers = {}  # {index: center_marker}
        self.handles = {}  # {index: radius_handle_marker}
        self.paths = {}  # {index: circle_path_object}

        self._build_ui()
        self._load_zones_to_ui()

    def _build_ui(self):
        """
        Initierar zon-fönstret, ställer in modal-status och laddar data.

        Args:
            parent (tk.Widget): Föräldrafönstret (huvudapplikationen).
            data_manager (DataManager): Instansen som hanterar persistens.
        """
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        # Karta
        map_frame = ttk.Frame(paned)
        paned.add(map_frame, weight=3)
        self.map_widget = tkintermapview.TkinterMapView(map_frame, corner_radius=0)
        self.map_widget.pack(fill="both", expand=True)
        self.map_widget.set_position(58.5, 15.0)
        self.map_widget.set_zoom(6)

        # Högerklick på karta för att skapa
        self.map_widget.add_right_click_menu_command(
            label="Skapa ny zon här", command=self._add_zone_from_map, pass_coords=True
        )
        # Högerklick för att ta bort (vi kollar om vi är nära en zon)
        self.map_widget.add_right_click_menu_command(
            label="Ta bort närmaste zon",
            command=self._delete_nearest_zone,
            pass_coords=True,
        )

        # Sidopanel med tabell
        list_frame = ttk.Frame(paned)
        paned.add(list_frame, weight=1)
        ttk.Label(list_frame, text="Mina Zoner", font=("Arial", 10, "bold")).pack(
            pady=5
        )

        self.tree = ttk.Treeview(
            list_frame, columns=("namn", "kat", "radie"), show="headings", height=10
        )
        self.tree.heading("namn", text="Namn")
        self.tree.heading("kat", text="Kategori")
        self.tree.heading("radie", text="Radie (m)")
        self.tree.column("radie", width=70)
        self.tree.pack(fill="both", expand=True)

        # Bind klick och dubbelklick
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        ttk.Label(
            list_frame,
            text="Tips:\n• Dra i nålen för att flytta\n• Dra i '↔' för att ändra radie\n• Högerklicka för att ta bort",
            font=("Arial", 8, "italic"),
            justify="left",
        ).pack(pady=10)

    def _get_circle_points(self, lat, lon, radius_m):
        """
        Beräknar polygon-punkter för att rita en visuell cirkel på kartan.

        Använder en approximering av jordens krökning (1 grad ≈ 111,111 meter)
        för att generera 36 punkter (var 10:e grad) som bildar en sluten cirkel
        runt en given mittpunkt.

        Args:
            lat (float): Mittpunktens latitud.
            lon (float): Mittpunktens longitud.
            radius_m (int/float): Cirkelns radie i meter.

        Returns:
            list: En lista av (lat, lon)-tupler som definierar cirkelns omkrets.
        """
        points = []
        # Enkel approximation: 1 grad lat ≈ 111 111 meter
        lat_step = radius_m / 111111.0
        lon_step = radius_m / (111111.0 * math.cos(math.radians(lat)))

        for i in range(37):
            angle = math.radians(i * 10)
            p_lat = lat + lat_step * math.cos(angle)
            p_lon = lon + lon_step * math.sin(angle)
            points.append((p_lat, p_lon))
        return points

    def _load_zones_to_ui(self):
        """
        Rensar gränssnittet och ritar om alla zoner på kartan och i tabellen.

        Metoden rensar först gamla markörer, stigar (paths) och tabellrader.
        Därefter itererar den över `self.zones` och återskapar grafiken: en
        mitt-markör för namnet, en cirkel för räckvidden och ett '↔'-handtag
        för att justera radien.
        """
        # 1. Rensa alla gamla objekt från kartan
        for m in self.markers.values():
            m.delete()
        for h in self.handles.values():
            h.delete()
        for p in self.paths.values():
            p.delete()

        # 2. Rensa tabellen
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.markers, self.handles, self.paths = {}, {}, {}

        # 3. Rita ut zoner
        for i, zone in enumerate(self.zones):
            lat, lon, rad = zone["lat"], zone["lon"], zone.get("radius", 200)

            # Formatera kategorin snyggt
            cat_val = zone.get("category", "PRIVAT")
            cat_text = "☑ TJÄNST" if cat_val == "TJÄNST" else "☐ PRIVAT"

            # Lägg till i tabellen
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(zone["name"], zone.get("category", "PRIVAT"), rad),
            )

            # Skapa huvudmarkör (klick-för-att-flytta)
            marker = self.map_widget.set_marker(
                lat,
                lon,
                text=zone["name"],
                command=lambda m, idx=i: self._initiate_move(idx),
            )
            self.markers[i] = marker

            # RITA CIRKELN (Visualiseringen)
            circle_points = self._get_circle_points(lat, lon, rad)
            path = self.map_widget.set_path(circle_points)
            self.paths[i] = path

            # Skapa radie-handtag (för att ändra storlek)
            # Vi placerar handtaget på cirkelns högra kant
            h_lon = lon + (rad / (111111.0 * math.cos(math.radians(lat))))
            handle = self.map_widget.set_marker(
                lat,
                h_lon,
                text="↔",
                command=lambda m, idx=i: self._initiate_radius_change(idx),
            )
            self.handles[i] = handle

        # Om vi har en vald zon, markera den i tabellen
        if hasattr(self, "selected_move_index"):
            self.tree.selection_set(str(self.selected_move_index))
            self.tree.see(str(self.selected_move_index))

    def _initiate_move(self, index):
        """Sparar vilken zon vi vill flytta."""
        self.selected_zone_index = index
        self.map_widget.add_left_click_map_command(self._move_selected_zone)
        messagebox.showinfo(
            "Flytta zon", "Klicka nu på den nya platsen på kartan för att flytta zonen."
        )

    def _move_selected_zone(self, coords):
        """
        Flyttar den valda zonen till de nya koordinaterna där användaren klickat.

        Args:
            coords (tuple): De nya (lat, lon) koordinaterna från kart-klicket.
        """
        if hasattr(self, "selected_zone_index"):
            idx = self.selected_zone_index
            self.zones[idx]["lat"], self.zones[idx]["lon"] = coords

            # Spara och rita om
            self._save_and_refresh()

            # Stäng av flytt-läget
            del self.selected_zone_index
            self.map_widget.add_left_click_map_command(None)

    def _initiate_radius_change(self, index):
        self.selected_radius_index = index
        self.map_widget.add_left_click_map_command(self._finalize_radius_change)
        messagebox.showinfo(
            "Ändra radie",
            "Klicka på en punkt på kartan för att bestämma zonens nya kant.",
        )

    def _finalize_radius_change(self, coords):
        """
        Beräknar och sparar en zons nya radie baserat på ett klick på kartan.

        Använder Pythagoras sats anpassad för geografiska koordinater för att
        mäta avståndet mellan zonens mittpunkt och platsen användaren klickade på.
        Sätter en minimal tillåten radie på 50 meter för att undvika osynliga zoner.

        Args:
            coords (tuple): (lat, lon) för var användaren klickade för att
                sätta den nya ytterkanten.
        """
        idx = self.selected_radius_index
        center = (self.zones[idx]["lat"], self.zones[idx]["lon"])

        # Beräkna avståndet mellan klick och center (Pythagoras)
        d_lat = (coords[0] - center[0]) * 111111.0
        d_lon = (coords[1] - center[1]) * (111111.0 * math.cos(math.radians(center[0])))
        new_radius = int(math.sqrt(d_lat**2 + d_lon**2))

        self.zones[idx]["radius"] = max(50, new_radius)  # Minst 50m

        del self.selected_radius_index
        self.map_widget.add_left_click_map_command(None)
        self._save_and_refresh()

    def _on_handle_moved(self, handle, index):
        """
        Uppdaterar zonens radie dynamiskt när användaren drar i handtaget (↔).

        Metoden anropas vid varje 'drag'-händelse på kartan. Den beräknar
        avståndet mellan zonens mittpunkt och handtagets nya position,
        uppdaterar radien i realtid och ritar om cirkeln för att ge
        omedelbar visuell feedback.

        Args:
            marker (tkintermapview.CanvasPositionMarker): Markörobjektet som
                representerar radie-handtaget som flyttas.

        Note:
            För att säkerställa precision används `calculate_distance_meters`
            från DataManager, vilket tar hänsyn till jordens krökning även
            vid små radiejusteringar.
        """
        center = self.markers[index].position
        edge = handle.position

        # Räkna ut avståndet (Pytagoras duger för små avstånd)
        d_lat = (edge[0] - center[0]) * 111111.0
        d_lon = (edge[1] - center[1]) * (111111.0 * math.cos(math.radians(center[0])))
        new_radius = int(math.sqrt(d_lat**2 + d_lon**2))

        # Begränsa radie (min 20m, max 2000m)
        self.zones[index]["radius"] = max(20, min(2000, new_radius))

        self._save_and_refresh()

    def _save_and_refresh(self):
        """
        Sparar ändringar i arbetskopian till systemets konfiguration och uppdaterar UI.

        Anropas varje gång en zon flyttas, ändrar radie, byter namn eller raderas.
        Garanterar att den grafiska representationen alltid är i synk med datan.
        """
        config = self.data_manager.get_config()
        config["auto_zones"] = self.zones
        self.data_manager.save_config(config)
        self._load_zones_to_ui()

    def _add_zone_from_map(self, coords):
        """
        Skapar en ny Auto-Zon vid de angivna koordinaterna på kartan.

        Metoden öppnar dialogrutor för att låta användaren namnge zonen
        (t.ex. 'Hem') och välja en kategori (t.ex. 'PRIVAT'). Om användaren
        slutför stegen skapas ett nytt zon-objekt med en standardradie (100m)
        som sedan läggs till i konfigurationen och ritas ut på kartan.

        Args:
            coords (tuple): De (lat, lon) koordinater där användaren
                högerklickade för att lägga till zonen.

        Returns:
            None: Metoden uppdaterar `self.zones` och anropar
                `_save_and_refresh()` internt vid framgång.
        """
        name = simpledialog.askstring("Ny Zon", "Namn på zon:")
        if not name:
            return

        # Standard: 200m, Privat
        self.zones.append(
            {
                "name": name,
                "lat": coords[0],
                "lon": coords[1],
                "radius": 200,
                "category": "PRIVAT",
            }
        )
        self._save_and_refresh()

    def _delete_nearest_zone(self, coords):
        """
        Identifierar och raderar den zon som ligger närmast ett klick på kartan.

        Metoden itererar igenom alla befintliga zoner och beräknar avståndet
        från klickpunkten till zonernas mittpunkter. Om den närmaste zonen
        ligger inom en rimlig räckvidd, raderas den från både arbetskopian
        och gränssnittet.

        Args:
            coords (tuple): (lat, lon) för platsen där användaren högerklickade
                för att radera.

        Note:
            Metoden anropar `_save_and_refresh()` efter radering för att
            säkerställa att ändringen skrivs till konfigurationsfilen omedelbart.
        """
        if not self.zones:
            return

        closest_idx = -1
        min_dist = 999999

        for i, z in enumerate(self.zones):
            dist = math.sqrt((z["lat"] - coords[0]) ** 2 + (z["lon"] - coords[1]) ** 2)
            if dist < min_dist:
                min_dist = dist
                closest_idx = i

        # Om vi är någorlunda nära (ca 500m tröskel)
        if closest_idx != -1 and min_dist < 0.005:
            if messagebox.askyesno(
                "Ta bort", f"Vill du ta bort zonen '{self.zones[closest_idx]['name']}'?"
            ):
                del self.zones[closest_idx]
                self._save_and_refresh()

    def _on_tree_click(self, event):
        """
        Hanterar enkelklick i zon-tabellen och fokuserar kartan på vald zon.

        När användaren markerar en zon i listan, hämtas zonens koordinater
        och kartan flyttas (panneras) automatiskt så att den valda zonen
        hamnar i centrum. Detta underlättar navigering mellan utspridda zoner.

        Args:
            event (tk.Event): Klick-eventet från Treeview-komponenten.
        """
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        if col == "#2":  # Kolumn 2 är Kategori
            idx = int(item)
            current_cat = self.zones[idx].get("category", "PRIVAT")
            # Byt värde
            self.zones[idx]["category"] = (
                "TJÄNST" if current_cat == "PRIVAT" else "PRIVAT"
            )
            self._save_and_refresh()

    def _on_tree_double_click(self, event):
        """
        Hanterar dubbelklick i zon-tabellen för att starta in-line redigering.

        Metoden identifierar exakt vilken rad och kolumn användaren klickat på.
        Om klicket sker i namn-kolumnen, initieras `_edit_cell` för att låta
        användaren skriva in ett nytt namn direkt i tabellen.

        Args:
            event (tk.Event): Dubbelklick-eventet som innehåller X- och
                Y-koordinater för klicket.
        """
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        if col == "#1":  # Kolumn 1 är Namn
            self._edit_cell(item, 0)  # 0 är index för värdet i 'values'

    def _edit_cell(self, row_id, col_index):
        """
        Skapar ett in-line redigeringsfält (Entry) inuti Treeview-tabellen.

        Låter användaren byta namn på en zon direkt i listan genom att dubbelklicka
        på cellen. Hanterar fokus, sparning vid 'Enter' eller 'FocusOut', och
        avbryter vid 'Escape'.

        Args:
            row_id (str): ID för den valda raden i Treeview.
            col_index (int): Index för kolumnen (0 för Namn).
        """
        col_id = f"#{col_index + 1}"
        bbox = self.tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, width, height = bbox

        idx = int(row_id)
        old_value = self.zones[idx].get("name", "")

        editor = ttk.Entry(self.tree)
        editor.insert(0, old_value)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        self._is_saving = False

        def save(event=None):
            """
            Slutför redigeringen av zonens namn och synkroniserar ändringarna.

            Denna inre funktion körs när användaren trycker på 'Enter' eller
            klickar utanför redigeringsrutan. Den ansvarar för att:
            1. Förhindra dubbelkörning med hjälp av flaggan `_is_saving`.
            2. Läsa ut det nya namnet och rensa eventuella mellanslag.
            3. Uppdatera den lokala zon-listan (`self.zones`).
            4. Uppdatera texten på kartans markör i realtid.
            5. Trigga en fullständig sparning till disk via `_save_and_refresh`.

            Args:
                event (tk.Event, optional): Händelsen som utlöste sparningen
                    (t.ex. knapptryckning). Standard är None.
            """
            if self._is_saving:
                return
            self._is_saving = True
            new_val = editor.get().strip()
            editor.destroy()

            # Spara bara om värdet har ändrats och inte är tomt
            if new_val and new_val != old_value:
                self.zones[idx]["name"] = new_val
                # Uppdatera även markörens text på kartan
                if idx in self.markers:
                    self.markers[idx].set_text(new_val)
                self._save_and_refresh()

        editor.bind("<Return>", save)
        editor.bind("<FocusOut>", save)
        editor.bind("<Escape>", lambda e: editor.destroy())
