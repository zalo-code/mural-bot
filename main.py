import re
import os
import smtplib
import pytz
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Carga variables de entorno
load_dotenv()
SC_TZ = pytz.timezone('America/New_York')

# ==============================================================================
# 1. DICCIONARIO MAESTRO (SOLUCIÓN PARA ERRORES HISTÓRICOS CONOCIDOS)
# ==============================================================================
KNOWN_CORRECTIONS = {
    "slc": "Salt Lake City Arts Council",
    "gcac": "Greater Columbus Arts Council",
    "dca": "New Mexico Dept. of Cultural Affairs",
    "ssprd": "South Suburban Parks and Recreation",
    "arts": "Rhode Island State Council on the Arts",
    "akt-artful": "Florida State University (Art in State Buildings)",
    "artist must direct all": "City of Greenwood Village", # Arreglo fila 34
    "cityofkeller": "City of Keller",
    "ahhaa": "Ah Haa School for the Arts",
    "millcreekut": "Millcreek City",
    "ofallonmo": "City of O'Fallon",
    "swiftel": "Swiftel Center",
    "palmettobay-fl": "Village of Palmetto Bay",
    "alleganyarts": "Allegany Arts Council",
    "bluelinearts": "Blue Line Arts",
    "stagvillememorialproject": "The Stagville Memorial Project",
    "highdesertmuseum": "High Desert Museum",
    "artworkscincinnati": "ArtWorks Cincinnati",
    "msstate": "Mississippi State University",
    "sfsarch": "City of Wichita (SFS Architecture)",
    "landworksstudio": "City of Wichita (Landworks Studio)",
    "louisvilleco": "City of Louisville (CO)",
    "ocfl": "Orange County (FL)",
    "greenefellowship": "The Greene Fellowship",
    "city of denver": "Denver Arts & Venues"
}

# ==============================================================================
# 2. CLASE OPPORTUNITY
# ==============================================================================
class Opportunity:
    def __init__(self, title, org, city, state, description, link, deadline, entry_fee, budget, eligibility, keywords, source, cafe_id):
        self.title = title
        self.org = org
        self.city = city
        self.state = state
        self.description = description
        self.link = link
        self.deadline = deadline
        self.entry_fee = entry_fee
        self.budget = budget
        self.eligibility = eligibility
        self.keywords = keywords
        self.source = source
        self.cafe_id = cafe_id

    def to_row(self):
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            clean_text(self.deadline),       # A
            today,                           # B
            clean_text(self.title),          # C
            clean_text(self.org),            # D (ESTA ES LA CLAVE)
            clean_text(self.city),           # E
            clean_text(self.state),          # F
            "Public Art",                    # G
            clean_text(self.budget),         # H
            clean_text(self.entry_fee),      # I
            clean_text(self.eligibility),    # J
            clean_text(self.keywords),       # K
            "CaFÉ",                          # L
            self.link,                       # M
            "New",                           # N
            "",                              # O
            f"CAFE_{self.cafe_id}",          # P
            today                            # Q
        ]

def clean_text(text):
    if not text: return ""
    text = str(text)
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_budget_numeric(text):
    if not text or text.strip().upper() == "N/A": return 0
    matches = re.findall(r'\$?(\d{1,3}(?:,\d{3})*)', text)
    values = []
    for m in matches:
        clean_str = m.replace(',', '')
        if clean_str.isdigit():
            values.append(int(clean_str))
    return max(values) if values else 0

def extract_keywords(text):
    keywords_list = [
        "mural", "sculpture", "installation", "interactive", "kinetic", 
        "bronze", "mosaic", "glass", "steel", "monument", "memorial",
        "terrazzo", "lighting", "landscape", "community", "residency"
    ]
    found = set()
    text_lower = text.lower()
    for kw in keywords_list:
        if kw in text_lower:
            found.add(kw)
    return ", ".join(sorted(found))

