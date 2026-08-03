import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sqlite3
import os
import io
import bcrypt

# OCR-ისთვის უსაფრთხო იმპორტი
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
LECTURES_FILE = "edumed_lectures_schedule.csv"
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

# --- ავტორიზაცია, სესია და Screen Lock მექანიზმი ---
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

# --- 💎 ULTRA-PREMIUM UI / CSS / ANIMATIONS დიზაინი (აღდგენილი ძველი სტილი) ---
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

# --- 🌟 გლობალური Alert ---
if os.path.exists(ALERTS_FILE):
    try:
        df_alerts = pd.read_csv(ALERTS_FILE)
        if not df_alerts.empty:
            latest_alert = df_alerts.iloc[-1]["alert_text"]
            st.error(f"🚨 **ოფიციალური განგაში / შეტყობინება მენეჯმენტიდან:** {latest_alert}")
    except:
        pass

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

# --- 🧭 საიდბარი (მენიუ) ---
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
        "📚 სალექციო პროცესის მართვა",
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
# 📚 სალექციო პროცესის მართვა
# =========================================================================
if menu_selection == "📚 სალექციო პროცესის მართვა":
    st.subheader("📚 სალექციო პროცესის მართვა და აუდიტორიების განრიგი")
    st.markdown("<p style='color: #94a3b8;'>აუდიტორიების დატვირთულობის ცხრილი და ავტომატური კონფლიქტების პრევენციის მექანიზმი.</p>", unsafe_allow_html=True)

    hours_cols = [f"{h:02d}:00" for h in range(9, 19)]
    auditoriums = ["აუდიტორია 1", "აუდიტორია 2", "აუდიტორია 3", "აუდიტორია 4", "აუდიტორია 5"]
    
    if os.path.exists(LECTURES_FILE):
        try:
            df_lectures = pd.read_csv(LECTURES_FILE)
            if "total_hours" not in df_lectures.columns:
                df_lectures["total_hours"] = 1
            if "include_weekend" not in df_lectures.columns:
                df_lectures["include_weekend"] = "არა"
        except:
            df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "include_weekend", "total_hours"])
    else:
        df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "include_weekend", "total_hours"])

    # --- 🗓️ აუდიტორიების განრიგის ცხრილი ---
    st.markdown("### 📊 აუდიტორიების განრიგი (დატვირთულობის ინდიკატორი)")
    
    matrix_data = []
    for aud in auditoriums:
        row = {"აუდიტორია": aud}
        for h in hours_cols:
            is_busy = False
            for _, lec in df_lectures.iterrows():
                if str(lec.get("auditorium")) == aud:
                    s_h = str(lec.get("start_hour", ""))
                    e_h = str(lec.get("end_hour", ""))
                    if s_h and e_h and s_h <= h <= e_h:
                        is_busy = True
                        break
            row[h] = "🔴 დაკავებულია" if is_busy else "🟢 თავისუფალია"
        matrix_data.append(row)
        
    df_matrix = pd.DataFrame(matrix_data)
    st.dataframe(df_matrix, use_container_width=True, hide_index=True, height=280)

    st.markdown("---")

    # --- 📥 სალექციო რეპორტის ფაილის გატანა (.xlsx) ---
    st.markdown("### 📥 ლექციების ჩატარების დეტალური რეპორტი")
    st.markdown("<p style='color: #94a3b8; font-size: 14px;'>გადმოწერეთ სრული რეპორტი საათებისა და კონტროლის მონაცემებით (Excel .xlsx).</p>", unsafe_allow_html=True)
    
    if not df_lectures.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_lectures.to_excel(writer, index=False, sheet_name='Lectures_Report')
        excel_data = output.getvalue()

        st.download_button(
            label="📥 დეტალური სალექციო რეპორტის გადმოწერა (Excel .xlsx)",
            data=excel_data,
            file_name="Lectures_Detailed_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.info("ℹ️ დაგეგმილი ლექციები ჯერ არ ფიქსირდება ბაზაში.")

    st.markdown("---")
    st.markdown("### 📝 ლექციის / კურსის დაგეგმვის ფორმა")

    with st.form("lecture_form"):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            lector_name = st.text_input("👨‍🏫 ლექტორი:", placeholder="ჩაწერეთ ლექტორის სახელი და გვარი")
            course_name = st.text_input("📖 კურსის დასახელება:", placeholder="მაგ: ფუნდამენტური საზოგადოებრივი ჯანდაცვა")
        with col_f2:
            university_name = st.text_input("🏛️ უნივერსიტეტი:", placeholder="მაგ: Caucasus University")
            sel_auditorium = st.selectbox("🚪 აუდიტორია:", auditoriums)
            
        col_f3, col_f4 = st.columns(2)
        with col_f3:
            start_date = st.date_input("📅 კურსის დასაწყისი:")
        with col_f4:
            end_date = st.date_input("📅 კურსის დასასრული:")
            
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            start_hour = st.selectbox("⏰ საწყისი საათი:", hours_cols, index=0)
        with col_h2:
            end_hour = st.selectbox("⏰ დასასრული საათი:", hours_cols, index=3)

        include_weekend = st.checkbox("📅 ტარდება თუ არა ლექცია შაბათ-კვირასაც?")
        
        submit_lecture = st.form_submit_button("🚀 ლექციის დაგეგმვა და განრიგში ასახვა", use_container_width=True)
        
        if submit_lecture:
            if not course_name.strip() or not lector_name.strip():
                st.error("⚠️ გთხოვთ, შეავსოთ ლექტორი და კურსის დასახელება!")
            elif start_hour > end_hour:
                st.error("⚠️ საწყისი საათი არ შეიძლება იყოს დასასრულის საათზე გვიანი!")
            elif start_date > end_date:
                st.error("⚠️ კურსის დასაწყისი თარიღი არ შეიძლება იყოს დასასრულის თარიღზე გვიანი!")
            else:
                conflict_detected = False
                conflict_message = ""

                d_start = pd.to_datetime(start_date)
                d_end = pd.to_datetime(end_date)
                
                if not df_lectures.empty:
                    for _, existing_lec in df_lectures.iterrows():
                        ex_start = pd.to_datetime(existing_lec["start_date"])
                        ex_end = pd.to_datetime(existing_lec["end_date"])
                        
                        dates_overlap = not (d_end < ex_start or d_start > ex_end)
                        
                        if dates_overlap:
                            ex_s_hour = str(existing_lec["start_hour"])
                            ex_e_hour = str(existing_lec["end_hour"])
                            hours_overlap = not (end_hour < ex_s_hour or start_hour > ex_e_hour)
                            
                            if hours_overlap:
                                if str(existing_lec["auditorium"]) == sel_auditorium:
                                    conflict_detected = True
                                    conflict_message = f"🚨 კონფლიქტი: **{sel_auditorium}** უკვე დაჯავშნილია ამ პერიოდში ({ex_s_hour} - {ex_e_hour}) კურსისთვის: „{existing_lec['course']}“!"
                                    break
                                
                                if str(existing_lec["lector"]).strip().lower() == lector_name.strip().lower():
                                    conflict_detected = True
                                    conflict_message = f"🚨 კონფლიქტი: ლექტორი **{lector_name}** უკვე დაკავებულია ამავე დროს ({ex_s_hour} - {ex_e_hour}) სხვა კურსზე: „{existing_lec['course']}“!"
                                    break

                if conflict_detected:
                    st.error(conflict_message)
                else:
                    total_calculated_hours = 0
                    current_d = d_start
                    s_idx = hours_cols.index(start_hour)
                    e_idx = hours_cols.index(end_hour)
                    hours_per_day = (e_idx - s_idx) + 1

                    while current_d <= d_end:
                        weekday = current_d.weekday()
                        if weekday < 5:
                            total_calculated_hours += hours_per_day
                        elif include_weekend:
                            total_calculated_hours += hours_per_day
                        current_d += timedelta(days=1)

                    weekend_str = "კი" if include_weekend else "არა"

                    new_lec_record = {
                        "lector": lector_name.strip(),
                        "course": course_name.strip(),
                        "university": university_name.strip(),
                        "start_date": str(start_date),
                        "end_date": str(end_date),
                        "auditorium": sel_auditorium,
                        "start_hour": start_hour,
                        "end_hour": end_hour,
                        "include_weekend": weekend_str,
                        "total_hours": total_calculated_hours
                    }
                    try:
                        df_new = pd.concat([df_lectures, pd.DataFrame([new_lec_record])], ignore_index=True)
                        df_new.to_csv(LECTURES_FILE, index=False, encoding='utf-8-sig')
                        log_action(st.session_state.current_user, "ლექციის დაგეგმვა", lector_name.strip(), f"კურსი: {course_name}, აუდიტორია: {sel_auditorium}, საათები: {total_calculated_hours}სთ")
                        st.success(f"✅ ლექცია წარმატებით დაიგეგმა! ჯამური საათები: **{total_calculated_hours} სთ**")
                        st.rerun()
                    except Exception as e:
                        st.error(f"შეცდომა შენახვისას: {e}")

# =========================================================================
# მთავარი დაფა & დანარჩენი ძველი სექციები
# =========================================================================
elif menu_selection == "მთავარი დაფა & ანალიტიკა":
    st.subheader("📊 მთავარი დაფა — ანალიტიკა და ვიზუალური დიაგრამები")
    doctors_data = fetch_doctors()
    df_main = pd.DataFrame(doctors_data)
    low_count = len(df_main[df_main["credits"] < 30]) if not df_main.empty else 0

    col_1, col_2, col_3 = st.columns(3)
    col_1.metric("რეგისტრირებულ ექიმთა საერთო რაოდენობა", len(df_main))
    col_2.metric("ჩართული კლინიკური ცენტრები", len(CLINICS_LIST))
    col_3.metric("ექიმები (<30 კრედიტით)", low_count)

elif menu_selection == "👤 ექიმის პირადი პორტალი":
    st.subheader("👤 ექიმის პირადი პორტალი (Doctor Self-Service)")
    all_docs_portal = fetch_doctors()
    if all_docs_portal:
        my_name = st.selectbox("აირჩიეთ თქვენი სახელი რეესტრიდან:", [d["name"] for d in all_docs_portal])
        my_profile = next((d for d in all_docs_portal if d["name"] == my_name), None)
        if my_profile:
            c1, c2, c3 = st.columns(3)
            c1.metric("მიმდინარე კრედიტები", my_profile["credits"])
            c2.metric("კლინიკა", my_profile["clinic"])
            c3.metric("ლიცენზიის ვადა", my_profile["expiry_date"])

elif menu_selection == "📚 აკრედიტებული კურსები":
    st.subheader("📚 აკრედიტებული სამედიცინო კურსები")
    st.info("აქ წარმოდგენილია რეკომენდებული კურსები კრედიტქულების ასამაღლებლად.")

elif menu_selection == "🩺 სპეციალობების & კრედიტების მატრიცა":
    st.subheader("🩺 სპეციალობების კვალიფიკაციისა და კრედიტების მატრიცა")
    doctors_data = fetch_doctors()
    if doctors_data:
        df_spec = pd.DataFrame(doctors_data).groupby("specialty").agg(ექიმების_რაოდენობა=('name', 'count'), საშუალო_კრედიტი=('credits', 'mean')).reset_index()
        st.dataframe(df_spec, use_container_width=True, hide_index=True)

elif menu_selection == "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი":
    st.subheader("🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი")
    doctors_data = fetch_doctors()
    if doctors_data:
        df_risk_filtered = pd.DataFrame(doctors_data)[pd.DataFrame(doctors_data)["credits"] < 30]
        st.dataframe(df_risk_filtered, use_container_width=True, hide_index=True)

elif menu_selection == "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა":
    st.subheader("📈 ექიმის კრედიტგროვების ისტორია")
    if os.path.exists(CREDITS_HISTORY_FILE):
        st.dataframe(pd.read_csv(CREDITS_HISTORY_FILE), use_container_width=True, hide_index=True)
    else:
        st.info("ისტორია ცარიელია.")

elif menu_selection == "ექიმების რეესტრი":
    st.subheader("📋 ექიმების რეესტრი და მართვა")
    all_docs = fetch_doctors()
    if all_docs:
        st.dataframe(pd.DataFrame(all_docs), use_container_width=True, hide_index=True)

elif menu_selection == "კლინიკები":
    st.subheader("🏥 კლინიკები — რეპორტები")
    sel_cl = st.selectbox("აირჩიეთ კლინიკა:", CLINICS_LIST)
    all_doctors = fetch_doctors()
    filtered_doctors = [d for d in all_doctors if d.get("clinic") == sel_cl]
    if filtered_doctors:
        st.dataframe(pd.DataFrame(filtered_doctors), use_container_width=True, hide_index=True)

elif menu_selection == "აუდიტის ჟურნალი":
    st.subheader("📜 უსაფრთხოების აუდიტის ჟურნალი")
    if os.path.exists(LOG_FILE):
        st.dataframe(pd.read_csv(LOG_FILE), use_container_width=True, hide_index=True)

elif menu_selection == "სეთინგები და პაროლები":
    st.subheader("⚙️ სეთინგები და Backup")
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            st.download_button("📥 ბაზის Backup (.db)", data=f.read(), file_name="backup.db", mime="application/octet-stream", use_container_width=True)

elif menu_selection == "📄 OCR სერთიფიკატების სკანერი":
    st.subheader("📄 ინტელექტუალური OCR სერთიფიკატების სკანერი")
    ocr_file = st.file_uploader("აირჩიეთ სერთიფიკატი (.pdf):", type=["pdf"])
    if ocr_file is not None:
        st.text_area("ამოკითხული ტექსტი:", extract_text_from_pdf(ocr_file), height=200)
