# PyAutoJournal

PyAutoJournal är ett grafiskt verktyg (byggt i Python/Tkinter) för att automatiskt hämta, kategorisera och exportera körjournaler från GPS-källor som **Traccar** och **TeslaMate**. Perfekt för att skapa körjournaler för Skatteverket med minimal handpåläggning.

## 📸 Gränssnitt
*Här kan du lägga in dina skärmdumpar senare genom att ersätta länkarna nedan:*
![Huvudvy - Journal](https://via.placeholder.com/800x450?text=Skärmdump+Journalvy)
*Huvudvyn med alla resor och miniatyrkartor.*

![Detaljvy - Karta](https://via.placeholder.com/800x450?text=Skärmdump+Detaljvy)
*Detaljerad ruttvisning med interaktiv karta.*

## Arkitektur & Design
Applikationen följer en tydlig arkitektur för att separera logik från användargränssnitt, vilket gör projektet lätt att underhålla och vidareutveckla.

* **GUI-lager (`src/gui_handler.py` & Co):** Ansvarar för användarinteraktion och rendering av vyer. Alla fönster är byggda med `tkinter` och `ttk`.
* **Logik-lager (`src/data_manager.py`):** Fungerar som "hjärnan" i applikationen. Den koordinerar datahämtning, adressuppslagningar och bearbetning.
* **Datakällor (`src/data_fetcher.py`, `src/data_processor.py`):** Hanterar rådata från API:er och databaser samt omvandlar det till läsbara reseloggar.

## Projektstruktur
```text
PyAutoJournal/
├── assets/             # Bilder, ikoner och logotyper
├── src/                # All källkod (motorn i appen)
│   ├── __init__.py     # Gör mappen till ett Python-paket
│   ├── gui_handler.py  # Huvudapplikationen
│   ├── data_manager.py # Datakoordinering
│   └── ...
├── tests/              # Enhetstester för att säkerställa stabilitet
├── main.py             # Startpunkt för applikationen
├── config.json         # Konfigurationsfil
├── requirements.txt    # Beroenden
└── README.md

## ✨ Funktioner
* **Multi-Source:** Stöd för Traccar API och TeslaMate (PostgreSQL).
* **Auto-Kategorisering:** Definiera zoner (Hemma, Jobbet) för automatisk taggning.
* **Smart Export:** Skapar professionella PDF-rapporter redo för redovisning.
* **Kartstöd:** Interaktiva kartor via OpenStreetMap.

## 🚀 Installation
1. Klona repot: `git clone https://github.com/ditt-anvandarnamn/PyAutoJournal.git`
2. Installera beroenden: `pip install -r requirements.txt`
3. Starta programmet: `python main.py`

### 2. 🛠 Krav för PDF-export
För att kunna generera PDF-rapporter använder PyAutoJournal verktyget `wkhtmltopdf`. Detta måste installeras separat på ditt operativsystem:

* **Windows:** Ladda ner och kör installatören från [wkhtmltopdf.org](https://wkhtmltopdf.org/downloads.html). **Viktigt:** Se till att välja "Add to PATH" under installationen.
* **Linux (Ubuntu/Debian):** Kör `sudo apt install wkhtmltopdf`.
* **macOS:** Kör `brew install wkhtmltopdf`.

## 🛠️ Byggt med
* [Tkinter & TkinterMapView](https://github.com/TomSchimansky/TkinterMapView) - GUI och inbyggda kartor
* [Folium](https://python-visualization.github.io/folium/) - HTML-kartgenerering
* [Psycopg2](https://pypi.org/project/psycopg2/) - Databaskoppling mot TeslaMate

## ⚖️ Licens
Detta projekt är licensierat under **MIT-licensen**. Se filen `LICENSE` för fullständig text.

*Skapat med ❤️  för att förenkla vardagen för bilister som måste rapportera in körjournal till skatteverket.*
