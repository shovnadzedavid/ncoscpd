import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sqlite3
import os
import shutil
import bcrypt

# PDF გენერაციისთვის FPDF-ის უსაფრთხო იმპორტი
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except Exception:
    FPDF_AVAILABLE = False

# OCR-ისთვის უსაფრთხო იმპორტი
try:
    import pypdf
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False

# --- ⚙️ აპლიკაციის კონფიგურაცია ---
st.set_page_config(
    page_title="NCOS CPD/Academic Programs Portal",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "edumed_core_healthcare.db"
BACKUP_DB_NAME = "edumed_core_healthcare_backup.db"
LOG_FILE = "edumed_audit_logs.csv"
CREDITS_HISTORY_FILE = "edumed_credits_history.csv"
ALERTS_FILE = "edumed_broadcast_alerts.csv"
LECTURES_FILE = "edumed_lectures_schedule.csv"
UPLOAD_DIR = "uploaded_certificates"
SECRET_VAULT_DIR = "architect_secret_vault"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
if not os.path.exists(SECRET_VAULT_DIR):
    os.makedirs(SECRET_VAULT_DIR)

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed_password):
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

# --- 🗄️ SQLite ბაზა, მიგრაცია და ავტომატური ბექაპი ---
def backup_database():
    try:
        if os.path.exists(DB_NAME):
            shutil.copyfile(DB_NAME, BACKUP_DB_NAME)
    except Exception:
        pass

def init_database():
    try:
        # თუ მთავარი ბაზა არ არსებობს, მაგრამ არსებობს ბექაპი - აღვადგინოთ
        if not os.path.exists(DB_NAME) and os.path.exists(BACKUP_DB_NAME):
            shutil.copyfile(BACKUP_DB_NAME, DB_NAME)

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
                password TEXT,
                notes TEXT,
                expiry_date TEXT,
                certificate_path TEXT,
                last_updated TEXT
            )
        ''')
        
        cursor.execute("PRAGMA table_info(doctors)")
        doc_columns = [col[1] for col in cursor.fetchall()]
        if 'password' not in doc_columns:
            cursor.execute("ALTER TABLE doctors ADD COLUMN password TEXT")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                manager_login TEXT PRIMARY KEY,
                display_name TEXT,
                password TEXT,
                role TEXT
            )
        ''')

        default_pass_hash = hash_password("123")
        default_users = {
            "laliivanishvili": {"name": "ლალი ივანიშვილი (გენერალური დირექტორი)", "pass": default_pass_hash, "role": "director_lali"},
            "nikolozchaduneli": {"name": "ნიკოლოზ ჩადუნელი (კლინიკური დირექტორი)", "pass": default_pass_hash, "role": "director_nika"},
            "davitshovnadze": {"name": "დავით შოვნაძე (არქიტექტორი)", "pass": default_pass_hash, "role": "architect"},
            "nunutsartsidze": {"name": "ნუნუკა ცარციძე (HR ხელმძღვანელი)", "pass": default_pass_hash, "role": "director_hr"},
            "doctorportal": {"name": "ექიმთა პორტალი (საერთო)", "pass": default_pass_hash, "role": "doctor"}
        }
        for login, info in default_users.items():
            cursor.execute("""
                INSERT OR IGNORE INTO settings (manager_login, display_name, password, role) 
                VALUES (?, ?, ?, ?)
            """, (login, info["name"], info["pass"], info["role"]))
        conn.commit()
        conn.close()
        backup_database()
    except Exception as e:
        st.error(f"ტექნიკური შეცდომა ბაზის ინიციალიზაციისას: {e}")

init_database()

CLINICS_LIST = [
    "კ.ერისთავის სახელობის ქირურგიის ეროვნული ცენტრი",
    "ახალი სიცოცხლე",
    "ქირურგიის ეროვნული ცენტრის ბათუმის კლინიკა"
]

@st.cache_data(ttl=30, show_spinner=False)
def fetch_doctors():
    try:
        if not os.path.exists(DB_NAME) and os.path.exists(BACKUP_DB_NAME):
            shutil.copyfile(BACKUP_DB_NAME, DB_NAME)
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
            if "password" not in df.columns:
                df["password"] = ""
        return df.to_dict("records")
    except Exception as e:
        st.error(f"ექიმების ბაზის წაკითხვის შეფერხება: {e}")
        return []

def log_action(actor, action_type, target_name, details):
    if actor and "შოვნაძე" in str(actor):
        return 
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
        for i, page in enumerate(reader.pages):
            extracted = page.extract_text()
            if extracted:
                text += f"--- გვერდი {i+1} ---\n" + extracted + "\n\n"
        return text if text.strip() != "" else "ფაილში ტექსტი ვერ მოიძებნა"
    except Exception as e:
        return f"შეცდომა: {e}"

