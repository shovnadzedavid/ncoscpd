import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sqlite3
import os
import io
import bcrypt

# OCR-ისთვის (ტექსტის ამოცნობა სერთიფიკატებიდან) უსაფრთხო იმპორტი
try:
    import pypdf
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False

# PDF გენერაციისთვის უსაფრთხო იმპორტი და ქართული ფონტის რეგისტრაცია
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    
    pdfmetrics.registerFont(TTFont('DejaVuSans', '/Library/Fonts/Arial Unicode.ttf'))
    REPORTLAB_AVAILABLE = True
except Exception:
    try:
        pdfmetrics.registerFont(TTFont('DejaVuSans', '/System/Library/Fonts/Supplemental/Arial.ttf'))
        REPORTLAB_AVAILABLE = True
    except Exception:
        REPORTLAB_AVAILABLE = False

# --- ⚙️ აპლიკაციის კონფიგურაცია ---
st.set_page_config(
    page_title="NCOS CPD/Academic Programs Portal",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "edumed_core_healthcare.db"
LOG_FILE = "edumed_audit_logs.csv"
CREDITS_HISTORY_FILE = "edumed_credits_history.csv"
ALERTS_FILE = "edumed_broadcast_alerts.csv"
UPLOAD_DIR = "uploaded_certificates"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed_password):
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

