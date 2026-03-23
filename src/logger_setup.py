import logging
import sys
from pathlib import Path

def setup_logging(log_file="pyautojournal.log", level=logging.DEBUG):
    """
    Konfigurerar den globala loggningsmiljön för hela applikationen.

    Denna funktion bör anropas en gång i början av main.py. Den sätter upp
    hur meddelanden ska formateras och vart de ska skickas (konsol och fil).
    """
    # Skapa en enhetlig formatering: Tid - Filnamn - Allvarlighetsgrad - Meddelande
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - [%(filename)s:%(lineno)d] - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Hämta root-loggern (grunden för alla andra loggers)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Rensa eventuella gamla handlers (förhindrar dubbel-loggning vid omstart)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Handler 1: Terminalen (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Vi sätter nivån till WARNING för de bibliotek som skräpar ner konsolen
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("tkintermapview").setLevel(logging.INFO) 

    # Handler 2: Filutskrift
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Kunde inte initiera filloggning: {e}")

def get_logger(name):
    """
    Hämtar en namngiven logger-instans för en specifik modul.

    Args:
        name (str): Oftast __name__ från den anropande modulen.
    """
    return logging.getLogger(name)
