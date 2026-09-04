import os
import random
import string
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- إعدادات قاعدة البيانات (نفس بيانات لوحة Streamlit) ---
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    # اتصال آمن متوافق مع DigitalOcean
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def format_date(dt):
    return dt.strftime('%d/%m/%Y') if dt else "--/--/----"

# ================== 1. تهيئة وتزامن الجداول ==================
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("CREATE SCHEMA IF NOT EXISTS myapp;")
        # جدول المستخدمين (متوافق مع اللوحة)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS myapp.users_status (
                device_id VARCHAR(255) PRIMARY KEY,
                phone VARCHAR(50) UNIQUE,
                status VARCHAR(50) DEFAULT 'Expired',
                bot_status VARCHAR(20) DEFAULT 'Offline',
                accepted_clicks INT DEFAULT 0,
                subscription_type VARCHAR(50) DEFAULT 'VIP',
                expiry_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                last_active TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                app_version VARCHAR(20) DEFAULT '7.1.0',
                notice_message TEXT,
                activated_code VARCHAR(100)
            );
        """)
        # جدول الأكواد
        cur.execute("""
            CREATE TABLE IF NOT EXISTS myapp.subscriptions (
                id SERIAL PRIMARY KEY,
                code VARCHAR(100) UNIQUE NOT NULL,
                sub_type VARCHAR(50) DEFAULT 'VIP',
                duration_days INT DEFAULT 30,
                is_used BOOLEAN DEFAULT FALSE,
                used_by_device VARCHAR(255),
                used_at TIMESTAMP WITH TIME ZONE,
                assigned_to_staff_id INT
            );
        """)
        # جدول تحليل الذروة (المستخدم في اللوحة)
        cur.execute("CREATE TABLE IF NOT EXISTS myapp.accepted_orders (id SERIAL PRIMARY KEY, device_id VARCHAR(255), price DECIMAL, order_time TIMESTAMP WITH TIME ZONE DEFAULT NOW());")
        # جدول الإعدادات
        cur.execute("CREATE TABLE IF NOT EXISTS myapp.app_config (key VARCHAR(50) PRIMARY KEY, value TEXT);")
        
        # إعدادات افتراضية
        configs = [('latest_version','7.1.0'), ('force_update','false'), ('update_url','')]
        for k, v in configs:
            cur.execute("INSERT INTO myapp.app_config (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (k, v))
            
        conn.commit()
    finally:
        cur.close()
        conn.close()

init_db()

# مسار فحص الجاهزية (لإنجاح الـ Deploy)
@app.route('/')
def health_check():
    return "Core API is Running ✅", 200

# ================== 2. مسار المزامنة والحماية (Heartbeat) ==================

@app.route('/api/check-status', methods=['POST'])
@app.route('/api', methods=['POST'])
def check_status():
    data = request.json
    d_id = data.get('deviceId')
    phone = data.get('phone')
    app_ver = data.get('appVersion', '7.1.0')
    bot_status = data.get('botStatus', 'Offline')
    app_clicks = data.get('acceptedClicks', 0)

    if not d_id:
        return jsonify({"success": False, "message": "Device ID Required"}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 🛡️ حماية: منع استخدام نفس الهاتف على أكثر من جهاز
        if phone:
            cur.execute("SELECT device_id FROM myapp.users_status WHERE phone = %s", (phone,))
            existing = cur.fetchone()
            if existing and existing['device_id'] != d_id:
                return jsonify({"success": False, "status": "DeviceMismatch", "message": "⚠️ هذا الحساب مسجل على جهاز آخر."})

        # 🔄 مزامنة عكسية (تحديث القاعدة من بيانات التطبيق)
        cur.execute("""
            INSERT INTO myapp.users_status (device_id, phone, last_active, app_version, bot_status, accepted_clicks)
            VALUES (%s, %s, NOW(), %s, %s, %s)
            ON CONFLICT (device_id) DO UPDATE SET
                last_active = NOW(),
                phone = COALESCE(EXCLUDED.phone, myapp.users_status.phone),
                bot_status = EXCLUDED.bot_status,
                app_version = EXCLUDED.app_version,
                accepted_clicks = EXCLUDED.accepted_clicks
            RETURNING *;
        """, (d_id, phone, app_ver, bot_status, app_clicks))
        
        user = cur.fetchone()

        # فحص الحظر (متوافق مع حالات اللوحة)
        if user['status'] in ['Blocked', 'Banned']:
            return jsonify({"success": False, "status": "Blocked", "message": "🛑 تم حظر هذا الحساب."})

        # فحص تاريخ انتهاء الاشتراك
        is_expired = user['expiry_date'] <= datetime.now(user['expiry_date'].tzinfo)
        
        # إرسال الرسائل المنبثقة (المكتوبة من لوحة Streamlit)
        notice = None
        if user['notice_message']:
            notice = {"id": str(datetime.now().timestamp()), "message": user['notice_message'], "type": "dialog"}
            cur.execute("UPDATE myapp.users_status SET notice_message = NULL WHERE device_id = %s", (d_id,))
        
        conn.commit()

        return jsonify({
            "success": True,
            "status": "Expired" if is_expired else user['status'],
            "expiryDate": format_date(user['expiry_date']),
            "subType": user['subscription_type'],
            "acceptedClicks": user['accepted_clicks'],
            "notice": notice,
            "access": not is_expired
        })
    finally:
        cur.close()
        conn.close()

# ================== 3. مسار تفعيل الأكواد وتسجيل النقرات ==================

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    data = request.json
    code, d_id, phone = data.get('code'), data.get('deviceId'), data.get('phone')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM myapp.subscriptions WHERE code = %s AND is_used = FALSE", (code,))
        sub = cur.fetchone()
        if not sub:
            return jsonify({"success": False, "message": "❌ كود غير صالح"}), 400

        # تفعيل الكود وربطه بالجهاز
        cur.execute("UPDATE myapp.subscriptions SET is_used = TRUE, used_by_device = %s, used_at = NOW() WHERE id = %s", (d_id, sub['id']))
        cur.execute("""
            UPDATE myapp.users_status SET 
                status = 'Active', phone = %s,
                expiry_date = GREATEST(expiry_date, NOW()) + (%s * INTERVAL '1 day'),
                subscription_type = %s, activated_code = %s
            WHERE device_id = %s RETURNING expiry_date;
        """, (phone, sub['duration_days'], sub['sub_type'], code, d_id))
        
        res = cur.fetchone()
        conn.commit()
        return jsonify({"success": True, "message": "✅ تم التفعيل بنجاح!", "expiryDate": format_date(res['expiry_date'])})
    finally:
        cur.close()
        conn.close()

@app.route('/api/click', methods=['POST'])
def log_click():
    data = request.json
    d_id, price = data.get('deviceId'), data.get('price', 0)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # تسجيل النقرة في جدول تحليل الذروة (الذي تقرأ منه لوحة Streamlit)
        cur.execute("INSERT INTO myapp.accepted_orders (device_id, price, order_time) VALUES (%s, %s, NOW())", (d_id, price))
        conn.commit()
        return jsonify({"status": "Success"})
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
