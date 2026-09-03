import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
import os
import urllib.request
import random
import string

st.set_page_config(page_title="Ultra MyClicker Dashboard", layout="wide", page_icon="⚡")

# --- 1. تحميل الشهادة والاتصال بقاعدة البيانات ---
def download_ca_cert():
    cert_path = "ca-certificate.crt"
    if not os.path.exists(cert_path):
        try:
            url = "https://certs.ondigitalocean.com/ca-certificate.crt"
            urllib.request.urlretrieve(url, cert_path)
        except Exception as e:
            st.error(f"فشل تحميل الشهادة: {e}")
    return cert_path

@st.cache_resource
def init_connection():
    try:
        cert_file = download_ca_cert()
        return psycopg2.connect(
            database="defaultdb",
            user="doadmin",
            password="1tHwqXCgn8BS6iTm942V3f7a",
            host="myclicker-db-rd7ky.db1.ondigitalocean.com",
            port="5432",
            sslmode="require",
            sslrootcert=cert_file
        )
    except Exception as e:
        st.error(f"خطأ في الاتصال: {e}")
        return None

conn = init_connection()
if conn is None:
    st.stop()

def run_query(query, params=()):
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        conn.rollback()
        st.error(f"حدث خطأ في قاعدة البيانات: {e}")
        return False

