import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sqlite3
import os
import bcrypt

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
            "laliivanishvili": {"name": "ლალი ივანიშვილი (გენერალური დირექტორი)", "pass": default_pass_hash, "role": "director_lali"},
            "nikolozchaduneli": {"name": "ნიკოლოზ ჩადუნელი (კლინიკური დირექტორი)", "pass": default_pass_hash, "role": "director_nika"},
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

@st.cache_data(ttl=30, show_spinner=False)
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
                            st.query_params["auth_user"] = display_name
                            st.query_params["auth_role"] = user_role
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

# --- 💎 UI / CSS დიზაინი ---
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
            padding: 30px; border-radius: 20px;
            border: 1px solid rgba(129, 140, 248, 0.25); border-left: 8px solid #6366f1;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
            margin-bottom: 25px; backdrop-filter: blur(20px);
        }
        .login-card-container {
            background: linear-gradient(145deg, rgba(15, 23, 42, 0.85) 0%, rgba(7, 13, 29, 0.95) 100%);
            padding: 30px 20px; border-radius: 20px; border: 1px solid rgba(129, 140, 248, 0.3);
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8); width: 100%; margin: 10px auto; position: relative; overflow: hidden; text-align: center;
        }
        .login-title { color: #ffffff; text-align: center; font-size: 22px; font-weight: 800; margin-bottom: 6px; }
        .login-subtitle { color: #94a3b8; text-align: center; font-size: 13px; margin-bottom: 0px; }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #030712 0%, #070d1d 50%, #0f172a 100%) !important;
            border-right: 1px solid rgba(129, 140, 248, 0.2); padding-top: 15px;
        }
        section[data-testid="stSidebar"] .stRadio label {
            background: rgba(30, 41, 59, 0.4); border-radius: 12px; padding: 10px 14px !important;
            border: 1px solid rgba(129, 140, 248, 0.15); width: 100% !important; display: flex !important; align-items: center !important; cursor: pointer; transition: all 0.3s ease;
        }
        section[data-testid="stSidebar"] .stRadio label:hover {
            background: linear-gradient(135deg, rgba(79, 70, 229, 0.3) 0%, rgba(99, 102, 241, 0.4) 100%);
            border-color: rgba(129, 140, 248, 0.6);
        }
        .stButton > button {
            border-radius: 12px !important; font-weight: 700 !important; font-size: 15px !important; padding: 12px 20px !important;
            border: 1px solid rgba(129, 140, 248, 0.5) !important; background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important; color: white !important; box-shadow: 0 8px 20px rgba(79, 70, 229, 0.4);
        }
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%);
            padding: 18px; border-radius: 16px; border: 1px solid rgba(129, 140, 248, 0.25); box-shadow: 0 8px 25px rgba(0,0,0,0.4);
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
        <div style='font-size: 12px; color: #818cf8; margin-bottom: 6px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px;'>უწყვეტი სამედიცინო განათლების მართვის პანელი</div>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;'>
            <div>
                <h2 style='color: white; margin: 0; font-size: 28px; font-weight: 800; letter-spacing: -0.5px;'>🧬 NCOS CPD/Academic Programs Portal</h2>
                <p style='color: #94a3b8; margin: 6px 0 0 0; font-size: 14px;'>კლინიკური მართვა, პერსონალის კვალიფიკაცია და რისკების კონტროლი</p>
            </div>
            <div>
                <span style='background: linear-gradient(135deg, #4f46e5, #6366f1); color: white; padding: 10px 22px; border-radius: 25px; font-size: 14px; font-weight: 700; box-shadow: 0 5px 15px rgba(99, 102, 241, 0.5);'>👤 აქტიური მენეჯერი: <b>{st.session_state.current_user}</b></span>
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

is_director = st.session_state.current_role in ["director_lali", "director_nika"]

if st.session_state.current_role == "doctor":
    menu_options = ["👤 ექიმის პირადი პორტალი", "📚 აკრედიტებული კურსები"]