# ==============================================================================
# 3. SCRAPER INTELIGENTE (LOGICA HÍBRIDA)
# ==============================================================================
def run_scrapers():
    opportunities = []
    with sync_playwright() as p:
        print("--- 🚀 STARTING PERFECT SCRAPER (CAFÉ) ---")
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        context.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2,gif}", lambda route: route.abort())
        page = context.new_page()

        try:
            print("Loading list...")
            page.goto("https://artist.callforentry.org/festivals.php", timeout=60000)
            
            # Esperar a que cargue la lista
            try:
                page.wait_for_selector("a[href*='festivals_unique_info.php']", timeout=10000)
            except:
                print("⚠️ Error loading list.")
                browser.close()
                return []
            
            # Recoger todos los enlaces
            links_elements = page.query_selector_all("a[href*='festivals_unique_info.php']")
            detail_links = []
            seen = set()
            for link in links_elements:
                url = link.get_attribute("href")
                if url and "ID=" in url:
                    if not url.startswith("http"): url = "https://artist.callforentry.org/" + url
                    if url not in seen:
                        seen.add(url)
                        detail_links.append(url)
            
            print(f"--> Found {len(detail_links)} links. Auditing ALL...")

            # BUCLE PRINCIPAL
            for i, link in enumerate(detail_links):
                try:
                    cafe_id = link.split("ID=")[-1]
                    page.goto(link, timeout=20000, wait_until='domcontentloaded')
                    full_body = page.inner_text("body")

                    # Título del Proyecto
                    title = "Untitled"
                    title_elem = page.query_selector("div.fairname")
                    if title_elem:
                        raw_title = title_elem.inner_text()
                        title = raw_title.split('\n')[0].strip()

                    # ========================================================
                    # 💡 LÓGICA MAESTRA PARA "ORGANIZATION"
                    # ========================================================
                    org = ""
                    strategy = ""

                    # Paso A: Obtener el "slug" del email como referencia (ej: 'cityofkeller')
                    # Esto nos sirve para comparar con el Diccionario
                    match_email = re.search(r'Contact Email:\s*.*?@([\w\.\-]+)', full_body, re.IGNORECASE)
                    org_slug = ""
                    if match_email:
                        d = match_email.group(1)
                        if "gmail" not in d and "yahoo" not in d:
                            org_slug = d.split('.')[0]

                    # 1. ESTRATEGIA DICCIONARIO (Prioridad Máxima - Arregla el Pasado)
                    # Comprobamos si el slug o el título contienen errores conocidos
                    if org_slug.lower() in KNOWN_CORRECTIONS:
                        org = KNOWN_CORRECTIONS[org_slug.lower()]
                        strategy = "Dictionary"
                    
                    # 2. ESTRATEGIA TÍTULO DE PESTAÑA (Prioridad Futura - Para Nuevos)
                    # El formato suele ser: "Nombre Proyecto - Nombre Organización - CaFÉ"
                    if not org:
                        page_tab_title = page.title()
                        if "-" in page_tab_title:
                            parts = page_tab_title.split("-")
                            if len(parts) >= 2:
                                possible_org = parts[-2].strip()
                                # Filtros de seguridad para no coger basura
                                if "CaFÉ" not in possible_org and len(possible_org) > 2 and "Call for" not in possible_org:
                                    org = possible_org
                                    strategy = "Page Title (Perfect)"

                    # 3. ESTRATEGIA VISUAL (Fallback - Busca 'Presented by')
                    if not org:
                        match_by = re.search(r'Presented by\s*[:\-]?\s*([A-Z][\w\s\.,&]+)', full_body, re.IGNORECASE)
                        if match_by:
                            cleaned = match_by.group(1).split('\n')[0]
                            if len(cleaned) < 60:
                                org = cleaned.strip()
                                strategy = "Presented By"

                    # 4. ESTRATEGIA SLUG LIMPIO (Último Recurso)
                    # Convierte "cityofkeller" -> "City Of Keller"
                    if not org and org_slug:
                        # Separa CamelCase y pone mayúsculas
                        org = re.sub(r"(\w)([A-Z])", r"\1 \2", org_slug)
                        org = org.title()
                        strategy = "Slug Cleaned"

                    # Fallback final por si acaso
                    if not org or len(org) > 60:
                        org = "Unknown Organization"

                    # ========================================================

                    # --- RESTO DE CAMPOS (IGUAL QUE ANTES) ---
                    state = ""
                    match_state = re.search(r'State:\s*(.*?)(?:\s+Budget|\n|$)', full_body, re.IGNORECASE)
                    if match_state: state = match_state.group(1).strip()

                    city = ""
                    match_city = re.search(r'City:\s*([A-Za-z\s\.]+)', full_body, re.IGNORECASE)
                    if match_city: city = match_city.group(1).strip()
                    else:
                        if state:
                            match_loc = re.search(r'in\s+([A-Z][a-z]+(?:[\s][A-Z][a-z]+)?),?\s*' + re.escape(state), full_body)
                            if match_loc: city = match_loc.group(1).strip()

                    budget = "N/A"
                    raw_match = re.search(r'(?:^|\n)\s*Budget\s*:\s*(.*?)(?:\n|$)', full_body, re.IGNORECASE)
                    if raw_match: budget = raw_match.group(1).strip()

                    entry_fee = "0"
                    if "No Entry Fee" in full_body or "Free" in full_body: entry_fee = "$0"
                    else:
                        fee_match = re.search(r'Entry Fee.*?:?\s*(\$[\d\.]+)', full_body, re.IGNORECASE)
                        if fee_match: entry_fee = fee_match.group(1)

                    deadline = "See Link"
                    dead_match = re.search(r'(?:Event Dates|Deadline).*?[:]\s*(.*?)(?:\n|$)', full_body, re.IGNORECASE)
                    if dead_match: deadline = dead_match.group(1).strip()

                    eligibility = ""
                    e_match = re.search(r'Eligibility Criteria\s*\n\s*(.*?)(?:\n\s*(?:Print|View|Legal)|$)', full_body, re.IGNORECASE | re.DOTALL)
                    if e_match: eligibility = e_match.group(1).strip()
                    
                    keywords = extract_keywords(full_body)

                    if budget == "N/A": continue
                    numeric_val = extract_budget_numeric(budget)
                    if numeric_val < 3000: continue

                    print(f"✅ [{i+1}] Org: {org} (Via {strategy}) | Budget: ${numeric_val}")
                    opportunities.append(Opportunity(title, org, city, state, "", link, deadline, entry_fee, budget, eligibility, keywords, "CaFÉ", cafe_id))

                except Exception as e:
                    print(f"[{i+1}] Error: {e}")
                    continue
        except Exception as e:
            print(f"General Error: {e}")
        browser.close()
    return opportunities

