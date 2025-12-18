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

load_dotenv()
SC_TZ = pytz.timezone('America/New_York')

# --- CLASE DE OPORTUNIDAD ---
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
            clean_text(self.org),            # D
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

# --- SCRAPER (TU LÓGICA DE PLAYWRIGHT) ---
def run_scrapers():
    opportunities = []
    with sync_playwright() as p:
        print("--- 🚀 INICIANDO CRAWLER (CAFÉ) ---")
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        context.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2,gif}", lambda route: route.abort())
        page = context.new_page()

        try:
            print("Cargando lista...")
            page.goto("https://artist.callforentry.org/festivals.php", timeout=60000)
            try:
                page.wait_for_selector("a[href*='festivals_unique_info.php']", timeout=10000)
            except:
                print("⚠️ Error cargando lista.")
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
            
            print(f"--> Encontrados {len(detail_links)} enlaces.")

            for i, link in enumerate(detail_links):
                try:
                    cafe_id = link.split("ID=")[-1]
                    page.goto(link, timeout=20000, wait_until='domcontentloaded')
                    full_body = page.inner_text("body")

                    title = "Sin Título"
                    title_elem = page.query_selector("div.fairname")
                    if title_elem:
                        raw_title = title_elem.inner_text()
                        title = raw_title.split('\n')[0].strip()
                    if title == "Sin Título" or "CaFÉ" == title:
                        header_elem = page.query_selector(".header_text")
                        if header_elem: title = header_elem.inner_text().strip()

                    org = ""
                    match_org = re.search(r'Presented by\s*[:\-]?\s*(.*?)(?:\n|$|\.)', full_body, re.IGNORECASE)
                    if match_org: org = match_org.group(1).strip()
                    else:
                        match_email = re.search(r'Contact Email:\s*.*?@([\w\.\-]+)', full_body, re.IGNORECASE)
                        if match_email:
                            d = match_email.group(1)
                            if "gmail" not in d: org = d.split('.')[0].capitalize()

                    state = ""
                    match_state = re.search(r'State:\s*([A-Za-z\s]+)', full_body, re.IGNORECASE)
                    if match_state: state = match_state.group(1).strip()

                    city = ""
                    match_city = re.search(r'City:\s*([A-Za-z\s\.]+)', full_body, re.IGNORECASE)
                    if match_city: city = match_city.group(1).strip()
                    else:
                        if state:
                            match_loc = re.search(r'in\s+([A-Z][a-z]+(?:[\s][A-Z][a-z]+)?),?\s*' + re.escape(state), full_body)
                            if match_loc: city = match_loc.group(1).strip()
                            else:
                                match_loc_gen = re.search(r'located\s+(?:in|at)\s+([A-Z][a-z]+(?:[\s][A-Z][a-z]+)?)', full_body)
                                if match_loc_gen:
                                    possible_city = match_loc_gen.group(1).strip()
                                    if len(possible_city) > 2 and possible_city not in ["The", "This", "Smith", "Site"]:
                                        city = possible_city

                    budget = "N/A"
                    raw_match = re.search(r'(?:^|\n)\s*Budget\s*:\s*(.*?)(?:\n|$)', full_body, re.IGNORECASE)
                    if raw_match: budget = raw_match.group(1).strip()

                    entry_fee = "0"
                    if "No Entry Fee" in full_body or "Free" in full_body: entry_fee = "$0"
                    else:
                        fee_match = re.search(r'Entry Fee.*?:?\s*(\$[\d\.]+)', full_body, re.IGNORECASE)
                        if fee_match: entry_fee = fee_match.group(1)

                    deadline = "Ver Link"
                    dead_match = re.search(r'(?:Event Dates|Deadline).*?[:]\s*(.*?)(?:\n|$)', full_body, re.IGNORECASE)
                    if dead_match: deadline = dead_match.group(1).strip()

                    eligibility = ""
                    e_match = re.search(r'Eligibility Criteria\s*\n\s*(.*?)(?:\n\s*(?:Print|View|Legal)|$)', full_body, re.IGNORECASE | re.DOTALL)
                    if e_match: eligibility = e_match.group(1).strip()
                    
                    keywords = extract_keywords(full_body)

                    if budget == "N/A": continue
                    numeric_val = extract_budget_numeric(budget)
                    if numeric_val < 3000: continue

                    print(f"✅ [{i+1}] {title[:30]}... | ${numeric_val}")
                    opportunities.append(Opportunity(title, org, city, state, "", link, deadline, entry_fee, budget, eligibility, keywords, "CaFÉ", cafe_id))

                except Exception as e:
                    print(f"[{i+1}] Error: {e}")
                    continue
        except Exception as e:
            print(f"Error general: {e}")
        browser.close()
    return opportunities

# --- CONEXIÓN GOOGLE SHEETS ---
def get_gspread_client():
    return gspread.service_account(filename='credentials.json')

def save_to_sheets(opportunities):
    try:
        client = get_gspread_client()
        sheet = client.open("Mural Opportunities Bot") 
        worksheet = sheet.worksheet("Opportunities")
    except Exception as e:
        print(f"ERROR CRÍTICO SHEETS: {e}")
        return []

    try:
        existing_records = worksheet.get_all_values()
        existing_links = set()
        # Columna D (index 3) es link? No, en tu clase la URL es la M (index 12)
        # Revisando tu .to_row(): A,B,C,D,E,F,G,H,I,J,K,L,M -> M es el link (índice 12)
        for row in existing_records[1:]:
            if len(row) > 12: existing_links.add(row[12]) 
    except:
        existing_links = set()

    new_items = []
    rows_to_add = []

    for op in opportunities:
        if op.link not in existing_links:
            rows_to_add.append(op.to_row())
            new_items.append(op)
            existing_links.add(op.link)
    
    if rows_to_add:
        worksheet.append_rows(rows_to_add)
        print(f"✅ Guardadas {len(rows_to_add)} filas.")
    else:
        print("No hay filas nuevas.")
    
    return new_items

# --- EMAIL ---
def send_email(new_items):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receivers_str = os.getenv("EMAIL_RECEIVER")
    
    if not sender or not password: 
        print("⚠️ Faltan credenciales de email.")
        return

    receivers = receivers_str.split(",")
    today_str = datetime.now(SC_TZ).strftime("%m/%d/%Y")
    
    html = f"<h2>New CaFÉ Opportunities ({len(new_items)})</h2><hr>"
    for item in new_items[:20]: 
        html += f"""
        <div style="margin-bottom:15px; border-bottom:1px solid #ccc; padding-bottom:10px;">
            <h3 style="margin:0;"><a href="{item.link}">{item.title}</a></h3>
            <p style="margin:5px 0;">
                💰 <b>Budget:</b> {item.budget} | 🎟️ <b>Fee:</b> {item.entry_fee}<br>
                📅 <b>Deadline:</b> {item.deadline}
            </p>
        </div>
        """
    
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ", ".join(receivers)
    msg['Subject'] = f"🎨 CaFÉ Alert - {today_str}"
    msg.attach(MIMEText(html, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("📧 Email enviado.")
    except Exception as e:
        print(f"Error Email: {e}")

if __name__ == "__main__":
    ops = run_scrapers()
    if ops:
        new_ops = save_to_sheets(ops)
        if new_ops:
            send_email(new_ops)