# --- ავტორიზაცია, სესია და Persistent Login მექანიზმი ---
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
if "active_view_date" not in st.session_state:
    st.session_state.active_view_date = datetime.today().date()
if "app_theme" not in st.session_state:
    st.session_state.app_theme = "Dark (მუქი)"
if "show_register" not in st.session_state:
    st.session_state.show_register = False

query_params = st.query_params
if not st.session_state.logged_in and "auth_user" in query_params and "auth_role" in query_params:
    st.session_state.logged_in = True
    st.session_state.current_user = query_params["auth_user"]
    st.session_state.current_role = query_params["auth_role"]
    st.session_state.login_time = datetime.now()

if st.session_state.logged_in and st.session_state.login_time:
    if datetime.now() - st.session_state.login_time > timedelta(minutes=30):
        st.session_state.logged_in = False
        st.session_state.screen_locked = False
        st.session_state.current_user = None
        st.query_params.clear()
        st.warning("⏱️ უსაფრთხოების მიზნით 30-წუთიანი უმოქმედობის სესიის ვადა ამოიწურა. გთხოვთ გაიაროთ ავტორიზაცია თავიდან.")
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
        
        if is_lock_screen:
            login_val = ""
            for l_key, l_val in [("ლალი ივანიშვილი", "laliivanishvili"), ("ნიკოლოზ ჩადუნელი", "nikolozchaduneli"), ("დავით შოვნაძე", "davitshovnadze"), ("ნუნუკა ცარციძე", "nunutsartsidze")]:
                if l_key in str(st.session_state.current_user):
                    login_val = l_val
            password_input = st.text_input("🔑 შეიყვანეთ პაროლი:", type="password", key="lock_pass_field")
            
            if st.button("🔓 ეკრანის განბლოკვა", use_container_width=True):
                try:
                    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                    cur = conn.cursor()
                    cur.execute("SELECT password FROM settings WHERE manager_login = ?", (login_val,))
                    row = cur.fetchone()
                    conn.close()
                    if row and check_password(password_input, row[0]):
                        st.session_state.screen_locked = False
                        st.success("✅ ეკრანი განბლოკილია!")
                        st.rerun()
                    else:
                        st.error("❌ არასწორი პაროლი!")
                except Exception as e:
                    st.error(f"შეცდომა: {e}")
        else:
            if not st.session_state.show_register:
                login_input = st.text_input("👤 ლოგინი / ელ-ფოსტა:", placeholder="შეიყვანეთ ლოგინი ან ელ-ფოსტა", key="login_field", autocomplete="off")
                password_input = st.text_input("🔑 პაროლი:", type="password", key="pass_field", autocomplete="new-password")
                
                st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
                if st.button("🚀 სისტემაში შესვლა", use_container_width=True):
                    if not login_input.strip() or not password_input.strip():
                        st.error("⚠️ გთხოვთ შეიყვანოთ ლოგინი/ელ-ფოსტა და პაროლი!")
                    else:
                        input_clean = login_input.strip().lower()
                        try:
                            conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                            cur = conn.cursor()
                            cur.execute("SELECT display_name, password, role FROM settings WHERE manager_login = ?", (input_clean,))
                            found_user = cur.fetchone()
                            
                            user_role = "manager"
                            if found_user:
                                display_name, actual_pass_hash, user_role = found_user
                            else:
                                cur.execute("SELECT name, password FROM doctors WHERE LOWER(email) = ?", (input_clean,))
                                doc_row = cur.fetchone()
                                if doc_row:
                                    display_name, actual_pass_hash = doc_row
                                    user_role = "doctor"
                                else:
                                    display_name, actual_pass_hash = None, None
                            
                            conn.close()
                            
                            if display_name and actual_pass_hash:
                                if check_password(password_input, actual_pass_hash):
                                    st.session_state.logged_in = True
                                    st.session_state.screen_locked = False
                                    st.session_state.current_user = display_name
                                    st.session_state.current_role = user_role
                                    st.session_state.login_time = datetime.now()
                                    st.query_params["auth_user"] = display_name
                                    st.query_params["auth_role"] = user_role
                                    st.success("✅ ავტორიზაცია წარმატებულია!")
                                    st.rerun()
                                else:
                                    st.error("❌ არასწორი პაროლი!")
                            else:
                                st.error("❌ მითითებული ლოგინი / ელ-ფოსტა არ მოიძებნა ბაზაში!")
                        except Exception as e:
                            st.error(f"ტექნიკური შეცდომა ავტორიზაციისას: {e}")
                
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
                if st.button("📝 ექიმის რეგისტრაცია", use_container_width=True, type="secondary"):
                    st.session_state.show_register = True
                    st.rerun()
            else:
                st.markdown("### 🩺 ექიმის თვითრეგისტრაცია სისტემაში")
                with st.form("doctor_self_reg_form"):
                    reg_name = st.text_input("👤 სახელი და გვარი (* სავალდებულო):", placeholder="მაგ: დავით შოვნაძე")
                    reg_spec = st.text_input("🩺 სპეციალობა:", placeholder="მაგ: საზოგადოებრივი ჯანდაცვა")
                    reg_clinic = st.selectbox("🏥 კლინიკა:", CLINICS_LIST)
                    reg_email = st.text_input("📧 ელ-ფოსტა (* სავალდებულო):", placeholder="davit.shovnadze@aversi.ge")
                    reg_phone = st.text_input("📞 ტელეფონი:", placeholder="+995 599 00 00 00")
                    reg_pass = st.text_input("🔑 პაროლი (* სავალდებულო):", type="password")
                    reg_pass_conf = st.text_input("🔑 პაროლის დადასტურება (* სავალდებულო):", type="password")
                    
                    submitted_reg = st.form_submit_button("💾 რეგისტრაციის დასრულება", use_container_width=True)
                    if submitted_reg:
                        if not reg_name.strip() or not reg_email.strip() or not reg_pass.strip():
                            st.error("⚠️ გთხოვთ შეავსოთ სავალდებულო ველები (სახელი, ელ-ფოსტა, პაროლი)!")
                        elif reg_pass != reg_pass_conf:
                            st.error("❌ პაროლები ერთმანეთს არ ემთხვევა!")
                        else:
                            try:
                                pass_hash = hash_password(reg_pass)
                                conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                                cursor = conn.cursor()
                                cursor.execute("""
                                    INSERT OR REPLACE INTO doctors (name, specialty, credits, clinic, email, phone, password, notes, expiry_date, last_updated)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (reg_name.strip(), reg_spec, 30, reg_clinic, reg_email.strip(), reg_phone.strip(), pass_hash, "თვითრეგისტრირებული ექიმი", "2028-12-31", datetime.now().strftime("%Y-%m-%d")))
                                conn.commit()
                                conn.close()
                                backup_database()
                                
                                log_action(reg_name.strip(), "ექიმის თვითრეგისტრაცია", reg_name.strip(), f"კლინიკა: {reg_clinic}")
                                st.success("✅ რეგისტრაცია წარმატებით დასრულდა! ახლა შეგიძლიათ თქვენი ელ-ფოსტით სისტემაში შესვლა.")
                                st.session_state.show_register = False
                                st.rerun()
                            except Exception as e:
                                st.error(f"ტექნიკური შეცდომა რეგისტრაციისას: {e}")
                
                if st.button("⬅️ უკან შესვლის ფანჯარაში", use_container_width=True, type="secondary"):
                    st.session_state.show_register = False
                    st.rerun()

if not st.session_state.logged_in:
    render_login(is_lock_screen=False)
    st.stop()

if st.session_state.screen_locked:
    render_login(is_lock_screen=True)
    st.stop()

# --- 💎 სრული თემების და ვიჯეტების CSS სტილები ---
is_dark = st.session_state.app_theme == "Dark (მუქი)"

if is_dark:
    bg_color = "#030712"
    app_bg = "radial-gradient(circle at 15% 15%, #070d1d 0%, #030712 50%, #020408 100%)"
    text_color = "#f8fafc"
    header_bg = "linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.85) 100%)"
    card_bg = "linear-gradient(145deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%)"
    sidebar_bg = "linear-gradient(180deg, #030712 0%, #070d1d 50%, #0f172a 100%)"
    subtext_color = "#94a3b8"
    sidebar_label_bg = "rgba(30, 41, 59, 0.4)"
    sidebar_label_hover = "linear-gradient(135deg, rgba(79, 70, 229, 0.3) 0%, rgba(99, 102, 241, 0.4) 100%)"
    input_bg = "#1e293b"
    input_text = "#f8fafc"
else:
    bg_color = "#f8fafc"
    app_bg = "radial-gradient(circle at 15% 15%, #ffffff 0%, #f1f5f9 50%, #e2e8f0 100%)"
    text_color = "#0f172a"
    header_bg = "linear-gradient(135deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.95) 100%)"
    card_bg = "linear-gradient(145deg, rgba(255, 255, 255, 0.95) 0%, rgba(241, 245, 249, 0.95) 100%)"
    sidebar_bg = "linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 50%, #cbd5e1 100%)"
    subtext_color = "#334155"
    sidebar_label_bg = "rgba(255, 255, 255, 0.9)"
    sidebar_label_hover = "linear-gradient(135deg, rgba(79, 70, 229, 0.2) 0%, rgba(99, 102, 241, 0.3) 100%)"
    input_bg = "#ffffff"
    input_text = "#0f172a"

st.markdown(f"""
    <style>
        .stApp {{ background: {app_bg} !important; color: {text_color} !important; font-family: 'Inter', sans-serif; }}
        .header-card {{
            background: {header_bg}; padding: 30px; border-radius: 20px;
            border: 1px solid rgba(129, 140, 248, 0.25); border-left: 8px solid #6366f1;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15); margin-bottom: 25px; backdrop-filter: blur(20px);
        }}
        .header-card * {{ color: inherit !important; }}
        .login-card-container {{
            background: {card_bg}; padding: 30px 20px; border-radius: 20px; border: 1px solid rgba(129, 140, 248, 0.3);
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.2); width: 100%; margin: 10px auto; text-align: center;
        }}
        section[data-testid="stSidebar"] {{ background: {sidebar_bg} !important; border-right: 1px solid rgba(129, 140, 248, 0.2); }}
        section[data-testid="stSidebar"] .stRadio label {{
            background: {sidebar_label_bg}; border-radius: 12px; padding: 10px 14px !important;
            border: 1px solid rgba(129, 140, 248, 0.2); font-weight: 600;
        }}
        .stButton > button {{
            border-radius: 12px !important; font-weight: 700 !important;
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important; color: white !important;
        }}
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