elif is_director:
    menu_options = [
        "მთავარი დაფა & ანალიტიკა", 
        "ექიმების რეესტრი", 
        "🩺 სპეციალობების & კრედიტების მატრიცა",
        "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი",
        "📈 ექიმის ისტორიისა და დინამიკის ზედამხედველობა",
        "კლინიკები", 
        "აუდიტის ჟურნალი", 
        "სეთინგები და პაროლები",
        "📄 OCR სერთიფიკატების სკანერი"
    ]
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
    st.query_params.clear()
    st.rerun()

# =========================================================================
# 📚 სალექციო პროცესის მართვა (მხოლოდ უფლებამოსილი მენეჯერებისთვის)
# =========================================================================
if menu_selection == "📚 სალექციო პროცესის მართვა" and not is_director:
    st.subheader("📚 სალექციო პროცესის მართვა და აუდიტორიების განრიგი")
    st.markdown("<p style='color: #94a3b8;'>აირჩიე სასურველი თარიღი და ნახე აუდიტორიების დატვირთულობა მკაცრი ვალიდაციითა და კონფლიქტების პრევენციით.</p>", unsafe_allow_html=True)

    hours_cols = [f"{h:02d}:00" for h in range(9, 19)]
    auditoriums = ["აუდიტორია 1", "აუდიტორია 2", "აუდიტორია 3", "აუდიტორია 4", "აუდიტორია 5"]
    
    if os.path.exists(LECTURES_FILE):
        try:
            df_lectures = pd.read_csv(LECTURES_FILE)
            if "total_hours" not in df_lectures.columns:
                df_lectures["total_hours"] = 1
            if "weekend_mode" not in df_lectures.columns:
                df_lectures["weekend_mode"] = "არცერთი"
        except:
            df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "weekend_mode", "total_hours"])
    else:
        df_lectures = pd.DataFrame(columns=["lector", "course", "university", "start_date", "end_date", "auditorium", "start_hour", "end_hour", "weekend_mode", "total_hours"])

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
                            if "შაბათი" in w_mode:
                                is_valid_day = True
                        elif sel_weekday == 6:
                            if "კვირა" in w_mode:
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

