# -*- coding: utf-8 -*-
"""
乐跑数据生成器 v2.0
- 用户注册/登录
- 管理员后台（查看注册人数、日用量统计、账号管理）
- TCX文件生成（原有功能）
"""

import math
import random
import sqlite3
import hashlib
import os
import io
import zipfile
import calendar
from functools import wraps
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, send_file,
    jsonify, session, redirect, url_for, g
)
import xml.etree.ElementTree as ET

app = Flask(__name__)
app.secret_key = 'lepao-system-secret-key-2026'
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lepao.db')

# ==================== 数据库 ====================

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

app.teardown_appcontext(close_db)

@app.context_processor
def inject_session():
    return dict(session=session)

def init_db():
    """初始化数据库表"""
    db = sqlite3.connect(DATABASE)
    c = db.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            disabled INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT -1,
            daily_limit INTEGER DEFAULT -1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_count INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            generate_count INTEGER DEFAULT 0,
            active_users INTEGER DEFAULT 0
        );
    ''')
    # 兼容旧表：如果缺少新字段就添加
    for col, typ in [('disabled', 'INTEGER DEFAULT 0'), ('max_uses', 'INTEGER DEFAULT -1'), ('daily_limit', 'INTEGER DEFAULT -1')]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except:
            pass
    # 创建默认管理员账号: admin / admin123
    c.execute("SELECT id FROM users WHERE username = 'admin'")
    if not c.fetchone():
        pw_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        c.execute("INSERT INTO users (username, password_hash, is_admin, max_uses, daily_limit) VALUES (?, ?, 1, -1, -1)",
                  ('admin', pw_hash))
    db.commit()
    db.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_user_limit(user_id):
    """检查用户是否超出限额，返回 (ok, message)"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return False, '用户不存在'
    if user['disabled']:
        return False, '账号已被禁用，请联系管理员'
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 检查总次数限制
    if user['max_uses'] >= 0:
        total = db.execute("SELECT COALESCE(SUM(file_count), 0) as cnt FROM usage_logs WHERE user_id = ?",
                          (user_id,)).fetchone()['cnt']
        if total >= user['max_uses']:
            return False, f'已达到总使用次数限制({user["max_uses"]}次)，请联系管理员'
    
    # 检查每日限额
    if user['daily_limit'] >= 0:
        daily = db.execute("SELECT COALESCE(SUM(file_count), 0) as cnt FROM usage_logs WHERE user_id = ? AND date(created_at) = ?",
                          (user_id, today)).fetchone()['cnt']
        if daily >= user['daily_limit']:
            return False, f'已达到每日限额({user["daily_limit"]}次)，明天再来吧'
    
    return True, ''

def log_usage(user_id, file_count=1):
    """记录一次生成操作"""
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    db.execute("INSERT INTO usage_logs (user_id, file_count) VALUES (?, ?)",
               (user_id, file_count))
    db.execute("""
        INSERT INTO daily_stats (date, generate_count, active_users)
        VALUES (?, ?, 1)
        ON CONFLICT(date) DO UPDATE SET
            generate_count = generate_count + ?,
            active_users = active_users + 1
    """, (today, file_count, file_count))
    db.commit()

# ==================== 登录装饰器 ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        db = get_db()
        user = db.execute("SELECT is_admin FROM users WHERE id = ?",
                          (session['user_id'],)).fetchone()
        if not user or not user['is_admin']:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== 页面路由 ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() or request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if not username or not password:
            return jsonify({'success': False, 'message': '请输入用户名和密码'})
        
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?",
                          (username,)).fetchone()
        if not user or user['password_hash'] != hash_password(password):
            return jsonify({'success': False, 'message': '用户名或密码错误'})
        
        if user['disabled']:
            return jsonify({'success': False, 'message': '账号已被禁用，请联系管理员'})
        
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = user['is_admin']
        return jsonify({'success': True, 'is_admin': bool(user['is_admin'])})
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.get_json() or request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or len(username) < 2:
            return jsonify({'success': False, 'message': '用户名至少2个字符'})
        if not password or len(password) < 4:
            return jsonify({'success': False, 'message': '密码至少4个字符'})
        
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE username = ?",
                              (username,)).fetchone()
        if existing:
            return jsonify({'success': False, 'message': '用户名已存在'})
        
        db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                   (username, hash_password(password)))
        db.commit()
        return jsonify({'success': True, 'message': '注册成功，请登录'})
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================== 管理员后台 ====================

@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('admin.html')

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    
    total_users = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()['cnt']
    today_users = db.execute("""
        SELECT COUNT(DISTINCT user_id) as cnt FROM usage_logs
        WHERE date(created_at) = ?
    """, (today,)).fetchone()['cnt']
    today_generates = db.execute("""
        SELECT COALESCE(SUM(file_count), 0) as cnt FROM usage_logs
        WHERE date(created_at) = ?
    """, (today,)).fetchone()['cnt']
    total_generates = db.execute("""
        SELECT COALESCE(SUM(file_count), 0) as cnt FROM usage_logs
    """).fetchone()['cnt']
    
    daily_stats = db.execute("""
        SELECT * FROM daily_stats
        ORDER BY date DESC LIMIT 30
    """).fetchall()
    
    users = db.execute("""
        SELECT u.*,
            (SELECT COALESCE(SUM(file_count), 0) FROM usage_logs WHERE user_id = u.id) as total_usage,
            (SELECT COALESCE(SUM(file_count), 0) FROM usage_logs WHERE user_id = u.id AND date(created_at) = ?) as today_usage
        FROM users ORDER BY u.created_at DESC
    """, (today,)).fetchall()
    
    return jsonify({
        'total_users': total_users,
        'today_users': today_users,
        'today_generates': today_generates,
        'total_generates': total_generates,
        'daily_stats': [dict(row) for row in daily_stats],
        'users': [dict(row) for row in users]
    })

@app.route('/api/admin/user/<int:user_id>/toggle', methods=['POST'])
@admin_required
def toggle_user(user_id):
    """禁用/启用用户"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'})
    if user['is_admin']:
        return jsonify({'success': False, 'message': '不能操作管理员账号'})
    
    new_status = 0 if user['disabled'] else 1
    db.execute("UPDATE users SET disabled = ? WHERE id = ?", (new_status, user_id))
    db.commit()
    status_text = '已禁用' if new_status else '已启用'
    return jsonify({'success': True, 'message': f'用户{status_text}', 'disabled': new_status})

@app.route('/api/admin/user/<int:user_id>/limit', methods=['POST'])
@admin_required
def set_user_limit(user_id):
    """设置使用限制"""
    data = request.get_json()
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'})
    
    max_uses = data.get('max_uses', user['max_uses'])
    daily_limit = data.get('daily_limit', user['daily_limit'])
    
    # -1 表示无限制
    if max_uses is not None:
        max_uses = int(max_uses)
    if daily_limit is not None:
        daily_limit = int(daily_limit)
    
    db.execute("UPDATE users SET max_uses = ?, daily_limit = ? WHERE id = ?",
               (max_uses, daily_limit, user_id))
    db.commit()
    
    return jsonify({
        'success': True,
        'message': '限额已更新',
        'max_uses': max_uses,
        'daily_limit': daily_limit
    })

# ==================== 主页 ====================

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username'))

# ==================== TCX生成（保持不变） ====================

def generate_tcx(start_time):
    total_seconds = random.randint(840, 1080)
    single_lap_distance = 400.0
    total_laps = round(random.uniform(5.2, 6.0), 2)
    total_distance = round(total_laps * single_lap_distance, 2)

    lap_time_list = []
    remaining_seconds = total_seconds
    int_laps = int(total_laps)
    for i in range(int_laps):
        if i == int_laps - 1:
            lap_t = remaining_seconds
        else:
            lap_t = random.randint(135, 185)
        lap_time_list.append(lap_t)
        remaining_seconds -= lap_t
    fractional_lap = total_laps - int_laps
    if fractional_lap > 0:
        total_seconds = sum(lap_time_list) + lap_time_list[-1] * fractional_lap

    points_per_lap = 120
    n_points = int(points_per_lap * total_laps)
    straight_length = 100.0
    curve_length = 100.0
    radius_meters = curve_length / math.pi

    base_center_lat = 34.197550
    base_center_lon = 117.173188

    meter_to_deg_lat = 1 / 111111.0
    meter_to_deg_lon = 1 / (111111.0 * math.cos(math.radians(base_center_lat)))

    lap_offsets = []
    for lap_idx in range(int(math.ceil(total_laps))):
        lap_lat_off = random.uniform(-2.0, 2.0) * meter_to_deg_lat
        lap_lon_off = random.uniform(-2.0, 2.0) * meter_to_deg_lon
        lap_offsets.append((lap_lat_off, lap_lon_off))

    change_lap_list = random.sample(
        range(int(math.ceil(total_laps))),
        k=random.randint(1, 2)
    )
    change_distance = 0.6
    change_segments = ["straight1", "straight2"]

    point_noise_meter = 0.2
    points_hr = []
    for i in range(n_points + 1):
        x = i / n_points if n_points > 0 else 0
        hr = 75 + int(45 * (1 - math.cos(x * math.pi))) + random.randint(-5, 5)
        points_hr.append(max(75, min(130, hr)))

    root = ET.Element(
        "TrainingCenterDatabase",
        xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
        **{"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
           "xsi:schemaLocation": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2 http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd"}
    )
    activities = ET.SubElement(root, "Activities")
    activity = ET.SubElement(activities, "Activity", Sport="Running")
    ET.SubElement(activity, "Id").text = start_time.isoformat()

    lap = ET.SubElement(activity, "Lap", StartTime=start_time.isoformat())
    ET.SubElement(lap, "TotalTimeSeconds").text = str(round(total_seconds, 2))
    ET.SubElement(lap, "DistanceMeters").text = str(total_distance)
    ET.SubElement(lap, "Intensity").text = "Active"
    ET.SubElement(lap, "TriggerMethod").text = "Manual"
    track = ET.SubElement(lap, "Track")

    dt = total_seconds / n_points if n_points > 0 else 1.0

    s1_end = straight_length
    c1_end = straight_length + curve_length
    s2_end = straight_length + curve_length + straight_length
    c2_end = single_lap_distance

    for i in range(n_points + 1):
        current_total_dist = total_distance * i / n_points if i < n_points else total_distance
        current_time = start_time + timedelta(seconds=dt * i)
        current_lap_dist = current_total_dist % single_lap_distance
        if math.isclose(current_lap_dist, 0.0) and current_total_dist > 0:
            current_lap_dist = c2_end

        current_lap_idx = int(current_total_dist / single_lap_distance)
        lap_lat_off, lap_lon_off = lap_offsets[min(current_lap_idx, len(lap_offsets) - 1)]
        center_lat = base_center_lat + lap_lat_off
        center_lon = base_center_lon + lap_lon_off

        lon_rad_off = radius_meters * meter_to_deg_lon
        lat_half_straight = (straight_length / 2.0) * meter_to_deg_lat
        north_curve_lat = center_lat + lat_half_straight
        south_curve_lat = center_lat - lat_half_straight
        start_lat = south_curve_lat
        start_lon = center_lon - lon_rad_off

        lat, lon = 0.0, 0.0
        is_change_lap = current_lap_idx in change_lap_list
        current_segment = ""

        if 0 <= current_lap_dist < s1_end:
            current_segment = "straight1"
            progress = current_lap_dist / straight_length
            lat = start_lat + progress * (north_curve_lat - south_curve_lat)
            lon = start_lon
            if is_change_lap and current_segment in change_segments and 20 < current_lap_dist < 80:
                lon -= change_distance * meter_to_deg_lon

        elif s1_end <= current_lap_dist < c1_end:
            current_segment = "curve1"
            dist_on_curve = current_lap_dist - s1_end
            angle_rad = (dist_on_curve / curve_length) * math.pi
            current_angle = math.pi - angle_rad
            lat = north_curve_lat + (radius_meters * meter_to_deg_lat) * math.sin(current_angle)
            lon = center_lon + (radius_meters * meter_to_deg_lon) * math.cos(current_angle)

        elif c1_end <= current_lap_dist < s2_end:
            current_segment = "straight2"
            dist_on_straight = current_lap_dist - c1_end
            progress = dist_on_straight / straight_length
            lat = north_curve_lat - progress * (north_curve_lat - south_curve_lat)
            lon = center_lon + lon_rad_off
            if is_change_lap and current_segment in change_segments and 220 < current_lap_dist < 280:
                lon += change_distance * meter_to_deg_lon

        elif s2_end <= current_lap_dist <= c2_end:
            current_segment = "curve2"
            dist_on_curve = current_lap_dist - s2_end
            dist_on_curve = max(0.0, min(dist_on_curve, curve_length))
            angle_rad = (dist_on_curve / curve_length) * math.pi
            current_angle = 2 * math.pi - angle_rad
            lat = south_curve_lat + (radius_meters * meter_to_deg_lat) * math.sin(current_angle)
            lon = center_lon + (radius_meters * meter_to_deg_lon) * math.cos(current_angle)
            if math.isclose(current_lap_dist, c2_end):
                lat, lon = start_lat, start_lon

        lat += random.uniform(-point_noise_meter, point_noise_meter) * meter_to_deg_lat
        lon += random.uniform(-point_noise_meter, point_noise_meter) * meter_to_deg_lon

        trackpoint = ET.SubElement(track, "Trackpoint")
        ET.SubElement(trackpoint, "Time").text = current_time.isoformat()
        position = ET.SubElement(trackpoint, "Position")
        ET.SubElement(position, "LatitudeDegrees").text = f"{lat:.8f}"
        ET.SubElement(position, "LongitudeDegrees").text = f"{lon:.8f}"
        ET.SubElement(trackpoint, "DistanceMeters").text = f"{current_total_dist:.2f}"
        hr_elem = ET.SubElement(trackpoint, "HeartRateBpm")
        ET.SubElement(hr_elem, "Value").text = str(points_hr[i])

    return ET.ElementTree(root)

@app.route('/generate', methods=['POST'])
@login_required
def generate():
    try:
        # 检查用户限额
        ok, msg = check_user_limit(session['user_id'])
        if not ok:
            return jsonify({'error': msg}), 403
        
        data = request.get_json()
        if not data or 'times' not in data or len(data['times']) == 0:
            return jsonify({'error': '请至少选择一个跑步时间！'}), 400

        start_times = []
        for time_item in data['times']:
            year = int(time_item.get('year', 0))
            month = int(time_item.get('month', 0))
            day = int(time_item.get('day', 0))
            hour = int(time_item.get('hour', 0))
            minute = int(time_item.get('minute', 0))
            second = int(time_item.get('second', 0))

            if not (2000 <= year <= 2100):
                return jsonify({'error': f'年份{year}无效'}), 400
            if not (1 <= month <= 12):
                return jsonify({'error': f'月份{month}无效'}), 400
            last_day = calendar.monthrange(year, month)[1]
            if not (1 <= day <= last_day):
                return jsonify({'error': f'{year}年{month}月只有{last_day}天'}), 400
            if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                return jsonify({'error': '时/分/秒无效'}), 400

            start_times.append(datetime(year, month, day, hour, minute, second))

        # 记录使用日志
        log_usage(session['user_id'], len(start_times))

        if len(start_times) == 1:
            st = start_times[0]
            tree = generate_tcx(st)
            output = io.BytesIO()
            tree.write(output, encoding='UTF-8', xml_declaration=True)
            output.seek(0)
            return send_file(
                output,
                mimetype='application/xml',
                as_attachment=True,
                download_name=f"run_{st.strftime('%Y%m%d_%H%M%S')}.tcx"
            )
        else:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for st in start_times:
                    tree = generate_tcx(st)
                    output = io.BytesIO()
                    tree.write(output, encoding='UTF-8', xml_declaration=True)
                    output.seek(0)
                    zf.writestr(f"run_{st.strftime('%Y%m%d_%H%M%S')}.tcx", output.getvalue())
            zip_buffer.seek(0)
            return send_file(
                zip_buffer,
                mimetype='application/zip',
                as_attachment=True,
                download_name='runs.zip'
            )

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'生成失败：{str(e)}'}), 500

# ==================== 启动 ====================

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
