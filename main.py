import tkinter as tk
from tkinter import PhotoImage
import logging
import json
import os

from src.logger_setup import setup_logging, get_logger
from src.gui_handler import PyAutoJournal

def main():
    """
    Applikationens entry point (startpunkt).

    Denna funktion fungerar som en 'Bootloader' för PyAutoJournal. Den har tre
    huvuduppgifter innan användaren ens ser gränssnittet:

    1. Miljökonfiguration:
       Läser 'config.json' i ett tidigt skede för att bestämma vilken
       loggnivå (DEBUG, INFO, etc.) som ska användas.

    2. Initiering av tjänster:
       Anropar 'setup_logging' för att aktivera loggning till både konsol
       och fil, vilket säkerställer att allt som händer vid start dokumenteras.

    3. GUI-etablering:
       Skapar huvudfönstret (root), sätter globala fönsterekenskaper
       (titel, storlek, ikon) och instansierar 'PyAutoJournal'.

    Felhantering:
       Hela uppstarten ligger i ett try-except-block. Om applikationen kraschar
       innan fönstret hunnit öppnas, fångas felet och skrivs till loggfilen
       med en fullständig stacktrace för enkel felsökning.
    """
    # 1. BESTÄM LOGGNIVÅ FRÅN START
    # Vi läser config.json rått här bara för att veta vilken nivå vi ska logga på
    start_level = logging.INFO  # Standard om filen saknas
    config_path = "config.json"

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                level_str = config_data.get("log_level", "INFO").upper()
                # Omvandla sträng ("DEBUG") till logging-objekt (10)
                start_level = getattr(logging, level_str, logging.INFO)
        except Exception:
            # Om filen är korrupt eller inte går att läsa, kör vi på INFO
            pass

    # 2. INITIERA LOGGNINGEN
    # Nu startar vi loggsystemet med rätt nivå innan GUI:t ens skapats
    setup_logging(level=start_level)
    logger = get_logger(__name__)
    logger.info(f"Systemet startar (Loggnivå: {logging.getLevelName(start_level)})")

    # 3. SKAPA GUI-FÖNSTRET
    root = tk.Tk(className="PyAutoJournal")
    root.title("PyAutoJournal v1.0")
    root.geometry("1400x900")

    # 4. SÄTT IKON (Om den finns)
    icon_file = os.path.join("assets", "logo.png")
    if os.path.exists(icon_file):
        try:
            app_icon = tk.PhotoImage(file=icon_file)
            root.iconphoto(True, app_icon)
        except Exception as e:
            logger.warning(f"Kunde inte ladda ikon: {e}")

    # 5. STARTA APPLIKATIONEN
    try:
        # Skickar kontrollen vidare till gui_handler.py
        app = PyAutoJournal(root)

        logger.info("GUI initierat, startar huvudloopen.")
        root.mainloop()

    except Exception as e:
        logger.critical(f"Ett kritiskt fel stoppade applikationen: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