# =========================================================================
# 📋 ექიმების რეესტრი
# =========================================================================
elif menu_selection == "ექიმების რეესტრი":
    col_reg_head1, col_reg_head2 = st.columns([4, 1])
    with col_reg_head1:
        st.subheader("📋 ექიმების რეესტრი, რეგისტრაცია და მონაცემთა მართვა")
    with col_reg_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_doctors_registry"):
            st.cache_data.clear()
            st.success("✅ ბაზა წარმატებით განახლდა!")
            st.rerun()

    st.markdown("<p style='color: #94a3b8;'>მართეთ ექიმთა ბაზა, დაამატეთ ახალი კადრები, მონიშნეთ checkbox-ებით და წაშალეთ საჭიროებისამებრ (20 ექიმი თითო გვერდზე).</p>", unsafe_allow_html=True)

    tab_reg, tab_list, tab_import = st.tabs(["➕ ექიმის რეგისტრაცია", "📋 რეესტრი & მართვა / მონიშვნა & წაშლა", "📁 Excel / CSV იმპორტი"])

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
                        log_action(st.session_state.current_user, "ექიმის რეგისტრაცია", new_doc_name.strip(), f"კლინიკა: {new_doc_clinic}, კრედიტი: {new_doc_credits}")
                        st.success(f"✅ ექიმი **{new_doc_name}** წარმატებით დარეგისტრირდა!")
                    except Exception as e:
                        st.error(f"ტექნიკური შეცდომა ბაზის ინიციალიზაციისას: {e}")

    with tab_list:
        st.markdown("### 📋 ექიმების სია, მონიშვნა და მართვა")
        docs_list = fetch_doctors()
        if docs_list:
            df_docs = pd.DataFrame(docs_list)
            
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

            st.markdown(f"<p style='color: #94a3b8; font-size: 14px;'>ნაჩვენებია ექიმები: {start_idx + 1} - {min(end_idx, total_doctors)} (სულ: {total_doctors})</p>", unsafe_allow_html=True)

            edited_df = st.data_editor(
                df_page,
                column_config={
                    "მონიშვნა": st.column_config.CheckboxColumn(
                        "მონიშვნა",
                        help="მონიშნეთ ექიმი წაშლისთვის ან მოქმედებისთვის",
                        default=False,
                    )
                },
                disabled=[c for c in df_page.columns if c != "მონიშვნა"],
                use_container_width=True,
                hide_index=True,
                key=f"doctor_table_page_{selected_page}"
            )

            st.markdown("---")
            col_act1, col_act2 = st.columns(2)
            with col_act1:
                if st.button("☑️ ყველას მონიშვნა / მოხსნა"):
                    st.session_state[select_all_key] = not st.session_state[select_all_key]
                    st.rerun()
            with col_act2:
                delete_selected_btn = st.button("🗑️ მონიშნული ექიმების წაშლა ბაზიდან", type="secondary", use_container_width=True)

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
                        log_action(st.session_state.current_user, "ექიმების მასობრივი წაშლა", f"{len(selected_names)} ექიმი", "მონიშნული კადრები წაიშალა რეესტრიდან")
                        st.success(f"✅ წარმატებით წაიშალა **{len(selected_names)}** მონიშნული ექიმი!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"შეცდომა წაშლისას: {e}")
                else:
                    st.warning("⚠️ არცერთი ექიმი არ არის მონიშნული დასაშლელად! მონიშნეთ სასურველი სტრიქონები Checkbox-ით.")
        else:
            st.info("ℹ️ ექიმთა ბაზა ცარიელია.")

    with tab_import:
        st.markdown("### 📁 მონაცემთა მასობრივი იმპორტი (Excel / CSV)")
        st.markdown("<p style='color: #94a3b8; font-size: 14px;'>ატვირთეთ ფაილი. სვეტი შეიძლება ერქვას <code>name</code>, <code>ექიმი</code> ან <code>სახელი</code>.</p>", unsafe_allow_html=True)
        uploaded_file = st.file_uploader("აირჩიეთ CSV ან Excel ფაილი:", type=["csv", "xlsx", "xls"])
        
        if uploaded_file is not None:
            df_imp = None
            try:
                if uploaded_file.name.endswith('.csv'):
                    for enc in ['utf-8-sig', 'utf-8', 'cp1251', 'latin1']:
                        try:
                            uploaded_file.seek(0)
                            df_imp = pd.read_csv(uploaded_file, encoding=enc, sep=None, engine='python')
                            break
                        except Exception:
                            continue
                else:
                    uploaded_file.seek(0)
                    df_imp = pd.read_excel(uploaded_file)
                
                if df_imp is not None and not df_imp.empty:
                    original_columns = list(df_imp.columns)
                    clean_cols = {str(c).strip().lower(): c for c in original_columns}
                    
                    st.success("✅ ფაილი წარმატებით იკითხა! წინასწარი მონაცემები:")
                    st.dataframe(df_imp.head(), use_container_width=True)
                    
                    name_col_key = None
                    possible_name_keys = ['name', 'fullname', 'ექიმი', 'სახელი', 'სახელი და გვარი', 'doctor', 'fio']
                    for pk in possible_name_keys:
                        if pk in clean_cols:
                            name_col_key = clean_cols[pk]
                            break
                    
                    if not name_col_key:
                        st.error("🚨 შეცდომა: ატვირთულ ფაილში ვერ მოიძებნა სახელის სვეტი (მაგ: **name**, **ექიმი** ან **სახელი**)!")
                    else:
                        if st.button("🚀 მონაცემების ბაზაში ჩატვირთვა", use_container_width=True):
                            conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
                            cursor = conn.cursor()
                            success_count = 0
                            
                            for _, row in df_imp.iterrows():
                                name_val = str(row.get(name_col_key, "")).strip()
                                if not name_val or name_val.lower() == 'nan':
                                    continue
                                
                                spec_val = "ზოგადი პროფილი"
                                for sk in ['specialty', 'სპეციალობა', 'prof']:
                                    if sk in clean_cols and pd.notna(row.get(clean_cols[sk])):
                                        spec_val = str(row.get(clean_cols[sk])).strip()
                                        break
                                
                                cred_val = 30
                                for ck in ['credits', 'კრედიტები', 'credit', 'ქულა']:
                                    if ck in clean_cols and pd.notna(row.get(clean_cols[ck])):
                                        try:
                                            cred_val = int(row.get(clean_cols[ck]))
                                        except:
                                            pass
                                        break
                                
                                clin_val = CLINICS_LIST[0]
                                for clk in ['clinic', 'კლინიკა', 'hospital']:
                                    if clk in clean_cols and pd.notna(row.get(clean_cols[clk])):
                                        clin_val = str(row.get(clean_cols[clk])).strip()
                                        break
                                
                                cursor.execute("""
                                    INSERT OR REPLACE INTO doctors (name, specialty, credits, clinic, expiry_date, last_updated)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (name_val, spec_val, cred_val, clin_val, "2028-12-31", datetime.now().strftime("%Y-%m-%d")))
                                success_count += 1
                                
                            conn.commit()
                            conn.close()
                            log_action(st.session_state.current_user, "მასობრივი იმპორტი", uploaded_file.name, f"წარმატებით აიტვირთა {success_count} ჩანაწერი")
                            st.success(f"✅ წარმატებით აიტვირთა და განახლდა **{success_count}** ექიმის მონაცემი!")
                            st.rerun()
                else:
                    st.error("🚨 ატვირთული ფაილი ცარიელია ან ვერ მოხერხდა მისი წაკითხვა.")
            except Exception as e:
                st.error(f"🚨 კრიტიკული შეცდომა ფაილის დამუშავებისას: {e}")

# =========================================================================
# მთავარი დაფა & დირექტორთა გაფართოებული ანალიტიკა
# =========================================================================
elif menu_selection == "მთავარი დაფა & ანალიტიკა":
    col_dash_head1, col_dash_head2 = st.columns([4, 1])
    with col_dash_head1:
        st.subheader("📊 გენერალური მენეჯმენტისა და დირექციის ანალიტიკური დაფა")
    with col_dash_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_main_dashboard"):
            st.cache_data.clear()
            st.success("✅ დაფა წარმატებით განახლდა!")
            st.rerun()

    st.markdown("<p style='color: #94a3b8;'>პლატფორმის ზოგადი წარმოდგენა: ძირითადი მეტრიკები, კლინიკების დატვირთულობა, კრედიტების სტატისტიკა და ბოლო აუდიტორული ცვლილებები.</p>", unsafe_allow_html=True)
    
    doctors_data = fetch_doctors()
    df_main = pd.DataFrame(doctors_data)
    
    if not df_main.empty:
        total_docs = len(df_main)
        low_credits_count = len(df_main[df_main["credits"] < 30])
        avg_credits = round(df_main["credits"].mean(), 1) if total_docs > 0 else 0
        
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("👥 რეგისტრირებული ექიმები", total_docs)
        col_m2.metric("⭐ საშუალო კრედიტები", avg_credits)
        col_m3.metric("⚠️ რისკ-ჯგუფი (<30 კრედიტი)", low_credits_count, delta_color="inverse")
        col_m4.metric("🏥 ჩართული კლინიკები", len(CLINICS_LIST))
        
        st.markdown("---")
        
        col_ch1, col_ch2 = st.columns(2)
        with col_ch1:
            st.markdown("### 🏥 კლინიკების მიხედვით განაწილება")
            clinic_counts = df_main["clinic"].value_counts().reset_index()
            clinic_counts.columns = ["კლინიკა", "ექიმების რაოდენობა"]
            st.dataframe(clinic_counts, use_container_width=True, hide_index=True)
            
        with col_ch2:
            st.markdown("### 🩺 ძირითადი სპეციალობები")
            spec_counts = df_main["specialty"].value_counts().reset_index()
            spec_counts.columns = ["სპეციალობა", "რაოდენობა"]
            st.dataframe(spec_counts, use_container_width=True, hide_index=True)
    else:
        st.info("ℹ️ ბაზა ცარიელია. მონაცემების სანახავად გთხოვთ ატვირთოთ ფაილი ან დაარეგისტრიროთ ექიმები.")

    st.markdown("---")
    
    # აქ ზუსტად იმ ზოლში, სადაც კურსორი იყო ნაჩვენები სურათზე, დაემატა Refresh ღილაკი
    col_audit_title, col_audit_btn = st.columns([3.5, 1.5])
    with col_audit_title:
        st.markdown("### 📜 ბოლო განახლებები & აუდიტის ცვლილებები სისტემაში")
    with col_audit_btn:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_audit_log_zone"):
            st.success("✅ აუდიტის ჟურნალი განახლდა!")
            st.rerun()

    if os.path.exists(LOG_FILE):
        try:
            df_log_view = pd.read_csv(LOG_FILE)
            if not df_log_view.empty:
                st.dataframe(df_log_view.tail(10).iloc[::-1], use_container_width=True, hide_index=True)
            else:
                st.info("ℹ️ აუდიტის ჟურნალი ცარიელია.")
        except Exception:
            st.info("ℹ️ აუდიტის ჟურნალის წაკითხვის შეფერხება.")
    else:
        st.info("ℹ️ აუდიტის ჟურნალის ფაილი ჯერ არ არსებობს.")

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

elif menu_selection == "🩺 სპეციალობების & კრედიტების მატრიცა":
    col_mat_head1, col_mat_head2 = st.columns([4, 1])
    with col_mat_head1:
        st.subheader("🩺 სპეციალობების კვალიფიკაციისა და კრედიტების მატრიცა")
    with col_mat_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_matrix"):
            st.cache_data.clear()
            st.rerun()

    doctors_data = fetch_doctors()
    if doctors_data:
        df_spec = pd.DataFrame(doctors_data).groupby("specialty").agg(ექიმების_რაოდენობა=('name', 'count'), საშუალო_კრედიტი=('credits', 'mean')).reset_index()
        st.dataframe(df_spec, use_container_width=True, hide_index=True)

elif menu_selection == "🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი":
    col_risk_head1, col_risk_head2 = st.columns([4, 1])
    with col_risk_head1:
        st.subheader("🚨 კლინიკური რისკ-ჯგუფების ოპერაციული მენეჯმენტი")
    with col_risk_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_risks"):
            st.cache_data.clear()
            st.rerun()

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

elif menu_selection == "კლინიკები":
    col_clin_head1, col_clin_head2 = st.columns([4, 1])
    with col_clin_head1:
        st.subheader("🏥 კლინიკები — რეპორტები")
    with col_clin_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_clinics"):
            st.cache_data.clear()
            st.rerun()

    sel_cl = st.selectbox("აირჩიეთ კლინიკა:", CLINICS_LIST)
    all_doctors = fetch_doctors()
    filtered_doctors = [d for d in all_doctors if d.get("clinic") == sel_cl]
    if filtered_doctors:
        st.dataframe(pd.DataFrame(filtered_doctors), use_container_width=True, hide_index=True)

elif menu_selection == "აუდიტის ჟურნალი":
    col_log_head1, col_log_head2 = st.columns([4, 1])
    with col_log_head1:
        st.subheader("📜 უსაფრთხოების აუდიტის ჟურნალი")
    with col_log_head2:
        if st.button("🔄 მონაცემების განახლება", use_container_width=True, key="refresh_audit_page"):
            st.rerun()

    if os.path.exists(LOG_FILE):
        st.dataframe(pd.read_csv(LOG_FILE), use_container_width=True, hide_index=True)

elif menu_selection == "სეთინგები და პაროლები":
    st.subheader("⚙️ სეთინგები და Backup")
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            st.download_button("📥 ბაზის Backup (.db)", data=f.read(), file_name="backup.db", mime="application/octet-stream", use_container_width=True)

elif menu_selection == "📄 OCR სერთიფიკატების სკანერი":
    st.subheader("📄 ინტელექტუალური OCR სერთიფიკატების სკანერი")
    st.markdown("<p style='color: #94a3b8; font-size: 14px;'>ატვირთეთ ექიმის სერტიფიკატის PDF ფაილი, რათა ავტომატურად ამოიკითხოს ტექსტური მონაცემები.</p>", unsafe_allow_html=True)
    ocr_file = st.file_uploader("აირჩიეთ სერთიფიკატი (.pdf):", type=["pdf"])
    if ocr_file is not None:
        with st.spinner("მიმდინარეობს დოკუმენტის დამუშავება და სკანირება..."):
            extracted_text = extract_text_from_pdf(ocr_file)
        st.text_area("📄 ამოკითხული დოკუმენტის შიგთავსი:", extracted_text, height=250)
