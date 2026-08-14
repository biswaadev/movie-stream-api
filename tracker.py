"""
Lightweight SQLite tracking system for MovieBox
Tracks IP addresses, watch events, downloads, searches, and device details.
"""

import os
import sqlite3
import datetime
from threading import Lock

DB_PATH = os.environ.get("TRACKER_DB_PATH", os.path.join(os.path.dirname(__file__), "activity.db"))
db_lock = Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                action TEXT NOT NULL,
                subject_id TEXT,
                media_title TEXT,
                season INTEGER,
                episode INTEGER,
                quality TEXT,
                details TEXT,
                user_agent TEXT,
                device TEXT,
                os TEXT,
                browser TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ip ON activity_logs (ip_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_action ON activity_logs (action)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON activity_logs (created_at)")
        conn.commit()
        conn.close()


def parse_ua(ua_str):
    ua = (ua_str or "").lower()
    device = "Desktop"
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        device = "Mobile"
    elif "tablet" in ua or "ipad" in ua:
        device = "Tablet"
    elif "bot" in ua or "crawl" in ua or "spider" in ua:
        device = "Bot"

    os_name = "Other"
    if "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        os_name = "iOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"

    browser = "Other"
    if "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "opera" in ua or "opr" in ua:
        browser = "Opera"

    return device, os_name, browser


def get_client_ip(req):
    if req.headers.get("CF-Connecting-IP"):
        return req.headers.get("CF-Connecting-IP").split(",")[0].strip()
    if req.headers.get("X-Forwarded-For"):
        return req.headers.get("X-Forwarded-For").split(",")[0].strip()
    if req.headers.get("X-Real-IP"):
        return req.headers.get("X-Real-IP").strip()
    return req.remote_addr or "127.0.0.1"


def log_activity(ip, action, subject_id="", media_title="", season=None, episode=None, quality="", details="", user_agent=""):
    device, os_name, browser = parse_ua(user_agent)
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO activity_logs (
                    ip_address, action, subject_id, media_title,
                    season, episode, quality, details,
                    user_agent, device, os, browser
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ip, action, str(subject_id or ""), str(media_title or ""),
                season, episode, str(quality or ""), str(details or ""),
                str(user_agent or "")[:300], device, os_name, browser
            ))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[Tracker Error] log_activity failed: {e}")


def get_stats():
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()

            # Total logs
            cursor.execute("SELECT COUNT(*) FROM activity_logs")
            total_logs = cursor.fetchone()[0]

            # Unique IPs
            cursor.execute("SELECT COUNT(DISTINCT ip_address) FROM activity_logs")
            unique_ips = cursor.fetchone()[0]

            # Unique IPs in last 24h
            cursor.execute("SELECT COUNT(DISTINCT ip_address) FROM activity_logs WHERE created_at >= datetime('now', '-1 day')")
            ips_24h = cursor.fetchone()[0]

            # Total Watches
            cursor.execute("SELECT COUNT(*) FROM activity_logs WHERE action IN ('WATCH_START', 'STREAM')")
            total_watches = cursor.fetchone()[0]

            # Total Downloads
            cursor.execute("SELECT COUNT(*) FROM activity_logs WHERE action = 'DOWNLOAD'")
            total_downloads = cursor.fetchone()[0]

            # Total Searches
            cursor.execute("SELECT COUNT(*) FROM activity_logs WHERE action = 'SEARCH'")
            total_searches = cursor.fetchone()[0]

            # Active streaming last 10 mins
            cursor.execute("SELECT COUNT(DISTINCT ip_address) FROM activity_logs WHERE action IN ('WATCH_START', 'STREAM', 'STREAM_PING') AND created_at >= datetime('now', '-10 minutes')")
            active_now = cursor.fetchone()[0]

            # Top 5 most watched titles
            cursor.execute("""
                SELECT media_title, COUNT(*) as cnt 
                FROM activity_logs 
                WHERE media_title != '' AND action IN ('WATCH_START', 'STREAM', 'DOWNLOAD')
                GROUP BY media_title 
                ORDER BY cnt DESC 
                LIMIT 5
            """)
            top_titles = [{"title": r[0], "count": r[1]} for r in cursor.fetchall()]

            # Top 5 most active IPs
            cursor.execute("""
                SELECT ip_address, device, os, browser, COUNT(*) as cnt, MAX(created_at) as last_seen
                FROM activity_logs 
                GROUP BY ip_address 
                ORDER BY cnt DESC 
                LIMIT 5
            """)
            top_ips = [{
                "ip": r[0],
                "device": r[1],
                "os": r[2],
                "browser": r[3],
                "count": r[4],
                "last_seen": r[5]
            } for r in cursor.fetchall()]

            conn.close()
            return {
                "total_logs": total_logs,
                "unique_ips": unique_ips,
                "ips_24h": ips_24h,
                "total_watches": total_watches,
                "total_downloads": total_downloads,
                "total_searches": total_searches,
                "active_now": active_now,
                "top_titles": top_titles,
                "top_ips": top_ips
            }
    except Exception as e:
        print(f"[Tracker Error] get_stats failed: {e}")
        return {
            "total_logs": 0, "unique_ips": 0, "ips_24h": 0,
            "total_watches": 0, "total_downloads": 0, "total_searches": 0,
            "active_now": 0, "top_titles": [], "top_ips": []
        }


def get_logs(page=1, limit=50, action=None, search=None):
    try:
        offset = (max(1, page) - 1) * limit
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()

            query = "SELECT * FROM activity_logs WHERE 1=1"
            params = []

            if action and action != "ALL":
                query += " AND action = ?"
                params.append(action)

            if search and search.strip():
                s = f"%{search.strip()}%"
                query += " AND (ip_address LIKE ? OR media_title LIKE ? OR details LIKE ? OR subject_id LIKE ?)"
                params.extend([s, s, s, s])

            # Total matching count
            count_query = query.replace("SELECT *", "SELECT COUNT(*)", 1)
            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            query += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()
            logs = [dict(r) for r in rows]
            conn.close()

            return {
                "logs": logs,
                "total": total_count,
                "page": page,
                "pages": (total_count + limit - 1) // limit if total_count > 0 else 1
            }
    except Exception as e:
        print(f"[Tracker Error] get_logs failed: {e}")
        return {"logs": [], "total": 0, "page": 1, "pages": 1}


def clear_logs():
    try:
        with db_lock:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM activity_logs")
            conn.commit()
            conn.close()
            return True
    except Exception as e:
        print(f"[Tracker Error] clear_logs failed: {e}")
        return False


# Initialize DB automatically on import
init_db()