# --- إنشاء جدول الصلاحيات إذا لم يكن موجوداً وتأمين حساب الأدمن الافتراضي ---
def setup_permissions_table():
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS myapp.app_permissions (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(100) NOT NULL,
                role_name VARCHAR(50),
                allowed_sections TEXT[]
            );
        """)
        conn.commit()
        
        # التأكد من وجود أدمن افتراضي
        cur.execute("SELECT COUNT(*) FROM myapp.app_permissions WHERE username = 'admin'")
        if cur.fetchone()[0] == 0:
            all_secs = [
                "👥 إدارة ومراقبة المستخدمين", 
                "🎫 توليد وإدارة الأكواد (الادمن)", 
                "🤝 قسم الشركاء (الموزعين)", 
                "📈 تحليل البيانات",
                "🖥️ حالة السيرفر",
                "🔐 إدارة الصلاحيات والتحكم"
            ]
            cur.execute(
                "INSERT INTO myapp.app_permissions (username, password, role_name, allowed_sections) VALUES (%s, %s, %s, %s)",
                ("admin", "admin123", "مدير النظام", all_secs)
            )
            conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()

setup_permissions_table()

# =====================================================================
# نظام تسجيل الدخول عبر قاعدة البيانات
# =====================================================================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.allowed_sections = []

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول إلى لوحة التحكم")
    with st.form("login_form"):
        u_input = st.text_input("اسم المستخدم:")
        p_input = st.text_input("كلمة المرور:", type="password")
        submit_login = st.form_submit_button("دخول")
        
        if submit_login:
            try:
                cur = conn.cursor()
                cur.execute("SELECT password, allowed_sections FROM myapp.app_permissions WHERE username = %s", (u_input,))
                user_record = cur.fetchone()
                cur.close()
                
                if user_record and user_record[0] == p_input:
                    st.session_state.logged_in = True
                    st.session_state.username = u_input
                    st.session_state.allowed_sections = user_record[1] if user_record[1] else []
                    st.rerun()
                else:
                    st.error("اسم المستخدم أو كلمة المرور غير صحيحة!")
            except Exception as e:
                st.error(f"خطأ في عملية التحقق: {e}")
    st.stop()

# زر تسجيل الخروج في الشريط الجانبي
if st.sidebar.button("🚪 تسجيل الخروج"):
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.allowed_sections = []
    st.rerun()

# =====================================================================
# عرض القائمة الجانبية بناءً على الصلاحيات الممنوحة للمستخدم
# =====================================================================
st.sidebar.markdown(f"### ⚡ MyClicker Pro")
st.sidebar.info(f"👤 المستخدم: {st.session_state.username}")

# الأقسام المتاحة للمستخدم الموثق بناءً على صلاحياته في قاعدة البيانات
user_allowed = st.session_state.allowed_sections

if not user_allowed:
    st.error("عذراً، ليس لديك أي صلاحيات محددة لعرض الأقسام. يرجى مراجعة الإدارة.")
    st.stop()

choice = st.sidebar.radio("القائمة الرئيسية:", user_allowed)

# =====================================================================
# 1. قسم إدارة المستخدمين
# =====================================================================
if choice == "👥 إدارة ومراقبة المستخدمين":
    st.title("👥 إدارة المستخدمين والرقابة الشاملة")
    
    df_users = pd.read_sql("SELECT device_id, phone, status, bot_status, accepted_clicks, subscription_type, expiry_date, notice_message FROM myapp.users_status ORDER BY last_active DESC", conn)
    
    c1, c2, c3 = st.columns(3)
    c1.metric("إجمالي المستخدمين", len(df_users))
    c2.metric("البوتات النشطة حالياً (Online)", len(df_users[df_users['bot_status'] == 'Online']))
    c3.metric("مستخدمين بحالة نشطة (Active)", len(df_users[df_users['status'] == 'Active']))
    
    st.markdown("### 📋 سجل المستخدمين (مراقبة حية)")
    st.dataframe(df_users, use_container_width=True)
    
    st.markdown("---")
    st.markdown("### 🛠️ لوحة التحكم بالمستخدم (تعديل / إشعار / حظر)")
    
    if not df_users.empty:
        user_list = df_users['device_id'].tolist()
        phones_list = df_users['phone'].astype(str).tolist()
        options = [f"هاتف: {p} | جهاز: {d[:8]}..." for p, d in zip(phones_list, user_list)]
        
        selected_index = st.selectbox("اختر المستخدم لتطبيق إجراء عليه:", range(len(options)), format_func=lambda x: options[x])
        selected_device = user_list[selected_index]
        
        col_edit1, col_edit2 = st.columns(2)
        
        with col_edit1:
            st.info("إرسال رسالة/إشعار منبثق للمستخدم")
            notice_msg = st.text_input("نص الرسالة المنبثقة:")
            if st.button("📤 إرسال الإشعار"):
                if run_query("UPDATE myapp.users_status SET notice_message = %s WHERE device_id = %s", (notice_msg, selected_device)):
                    st.success("تم إرسال الإشعار للمستخدم بنجاح!")
        
        with col_edit2:
            st.warning("تعديل حالة المستخدم (حظر / تعديل الهاتف)")
            new_phone = st.text_input("تعديل رقم الهاتف:", value=df_users.iloc[selected_index]['phone'] or "")
            new_status = st.selectbox("تغيير حالة الاشتراك:", ["Active", "Expired", "Banned"], index=["Active", "Expired", "Banned"].index(df_users.iloc[selected_index]['status'] if df_users.iloc[selected_index]['status'] in ["Active", "Expired", "Banned"] else "Expired"))
            
            if st.button("💾 حفظ التعديلات"):
                if run_query("UPDATE myapp.users_status SET phone = %s, status = %s WHERE device_id = %s", (new_phone, new_status, selected_device)):
                    st.success("تم تحديث بيانات المستخدم بنجاح!")

# =====================================================================
# 2. قسم توليد وإدارة الأكواد (الادمن)
# =====================================================================
elif choice == "🎫 توليد وإدارة الأكواد (الادمن)":
    st.title("🎫 لوحة توليد وإدارة الأكواد الشاملة (الادمن)")
    
    df_codes = pd.read_sql("SELECT * FROM myapp.subscriptions ORDER BY id DESC", conn)
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### ⚙️ توليد أكواد جديدة")
        with st.form("generate_codes_form"):
            code_type = st.selectbox("نوع الكود (Sub Type):", ["VIP", "TRIAL"])
            code_days = st.number_input("مدة الاشتراك (بالأيام):", min_value=1, value=30)
            code_count = st.number_input("الكمية المراد توليدها:", min_value=1, max_value=500, value=10)
            
            submit_gen = st.form_submit_button("توليد الأكواد الآن 🚀")
            
            if submit_gen:
                generated_codes = []
                for _ in range(code_count):
                    prefix = code_type[:3].upper()
                    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    new_code = f"{prefix}-{random_str}"
                    generated_codes.append((new_code, code_type, code_days, None))
                
                success = True
                for c in generated_codes:
                    if not run_query("INSERT INTO myapp.subscriptions (code, sub_type, duration_days, assigned_to_staff_id) VALUES (%s, %s, %s, %s)", c):
                        success = False
                
                if success:
                    st.success(f"تم توليد {code_count} كود بنجاح!")
    
    with col2:
        st.markdown("### 📊 إحصائيات الأكواد")
        unused_codes = df_codes[df_codes['is_used'] == False]
        used_codes = df_codes[df_codes['is_used'] == True]
        
        st.metric("أكواد غير مستعملة (جاهزة)", len(unused_codes))
        st.metric("أكواد تم استعمالها (مباعة)", len(used_codes))
        
    st.markdown("---")
    tab_unused, tab_used = st.tabs(["🎫 الأكواد غير المستعملة (الجديدة)", "✅ الأكواد المستعملة (المبيعات)"])
    
    with tab_unused:
        st.dataframe(unused_codes[['id', 'code', 'sub_type', 'duration_days']], use_container_width=True)
        
    with tab_used:
        st.dataframe(used_codes[['code', 'used_by_device', 'used_at', 'sub_type']], use_container_width=True)

# =====================================================================
# 3. قسم الشركاء (الموزعين)
# =====================================================================
elif choice == "🤝 قسم الشركاء (الموزعين)":
    st.title("🤝 لوحة الشركاء والموزعين")
    st.info("متابعة الأكواد المتاحة والأكواد المفعلة مع أجهزة المستخدمين بكل شفافية.")
    
    df_codes = pd.read_sql("SELECT * FROM myapp.subscriptions ORDER BY id DESC", conn)
    
    unused_codes = df_codes[df_codes['is_used'] == False]
    used_codes = df_codes[df_codes['is_used'] == True]
    
    c1, c2 = st.columns(2)
    c1.metric("📦 الأكواد المتاحة للبيع", len(unused_codes))
    c2.metric("✅ الأكواد المفعلة على الأجهزة", len(used_codes))
    
    st.markdown("---")
    
    col_unused, col_used = st.columns(2)
    
    with col_unused:
        st.subheader("📦 الأكواد المتاحة (غير مستعملة)")
        if not unused_codes.empty:
            st.dataframe(unused_codes[['code', 'sub_type', 'duration_days']], use_container_width=True, hide_index=True)
        else:
            st.warning("لا توجد أكواد متاحة حالياً.")
            
    with col_used:
        st.subheader("✅ الأكواد المفعلة والأجهزة التابعة لها")
        if not used_codes.empty:
            st.dataframe(used_codes[['code', 'sub_type', 'used_by_device', 'used_at']], use_container_width=True, hide_index=True)
        else:
            st.info("لم يتم تفعيل أي كود حتى الآن.")

# =====================================================================
# 4. قسم تحليل البيانات
# =====================================================================
elif choice == "📈 تحليل البيانات":
    st.title("📈 تحليل البيانات وأوقات الذروة")
    query_orders = "SELECT order_time, price FROM myapp.accepted_orders"
    try:
        df_orders = pd.read_sql(query_orders, conn)
        if not df_orders.empty:
            df_orders['order_time'] = pd.to_datetime(df_orders['order_time'])
            df_orders['hour'] = df_orders['order_time'].dt.hour
            peak_hours = df_orders.groupby('hour').size().reset_index(name='count')
            fig = px.bar(peak_hours, x='hour', y='count', title="أوقات الذروة للطلبات المقبولة خلال اليوم (بالساعة)", color='count', color_continuous_scale='Blues')
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("لا توجد سجلات طلبات كافية لعرض الرسوم البيانية حالياً.")
    except Exception as e:
        st.info(f"عذراً، لم نتمكن من قراءة جدول الطلبات: {e}")

# =====================================================================
# 5. قسم حالة السيرفر
# =====================================================================
elif choice == "🖥️ حالة السيرفر":
    st.title("🖥️ مراقبة السيرفر")
    c1, c2 = st.columns(2)
    c1.metric("حالة الخادم وقاعدة البيانات", "متصل 🟢", "DigitalOcean")
    c2.metric("حالة الشهادة الأمنية (SSL)", "مفعلة ومحمية 🔒")
    st.info("هذا السيرفر مرتبط بنظام Node.js (MyClicker Pro Ultra) ويعمل في الوقت الفعلي.")

# =====================================================================
# 6. قسم إدارة الصلاحيات والتحكم
# =====================================================================
elif choice == "🔐 إدارة الصلاحيات والتحكم":
    st.title("🔐 إدارة حسابات المستخدمين وصلاحيات الأقسام")
    st.info("من هنا يمكنك إضافة مستخدمين جدد (مثل الشركاء أو الموظفين) وتحديد الأقسام المسموح لهم برؤيتها فقط.")
    
    col_add, col_view = st.columns([1, 1.5])
    
    with col_add:
        st.markdown("### ➕ إضافة حساب جديد وصلاحيات")
        with st.form("add_user_form"):
            new_user = st.text_input("اسم المستخدم (Username):")
            new_pass = st.text_input("كلمة المرور (Password):", type="password")
            role_desc = st.text_input("مسمي الوظيفة / الوصف (مثال: شريك بغداد):")
            
            st.markdown("**حدد الأقسام المسموح له بدخولها:**")
            sec_p1 = st.checkbox("👥 إدارة ومراقبة المستخدمين")
            sec_p2 = st.checkbox("🎫 توليد وإدارة الأكواد (الادمن)")
            sec_p3 = st.checkbox("🤝 قسم الشركاء (الموزعين)", value=True)
            sec_p4 = st.checkbox("📈 تحليل البيانات")
            sec_p5 = st.checkbox("🖥️ حالة السيرفر")
            sec_p6 = st.checkbox("🔐 إدارة الصلاحيات والتحكم")
            
            submit_new_user = st.form_submit_button("حفظ وإضافة الحساب 💾")
            
            if submit_new_user:
                if not new_user or not new_pass:
                    st.error("يرجى ملء اسم المستخدم وكلمة المرور على الأقل!")
                else:
                    selected_sections = []
                    if sec_p1: selected_sections.append("👥 إدارة ومراقبة المستخدمين")
                    if sec_p2: selected_sections.append("🎫 توليد وإدارة الأكواد (الادمن)")
                    if sec_p3: selected_sections.append("🤝 قسم الشركاء (الموزعين)")
                    if sec_p4: selected_sections.append("📈 تحليل البيانات")
                    if sec_p5: selected_sections.append("🖥️ حالة السيرفر")
                    if sec_p6: selected_sections.append("🔐 إدارة الصلاحيات والتحكم")
                    
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO myapp.app_permissions (username, password, role_name, allowed_sections) VALUES (%s, %s, %s, %s)",
                            (new_user, new_pass, role_desc, selected_sections)
                        )
                        conn.commit()
                        cur.close()
                        st.success(f"تم إنشاء الحساب ({new_user}) بنجاح!")
                    except Exception as err:
                        conn.rollback()
                        st.error(f"اسم المستخدم موجود مسبقاً أو حدث خطأ: {err}")
                        
    with col_view:
        st.markdown("### 📋 الحسابات والصلاحيات الحالية")
        try:
            df_perms = pd.read_sql("SELECT id, username, role_name, allowed_sections FROM myapp.app_permissions", conn)
            st.dataframe(df_perms, use_container_width=True)
            
            st.markdown("---")
            st.markdown("### 🗑️ حذف حساب مستخدم")
            del_username = st.text_input("أدخل اسم المستخدم المراد حذفه:")
            if st.button("حذف الحساب نهائياً ⚠️"):
                if del_username == "admin":
                    st.error("لا يمكنك حذف حساب الأدمن الرئيسي!")
                else:
                    if run_query("DELETE FROM myapp.app_permissions WHERE username = %s", (del_username,)):
                        st.success(f"تم حذف الحساب ({del_username}) بنجاح!")
        except Exception as e:
            st.info(f"جاري تحميل قائمة الحسابات: {e}")
