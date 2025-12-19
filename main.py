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
# 1. DICCIONARIO MAESTRO (SOLUCIÓN PARA ERRORES HISTÓRICOS DE ORGANIZACIÓN)
# ==============================================================================
KNOWN_CORRECTIONS = {
    "slc": "Salt Lake City Arts Council",
    "gcac": "Greater Columbus Arts Council",
    "dca": "New Mexico Dept. of Cultural Affairs",
    "ssprd": "South Suburban Parks and Recreation",
    "arts": "Rhode Island State Council on the Arts",
    "akt-artful": "Florida State University (Art in State Buildings)",
    "artist must direct all": "City of Greenwood Village",
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
    def __init__(self, title, org, city, state, description, link, deadline, entry_fee, budget, eligibility, keywords, source, cafe_id, project_type):
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
        self.project_type = project_type # <--- NUEVO CAMPO DINÁMICO

    def to_row(self):
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            clean_text(self.deadline),       # A
            today,                           # B
            clean_text(self.title),          # C
            clean_text(self.org),            # D
            clean_text(self.city),           # E
            clean_text(self.state),          # F
            clean_text(self.project_type),   # G <--- YA NO ES FIJO
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
        "terrazzo", "lighting", "landscape", "community", "residency",
        "photography", "painting", "festival"
    ]
    found = set()
    text_lower = text.lower()
    for kw in keywords_list:
        if kw in text_lower:
            found.add(kw)
    return ", ".join(sorted(found))

# ==============================================================================
# 🔥 NUEVA LÓGICA: DETERMINAR TIPO DE PROYECTO DINÁMICAMENTE
# ==============================================================================
def determine_project_type(title, body, keywords):
    t_lower = title.lower()
    b_lower = body.lower()
    k_lower = keywords.lower()
    
    # 1. Detectar si es RFQ o RFP (Prioridad de Sufijo)
    suffix = ""
    if "rfq" in t_lower or "qualifications" in t_lower: suffix = " (RFQ)"
    elif "rfp" in t_lower or "proposals" in t_lower: suffix = " (RFP)"

    # 2. Clasificación por Título (Prioridad Máxima)
    if "mural" in t_lower: return "Mural" + suffix
    if "sculpture" in t_lower or "statue" in t_lower: return "Sculpture" + suffix
    if "residency" in t_lower or "resident" in t_lower: return "Residency"
    if "festival" in t_lower: return "Festival/Event"
    if "installation" in t_lower: return "Installation" + suffix
    if "mosaic" in t_lower: return "Mosaic" + suffix
    if "glass" in t_lower: return "Glass Art" + suffix
    if "memorial" in t_lower or "monument" in t_lower: return "Memorial/Monument" + suffix
    if "photography" in t_lower or "photo" in t_lower: return "Photography"

    # 3. Clasificación por Keywords (Si el título es genérico como "Call for Art")
    if "mural" in k_lower: return "Mural" + suffix
    if "sculpture" in k_lower: return "Sculpture" + suffix
    if "installation" in k_lower: return "Installation" + suffix
    
    # 4. Clasificación por Cuerpo del Texto (Búsqueda específica)
    if "muralist" in b_lower: return "Mural" + suffix
    
    # 5. Fallback Genérico
    return "Public Art" + suffix

# ==============================================================================
# 3. SCRAPER INTELIGENTE
# ==============================================================================
def run_scrapers():
    opportunities = []
    with sync_playwright() as p:
        print("--- 🚀 STARTING SCRAPER (Dynamic Types + Org Fix) ---")
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        context.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2,gif}", lambda route: route.abort())
        page = context.new_page()

        try:
            print("Loading list...")
            page.goto("https://artist.callforentry.org/festivals.php", timeout=60000)
            
            try:
                page.wait_for_selector("a[href*='festivals_unique_info.php']", timeout=10000)
            except:
                print("⚠️ Error loading list.")
                browser.close()
                return []
            
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
            
            print(f"--> Found {len(detail_links)} links. Processing...")

            for i, link in enumerate(detail_links):
                try:
                    cafe_id = link.split("ID=")[-1]
                    page.goto(link, timeout=20000, wait_until='domcontentloaded')
                    full_body = page.inner_text("body")

                    title = "Untitled"
                    title_elem = page.query_selector("div.fairname")
                    if title_elem:
                        raw_title = title_elem.inner_text()
                        title = raw_title.split('\n')[0].strip()

                    # --- LÓGICA DE ORGANIZACIÓN (MANTENIDA DEL ÚLTIMO FIX) ---
                    org = ""
                    match_email = re.search(r'Contact Email:\s*.*?@([\w\.\-]+)', full_body, re.IGNORECASE)
                    org_slug = ""
                    if match_email:
                        d = match_email.group(1)
                        if "gmail" not in d and "yahoo" not in d: org_slug = d.split('.')[0]

                    if org_slug.lower() in KNOWN_CORRECTIONS:
                        org = KNOWN_CORRECTIONS[org_slug.lower()]
                    
                    if not org:
                        page_tab_title = page.title()
                        if "-" in page_tab_title:
                            parts = page_tab_title.split("-")
                            if len(parts) >= 2:
                                possible_org = parts[-2].strip()
                                if "CaFÉ" not in possible_org and len(possible_org) > 2 and "Call for" not in possible_org:
                                    org = possible_org

                    if not org:
                        match_by = re.search(r'Presented by\s*[:\-]?\s*([A-Z][\w\s\.,&]+)', full_body, re.IGNORECASE)
                        if match_by:
                            cleaned = match_by.group(1).split('\n')[0]
                            if len(cleaned) < 60: org = cleaned.strip()

                    if not org and org_slug:
                        org = re.sub(r"(\w)([A-Z])", r"\1 \2", org_slug).title()

                    if not org or len(org) > 60: org = "Unknown Organization"
                    # ---------------------------------------------------------

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
                    
                    # 🔥 DETERMINAR TIPO DE PROYECTO DINÁMICO 🔥
                    project_type = determine_project_type(title, full_body, keywords)

                    if budget == "N/A": continue
                    numeric_val = extract_budget_numeric(budget)
                    if numeric_val < 3000: continue

                    print(f"✅ [{i+1}] Type: {project_type} | Org: {org} | {title[:20]}...")
                    
                    # Pasamos el nuevo campo project_type al constructor
                    opportunities.append(Opportunity(title, org, city, state, "", link, deadline, entry_fee, budget, eligibility, keywords, "CaFÉ", cafe_id, project_type))

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

    try:
        all_values = worksheet.get_all_values()
        url_to_row_map = {}
        for idx, row in enumerate(all_values):
            if len(row) > 12: 
                url_val = row[12]
                if "http" in url_val:
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
            
            # Actualizamos Organization (Columna D = 4)
            worksheet.update_cell(row_num, 4, op.org)
            
            # 🔥 Actualizamos TAMBIÉN Project Type (Columna G = 7)
            worksheet.update_cell(row_num, 7, op.project_type)
            
            # print(f"🔄 Updated Row {row_num}: Org & Type fixed.")
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
                <b>{item.org}</b> ({item.project_type})<br>
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