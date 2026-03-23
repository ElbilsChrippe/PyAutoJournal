import os
from datetime import datetime
from src.logger_setup import get_logger

# Initiera loggern för den här filen
logger = get_logger(__name__)


def generate_html_report(trips, config, output_path, period_str, map_cache_dir=None):
    """
    Genererar en professionell körjournal anpassad för Skatteverket.
    """
    # Beräkningar
    total_km = sum(float(t.get("distance_km", 0)) for t in trips)
    total_trips = len(trips)

    # Hantera företagsinfo
    company_name = config.get("company_name", "Företagsnamn saknas")
    org_nr = config.get("org_nr", "")
    org_str = f"Org.nr: {org_nr}" if org_nr else ""

    logo_path = config.get("company_logo", "")
    logo_html = ""
    if logo_path and os.path.exists(logo_path):
        logo_url = "file://" + os.path.abspath(logo_path)
        logo_html = f'<img src="{logo_url}" class="logo">'

    # HTML-struktur med marginaler och layout
    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            /* Centrerar sidan och ger snygga marginaler */
            body {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 12px; color: #333; max-width: 1100px; margin: 40px auto; padding: 0 20px; }}
            .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #333; margin-bottom: 20px; padding-bottom: 10px; }}
            .logo {{ max-height: 60px; }}
            h1 {{ margin: 0; font-size: 20px; }}

            /* Sammanställning högst upp */
            .summary {{ background: #f8f9fa; padding: 15px; margin-bottom: 20px; border-radius: 4px; border: 1px solid #ddd; }}

            /* Tabell */
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th {{ background: #f2f2f2; border-bottom: 2px solid #333; text-align: left; padding: 10px 8px; font-weight: bold; }}
            td {{ border-bottom: 1px solid #ddd; padding: 8px; vertical-align: middle; }}

            /* Kartbilder i tabellen - håller dem små */
            .map-img {{ width: 80px; height: 40px; object-fit: cover; border-radius: 3px; border: 1px solid #ccc; display: block; }}

            .tag-work {{ font-weight: bold; color: #1976d2; }}
            .tag-private {{ font-weight: bold; color: #616161; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h1>Körjournal - {company_name}</h1>
                <p style="margin: 4px 0;">{org_str}<br>Period: {period_str}</p>
            </div>
            {logo_html}
        </div>

        <div class="summary">
            <h3 style="margin-top: 0;">Sammanställning</h3>
            <p style="margin: 4px 0;"><strong>Fordon:</strong> {config.get('brand', '')} {config.get('model', '')} ({config.get('reg_nr', '')})</p>
            <p style="margin: 4px 0;"><strong>Totalt antal resor:</strong> {total_trips} st</p>
            <p style="margin: 4px 0;"><strong>Total körsträcka:</strong> {total_km:.1f} km</p>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width: 85px;">Karta</th>
                    <th style="width: 120px;">Datum/Tid</th>
                    <th>Från / Till</th>
                    <th style="width: 120px;">Mätarställning</th>
                    <th style="width: 60px;">Tid (min)</th>
                    <th>Syfte</th>
                    <th style="width: 50px;">Km</th>
                </tr>
            </thead>
            <tbody>
    """

    # Rader för varje resa
    for t in trips:
        start_t = t.get("start_time_display", "---")
        purpose = t.get("desc_saved", "Tjänsteresa")
        is_work = t.get("is_work_saved", False)
        if not is_work:
            purpose = "PRIVATRESA"

        tag_class = "tag-work" if is_work else "tag-private"

        # Hantera Mätarställning (ODO)
        odo_start = t.get("Start_Odo", 0)
        odo_end = t.get("End_Odo", 0)
        # Om ODO är i meter (t.ex. > 100 000), konvertera till km
        if odo_start > 100000:
            odo_start /= 1000
            odo_end /= 1000

        odo_str = (
            f"Start: {odo_start:.1f}<br>Stopp: {odo_end:.1f}"
            if odo_start > 0
            else "Saknas"
        )

        # Hantera Karta
        coords = t.get("coords", [])
        trip_id = t.get("id", "unknown")
        cache_id = f"{trip_id}_{len(coords)}"

        img_html = "<span style='font-size:10px;color:#999;'>Saknas</span>"

        if map_cache_dir:
            # Konstruera den absoluta sökvägen till bilden i .config-mappen
            img_path = os.path.join(map_cache_dir, f"drive_{cache_id}.png")

            if os.path.exists(img_path):
                # Vi använder file:// för att webbläsaren/PDF-motorn ska hitta filen på disken
                abs_path = os.path.abspath(img_path)
                img_html = f'<img src="file://{abs_path}" class="map-img">'

        # Körtid
        tid = t.get("Total_Tid", 0)

        html += f"""
            <tr>
                <td>{img_html}</td>
                <td>{start_t}</td>
                <td><strong>Från:</strong> {t.get('Från', '')}<br><strong>Till:</strong> {t.get('Till', '')}</td>
                <td>{odo_str}</td>
                <td>{tid}</td>
                <td><span class="{tag_class}">{purpose}</span></td>
                <td><strong>{t.get('distance_km', 0):.1f}</strong></td>
            </tr>
        """

    html += """
            </tbody>
        </table>
    </body>
    </html>
    """

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