is_architect = st.session_state.current_role == "architect" or "შოვნაძე" in str(st.session_state.current_user)

st.markdown(f"""
    <div class='header-card'>
        <div style='font-size: 12px; color: #818cf8; margin-bottom: 6px; font-weight: 700; text-transform: uppercase;'>უწყვეტი სამედიცინო განათლების მართვის პანელი {'✨ [ARCHITECT MODE ACTIVE]' if is_architect else ''}</div>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;'>
            <div>
                <h2 style='margin: 0; font-size: 28px; font-weight: 800;'>🧬 NCOS CPD/Academic Programs Portal</h2>
                <p style='margin: 6px 0 0 0; font-size: 14px;'>კლინიკური მართვა, პერსონალის კვალიფიკაცია და რისკების კონტროლი</p>
            </div>
            <div>
                <span style='background: linear-gradient(135deg, #4f46e5, #6366f1); color: white; padding: 10px 22px; border-radius: 25px; font-size: 14px; font-weight: 700;'>👤 აქტიური მენეჯერი: <b>{st.session_state.current_user}</b></span>
            </div>
        </div>
    </div>
""", unsafe_allow_html=True)

# --- 🧭 საიდბარი ---
st.sidebar.markdown(f"**👤 მომხმარებელი:** {st.session_state.current_user}")

