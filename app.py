import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def format_date(dt):
    return dt.strftime('%d/%m/%Y') if dt else "--/--/----"

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS myapp;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS myapp.users_status (
            device_id VARCHAR(255) PRIMARY KEY,
            phone VARCHAR(50) UNIQUE,
            status VARCHAR(50) DEFAULT 'Expired',
            expiry_date TIMESTAMP WITH TIME ZONE DEFAULT (NOW() - INTERVAL '1 day'),
            last_active TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            app_version VARCHAR(20) DEFAULT '7.1.0',
            bot_status VARCHAR(20) DEFAULT 'Offline',
            accepted_clicks INT DEFAULT 0,
            subscription_type VARCHAR(50) DEFAULT 'VIP',
            notice_message TEXT,
            activated_code VARCHAR(100)
        );
    """)
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
    conn.commit()
    cur.close()
    conn.close()

init_db()

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
        if phone:
            cur.execute("SELECT device_id FROM myapp.users_status WHERE phone = %s", (phone,))
            existing = cur.fetchone()
            if existing and existing['device_id'] != d_id:
                return jsonify({
                    "success": False, 
                    "status": "DeviceMismatch", 
                    "message": "⚠️ هذا الحساب يعمل على جهاز آخر حالياً."
                })

        # 🔄 إدراج المستخدم إذا لم يكن موجوداً، مع إعطائه تاريخ انتهاء منتهي مبدئياً ليدخل شاشة التفعيل
        cur.execute("""
            INSERT INTO myapp.users_status (device_id, phone, last_active, app_version, bot_status, accepted_clicks, expiry_date, status)
            VALUES (%s, %s, NOW(), %s, %s, %s, NOW() - INTERVAL '1 day', 'Expired')
            ON CONFLICT (device_id) DO UPDATE SET
                last_active = NOW(),
                phone = COALESCE(EXCLUDED.phone, myapp.users_status.phone),
                bot_status = EXCLUDED.bot_status,
                app_version = EXCLUDED.app_version,
                accepted_clicks = GREATEST(myapp.users_status.accepted_clicks, EXCLUDED.accepted_clicks)
            RETURNING *;
        """, (d_id, phone, app_ver, bot_status, app_clicks))
        
        user = cur.fetchone()

        cur.execute("SELECT * FROM myapp.app_config WHERE key IN ('latest_version', 'force_update', 'update_url');")
        config = {r['key']: r['value'] for r in cur.fetchall()}
        
        if app_ver != config.get('latest_version') and config.get('force_update') == 'true':
            return jsonify({
                "status": "ForceUpdate", 
                "message": "يوجد إصدار جديد. يرجى التحديث لمتابعة العمل.", 
                "updateUrl": config.get('update_url')
            })

        if user['status'] == 'Blocked':
            return jsonify({"success": False, "status": "Blocked", "message": "🛑 تم حظر هذا الجهاز."})

        # 📅 فحص انتهاء الاشتراك مباشرة عبر قاعدة البيانات لضمان دقة التوقيت 100%
        cur.execute("""
            SELECT (expiry_date <= CURRENT_TIMESTAMP) AS is_expired 
            FROM myapp.users_status WHERE device_id = %s
        """, (d_id,))
        exp_res = cur.fetchone()
        is_expired = exp_res['is_expired'] if exp_res else True

        # تحديث حالة الجدول تلقائياً لتتطابق مع الواقع
        new_status = "Expired" if is_expired else "Active"
        cur.execute("UPDATE myapp.users_status SET status = %s WHERE device_id = %s", (new_status, d_id))

        notice = None
        if user['notice_message']:
            notice = {"id": str(datetime.now().timestamp()), "message": user['notice_message'], "type": "dialog"}
            cur.execute("UPDATE myapp.users_status SET notice_message = NULL WHERE device_id = %s", (d_id,))
        
        conn.commit()

        return jsonify({
            "success": True,
            "status": new_status,
            "expiryDate": format_date(user['expiry_date']),
            "subType": user['subscription_type'],
            "acceptedClicks": user['accepted_clicks'],
            "notice": notice,
            "access": not is_expired
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    data = request.json
    code = data.get('code')
    d_id = data.get('deviceId')
    phone = data.get('phone')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM myapp.subscriptions WHERE code = %s AND is_used = FALSE", (code,))
        sub = cur.fetchone()
        
        if not sub:
            return jsonify({"success": False, "message": "❌ الكود غير صالح أو تم استخدامه"}), 400

        cur.execute("UPDATE myapp.subscriptions SET is_used = TRUE, used_by_device = %s, used_at = NOW() WHERE id = %s", (d_id, sub['id']))
        
        # تفعيل الحساب وإضافة الأيام فوق الوقت الحالي أو فوق التاريخ القديم المتبقي
        cur.execute("""
            UPDATE myapp.users_status SET 
                status = 'Active', 
                phone = COALESCE(%s, phone),
                expiry_date = CASE 
                    WHEN expiry_date > NOW() THEN expiry_date + (%s * INTERVAL '1 day')
                    ELSE NOW() + (%s * INTERVAL '1 day')
                END,
                subscription_type = %s,
                activated_code = %s
            WHERE device_id = %s RETURNING expiry_date;
        """, (phone, sub['duration_days'], sub['duration_days'], sub['sub_type'], code, d_id))
        
        res = cur.fetchone()
        conn.commit()
        return jsonify({"success": True, "message": "✅ تم تفعيل الاشتراك!", "expiryDate": format_date(res['expiry_date'])})
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