# --- 🗄️ SQLite ბაზა და მიგრაცია ---
def init_database():
    try:
        conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                specialty TEXT,
                credits INTEGER,
                clinic TEXT,
                email TEXT,
                phone TEXT,
                notes TEXT,
                expiry_date TEXT,
                certificate_path TEXT,
                last_updated TEXT
            )
        ''')
        
        # ვამოწმებთ არსებობს თუ არა settings ცხრილი და აქვს თუ არა role სვეტი
        cursor.execute("PRAGMA table_info(settings)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if not columns:
            cursor.execute('''
                CREATE TABLE settings (
                    manager_login TEXT PRIMARY KEY,
                    display_name TEXT,
                    password TEXT,
                    role TEXT
                )
            ''')
        elif 'role' not in columns:
            # თუ ძველი ცხრილია და role სვეტი აკლია, უსაფრთხოდ განვანახლებთ
            cursor.execute("DROP TABLE settings")
            cursor.execute('''
                CREATE TABLE settings (
                    manager_login TEXT PRIMARY KEY,
                    display_name TEXT,
                    password TEXT,
                    role TEXT
                )
            ''')

        default_pass_hash = hash_password("123")
        default_users = {
            "laliivanishvili": {"name": "ლალი ივანიშვილი (გენერალური დირექტორი)", "pass": default_pass_hash, "role": "manager"},
            "nikolozchaduneli": {"name": "ნიკოლოზ ჩადუნელი (კლინიკური დირექტორი)", "pass": default_pass_hash, "role": "manager"},
            "davitshovnadze": {"name": "დავით შოვნაძე", "pass": default_pass_hash, "role": "manager"},
            "doctorportal": {"name": "ექიმთა პორტალი (საერთო)", "pass": default_pass_hash, "role": "doctor"}
        }
        for login, info in default_users.items():
            cursor.execute("""
                INSERT OR REPLACE INTO settings (manager_login, display_name, password, role) 
                VALUES (?, ?, ?, ?)
            """, (login, info["name"], info["pass"], info["role"]))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"ტექნიკური შეცდომა ბაზის ინიციალიზაციისას: {e}")

init_database()

CLINICS_LIST = [
    "კ.ერისთავის სახელობის ქირურგიის ეროვნული ცენტრი",
    "ახალი სიცოცხლე",
    "ქირურგიის ეროვნული ცენტრის ბათუმის კლინიკა"
]

@st.cache_data(ttl=60, show_spinner=False)
def fetch_doctors():
    try:
        conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
        df = pd.read_sql("SELECT * FROM doctors", conn)
        conn.close()
        if not df.empty:
            if "expiry_date" not in df.columns:
                df["expiry_date"] = "2028-12-31"
            if "phone" not in df.columns:
                df["phone"] = "+995 500 000 000"
            if "email" not in df.columns:
                df["email"] = "doctor@edumed.ge"
            if "notes" not in df.columns:
                df["notes"] = "შენიშვნა არ არის"
            if "certificate_path" not in df.columns:
                df["certificate_path"] = ""
        return df.to_dict("records")
    except Exception as e:
        st.error(f"ექიმების ბაზის წაკითხვის შეფერხება: {e}")
        return []

def log_action(actor, action_type, target_name, details):
    try:
        log_record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "manager": actor,
            "action": action_type,
            "target_doctor": target_name,
            "details": details,
            "ip_session": "Secure-Internal-Node"
        }
        if os.path.exists(LOG_FILE):
            df_log = pd.read_csv(LOG_FILE)
            df_log = pd.concat([df_log, pd.DataFrame([log_record])], ignore_index=True)
        else:
            df_log = pd.DataFrame([log_record])
        df_log.to_csv(LOG_FILE, index=False, encoding='utf-8-sig')
    except Exception:
        pass

def log_credits_history(doctor_name, old_credits, new_credits, actor, reason):
    try:
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "doctor": doctor_name,
            "old_credits": old_credits,
            "new_credits": new_credits,
            "manager": actor,
            "reason": reason
        }
        if os.path.exists(CREDITS_HISTORY_FILE):
            df_h = pd.read_csv(CREDITS_HISTORY_FILE)
            df_h = pd.concat([df_h, pd.DataFrame([record])], ignore_index=True)
        else:
            df_h = pd.DataFrame([record])
        df_h.to_csv(CREDITS_HISTORY_FILE, index=False, encoding='utf-8-sig')
    except Exception:
        pass

def get_original_creator(doc_name):
    if os.path.exists(LOG_FILE):
        try:
            df_logs = pd.read_csv(LOG_FILE)
            reg_logs = df_logs[(df_logs["action"] == "რეგისტრაცია") & (df_logs["target_doctor"].str.strip().str.lower() == doc_name.strip().lower())]
            if not reg_logs.empty:
                first_reg = reg_logs.iloc[0]
                return first_reg["manager"], first_reg["timestamp"]
        except Exception:
            pass
    return "უცნობი მენეჯერი", "უცნობი დრო"

def extract_text_from_pdf(uploaded_file):
    if not PYPDF_AVAILABLE:
        return "OCR მოდული მიუწვდომელია"
    try:
        reader = pypdf.PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        return ""

def generate_executive_pdf_report(clinic_name, doctors_list, manager_name):
    if not REPORTLAB_AVAILABLE:
        return None
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        elements = []
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('GeorgianTitle', parent=styles['Heading1'], fontName='DejaVuSans', fontSize=18, textColor=colors.HexColor("#1e1b4b"), spaceAfter=10, alignment=1)
        subtitle_style = ParagraphStyle('GeorgianSubTitle', parent=styles['Normal'], fontName='DejaVuSans', fontSize=10, textColor=colors.HexColor("#475569"), spaceAfter=20, alignment=1)
        cell_style = ParagraphStyle('GeorgianCell', parent=styles['Normal'], fontName='DejaVuSans', fontSize=9, alignment=1)
        header_cell_style = ParagraphStyle('GeorgianHeaderCell', parent=styles['Normal'], fontName='DejaVuSans', fontSize=9, textColor=colors.whitesmoke, alignment=1)

        elements.append(Paragraph(f"<b>NCOS CPD/Academic Programs Portal — Clinical Audit Report</b>", title_style))
        elements.append(Paragraph(f"<b>კლინიკა / დეპარტამენტი:</b> {clinic_name}<br/><b>გენერირებულია:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} | <b>პასუხისმგებელი:</b> {manager_name}", subtitle_style))
        elements.append(Spacer(1, 10))
        
        table_data = [[
            Paragraph("<b>№</b>", header_cell_style), 
            Paragraph("<b>ექიმი</b>", header_cell_style), 
            Paragraph("<b>სპეციალობა</b>", header_cell_style), 
            Paragraph("<b>კრედიტები</b>", header_cell_style), 
            Paragraph("<b>სტატუსი</b>", header_cell_style),
            Paragraph("<b>ლიცენზიის ვადა</b>", header_cell_style)
        ]]
        for idx, doc_item in enumerate(doctors_list, 1):
            c = doc_item.get('credits', 0)
            status_str = "სრულ წესრიგშია" if c >= 30 else ("ყურადღებმისაქცევი" if c >= 16 else "კრიტიკული")
            table_data.append([
                Paragraph(str(idx), cell_style),
                Paragraph(str(doc_item.get('name', '')), cell_style),
                Paragraph(str(doc_item.get('specialty', '')), cell_style),
                Paragraph(str(c), cell_style),
                Paragraph(status_str, cell_style),
                Paragraph(str(doc_item.get('expiry_date', '')), cell_style)
            ])
        t = Table(table_data, colWidths=[25, 140, 110, 60, 95, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#4f46e5")),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8fafc")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ]))
        elements.append(t)
        doc.build(elements)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception:
        return None

# --- ავტორიზაცია, სესია (10 წუთიანი ლიმიტი) და Screen Lock მექანიზმი ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "screen_locked" not in st.session_state:
    st.session_state.screen_locked = False
if "current_user" not in st.session_state:
    st.session_state.current_user = None
if "current_role" not in st.session_state:
    st.session_state.current_role = None
if "login_time" not in st.session_state:
    st.session_state.login_time = None

if st.session_state.logged_in and st.session_state.login_time:
    if datetime.now() - st.session_state.login_time > timedelta(minutes=10):
        st.session_state.logged_in = False
        st.session_state.screen_locked = False
        st.session_state.current_user = None
        st.warning("⏱️ უსაფრთხოების მიზნით 10-წუთიანი უმოქმედობის სესიის ვადა ამოიწურა. გთხოვთ გაიაროთ ავტორიზაცია თავიდან.")
        st.rerun()

def render_login(is_lock_screen=False):
    title_text = "🔒 ეკრანი დაბლოკილია" if is_lock_screen else "NCOS CPD/Academic Programs Portal"
    subtitle_text = f"მენეჯერი: {st.session_state.current_user} (შეიყვანეთ პაროლი გასახსნელად)" if is_lock_screen else "სამედიცინო პერსონალისა და კრედიტების მართვის სივრცე"
    
    # სრულიად ცენტრალიზებული ბლოკი ლოგინის და პაროლის ველების თავზე
    col_l1, col_l2, col_l3 = st.columns([1, 1.6, 1])
    with col_l2:
        st.markdown(f"""
            <div class='login-card-container'>
                <div style='text-align: center; font-size: 46px; margin-bottom: 12px; filter: drop-shadow(0 0 15px rgba(99,102,241,0.6));'>{'🔐' if is_lock_screen else '🧬'}</div>
                <div class='login-title'>{title_text}</div>
                <div class='login-subtitle'>{subtitle_text}</div>
            </div>
        """, unsafe_allow_html=True)
        
        try:
            conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
            df_sets = pd.read_sql("SELECT * FROM settings", conn)
            conn.close()
        except:
            df_sets = pd.DataFrame()
        
        if is_lock_screen:
            login_val = ""
            for l_key, l_val in [("ლალი ივანიშვილი", "laliivanishvili"), ("ნიკოლოზ ჩადუნელი", "nikolozchaduneli"), ("დავით შოვნაძე", "davitshovnadze")]:
                if l_key in str(st.session_state.current_user):
                    login_val = l_val
            password_input = st.text_input("🔑 შეიყვანეთ პაროლი:", type="password", key="lock_pass_field")
            
            if st.button("🔓 ეკრანის განბლოკვა", use_container_width=True):
                user_row = df_sets[df_sets["manager_login"] == login_val]
                if not user_row.empty and check_password(password_input, user_row["password"].values[0]):
                    st.session_state.screen_locked = False
                    st.success("✅ ეკრანი განბლოკილია!")
                    st.rerun()
                else:
                    st.error("❌ არასწორი პაროლი!")
        else:
            login_input = st.text_input("👤 ლოგინი:", placeholder="შეიყვანეთ ლოგინი ლათინურად", key="login_field", autocomplete="off")
            password_input = st.text_input("🔑 პაროლი:", type="password", key="pass_field", autocomplete="new-password")
            
            st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
            if st.button("🚀 სისტემაში შესვლა", use_container_width=True):
                if not df_sets.empty:
                    user_row = df_sets[df_sets["manager_login"] == login_input.strip().lower()]
                    if not user_row.empty:
                        actual_pass_hash = user_row["password"].values[0]
                        display_name = user_row["display_name"].values[0]
                        user_role = user_row["role"].values[0] if "role" in user_row.columns else "manager"
                        
                        if check_password(password_input, actual_pass_hash):
                            st.session_state.logged_in = True
                            st.session_state.screen_locked = False
                            st.session_state.current_user = display_name
                            st.session_state.current_role = user_role
                            st.session_state.login_time = datetime.now()
                            st.success("✅ ავტორიზაცია წარმატებულია!")
                            st.rerun()
                        else:
                            st.error("❌ არასწორი პაროლი!")
                    else:
                        st.error("❌ მითითებული ლოგინი არ მოიძებნა ბაზაში!")

if not st.session_state.logged_in:
    render_login(is_lock_screen=False)
    st.stop()

if st.session_state.screen_locked:
    render_login(is_lock_screen=True)
    st.stop()

# --- 💎 ULTRA-PREMIUM UI / CSS / ANIMATIONS დიზაინი ---
st.markdown("""
    <style>
        .stApp {
            background: radial-gradient(circle at 15% 15%, #070d1d 0%, #030712 50%, #020408 100%) !important;
            color: #f8fafc !important;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .main { background: transparent !important; }
        .header-card {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.85) 100%);
            padding: 36px; border-radius: 24px;
            border: 1px solid rgba(129, 140, 248, 0.25); border-left: 8px solid #6366f1;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            margin-bottom: 35px; backdrop-filter: blur(25px);
        }
        .login-card-container {
            background: linear-gradient(145deg, rgba(15, 23, 42, 0.85) 0%, rgba(7, 13, 29, 0.95) 100%);
            padding: 35px 30px; border-radius: 24px; border: 1px solid rgba(129, 140, 248, 0.3);
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.8), 0 0 30px rgba(99, 102, 241, 0.15);
            backdrop-filter: blur(30px); width: 100%; margin: 10px auto 20px auto; position: relative; overflow: hidden; text-align: center;
        }
        .login-card-container::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px;
            background: linear-gradient(90deg, #4f46e5, #6366f1, #a855f7); box-shadow: 0 0 15px #6366f1;
        }
        .login-title { color: #ffffff; text-align: center; font-size: 24px; font-weight: 800; margin-bottom: 8px; }
        .login-subtitle { color: #94a3b8; text-align: center; font-size: 14px; margin-bottom: 0px; line-height: 1.4; }
        .board-card {
            background: linear-gradient(145deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.7) 100%);
            border: 1px solid rgba(129, 140, 248, 0.2); padding: 30px; border-radius: 22px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4); height: 100%; display: flex; flex-direction: column; justify-content: space-between; backdrop-filter: blur(20px);
        }
        .roi-card {
            background: linear-gradient(135deg, rgba(79, 70, 229, 0.2) 0%, rgba(15, 23, 42, 0.8) 100%);
            border: 1px solid rgba(129, 140, 248, 0.35); padding: 28px; border-radius: 20px; margin-bottom: 25px; box-shadow: 0 15px 35px rgba(0,0,0,0.5);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #030712 0%, #070d1d 50%, #0f172a 100%) !important;
            border-right: 1px solid rgba(129, 140, 248, 0.2); padding-top: 20px;
        }
        section[data-testid="stSidebar"] .stRadio label {
            background: rgba(30, 41, 59, 0.4); border-radius: 14px; padding: 12px 16px !important;
            border: 1px solid rgba(129, 140, 248, 0.15); width: 100% !important; display: flex !important; align-items: center !important; cursor: pointer; transition: all 0.3s ease;
        }
        section[data-testid="stSidebar"] .stRadio label:hover {
            background: linear-gradient(135deg, rgba(79, 70, 229, 0.3) 0%, rgba(99, 102, 241, 0.4) 100%);
            border-color: rgba(129, 140, 248, 0.6); transform: translateX(6px);
        }
        .stTabs [data-baseweb="tab"] {
            height: 58px !important; background: rgba(30, 41, 59, 0.45) !important;
            border-radius: 14px 14px 0px 0px !important; padding: 14px 26px !important; font-size: 15px !important; font-weight: 700 !important; color: #94a3b8 !important; border: 1px solid rgba(129, 140, 248, 0.2) !important;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important; color: white !important; box-shadow: 0 10px 30px rgba(99, 102, 241, 0.5);
        }
        .stButton > button {
            border-radius: 14px !important; font-weight: 700 !important; font-size: 16px !important; padding: 13px 26px !important;
            border: 1px solid rgba(129, 140, 248, 0.5) !important; background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important; color: white !important; box-shadow: 0 10px 25px rgba(79, 70, 229, 0.4);
        }
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%);
            padding: 22px; border-radius: 20px; border: 1px solid rgba(129, 140, 248, 0.25); box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        }
    </style>
""", unsafe_allow_html=True)

# --- 🌟 ზედა ჰედერი ---
st.markdown(f"""
    <div class='header-card'>
        <div style='font-size: 13px; color: #818cf8; margin-bottom: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px;'>უწყვეტი სამედიცინო განათლების მართვის პანელი</div>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 20px;'>
            <div>
                <h2 style='color: white; margin: 0; font-size: 34px; font-weight: 800; letter-spacing: -0.5px;'>🧬 NCOS CPD/Academic Programs Portal</h2>
                <p style='color: #94a3b8; margin: 8px 0 0 0; font-size: 16px;'>კლინიკური მართვა, პერსონალის კვალიფიკაცია და რისკების კონტროლი</p>
            </div>
            <div>
                <span style='background: linear-gradient(135deg, #4f46e5, #6366f1); color: white; padding: 12px 26px; border-radius: 30px; font-size: 15px; font-weight: 700; box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5);'>👤 აქტიური მენეჯერი: <b>{st.session_state.current_user}</b></span>
            </div>
        </div>
    </div>
""", unsafe_allow_html=True)

# --- 🧭 საიდბარი & Lock Screen კონტროლი ---
st.sidebar.markdown(f"**👤 მომხმარებელი:** {st.session_state.current_user}")
if st.sidebar.button("🔒 ეკრანის დაბლოკვა (Lock)", use_container_width=True):
    st.session_state.screen_locked = True
    st.rerun()

st.sidebar.markdown("---")

if st.session_state.current_role == "doctor":
    menu_options = ["👤 ექიმის პირადი პორტალი", "📚 აკრედიტებული კურსები"]
else:
    menu_options = [
        "მთავარი დაფა & ანალიტიკა", 
        "🩺 სპეციალობების & კრედიტების მატრიცა",
        "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი",
        "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა",
        "ექიმების რეესტრი", 
        "კლინიკები", 
        "აუდიტის ჟურნალი", 
        "სეთინგები და პაროლები",
        "📄 OCR სერთიფიკატების სკანერი"
    ]

menu_selection = st.sidebar.radio("", menu_options)

st.sidebar.markdown("---")
if st.sidebar.button("🚪 სისტემიდან გასვლა", use_container_width=True, type="secondary"):
    st.session_state.logged_in = False
    st.session_state.screen_locked = False
    st.session_state.current_user = None
    st.session_state.current_role = None
    st.session_state.login_time = None
    st.rerun()


# =========================================================================
# 👤 ექიმის პირადი პორტალი
# =========================================================================
if menu_selection == "👤 ექიმის პირადი პორტალი":
    st.subheader("👤 ექიმის პირადი პორტალი (Doctor Self-Service)")
    st.markdown("<p style='color: #94a3b8;'>მოძებნეთ თქვენი სახელი და შეამოწმეთ მიმდინარე კრედიტქულები და ლიცენზიის ვადა.</p>", unsafe_allow_html=True)
    
    all_docs_portal = fetch_doctors()
    if all_docs_portal:
        doc_names_list = [d["name"] for d in all_docs_portal]
        my_name = st.selectbox("აირჩიეთ თქვენი სახელი რეესტრიდან:", doc_names_list)
        my_profile = next((d for d in all_docs_portal if d["name"] == my_name), None)
        
        if my_profile:
            c1, c2, c3 = st.columns(3)
            c1.metric("მიმდინარე კრედიტები", my_profile["credits"])
            c2.metric("კლინიკა", my_profile["clinic"])
            c3.metric("ლიცენზიის ვადა", my_profile["expiry_date"])
            
            st.markdown("---")
            st.markdown("### 📋 პირადი დეტალები და სტატუსი")
            st.write(f"**სპეციალობა:** {my_profile['specialty']}")
            st.write(f"**ელ-ფოსტა:** {my_profile['email']}")
            st.write(f"**ტელეფონი:** {my_profile['phone']}")
            st.write(f"**შენიშვნა:** {my_profile['notes']}")
            
            cred_val = my_profile["credits"]
            if cred_val >= 30:
                st.success("🟢 თქვენი კრედიტების ბალანსი სრულ წესრიგშია (≥30 ქულა).")
            elif 16 <= cred_val <= 29:
                st.warning("🟡 ყურადღება: თქვენი ქულები რისკ-ზონაშია (16-29). გთხოვთ გაიაროთ დამატებითი ტრენინგი.")
            else:
                st.error("🔴 კრიტიკული ზონა (<15 ქულა)! გთხოვთ დაუყოვნებლივ მიმართოთ კლინიკურ მენეჯმენტს.")
    else:
        st.info("ექიმების ბაზა ცარიელია.")


# =========================================================================
# 📚 აკრედიტებული კურსები
# =========================================================================
elif menu_selection == "📚 აკრედიტებული კურსები":
    st.subheader("📚 აკრედიტებული სამედიცინო კურსები და კონფერენციები")
    st.markdown("<p style='color: #94a3b8;'>აქ წარმოდგენილია რეკომენდებული კურსები კრედიტქულების ასამაღლებლად.</p>", unsafe_allow_html=True)
    
    courses = [
        {"name": "ფუნდამენტური საზოგადოებრივი ჯანდაცვა 2026", "credits": 10, "provider": "საქართველოს სამედიცინო აკადემია", "type": "Online"},
        {"name": "კლინიკური კომუნიკაციებისა და პაციენტთა უსაფრთხოების მართვა", "credits": 15, "provider": "ევროპული სამედიცინო ცენტრი", "type": "Hybrid"},
        {"name": "გადაუდებელი დახმარების განახლებული პროტოკოლები", "credits": 20, "provider": "ერისთავის სახელობის ცენტრი", "type": "Offline"}
    ]
    for c in courses:
        st.markdown(f"""
            <div class='roi-card'>
                <h4 style='color: #818cf8; margin-top:0;'>📖 {c['name']}</h4>
                <p style='color: #f8fafc; margin: 4px 0;'><b>მომწოდებელი:</b> {c['provider']} | <b>ფორმატი:</b> {c['type']}</p>
                <p style='color: #34d399; font-weight: 700; margin-bottom:0;'>✨ მოსაპოვებელი კრედიტები: +{c['credits']} ქულა</p>
            </div>
        """, unsafe_allow_html=True)


# =========================================================================
# 🩺 სპეციალობების & კრედიტების მატრიცა
# =========================================================================
elif menu_selection == "🩺 სპეციალობების & კრედიტების მატრიცა":
    st.subheader("🩺 სპეციალობების კვალიფიკაციისა და კრედიტების მატრიცა")
    st.markdown("<p style='color: #94a3b8;'>კლინიკური დეფიციტებისა და სპეციალობების მიხედვით საშუალო კრედიტების მიმოხილვა.</p>", unsafe_allow_html=True)

    doctors_data = fetch_doctors()
    if doctors_data:
        df_spec = pd.DataFrame(doctors_data)
        spec_summary = df_spec.groupby("specialty").agg(
            ექიმების_რაოდენობა=('name', 'count'),
            საშუალო_კრედიტი=('credits', 'mean'),
            მინიმალური_კრედიტი=('credits', 'min')
        ).reset_index()
        spec_summary["საშუალო_კრედიტი"] = spec_summary["საშუალო_კრედიტი"].round(1)
        
        st.dataframe(spec_summary, use_container_width=True, hide_index=True)
        st.bar_chart(spec_summary.set_index("specialty")["საშუალო_კრედიტი"])
    else:
        st.info("მონაცემები არ მოიძებნა.")


# =========================================================================
# 🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი
# =========================================================================
elif menu_selection == "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი":
    st.subheader("🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი")
    st.markdown("<p style='color: #94a3b8;'>რისკ-ზონაში მყოფი ექიმების ავტომატური სია და მართვის პანელი.</p>", unsafe_allow_html=True)

    doctors_data = fetch_doctors()
    if doctors_data:
        df_risk_mgr = pd.DataFrame(doctors_data)
        df_risk_filtered = df_risk_mgr[df_risk_mgr["credits"] < 30]
        
        if not df_risk_filtered.empty:
            st.warning(f"⚠️ ყურადღება: რისკ-ზონაში ფიქსირდება **{len(df_risk_filtered)}** ექიმი (<30 ქულა).")
            st.dataframe(df_risk_filtered[["name", "specialty", "credits", "clinic", "email", "notes"]], use_container_width=True, hide_index=True)
            
            if REPORTLAB_AVAILABLE:
                if st.button("📄 კლინიკური რისკ-ანგარიშის გენერაცია (PDF)", use_container_width=True):
                    pdf_b = generate_executive_pdf_report("რისკ-ჯგუფების კლინიკური აუდიტი", df_risk_filtered.to_dict("records"), st.session_state.current_user)
                    if pdf_b:
                        st.download_button("📥 გადმოწერა PDF", data=pdf_b, file_name="Clinical_Risk_Report.pdf", mime="application/pdf", use_container_width=True)
        else:
            st.success("✅ ყველა ექიმის კრედიტქულა ნორმის ფარგლებშია. კლინიკური რისკები არ ფიქსირდება.")
    else:
        st.info("მონაცემები არ მოიძებნა.")


# =========================================================================
# 📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა
# =========================================================================
elif menu_selection == "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა":
    st.subheader("📈 ექიმის კრედიტგროვების ისტორიისა და დინამიკის ზედამხედველობა")
    st.markdown("<p style='color: #94a3b8;'>თითოეული ექიმის პროგრესი და ქულების ცვლილების ისტორია დროში.</p>", unsafe_allow_html=True)

    if os.path.exists(CREDITS_HISTORY_FILE):
        try:
            df_hist = pd.read_csv(CREDITS_HISTORY_FILE)
            if not df_hist.empty:
                doc_list_h = df_hist["doctor"].unique().tolist()
                selected_doc_h = st.selectbox("აირჩიეთ ექიმი ისტორიის სანახავად:", doc_list_h)
                
                doc_specific_history = df_hist[df_hist["doctor"] == selected_doc_h]
                st.dataframe(doc_specific_history.sort_values(by="timestamp", ascending=False), use_container_width=True, hide_index=True)
            else:
                st.info("ისტორიის ჟურნალი ცარიელია.")
        except:
            st.info("ისტორიის წაკითხვის შეფერხება.")
    else:
        st.info("ქულების ცვლილების ისტორია ჯერ არ ფიქსირდება ბაზაში.")


# =========================================================================
# 📄 OCR სერთიფიკატების სკანერი
# =========================================================================
elif menu_selection == "📄 OCR სერთიფიკატების სკანერი":
    st.subheader("📄 ინტელექტუალური OCR სერთიფიკატების სკანერი")
    st.markdown("<p style='color: #94a3b8;'>ატვირთეთ ექიმის სერთიფიკატი (PDF), რომ სისტემამ ავტომატურად წაიკითხოს ტექსტი.</p>", unsafe_allow_html=True)
    
    ocr_file = st.file_uploader("აირჩიეთ სერთიფიკატი (.pdf):", type=["pdf"])
    if ocr_file is not None:
        with st.spinner("🔍 მიმდინარეობს დოკუმენტიდან ტექსტის ამოკითხვა (OCR)..."):
            extracted_text = extract_text_from_pdf(ocr_file)
        
        if extracted_text:
            st.success("✅ დოკუმენტი წარმატებით დამუშავდა!")
            st.text_area("ამოკითხული ტექსტი დოკუმენტიდან:", extracted_text, height=200)
        else:
            st.warning("⚠️ ტექსტის ამოკითხვა ვერ მოხერხდა.")


# =========================================================================
# 📊 მთავარი დაფა & ანალიტიკა
# =========================================================================
elif menu_selection == "მთავარი დაფა & ანალიტიკა":
    st.subheader("📊 მთავარი დაფა — ანალიტიკა და ვიზუალური დიაგრამები")
    
    doctors_data = fetch_doctors()
    df_main = pd.DataFrame(doctors_data)
    
    if not df_main.empty:
        low_count = len(df_main[df_main["credits"] < 30])
    else:
        low_count = 0

    col_1, col_2, col_3 = st.columns(3)
    col_1.metric("რეგისტრირებულ ექიმთა საერთო რაოდენობა", len(df_main))
    col_2.metric("ჩართული კლინიკური ცენტრები", len(CLINICS_LIST))
    col_3.metric("ექიმები (<30 კრედიტით)", low_count)
    
    st.markdown("---")

    if not df_main.empty:
        st.markdown("### 📈 დეპარტამენტებისა და სპეციალობების ანალიტიკური დიაგრამები")
        g_col1, g_col2 = st.columns(2)
        with g_col1:
            st.markdown("#### 🏥 კრედიტების განაწილება კლინიკების მიხედვით")
            clinic_credits = df_main.groupby("clinic")["credits"].sum()
            st.bar_chart(clinic_credits)
            
        with g_col2:
            st.markdown("#### 🩺 ექიმების რაოდენობა სპეციალობების მიხედვით")
            spec_counts = df_main["specialty"].value_counts()
            st.bar_chart(spec_counts)
    
    st.markdown("---")
    board_col1, board_col2 = st.columns(2)

    with board_col1:
        st.markdown("<div class='board-card'>", unsafe_allow_html=True)
        st.markdown("""
            <div>
                <h3>⚡ ექიმებისთვის კრედიტქულების დამატება</h3>
                <p style='color: #94a3b8; font-size: 14px;'>აირჩიეთ ექიმი და სწრაფად მიანიჭეთ ან ჩამოაჭერით კრედიტქულები.</p>
            </div>
        """, unsafe_allow_html=True)
        
        if doctors_data:
            doc_names = [d["name"] for d in doctors_data]
            selected_doc_for_points = st.selectbox("აირჩიეთ ექიმი:", doc_names, key="main_pts_select_board")
            target_doc_main = next((d for d in doctors_data if d["name"] == selected_doc_for_points), None)
            
            if target_doc_main:
                st.markdown(f"**მიმდინარე ბალანსი:** `{target_doc_main['credits']}` ქულა | **კლინიკა:** {target_doc_main['clinic']}")
                
                with st.form("main_points_form_board"):
                    pts_change = st.number_input("ქულების ოდენობა:", min_value=1, max_value=50, value=10)
                    action_type = st.radio("ოპერაცია:", ["ქულების დამატება (+)", "ქულების წაშლა (-)"], horizontal=True)
                    reason_text = st.text_input("ოპერაციის მიზეზი / სასწავლო კურსი:")
                    submit_main_pts = st.form_submit_button("💾 ქულების განახლება ბაზაში", use_container_width=True)
                    
                    if submit_main_pts:
                        old_creds = target_doc_main['credits']
                        new_credits = old_creds + pts_change if "დამატება" in action_type else max(0, old_creds - pts_change)
                        try:
                            conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                            cursor = conn.cursor()
                            cursor.execute("UPDATE doctors SET credits = ?, last_updated = ? WHERE id = ?", (new_credits, str(datetime.now()), target_doc_main['id']))
                            conn.commit()
                            conn.close()
                            
                            action_label = "ქულების დამატება" if "დამატება" in action_type else "ქულების წაშლა"
                            log_action(st.session_state.current_user, action_label, target_doc_main['name'], f"რაოდენობა: {pts_change}, მიზეზი: {reason_text}. ახალი ჯამი: {new_credits}")
                            log_credits_history(target_doc_main['name'], old_creds, new_credits, st.session_state.current_user, reason_text)
                            st.cache_data.clear()
                            st.success("✅ კრედიტქულები წარმატებით განახლდა!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"შეცდომა ბაზასთან მიმართვისას: {e}")
        else:
            st.info("ექიმები არ არის რეგისტრირებული.")
        st.markdown("</div>", unsafe_allow_html=True)

    with board_col2:
        st.markdown("<div class='board-card'>", unsafe_allow_html=True)
        st.markdown("""
            <div>
                <h3>📌 ინფორმაცია მონიტორინგზე</h3>
                <p style='color: #94a3b8; font-size: 15px;'>რისკის ქვეშ მყოფი ექიმების სია (<30 კრედიტი) გადატანილია ცალკე ქვე-ტაბში:</p>
                <p style='color: #818cf8; font-size: 15px; font-weight: 700;'>👉 ექიმების რეესტრი ➔ „🚨 რისკის ქვეშ მყოფი ექიმები“</p>
            </div>
        """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


# =========================================================================
# ექიმების რეესტრი
# =========================================================================
elif menu_selection == "ექიმების რეესტრი":
    st.subheader("📋 ექიმების რეესტრი, რეგისტრაცია და მართვა")
    
    tab_reg, tab_upload, tab_list, tab_risk, tab_edit = st.tabs([
        "➕ ინდივიდუალური რეგისტრაცია", 
        "📤 ფაილიდან (Excel/CSV) იმპორტი",
        "🔍 ექიმების სია და ფილტრაცია", 
        "🚨 რისკის ქვეშ მყოფი ექიმები (<30 კრედიტი)",
        "✏️ მრავალმხრივი რედაქტირება & მონიშვნით წაშლა"
    ])
    
    with tab_reg:
        st.markdown("#### 📝 ახალი პროფესიონალის ინდივიდუალური დამატება")
        with st.form("register_doctor_form"):
            doc_name = st.text_input("ექიმის სახელი და გვარი:")
            doc_clinic = st.selectbox("მიმაგრებული კლინიკა:", CLINICS_LIST)
            doc_spec = st.text_input("სპეციალობა:", value="ოჯახის ექიმი")
            doc_email = st.text_input("ელ-ფოსტა:", value="doctor@edumed.ge")
            doc_phone = st.text_input("ტელეფონი:", value="+995 599 00 00 00")
            doc_credits = st.number_input("საწყისი კრედიტქულები:", min_value=0, value=15)
            doc_notes = st.text_input("შენიშვნა:", value="სრული მზადყოფნა")
            
            submit_reg = st.form_submit_button("🚀 რეგისტრაცია", use_container_width=True)
            if submit_reg:
                cleaned_name = doc_name.strip()
                if not cleaned_name or not doc_spec.strip():
                    st.error("⚠️ გთხოვთ, შეავსოთ სახელი და სპეციალობა სავალდებულოდ!")
                elif "@" not in doc_email or "." not in doc_email:
                    st.error("⚠️ გთხოვთ, მიუთითოთ სწორი ელ-ფოსტის მისამართი!")
                else:
                    try:
                        conn_chk = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                        cursor_chk = conn_chk.cursor()
                        cursor_chk.execute("SELECT id FROM doctors WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))", (cleaned_name,))
                        existing_doc = cursor_chk.fetchone()
                        conn_chk.close()
                    except:
                        existing_doc = None

                    if existing_doc:
                        orig_manager, orig_time = get_original_creator(cleaned_name)
                        st.error(f"❌ **შეცდომა:** ექიმი სახელით **'{cleaned_name}'** უკვე შეტანილია სისტემაში!\n\n📋 **დეტალები:** პირველად დაარეგისტრირა მენეჯერმა **'{orig_manager}'** — თარიღი/დრო: `{orig_time}`.")
                    else:
                        try:
                            conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                            cursor = conn.cursor()
                            default_expiry = "2028-12-31"
                            cursor.execute("""
                                INSERT INTO doctors (name, specialty, credits, clinic, email, phone, notes, expiry_date, certificate_path, last_updated)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (cleaned_name, doc_spec.strip(), int(doc_credits), doc_clinic, doc_email.strip(), doc_phone.strip(), doc_notes.strip(), default_expiry, "", str(datetime.now())))
                            conn.commit()
                            conn.close()
                            log_action(st.session_state.current_user, "რეგისტრაცია", cleaned_name, f"კლინიკა: {doc_clinic}")
                            log_credits_history(cleaned_name, 0, int(doc_credits), st.session_state.current_user, "საწყისი რეგისტრაცია")
                            st.cache_data.clear()
                            st.success("✅ შესრულებულია! ექიმი წარმატებით დარეგისტრირდა.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"შეცდომა რეგისტრაციისას: {e}")

    with tab_upload:
        st.markdown("#### 📂 ექიმების სიის ინტელექტუალური იმპორტი (Excel / CSV)")
        uploaded_excel = st.file_uploader("აირჩიეთ ფაილი (.xlsx ან .csv):", type=["xlsx", "csv"])
        if uploaded_excel is not None:
            try:
                if uploaded_excel.name.endswith('.csv'):
                    df_up = pd.read_csv(uploaded_excel, sep=None, engine='python')
                else:
                    df_up = pd.read_excel(uploaded_excel)
                st.dataframe(df_up.head(3), use_container_width=True)
                
                if st.button("🚀 ფაილის ავტომატური იმპორტი ბაზაში", use_container_width=True):
                    with st.spinner("⏳ მიმდინარეობს მონაცემების დამუშავება..."):
                        conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                        cursor = conn.cursor()
                        imported_count = 0
                        skipped_count = 0
                        cols = list(df_up.columns)
                        
                        name_col = next((c for c in cols if any(k in str(c).lower() for k in ['ექიმი', 'სახელი', 'name'])), cols[1] if len(cols) > 1 else cols[0])
                        cred_col = next((c for c in cols if any(k in str(c).lower() for k in ['ქულა', 'credit'])), cols[0])
                        
                        for _, r in df_up.iterrows():
                            try:
                                d_name = str(r[name_col]).strip() if pd.notna(r[name_col]) else ""
                                if not d_name or d_name.lower() == 'nan':
                                    continue
                                cursor.execute("SELECT id FROM doctors WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))", (d_name,))
                                if cursor.fetchone():
                                    skipped_count += 1
                                    continue
                                raw_cred = str(r[cred_col]) if cred_col and pd.notna(r[cred_col]) else "15"
                                d_cred = int(''.join(filter(str.isdigit, raw_cred))) if any(char.isdigit() for char in raw_cred) else 15
                                
                                cursor.execute("""
                                    INSERT INTO doctors (name, specialty, credits, clinic, email, phone, notes, expiry_date, certificate_path, last_updated)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (d_name, "ოჯახის ექიმი", d_cred, CLINICS_LIST[0], "doctor@edumed.ge", "+995 599 00 00 00", "იმპორტი", "2028-12-31", "", str(datetime.now())))
                                imported_count += 1
                            except:
                                pass
                        conn.commit()
                        conn.close()
                        st.cache_data.clear()
                    st.success(f"🎉 დაემატა: {imported_count} | გამოტოვდა: {skipped_count}")
            except Exception as e:
                st.error(f"შეცდომა: {e}")

    with tab_list:
        all_docs = fetch_doctors()
        if all_docs:
            df_search = pd.DataFrame(all_docs)
            search_query = st.text_input("🔎 ძებნა (სახელი / სპეციალობა):", "")
            if search_query:
                df_search = df_search[df_search.apply(lambda row: row.astype(str).str.contains(search_query, case=False).any(), axis=1)]
            st.dataframe(df_search, use_container_width=True, hide_index=True)
        else:
            st.info("ბაზა ცარიელია.")

    with tab_risk:
        all_docs_risk = fetch_doctors()
        if all_docs_risk:
            df_risk = pd.DataFrame(all_docs_risk)
            df_risk_filtered = df_risk[df_risk["credits"] < 30]
            if not df_risk_filtered.empty:
                emails_string = ", ".join(df_risk_filtered["email"].dropna().tolist())
                st.text_input("📋 ყველა მეილი (კოპირებისთვის):", value=emails_string)
                st.dataframe(df_risk_filtered, use_container_width=True, hide_index=True)
            else:
                st.success("✅ რისკ-ზონაში ექიმები არ არიან.")
        else:
            st.info("ბაზა ცარიელია.")

    with tab_edit:
        doctors_list = fetch_doctors()
        if doctors_list:
            df_edit_tbl = pd.DataFrame(doctors_list)
            selected_ids = []
            for idx, row in df_edit_tbl.iterrows():
                if st.checkbox(f"{row['name']} ({row['credits']} ქულა)", key=f"del_{row['id']}"):
                    selected_ids.append(row['id'])
            if st.button("🗑️ მონიშნულების წაშლა"):
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                for did in selected_ids:
                    c.execute("DELETE FROM doctors WHERE id = ?", (did,))
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.success("წარმატებით წაიშალა!")
                st.rerun()


# =========================================================================
# კლინიკები
# =========================================================================
elif menu_selection == "კლინიკები":
    st.subheader("🏥 კლინიკები — რეპორტები და ექსპორტი")
    selected_clinic_filter = st.selectbox("აირჩიეთ კლინიკური ცენტრი:", CLINICS_LIST)
    all_doctors = fetch_doctors()
    filtered_doctors = [d for d in all_doctors if d.get("clinic") == selected_clinic_filter]
    
    if filtered_doctors:
        df_clinic = pd.DataFrame(filtered_doctors)
        st.dataframe(df_clinic, use_container_width=True, hide_index=True)
        csv_data = df_clinic.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 გადმოწერა (CSV)", data=csv_data, file_name="Report.csv", mime="text/csv", use_container_width=True)
    else:
        st.info("ექიმები არ არიან.")


# =========================================================================
# აუდიტის ჟურნალი
# =========================================================================
elif menu_selection == "აუდიტის ჟურნალი":
    st.subheader("📜 უსაფრთხოების აუდიტისა და სესიების ჟურნალი")
    if os.path.exists(LOG_FILE):
        try:
            df_logs = pd.read_csv(LOG_FILE)
            st.dataframe(df_logs.sort_values(by="timestamp", ascending=False), use_container_width=True, hide_index=True)
        except:
            st.info("ჟურნალი ცარიელია.")
    else:
        st.info("ჟურნალი ცარიელია.")


# =========================================================================
# სეთინგები და პაროლები
# =========================================================================
elif menu_selection == "სეთინგები და პაროლები":
    st.subheader("⚙️ სეთინგები და Backup")
    with st.form("change_password_form"):
        old_p = st.text_input("მიმდინარე პაროლი:", type="password")
        new_p = st.text_input("ახალი პაროლი:", type="password")
        if st.form_submit_button("💾 პაროლის განახლება", use_container_width=True):
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cur_key = "davitshovnadze"
            if "ლალი" in st.session_state.current_user: cur_key = "laliivanishvili"
            elif "ნიკოლოზ" in st.session_state.current_user: cur_key = "nikolozchaduneli"
            
            cursor.execute("SELECT password FROM settings WHERE manager_login = ?", (cur_key,))
            res = cursor.fetchone()
            if res and check_password(old_p, res[0]):
                cursor.execute("UPDATE settings SET password = ? WHERE manager_login = ?", (hash_password(new_p), cur_key))
                conn.commit()
                conn.close()
                st.success("✅ პაროლი წარმატებით შეიცვალა!")
            else:
                st.error("არასწორი პაროლია.")
                conn.close()
                
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            st.download_button("📥 ბაზის Backup (.db)", data=f.read(), file_name="backup.db", mime="application/octet-stream", use_container_width=True)