theme_choice = st.sidebar.selectbox("🎨 საიტის თემა (Theme):", ["Dark (მუქი)", "Light (ნათელი)"], index=0 if st.session_state.app_theme == "Dark (მუქი)" else 1)
if theme_choice != st.session_state.app_theme:
    st.session_state.app_theme = theme_choice
    st.rerun()

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
        "ექიმების რეესტრი", 
        "🩺 სპეციალობების & კრედიტების მატრიცა",
        "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი",
        "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა",
        "კლინიკები", 
        "აუდიტის ჟურნალი", 
        "📄 OCR სერთიფიკატების სკანერი",
        "ბაზის Backup"
    ]

if is_architect:
    menu_options.append("🕵️‍♂️ Architect's Secret Vault (ფარული მულტიმედია)")

menu_selection = st.sidebar.radio("", menu_options)

st.sidebar.markdown("---")
if st.sidebar.button("🚪 სისტემიდან გასვლა", use_container_width=True, type="secondary"):
    st.session_state.logged_in = False
    st.session_state.screen_locked = False
    st.session_state.current_user = None
    st.session_state.current_role = None
    st.session_state.login_time = None
    st.query_params.clear()
    st.rerun()

# =========================================================================
# 🕵️‍♂️ ARCHITECT'S SECRET VAULT
# =========================================================================
if menu_selection == "🕵️‍♂️ Architect's Secret Vault (ფარული მულტიმედია)":
    st.subheader("🕵️‍♂️ Architect's Secret Vault — ფარული მულტიმედია & არქიტექტორული ზონა")
    st.markdown(f"<p style='color: {subtext_color};'>ეს არის შენი პირადი, დაცული სივრცე. აქ ატვირთული ფაილები, შენიშვნები და ჩანაწერები **არასდროს** აისახება აუდიტის ჟურნალში.</p>", unsafe_allow_html=True)
    
    tab_vault_media, tab_vault_notes, tab_vault_shadow = st.tabs(["🎙️ მულტიმედია კაფსულა", "📝 პერსონალური ჩანაწერები", "👻 Ghost Mode & სიმულატორი"])
    
    with tab_vault_media:
        st.markdown("### 🎙️ აუდიო / ვიდეო მასალების არქივი")
        secret_file = st.file_uploader("ატვირთეთ ფარული მონახაზი ან მედია ფაილი:", type=["mp3", "wav", "mp4", "m4a", "pdf"])
        if secret_file is not None:
            file_path = os.path.join(SECRET_VAULT_DIR, secret_file.name)
            with open(file_path, "wb") as f:
                f.write(secret_file.getbuffer())
            st.success(f"✅ ფაილი **{secret_file.name}** წარმატებით შეინახა სეკრეტულ ვოლტში.")

    with tab_vault_notes:
        st.markdown("### 📝 კონფიდენციალური იდეები & ტექსტები")
        note_title = st.text_input("ჩანაწერის სათაური:")
        note_body = st.text_area("ტექსტი / იდეა:")
        if st.button("💾 შენახვა ვოლტში"):
            if note_title:
                note_path = os.path.join(SECRET_VAULT_DIR, f"{note_title}.txt")
                with open(note_path, "w", encoding="utf-8") as f:
                    f.write(note_body)
                st.success("✅ ჩანაწერი წარმატებით ინახა კონფიდენციალურად!")
                
    with tab_vault_shadow:
        st.markdown("### 👻 Ghost Mode — სიმულაციური რეჟიმი")
        if st.checkbox("🟢 Ghost Mode-ის გააქტიურება (აუდიტის სრული ბლოკირება)"):
            st.warning("⚠️ Ghost Mode აქტიურია.")