# ==============================================================================
# 4. GUARDADO EN SHEETS (ACTUALIZA LO VIEJO + AÑADE LO NUEVO)
# ==============================================================================
def get_gspread_client():
    return gspread.service_account(filename='credentials.json')

def save_to_sheets(opportunities):
    try:
        client = get_gspread_client()
        sheet = client.open("Mural Opportunities Bot") 
        worksheet = sheet.worksheet("Opportunities")
    except Exception as e:
        print(f"CRITICAL SHEETS ERROR: {e}")
        return []

    # Leemos todo para mapear URLs a Números de Fila
    try:
        all_values = worksheet.get_all_values()
        url_to_row_map = {}
        # Asumiendo que la columna 12 (índice 12, columna M) es Source URL
        for idx, row in enumerate(all_values):
            if len(row) > 12: 
                url_val = row[12]
                if "http" in url_val:
                    # Guardamos fila (idx + 1 porque Sheets empieza en 1)
                    url_to_row_map[url_val] = idx + 1 
    except:
        url_to_row_map = {}

    new_items = []
    rows_to_add = []

    print(f"Processing Sheet Update... ({len(url_to_row_map)} existing rows found)")

    for op in opportunities:
        if op.link in url_to_row_map:
            # --- SI YA EXISTE: ACTUALIZAR ---
            row_num = url_to_row_map[op.link]
            
            # Actualizamos SOLO la celda de Organization (Columna D = 4)
            # Esto arregla los errores antiguos como 'Slc' o 'Artist must direct all'
            worksheet.update_cell(row_num, 4, op.org)
            
            # Opcional: Actualizar Budget también por si acaso (Columna H = 8)
            # worksheet.update_cell(row_num, 8, op.budget)
            
            # print(f"🔄 Updated Row {row_num} with Organization: {op.org}")
        else:
            # --- SI ES NUEVO: AÑADIR ---
            rows_to_add.append(op.to_row())
            new_items.append(op)
    
    if rows_to_add:
        worksheet.append_rows(rows_to_add)
        print(f"✅ Added {len(rows_to_add)} new rows.")
    else:
        print("✅ Finished. No new rows, but existing rows were updated.")
    
    return new_items

# ==============================================================================
# 5. ENVIO DE EMAIL
# ==============================================================================
def send_email(new_items):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receivers_str = os.getenv("EMAIL_RECEIVER")
    sheet_id = os.getenv("SHEET_ID") 
    
    if not sender or not password or not new_items: 
        return

    receivers = receivers_str.split(",")
    today_str = datetime.now(SC_TZ).strftime("%m/%d/%Y")
    
    sheet_link = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    
    html = f"""
    <h2 style="color:#2c3e50;">🎨 New CaFÉ Opportunities ({len(new_items)})</h2>
    <p style="font-size:16px;">
        👉 <a href="{sheet_link}" style="background-color:#27ae60; color:white; padding:10px 15px; text-decoration:none; border-radius:5px; font-weight:bold;">
        OPEN GOOGLE SHEETS
        </a>
    </p>
    <hr>
    """

    for item in new_items[:15]: 
        html += f"""
        <div style="margin-bottom:15px; border-bottom:1px solid #eee;">
            <h3 style="margin:0;"><a href="{item.link}">{item.title}</a></h3>
            <p style="margin:5px 0; color:#555;">
                <b>{item.org}</b> <br>
                💰 {item.budget} | 📅 {item.deadline}
            </p>
        </div>
        """
    
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ", ".join(receivers)
    msg['Subject'] = f"Weekly Opportunities Report - {today_str}"
    msg.attach(MIMEText(html, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("📧 Email sent.")
    except Exception as e:
        print(f"Email Error: {e}")

if __name__ == "__main__":
    ops = run_scrapers()
    if ops:
        new_ops = save_to_sheets(ops)
        if new_ops:
            send_email(new_ops)