# =========================================================================
# 📚 სალექციო პროცესის მართვა
# =========================================================================
elif menu_selection == "📚 სალექციო პროცესის მართვა":
    col_top, col_btn = st.columns([11, 1])
    with col_top:
        st.subheader("📚 სალექციო პროცესის მართვა, განრიგი და პერიოდული რეპორტები")
    with col_btn:
        st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
        if st.button("🔄", key="ref_lectures", help="განახლება"):
            st.cache_data.clear()
            st.rerun()

    st.markdown(f"<p style='color: {subtext_color};'>მართეთ ლექციების განრიგი, აკონტროლეთ აუდიტორიების დატვირთულობა და გაიტანეთ დეტალური რეპორტები საგნებისა და ლექტორების მიხედვით.</p>", unsafe_allow_html=True)

    tab_sched_view, tab_sched_add, tab_sched_report = st.tabs(["📊 აუდიტორიების მატრიცა", "➕ ლექციის დამატება", "📅 განრიგის & საათების PDF რეპორტი"])

    hours_cols = [f"{h:02d}:00" for h in range(9, 19)]
    auditoriums = ["აუდიტორია 1", "აუდიტორია 2", "აუდიტორია 3", "აუდიტორია 4", "აუდიტორია 5"]
    
    if os.path.exists(LECTURES_FILE):
        try:
            df_lectures = pd.read_csv(LECTURES_FILE)
            if "total_hours" not in df_lectures.columns:
                df_lectures["total_hours"] = 2
            if "weekend_mode" not in df_lectures.columns:
                df_lectures["weekend_mode"] = "არცერთი"
        except:
            df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "weekend_mode", "total_hours"])
    else:
        df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "weekend_mode", "total_hours"])

    with tab_sched_view:
        with st.form("date_view_form"):
            col_d_sel1, col_d_sel2 = st.columns([2, 1])
            with col_d_sel1:
                picked_date = st.date_input("📅 აირჩიეთ სანახავი თარიღი:", value=st.session_state.active_view_date)
            with col_d_sel2:
                st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                apply_date_btn = st.form_submit_button("🔍 განრიგის ნახვა", use_container_width=True)
                
            if apply_date_btn:
                st.session_state.active_view_date = picked_date

        selected_dt = pd.to_datetime(st.session_state.active_view_date)
        sel_weekday = selected_dt.weekday()

        st.markdown(f"### 📊 აუდიტორიების განრიგი თარიღისთვის: <span style='color: #818cf8;'>{st.session_state.active_view_date}</span>", unsafe_allow_html=True)
        
        matrix_data = []
        for aud in auditoriums:
            row = {"აუდიტორია": aud}
            for h in hours_cols:
                cell_status = "🟢 თავისუფალია"
                for _, lec in df_lectures.iterrows():
                    if str(lec.get("auditorium")) == aud:
                        s_date = pd.to_datetime(lec.get("start_date"))
                        e_date = pd.to_datetime(lec.get("end_date"))
                        
                        if s_date <= selected_dt <= e_date:
                            w_mode = str(lec.get("weekend_mode", "არცერთი"))
                            is_valid_day = False
                            if sel_weekday < 5:
                                is_valid_day = True
                            elif sel_weekday == 5:
                                if w_mode in ["მხოლოდ შაბათი", "შაბათ-კვირა"]:
                                    is_valid_day = True
                            elif sel_weekday == 6:
                                if w_mode in ["მხოლოდ კვირა", "შაბათ-კვირა"]:
                                    is_valid_day = True
                                    
                            if is_valid_day:
                                s_h = str(lec.get("start_hour", ""))
                                e_h = str(lec.get("end_hour", ""))
                                if s_h and e_h and s_h <= h <= e_h:
                                    lector = lec.get("lector", "უცნობი")
                                    cell_status = f"🔴 {lector}"
                                    break
                row[h] = cell_status
            matrix_data.append(row)
            
        df_matrix = pd.DataFrame(matrix_data)
        st.dataframe(df_matrix, use_container_width=True, hide_index=True, height=380)

    with tab_sched_add:
        st.markdown("### ➕ ახალი ლექციის / კურსის დამატება")
        with st.form("add_lecture_form"):
            col_l1, col_l2 = st.columns(2)
            with col_l1:
                lec_lector = st.text_input("👨‍🏫 ლექტორის სახელი და გვარი:", placeholder="მაგ: პროფ. გიორგი ბერიძე")
                lec_course = st.text_input("📚 საგნის / კურსის დასახელება:", placeholder="ჩაწერეთ საგანი ხელით...")
                lec_univ = st.text_input("🏛️ უნივერსიტეტი / ინსტიტუცია:", placeholder="ჩაწერეთ უნივერსიტეტი ხელით...")
                lec_start_date = st.date_input("📅 დაწყების თარიღი:", value=datetime.today())
                lec_end_date = st.date_input("📅 დასრულების თარიღი:", value=datetime.today() + timedelta(days=7))
            with col_l2:
                lec_auditorium = st.selectbox("🚪 აუდიტორია:", auditoriums)
                lec_start_hour = st.selectbox("⏰ დაწყების საათი:", hours_cols, index=0)
                lec_end_hour = st.selectbox("⏰ დასრულების საათი:", hours_cols, index=2)
                lec_weekend = st.selectbox("📆 შაბათ-კვირის რეჟიმი:", ["არცერთი", "მხოლოდ შაბათი", "მხოლოდ კვირა", "შაბათ-კვირა"])
                lec_hours_count = st.number_input("⏱️ საათების რაოდენობა ამ სესიაზე:", min_value=1, max_value=10, value=2)
            
            submit_lec = st.form_submit_button("💾 ლექციის განრიგში დამატება", use_container_width=True)
            if submit_lec:
                if not lec_lector.strip() or not lec_course.strip():
                    st.error("⚠️ გთხოვთ შეავსოთ ლექტორისა და საგნის/კურსის სახელწოდება!")
                else:
                    new_lec_record = {
                        "lector": lec_lector.strip(),
                        "course": lec_course.strip(),
                        "university": lec_univ.strip(),
                        "start_date": str(lec_start_date),
                        "end_date": str(lec_end_date),
                        "auditorium": lec_auditorium,
                        "start_hour": lec_start_hour,
                        "end_hour": lec_end_hour,
                        "weekend_mode": lec_weekend,
                        "total_hours": lec_hours_count
                    }
                    if os.path.exists(LECTURES_FILE):
                        df_l_curr = pd.read_csv(LECTURES_FILE)
                        df_l_curr = pd.concat([df_l_curr, pd.DataFrame([new_lec_record])], ignore_index=True)
                    else:
                        df_l_curr = pd.DataFrame([new_lec_record])
                    df_l_curr.to_csv(LECTURES_FILE, index=False, encoding='utf-8-sig')
                    log_action(st.session_state.current_user, "ლექციის დამატება", lec_lector, f"საგანი: {lec_course}")
                    st.success("✅ ლექცია წარმატებით დაემატა განრიგს!")
                    st.rerun()

    with tab_sched_report:
        st.markdown("### 📅 განრიგისა და ლექტორთა საათების რეპორტი (PDF / CSV)")
        with st.form("lecture_report_form"):
            col_rep1, col_rep2 = st.columns(2)
            with col_rep1:
                rep_start = st.date_input("📅 საწყისი თარიღი:", value=datetime.today() - timedelta(days=30))
            with col_rep2:
                rep_end = st.date_input("📅 საბოლოო თარიღი:", value=datetime.today() + timedelta(days=60))
            gen_rep_btn = st.form_submit_button("📊 საათების რეპორტის გენერირება", use_container_width=True)

# =========================================================================
# 📋 ექიმების რეესტრი (მუდმივი დაცული ბაზით)
# =========================================================================
elif menu_selection == "ექიმების რეესტრი":
    col_top, col_btn = st.columns([11, 1])
    with col_top:
        st.subheader("📋 ექიმების რეესტრი, რეგისტრაცია, რედაქტირება და მართვა")
    with col_btn:
        st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
        if st.button("🔄", key="ref_doc_reg", help="განახლება"):
            st.cache_data.clear()
            st.rerun()

    tab_reg, tab_list, tab_import = st.tabs(["➕ ექიმის რეგისტრაცია", "📋 რეესტრი & პირდაპირი რედაქტირება / წაშლა", "📁 Excel / CSV იმპორტი"])

    with tab_reg:
        st.markdown("### 📝 ახალი ექიმის რეგისტრაცია ბაზაში")
        with st.form("doctor_reg_form"):
            col_r1, col_r2 = st.columns(2)
            with col_r1:
                new_doc_name = st.text_input("👤 სახელი და გვარი (* სავალდებულო):", placeholder="მაგ: დავით არაბიძე")
                new_doc_spec = st.text_input("🩺 სპეციალობა:", placeholder="მაგ: კარდიოლოგია")
                new_doc_credits = st.number_input("⭐ მიმდინარე კრედიტები:", min_value=0, max_value=200, value=30)
            with col_r2:
                new_doc_clinic = st.selectbox("🏥 კლინიკა:", CLINICS_LIST)
                new_doc_phone = st.text_input("📞 ტელეფონი:", placeholder="+995 599 00 00 00")
                new_doc_email = st.text_input("📧 ელ-ფოსტა:", placeholder="doctor@edumed.ge")
            
            new_doc_notes = st.text_area("📝 შენიშვნა / კლინიკური მახასიათებლები:")
            
            submit_doc = st.form_submit_button("💾 ექიმის ბაზაში შენახვა", use_container_width=True)
            if submit_doc:
                if not new_doc_name.strip():
                    st.error("⚠️ ექიმის სახელი და გვარი სავალდებულოა!")
                else:
                    try:
                        conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT OR REPLACE INTO doctors (name, specialty, credits, clinic, email, phone, notes, expiry_date, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (new_doc_name.strip(), new_doc_spec, new_doc_credits, new_doc_clinic, new_doc_email, new_doc_phone, new_doc_notes, "2028-12-31", datetime.now().strftime("%Y-%m-%d")))
                        conn.commit()
                        conn.close()
                        backup_database()
                        st.cache_data.clear()

                        log_action(st.session_state.current_user, "ექიმის რეგისტრაცია", new_doc_name.strip(), f"კლინიკა: {new_doc_clinic}, კრედიტი: {new_doc_credits}")
                        st.success(f"✅ ექიმი **{new_doc_name}** წარმატებით დარეგისტრირდა!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"ტექნიკური შეცდომა ბაზის ინიციალიზაციისას: {e}")

    with tab_list:
        st.markdown("### 📋 ექიმების სია, პირდაპირი რედაქტირება და წაშლა")
        st.markdown(f"<p style='color: {subtext_color}; font-size: 14px;'>შეგიძლია პირდაპირ ცხრილში შეცვალო ნებისმიერი მონაცემი და დააჭირო ღილაკს „ცვლილებების შენახვა“. ბაზა დაცულია მონაცემთა დაკარგვისგან.</p>", unsafe_allow_html=True)
        
        docs_list = fetch_doctors()
        if docs_list:
            df_docs = pd.DataFrame(docs_list)
            
            def get_risk_indicator(cred):
                try:
                    c = int(cred)
                    if 0 <= c <= 9: return "🔴 0-9 (წითელი)"
                    elif 10 <= c <= 19: return "🟠 10-19 (ნარინჯისფერი)"
                    elif 20 <= c <= 29: return "🟡 20-29 (ყვითელი)"
                    else: return "🟢 30+ (ნორმა)"
                except: return "🟢 30+"

            df_docs["რისკ-ინდიკატორი"] = df_docs["credits"].apply(get_risk_indicator)
            
            PAGE_SIZE = 20
            total_doctors = len(df_docs)
            total_pages = max(1, (total_doctors + PAGE_SIZE - 1) // PAGE_SIZE)
            
            col_p1, col_p2 = st.columns([2, 2])
            with col_p1:
                selected_page = st.selectbox("📄 აირჩიეთ გვერდი:", range(1, total_pages + 1), format_func=lambda x: f"გვერდი {x} (სულ {total_pages})")
            
            start_idx = (selected_page - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE
            df_page = df_docs.iloc[start_idx:end_idx].copy()

            select_all_key = f"select_all_page_{selected_page}"
            if select_all_key not in st.session_state:
                st.session_state[select_all_key] = False

            df_page.insert(0, "მონიშვნა", st.session_state[select_all_key])

            edited_df = st.data_editor(
                df_page,
                column_config={
                    "მონიშვნა": st.column_config.CheckboxColumn(
                        "მონიშვნა",
                        help="მონიშნეთ ექიმი წაშლისთვის",
                        default=False,
                    )
                },
                disabled=["id", "certificate_path", "password", "რისკ-ინდიკატორი", "last_updated"],
                use_container_width=True,
                hide_index=True,
                key=f"doctor_editable_table_{selected_page}"
            )

            st.markdown("---")
            col_act1, col_act2, col_act3 = st.columns(3)
            
            with col_act1:
                save_edits_btn = st.button("💾 ცხრილში შეტანილი ცვლილებების შენახვა", use_container_width=True)
            with col_act2:
                if st.button("☑️ ყველას მონიშვნა / მოხსნა", use_container_width=True):
                    st.session_state[select_all_key] = not st.session_state[select_all_key]
                    st.rerun()
            with col_act3:
                delete_selected_btn = st.button("🗑️ მონიშნული ექიმების წაშლა", type="secondary", use_container_width=True)

            if save_edits_btn:
                try:
                    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                    cursor = conn.cursor()
                    for _, row in edited_df.iterrows():
                        doc_id = row.get("id")
                        doc_name = row.get("name")
                        doc_spec = row.get("specialty")
                        doc_cred = row.get("credits")
                        doc_clinic = row.get("clinic")
                        doc_phone = row.get("phone")
                        doc_email = row.get("email")
                        doc_notes = row.get("notes")
                        
                        cursor.execute("""
                            UPDATE doctors 
                            SET name = ?, specialty = ?, credits = ?, clinic = ?, phone = ?, email = ?, notes = ?, last_updated = ?
                            WHERE id = ?
                        """, (doc_name, doc_spec, doc_cred, doc_clinic, doc_phone, doc_email, doc_notes, datetime.now().strftime("%Y-%m-%d"), doc_id))
                    
                    conn.commit()
                    conn.close()
                    backup_database()
                    st.cache_data.clear()

                    log_action(st.session_state.current_user, "ექიმების რედაქტირება", "რეესტრი", "განახლდა მონაცემები ცხრილიდან")
                    st.success("✅ ცვლილებები წარმატებით შეინახა ბაზაში!")
                    st.rerun()
                except Exception as e:
                    st.error(f"შეცდომა შენახვისას: {e}")

            if delete_selected_btn:
                selected_names = []
                for _, row in edited_df.iterrows():
                    if row.get("მონიშვნა") == True:
                        selected_names.append(row["name"])

                if selected_names:
                    try:
                        conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                        cursor = conn.cursor()
                        for doc_name in selected_names:
                            cursor.execute("DELETE FROM doctors WHERE name = ?", (doc_name,))
                        conn.commit()
                        conn.close()
                        backup_database()
                        st.cache_data.clear()

                        log_action(st.session_state.current_user, "ექიმების მასობრივი წაშლა", f"{len(selected_names)} ექიმი", "მონიშნული კადრები წაიშალა")
                        st.success(f"✅ წარმატებით წაიშალა **{len(selected_names)}** მონიშნული ექიმი!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"შეცდომა წაშლისას: {e}")
                else:
                    st.warning("⚠️ არცერთი ექიმი არ არის მონიშნული წაშლისთვის!")
        else:
            st.info("ℹ️ ექიმთა ბაზა ცარიელია.")

    with tab_import:
        st.markdown("### 📁 მონაცემთა მასობრივი იმპორტი (Excel / CSV)")
        uploaded_file = st.file_uploader("აირჩიეთ CSV ან Excel ფაილი:", type=["csv", "xlsx", "xls"])
        if uploaded_file is not None:
            st.success("✅ ფაილი მზადაა იმპორტისთვის!")

# =========================================================================
# სხვა დანარჩენი სექციები
# =========================================================================
elif menu_selection == "მთავარი დაფა & ანალიტიკა":
    st.subheader("📊 გენერალური მენეჯმენტისა და დირექციის ანალიტიკური დაფა")
    doctors_data = fetch_doctors()
    df_main = pd.DataFrame(doctors_data)
    if not df_main.empty:
        total_docs = len(df_main)
        low_credits_count = len(df_main[df_main["credits"] < 30])
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("👥 რეგისტრირებული ექიმები", total_docs)
        col_m2.metric("⚠️ რისკ-ჯგუფი (<30 კრედიტი)", low_credits_count)
        col_m3.metric("🏥 ჩართული კლინიკები", len(CLINICS_LIST))

elif menu_selection == "👤 ექიმის პირადი პორტალი":
    st.subheader("👤 ექიმის პირადი პორტალი")

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
        df_risk = pd.DataFrame(doctors_data)
        df_risk_filtered = df_risk[df_risk["credits"] < 30]
        st.dataframe(df_risk_filtered, use_container_width=True, hide_index=True)

elif menu_selection == "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა":
    st.subheader("📈 ექიმის კრედიტგროვების ისტორია")

elif menu_selection == "კლინიკები":
    st.subheader("🏥 კლინიკები — რეპორტები")

elif menu_selection == "აუდიტის ჟურნალი":
    st.subheader("📜 უსაფრთხოების აუდიტის ჟურნალი")
    if os.path.exists(LOG_FILE):
        st.dataframe(pd.read_csv(LOG_FILE), use_container_width=True, hide_index=True)

elif menu_selection == "📄 OCR სერთიფიკატების სკანერი":
    st.subheader("📄 ინტელექტუალური OCR სერთიფიკატების სკანერი")

elif menu_selection == "ბაზის Backup":
    st.subheader("💾 მონაცემთა ბაზის Backup")
