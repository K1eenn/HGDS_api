from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Union, Tuple
import json
import base64
from io import BytesIO
import asyncio
import os
import dotenv
import datetime
import random
import hashlib
import requests
import time
import logging
from PIL import Image
import uuid
# Updated OpenAI import style
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
import shutil
import tempfile
from gtts import gTTS
import re
from html import unescape # For cleaning HTML before TTS
import dateparser
from dateutil.relativedelta import relativedelta

# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập log
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler()])
# Use specific loggers per module/area if desired, otherwise a root logger is fine
logger = logging.getLogger('family_assistant_api')
logger = logging.getLogger('weather_advisor')
# Khởi tạo API
app = FastAPI(title="Trợ lý Gia đình API (Tool Calling)",
              description="API cho Trợ lý Gia đình thông minh với khả năng xử lý text, hình ảnh, âm thanh và sử dụng Tool Calling, bao gồm thông tin thời tiết qua OpenWeatherMap.", # CHANGED description
              version="1.2.0") # CHANGED Version update

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Trong production nên giới hạn origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.environ.get("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Đường dẫn file lưu trữ dữ liệu
FAMILY_DATA_FILE = os.path.join(DATA_DIR, "family_data.json")
EVENTS_DATA_FILE = os.path.join(DATA_DIR, "events_data.json")
NOTES_DATA_FILE = os.path.join(DATA_DIR, "notes_data.json")
CHAT_HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
SESSIONS_DATA_FILE = os.path.join(DATA_DIR, "sessions_data.json")

# Thư mục lưu trữ tạm thời
TEMP_DIR = os.path.join(DATA_DIR, "temp_files")
os.makedirs(TEMP_DIR, exist_ok=True)

# Danh sách domain tin tức Việt Nam
VIETNAMESE_NEWS_DOMAINS = [
    "vnexpress.net", "tuoitre.vn", "thanhnien.vn", "vietnamnet.vn", "vtv.vn",
    "nhandan.vn", "baochinhphu.vn", "laodong.vn", "tienphong.vn", "zingnews.vn",
    "cand.com.vn", "kenh14.vn", "baophapluat.vn",
]

# --- API Keys ---
OPENAI_API_KEY_ENV = os.getenv("OPENAI_API_KEY", "")
TAVILY_API_KEY_ENV = os.getenv("TAVILY_API_KEY", "")
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY") 
#OPENWEATHERMAP_API_KEY = "94c94ebc644d803eef31af2f1d399bd2"
openai_model = "gpt-4o-mini" # Or your preferred model supporting Tool Calling

# ------- Date/Time Helper Functions (Moved from WeatherService) --------
VIETNAMESE_WEEKDAY_MAP = {
    "thứ 2": 0, "thứ hai": 0, "t2": 0,
    "thứ 3": 1, "thứ ba": 1, "t3": 1,
    "thứ 4": 2, "thứ tư": 2, "t4": 2,
    "thứ 5": 3, "thứ năm": 3, "t5": 3,
    "thứ 6": 4, "thứ sáu": 4, "t6": 4,
    "thứ 7": 5, "thứ bảy": 5, "t7": 5,
    "chủ nhật": 6, "cn": 6,
}

NEXT_WEEK_KEYWORDS = ["tuần sau", "tuần tới", "next week"]
RECURRING_KEYWORDS = [
    "hàng ngày", "mỗi ngày", "hàng tuần", "mỗi tuần", "hàng tháng", "mỗi tháng",
    "hàng năm", "mỗi năm", "định kỳ", "lặp lại",
    "mỗi sáng thứ", "mỗi trưa thứ", "mỗi chiều thứ", "mỗi tối thứ",
    "thứ 2 hàng tuần", "mỗi thứ 2", "mỗi t2", "thứ 3 hàng tuần", "mỗi thứ 3", "mỗi t3",
    "thứ 4 hàng tuần", "mỗi thứ 4", "mỗi t4", "thứ 5 hàng tuần", "mỗi thứ 5", "mỗi t5",
    "thứ 6 hàng tuần", "mỗi thứ 6", "mỗi t6", "thứ 7 hàng tuần", "mỗi thứ 7", "mỗi t7",
    "chủ nhật hàng tuần", "mỗi chủ nhật", "mỗi cn",
    "daily", "every day", "weekly", "every week", "monthly", "every month",
    "yearly", "annually", "every year", "recurring", "repeating",
    "every monday", "every tuesday", "every wednesday", "every thursday",
    "every friday", "every saturday", "every sunday",
    # Thêm từ khóa mới
    "tất cả", "mọi", "các", "tất cả thứ", "mọi thứ", "các thứ",
    "tất cả tối thứ", "mọi tối thứ", "các tối thứ",
    "tất cả sáng thứ", "mọi sáng thứ", "các sáng thứ",
    "tất cả chiều thứ", "mọi chiều thứ", "các chiều thứ",
    "vào các", "vào tất cả", "vào mọi"
]

def get_date_from_relative_term(term: str) -> Optional[str]:
    """
    Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế (YYYY-MM-DD).
    Hỗ trợ: hôm nay/nay/tối nay/sáng nay..., ngày mai/mai, ngày kia/mốt,
    hôm qua, thứ X tuần này/sau/tới, DD/MM, DD/MM/YYYY.
    """
    if not term:
        logger.warning("get_date_from_relative_term called with empty term.")
        return None

    term = term.lower().strip()
    today = datetime.date.today()
    logger.debug(f"Calculating date for term: '{term}', today is: {today.strftime('%Y-%m-%d %A')}")

    # --- 1. Direct relative terms ---
    if term in ["hôm nay", "today", "nay", "tối nay", "sáng nay", "chiều nay", "trưa nay"]:
        logger.info(f"Term '{term}' interpreted as today: {today.strftime('%Y-%m-%d')}")
        return today.strftime("%Y-%m-%d")
    if term in ["ngày mai", "mai", "tomorrow"]:
        calculated_date = today + datetime.timedelta(days=1)
        logger.info(f"Term '{term}' interpreted as tomorrow: {calculated_date.strftime('%Y-%m-%d')}")
        return calculated_date.strftime("%Y-%m-%d")
    if term in ["ngày kia", "mốt", "ngày mốt", "day after tomorrow"]:
        calculated_date = today + datetime.timedelta(days=2)
        logger.info(f"Term '{term}' interpreted as day after tomorrow: {calculated_date.strftime('%Y-%m-%d')}")
        return calculated_date.strftime("%Y-%m-%d")
    if term in ["hôm qua", "yesterday"]:
        calculated_date = today - datetime.timedelta(days=1)
        logger.info(f"Term '{term}' interpreted as yesterday: {calculated_date.strftime('%Y-%m-%d')}")
        # Note: Returning past date might need special handling depending on use case (e.g., for events)
        return calculated_date.strftime("%Y-%m-%d")

    # --- 2. Specific weekdays (upcoming or next week) ---
    target_weekday = -1
    is_next_week = False
    term_for_weekday_search = term

    # Check for "next week" indicators
    for kw in NEXT_WEEK_KEYWORDS:
        if kw in term:
            is_next_week = True
            term_for_weekday_search = term.replace(kw, "").strip()
            logger.debug(f"Detected 'next week' in '{term}', searching for weekday in '{term_for_weekday_search}'")
            break

    # Find the target weekday number
    for day_str, day_num in VIETNAMESE_WEEKDAY_MAP.items():
        if re.search(r'\b' + re.escape(day_str) + r'\b', term_for_weekday_search):
            target_weekday = day_num
            logger.debug(f"Found target weekday: {day_str} ({target_weekday}) in '{term_for_weekday_search}'")
            break

    if target_weekday != -1:
        today_weekday = today.weekday() # Monday is 0, Sunday is 6

        if is_next_week:
            days_to_next_monday = (7 - today_weekday) % 7
            if days_to_next_monday == 0 and today_weekday == 0: days_to_next_monday = 7
            base_date_for_next_week = today + datetime.timedelta(days=days_to_next_monday)
            final_date = base_date_for_next_week + datetime.timedelta(days=target_weekday)
        else: # Upcoming weekday
            days_ahead = (target_weekday - today_weekday + 7) % 7
            if days_ahead == 0: days_ahead = 7 # Assume next week if asking for today's weekday name
            final_date = today + datetime.timedelta(days=days_ahead)

        logger.info(f"Calculated date for '{term}' ({'next week' if is_next_week else 'upcoming'} weekday {target_weekday}): {final_date.strftime('%Y-%m-%d %A')}")
        return final_date.strftime("%Y-%m-%d")

    # --- 3. General future terms (without specific day) ---
    if any(kw in term for kw in NEXT_WEEK_KEYWORDS):
        days_to_next_monday = (7 - today.weekday()) % 7
        if days_to_next_monday == 0: days_to_next_monday = 7
        calculated_date = today + datetime.timedelta(days=days_to_next_monday)
        logger.info(f"Calculated date for general 'next week': {calculated_date.strftime('%Y-%m-%d')} (Next Monday)")
        return calculated_date.strftime("%Y-%m-%d")
    if "tháng tới" in term or "tháng sau" in term or "next month" in term:
         next_month_date = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
         logger.info(f"Calculated date for 'next month': {next_month_date.strftime('%Y-%m-%d')}")
         return next_month_date.strftime("%Y-%m-%d")

    # --- 4. Explicit date formats ---
    try:
        # YYYY-MM-DD
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', term):
             parsed_date = datetime.datetime.strptime(term, "%Y-%m-%d").date()
             logger.info(f"Term '{term}' matched YYYY-MM-DD format.")
             return parsed_date.strftime("%Y-%m-%d")
        # DD/MM/YYYY or D/M/YYYY
        if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', term):
             parsed_date = datetime.datetime.strptime(term, "%d/%m/%Y").date()
             logger.info(f"Term '{term}' matched DD/MM/YYYY format, normalized.")
             return parsed_date.strftime("%Y-%m-%d")
        # DD/MM or D/M (assume current year or next year if past)
        if re.fullmatch(r'\d{1,2}/\d{1,2}', term):
             day, month = map(int, term.split('/'))
             current_year = today.year
             try:
                  parsed_date = datetime.date(current_year, month, day)
                  if parsed_date < today:
                       parsed_date = datetime.date(current_year + 1, month, day)
                       logger.info(f"Term '{term}' (DD/MM) is past, assumed year {current_year + 1}.")
                  else:
                       logger.info(f"Term '{term}' (DD/MM) assumed current year {current_year}.")
                  return parsed_date.strftime("%Y-%m-%d")
             except ValueError:
                  logger.warning(f"Term '{term}' (DD/MM) resulted in an invalid date.")
                  pass

    except ValueError as date_parse_error:
        logger.warning(f"Term '{term}' resembled a date format but failed parsing: {date_parse_error}")
        pass

    # --- 5. Fallback ---
    logger.warning(f"Could not interpret relative date term: '{term}'. Returning None.")
    return None

def date_time_to_cron(date_str, time_str="19:00"):
    """Chuyển ngày giờ cụ thể sang Quartz cron expression."""
    try:
        if not time_str or ':' not in time_str: time_str = "19:00"
        hour, minute = map(int, time_str.split(":"))
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        quartz_cron = f"0 {minute} {hour} {date_obj.day} {date_obj.month} ? {date_obj.year}"
        logger.info(f"Generated Quartz cron ONCE for {date_str} {time_str}: {quartz_cron}")
        return quartz_cron
    except (ValueError, TypeError) as e:
        logger.error(f"Lỗi tạo cron expression ONCE cho date='{date_str}', time='{time_str}': {e}")
        return ""

def determine_repeat_type(description, title):
    """Xác định kiểu lặp lại dựa trên mô tả và tiêu đề."""
    combined_text = (str(description) + " " + str(title)).lower()
    
    # Kiểm tra các mẫu đặc biệt của sự kiện lặp lại
    weekday_patterns = [
        r"\b(tất cả|mọi|các)\b.*\b(thứ \d|thứ hai|thứ ba|thứ tư|thứ năm|thứ sáu|thứ bảy|chủ nhật|t\d|cn)\b",
        r"\b(vào|mỗi)\b.*\b(thứ \d|thứ hai|thứ ba|thứ tư|thứ năm|thứ sáu|thứ bảy|chủ nhật|t\d|cn)\b"
    ]
    
    for pattern in weekday_patterns:
        if re.search(pattern, combined_text):
            logger.info(f"Phát hiện mẫu lặp lại đặc biệt '{pattern}' trong '{combined_text[:100]}...' -> RECURRING")
            return "RECURRING"
    
    # Kiểm tra danh sách từ khóa
    for keyword in RECURRING_KEYWORDS:
        if re.search(r'\b' + re.escape(keyword) + r'\b', combined_text):
            logger.info(f"Phát hiện từ khóa lặp lại '{keyword}' -> RECURRING")
            return "RECURRING"
    
    logger.info(f"Không tìm thấy từ khóa lặp lại trong '{combined_text[:100]}...' -> ONCE")
    return "ONCE"

def generate_recurring_cron(description, title, time_str="19:00"):
    """Tạo Quartz cron expression cho sự kiện lặp lại."""
    try:
        if not time_str or ':' not in time_str: time_str = "19:00"
        hour, minute = map(int, time_str.split(":"))
        combined_text = (str(description) + " " + str(title)).lower()

        # 1. Daily
        if "hàng ngày" in combined_text or "mỗi ngày" in combined_text or "daily" in combined_text:
            quartz_cron = f"0 {minute} {hour} ? * * *"
            logger.info(f"Tạo cron Quartz HÀNG NGÀY lúc {time_str}: {quartz_cron}")
            return quartz_cron

        # 2. Weekly on specific day
        quartz_day_map = {
            "chủ nhật": 1, "cn": 1, "sunday": 1, "thứ 2": 2, "t2": 2, "monday": 2,
            "thứ 3": 3, "t3": 3, "tuesday": 3, "thứ 4": 4, "t4": 4, "wednesday": 4,
            "thứ 5": 5, "t5": 5, "thursday": 5, "thứ 6": 6, "t6": 6, "friday": 6,
            "thứ 7": 7, "t7": 7, "saturday": 7
        }
        found_day_num = None
        found_day_text = ""
        for day_text, day_num in quartz_day_map.items():
            if re.search(r'\b' + re.escape(day_text) + r'\b', combined_text):
                found_day_num = day_num
                found_day_text = day_text
                break
        if found_day_num is not None:
             is_weekly = any(kw in combined_text for kw in ["hàng tuần", "mỗi tuần", "weekly", "every"])
             if is_weekly or any(kw in combined_text for kw in RECURRING_KEYWORDS):
                 quartz_cron = f"0 {minute} {hour} ? * {found_day_num} *"
                 logger.info(f"Tạo cron Quartz HÀNG TUẦN vào Thứ {found_day_text} ({found_day_num}) lúc {time_str}: {quartz_cron}")
                 return quartz_cron
             else:
                  logger.warning(f"Tìm thấy '{found_day_text}' nhưng không rõ là hàng tuần. Không tạo cron lặp lại.")

        # 3. Monthly
        monthly_match = re.search(r"(ngày\s+(\d{1,2})|ngày\s+cuối\s+cùng)\s+(hàng\s+tháng|mỗi\s+tháng)", combined_text)
        if monthly_match:
            day_specifier = monthly_match.group(1)
            day_of_month = "L" if "cuối cùng" in day_specifier else ""
            if not day_of_month:
                day_num_match = re.search(r'\d{1,2}', day_specifier)
                if day_num_match: day_of_month = day_num_match.group(0)

            if day_of_month:
                quartz_cron = f"0 {minute} {hour} {day_of_month} * ? *"
                logger.info(f"Tạo cron Quartz HÀNG THÁNG vào ngày {day_of_month} lúc {time_str}: {quartz_cron}")
                return quartz_cron

        # 4. Fallback
        logger.warning(f"Không thể xác định lịch lặp lại cụ thể từ '{combined_text[:100]}...'. Cron sẽ rỗng.")
        return ""

    except Exception as e:
        logger.error(f"Lỗi khi tạo cron Quartz lặp lại: {e}", exc_info=True)
        return ""

# ------- Classes & Models -------------

class SessionManager:
    """Quản lý session và trạng thái cho mỗi client với khả năng lưu trạng thái"""
    def __init__(self, sessions_file=SESSIONS_DATA_FILE): # Use constant
        self.sessions = {}
        self.sessions_file = sessions_file
        self._load_sessions()

    def _load_sessions(self):
        """Tải dữ liệu session từ file"""
        try:
            if os.path.exists(self.sessions_file):
                with open(self.sessions_file, "r", encoding="utf-8") as f:
                    loaded_sessions = json.load(f)
                    if isinstance(loaded_sessions, dict):
                        self.sessions = loaded_sessions
                        logger.info(f"Đã tải {len(self.sessions)} session từ {self.sessions_file}")
                    else:
                        logger.warning(f"Dữ liệu session trong {self.sessions_file} không hợp lệ (không phải dict), khởi tạo lại.")
                        self.sessions = {} # Reset if invalid structure
        except json.JSONDecodeError as e:
            logger.error(f"Lỗi JSON khi tải session từ {self.sessions_file}: {e}. Khởi tạo lại.")
            self.sessions = {} # Reset on JSON error
        except Exception as e:
            logger.error(f"Lỗi không xác định khi tải session: {e}", exc_info=True)
            self.sessions = {} # Reset on other errors


    def _save_sessions(self):
        """Lưu dữ liệu session vào file"""
        try:
            os.makedirs(os.path.dirname(self.sessions_file) or '.', exist_ok=True)
            with open(self.sessions_file, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, ensure_ascii=False, indent=2)
            logger.debug(f"Đã lưu {len(self.sessions)} session vào {self.sessions_file}") # Reduced log level
            return True
        except Exception as e:
            logger.error(f"Lỗi khi lưu session: {e}", exc_info=True)
            return False

    def get_session(self, session_id):
        """Lấy session hoặc tạo mới nếu chưa tồn tại"""
        if session_id not in self.sessions:
            logger.info(f"Tạo session mới: {session_id}")
            self.sessions[session_id] = {
                "messages": [],
                "current_member": None,
                "suggested_question": None,
                "process_suggested": False,
                "question_cache": {},
                "created_at": datetime.datetime.now().isoformat(),
                "last_updated": datetime.datetime.now().isoformat()
            }
            self._save_sessions() # Save immediately after creation
        return self.sessions[session_id]

    def update_session(self, session_id, data):
        """Cập nhật dữ liệu session"""
        if session_id in self.sessions:
            try:
                self.sessions[session_id].update(data)
                self.sessions[session_id]["last_updated"] = datetime.datetime.now().isoformat()
                # Consider saving less often if performance is an issue.
                if not self._save_sessions():
                     logger.error(f"Cập nhật session {session_id} thành công trong bộ nhớ nhưng LƯU THẤT BẠI.")
                return True
            except Exception as e:
                logger.error(f"Lỗi khi cập nhật session {session_id} trong bộ nhớ: {e}", exc_info=True)
                return False
        else:
             logger.warning(f"Cố gắng cập nhật session không tồn tại: {session_id}")
             return False

    def delete_session(self, session_id):
        """Xóa session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save_sessions()
            logger.info(f"Đã xóa session: {session_id}")
            return True
        return False

    def cleanup_old_sessions(self, days_threshold=30):
        """Xóa các session cũ không hoạt động sau số ngày nhất định"""
        now = datetime.datetime.now(datetime.timezone.utc) # Use timezone-aware datetime
        sessions_to_remove = []
        removed_count = 0

        for session_id, session_data in list(self.sessions.items()): # Iterate over a copy
            last_updated_str = session_data.get("last_updated")
            if last_updated_str:
                try:
                    last_updated_date = datetime.datetime.fromisoformat(last_updated_str)
                    if last_updated_date.tzinfo is None:
                        # Assuming stored time is UTC, make it aware
                        last_updated_date = last_updated_date.replace(tzinfo=datetime.timezone.utc)

                    time_inactive = now - last_updated_date
                    if time_inactive.days > days_threshold:
                        sessions_to_remove.append(session_id)
                except ValueError:
                    logger.error(f"Định dạng last_updated không hợp lệ ('{last_updated_str}') cho session {session_id}. Xem xét xóa.")
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý thời gian cho session {session_id}: {e}", exc_info=True)

        if sessions_to_remove:
             for session_id in sessions_to_remove:
                 if session_id in self.sessions:
                     del self.sessions[session_id]
                     removed_count += 1
             if removed_count > 0:
                 self._save_sessions()
                 logger.info(f"Đã xóa {removed_count} session cũ (quá {days_threshold} ngày không hoạt động).")
             else:
                  logger.info("Không có session cũ nào cần xóa.")
        else:
            logger.info("Không có session cũ nào cần xóa.")

# --- Tool Definitions (JSON Schema) ---
available_tools = [
    {
        "type": "function",
        "function": {
            "name": "add_family_member",
            "description": "Thêm một thành viên mới vào danh sách gia đình.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tên đầy đủ của thành viên."},
                    "age": {"type": "string", "description": "Tuổi của thành viên (ví dụ: '30', 'khoảng 10')."},
                    "preferences": {
                        "type": "object",
                        "description": "Sở thích của thành viên (ví dụ: {'food': 'Phở', 'hobby': 'Đọc sách'}).",
                        "properties": {
                            "food": {"type": "string", "description": "Món ăn yêu thích."},
                            "hobby": {"type": "string", "description": "Sở thích chính."},
                            "color": {"type": "string", "description": "Màu sắc yêu thích."}
                        },
                         "additionalProperties": {"type": "string"}
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_preference",
            "description": "Cập nhật một sở thích cụ thể cho một thành viên gia đình đã biết.",
            "parameters": {
                "type": "object",
                "properties": {
                    "member_id": {"type": "string", "description": "ID của thành viên cần cập nhật (lấy từ thông tin gia đình trong context)."},
                    "preference_key": {"type": "string", "description": "Loại sở thích cần cập nhật (ví dụ: 'food', 'hobby', 'color')."},
                    "preference_value": {"type": "string", "description": "Giá trị mới cho sở thích đó."}
                },
                "required": ["member_id", "preference_key", "preference_value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": "Thêm một sự kiện mới vào lịch gia đình. Hệ thống sẽ tự động tính toán ngày chính xác từ mô tả.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Tiêu đề của sự kiện."},
                    "date_description": {"type": "string", "description": "Mô tả về ngày diễn ra sự kiện THEO LỜI NGƯỜI DÙNG (ví dụ: 'ngày mai', 'thứ 6 tuần sau', '25/12/2024', '20/7'). Không tự tính toán ngày."},
                    "time": {"type": "string", "description": "Thời gian diễn ra sự kiện (HH:MM). Mặc định là 19:00 nếu không được cung cấp.", "default": "19:00"},
                    "description": {"type": "string", "description": "Mô tả chi tiết về sự kiện, bao gồm cả thông tin lặp lại nếu có (ví dụ: 'Họp gia đình hàng tháng', 'học tiếng Anh mỗi tối thứ 6')."},
                    "participants": {"type": "array", "items": {"type": "string"}, "description": "Danh sách tên những người tham gia."}
                },
                "required": ["title", "date_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "Cập nhật thông tin cho một sự kiện đã tồn tại trong lịch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID của sự kiện cần cập nhật (lấy từ danh sách sự kiện trong context)."},
                    "title": {"type": "string", "description": "Tiêu đề mới cho sự kiện."},
                    "date_description": {"type": "string", "description": "Mô tả MỚI về ngày diễn ra sự kiện THEO LỜI NGƯỜI DÙNG (nếu thay đổi)."},
                    "time": {"type": "string", "description": "Thời gian mới (HH:MM)."},
                    "description": {"type": "string", "description": "Mô tả chi tiết mới, bao gồm thông tin lặp lại nếu có."},
                    "participants": {"type": "array", "items": {"type": "string"}, "description": "Danh sách người tham gia mới."}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Xóa một sự kiện khỏi lịch gia đình.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID của sự kiện cần xóa (lấy từ danh sách sự kiện trong context)."}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Thêm một ghi chú mới.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Tiêu đề của ghi chú."},
                    "content": {"type": "string", "description": "Nội dung chi tiết của ghi chú."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Danh sách các thẻ (tags) liên quan đến ghi chú."}
                },
                "required": ["title", "content"]
            }
        }
    }
]


# ------- Load/Save Data & Verification --------
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                     logger.warning(f"Dữ liệu trong {file_path} không phải từ điển. Khởi tạo lại.")
                     return {}
                return data
        except json.JSONDecodeError as e:
            logger.error(f"Lỗi JSON khi đọc {file_path}: {e}. Trả về dữ liệu trống.")
            return {}
        except Exception as e:
            logger.error(f"Lỗi không xác định khi đọc {file_path}: {e}", exc_info=True)
            return {}
    return {}

def save_data(file_path, data):
    try:
        os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
        temp_file_path = file_path + ".tmp"
        with open(temp_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        shutil.move(temp_file_path, file_path)
        return True
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}", exc_info=True)
        if os.path.exists(temp_file_path):
             try: os.remove(temp_file_path)
             except OSError as rm_err: logger.error(f"Không thể xóa file tạm {temp_file_path}: {rm_err}")
        return False

def verify_data_structure():
    """Kiểm tra và đảm bảo cấu trúc dữ liệu ban đầu."""
    global family_data, events_data, notes_data, chat_history
    needs_save = False

    if not isinstance(family_data, dict):
        logger.warning("family_data không phải từ điển. Khởi tạo lại.")
        family_data = {}
        needs_save = True

    if not isinstance(events_data, dict):
        logger.warning("events_data không phải từ điển. Khởi tạo lại.")
        events_data = {}
        needs_save = True

    if not isinstance(notes_data, dict):
        logger.warning("notes_data không phải từ điển. Khởi tạo lại.")
        notes_data = {}
        needs_save = True

    if not isinstance(chat_history, dict):
        logger.warning("chat_history không phải từ điển. Khởi tạo lại.")
        chat_history = {}
        needs_save = True

    if needs_save:
        logger.info("Lưu lại cấu trúc dữ liệu mặc định do phát hiện lỗi.")
        save_data(FAMILY_DATA_FILE, family_data)
        save_data(EVENTS_DATA_FILE, events_data)
        save_data(NOTES_DATA_FILE, notes_data)
        save_data(CHAT_HISTORY_FILE, chat_history)

# --- Data Management Functions ---
def add_family_member(details):
    """Thêm thành viên mới."""
    global family_data
    try:
        member_id = str(uuid.uuid4())
        if not details.get("name"):
             logger.error("Không thể thêm thành viên: thiếu tên.")
             return False
        family_data[member_id] = {
            "id": member_id,
            "name": details.get("name"),
            "age": details.get("age", ""),
            "preferences": details.get("preferences", {}),
            "added_on": datetime.datetime.now().isoformat()
        }
        if save_data(FAMILY_DATA_FILE, family_data):
             logger.info(f"Đã thêm thành viên ID {member_id}: {details.get('name')}")
             return True
        else:
             logger.error(f"Lưu thất bại sau khi thêm thành viên {member_id} vào bộ nhớ.")
             if member_id in family_data: del family_data[member_id]
             return False
    except Exception as e:
         logger.error(f"Lỗi khi thêm thành viên: {e}", exc_info=True)
         return False

def update_preference(details):
    """Cập nhật sở thích."""
    global family_data
    try:
        member_id = str(details.get("member_id"))
        preference_key = details.get("preference_key")
        preference_value = details.get("preference_value")

        if not member_id or not preference_key or preference_value is None:
             logger.error(f"Thiếu thông tin để cập nhật sở thích: {details}")
             return False

        if member_id in family_data:
            if "preferences" not in family_data[member_id] or not isinstance(family_data[member_id]["preferences"], dict):
                family_data[member_id]["preferences"] = {}

            original_value = family_data[member_id]["preferences"].get(preference_key)
            family_data[member_id]["preferences"][preference_key] = preference_value
            family_data[member_id]["last_updated"] = datetime.datetime.now().isoformat()

            if save_data(FAMILY_DATA_FILE, family_data):
                logger.info(f"Đã cập nhật sở thích '{preference_key}' cho thành viên {member_id}")
                return True
            else:
                 logger.error(f"Lưu thất bại sau khi cập nhật sở thích cho {member_id}.")
                 if original_value is not None:
                      family_data[member_id]["preferences"][preference_key] = original_value
                 else:
                      if preference_key in family_data[member_id]["preferences"]:
                           del family_data[member_id]["preferences"][preference_key]
                 return False
        else:
            logger.warning(f"Không tìm thấy thành viên ID={member_id} để cập nhật sở thích.")
            return False
    except Exception as e:
         logger.error(f"Lỗi khi cập nhật sở thích: {e}", exc_info=True)
         return False

EVENT_CATEGORIES = {
    "Health": ["khám sức khỏe", "uống thuốc", "bác sĩ", "nha sĩ", "tái khám", "tập luyện", "gym", "yoga", "chạy bộ", "thể dục"],
    "Study": ["học", "lớp học", "ôn tập", "bài tập", "deadline", "thuyết trình", "seminar", "workshop", "thi", "kiểm tra"],
    "Meeting": ["họp", "hội nghị", "phỏng vấn", "gặp mặt", "trao đổi", "thảo luận", "team sync", "standup", "meeting"],
    "Travel": ["bay", "chuyến bay", "tàu", "xe", "đi công tác", "du lịch", "sân bay", "ga tàu", "di chuyển", "check-in", "check-out"],
    "Event": ["sinh nhật", "kỷ niệm", "lễ", "tiệc", "liên hoan", "đám cưới", "đám hỏi", "ăn mừng", "tụ tập", "sum họp", "event"],
    "Personal": ["riêng tư", "cá nhân", "sở thích", "đọc sách", "xem phim", "thời gian riêng", "cắt tóc", "spa", "làm đẹp"],
    "Reminder": ["nhắc", "nhớ", "mua", "gọi điện", "thanh toán", "đặt lịch", "nộp", "đến hạn", "chuyển tiền", "lấy đồ"],
    "Break": ["nghỉ ngơi", "thư giãn", "giải lao", "ăn trưa", "ăn tối", "ngủ trưa", "nghỉ phép"],
    # Thêm một category mặc định cuối cùng
    "General": [] # Dùng làm fallback
}

# Ưu tiên các category cụ thể hơn
CATEGORY_PRIORITY = [
    "Health", "Study", "Meeting", "Travel", "Reminder", "Event", "Personal", "Break", "General"
]

def classify_event(title: str, description: Optional[str]) -> str:
    """
    Phân loại sự kiện vào một category dựa trên tiêu đề và mô tả.
    """
    if not title:
        return "General" # Không có tiêu đề thì khó phân loại

    combined_text = title.lower()
    if description:
        combined_text += " " + description.lower()

    logger.debug(f"Classifying event with text: '{combined_text[:100]}...'")

    for category in CATEGORY_PRIORITY:
        keywords = EVENT_CATEGORIES.get(category, [])
        if not keywords and category != "General": # Bỏ qua nếu category (trừ General) không có keyword
             continue
        # Kiểm tra các keywords của category hiện tại
        for keyword in keywords:
            # Sử dụng regex để tìm từ khóa đứng riêng lẻ (word boundary)
            if re.search(r'\b' + re.escape(keyword) + r'\b', combined_text):
                logger.info(f"Event classified as '{category}' based on keyword '{keyword}'")
                return category

    # Nếu không khớp với category nào có keyword, trả về General
    logger.info(f"Event could not be specifically classified, defaulting to 'General'")
    return "General"

def add_event(details):
    """Thêm một sự kiện mới. Expects 'date' to be calculated YYYY-MM-DD."""
    global events_data
    try:
        event_id = str(uuid.uuid4())
        # Kiểm tra các trường bắt buộc cơ bản
        if not details.get('title') or ('date' not in details and details.get("repeat_type", "ONCE") == "ONCE"): # Cần date nếu là ONCE
            logger.error(f"Thiếu title hoặc date (cho sự kiện ONCE) khi thêm sự kiện: {details}")
            return False

        # Lấy category từ details, nếu không có thì dùng mặc định 'General'
        category = details.get("category", "General") # Lấy category đã được phân loại
        logger.info(f"Adding event with category: {category}")

        events_data[event_id] = {
            "id": event_id,
            "title": details.get("title"),
            "date": details.get("date"), # Có thể là None nếu là RECURRING không rõ ngày bắt đầu
            "time": details.get("time", "19:00"),
            "description": details.get("description", ""),
            "participants": details.get("participants", []),
            "repeat_type": details.get("repeat_type", "ONCE"),
            "category": category, # <<< THÊM CATEGORY VÀO ĐÂY
            "created_by": details.get("created_by"),
            "created_on": datetime.datetime.now().isoformat()
        }
        if save_data(EVENTS_DATA_FILE, events_data):
             logger.info(f"Đã thêm sự kiện ID {event_id}: {details.get('title')} (Category: {category})")
             return True
        else:
             logger.error(f"Lưu sự kiện ID {event_id} thất bại.")
             if event_id in events_data: del events_data[event_id]
             return False
    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng khi thêm sự kiện: {e}", exc_info=True)
        return False

def update_event(details):
    """Cập nhật sự kiện. Expects 'date' to be calculated YYYY-MM-DD if provided."""
    global events_data
    event_id_str = str(details.get("id"))
    original_event_copy = None

    try:
        if not event_id_str or event_id_str not in events_data:
            logger.warning(f"Không tìm thấy sự kiện ID={event_id_str} để cập nhật.")
            return False

        original_event_copy = events_data.get(event_id_str, {}).copy()
        if not original_event_copy:
             logger.error(f"Không thể tạo bản sao cho event ID {event_id_str}.")
             return False

        updated = False
        event_to_update = events_data[event_id_str]

        for key, value in details.items():
            if key == "id": continue
            current_value = event_to_update.get(key)
            if value != current_value:
                if key == 'date' and not value and event_to_update.get("repeat_type", "ONCE") == "ONCE": # Chỉ cảnh báo nếu là ONCE và date bị xóa
                    logger.warning(f"Bỏ qua cập nhật date thành giá trị rỗng cho event ONCE ID {event_id_str}")
                    continue
                event_to_update[key] = value
                updated = True
                logger.debug(f"Event {event_id_str}: Updated field '{key}' to '{value}'") # Log giá trị mới

        if updated:
            # Lấy category mới nếu có, nếu không giữ nguyên category cũ
            new_category = details.get("category", event_to_update.get("category", "General"))
            if event_to_update.get("category") != new_category:
                 event_to_update["category"] = new_category
                 logger.info(f"Event {event_id_str}: Category updated to '{new_category}'")
            else:
                 logger.debug(f"Event {event_id_str}: Category remains '{new_category}'")

            event_to_update["last_updated"] = datetime.datetime.now().isoformat()
            logger.info(f"Attempting to save updated event ID={event_id_str}")
            if save_data(EVENTS_DATA_FILE, events_data):
                logger.info(f"Đã cập nhật và lưu thành công sự kiện ID={event_id_str}")
                return True
            else:
                 logger.error(f"Lưu cập nhật sự kiện ID {event_id_str} thất bại.")
                 if event_id_str in events_data and original_event_copy:
                      events_data[event_id_str] = original_event_copy
                      logger.info(f"Đã rollback thay đổi trong bộ nhớ cho event ID {event_id_str} do lưu thất bại.")
                 return False
        else:
             logger.info(f"Không có thay đổi nào được áp dụng cho sự kiện ID={event_id_str}")
             return True # No changes is success

    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng khi cập nhật sự kiện ID {details.get('id')}: {e}", exc_info=True)
        if event_id_str and event_id_str in events_data and original_event_copy:
             events_data[event_id_str] = original_event_copy
             logger.info(f"Đã rollback thay đổi trong bộ nhớ cho event ID {event_id_str} do lỗi xử lý.")
        return False

def delete_event(details):
    """Xóa sự kiện dựa trên ID trong details dict."""
    global events_data
    event_id_to_delete = str(details.get("event_id"))
    if not event_id_to_delete:
         logger.error("Thiếu event_id để xóa sự kiện.")
         return False
    try:
        if event_id_to_delete in events_data:
            deleted_event_copy = events_data.pop(event_id_to_delete)
            if save_data(EVENTS_DATA_FILE, events_data):
                 logger.info(f"Đã xóa sự kiện ID {event_id_to_delete}")
                 return True
            else:
                 logger.error(f"Lưu sau khi xóa sự kiện ID {event_id_to_delete} thất bại.")
                 events_data[event_id_to_delete] = deleted_event_copy
                 logger.info(f"Đã rollback xóa trong bộ nhớ cho event ID {event_id_to_delete}.")
                 return False
        else:
            logger.warning(f"Không tìm thấy sự kiện ID {event_id_to_delete} để xóa.")
            return False
    except Exception as e:
         logger.error(f"Lỗi khi xóa sự kiện ID {event_id_to_delete}: {e}", exc_info=True)
         return False


def add_note(details):
    """Thêm ghi chú mới."""
    global notes_data
    try:
        note_id = str(uuid.uuid4())
        if not details.get("title") or not details.get("content"):
             logger.error(f"Thiếu title hoặc content khi thêm note: {details}")
             return False

        notes_data[note_id] = {
            "id": note_id,
            "title": details.get("title"),
            "content": details.get("content"),
            "tags": details.get("tags", []),
            "created_by": details.get("created_by"),
            "created_on": datetime.datetime.now().isoformat()
        }
        if save_data(NOTES_DATA_FILE, notes_data):
            logger.info(f"Đã thêm ghi chú ID {note_id}: {details.get('title')}")
            return True
        else:
             logger.error(f"Lưu thất bại sau khi thêm note {note_id} vào bộ nhớ.")
             if note_id in notes_data: del notes_data[note_id]
             return False
    except Exception as e:
         logger.error(f"Lỗi khi thêm note: {e}", exc_info=True)
         return False

# Map tool names to actual Python functions
tool_functions = {
    "add_family_member": add_family_member,
    "update_preference": update_preference,
    "add_event": add_event,
    "update_event": update_event,
    "delete_event": delete_event,
    "add_note": add_note,
}

# Load initial data after functions are defined
family_data = load_data(FAMILY_DATA_FILE)
events_data = load_data(EVENTS_DATA_FILE)
notes_data = load_data(NOTES_DATA_FILE)
chat_history = load_data(CHAT_HISTORY_FILE)
verify_data_structure() # Verify after loading

# Initialize Session Manager
session_manager = SessionManager(SESSIONS_DATA_FILE)

# -------Date -----------

class DateTimeHandler:
    """Lớp xử lý ngày giờ sử dụng dateparser với hỗ trợ nâng cao cho tiếng Việt."""

    # Ánh xạ thứ tiếng Việt
    VIETNAMESE_WEEKDAY_MAP = {
        "thứ 2": 0, "thứ hai": 0, "t2": 0,
        "thứ 3": 1, "thứ ba": 1, "t3": 1,
        "thứ 4": 2, "thứ tư": 2, "t4": 2,
        "thứ 5": 3, "thứ năm": 3, "t5": 3,
        "thứ 6": 4, "thứ sáu": 4, "t6": 4,
        "thứ 7": 5, "thứ bảy": 5, "t7": 5,
        "chủ nhật": 6, "cn": 6,
    }
    
    # Ánh xạ thời gian trong ngày tiếng Việt
    VIETNAMESE_TIME_OF_DAY = {
        "sáng": {"start_hour": 6, "end_hour": 11, "default_hour": 8},
        "trưa": {"start_hour": 11, "end_hour": 14, "default_hour": 12},
        "chiều": {"start_hour": 14, "end_hour": 18, "default_hour": 16},
        "tối": {"start_hour": 18, "end_hour": 22, "default_hour": 19},
        "đêm": {"start_hour": 22, "end_hour": 6, "default_hour": 22},
    }
    
    # Từ khóa thời gian tương đối tiếng Việt
    VIETNAMESE_RELATIVE_TIME = {
        "hôm nay": 0,
        "bây giờ": 0,
        "hiện tại": 0,
        "nay": 0, 
        "ngày mai": 1,
        "mai": 1,
        "ngày mốt": 2,
        "mốt": 2,
        "ngày kia": 2,
        "hôm qua": -1,
        "qua": -1,
        "hôm kia": -2,
        "tuần này": 0,
        "tuần sau": 7,
        "tuần tới": 7,
        "tuần trước": -7,
        "tháng này": 0,
        "tháng sau": 30,
        "tháng tới": 30,
        "tháng trước": -30,
    }

    # Từ khóa lặp lại
    RECURRING_KEYWORDS = [
    "hàng ngày", "mỗi ngày", "hàng tuần", "mỗi tuần", "hàng tháng", "mỗi tháng",
    "hàng năm", "mỗi năm", "định kỳ", "lặp lại",
    "mỗi sáng thứ", "mỗi trưa thứ", "mỗi chiều thứ", "mỗi tối thứ",
    "thứ 2 hàng tuần", "mỗi thứ 2", "mỗi t2", "thứ 3 hàng tuần", "mỗi thứ 3", "mỗi t3",
    "thứ 4 hàng tuần", "mỗi thứ 4", "mỗi t4", "thứ 5 hàng tuần", "mỗi thứ 5", "mỗi t5",
    "thứ 6 hàng tuần", "mỗi thứ 6", "mỗi t6", "thứ 7 hàng tuần", "mỗi thứ 7", "mỗi t7",
    "chủ nhật hàng tuần", "mỗi chủ nhật", "mỗi cn",
    "daily", "every day", "weekly", "every week", "monthly", "every month",
    "yearly", "annually", "every year", "recurring", "repeating",
    "every monday", "every tuesday", "every wednesday", "every thursday",
    "every friday", "every saturday", "every sunday",
    # Thêm từ khóa mới
    "tất cả", "mọi", "các", "tất cả thứ", "mọi thứ", "các thứ",
    "tất cả tối thứ", "mọi tối thứ", "các tối thứ",
    "tất cả sáng thứ", "mọi sáng thứ", "các sáng thứ",
    "tất cả chiều thứ", "mọi chiều thứ", "các chiều thứ",
    "vào các", "vào tất cả", "vào mọi"
]

    # Cài đặt mặc định cho dateparser
    DEFAULT_DATEPARSER_SETTINGS = {
        'TIMEZONE': 'Asia/Ho_Chi_Minh',
        'RETURN_AS_TIMEZONE_AWARE': True,
        'PREFER_DAY_OF_MONTH': 'current',
        'PREFER_DATES_FROM': 'future',
        'DATE_ORDER': 'DMY',
        'STRICT_PARSING': False,
        'RELATIVE_BASE': datetime.datetime.now()
    }

    @classmethod
    def parse_date(cls, date_description: str, base_date: Optional[datetime.datetime] = None) -> Optional[datetime.date]:
        """
        Phân tích mô tả ngày thành đối tượng datetime.date.
        Hỗ trợ tiếng Việt và các mô tả tương đối.

        Args:
            date_description: Mô tả ngày (ví dụ: "ngày mai", "thứ 6 tuần sau", "25/12/2024")
            base_date: Ngày cơ sở để tính toán tương đối (mặc định là hôm nay)

        Returns:
            datetime.date hoặc None nếu không thể phân tích
        """
        if not date_description:
            logger.warning("Mô tả ngày rỗng.")
            return None

        date_description = date_description.lower().strip()
        today = base_date.date() if base_date else datetime.date.today()

        # 0. Xử lý nhanh các từ khóa thời gian trong ngày (sáng nay, tối nay, etc.) - Giữ nguyên
        for time_of_day in cls.VIETNAMESE_TIME_OF_DAY.keys():
            if f"{time_of_day} nay" in date_description or date_description == time_of_day:
                logger.info(f"Phát hiện thời điểm trong ngày: '{time_of_day} nay/nay' -> ngày hôm nay")
                return today

        # 1. Xử lý trực tiếp các từ khóa thời gian tương đối đặc biệt tiếng Việt - Giữ nguyên
        for rel_time, days_offset in cls.VIETNAMESE_RELATIVE_TIME.items():
            # Check for exact match or start of string to avoid partial matches in longer descriptions
            if rel_time == date_description or date_description.startswith(f"{rel_time} "):
                result_date = today + datetime.timedelta(days=days_offset)
                logger.info(f"Phát hiện từ khóa tương đối '{rel_time}' -> {result_date}")
                return result_date

        # --- START: DI CHUYỂN KHỐI XỬ LÝ THỨ LÊN TRÊN ---
        # 3. Xử lý các trường hợp đặc biệt tiếng Việt (ƯU TIÊN TÊN THỨ)
        try:
            # Xử lý thứ trong tuần (ƯU TIÊN HÀNG ĐẦU)
            for weekday_name, weekday_num in cls.VIETNAMESE_WEEKDAY_MAP.items():
                # Use regex for whole word matching to avoid partial matches (e.g., "thứ 2" vs "thứ 20")
                if re.search(r'\b' + re.escape(weekday_name) + r'\b', date_description):
                    is_next_week = "tuần sau" in date_description or "tuần tới" in date_description
                    current_weekday = today.weekday() # Monday is 0, Sunday is 6

                    if is_next_week:
                        # Tính ngày thứ X tuần sau
                        days_to_next_monday = (7 - current_weekday) % 7
                        # If today is Monday, next Monday is 7 days later
                        if days_to_next_monday == 0: days_to_next_monday = 7

                        next_monday = today + datetime.timedelta(days=days_to_next_monday)
                        target_date = next_monday + datetime.timedelta(days=weekday_num)
                    else:
                        # Tính ngày thứ X gần nhất trong tương lai
                        days_ahead = (weekday_num - current_weekday + 7) % 7
                        # If asking for today's weekday name, assume next week's
                        if days_ahead == 0:
                             days_ahead = 7

                        target_date = today + datetime.timedelta(days=days_ahead)

                    logger.info(f"Xử lý thủ công (Ưu tiên Thứ): '{date_description}' thành: {target_date}")
                    return target_date # TRẢ VỀ NGAY KHI TÌM THẤY THỨ

            # Xử lý "đầu tháng", "cuối tháng", "giữa tháng" (chỉ khi không có thứ)
            if "đầu tháng" in date_description:
                target_day = 5 # Default to 5th
                if "sau" in date_description or "tới" in date_description:
                    target_month_base = today.replace(day=1) + relativedelta(months=1)
                else:
                    target_month_base = today
                    # If it's already past the target day this month, use next month
                    if today.day > target_day + 5: # Add buffer
                         target_month_base = today.replace(day=1) + relativedelta(months=1)
                return target_month_base.replace(day=target_day)


            if "giữa tháng" in date_description:
                target_day = 15
                if "sau" in date_description or "tới" in date_description:
                     target_month_base = today.replace(day=1) + relativedelta(months=1)
                else:
                     target_month_base = today
                     if today.day > target_day + 5: # Add buffer
                          target_month_base = today.replace(day=1) + relativedelta(months=1)
                return target_month_base.replace(day=target_day)

            if "cuối tháng" in date_description:
                if "sau" in date_description or "tới" in date_description:
                    base_for_last_day = today.replace(day=1) + relativedelta(months=1)
                else:
                    base_for_last_day = today
                last_day_of_month = (base_for_last_day.replace(day=1) + relativedelta(months=1, days=-1)).day
                return base_for_last_day.replace(day=last_day_of_month)


            # Xử lý định dạng DD/MM hoặc D/M (chỉ khi không có thứ)
            if re.fullmatch(r'\d{1,2}/\d{1,2}', date_description):
                day, month = map(int, date_description.split('/'))

                try:
                    parsed_date = datetime.date(today.year, month, day)
                    # Nếu ngày đã qua trong năm nay, sử dụng năm sau
                    if parsed_date < today:
                        parsed_date = datetime.date(today.year + 1, month, day)

                    logger.info(f"Xử lý định dạng DD/MM '{date_description}' thành: {parsed_date}")
                    return parsed_date
                except ValueError as date_err:
                    logger.warning(f"Ngày không hợp lệ từ DD/MM '{date_description}': {date_err}")
                    # Fall through to dateparser or return None

        except Exception as e:
            logger.warning(f"Xử lý thủ công gặp lỗi với '{date_description}': {e}")
            # Fall through to dateparser or return None

        # --- END: DI CHUYỂN KHỐI XỬ LÝ THỨ LÊN TRÊN ---


        # 2. Thử dùng dateparser (SAU KHI ĐÃ KIỂM TRA THỨ)
        settings = cls.DEFAULT_DATEPARSER_SETTINGS.copy()
        if base_date:
            settings['RELATIVE_BASE'] = base_date

        # Tăng cường PREFER_DATES_FROM để dateparser ít ưu tiên ngày trong tháng hơn khi có từ khác
        # settings['PREFER_DATES_FROM'] = 'future' # Giữ nguyên hoặc thử 'relative-future'

        try:
            # Add some fixes for Vietnamese time expressions
            dp_input = date_description
            # Replace 'tối nay' etc. only if they affect date parsing significantly
            # Removed the replacement here as time is handled separately

            parsed_date_obj = dateparser.parse(
                dp_input,
                languages=['vi', 'en'],
                settings=settings
            )

            if parsed_date_obj:
                # Convert timezone-aware datetime from dateparser to simple date
                parsed_date = parsed_date_obj.date()
                logger.info(f"Dateparser phân tích '{date_description}' thành: {parsed_date}")
                return parsed_date
        except Exception as e:
            logger.warning(f"Dateparser gặp lỗi khi phân tích '{date_description}': {e}")


        # 4. Xử lý đặc biệt cho các mô tả thời gian của ngày (nếu chỉ có thời gian) - Giữ nguyên
        for time_of_day in cls.VIETNAMESE_TIME_OF_DAY.keys():
             # Check for exact match to avoid matching parts of other words
             if date_description == time_of_day:
                 logger.info(f"Phát hiện chỉ có thời điểm trong ngày: '{time_of_day}' -> Giả định ngày hôm nay")
                 return today

        # 5. Trả về None nếu không thể phân tích - Giữ nguyên
        logger.warning(f"Không thể phân tích mô tả ngày: '{date_description}'")
        return None
    
    @classmethod
    def format_date(cls, date_obj: datetime.date) -> str:
        """
        Định dạng đối tượng datetime.date thành chuỗi ISO YYYY-MM-DD.
        
        Args:
            date_obj: Đối tượng datetime.date
            
        Returns:
            Chuỗi định dạng YYYY-MM-DD
        """
        if not date_obj:
            return None
        return date_obj.strftime("%Y-%m-%d")
    
    @classmethod
    def determine_repeat_type(cls, description: str, title: str) -> str:
        """
        Xác định kiểu lặp lại dựa trên mô tả và tiêu đề.
        
        Args:
            description: Mô tả sự kiện
            title: Tiêu đề sự kiện
            
        Returns:
            "RECURRING" hoặc "ONCE"
        """
        combined_text = (str(description) + " " + str(title)).lower()
        
        for keyword in cls.RECURRING_KEYWORDS:
            if re.search(r'\b' + re.escape(keyword) + r'\b', combined_text):
                logger.info(f"Phát hiện từ khóa lặp lại '{keyword}' -> RECURRING")
                return "RECURRING"
                
        logger.info(f"Không tìm thấy từ khóa lặp lại -> ONCE")
        return "ONCE"
    
    @classmethod
    def generate_cron_expression(cls, date_str: str, time_str: str, repeat_type: str, 
                                description: str, title: str) -> str:
        """
        Tạo biểu thức Quartz cron dựa trên thông tin sự kiện.
        
        Args:
            date_str: Ngày định dạng YYYY-MM-DD
            time_str: Thời gian định dạng HH:MM
            repeat_type: Kiểu lặp lại ("RECURRING" hoặc "ONCE")
            description: Mô tả sự kiện
            title: Tiêu đề sự kiện
            
        Returns:
            Biểu thức Quartz cron
        """
        try:
            if not time_str or ':' not in time_str:
                time_str = "19:00"
                
            hour, minute = map(int, time_str.split(":"))
            
            if repeat_type == "RECURRING":
                combined_text = (str(description) + " " + str(title)).lower()
                
                # 1. Hàng ngày
                if "hàng ngày" in combined_text or "mỗi ngày" in combined_text or "daily" in combined_text:
                    cron = f"0 {minute} {hour} ? * * *"
                    logger.info(f"Tạo cron Quartz HÀNG NGÀY lúc {time_str}: {cron}")
                    return cron
                
                # 2. Hàng tuần vào thứ cụ thể
                quartz_day_map = {
                    "chủ nhật": 1, "cn": 1, "sunday": 1, "thứ 2": 2, "t2": 2, "monday": 2,
                    "thứ 3": 3, "t3": 3, "tuesday": 3, "thứ 4": 4, "t4": 4, "wednesday": 4,
                    "thứ 5": 5, "t5": 5, "thursday": 5, "thứ 6": 6, "t6": 6, "friday": 6,
                    "thứ 7": 7, "t7": 7, "saturday": 7
                }
                
                for day_text, day_num in quartz_day_map.items():
                    if re.search(r'\b' + re.escape(day_text) + r'\b', combined_text):
                        is_weekly = any(kw in combined_text for kw in ["hàng tuần", "mỗi tuần", "weekly", "every"])
                        
                        if is_weekly or any(kw in combined_text for kw in cls.RECURRING_KEYWORDS):
                            cron = f"0 {minute} {hour} ? * {day_num} *"
                            logger.info(f"Tạo cron Quartz HÀNG TUẦN vào Thứ {day_text} ({day_num}) lúc {time_str}: {cron}")
                            return cron
                
                # 3. Hàng tháng vào ngày cụ thể
                monthly_match = re.search(r"(ngày\s+(\d{1,2})|ngày\s+cuối\s+cùng)\s+(hàng\s+tháng|mỗi\s+tháng)", combined_text)
                if monthly_match:
                    day_specifier = monthly_match.group(1)
                    day_of_month = "L" if "cuối cùng" in day_specifier else ""
                    if not day_of_month:
                        day_num_match = re.search(r'\d{1,2}', day_specifier)
                        if day_num_match:
                            day_of_month = day_num_match.group(0)
                    
                    if day_of_month:
                        cron = f"0 {minute} {hour} {day_of_month} * ? *"
                        logger.info(f"Tạo cron Quartz HÀNG THÁNG vào ngày {day_of_month} lúc {time_str}: {cron}")
                        return cron
                
                # 4. Fallback: mặc định không cron nếu không xác định được
                logger.warning(f"Không thể xác định lịch lặp lại cụ thể. Cron sẽ rỗng.")
                return ""
                
            else:  # ONCE - Một lần
                if not date_str:
                    logger.warning("Không có ngày cho sự kiện một lần. Cron sẽ rỗng.")
                    return ""
                    
                try:
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    cron = f"0 {minute} {hour} {date_obj.day} {date_obj.month} ? {date_obj.year}"
                    logger.info(f"Tạo cron Quartz MỘT LẦN cho {date_str} {time_str}: {cron}")
                    return cron
                except ValueError as e:
                    logger.error(f"Lỗi định dạng ngày '{date_str}' khi tạo cron một lần: {e}")
                    return ""
        
        except Exception as e:
            logger.error(f"Lỗi khi tạo biểu thức cron: {e}")
            return ""
    
    @classmethod
    def extract_time_from_date_description(cls, date_description: str) -> Tuple[str, str]:
        """
        Trích xuất thời gian (nếu có) từ mô tả ngày và trả về mô tả ngày đã làm sạch.
        
        Args:
            date_description: Mô tả ngày có thể chứa thời gian
            
        Returns:
            Tuple (cleaned_date_description, time_str)
            - time_str sẽ là None nếu không tìm thấy thời gian
        """
        cleaned_description = date_description.lower().strip()
        time_str = None
        
        # 1. Tìm định dạng giờ:phút
        time_pattern = r'(\d{1,2})[:\.](\d{2})(\s*(?:am|pm|sáng|chiều|tối|đêm))?'
        time_match = re.search(time_pattern, cleaned_description)
        if time_match:
            hour = int(time_match.group(1))
            minute = time_match.group(2)
            ampm = time_match.group(3)
            
            # Xử lý AM/PM
            if ampm:
                ampm = ampm.strip().lower()
                if any(pm in ampm for pm in ['pm', 'chiều', 'tối', 'đêm']) and hour < 12:
                    hour += 12
                elif any(am in ampm for pm in ['am', 'sáng']) and hour == 12:
                    hour = 0
            
            time_str = f"{hour:02d}:{minute}"
            # Loại bỏ phần thời gian từ mô tả
            cleaned_description = cleaned_description.replace(time_match.group(0), "").strip()
            logger.info(f"Đã trích xuất thời gian '{time_str}' từ mô tả '{date_description}'")
            
        # 2. Tìm thời điểm trong ngày (sáng, trưa, chiều, tối, đêm)
        else:
            for time_of_day, time_info in cls.VIETNAMESE_TIME_OF_DAY.items():
                if time_of_day in cleaned_description:
                    hour = time_info['default_hour']
                    time_str = f"{hour:02d}:00"
                    logger.info(f"Đã ánh xạ '{time_of_day}' thành thời gian '{time_str}'")
                    break
        
        return cleaned_description, time_str
    
    @classmethod
    def parse_and_process_event_date(cls, date_description: str, time_str: str = "19:00", 
                                    description: str = "", title: str = "") -> Tuple[str, str, str]:
        """
        Phân tích mô tả ngày và tạo ngày chuẩn cùng biểu thức cron.
        
        Args:
            date_description: Mô tả ngày (vd: "ngày mai", "thứ 6 tuần sau")
            time_str: Thời gian định dạng HH:MM
            description: Mô tả sự kiện
            title: Tiêu đề sự kiện
            
        Returns:
            Tuple (date_str, repeat_type, cron_expression)
        """
        # Trích xuất thời gian từ mô tả ngày nếu có
        cleaned_date_description, extracted_time = cls.extract_time_from_date_description(date_description)
        
        # Ưu tiên thời gian đã trích xuất
        if extracted_time:
            time_str = extracted_time
        
        # Phân tích ngày
        date_obj = cls.parse_date(cleaned_date_description)
        date_str = cls.format_date(date_obj) if date_obj else None
        
        if not date_str:
            logger.warning(f"Không thể xác định ngày từ mô tả: '{date_description}'")
            return None, "ONCE", ""
            
        repeat_type = cls.determine_repeat_type(description, title)
        cron_expression = cls.generate_cron_expression(date_str, time_str, repeat_type, description, title)
        
        logger.info(f"Kết quả xử lý ngày giờ - Mô tả: '{date_description}', Kết quả: {date_str} {time_str}, Lặp lại: {repeat_type}")
        return date_str, repeat_type, cron_expression

# ------- Weather -----------

class WeatherService:
    """Dịch vụ lấy dữ liệu thời tiết từ OpenWeatherMap API."""
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.openweathermap.org/data/2.5"
        
    async def get_current_weather(self, lat=None, lon=None, location=None, lang="vi"):
        """
        Lấy thông tin thời tiết hiện tại. Ưu tiên sử dụng tọa độ (lat/lon) nếu có,
        nếu không thì dùng location để tìm kiếm.
        """
        try:
            if lat is not None and lon is not None:
                # Sử dụng tọa độ
                url = f"{self.base_url}/weather"
                params = {
                    "lat": lat,
                    "lon": lon,
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang
                }
            elif location:
                # Sử dụng tên địa điểm
                url = f"{self.base_url}/weather"
                params = {
                    "q": location,
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang
                }
            else:
                # Mặc định là Hà Nội
                url = f"{self.base_url}/weather"
                params = {
                    "q": "Hanoi,vn",
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang
                }
                
            response = await asyncio.to_thread(
                requests.get, url, params=params, timeout=10
            )
            
            if response.status_code != 200:
                logger.error(f"OpenWeatherMap API error: {response.status_code} - {response.text}")
                return None
                
            data = response.json()
            return self._process_current_weather(data)
            
        except Exception as e:
            logger.error(f"Error fetching weather data: {e}", exc_info=True)
            return None
            
    async def get_forecast(self, lat=None, lon=None, location=None, lang="vi", days=5):
        """
        Lấy dự báo thời tiết cho nhiều ngày. Ưu tiên sử dụng tọa độ (lat/lon) nếu có,
        nếu không thì dùng location để tìm kiếm.
        """
        try:
            if lat is not None and lon is not None:
                # Sử dụng tọa độ
                url = f"{self.base_url}/forecast"
                params = {
                    "lat": lat,
                    "lon": lon,
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang,
                    "cnt": days * 8  # API trả về dữ liệu 3 giờ một lần, 8 lần/ngày
                }
            elif location:
                # Sử dụng tên địa điểm
                url = f"{self.base_url}/forecast"
                params = {
                    "q": location,
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang,
                    "cnt": days * 8
                }
            else:
                # Mặc định là Hà Nội
                url = f"{self.base_url}/forecast"
                params = {
                    "q": "Hanoi,vn",
                    "appid": self.api_key,
                    "units": "metric",
                    "lang": lang,
                    "cnt": days * 8
                }
                
            response = await asyncio.to_thread(
                requests.get, url, params=params, timeout=10
            )
            
            if response.status_code != 200:
                logger.error(f"OpenWeatherMap API error: {response.status_code} - {response.text}")
                return None
                
            data = response.json()
            return self._process_forecast(data)
            
        except Exception as e:
            logger.error(f"Error fetching weather forecast: {e}", exc_info=True)
            return None
    
    def _process_current_weather(self, data):
        """Xử lý dữ liệu thời tiết hiện tại thành định dạng dễ sử dụng."""
        try:
            result = {
                "location": {
                    "name": data.get("name", ""),
                    "country": data.get("sys", {}).get("country", ""),
                    "lat": data.get("coord", {}).get("lat"),
                    "lon": data.get("coord", {}).get("lon"),
                },
                "current": {
                    "dt": data.get("dt"),
                    "date": datetime.datetime.fromtimestamp(data.get("dt", 0)),
                    "temp": data.get("main", {}).get("temp"),
                    "feels_like": data.get("main", {}).get("feels_like"),
                    "temp_min": data.get("main", {}).get("temp_min"),
                    "temp_max": data.get("main", {}).get("temp_max"),
                    "humidity": data.get("main", {}).get("humidity"),
                    "pressure": data.get("main", {}).get("pressure"),
                    "weather": {
                        "id": data.get("weather", [{}])[0].get("id"),
                        "main": data.get("weather", [{}])[0].get("main"),
                        "description": data.get("weather", [{}])[0].get("description"),
                        "icon": data.get("weather", [{}])[0].get("icon"),
                    },
                    "wind": {
                        "speed": data.get("wind", {}).get("speed"),
                        "deg": data.get("wind", {}).get("deg"),
                    },
                    "clouds": data.get("clouds", {}).get("all"),
                    "visibility": data.get("visibility"),
                    "sunrise": datetime.datetime.fromtimestamp(data.get("sys", {}).get("sunrise", 0)),
                    "sunset": datetime.datetime.fromtimestamp(data.get("sys", {}).get("sunset", 0)),
                }
            }
            
            # Thêm đường dẫn đến icon
            if result["current"]["weather"]["icon"]:
                result["current"]["weather"]["icon_url"] = f"https://openweathermap.org/img/wn/{result['current']['weather']['icon']}@2x.png"
            
            return result
        except Exception as e:
            logger.error(f"Error processing weather data: {e}", exc_info=True)
            return None
    
    def _process_forecast(self, data):
        """Xử lý dữ liệu dự báo thời tiết thành định dạng dễ sử dụng."""
        try:
            result = {
                "location": {
                    "name": data.get("city", {}).get("name", ""),
                    "country": data.get("city", {}).get("country", ""),
                    "lat": data.get("city", {}).get("coord", {}).get("lat"),
                    "lon": data.get("city", {}).get("coord", {}).get("lon"),
                },
                "forecast": []
            }
            
            # Nhóm dự báo theo ngày
            forecasts_by_day = {}
            
            for item in data.get("list", []):
                dt = datetime.datetime.fromtimestamp(item.get("dt", 0))
                day_key = dt.strftime("%Y-%m-%d")
                
                if day_key not in forecasts_by_day:
                    forecasts_by_day[day_key] = []
                
                forecast_item = {
                    "dt": item.get("dt"),
                    "date": dt,
                    "time": dt.strftime("%H:%M"),
                    "temp": item.get("main", {}).get("temp"),
                    "feels_like": item.get("main", {}).get("feels_like"),
                    "temp_min": item.get("main", {}).get("temp_min"),
                    "temp_max": item.get("main", {}).get("temp_max"),
                    "humidity": item.get("main", {}).get("humidity"),
                    "weather": {
                        "id": item.get("weather", [{}])[0].get("id"),
                        "main": item.get("weather", [{}])[0].get("main"),
                        "description": item.get("weather", [{}])[0].get("description"),
                        "icon": item.get("weather", [{}])[0].get("icon"),
                    },
                    "wind": {
                        "speed": item.get("wind", {}).get("speed"),
                        "deg": item.get("wind", {}).get("deg"),
                    },
                    "clouds": item.get("clouds", {}).get("all"),
                    "pop": item.get("pop", 0) * 100,  # Chuyển xác suất mưa từ 0-1 thành phần trăm
                }
                
                # Thêm đường dẫn đến icon
                if forecast_item["weather"]["icon"]:
                    forecast_item["weather"]["icon_url"] = f"https://openweathermap.org/img/wn/{forecast_item['weather']['icon']}@2x.png"
                
                forecasts_by_day[day_key].append(forecast_item)
            
            # Tổng hợp dữ liệu theo ngày
            for day_key, items in forecasts_by_day.items():
                day_summary = {
                    "date": day_key,
                    "day_of_week": datetime.datetime.strptime(day_key, "%Y-%m-%d").strftime("%A"),
                    "temp_min": min(item["temp_min"] for item in items if "temp_min" in item),
                    "temp_max": max(item["temp_max"] for item in items if "temp_max" in item),
                    "hourly": sorted(items, key=lambda x: x["date"]),
                    "main_weather": self._get_main_weather_for_day(items),
                }
                result["forecast"].append(day_summary)
            
            # Sắp xếp dự báo theo ngày
            result["forecast"] = sorted(result["forecast"], key=lambda x: x["date"])
            
            return result
        except Exception as e:
            logger.error(f"Error processing forecast data: {e}", exc_info=True)
            return None
    
    def _get_main_weather_for_day(self, hourly_items):
        """Xác định thời tiết chính trong ngày dựa trên các dự báo theo giờ."""
        # Ưu tiên thời tiết buổi sáng và chiều (9h-18h)
        daytime_items = [item for item in hourly_items 
                        if 9 <= item["date"].hour < 18]
        
        if not daytime_items:
            daytime_items = hourly_items
            
        # Đếm tần suất xuất hiện của mỗi loại thời tiết
        weather_counts = {}
        for item in daytime_items:
            weather_id = item["weather"]["id"]
            weather_counts[weather_id] = weather_counts.get(weather_id, 0) + 1
            
        # Lấy loại thời tiết xuất hiện nhiều nhất
        if weather_counts:
            most_common_weather_id = max(weather_counts.items(), key=lambda x: x[1])[0]
            
            # Tìm item có weather id này
            for item in daytime_items:
                if item["weather"]["id"] == most_common_weather_id:
                    return item["weather"]
        
        # Nếu không tìm được, trả về thời tiết của item đầu tiên
        if daytime_items:
            return daytime_items[0]["weather"]
            
        return {
            "id": 800,
            "main": "Clear",
            "description": "trời quang đãng",
            "icon": "01d",
            "icon_url": "https://openweathermap.org/img/wn/01d@2x.png"
        }


def format_weather_for_prompt(current_weather, forecast=None):
    """Định dạng thông tin thời tiết để đưa vào prompt."""
    if not current_weather:
        return "Không có thông tin thời tiết."
        
    location_name = current_weather.get("location", {}).get("name", "")
    country = current_weather.get("location", {}).get("country", "")
    location_str = f"{location_name}, {country}" if country else location_name
    
    current = current_weather.get("current", {})
    temp = current.get("temp")
    feels_like = current.get("feels_like")
    humidity = current.get("humidity")
    weather_desc = current.get("weather", {}).get("description", "")
    wind_speed = current.get("wind", {}).get("speed")
    
    # Format ngày giờ cập nhật
    update_time = current.get("date")
    update_time_str = update_time.strftime("%H:%M, %d/%m/%Y") if update_time else "N/A"
    
    # Thông tin mặt trời mọc/lặn
    sunrise = current.get("sunrise")
    sunset = current.get("sunset")
    sunrise_str = sunrise.strftime("%H:%M") if sunrise else "N/A"
    sunset_str = sunset.strftime("%H:%M") if sunset else "N/A"
    
    result = f"""
Thông tin thời tiết cho {location_str}:
- Thời điểm cập nhật: {update_time_str}
- Nhiệt độ hiện tại: {temp}°C (cảm giác như: {feels_like}°C)
- Thời tiết: {weather_desc}
- Độ ẩm: {humidity}%
- Gió: {wind_speed} m/s
- Mặt trời mọc: {sunrise_str}, mặt trời lặn: {sunset_str}
"""

    # Thêm thông tin dự báo nếu có
    if forecast and "forecast" in forecast:
        result += "\nDự báo thời tiết:"
        for i, day in enumerate(forecast["forecast"][:3]):  # Chỉ lấy 3 ngày
            date_obj = datetime.datetime.strptime(day["date"], "%Y-%m-%d")
            date_str = date_obj.strftime("%d/%m/%Y")
            day_of_week = day["day_of_week"]
            
            if i == 0:
                day_label = "Hôm nay"
            elif i == 1:
                day_label = "Ngày mai"
            else:
                day_label = f"{day_of_week}, {date_str}"
                
            weather_desc = day["main_weather"]["description"]
            temp_min = day["temp_min"]
            temp_max = day["temp_max"]
            
            result += f"\n- {day_label}: {weather_desc}, {temp_min}°C đến {temp_max}°C"
    
    return result


class WeatherQueryParser:
    """
    Phân tích truy vấn thời tiết để trích xuất địa điểm và thời gian.
    Sử dụng DateTimeHandler để tính toán ngày cụ thể từ mô tả tương đối.
    """
    
    @classmethod
    async def parse_weather_query(cls, query: str, openai_api_key: str) -> Tuple[bool, str, Optional[str]]:
        """
        Phân tích truy vấn thời tiết để xác định:
        - Có phải truy vấn thời tiết không
        - Địa điểm nào
        - Thời gian nào (nếu có)
        
        Args:
            query: Chuỗi truy vấn của người dùng 
            openai_api_key: API key của OpenAI
            
        Returns:
            Tuple (is_weather_query, location, date_description)
        """
        if not query or not openai_api_key:
            return False, None, None
            
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_api_key)
            
            system_prompt = """
Bạn là một hệ thống phân loại truy vấn thời tiết thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có phải là về thời tiết hoặc liên quan đến thời tiết không (`is_weather_query`).
2. Nếu là truy vấn thời tiết, xác định địa điểm đề cập trong câu hỏi (`location`).
   - Trích xuất TÊN ĐỊA ĐIỂM CHÍNH XÁC từ câu hỏi, ví dụ: "Hà Nội", "Đà Nẵng", "Sài Gòn", "Ho Chi Minh City"
   - Chỉ trả về "Hanoi" khi câu hỏi KHÔNG đề cập đến bất kỳ địa điểm nào cụ thể.
   - Đối với "Sài Gòn", hãy trả về "Ho Chi Minh City" để API tìm kiếm chính xác.
3. Nếu là truy vấn thời tiết, phân tích xem câu hỏi có đề cập đến thời gian cụ thể không (`date_description`).
   - Trích xuất MÔ TẢ THỜI GIAN NGUYÊN BẢN từ câu hỏi, ví dụ: "ngày mai", "thứ 2 tuần sau", "cuối tuần"
   - Trả về null nếu không có mô tả thời gian nào được đề cập (tức là hỏi về thời tiết hiện tại)
   - QUAN TRỌNG: Đừng cố diễn giải hay chuyển đổi mô tả thời gian, chỉ trích xuất đúng nguyên từ mô tả.

Ví dụ:
- User: "thời tiết ở Đà Nẵng hôm nay" -> { "is_weather_query": true, "location": "Da Nang", "date_description": "hôm nay" }
- User: "thời tiết Hà Nội thứ 2 tuần sau" -> { "is_weather_query": true, "location": "Hanoi", "date_description": "thứ 2 tuần sau" }
- User: "trời có mưa không" -> { "is_weather_query": true, "location": "Hanoi", "date_description": null }
- User: "dự báo thời tiết cuối tuần Sài Gòn" -> { "is_weather_query": true, "location": "Ho Chi Minh City", "date_description": "cuối tuần" }
- User: "kết quả trận MU tối qua" -> { "is_weather_query": false, "location": null, "date_description": null }

Trả lời DƯỚI DẠNG JSON HỢP LỆ với 3 trường: is_weather_query (boolean), location (string hoặc null), date_description (string hoặc null).
"""
            response = await asyncio.to_thread(
                 client.chat.completions.create,
                 model="gpt-4o-mini",
                 messages=[
                     {"role": "system", "content": system_prompt},
                     {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\""}
                 ],
                 temperature=0.1,
                 max_tokens=150,
                 response_format={"type": "json_object"}
            )

            result_str = response.choices[0].message.content
            logger.info(f"Kết quả parse_weather_query (raw): {result_str}")

            try:
                result = json.loads(result_str)
                is_weather_query = result.get("is_weather_query", False)
                location = result.get("location")
                date_description = result.get("date_description")
                
                if is_weather_query and not location:
                    location = "Hanoi"  # Mặc định là Hà Nội
                    
                logger.info(f"Phân tích truy vấn '{query}': is_weather_query={is_weather_query}, location='{location}', date_description='{date_description}'")
                return is_weather_query, location, date_description

            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Lỗi giải mã JSON từ parse_weather_query: {e}. Raw: {result_str}")
                return False, None, None
                
        except Exception as e:
            logger.error(f"Lỗi khi gọi OpenAI trong parse_weather_query: {e}", exc_info=True)
            return False, None, None

    @classmethod
    async def get_forecast_for_specific_date(cls, weather_service, location: str, date_description: str, 
                                           lat: Optional[float] = None, lon: Optional[float] = None,
                                           days: int = 7, lang: str = "vi"):
        """
        Lấy dự báo thời tiết cho ngày cụ thể dựa trên mô tả ngày.
        
        Args:
            weather_service: Đối tượng WeatherService đã khởi tạo
            location: Tên địa điểm 
            date_description: Mô tả ngày (vd: "thứ 2 tuần sau")
            lat, lon: Tọa độ (optional)
            days: Số ngày dự báo tối đa
            lang: Ngôn ngữ
            
        Returns:
            Tuple (current_weather, forecast, target_date, date_text)
        """
        # Sử dụng DateTimeHandler để phân tích mô tả ngày
        try:
            target_date = DateTimeHandler.parse_date(date_description)
            if not target_date:
                logger.warning(f"Không thể phân tích mô tả ngày '{date_description}', sử dụng ngày hiện tại")
                target_date = datetime.date.today()
                
            # Format lại ngày để hiển thị
            date_text = target_date.strftime("%d/%m/%Y")
            
            # Tính số ngày cần dự báo (từ hôm nay đến target_date)
            today = datetime.date.today()
            days_diff = (target_date - today).days
            
            # Đảm bảo dự báo đủ số ngày
            forecast_days = max(days_diff + 1, 1)
            if forecast_days > 7:  # OpenWeatherMap giới hạn 7 ngày
                logger.warning(f"Ngày yêu cầu quá xa trong tương lai ({days_diff} ngày), dự báo có thể không chính xác")
                forecast_days = 7
                
            logger.info(f"Lấy dự báo thời tiết cho '{location}', mục tiêu ngày: {date_text} (sau {days_diff} ngày)")
                
            # Lấy dữ liệu thời tiết hiện tại
            if lat is not None and lon is not None:
                current_weather = await weather_service.get_current_weather(lat=lat, lon=lon, lang=lang)
                forecast = await weather_service.get_forecast(lat=lat, lon=lon, lang=lang, days=forecast_days)
            else:
                current_weather = await weather_service.get_current_weather(location=location, lang=lang)
                forecast = await weather_service.get_forecast(location=location, lang=lang, days=forecast_days)
                
            return current_weather, forecast, target_date, date_text
                
        except Exception as e:
            logger.error(f"Lỗi khi lấy dự báo cho ngày cụ thể: {e}", exc_info=True)
            return None, None, None, None
    
    @classmethod
    def format_weather_for_date(cls, current_weather, forecast, target_date, date_text=None):
        """
        Định dạng thông tin thời tiết cho ngày cụ thể để đưa vào prompt.
        
        Args:
            current_weather: Dữ liệu thời tiết hiện tại
            forecast: Dữ liệu dự báo 
            target_date: Ngày mục tiêu (datetime.date)
            date_text: Chuỗi biểu diễn ngày (optional)
            
        Returns:
            Văn bản định dạng thông tin thời tiết
        """
        if not current_weather or not forecast:
            return "Không có thông tin thời tiết."
            
        location_name = current_weather.get("location", {}).get("name", "")
        country = current_weather.get("location", {}).get("country", "")
        location_str = f"{location_name}, {country}" if country else location_name
        
        if not date_text and target_date:
            date_text = target_date.strftime("%d/%m/%Y")
        elif not date_text:
            date_text = "ngày được yêu cầu"
            
        target_date_str = target_date.strftime("%Y-%m-%d") if target_date else None
        
        # Nếu ngày mục tiêu là hôm nay, sử dụng thông tin hiện tại
        today = datetime.date.today()
        if target_date == today:
            current = current_weather.get("current", {})
            temp = current.get("temp")
            feels_like = current.get("feels_like")
            humidity = current.get("humidity")
            weather_desc = current.get("weather", {}).get("description", "")
            wind_speed = current.get("wind", {}).get("speed")
            
            sunrise = current.get("sunrise")
            sunset = current.get("sunset")
            sunrise_str = sunrise.strftime("%H:%M") if sunrise else "N/A"
            sunset_str = sunset.strftime("%H:%M") if sunset else "N/A"
            
            result = f"""
Thông tin thời tiết cho {location_str} vào {date_text} (hôm nay):
- Nhiệt độ hiện tại: {temp}°C (cảm giác như: {feels_like}°C)
- Thời tiết: {weather_desc}
- Độ ẩm: {humidity}%
- Gió: {wind_speed} m/s
- Mặt trời mọc: {sunrise_str}, mặt trời lặn: {sunset_str}
"""
        else:
            # Tìm dự báo cho ngày mục tiêu
            target_forecast = None
            for day_forecast in forecast.get("forecast", []):
                if day_forecast.get("date") == target_date_str:
                    target_forecast = day_forecast
                    break
                    
            if target_forecast:
                temp_min = target_forecast.get("temp_min", "N/A")
                temp_max = target_forecast.get("temp_max", "N/A")
                weather_desc = target_forecast.get("main_weather", {}).get("description", "không rõ")
                
                day_of_week = target_date.strftime("%A")
                # Chuyển đổi sang tiếng Việt
                day_of_week_vi = {
                    "Monday": "Thứ Hai",
                    "Tuesday": "Thứ Ba",
                    "Wednesday": "Thứ Tư",
                    "Thursday": "Thứ Năm",
                    "Friday": "Thứ Sáu",
                    "Saturday": "Thứ Bảy",
                    "Sunday": "Chủ Nhật"
                }.get(day_of_week, day_of_week)
                
                result = f"""
Dự báo thời tiết cho {location_str} vào {date_text} ({day_of_week_vi}):
- Nhiệt độ: {temp_min}°C đến {temp_max}°C
- Thời tiết: {weather_desc}
"""
                
                # Thêm thông tin chi tiết theo giờ nếu có
                hourly_data = target_forecast.get("hourly", [])
                if hourly_data:
                    result += "\nDự báo chi tiết theo giờ:\n"
                    
                    # Chỉ hiển thị 4 khung giờ quan trọng
                    key_hours = [8, 12, 16, 20]  # sáng, trưa, chiều, tối
                    key_forecasts = []
                    
                    for hour in key_hours:
                        closest_forecast = min(hourly_data, 
                                             key=lambda x: abs(x.get("date").hour - hour) 
                                             if isinstance(x.get("date"), datetime.datetime) else float('inf'))
                        
                        if isinstance(closest_forecast.get("date"), datetime.datetime):
                            time_str = closest_forecast.get("date").strftime("%H:%M")
                            temp = closest_forecast.get("temp", "N/A")
                            desc = closest_forecast.get("weather", {}).get("description", "không rõ")
                            
                            time_of_day = ""
                            if 6 <= closest_forecast.get("date").hour < 12:
                                time_of_day = "Sáng"
                            elif 12 <= closest_forecast.get("date").hour < 14:
                                time_of_day = "Trưa"
                            elif 14 <= closest_forecast.get("date").hour < 18:
                                time_of_day = "Chiều"
                            else:
                                time_of_day = "Tối"
                                
                            key_forecasts.append(f"- {time_of_day} ({time_str}): {temp}°C, {desc}")
                    
                    result += "\n".join(key_forecasts)
            else:
                result = f"""
Dự báo thời tiết cho {location_str} vào {date_text}:
- Không có dữ liệu dự báo chi tiết cho ngày này (có thể ngày này quá xa trong tương lai).
"""
                
                # Thêm dự báo 3 ngày gần nhất
                result += "\nDự báo 3 ngày tới:\n"
                for i, day in enumerate(forecast.get("forecast", [])[:3]):
                    date_obj = datetime.datetime.strptime(day.get("date", ""), "%Y-%m-%d") if day.get("date") else None
                    if date_obj:
                        date_str = date_obj.strftime("%d/%m/%Y")
                        day_name = date_obj.strftime("%A")
                        day_name_vi = {
                            "Monday": "Thứ Hai",
                            "Tuesday": "Thứ Ba",
                            "Wednesday": "Thứ Tư",
                            "Thursday": "Thứ Năm",
                            "Friday": "Thứ Sáu",
                            "Saturday": "Thứ Bảy",
                            "Sunday": "Chủ Nhật"
                        }.get(day_name, day_name)
                        
                        weather_desc = day.get("main_weather", {}).get("description", "không rõ")
                        temp_min = day.get("temp_min", "N/A")
                        temp_max = day.get("temp_max", "N/A")
                        
                        result += f"- {day_name_vi}, {date_str}: {weather_desc}, {temp_min}°C đến {temp_max}°C\n"
    
        return result

class WeatherAdvisor:
    """
    Lớp cung cấp tư vấn thông minh dựa trên dữ liệu thời tiết.
    Bao gồm tư vấn trang phục, đồ mang theo, địa điểm đi chơi và các gợi ý khác.
    """
    
    # Mức nhiệt độ và phân loại
    TEMP_RANGES = {
        "rất_lạnh": (float('-inf'), 10),  # Dưới 10°C
        "lạnh": (10, 18),                  # 10-18°C
        "mát_mẻ": (18, 22),                # 18-22°C
        "dễ_chịu": (22, 27),               # 22-27°C
        "ấm": (27, 32),                    # 27-32°C
        "nóng": (32, 36),                  # 32-36°C
        "rất_nóng": (36, float('inf'))     # Trên 36°C
    }
    
    # Phân loại thời tiết theo điều kiện
    WEATHER_CONDITIONS = {
        # Mưa
        "mưa_nhẹ": ["mưa nhẹ", "mưa phùn", "mưa rào nhẹ"],
        "mưa_vừa": ["mưa vừa", "mưa rào"],
        "mưa_to": ["mưa to", "mưa lớn", "mưa rào mạnh", "dông", "giông", "mưa rào và dông", "mưa rào và giông"],
        
        # Nắng
        "nắng_nhẹ": ["nắng nhẹ", "trời quang", "quang đãng", "nắng ít"],
        "nắng": ["nắng", "trời nắng", "nắng vừa"],
        "nắng_gắt": ["nắng gắt", "nắng nóng", "nắng gay gắt", "nắng to"],
        
        # Mây
        "có_mây": ["có mây", "trời nhiều mây", "mây thưa", "mây rải rác"],
        "u_ám": ["u ám", "trời âm u", "âm u", "mây đen", "mây đặc"],
        
        # Sương mù
        "sương_mù": ["sương mù", "trời sương mù", "sương", "mù sương"],
        
        # Khác
        "ẩm_ướt": ["ẩm ướt", "độ ẩm cao", "ẩm thấp"],
        "hanh_khô": ["hanh khô", "khô", "khô hanh", "hanh", "khô ráo"],
        "gió_mạnh": ["gió mạnh", "gió lớn", "gió to", "gió cấp", "gió giật"],
    }
    
    # Tư vấn trang phục theo nhiệt độ và điều kiện thời tiết
    CLOTHING_ADVICE = {
        "rất_lạnh": {
            "default": [
                "Áo khoác dày hoặc áo phao",
                "Khăn quàng cổ, găng tay và mũ len",
                "Quần dài dày hoặc quần nỉ",
                "Tất dày và giày bốt hoặc giày kín",
                "Có thể mặc nhiều lớp áo bên trong"
            ],
            "mưa_nhẹ": [
                "Áo khoác dày chống thấm nước",
                "Ủng cao su hoặc giày không thấm nước",
                "Mũ chống mưa hoặc mũ có phủ chống thấm"
            ],
            "mưa_vừa": [
                "Áo mưa hoặc áo khoác chống thấm dày",
                "Ủng cao su cao cổ",
                "Tránh đồ len có thể thấm nước nhiều"
            ],
            "mưa_to": [
                "Áo mưa kín hoặc áo khoác chống thấm nước tốt",
                "Mũ có vành rộng chống nước",
                "Giày và tất dự phòng để thay"
            ]
        },
        "lạnh": {
            "default": [
                "Áo khoác nhẹ hoặc áo len dày",
                "Khăn quàng cổ mỏng",
                "Quần dài",
                "Giày kín"
            ],
            "mưa_nhẹ": [
                "Áo khoác nhẹ chống nước",
                "Mũ chống mưa",
                "Giày kín không thấm nước"
            ],
            "mưa_vừa": [
                "Áo khoác chống thấm",
                "Ủng hoặc giày không thấm nước",
                "Mũ chống mưa"
            ]
        },
        "mát_mẻ": {
            "default": [
                "Áo sơ mi dài tay hoặc áo thun dài tay",
                "Áo khoác mỏng có thể mặc khi trời mát",
                "Quần dài vải nhẹ",
                "Giày thể thao hoặc giày lười"
            ],
            "nắng": [
                "Mũ nhẹ để che nắng",
                "Kính râm"
            ],
            "có_mây": [
                "Áo khoác mỏng có thể mặc/cởi linh hoạt"
            ]
        },
        "dễ_chịu": {
            "default": [
                "Áo thun hoặc áo sơ mi ngắn tay",
                "Quần dài vải mỏng hoặc quần lửng",
                "Giày mở thoáng hoặc sandal"
            ],
            "nắng": [
                "Mũ rộng vành để che nắng",
                "Kính râm",
                "Khẩu trang chống nắng (nếu ra ngoài lâu)"
            ],
            "mưa_nhẹ": [
                "Áo khoác mỏng chống thấm",
                "Giày không thấm nước"
            ]
        },
        "ấm": {
            "default": [
                "Áo thun thoáng mát",
                "Váy hoặc quần short (nếu đi chơi)",
                "Quần vải nhẹ thoáng khí",
                "Sandal hoặc dép"
            ],
            "nắng": [
                "Mũ rộng vành",
                "Kính râm",
                "Khẩu trang chống nắng",
                "Áo chống nắng nhẹ (nếu ra ngoài lâu)"
            ],
            "mưa_nhẹ": [
                "Áo mưa mỏng có thể gấp gọn mang theo"
            ]
        },
        "nóng": {
            "default": [
                "Áo thun mỏng nhẹ, thoáng khí",
                "Quần short hoặc váy ngắn thoáng mát",
                "Dép hoặc sandal thoáng",
                "Tránh quần áo tối màu hấp thụ nhiệt"
            ],
            "nắng": [
                "Áo chống nắng nhẹ, thoáng khí",
                "Mũ rộng vành",
                "Kính râm chống tia UV",
                "Khẩu trang chống nắng"
            ]
        },
        "rất_nóng": {
            "default": [
                "Áo thun siêu nhẹ, áo ba lỗ hoặc áo sát nách",
                "Quần đùi hoặc váy siêu ngắn, thoáng mát nhất có thể",
                "Dép lê hoặc sandal mỏng",
                "Mặc đồ sáng màu phản chiếu nhiệt"
            ],
            "nắng_gắt": [
                "Áo chống nắng UPF 50+ nếu phải ra ngoài",
                "Mũ rộng vành kín",
                "Kính râm chống UV cao",
                "Khẩu trang chống nắng"
            ]
        }
    }
    
    # Tư vấn đồ mang theo dựa vào điều kiện thời tiết
    ITEMS_TO_BRING = {
        "mưa_nhẹ": [
            "Ô nhỏ gấp gọn",
            "Áo mưa mỏng hoặc áo khoác chống thấm"
        ],
        "mưa_vừa": [
            "Ô chắc chắn",
            "Áo mưa",
            "Túi chống nước cho điện thoại và ví"
        ],
        "mưa_to": [
            "Áo mưa kín",
            "Ủng cao su nếu phải đi bộ nhiều",
            "Túi chống nước cho đồ điện tử",
            "Quần áo dự phòng (nếu đi xa)"
        ],
        "nắng": [
            "Kem chống nắng SPF 30+",
            "Kính râm",
            "Mũ",
            "Nước uống"
        ],
        "nắng_gắt": [
            "Kem chống nắng SPF 50+",
            "Kính râm chống UV",
            "Mũ rộng vành",
            "Khăn che cổ",
            "Nhiều nước uống",
            "Dù che nắng",
            "Quạt cầm tay hoặc quạt mini"
        ],
        "rất_lạnh": [
            "Găng tay",
            "Mũ len",
            "Khăn quàng cổ",
            "Miếng dán giữ nhiệt",
            "Đồ uống giữ nhiệt"
        ],
        "hanh_khô": [
            "Xịt khoáng hoặc bình phun sương",
            "Kem dưỡng ẩm",
            "Son dưỡng môi",
            "Nhiều nước uống"
        ],
        "gió_mạnh": [
            "Mũ có dây buộc",
            "Áo khoác chắn gió",
            "Kính bảo vệ mắt khỏi bụi"
        ],
        "sương_mù": [
            "Đèn pin hoặc đèn đeo trán",
            "Khẩu trang",
            "Khăn lau kính"
        ]
    }
    
    # Tư vấn địa điểm đi chơi theo thời tiết
    PLACES_TO_GO = {
        "đẹp_trời": [
            "Công viên",
            "Vườn bách thảo",
            "Hồ",
            "Đi picnic",
            "Các điểm tham quan ngoài trời",
            "Đạp xe quanh hồ",
            "Đồi núi (nếu có)",
            "Bãi biển (nếu có)"
        ],
        "mưa": [
            "Trung tâm thương mại",
            "Bảo tàng",
            "Rạp chiếu phim",
            "Quán cà phê",
            "Nhà sách",
            "Trung tâm giải trí trong nhà",
            "Tiệm trà",
            "Nhà hàng ấm cúng"
        ],
        "nóng": [
            "Bể bơi",
            "Rạp chiếu phim có máy lạnh",
            "Trung tâm thương mại có điều hòa",
            "Công viên nước",
            "Quán cà phê có máy lạnh",
            "Bảo tàng có điều hòa",
            "Thư viện"
        ],
        "lạnh": [
            "Quán cà phê ấm cúng",
            "Nhà hàng lẩu",
            "Trung tâm thương mại có hệ thống sưởi",
            "Phòng trà",
            "Nhà hát",
            "Tiệm bánh"
        ]
    }
    
    # Các hoạt động phù hợp với điều kiện thời tiết
    ACTIVITIES = {
        "đẹp_trời": [
            "Đi bộ dạo phố",
            "Đạp xe",
            "Chạy bộ",
            "Picnic",
            "Chụp ảnh ngoài trời",
            "Vẽ tranh phong cảnh",
            "Trồng cây",
            "Câu cá (nếu có địa điểm thích hợp)"
        ],
        "mưa": [
            "Đọc sách",
            "Xem phim",
            "Nấu ăn tại nhà",
            "Chơi board game với gia đình",
            "Học một kỹ năng mới trực tuyến",
            "Sắp xếp lại tủ đồ",
            "Thử một quán cà phê mới"
        ],
        "nóng": [
            "Bơi lội",
            "Uống đồ lạnh tại một quán cà phê",
            "Thưởng thức kem",
            "Ngâm chân trong nước mát",
            "Xem phim trong rạp có điều hòa"
        ],
        "lạnh": [
            "Thưởng thức đồ uống nóng",
            "Ăn lẩu",
            "Nướng BBQ",
            "Xem phim dưới chăn ấm",
            "Nghe nhạc và đọc sách"
        ]
    }

    @classmethod
    def get_temperature_category(cls, temp: float) -> str:
        """
        Xác định danh mục nhiệt độ dựa trên nhiệt độ đầu vào.
        
        Args:
            temp: Nhiệt độ (độ C)
            
        Returns:
            Danh mục nhiệt độ (ví dụ: "lạnh", "dễ_chịu", "nóng"...)
        """
        for category, (min_temp, max_temp) in cls.TEMP_RANGES.items():
            if min_temp <= temp < max_temp:
                return category
        return "dễ_chịu"  # Mặc định nếu không khớp
    
    @classmethod
    def get_weather_conditions(cls, weather_desc: str) -> List[str]:
        """
        Xác định các điều kiện thời tiết dựa trên mô tả.
        
        Args:
            weather_desc: Mô tả thời tiết (ví dụ: "mưa rào và có gió")
            
        Returns:
            Danh sách các điều kiện thời tiết phù hợp
        """
        weather_desc = weather_desc.lower()
        conditions = []
        
        for condition, keywords in cls.WEATHER_CONDITIONS.items():
            if any(keyword in weather_desc for keyword in keywords):
                conditions.append(condition)
                
        # Phân loại chung hơn dựa trên các điều kiện cụ thể
        if any(cond.startswith("mưa_") for cond in conditions):
            conditions.append("mưa")
            
        if any(cond.startswith("nắng_") for cond in conditions):
            conditions.append("nắng")
            
        if not conditions:
            # Nếu không tìm thấy điều kiện cụ thể, đoán theo một số từ khóa chung
            if "mưa" in weather_desc:
                conditions.append("mưa")
            elif "nắng" in weather_desc:
                conditions.append("nắng")
            elif any(word in weather_desc for word in ["quang", "đẹp", "trong"]):
                conditions.append("đẹp_trời")
                
        return conditions if conditions else ["đẹp_trời"]  # Mặc định là trời đẹp nếu không xác định được
    
    @classmethod
    def get_general_weather_category(cls, temp_category: str, weather_conditions: List[str]) -> str:
        """
        Xác định danh mục thời tiết tổng quát cho tư vấn địa điểm và hoạt động.
        
        Args:
            temp_category: Danh mục nhiệt độ
            weather_conditions: Danh sách các điều kiện thời tiết
            
        Returns:
            Danh mục thời tiết tổng quát
        """
        if "mưa" in weather_conditions or any(cond.startswith("mưa_") for cond in weather_conditions):
            return "mưa"
            
        if temp_category in ["nóng", "rất_nóng"] or "nắng_gắt" in weather_conditions:
            return "nóng"
            
        if temp_category in ["lạnh", "rất_lạnh"]:
            return "lạnh"
            
        return "đẹp_trời"
    
    @classmethod
    def analyze_weather_data(cls, weather_data: Dict[str, Any], target_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        """
        Phân tích dữ liệu thời tiết để chuẩn bị cho tư vấn.
        
        Args:
            weather_data: Dữ liệu thời tiết (current_weather hoặc forecast)
            target_date: Ngày cần phân tích (None = ngày hiện tại)
            
        Returns:
            Dict chứa thông tin phân tích
        """
        analysis = {}
        today = datetime.date.today()
        is_current_weather = False
        
        # Xác định nếu dữ liệu là thời tiết hiện tại
        if weather_data.get("current") is not None:
            is_current_weather = True
            
        # Xử lý thời tiết hiện tại
        if is_current_weather and (target_date is None or target_date == today):
            current = weather_data.get("current", {})
            temp = current.get("temp")
            feels_like = current.get("feels_like")
            humidity = current.get("humidity")
            weather_desc = current.get("weather", {}).get("description", "")
            wind_speed = current.get("wind", {}).get("speed")
            
            if temp is not None:
                analysis["temperature"] = temp
                analysis["temp_category"] = cls.get_temperature_category(temp)
            
            if feels_like is not None:
                analysis["feels_like"] = feels_like
                # Sử dụng feels_like để tư vấn trang phục nếu khác biệt nhiều với nhiệt độ thực
                if abs(feels_like - temp) > 3:
                    # Nếu cảm giác lạnh hơn hoặc nóng hơn đáng kể
                    analysis["feels_temp_category"] = cls.get_temperature_category(feels_like)
                    
            if humidity is not None:
                analysis["humidity"] = humidity
                if humidity > 80:
                    analysis["high_humidity"] = True
                elif humidity < 30:
                    analysis["low_humidity"] = True
                    
            if weather_desc:
                analysis["weather_desc"] = weather_desc
                analysis["weather_conditions"] = cls.get_weather_conditions(weather_desc)
                
            if wind_speed is not None:
                analysis["wind_speed"] = wind_speed
                if wind_speed > 8:  # m/s, khoảng 30 km/h
                    analysis["strong_wind"] = True
                    if "gió_mạnh" not in analysis.get("weather_conditions", []):
                        analysis.setdefault("weather_conditions", []).append("gió_mạnh")
                        
        # Xử lý dự báo
        else:
            # Tìm dự báo cho ngày cụ thể
            target_date_str = target_date.strftime("%Y-%m-%d") if target_date else today.strftime("%Y-%m-%d")
            target_forecast = None
            
            for day_forecast in weather_data.get("forecast", []):
                if day_forecast.get("date") == target_date_str:
                    target_forecast = day_forecast
                    break
                    
            if target_forecast:
                temp_min = target_forecast.get("temp_min")
                temp_max = target_forecast.get("temp_max")
                weather_desc = target_forecast.get("main_weather", {}).get("description", "")
                
                # Tính nhiệt độ trung bình cho tư vấn trang phục
                if temp_min is not None and temp_max is not None:
                    avg_temp = (temp_min + temp_max) / 2
                    analysis["temperature"] = avg_temp
                    analysis["temp_category"] = cls.get_temperature_category(avg_temp)
                    analysis["temp_range"] = (temp_min, temp_max)
                    
                if weather_desc:
                    analysis["weather_desc"] = weather_desc
                    analysis["weather_conditions"] = cls.get_weather_conditions(weather_desc)
                    
                # Xác định thời điểm trong ngày cho dự báo chi tiết nếu có
                hourly_data = target_forecast.get("hourly", [])
                if hourly_data:
                    morning = None
                    afternoon = None
                    evening = None
                    
                    for hourly in hourly_data:
                        hour = hourly.get("date").hour if isinstance(hourly.get("date"), datetime.datetime) else -1
                        
                        if 6 <= hour < 12 and not morning:
                            morning = hourly
                        elif 12 <= hour < 18 and not afternoon:
                            afternoon = hourly
                        elif 18 <= hour < 23 and not evening:
                            evening = hourly
                            
                    analysis["time_of_day"] = {
                        "morning": morning,
                        "afternoon": afternoon,
                        "evening": evening
                    }
        
        # Xác định danh mục thời tiết tổng quát
        if "temp_category" in analysis and "weather_conditions" in analysis:
            analysis["general_category"] = cls.get_general_weather_category(
                analysis["temp_category"], 
                analysis["weather_conditions"]
            )
            
        return analysis
    
    @classmethod
    def get_clothing_advice(cls, weather_analysis: Dict[str, Any]) -> List[str]:
        """
        Đưa ra tư vấn trang phục dựa trên phân tích thời tiết.
        
        Args:
            weather_analysis: Kết quả phân tích thời tiết
            
        Returns:
            Danh sách các tư vấn trang phục
        """
        advice = []
        
        # Lấy danh mục nhiệt độ (ưu tiên feels_like nếu có)
        temp_category = weather_analysis.get("feels_temp_category", weather_analysis.get("temp_category"))
        if not temp_category:
            return ["Không có đủ thông tin về nhiệt độ để tư vấn trang phục."]
            
        # Lấy tư vấn cơ bản dựa trên nhiệt độ
        if temp_category in cls.CLOTHING_ADVICE:
            advice.extend(cls.CLOTHING_ADVICE[temp_category]["default"])
            
        # Tư vấn bổ sung dựa trên điều kiện thời tiết
        weather_conditions = weather_analysis.get("weather_conditions", [])
        for condition in weather_conditions:
            if condition in cls.CLOTHING_ADVICE.get(temp_category, {}):
                advice.extend(cls.CLOTHING_ADVICE[temp_category][condition])
                
        # Điều chỉnh theo thời điểm trong ngày
        time_of_day = weather_analysis.get("time_of_day", {})
        temp_range = weather_analysis.get("temp_range")
        
        if temp_range and max(temp_range) - min(temp_range) > 8:
            advice.append("Nhiệt độ dao động lớn trong ngày, nên mặc nhiều lớp để dễ điều chỉnh.")
            
        # Điều chỉnh theo độ ẩm
        if weather_analysis.get("high_humidity"):
            advice.append("Độ ẩm cao, nên mặc vải thoáng khí như cotton hoặc linen để thoải mái hơn.")
            
        if weather_analysis.get("low_humidity"):
            advice.append("Độ ẩm thấp, nên mặc quần áo thoải mái và mang theo dưỡng ẩm.")
            
        # Loại bỏ trùng lặp
        unique_advice = []
        advice_set = set()
        
        for item in advice:
            normalized_item = re.sub(r'\s+', ' ', item.lower().strip())
            if normalized_item not in advice_set:
                advice_set.add(normalized_item)
                unique_advice.append(item)
                
        return unique_advice
    
    @classmethod
    def get_items_to_bring(cls, weather_analysis: Dict[str, Any]) -> List[str]:
        """
        Đưa ra tư vấn đồ vật nên mang theo dựa trên phân tích thời tiết.
        
        Args:
            weather_analysis: Kết quả phân tích thời tiết
            
        Returns:
            Danh sách các đồ vật nên mang theo
        """
        items = []
        items.append("Điện thoại và sạc dự phòng")  # Luôn cần thiết
        items.append("Ví/bóp đựng giấy tờ và tiền")
        
        # Thêm đồ vật dựa trên điều kiện thời tiết
        weather_conditions = weather_analysis.get("weather_conditions", [])
        for condition in weather_conditions:
            if condition in cls.ITEMS_TO_BRING:
                items.extend(cls.ITEMS_TO_BRING[condition])
                
        temp_category = weather_analysis.get("temp_category")
        
        # Thêm đồ vật dựa trên nhiệt độ
        if temp_category in ["nóng", "rất_nóng"]:
            items.append("Chai nước để giữ đủ nước")
            items.append("Khăn lau mồ hôi")
            
        if temp_category in ["lạnh", "rất_lạnh"]:
            items.append("Đồ uống giữ nhiệt")
            
        # Thời gian trong ngày
        time_of_day = weather_analysis.get("time_of_day", {})
        if time_of_day.get("evening"):
            items.append("Đèn pin nhỏ hoặc đèn điện thoại (nếu về muộn)")
            
        # Loại bỏ trùng lặp
        unique_items = []
        items_set = set()
        
        for item in items:
            normalized_item = re.sub(r'\s+', ' ', item.lower().strip())
            if normalized_item not in items_set:
                items_set.add(normalized_item)
                unique_items.append(item)
                
        return unique_items
    
    @classmethod
    def get_places_to_go(cls, weather_analysis: Dict[str, Any]) -> List[str]:
        """
        Đưa ra tư vấn địa điểm đi chơi dựa trên phân tích thời tiết.
        
        Args:
            weather_analysis: Kết quả phân tích thời tiết
            
        Returns:
            Danh sách các địa điểm đề xuất
        """
        places = []
        
        # Sử dụng danh mục thời tiết tổng quát
        general_category = weather_analysis.get("general_category", "đẹp_trời")
        
        if general_category in cls.PLACES_TO_GO:
            places.extend(cls.PLACES_TO_GO[general_category])
            
        # Điều chỉnh theo thời điểm cụ thể
        temp_category = weather_analysis.get("temp_category")
        if temp_category == "rất_nóng" and "mưa" not in general_category:
            # Ưu tiên những nơi có điều hòa/mát mẻ khi quá nóng
            for place in cls.PLACES_TO_GO.get("nóng", []):
                if place not in places:
                    places.append(place)
                    
        if temp_category == "rất_lạnh" and "mưa" not in general_category:
            # Ưu tiên những nơi ấm khi quá lạnh
            for place in cls.PLACES_TO_GO.get("lạnh", []):
                if place not in places:
                    places.append(place)
                    
        return places
    
    @classmethod
    def get_activities(cls, weather_analysis: Dict[str, Any]) -> List[str]:
        """
        Đưa ra tư vấn hoạt động dựa trên phân tích thời tiết.
        
        Args:
            weather_analysis: Kết quả phân tích thời tiết
            
        Returns:
            Danh sách các hoạt động đề xuất
        """
        activities = []
        
        # Sử dụng danh mục thời tiết tổng quát
        general_category = weather_analysis.get("general_category", "đẹp_trời")
        
        if general_category in cls.ACTIVITIES:
            activities.extend(cls.ACTIVITIES[general_category])
            
        return activities
    
    @classmethod
    def combine_advice(cls, weather_data: Dict[str, Any], target_date: Optional[datetime.date] = None, 
                     query_type: str = "general") -> Dict[str, Any]:
        """
        Kết hợp tất cả lời khuyên dựa trên dữ liệu thời tiết và loại truy vấn.
        
        Args:
            weather_data: Dữ liệu thời tiết
            target_date: Ngày mục tiêu (None = ngày hiện tại)
            query_type: Loại truy vấn ("clothing", "items", "places", "activities", "general")
            
        Returns:
            Dict chứa tất cả lời khuyên theo loại truy vấn
        """
        # Phân tích dữ liệu thời tiết
        weather_analysis = cls.analyze_weather_data(weather_data, target_date)
        
        # Kết quả cuối cùng
        result = {
            "weather_summary": {
                "temperature": weather_analysis.get("temperature"),
                "weather_desc": weather_analysis.get("weather_desc"),
                "temp_category": weather_analysis.get("temp_category")
            }
        }
        
        # Thêm tư vấn theo loại truy vấn
        if query_type in ["clothing", "general"]:
            result["clothing_advice"] = cls.get_clothing_advice(weather_analysis)
            
        if query_type in ["items", "general"]:
            result["items_to_bring"] = cls.get_items_to_bring(weather_analysis)
            
        if query_type in ["places", "general"]:
            result["places_to_go"] = cls.get_places_to_go(weather_analysis)
            
        if query_type in ["activities", "general"]:
            result["activities"] = cls.get_activities(weather_analysis)
            
        return result
    
    @classmethod
    def format_advice_for_prompt(cls, advice_data: Dict[str, Any], query_type: str = "general") -> str:
        """
        Định dạng lời khuyên để đưa vào prompt.
        
        Args:
            advice_data: Dữ liệu lời khuyên từ combine_advice
            query_type: Loại truy vấn
            
        Returns:
            Chuỗi lời khuyên định dạng
        """
        result = []
        
        # Thông tin thời tiết tóm tắt
        weather_summary = advice_data.get("weather_summary", {})
        temp = weather_summary.get("temperature")
        weather_desc = weather_summary.get("weather_desc", "")
        
        # Nếu là truy vấn chung, thêm giới thiệu
        if query_type == "general":
            intro = "Dựa trên dữ liệu thời tiết"
            if temp is not None:
                intro += f" (nhiệt độ {temp}°C, {weather_desc})"
            intro += ", đây là một số gợi ý cho bạn:"
            result.append(intro)
            result.append("")  # Dòng trống
            
        # Tư vấn trang phục
        if "clothing_advice" in advice_data:
            if query_type == "clothing":
                result.append(f"### Gợi ý trang phục phù hợp với thời tiết {temp}°C, {weather_desc}:")
            else:
                result.append("### Trang phục nên mặc:")
                
            for item in advice_data["clothing_advice"]:
                result.append(f"- {item}")
                
            result.append("")  # Dòng trống
            
        # Đồ vật nên mang theo
        if "items_to_bring" in advice_data:
            if query_type == "items":
                result.append(f"### Những thứ nên mang theo trong thời tiết {temp}°C, {weather_desc}:")
            else:
                result.append("### Đồ vật nên mang theo:")
                
            for item in advice_data["items_to_bring"]:
                result.append(f"- {item}")
                
            result.append("")  # Dòng trống
            
        # Địa điểm đề xuất
        if "places_to_go" in advice_data:
            if query_type == "places":
                result.append(f"### Địa điểm đề xuất trong thời tiết {temp}°C, {weather_desc}:")
            else:
                result.append("### Địa điểm phù hợp để đi chơi:")
                
            for place in advice_data["places_to_go"][:5]:  # Chỉ hiển thị tối đa 5 địa điểm
                result.append(f"- {place}")
                
            result.append("")  # Dòng trống
            
        # Hoạt động đề xuất
        if "activities" in advice_data:
            if query_type == "activities":
                result.append(f"### Hoạt động phù hợp trong thời tiết {temp}°C, {weather_desc}:")
            else:
                result.append("### Hoạt động đề xuất:")
                
            for activity in advice_data["activities"][:5]:  # Chỉ hiển thị tối đa 5 hoạt động
                result.append(f"- {activity}")
                
        return "\n".join(result)
    
    @classmethod
    async def detect_weather_advice_need(cls, query: str, openai_api_key: str) -> Tuple[bool, str, Optional[str]]:
        """
        Phát hiện nhu cầu tư vấn liên quan đến thời tiết từ câu hỏi.
        
        Args:
            query: Câu hỏi của người dùng
            openai_api_key: API key của OpenAI
            
        Returns:
            Tuple (is_advice_query, advice_type, location, date_description)
        """
        if not query or not openai_api_key:
            return False, "general", None, None
            
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_api_key)
            
            system_prompt = """
Bạn là một hệ thống phân loại truy vấn tư vấn thời tiết thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có phải là yêu cầu tư vấn liên quan đến thời tiết không (`is_advice_query`).
2. Nếu là yêu cầu tư vấn, xác định loại tư vấn (`advice_type`):
   - "clothing": Tư vấn về trang phục nên mặc (ví dụ: "nên mặc gì", "mặc quần áo gì")
   - "items": Tư vấn về đồ vật nên mang theo (ví dụ: "mang theo gì", "chuẩn bị những gì")
   - "places": Tư vấn về địa điểm đi chơi (ví dụ: "nên đi đâu", "chỗ nào để đi chơi")
   - "activities": Tư vấn về hoạt động (ví dụ: "nên làm gì", "hoạt động gì phù hợp")
   - "general": Tư vấn chung kết hợp nhiều loại trên
3. Xác định địa điểm đề cập trong câu hỏi (`location`), nếu có.
4. Xác định mô tả thời gian trong câu hỏi (`date_description`), nếu có.

Ví dụ:
- User: "hôm nay nên mặc gì" -> { "is_advice_query": true, "advice_type": "clothing", "location": null, "date_description": "hôm nay" }
- User: "đi chơi ở Đà Nẵng ngày mai nên mang theo gì" -> { "is_advice_query": true, "advice_type": "items", "location": "Da Nang", "date_description": "ngày mai" }
- User: "thời tiết Hà Nội cuối tuần có thích hợp để đi chơi ở công viên không" -> { "is_advice_query": true, "advice_type": "places", "location": "Hanoi", "date_description": "cuối tuần" }
- User: "nên làm gì khi trời mưa ở Sài Gòn cuối tuần" -> { "is_advice_query": true, "advice_type": "activities", "location": "Ho Chi Minh City", "date_description": "cuối tuần" }
- User: "tư vấn giúp tôi mai đi Hà Nội nên chuẩn bị thế nào" -> { "is_advice_query": true, "advice_type": "general", "location": "Hanoi", "date_description": "mai" }
- User: "thời tiết Hà Nội hôm nay thế nào" -> { "is_advice_query": false, "advice_type": null, "location": "Hanoi", "date_description": "hôm nay" }

Trả lời DƯỚI DẠNG JSON HỢP LỆ với 4 trường: is_advice_query (boolean), advice_type (string hoặc null), location (string hoặc null), date_description (string hoặc null).
"""
            response = await asyncio.to_thread(
                 client.chat.completions.create,
                 model="gpt-4o-mini",
                 messages=[
                     {"role": "system", "content": system_prompt},
                     {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\""}
                 ],
                 temperature=0.1,
                 max_tokens=150,
                 response_format={"type": "json_object"}
            )

            result_str = response.choices[0].message.content
            logger.info(f"Kết quả detect_weather_advice_need (raw): {result_str}")

            try:
                result = json.loads(result_str)
                is_advice_query = result.get("is_advice_query", False)
                advice_type = result.get("advice_type") if is_advice_query else None
                location = result.get("location")
                date_description = result.get("date_description")
                
                if is_advice_query and not advice_type:
                    advice_type = "general"  # Mặc định nếu không xác định được
                    
                if is_advice_query and not location:
                    location = "Hanoi"  # Mặc định là Hà Nội
                    
                logger.info(f"Phân tích truy vấn tư vấn '{query}': is_advice_query={is_advice_query}, advice_type='{advice_type}', location='{location}', date_description='{date_description}'")
                return is_advice_query, advice_type, location, date_description

            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Lỗi giải mã JSON từ detect_weather_advice_need: {e}. Raw: {result_str}")
                return False, "general", None, None
                
        except Exception as e:
            logger.error(f"Lỗi khi gọi OpenAI trong detect_weather_advice_need: {e}", exc_info=True)
            return False, "general", None, None

async def detect_weather_query(query, api_key):
    """Phát hiện nếu query là về thời tiết và trích xuất địa điểm."""
    if not api_key or not query: 
        return False, None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        system_prompt = """
Bạn là một hệ thống phân loại truy vấn thời tiết thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có phải là về thời tiết hoặc liên quan đến thời tiết không (`is_weather_query`).
   - Câu hỏi về thời tiết: "thời tiết ở Hà Nội", "trời có mưa không", "nhiệt độ hôm nay", "có nắng không", etc.
   - Câu hỏi liên quan đến thời tiết: "nên mặc gì hôm nay", "có nên mang ô không", "có nên đi biển cuối tuần", etc.
2. Nếu là truy vấn thời tiết, xác định địa điểm đề cập trong câu hỏi (`location`).
   - Trích xuất TÊN ĐỊA ĐIỂM CHÍNH XÁC từ câu hỏi, ví dụ: "Hà Nội", "Đà Nẵng", "Sài Gòn", "Ho Chi Minh City"
   - Chỉ trả về "Hanoi" khi câu hỏi KHÔNG đề cập đến bất kỳ địa điểm nào cụ thể.
   - Đối với "Sài Gòn", hãy trả về "Ho Chi Minh City" để API tìm kiếm chính xác.

Ví dụ:
- User: "thời tiết ở Đà Nẵng hôm nay" -> { "is_weather_query": true, "location": "Da Nang" }
- User: "trời có mưa không" -> { "is_weather_query": true, "location": "Hanoi" }
- User: "thời tiết Sài Gòn hôm nay" -> { "is_weather_query": true, "location": "Ho Chi Minh City" }
- User: "nên mặc áo gì ở Sài Gòn hôm nay" -> { "is_weather_query": true, "location": "Ho Chi Minh City" }
- User: "kết quả trận MU tối qua" -> { "is_weather_query": false, "location": null }

Trả lời DƯỚI DẠNG JSON HỢP LỆ với 2 trường: is_weather_query (boolean), location (string hoặc null).
"""
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model="gpt-4o-mini",
             messages=[
                 {"role": "system", "content": system_prompt},
                 {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\""}
             ],
             temperature=0.1,
             max_tokens=150,
             response_format={"type": "json_object"}
        )

        result_str = response.choices[0].message.content
        logger.info(f"Kết quả detect_weather_query (raw): {result_str}")

        try:
            result = json.loads(result_str)
            is_weather_query = result.get("is_weather_query", False)
            location = result.get("location")
            
            if is_weather_query and not location:
                location = "Hanoi"  # Mặc định là Hà Nội
                
            logger.info(f"Phân tích truy vấn '{query}': is_weather_query={is_weather_query}, location='{location}'")
            return is_weather_query, location

        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Lỗi giải mã JSON từ detect_weather_query: {e}. Raw: {result_str}")
            return False, None
    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_weather_query: {e}", exc_info=True)
        return False, None

# ------- Request & Response Models --------
class MessageContent(BaseModel):
    type: str
    text: Optional[str] = None
    html: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None # Expects {"url": "data:..."}
    audio_data: Optional[str] = None # Base64 encoded

class Message(BaseModel):
    role: str
    content: Union[str, List[MessageContent], None] = None # Allow str for simple text, list for multimodal
    tool_calls: Optional[List[Dict[str, Any]]] = None # For assistant messages with tool calls
    tool_call_id: Optional[str] = None # For tool role messages

class ChatRequest(BaseModel):
    session_id: str
    member_id: Optional[str] = None
    message: MessageContent  # The new message from user
    content_type: str = "text"  # "text", "image", "audio"
    openai_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    messages: Optional[List[Message]] = None  # Optional full history from client


class ChatResponse(BaseModel):
    session_id: str
    messages: List[Message] # Return only the *last* assistant message(s)
    audio_response: Optional[str] = None
    response_format: Optional[str] = "html"
    content_type: Optional[str] = "text" # Reflect back the input type
    event_data: Optional[Dict[str, Any]] = None # Include event data if generated

class MemberModel(BaseModel):
    name: str
    age: Optional[str] = None
    preferences: Optional[Dict[str, str]] = None

class EventModel(BaseModel):
    title: str
    date: str # Expects YYYY-MM-DD
    time: Optional[str] = "19:00"
    description: Optional[str] = None
    participants: Optional[List[str]] = None

class NoteModel(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = None

class SearchRequest(BaseModel):
    query: str
    tavily_api_key: str
    openai_api_key: str
    is_news_query: Optional[bool] = None

class SuggestedQuestionsResponse(BaseModel):
    session_id: str
    member_id: Optional[str] = None
    suggested_questions: List[str]
    timestamp: str


# ------- API Endpoints -------------

# Helper function to execute a tool call
def execute_tool_call(tool_call: ChatCompletionMessageToolCall, current_member_id: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Executes the appropriate Python function based on the tool call.
    Handles date calculation and event classification for event tools. # MODIFIED
    Returns a tuple: (event_data_for_frontend, tool_result_content_for_llm)
    """
    function_name = tool_call.function.name
    try:
        arguments_str = tool_call.function.arguments
        if not arguments_str:
             arguments = {}
             logger.warning(f"Tool call {function_name} received empty arguments string.")
        else:
             arguments = json.loads(arguments_str)

        logger.info(f"Executing tool: {function_name} with args: {arguments}")

        # --- Special Handling for Event Dates and Classification --- # MODIFIED
        final_date_str = None
        repeat_type = "ONCE"
        cron_expression = ""
        event_category = "General" # Default category # ADDED
        event_action_data = None

        if function_name in ["add_event", "update_event"]:
            date_description = arguments.get("date_description")
            time_str = arguments.get("time", "19:00")
            description = arguments.get("description", "")
            title = arguments.get("title", "") # Cần title để phân loại

            # Lấy title/description cũ nếu update mà không cung cấp cái mới (để phân loại)
            if function_name == "update_event":
                event_id = arguments.get("event_id")
                if event_id and str(event_id) in events_data:
                     event_id_str = str(event_id)
                     if not title: title = events_data[event_id_str].get("title", "")
                     if not description: description = events_data[event_id_str].get("description", "")
                     arguments["id"] = event_id_str # Đảm bảo có id dạng string
                else:
                    # Quan trọng: Trả về lỗi ngay nếu không tìm thấy event ID khi update
                    error_msg = f"Lỗi: Không tìm thấy sự kiện ID '{event_id}' để cập nhật."
                    logger.error(error_msg)
                    return None, error_msg # Trả về tuple (None, error_message)

            # --- Phân loại sự kiện TRƯỚC khi xử lý ngày giờ ---
            event_category = classify_event(title, description) # <<< GỌI HÀM PHÂN LOẠI
            arguments["category"] = event_category # Thêm category vào arguments để lưu
            logger.info(f"Determined event category: '{event_category}' for title: '{title}'")

            # --- Xử lý ngày giờ (giữ nguyên logic cũ) ---
            if date_description:
                # Phân tích và xử lý mô tả ngày một lần duy nhất
                final_date_str, repeat_type, cron_expression = DateTimeHandler.parse_and_process_event_date(
                    date_description, time_str, description, title
                )

                if final_date_str:
                    logger.info(f"Ngày đã xử lý: '{final_date_str}' từ mô tả '{date_description}'")
                    arguments["date"] = final_date_str
                else:
                    logger.warning(f"Không thể xác định ngày từ '{date_description}'. Ngày sự kiện sẽ trống hoặc không thay đổi.")
                    # Không xóa date khỏi arguments nếu nó đã tồn tại (trường hợp update không đổi ngày)
                    if "date" not in arguments and function_name == "add_event":
                         pass # Cho phép date là None khi thêm mới nếu là recurring
                    elif "date" in arguments and not final_date_str:
                         # Nếu update mà date_description không parse được, không nên xóa date cũ
                         logger.info(f"Update event: date_description '{date_description}' không hợp lệ, giữ nguyên date cũ nếu có.")
                         # Không cần làm gì thêm, date cũ vẫn trong arguments nếu được truyền
                    elif "date" in arguments and final_date_str is None and event_to_update.get("repeat_type") == "ONCE":
                        # Nếu update sự kiện ONCE mà date_desc không parse đc, nên báo lỗi hoặc giữ ngày cũ thay vì xóa?
                        # Hiện tại đang giữ nguyên date cũ trong arguments nếu có.
                        pass


            # Nếu không có date_description, nhưng là update, cần giữ lại repeat_type cũ nếu có
            elif function_name == "update_event" and event_id_str in events_data:
                 repeat_type = events_data[event_id_str].get("repeat_type", "ONCE")
                 # Lấy lại cron expression cũ nếu không có thay đổi về description/title/time ảnh hưởng cron
                 # Hoặc đơn giản là không cập nhật cron nếu không có date_description/description mới?
                 # Hiện tại: Sẽ không tạo cron mới nếu không có date_description.
                 # Có thể cần logic phức tạp hơn để cập nhật cron nếu chỉ description thay đổi.

            if "date_description" in arguments:
                del arguments["date_description"]

            # Đặt repeat_type đã được xác định (từ date_description hoặc từ event cũ)
            arguments['repeat_type'] = repeat_type

            logger.info(f"Event type: {repeat_type}, Cron generated: '{cron_expression}'")

            # Chuẩn bị data để trả về frontend nếu cần
            event_action_data = {
                "action": "add" if function_name == "add_event" else "update",
                "id": arguments.get("id"), # Sẽ là None cho add, có giá trị cho update
                "title": title,
                "description": description,
                "cron_expression": cron_expression,
                "repeat_type": repeat_type,
                "original_date": final_date_str, # Ngày YYYY-MM-DD đã parse
                "original_time": time_str,
                "participants": arguments.get("participants", []),
                "category": event_category # <<< Thêm category vào dữ liệu trả về
            }

        # --- Assign creator/updater ID ---
        if current_member_id:
            if function_name == "add_event" or function_name == "add_note":
                arguments["created_by"] = current_member_id
            elif function_name == "update_event":
                 arguments["updated_by"] = current_member_id

        # --- Execute the function ---
        if function_name in tool_functions:
            func_to_call = tool_functions[function_name]
            try:
                result = func_to_call(arguments) # arguments giờ đã bao gồm 'category' nếu là event
                if result is False:
                     tool_result_content = f"Thất bại khi thực thi {function_name}. Chi tiết lỗi đã được ghi lại."
                     logger.error(f"Execution failed for tool {function_name} with args {arguments}")
                     event_action_data = None # Reset event data if execution failed
                else:
                     tool_result_content = f"Đã thực thi thành công {function_name}."
                     logger.info(f"Successfully executed tool {function_name}")
                     # Xử lý event_action_data cho delete
                     if function_name == "delete_event":
                         deleted_event_id = arguments.get("event_id")
                         # Cố gắng lấy category của event bị xóa để trả về (nếu cần)
                         deleted_category = events_data.get(str(deleted_event_id), {}).get("category", "Unknown") if str(deleted_event_id) in events_data else "Unknown" # Lấy trước khi pop
                         # Logic xóa thực tế nằm trong hàm delete_event được gọi ở trên
                         # Cập nhật event_action_data sau khi hàm delete_event chạy thành công
                         event_action_data = {
                            "action": "delete",
                            "id": deleted_event_id,
                            "category": deleted_category # Trả về category của event đã xóa
                         }
                         tool_result_content = f"Đã xóa thành công sự kiện ID {deleted_event_id}."

                return event_action_data, tool_result_content
            except Exception as func_exc:
                 logger.error(f"Error executing tool function {function_name}: {func_exc}", exc_info=True)
                 # Giữ lại event_action_data = None ở đây vì tool lỗi
                 return None, f"Lỗi trong quá trình thực thi {function_name}: {str(func_exc)}"
        else:
            logger.error(f"Unknown tool function: {function_name}")
            return None, f"Lỗi: Không tìm thấy hàm cho công cụ {function_name}."

    except json.JSONDecodeError as json_err:
        logger.error(f"Error decoding arguments for {function_name}: {json_err}")
        logger.error(f"Invalid JSON string: {tool_call.function.arguments}")
        return None, f"Lỗi: Dữ liệu cho công cụ {function_name} không hợp lệ (JSON sai định dạng)."
    except Exception as e:
        logger.error(f"Unexpected error in execute_tool_call for {function_name}: {e}", exc_info=True)
        return None, f"Lỗi không xác định khi chuẩn bị thực thi {function_name}."

# --- Endpoint /chat ---
@app.post("/chat")
async def chat_endpoint(chat_request: ChatRequest):
    """
    Endpoint chính cho trò chuyện (sử dụng Tool Calling).
    Includes event_data in the response.
    """
    openai_api_key = chat_request.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    tavily_api_key = chat_request.tavily_api_key or os.getenv("TAVILY_API_KEY", "")
    if not openai_api_key or "sk-" not in openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ")

    session = session_manager.get_session(chat_request.session_id)
    current_member_id = chat_request.member_id or session.get("current_member")
    session["current_member"] = current_member_id

    # --- Message Handling ---
    if chat_request.messages is not None and not session.get("messages"):
         logger.info(f"Loading message history from client for session {chat_request.session_id}")
         session["messages"] = [msg.dict(exclude_none=True) for msg in chat_request.messages]

    message_content_model = chat_request.message
    message_dict = message_content_model.dict(exclude_none=True)
    logger.info(f"Nhận request với content_type: {chat_request.content_type}")
    processed_content_list = []

    if chat_request.content_type == "audio" and message_dict.get("type") == "audio" and message_dict.get("audio_data"):
        processed_audio = process_audio(message_dict, openai_api_key)
        if processed_audio and processed_audio.get("text"):
             processed_content_list.append({"type": "text", "text": processed_audio["text"]})
             logger.info(f"Đã xử lý audio thành text: {processed_audio['text'][:50]}...")
        else:
             logger.error("Xử lý audio thất bại hoặc không trả về text.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý audio]"})

    elif chat_request.content_type == "image" and message_dict.get("type") == "image_url":
        logger.info(f"Đã nhận hình ảnh: {message_dict.get('image_url', {}).get('url', '')[:60]}...")
        if message_dict.get("image_url"):
            processed_content_list.append({"type": "image_url", "image_url": message_dict["image_url"]})
        else:
            logger.error("Content type là image nhưng thiếu image_url.")
            processed_content_list.append({"type": "text", "text": "[Lỗi xử lý ảnh: thiếu URL]"})
        if message_dict.get("text"):
             processed_content_list.append({"type": "text", "text": message_dict["text"]})

    elif message_dict.get("type") == "html":
         if message_dict.get("html"):
             clean_text = re.sub(r'<[^>]*>', ' ', message_dict["html"])
             clean_text = unescape(clean_text)
             clean_text = re.sub(r'\s+', ' ', clean_text).strip()
             processed_content_list.append({"type": "text", "text": clean_text})
             logger.info(f"Đã xử lý HTML thành text: {clean_text[:50]}...")
         else:
             logger.warning("Loại nội dung là html nhưng thiếu trường 'html'.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý HTML: thiếu nội dung]"})

    elif message_dict.get("type") == "text":
        text_content = message_dict.get("text")
        if text_content:
            processed_content_list.append({"type": "text", "text": text_content})
        else:
             logger.warning("Loại nội dung là text nhưng thiếu trường 'text'.")
             # processed_content_list.append({"type": "text", "text": ""}) # Allow empty text?

    else:
         logger.warning(f"Loại nội dung không xác định hoặc thiếu dữ liệu: {message_dict.get('type')}")
         processed_content_list.append({"type": "text", "text": "[Nội dung không hỗ trợ hoặc bị lỗi]"})

    if processed_content_list:
         session["messages"].append({
             "role": "user",
             "content": processed_content_list
         })
    else:
         logger.error("Không thể xử lý nội dung tin nhắn người dùng.")
         # Raise error or return immediately?


    # --- Tool Calling Flow ---
    final_event_data_to_return: Optional[Dict[str, Any]] = None

    try:
        client = OpenAI(api_key=openai_api_key)
        system_prompt_content = build_system_prompt(current_member_id)

        openai_messages = [{"role": "system", "content": system_prompt_content}]
        for msg in session["messages"]:
             message_for_api = {
                 "role": msg["role"],
                 **({ "tool_calls": msg["tool_calls"] } if msg.get("tool_calls") else {}),
                 **({ "tool_call_id": msg.get("tool_call_id") } if msg.get("tool_call_id") else {}),
             }
             msg_content = msg.get("content")
             if isinstance(msg_content, list):
                  message_for_api["content"] = msg_content
             elif isinstance(msg_content, str):
                  message_for_api["content"] = msg_content
             elif msg.get("role") == "tool":
                  message_for_api["content"] = str(msg_content) if msg_content is not None else ""
             else:
                  logger.warning(f"Định dạng content không mong đợi cho role {msg['role']}: {type(msg_content)}. Sử dụng chuỗi rỗng.")
                  message_for_api["content"] = ""
             openai_messages.append(message_for_api)


        # --- Check Search Need ---
        search_result_for_prompt = await check_search_need(
            openai_messages, 
            openai_api_key, 
            tavily_api_key,
            lat=chat_request.latitude,
            lon=chat_request.longitude
        )
        if search_result_for_prompt:
             # Replace or append to system prompt
             openai_messages[0] = {"role": "system", "content": system_prompt_content + search_result_for_prompt}


        logger.info("--- Calling OpenAI API (Potential First Pass) ---")
        logger.debug(f"Messages sent (last 3): {json.dumps(openai_messages[-3:], indent=2, ensure_ascii=False)}")

        first_response = client.chat.completions.create(
            model=openai_model,
            messages=openai_messages,
            tools=available_tools,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=2048
        )

        response_message: ChatCompletionMessage = first_response.choices[0].message
        session["messages"].append(response_message.dict(exclude_none=True))

        # --- Handle Tool Calls ---
        tool_calls = response_message.tool_calls
        if tool_calls:
            logger.info(f"--- Tool Calls Detected: {len(tool_calls)} ---")
            messages_for_second_call = openai_messages + [response_message.dict(exclude_none=True)]

            for tool_call in tool_calls:
                event_data_from_tool, tool_result_content = execute_tool_call(tool_call, current_member_id)

                if event_data_from_tool and final_event_data_to_return is None:
                    if event_data_from_tool.get("action") in ["add", "update", "delete"]:
                        final_event_data_to_return = event_data_from_tool
                        logger.info(f"Captured event_data for response: {final_event_data_to_return}")

                tool_result_message = {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": tool_call.function.name,
                    "content": tool_result_content,
                }
                messages_for_second_call.append(tool_result_message)
                session["messages"].append(tool_result_message)

            logger.info("--- Calling OpenAI API (Second Pass - Summarizing Tool Results) ---")
            logger.debug(f"Messages for second call (last 4): {json.dumps(messages_for_second_call[-4:], indent=2, ensure_ascii=False)}")

            second_response = client.chat.completions.create(
                model=openai_model,
                messages=messages_for_second_call,
                temperature=0.7,
                max_tokens=1024
            )
            final_assistant_message = second_response.choices[0].message
            final_response_content = final_assistant_message.content

            session["messages"].append(final_assistant_message.dict(exclude_none=True))
            logger.info("Tool execution and summary completed.")

        else:
            logger.info("--- No Tool Calls Detected ---")
            final_response_content = response_message.content

        # --- Final Processing & Response ---
        final_html_content = final_response_content if final_response_content else "Tôi đã thực hiện xong yêu cầu của bạn."

        audio_response_b64 = text_to_speech_google(final_html_content)

        if current_member_id:
             summary = await generate_chat_summary(session["messages"], openai_api_key)
             save_chat_history(current_member_id, session["messages"], summary, chat_request.session_id)

        session_manager.update_session(chat_request.session_id, {"messages": session["messages"]})

        last_message_dict = session["messages"][-1] if session["messages"] else {}
        last_assistant_msg_obj = None
        if last_message_dict.get("role") == "assistant":
             try:
                  content = last_message_dict.get("content")
                  content_for_model = []
                  if isinstance(content, str):
                       content_for_model.append(MessageContent(type="html", html=content))
                  elif isinstance(content, list):
                       if all(isinstance(item, dict) and 'type' in item for item in content):
                           content_for_model = [MessageContent(**item) for item in content]
                       else:
                            logger.warning("Assistant message content list has unexpected structure. Converting to text.")
                            content_text = " ".join(map(str, content))
                            content_for_model.append(MessageContent(type="html", html=content_text))
                  else:
                       content_for_model.append(MessageContent(type="html", html=""))

                  last_assistant_msg_obj = Message(
                      role="assistant",
                      content=content_for_model,
                      tool_calls=last_message_dict.get("tool_calls")
                  )
             except Exception as model_err:
                  logger.error(f"Error creating response Message object: {model_err}", exc_info=True)

        if not last_assistant_msg_obj:
             fallback_content = MessageContent(type="html", html="Đã có lỗi xảy ra hoặc không có phản hồi.")
             last_assistant_msg_obj = Message(role="assistant", content=[fallback_content])

        return ChatResponse(
            session_id=chat_request.session_id,
            messages=[last_assistant_msg_obj],
            audio_response=audio_response_b64,
            response_format="html",
            content_type=chat_request.content_type,
            event_data=final_event_data_to_return
        )

    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng trong /chat endpoint: {str(e)}", exc_info=True)
        session_manager.update_session(chat_request.session_id, {"messages": session.get("messages", [])})
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý chat: {str(e)}")


# --- Endpoint /chat/stream ---
@app.post("/chat/stream")
async def chat_stream_endpoint(chat_request: ChatRequest):
    """
    Endpoint streaming cho trò chuyện (sử dụng Tool Calling).
    Includes event_data in the final completion message.
    """
    openai_api_key = chat_request.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    tavily_api_key = chat_request.tavily_api_key or os.getenv("TAVILY_API_KEY", "")
    if not openai_api_key or "sk-" not in openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ")

    session = session_manager.get_session(chat_request.session_id)
    current_member_id = chat_request.member_id or session.get("current_member")
    session["current_member"] = current_member_id

    # --- Message Handling ---
    if chat_request.messages is not None and not session.get("messages"):
         logger.info(f"Stream: Loading message history from client for session {chat_request.session_id}")
         session["messages"] = [msg.dict(exclude_none=True) for msg in chat_request.messages]

    message_content_model = chat_request.message
    message_dict = message_content_model.dict(exclude_none=True)
    logger.info(f"Stream: Nhận request với content_type: {chat_request.content_type}")
    processed_content_list = []

    # --- Process incoming message based on content_type ---
    if chat_request.content_type == "audio" and message_dict.get("type") == "audio" and message_dict.get("audio_data"):
        processed_audio = process_audio(message_dict, openai_api_key)
        if processed_audio and processed_audio.get("text"):
             processed_content_list.append({"type": "text", "text": processed_audio["text"]})
             logger.info(f"Stream: Đã xử lý audio thành text: {processed_audio['text'][:50]}...")
        else:
             logger.error("Stream: Xử lý audio thất bại.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý audio]"})

    elif chat_request.content_type == "image" and message_dict.get("type") == "image_url":
        logger.info(f"Stream: Đã nhận hình ảnh: {message_dict.get('image_url', {}).get('url', '')[:60]}...")
        if message_dict.get("image_url"):
            processed_content_list.append({"type": "image_url", "image_url": message_dict["image_url"]})
        else:
            logger.error("Stream: Content type là image nhưng thiếu image_url.")
            processed_content_list.append({"type": "text", "text": "[Lỗi xử lý ảnh: thiếu URL]"})
        if message_dict.get("text"):
             processed_content_list.append({"type": "text", "text": message_dict["text"]})

    elif message_dict.get("type") == "html":
         if message_dict.get("html"):
            clean_text = re.sub(r'<[^>]*>', ' ', message_dict["html"])
            clean_text = unescape(clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            processed_content_list.append({"type": "text", "text": clean_text})
            logger.info(f"Stream: Đã xử lý HTML thành text: {clean_text[:50]}...")
         else:
             logger.warning("Stream: Loại nội dung là html nhưng thiếu trường 'html'.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý HTML: thiếu nội dung]"})

    elif message_dict.get("type") == "text":
        text_content = message_dict.get("text")
        if text_content:
            processed_content_list.append({"type": "text", "text": text_content})
        else:
             logger.warning("Stream: Loại nội dung là text nhưng thiếu trường 'text'.")

    else:
         logger.warning(f"Stream: Loại nội dung không xác định hoặc thiếu dữ liệu: {message_dict.get('type')}")
         processed_content_list.append({"type": "text", "text": "[Nội dung không hỗ trợ hoặc bị lỗi]"})
    # --- End of message processing ---

    if processed_content_list:
         session["messages"].append({
             "role": "user",
             "content": processed_content_list
         })
    else:
         logger.error("Stream: Không thể xử lý nội dung tin nhắn đến. Không thêm vào lịch sử.")

    # --- Streaming Generator ---
    async def response_stream_generator():
        final_event_data_to_return: Optional[Dict[str, Any]] = None
        client = OpenAI(api_key=openai_api_key)
        system_prompt_content = build_system_prompt(current_member_id)

        openai_messages = [{"role": "system", "content": system_prompt_content}]
        for msg in session["messages"]:
             message_for_api = {
                 "role": msg["role"],
                 **({ "tool_calls": msg["tool_calls"] } if msg.get("tool_calls") else {}),
                 **({ "tool_call_id": msg.get("tool_call_id") } if msg.get("tool_call_id") else {}),
             }
             msg_content = msg.get("content")
             if isinstance(msg_content, list): message_for_api["content"] = msg_content
             elif isinstance(msg_content, str): message_for_api["content"] = msg_content
             elif msg.get("role") == "tool": message_for_api["content"] = str(msg_content) if msg_content is not None else ""
             else: message_for_api["content"] = ""
             openai_messages.append(message_for_api)

        # --- Check Search Need ---
        try:
             search_result_for_prompt = await check_search_need(
                 openai_messages, 
                 openai_api_key, 
                 tavily_api_key,
                 lat=chat_request.latitude,
                 lon=chat_request.longitude
             )
             if search_result_for_prompt:
                  openai_messages[0] = {"role": "system", "content": system_prompt_content + search_result_for_prompt}
        except Exception as search_err:
             logger.error(f"Error during search need check: {search_err}", exc_info=True)

        accumulated_tool_calls = []
        accumulated_assistant_content = ""
        assistant_message_dict_for_session = {"role": "assistant", "content": None, "tool_calls": None}
        tool_call_chunks = {}

        # --- Main Streaming Logic ---
        try:
            logger.info("--- Calling OpenAI API (Streaming - Potential First Pass) ---")
            stream = client.chat.completions.create(
                model=openai_model,
                messages=openai_messages,
                tools=available_tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=2048,
                stream=True
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta: continue

                finish_reason = chunk.choices[0].finish_reason

                if delta.content:
                    accumulated_assistant_content += delta.content
                    yield json.dumps({"chunk": delta.content, "type": "html", "content_type": chat_request.content_type}) + "\n"
                    await asyncio.sleep(0)

                if delta.tool_calls:
                    for tc_chunk in delta.tool_calls:
                        index = tc_chunk.index
                        if index not in tool_call_chunks:
                            tool_call_chunks[index] = {"function": {"arguments": ""}}
                        if tc_chunk.id: tool_call_chunks[index]["id"] = tc_chunk.id
                        if tc_chunk.type: tool_call_chunks[index]["type"] = tc_chunk.type
                        if tc_chunk.function:
                             if tc_chunk.function.name: tool_call_chunks[index]["function"]["name"] = tc_chunk.function.name
                             if tc_chunk.function.arguments: tool_call_chunks[index]["function"]["arguments"] += tc_chunk.function.arguments

                if finish_reason:
                    if finish_reason == "tool_calls":
                        logger.info("--- Stream detected tool_calls ---")
                        for index in sorted(tool_call_chunks.keys()):
                             chunk_data = tool_call_chunks[index]
                             if chunk_data.get("id") and chunk_data.get("function", {}).get("name"):
                                  try:
                                       reconstructed_tc = ChatCompletionMessageToolCall(
                                           id=chunk_data["id"],
                                           type='function',
                                           function=chunk_data["function"]
                                       )
                                       accumulated_tool_calls.append(reconstructed_tc)
                                  except Exception as recon_err:
                                       logger.error(f"Error reconstructing tool call at index {index}: {recon_err} - Data: {chunk_data}")
                             else:
                                  logger.error(f"Incomplete data for tool call reconstruction at index {index}: {chunk_data}")

                        if accumulated_tool_calls:
                             assistant_message_dict_for_session["tool_calls"] = [tc.dict() for tc in accumulated_tool_calls]
                             assistant_message_dict_for_session["content"] = accumulated_assistant_content or None
                             logger.info(f"Reconstructed {len(accumulated_tool_calls)} tool calls.")
                        else:
                             logger.error("Tool calls detected by finish_reason, but failed reconstruction.")
                             assistant_message_dict_for_session["content"] = accumulated_assistant_content

                    elif finish_reason == "stop":
                        logger.info("--- Stream finished without tool_calls ---")
                        assistant_message_dict_for_session["content"] = accumulated_assistant_content
                    else:
                         logger.warning(f"Stream finished with reason: {finish_reason}")
                         assistant_message_dict_for_session["content"] = accumulated_assistant_content
                    break

            # --- Execute Tools and Second Stream (if needed) ---
            if accumulated_tool_calls:
                logger.info(f"--- Executing {len(accumulated_tool_calls)} Tool Calls (Non-Streamed) ---")
                # Add the first assistant message (which contained tool calls) to history
                # Check if it was already added, avoid duplicates
                if not session["messages"] or session["messages"][-1].get("tool_calls") != assistant_message_dict_for_session.get("tool_calls"):
                     session["messages"].append(assistant_message_dict_for_session)

                messages_for_second_call = openai_messages + [assistant_message_dict_for_session]

                for tool_call in accumulated_tool_calls:
                    yield json.dumps({"tool_start": tool_call.function.name}) + "\n"; await asyncio.sleep(0.05)
                    event_data_from_tool, tool_result_content = execute_tool_call(tool_call, current_member_id)

                    if event_data_from_tool and final_event_data_to_return is None:
                         if event_data_from_tool.get("action") in ["add", "update", "delete"]:
                              final_event_data_to_return = event_data_from_tool
                              logger.info(f"Captured event_data for stream response: {final_event_data_to_return}")

                    yield json.dumps({"tool_end": tool_call.function.name, "result_preview": tool_result_content[:50]+"..."}) + "\n"; await asyncio.sleep(0.05)

                    tool_result_message = {
                        "tool_call_id": tool_call.id, "role": "tool",
                        "name": tool_call.function.name, "content": tool_result_content,
                    }
                    messages_for_second_call.append(tool_result_message)
                    session["messages"].append(tool_result_message)

                logger.info("--- Calling OpenAI API (Streaming - Second Pass - Summary) ---")
                logger.debug(f"Messages for second stream call (last 4): {json.dumps(messages_for_second_call[-4:], indent=2, ensure_ascii=False)}")
                summary_stream = client.chat.completions.create(
                    model=openai_model, messages=messages_for_second_call,
                    temperature=0.7, max_tokens=1024, stream=True
                )

                final_summary_content = ""
                async for summary_chunk in summary_stream:
                     delta_summary = summary_chunk.choices[0].delta.content if summary_chunk.choices else None
                     if delta_summary:
                          final_summary_content += delta_summary
                          yield json.dumps({"chunk": delta_summary, "type": "html", "content_type": chat_request.content_type}) + "\n"
                          await asyncio.sleep(0)

                # Add final summary message to history
                session["messages"].append({"role": "assistant", "content": final_summary_content})
                final_response_for_tts = final_summary_content if final_summary_content else "Đã xử lý xong."

            else:
                # If no tool calls, the first assistant message is the final one
                # Check if it was already added, avoid duplicates
                if not session["messages"] or session["messages"][-1].get("content") != assistant_message_dict_for_session.get("content"):
                     session["messages"].append(assistant_message_dict_for_session)
                final_response_for_tts = accumulated_assistant_content if accumulated_assistant_content else "Vâng."

            # --- Post-Streaming Processing ---
            logger.info("Generating final audio response...")
            audio_response_b64 = text_to_speech_google(final_response_for_tts)

            if current_member_id:
                 summary = await generate_chat_summary(session["messages"], openai_api_key)
                 save_chat_history(current_member_id, session["messages"], summary, chat_request.session_id)

            session_manager.update_session(chat_request.session_id, {"messages": session["messages"]})

            complete_response = {
                "complete": True,
                "audio_response": audio_response_b64,
                "content_type": chat_request.content_type,
                "event_data": final_event_data_to_return
            }
            yield json.dumps(complete_response) + "\n"
            logger.info("--- Streaming finished successfully ---")

        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng trong quá trình stream: {str(e)}", exc_info=True)
            error_msg = f"Xin lỗi, đã có lỗi xảy ra trong quá trình xử lý: {str(e)}"
            try:
                yield json.dumps({"error": error_msg, "content_type": chat_request.content_type}) + "\n"
            except Exception as yield_err:
                 logger.error(f"Lỗi khi gửi thông báo lỗi stream cuối cùng: {yield_err}")
        finally:
            logger.info("Đảm bảo lưu session sau khi stream kết thúc hoặc gặp lỗi.")
            session_manager.update_session(chat_request.session_id, {"messages": session.get("messages", [])})

    # Return the StreamingResponse object
    return StreamingResponse(
        response_stream_generator(),
        media_type="application/x-ndjson"
    )

@app.get("/weather")
async def get_weather(
    location: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    type: str = "current",  # "current" hoặc "forecast"
    lang: str = "vi"
):
    """Lấy thông tin thời tiết từ OpenWeatherMap."""
    api_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenWeatherMap API key không có sẵn")
        
    weather_service = WeatherService(api_key)
    
    # Ưu tiên địa điểm, sau đó đến tọa độ
    if location:
        logger.info(f"API call: Sử dụng địa điểm: {location}")
        if type == "current":
            result = await weather_service.get_current_weather(location=location, lang=lang)
        else:
            result = await weather_service.get_forecast(location=location, lang=lang)
    elif lat is not None and lon is not None:
        logger.info(f"API call: Sử dụng tọa độ: lat={lat}, lon={lon}")
        if type == "current":
            result = await weather_service.get_current_weather(lat=lat, lon=lon, lang=lang)
        else:
            result = await weather_service.get_forecast(lat=lat, lon=lon, lang=lang)
    else:
        logger.info("API call: Không có địa điểm/tọa độ, sử dụng mặc định Hà Nội")
        if type == "current":
            result = await weather_service.get_current_weather(location="Hanoi", lang=lang)
        else:
            result = await weather_service.get_forecast(location="Hanoi", lang=lang)
        
    if not result:
        raise HTTPException(status_code=500, detail="Không thể lấy dữ liệu thời tiết")
        
    return result

# ------- Other Helper Functions --------

# --- Audio Processing ---
def process_audio(message_dict, api_key):
    """Chuyển đổi audio base64 sang text dùng Whisper."""
    try:
        if not message_dict.get("audio_data"):
            logger.error("process_audio: Thiếu audio_data.")
            return None
        audio_data = base64.b64decode(message_dict["audio_data"])

        temp_audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.wav") # Assume wav for simplicity

        with open(temp_audio_path, "wb") as f:
            f.write(audio_data)

        client = OpenAI(api_key=api_key)
        with open(temp_audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        os.remove(temp_audio_path)

        return {"type": "text", "text": transcript.text}

    except base64.binascii.Error as b64_err:
        logger.error(f"Lỗi giải mã Base64 audio: {b64_err}")
        return None
    except Exception as e:
        logger.error(f"Lỗi khi xử lý audio: {e}", exc_info=True)
        if 'temp_audio_path' in locals() and os.path.exists(temp_audio_path):
             try: os.remove(temp_audio_path)
             except OSError: pass
        return None

# --- System Prompt Builder ---
def build_system_prompt(current_member_id=None):
    """Xây dựng system prompt cho trợ lý gia đình (sử dụng Tool Calling)."""
    # Start with the base persona and instructions
    system_prompt_parts = [
        "Bạn là trợ lý gia đình thông minh, đa năng và thân thiện tên là HGDS. Nhiệm vụ của bạn là giúp quản lý thông tin gia đình, sự kiện, ghi chú, trả lời câu hỏi, tìm kiếm thông tin, phân tích hình ảnh, và cung cấp thông tin thời tiết.",
        "Giao tiếp tự nhiên, lịch sự và theo phong cách trò chuyện bằng tiếng Việt.",
        "Sử dụng định dạng HTML đơn giản cho phản hồi văn bản (thẻ p, b, i, ul, li, h3, h4, br).",
        "Bạn có thể cung cấp thông tin thời tiết và đưa ra lời khuyên dựa trên thời tiết khi được hỏi.",
        f"Hôm nay là {datetime.datetime.now().strftime('%A, %d/%m/%Y')}.",
        "\n**Các Công Cụ Có Sẵn:**",
        "Bạn có thể sử dụng các công cụ sau khi cần thiết để thực hiện yêu cầu của người dùng:",
        "- `add_family_member`: Để thêm thành viên mới.",
        "- `update_preference`: Để cập nhật sở thích cho thành viên đã biết.",
        "- `add_event`: Để thêm sự kiện mới. Hãy cung cấp mô tả ngày theo lời người dùng (ví dụ: 'ngày mai', 'thứ 6 tuần sau') vào `date_description`, hệ thống sẽ tính ngày chính xác. Bao gồm mô tả lặp lại (ví dụ 'hàng tuần') trong `description` nếu có.",
        "**QUAN TRỌNG VỀ LẶP LẠI:** Chỉ bao gồm mô tả sự lặp lại (ví dụ 'hàng tuần', 'mỗi tháng') trong trường `description` **KHI VÀ CHỈ KHI** người dùng **nêu rõ ràng** ý muốn lặp lại. Nếu người dùng chỉ nói một ngày cụ thể (ví dụ 'thứ 3 tới'), thì **KHÔNG được tự ý thêm** 'hàng tuần' hay bất kỳ từ lặp lại nào vào `description`; sự kiện đó là MỘT LẦN (ONCE)."
        "- `update_event`: Để sửa sự kiện. Cung cấp `event_id` và các trường cần thay đổi. Tương tự `add_event` về cách xử lý ngày (`date_description`) và lặp lại (`description`).",
        "**QUAN TRỌNG VỀ LẶP LẠI:** Nếu cập nhật `description`, chỉ đưa thông tin lặp lại vào đó nếu người dùng **nêu rõ ràng**. Nếu người dùng chỉ thay đổi sang một ngày cụ thể, **KHÔNG tự ý** thêm thông tin lặp lại."
        "- `delete_event`: Để xóa sự kiện.",
        "- `add_note`: Để tạo ghi chú mới.",
        "\n**QUY TẮC QUAN TRỌNG:**",
        "1.  **Chủ động sử dụng công cụ:** Khi người dùng yêu cầu rõ ràng (thêm, sửa, xóa, tạo...), hãy sử dụng công cụ tương ứng.",
        "2.  **Xử lý ngày/giờ:** KHÔNG tự tính toán ngày YYYY-MM-DD. Hãy gửi mô tả ngày của người dùng (ví dụ 'ngày mai', '20/7', 'thứ 3 tới') trong trường `date_description` của công cụ `add_event` hoặc `update_event`. Nếu sự kiện lặp lại, hãy nêu rõ trong trường `description` (ví dụ 'học tiếng Anh thứ 6 hàng tuần').",
        "3.  **Tìm kiếm và thời tiết:** Sử dụng thông tin tìm kiếm và thời tiết được cung cấp trong context (đánh dấu bằng --- THÔNG TIN ---) để trả lời các câu hỏi liên quan. Đừng gọi công cụ nếu thông tin đã có sẵn.",
        "4.  **Phân tích hình ảnh:** Khi nhận được hình ảnh, hãy mô tả nó và liên kết với thông tin gia đình nếu phù hợp.",
        "5.  **Xác nhận:** Sau khi sử dụng công cụ thành công (nhận được kết quả từ 'tool role'), hãy thông báo ngắn gọn cho người dùng biết hành động đã được thực hiện dựa trên kết quả đó. Nếu tool thất bại, hãy thông báo lỗi một cách lịch sự.",
        "6. **Độ dài phản hồi:** Giữ phản hồi cuối cùng cho người dùng tương đối ngắn gọn và tập trung vào yêu cầu chính, trừ khi được yêu cầu chi tiết.",
        "7. **Thời tiết:** Khi được hỏi về thời tiết hoặc lời khuyên liên quan đến thời tiết, sử dụng thông tin thời tiết được cung cấp để trả lời một cách chính xác và hữu ích."
    ]

    # Add current user context
    member_context = ""
    if current_member_id and current_member_id in family_data:
        current_member = family_data[current_member_id]
        member_context = f"""
        \n**Thông Tin Người Dùng Hiện Tại:**
        - ID: {current_member_id}
        - Tên: {current_member.get('name')}
        - Tuổi: {current_member.get('age', 'Chưa biết')}
        - Sở thích: {json.dumps(current_member.get('preferences', {}), ensure_ascii=False)}
        (Hãy cá nhân hóa tương tác và ghi nhận hành động dưới tên người dùng này. Sử dụng ID '{current_member_id}' khi cần `member_id`.)
        """
        system_prompt_parts.append(member_context)
    else:
         system_prompt_parts.append("\n(Hiện tại đang tương tác với khách.)")


    # Add data context
    recent_events_summary = {}
    try:
         sorted_event_ids = sorted(
             events_data.keys(),
             key=lambda eid: events_data[eid].get("created_on", ""),
             reverse=True
         )
         for eid in sorted_event_ids[:3]:
              event = events_data[eid]
              recent_events_summary[eid] = f"{event.get('title')} ({event.get('date')})"
    except Exception as sort_err:
         logger.error(f"Error summarizing recent events: {sort_err}")
         recent_events_summary = {"error": "Không thể tóm tắt"}

    data_context = f"""
    \n**Dữ Liệu Hiện Tại (Tóm tắt):**
    *   Thành viên (IDs): {json.dumps(list(family_data.keys()), ensure_ascii=False)}
    *   Sự kiện gần đây (IDs & Titles): {json.dumps(recent_events_summary, ensure_ascii=False)} (Tổng cộng: {len(events_data)})
    *   Ghi chú (Tổng cộng): {len(notes_data)}
    (Sử dụng ID sự kiện từ tóm tắt này khi cần `event_id` cho việc cập nhật hoặc xóa.)
    """
    system_prompt_parts.append(data_context)

    return "\n".join(system_prompt_parts)


# --- Search & Summarize Helpers ---
async def check_search_need(messages: List[Dict], openai_api_key: str, tavily_api_key: str, lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    """Kiểm tra nhu cầu tìm kiếm từ tin nhắn cuối của người dùng."""
    if not tavily_api_key and not OPENWEATHERMAP_API_KEY: 
        return ""  # Need Tavily for web search or OpenWeatherMap for weather

    last_user_message_content = None
    for message in reversed(messages):
        if message["role"] == "user":
            last_user_message_content = message["content"]
            break

    if not last_user_message_content: 
        return ""

    last_user_text = ""
    if isinstance(last_user_message_content, str):
        last_user_text = last_user_message_content
    elif isinstance(last_user_message_content, list):
        for item in last_user_message_content:
             if isinstance(item, dict) and item.get("type") == "text":
                  last_user_text = item.get("text", "")
                  break

    if not last_user_text: 
        return ""

    logger.info(f"Checking search need for: '{last_user_text[:100]}...'")

    # Check for Weather Advice Query (new)
    if OPENWEATHERMAP_API_KEY:
        is_advice_query, advice_type, location, date_description = await WeatherAdvisor.detect_weather_advice_need(
            last_user_text, openai_api_key
        )
        
        if is_advice_query:
            logger.info(f"Phát hiện truy vấn tư vấn thời tiết: type={advice_type}, location={location}, date={date_description}")
            weather_service = WeatherService(OPENWEATHERMAP_API_KEY)
            
            # Lấy dữ liệu thời tiết
            if date_description:
                # Sử dụng DateTimeHandler để phân tích ngày
                target_date = DateTimeHandler.parse_date(date_description)
                
                current_weather, forecast, target_date, date_text = await WeatherQueryParser.get_forecast_for_specific_date(
                    weather_service, location, date_description, lat, lon
                )
                
                if current_weather and forecast:
                    # Kết hợp lời khuyên dựa trên loại truy vấn và dữ liệu thời tiết
                    advice_data = WeatherAdvisor.combine_advice(
                        {"current": current_weather.get("current"), "forecast": forecast.get("forecast")}, 
                        target_date,
                        advice_type
                    )
                    # Định dạng lời khuyên để đưa vào prompt
                    advice_text = WeatherAdvisor.format_advice_for_prompt(advice_data, advice_type)
                    
                    advice_prompt_addition = f"""
                    \n\n--- TƯ VẤN THỜI TIẾT (DÙNG ĐỂ TRẢ LỜI) ---
                    Người dùng hỏi: "{last_user_text}"
                    
                    {advice_text}
                    --- KẾT THÚC TƯ VẤN THỜI TIẾT ---
                    
                    Hãy sử dụng thông tin tư vấn trên để trả lời câu hỏi của người dùng một cách tự nhiên và hữu ích.
                    Đưa ra lời khuyên chi tiết, cụ thể và phù hợp với tình hình thời tiết hiện tại/dự báo.
                    """
                    return advice_prompt_addition
            else:
                # Sử dụng thời tiết hiện tại
                if location and location.lower() not in ["hanoi", "hà nội"]:
                    weather_data = await weather_service.get_current_weather(location=location)
                    forecast_data = await weather_service.get_forecast(location=location, days=3)
                elif lat is not None and lon is not None:
                    weather_data = await weather_service.get_current_weather(lat=lat, lon=lon)
                    forecast_data = await weather_service.get_forecast(lat=lat, lon=lon, days=3)
                else:
                    weather_data = await weather_service.get_current_weather(location="Hanoi")
                    forecast_data = await weather_service.get_forecast(location="Hanoi", days=3)
                
                if weather_data:
                    # Kết hợp lời khuyên dựa trên loại truy vấn và dữ liệu thời tiết
                    advice_data = WeatherAdvisor.combine_advice(
                        {"current": weather_data.get("current"), "forecast": forecast_data.get("forecast")}, 
                        None,
                        advice_type
                    )
                    # Định dạng lời khuyên để đưa vào prompt
                    advice_text = WeatherAdvisor.format_advice_for_prompt(advice_data, advice_type)
                    
                    advice_prompt_addition = f"""
                    \n\n--- TƯ VẤN THỜI TIẾT (DÙNG ĐỂ TRẢ LỜI) ---
                    Người dùng hỏi: "{last_user_text}"
                    
                    {advice_text}
                    --- KẾT THÚC TƯ VẤN THỜI TIẾT ---
                    
                    Hãy sử dụng thông tin tư vấn trên để trả lời câu hỏi của người dùng một cách tự nhiên và hữu ích.
                    Đưa ra lời khuyên chi tiết, cụ thể và phù hợp với tình hình thời tiết hiện tại/dự báo.
                    """
                    return advice_prompt_addition
    
    # Check for Weather Query
    is_weather_query, location, date_description = await WeatherQueryParser.parse_weather_query(last_user_text, openai_api_key)
    
    if is_weather_query and OPENWEATHERMAP_API_KEY:
        logger.info(f"Phát hiện truy vấn thời tiết cho địa điểm: '{location}', thời gian: '{date_description}'")
        weather_service = WeatherService(OPENWEATHERMAP_API_KEY)
        
        # Xử lý truy vấn có cả địa điểm và thời gian (dùng DateTimeHandler)
        if date_description:
            current_weather, forecast, target_date, date_text = await WeatherQueryParser.get_forecast_for_specific_date(
                weather_service, location, date_description, lat, lon
            )
            
            if current_weather and forecast and target_date:
                weather_info = WeatherQueryParser.format_weather_for_date(
                    current_weather, forecast, target_date, date_text
                )
                logger.info(f"Đã lấy thông tin thời tiết cho '{location}' vào ngày {date_text}")
            else:
                logger.warning(f"Không thể lấy thông tin thời tiết cho '{location}' vào '{date_description}'")
                weather_info = format_weather_for_prompt(current_weather, forecast)
        else:
            # Xử lý truy vấn chỉ có địa điểm (không có thời gian cụ thể - trả về thời tiết hiện tại)
            if location and location.lower() not in ["hanoi", "hà nội"]:
                logger.info(f"Sử dụng địa điểm từ câu hỏi: {location}")
                weather_data = await weather_service.get_current_weather(location=location)
                forecast_data = await weather_service.get_forecast(location=location, days=3)
            elif lat is not None and lon is not None:
                logger.info(f"Sử dụng tọa độ: lat={lat}, lon={lon}")
                weather_data = await weather_service.get_current_weather(lat=lat, lon=lon)
                forecast_data = await weather_service.get_forecast(lat=lat, lon=lon, days=3)
            else:
                logger.info("Không có địa điểm và tọa độ, sử dụng mặc định Hà Nội")
                weather_data = await weather_service.get_current_weather(location="Hanoi")
                forecast_data = await weather_service.get_forecast(location="Hanoi", days=3)
                
            if not weather_data:
                return "\n\n--- LỖI THỜI TIẾT: Không thể lấy thông tin thời tiết. Hãy báo lại cho người dùng. ---"
                
            weather_info = format_weather_for_prompt(weather_data, forecast_data)
            
        weather_prompt_addition = f"""
        \n\n--- THÔNG TIN THỜI TIẾT (DÙNG ĐỂ TRẢ LỜI) ---
        Người dùng hỏi: "{last_user_text}"
        {weather_info}
        --- KẾT THÚC THÔNG TIN THỜI TIẾT ---
        Hãy sử dụng thông tin thời tiết này để trả lời câu hỏi của người dùng một cách tự nhiên.
        Đưa ra lời khuyên phù hợp với điều kiện thời tiết nếu người dùng hỏi về việc nên mặc gì, nên đi đâu, nên làm gì, v.v.
        """
        return weather_prompt_addition

    # Check for General Search Intent - Phần còn lại giữ nguyên
    if tavily_api_key:
         need_search, search_query, is_news_query = await detect_search_intent(last_user_text, openai_api_key)
         if need_search:
             logger.info(f"Phát hiện nhu cầu tìm kiếm: query='{search_query}', is_news={is_news_query}")
             domains_to_include = VIETNAMESE_NEWS_DOMAINS if is_news_query else None
             try:
                search_summary = await search_and_summarize(
                    tavily_api_key, search_query, openai_api_key, include_domains=domains_to_include
                )
                search_prompt_addition = f"""
                \n\n--- THÔNG TIN TÌM KIẾM (DÙNG ĐỂ TRẢ LỜI) ---
                Người dùng hỏi: "{last_user_text}"
                Kết quả tìm kiếm và tóm tắt cho truy vấn '{search_query}':
                {search_summary}
                --- KẾT THÚC THÔNG TIN TÌM KIẾM ---
                Hãy sử dụng kết quả tóm tắt này để trả lời câu hỏi của người dùng một cách tự nhiên, trích dẫn nguồn nếu có.
                """
                return search_prompt_addition
             except Exception as search_err:
                  logger.error(f"Lỗi khi tìm kiếm/tóm tắt cho '{search_query}': {search_err}", exc_info=True)
                  return "\n\n--- LỖI TÌM KIẾM: Không thể lấy thông tin. Hãy báo lại cho người dùng. ---"

    # No search need detected
    logger.info("Không phát hiện nhu cầu tìm kiếm đặc biệt.")
    return ""

async def tavily_extract(api_key, urls, include_images=False, extract_depth="advanced"):
    """Trích xuất nội dung từ URL (Wrap sync call)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"urls": urls, "include_images": include_images, "extract_depth": extract_depth}
    try:
        response = await asyncio.to_thread(
             requests.post, "https://api.tavily.com/extract", headers=headers, json=data, timeout=15
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Lỗi Tavily Extract API ({e.__class__.__name__}): {e}")
        return None
    except Exception as e:
         logger.error(f"Lỗi không xác định trong tavily_extract: {e}", exc_info=True)
         return None


async def tavily_search(api_key, query, search_depth="advanced", max_results=5, include_domains=None, exclude_domains=None):
    """Tìm kiếm Tavily (Wrap sync call)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"query": query, "search_depth": search_depth, "max_results": max_results}
    if include_domains: data["include_domains"] = include_domains
    if exclude_domains: data["exclude_domains"] = exclude_domains
    try:
        response = await asyncio.to_thread(
            requests.post, "https://api.tavily.com/search", headers=headers, json=data, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Lỗi Tavily Search API ({e.__class__.__name__}): {e}")
        return None
    except Exception as e:
         logger.error(f"Lỗi không xác định trong tavily_search: {e}", exc_info=True)
         return None


async def search_and_summarize(tavily_api_key, query, openai_api_key, include_domains=None):
    """Tìm kiếm và tổng hợp."""
    if not tavily_api_key or not openai_api_key or not query:
        return "Thiếu thông tin API key hoặc câu truy vấn."

    try:
        logger.info(f"Bắt đầu tìm kiếm Tavily cho: '{query}'" + (f" (Domains: {include_domains})" if include_domains else ""))
        search_results = await tavily_search(
            tavily_api_key, query, include_domains=include_domains, max_results=5
        )

        if not search_results or not search_results.get("results"):
            logger.warning(f"Không tìm thấy kết quả Tavily cho '{query}'")
            return f"Xin lỗi, tôi không tìm thấy kết quả nào cho '{query}'" + (f" trong các trang tin tức được chỉ định." if include_domains else ".")

        urls_to_extract = [result["url"] for result in search_results["results"][:3]]
        if not urls_to_extract:
            logger.warning(f"Không có URL nào để trích xuất từ kết quả Tavily cho '{query}'.")
            return f"Đã tìm thấy một số tiêu đề liên quan đến '{query}' nhưng không thể trích xuất nội dung."

        logger.info(f"Trích xuất nội dung từ URLs: {urls_to_extract}")
        extract_result = await tavily_extract(tavily_api_key, urls_to_extract)

        extracted_contents = []
        if extract_result and extract_result.get("results"):
             for res in extract_result["results"]:
                  content = res.get("raw_content", "")
                  if content:
                       max_len_per_source = 4000
                       content = content[:max_len_per_source] + "..." if len(content) > max_len_per_source else content
                       extracted_contents.append({"url": res.get("url"), "content": content})
                  else:
                       logger.warning(f"Nội dung trống rỗng từ URL: {res.get('url')}")
        else:
             logger.warning(f"Trích xuất nội dung thất bại từ Tavily cho URLs: {urls_to_extract}")
             basic_info = ""
             for res in search_results.get("results", [])[:3]:
                 basic_info += f"- Tiêu đề: {res.get('title', '')}\n URL: {res.get('url')}\n\n"
             if basic_info:
                  return f"Không thể trích xuất chi tiết nội dung, nhưng đây là một số kết quả tìm thấy:\n{basic_info}"
             else:
                  return f"Không thể trích xuất nội dung từ các kết quả tìm kiếm cho '{query}'."

        if not extracted_contents:
             logger.warning(f"Không có nội dung nào được trích xuất thành công cho '{query}'.")
             return f"Không thể trích xuất nội dung chi tiết cho '{query}'."

        logger.info(f"Tổng hợp {len(extracted_contents)} nguồn trích xuất cho '{query}'.")
        client = OpenAI(api_key=openai_api_key)

        content_for_prompt = ""
        total_len = 0
        max_total_len = 15000
        for item in extracted_contents:
             source_text = f"\n--- Nguồn: {item['url']} ---\n{item['content']}\n--- Hết nguồn ---\n"
             if total_len + len(source_text) > max_total_len:
                  logger.warning(f"Đã đạt giới hạn độ dài context khi tổng hợp, bỏ qua các nguồn sau.")
                  break
             content_for_prompt += source_text
             total_len += len(source_text)

        prompt = f"""
        Dưới đây là nội dung trích xuất từ các trang web liên quan đến câu hỏi: "{query}"

        {content_for_prompt}

        Nhiệm vụ của bạn:
        1.  **Tổng hợp thông tin chính:** Phân tích và tổng hợp các thông tin quan trọng nhất từ các nguồn trên để trả lời cho câu hỏi "{query}".
        2.  **Tập trung vào ngày cụ thể (nếu có):** Nếu câu hỏi đề cập ngày cụ thể, ưu tiên thông tin ngày đó.
        3.  **Trình bày rõ ràng:** Viết một bản tóm tắt mạch lạc, có cấu trúc bằng tiếng Việt.
        4.  **Xử lý mâu thuẫn:** Nếu có thông tin trái ngược, hãy nêu rõ.
        5.  **Nêu nguồn:** Cố gắng trích dẫn nguồn (URL) cho các thông tin quan trọng nếu có thể, ví dụ: "(Nguồn: [URL])".
        6.  **Phạm vi:** Chỉ sử dụng thông tin từ các nguồn được cung cấp. Không thêm kiến thức ngoài.
        7.  **Định dạng:** Sử dụng HTML đơn giản (p, b, ul, li).

        Hãy bắt đầu bản tóm tắt của bạn.
        """

        try:
            response = await asyncio.to_thread(
                 client.chat.completions.create,
                 model=openai_model,
                 messages=[
                     {"role": "system", "content": "Bạn là một trợ lý tổng hợp thông tin chuyên nghiệp. Nhiệm vụ của bạn là tổng hợp nội dung từ các nguồn được cung cấp để tạo ra một bản tóm tắt chính xác, tập trung vào yêu cầu của người dùng và trích dẫn nguồn nếu có thể."},
                     {"role": "user", "content": prompt}
                 ],
                 temperature=0.3,
                 max_tokens=1500
            )
            summarized_info = response.choices[0].message.content
            return summarized_info.strip()

        except Exception as summary_err:
             logger.error(f"Lỗi khi gọi OpenAI để tổng hợp: {summary_err}", exc_info=True)
             return "Xin lỗi, tôi gặp lỗi khi đang tóm tắt thông tin tìm kiếm."

    except Exception as e:
        logger.error(f"Lỗi trong quá trình tìm kiếm và tổng hợp cho '{query}': {e}", exc_info=True)
        return f"Có lỗi xảy ra trong quá trình tìm kiếm và tổng hợp thông tin: {str(e)}"


async def detect_search_intent(query, api_key):
    """Phát hiện ý định tìm kiếm (async wrapper)."""
    if not api_key or not query: return False, query, False

    try:
        client = OpenAI(api_key=api_key)
        current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        system_prompt = f"""
Bạn là một hệ thống phân loại và tinh chỉnh câu hỏi thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có cần tìm kiếm thông tin thực tế, tin tức mới hoặc dữ liệu cập nhật không (`need_search`). Câu hỏi về kiến thức chung, định nghĩa đơn giản thường không cần tìm kiếm.
2. Nếu cần tìm kiếm, hãy tinh chỉnh câu hỏi thành một truy vấn tìm kiếm tối ưu (`search_query`), bao gồm yếu tố thời gian nếu có (hôm nay, 26/03...).
3. Xác định xem câu hỏi có chủ yếu về tin tức, thời sự, thể thao, sự kiện hiện tại không (`is_news_query`). Câu hỏi về giá cả, sản phẩm, hướng dẫn KHÔNG phải là tin tức.

Hôm nay là ngày: {current_date_str}.

CHÚ Ý QUAN TRỌNG: Các câu hỏi về thời tiết (ví dụ: "thời tiết ở Hà Nội", "trời có mưa không") hoặc yêu cầu tư vấn dựa trên thời tiết (ví dụ: "nên mặc gì hôm nay") KHÔNG cần tìm kiếm (`need_search` = false).

Ví dụ:
- User: "tin tức covid hôm nay" -> {{ "need_search": true, "search_query": "tin tức covid mới nhất ngày {current_date_str}", "is_news_query": true }}
- User: "kết quả trận MU tối qua" -> {{ "need_search": true, "search_query": "kết quả Manchester United tối qua", "is_news_query": true }}
- User: "có phim gì hay tuần này?" -> {{ "need_search": true, "search_query": "phim chiếu rạp hay tuần này", "is_news_query": false }}
- User: "giá vàng SJC" -> {{ "need_search": true, "search_query": "giá vàng SJC mới nhất", "is_news_query": false }}
- User: "thủ đô nước Pháp là gì?" -> {{ "need_search": false, "search_query": "thủ đô nước Pháp là gì?", "is_news_query": false }}
- User: "thời tiết Hà Nội ngày mai" -> {{ "need_search": false, "search_query": "dự báo thời tiết Hà Nội ngày mai", "is_news_query": true }}
- User: "cách làm bánh cuốn" -> {{ "need_search": true, "search_query": "cách làm bánh cuốn ngon", "is_news_query": false }}
- User: "sin(pi/2) bằng mấy?" -> {{ "need_search": false, "search_query": "sin(pi/2)", "is_news_query": false }}

Trả lời DƯỚI DẠNG JSON HỢP LỆ với 3 trường: need_search (boolean), search_query (string), is_news_query (boolean).
"""
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model=openai_model,
             messages=[
                 {"role": "system", "content": system_prompt},
                 {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\""}
             ],
             temperature=0.1,
             max_tokens=150,
             response_format={"type": "json_object"}
        )

        result_str = response.choices[0].message.content
        logger.info(f"Kết quả detect_search_intent (raw): {result_str}")

        try:
            result = json.loads(result_str)
            need_search = result.get("need_search", False)
            search_query = query
            is_news_query = False

            # Ensure weather-related queries are explicitly marked as need_search=false
            weather_keywords_for_detection = ["thời tiết", "dự báo", "nhiệt độ", "nắng", "mưa", "gió", "mấy độ", "bao nhiêu độ", "mặc gì", "nên đi"]
            if any(keyword in query.lower() for keyword in weather_keywords_for_detection):
                 need_search = False
                 logger.info(f"Detected potential weather query '{query}', overriding need_search to False.")


            if need_search:
                search_query = result.get("search_query", query)
                if not search_query: search_query = query
                is_news_query = result.get("is_news_query", False)

            logger.info(f"Phân tích truy vấn '{query}': need_search={need_search}, search_query='{search_query}', is_news_query={is_news_query}")
            return need_search, search_query, is_news_query

        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Lỗi giải mã JSON từ detect_search_intent: {e}. Raw: {result_str}")
            return False, query, False
    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_search_intent: {e}", exc_info=True)
        return False, query, False

# --- Suggested Questions ---
def generate_dynamic_suggested_questions(api_key, member_id=None, max_questions=5):
    """Tạo câu hỏi gợi ý động (sử dụng mẫu câu)."""
    logger.info("Sử dụng phương pháp mẫu câu để tạo câu hỏi gợi ý")

    random_seed = int(hashlib.md5(f"{datetime.datetime.now().strftime('%Y-%m-%d_%H')}_{member_id or 'guest'}".encode()).hexdigest(), 16)
    random.seed(random_seed)

    question_templates = {
        "news": [ "Tin tức {topic} mới nhất?", "Có gì mới về {topic} hôm nay?", "Điểm tin {topic} sáng nay?", "Cập nhật tình hình {topic}?" ],
        # Removed "weather" category
        "events": [ "Sự kiện nổi bật tuần này?", "Lịch chiếu phim {cinema}?", "Trận đấu {team} tối nay mấy giờ?", "Có hoạt động gì thú vị cuối tuần?" ],
        "food": [ "Công thức nấu món {dish}?", "Quán {dish} ngon ở {district}?", "Cách làm {dessert} đơn giản?" ],
        "hobbies": [ "Sách hay về chủ đề {genre}?", "Mẹo chụp ảnh đẹp bằng điện thoại?", "Bài tập yoga giảm căng thẳng?" ],
        "general": [ "Kể một câu chuyện cười?", "Đố vui về {category}?", "Hôm nay có ngày gì đặc biệt?", "Cho tôi một lời khuyên ngẫu nhiên?", "Ý tưởng làm gì khi rảnh?" ]
    }

    prefs = {}
    if member_id and member_id in family_data:
         prefs = family_data[member_id].get("preferences", {})

    replacements = {
        "topic": ["thế giới", "kinh tế", "thể thao", "giải trí", "công nghệ", "giáo dục", "y tế", prefs.get("hobby", "khoa học")],
        # Removed "location" as it was mainly for weather
        "cinema": ["CGV", "Lotte", "BHD", "Galaxy"],
        "team": [prefs.get("team", "Việt Nam"), "Man City", "Real Madrid", "Arsenal"],
        "dish": [prefs.get("food", "phở"), "bún chả", "cơm tấm", "pizza", "sushi"],
        "district": ["quận 1", "Hoàn Kiếm", "Hải Châu", "gần đây"],
        "dessert": ["chè", "bánh flan", "rau câu"],
        "genre": [prefs.get("book_genre", "trinh thám"), "lịch sử", "khoa học viễn tưởng", "tâm lý"],
        "category": ["động vật", "lịch sử", "khoa học", "phim ảnh"]
    }

    all_questions = []
    categories = list(question_templates.keys())
    random.shuffle(categories)

    for category in categories:
        template = random.choice(question_templates[category])
        question = template
        placeholder_match = re.search(r'\{(\w+)\}', question)
        while placeholder_match:
             key = placeholder_match.group(1)
             if key in replacements:
                  replacement = random.choice(replacements[key])
                  question = question.replace(placeholder_match.group(0), replacement, 1)
             else:
                  question = question.replace(placeholder_match.group(0), "...", 1)
             placeholder_match = re.search(r'\{(\w+)\}', question)
        all_questions.append(question)

    final_suggestions = []
    seen_suggestions = set()
    for q in all_questions:
         if len(final_suggestions) >= max_questions: break
         if q not in seen_suggestions:
              final_suggestions.append(q)
              seen_suggestions.add(q)

    while len(final_suggestions) < max_questions and len(final_suggestions) < len(all_questions):
         q = random.choice(all_questions)
         if q not in seen_suggestions:
              final_suggestions.append(q)
              seen_suggestions.add(q)

    logger.info(f"Đã tạo {len(final_suggestions)} câu hỏi gợi ý bằng mẫu.")
    return final_suggestions


# --- Chat History ---
async def generate_chat_summary(messages, api_key):
    """Tạo tóm tắt từ lịch sử trò chuyện (async wrapper)."""
    if not api_key or not messages or len(messages) < 2:
        return "Chưa đủ nội dung để tóm tắt."

    conversation_text = ""
    for msg in messages[-10:]:
        role = msg.get("role")
        content = msg.get("content")
        text_content = ""
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
             for item in content:
                  if isinstance(item, dict) and item.get("type") == "text":
                       text_content += item.get("text", "") + " "
        elif role == "tool":
             text_content = f"[Tool {msg.get('name')} result: {str(content)[:50]}...]"

        if role and text_content:
             conversation_text += f"{role.capitalize()}: {text_content.strip()}\n"

    if not conversation_text: return "Không có nội dung text để tóm tắt."


    try:
        client = OpenAI(api_key=api_key)
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model=openai_model,
             messages=[
                 {"role": "system", "content": "Tóm tắt cuộc trò chuyện sau thành 1 câu ngắn gọn bằng tiếng Việt, nêu bật yêu cầu chính hoặc kết quả cuối cùng."},
                 {"role": "user", "content": conversation_text}
             ],
             temperature=0.2,
             max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Lỗi khi tạo tóm tắt chat: {e}", exc_info=True)
        return "[Lỗi tóm tắt]"


def save_chat_history(member_id, messages, summary=None, session_id=None):
    """Lưu lịch sử chat cho member_id."""
    global chat_history
    if not member_id: return

    if member_id not in chat_history or not isinstance(chat_history[member_id], list):
        chat_history[member_id] = []

    history_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": messages,
        "summary": summary or "",
        "session_id": session_id
    }

    chat_history[member_id].insert(0, history_entry)

    max_history_per_member = 20
    if len(chat_history[member_id]) > max_history_per_member:
        chat_history[member_id] = chat_history[member_id][:max_history_per_member]

    if not save_data(CHAT_HISTORY_FILE, chat_history):
        logger.error(f"Lưu lịch sử chat cho member {member_id} thất bại.")


# --- Text to Speech ---
def text_to_speech_google(text, lang='vi', slow=False, max_length=5000):
    """Chuyển text thành audio base64 dùng gTTS."""
    try:
        clean_text = re.sub(r'<[^>]*>', ' ', text)
        clean_text = unescape(clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()

        if not clean_text:
             logger.warning("TTS: Văn bản rỗng sau khi làm sạch.")
             return None

        if len(clean_text) > max_length:
            logger.warning(f"TTS: Văn bản quá dài ({len(clean_text)}), cắt ngắn còn {max_length}.")
            cut_pos = clean_text.rfind('.', 0, max_length)
            if cut_pos == -1: cut_pos = clean_text.rfind('?', 0, max_length)
            if cut_pos == -1: cut_pos = clean_text.rfind('!', 0, max_length)
            if cut_pos == -1 or cut_pos < max_length // 2: cut_pos = max_length
            clean_text = clean_text[:cut_pos+1]

        audio_buffer = BytesIO()
        tts = gTTS(text=clean_text, lang=lang, slow=slow)
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        audio_data = audio_buffer.read()
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        return audio_base64

    except Exception as e:
        logger.error(f"Lỗi khi sử dụng Google TTS: {e}", exc_info=True)
        return None

# --- Image Processing ---
def get_image_base64(image_raw: Image.Image) -> Optional[str]:
    """Chuyển đối tượng PIL Image sang base64 data URL."""
    try:
        buffered = BytesIO()
        img_format = image_raw.format if image_raw.format else "JPEG"
        if img_format not in ["JPEG", "PNG", "GIF", "WEBP"]:
             logger.warning(f"Định dạng ảnh không được hỗ trợ trực tiếp '{img_format}', chuyển đổi sang JPEG.")
             img_format = "JPEG"
             if image_raw.mode != "RGB":
                 image_raw = image_raw.convert("RGB")

        image_raw.save(buffered, format=img_format)
        img_byte = buffered.getvalue()
        mime_type = f"image/{img_format.lower()}"
        base64_str = base64.b64encode(img_byte).decode('utf-8')
        return f"data:{mime_type};base64,{base64_str}"
    except Exception as e:
         logger.error(f"Lỗi chuyển đổi ảnh sang base64: {e}", exc_info=True)
         return None


# --- Event Filtering ---
def filter_events_by_member(member_id=None):
    """Lọc sự kiện theo thành viên (người tạo hoặc tham gia)."""
    if not member_id: return events_data

    filtered = {}
    member_name = family_data.get(member_id, {}).get("name") if member_id in family_data else None

    for event_id, event in events_data.items():
        is_creator = event.get("created_by") == member_id
        is_participant = member_name and (member_name in event.get("participants", []))

        if is_creator or is_participant:
            filtered[event_id] = event
    return filtered


# ------- Remaining API Endpoints --------

# --- GET / ---
@app.get("/")
async def root():
    return {
        "name": "Trợ lý Gia đình API (Tool Calling)", "version": "1.1.0-no_weather",
        "description": "API cho ứng dụng Trợ lý Gia đình thông minh (không bao gồm thời tiết)",
        "endpoints": ["/chat", "/chat/stream", "/suggested_questions", "/family_members", "/events", "/notes", "/search", "/session", "/analyze_image", "/transcribe_audio", "/tts", "/chat_history/{member_id}"] # Removed /weather
    }

# --- Family Members ---
@app.get("/family_members")
async def get_family_members():
    return family_data

@app.post("/family_members")
async def add_family_member_endpoint(member: MemberModel):
    """Thêm thành viên (qua endpoint trực tiếp)."""
    details = member.dict()
    if add_family_member(details):
         new_member_id = None
         for mid, mdata in family_data.items():
              if mdata.get("name") == member.name and mdata.get("age") == member.age:
                   new_member_id = mid
                   break
         if new_member_id:
             return {"id": new_member_id, "member": family_data[new_member_id]}
         else:
             raise HTTPException(status_code=500, detail="Thêm thành công nhưng không tìm thấy ID.")
    else:
        raise HTTPException(status_code=500, detail="Không thể thêm thành viên.")


# --- Events ---
@app.get("/events")
async def get_events(member_id: Optional[str] = None):
    if member_id:
        return filter_events_by_member(member_id)
    return events_data

@app.post("/events")
async def add_event_endpoint(event: EventModel, member_id: Optional[str] = None):
    """Thêm sự kiện (qua endpoint trực tiếp)."""
    details = event.dict()
    details["created_by"] = member_id
    details["repeat_type"] = determine_repeat_type(details.get("description"), details.get("title"))

    if add_event(details):
         new_event_id = None
         for eid, edata in events_data.items():
              if (edata.get("title") == event.title and
                  edata.get("date") == event.date and
                  edata.get("created_by") == member_id):
                   new_event_id = eid
                   break
         if new_event_id:
              return {"id": new_event_id, "event": events_data[new_event_id]}
         else:
              raise HTTPException(status_code=500, detail="Thêm sự kiện thành công nhưng không tìm thấy ID.")

    else:
        raise HTTPException(status_code=500, detail="Không thể thêm sự kiện.")


# --- Notes ---
@app.get("/notes")
async def get_notes(member_id: Optional[str] = None):
    if member_id:
        return {note_id: note for note_id, note in notes_data.items()
                if note.get("created_by") == member_id}
    return notes_data

@app.post("/notes")
async def add_note_endpoint(note: NoteModel, member_id: Optional[str] = None):
    """Thêm ghi chú (qua endpoint trực tiếp)."""
    details = note.dict()
    details["created_by"] = member_id
    if add_note(details):
         new_note_id = None
         for nid, ndata in notes_data.items():
              if (ndata.get("title") == note.title and
                  ndata.get("content") == note.content and
                  ndata.get("created_by") == member_id):
                   new_note_id = nid
                   break
         if new_note_id:
              return {"id": new_note_id, "note": notes_data[new_note_id]}
         else:
              raise HTTPException(status_code=500, detail="Thêm note thành công nhưng không tìm thấy ID.")
    else:
        raise HTTPException(status_code=500, detail="Không thể thêm ghi chú.")


# --- Search ---
@app.post("/search")
async def search_endpoint(search_request: SearchRequest):
    """Tìm kiếm thông tin thời gian thực."""
    if not search_request.tavily_api_key or not search_request.openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu API key cho tìm kiếm.")

    domains_to_include = VIETNAMESE_NEWS_DOMAINS if search_request.is_news_query else None
    try:
        result = await search_and_summarize(
            search_request.tavily_api_key,
            search_request.query,
            search_request.openai_api_key,
            include_domains=domains_to_include
        )
        return {"query": search_request.query, "result": result}
    except Exception as e:
         logger.error(f"Lỗi trong search_endpoint: {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"Lỗi tìm kiếm: {str(e)}")


# --- Session Management ---
@app.post("/session")
async def create_session():
    session_id = str(uuid.uuid4())
    session_manager.get_session(session_id) # Creates if not exists
    return {"session_id": session_id}

@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    if session_manager.delete_session(session_id):
        return {"status": "success", "message": f"Đã xóa session {session_id}"}
    raise HTTPException(status_code=404, detail="Session không tồn tại")

@app.get("/sessions")
async def list_sessions():
    sessions_info = {}
    for session_id, session_data in session_manager.sessions.items():
        sessions_info[session_id] = {
            "created_at": session_data.get("created_at"),
            "last_updated": session_data.get("last_updated"),
            "member_id": session_data.get("current_member"),
            "message_count": len(session_data.get("messages", [])),
        }
    sorted_sessions = sorted(sessions_info.items(), key=lambda item: item[1].get('last_updated', ''), reverse=True)
    return dict(sorted_sessions)


@app.delete("/cleanup_sessions")
async def cleanup_old_sessions_endpoint(days: int = 30):
    try:
         session_manager.cleanup_old_sessions(days_threshold=days)
         return {"status": "success", "message": f"Đã bắt đầu dọn dẹp sessions không hoạt động trên {days} ngày"}
    except Exception as e:
         logger.error(f"Lỗi khi dọn dẹp session: {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"Lỗi dọn dẹp session: {str(e)}")


# --- Suggested Questions ---
@app.get("/suggested_questions")
async def get_suggested_questions(
    session_id: str,
    member_id: Optional[str] = None,
    openai_api_key: Optional[str] = None
):
    """Lấy câu hỏi gợi ý."""
    api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
    session = session_manager.get_session(session_id)
    current_member_id = member_id or session.get("current_member")

    suggested_questions = generate_dynamic_suggested_questions(api_key, current_member_id, max_questions=5)
    current_timestamp = datetime.datetime.now().isoformat()

    session["suggested_question"] = suggested_questions
    session["question_timestamp"] = current_timestamp
    session_manager.update_session(session_id, session)

    return SuggestedQuestionsResponse(
        session_id=session_id,
        member_id=current_member_id,
        suggested_questions=suggested_questions,
        timestamp=current_timestamp
    )

@app.get("/cached_suggested_questions")
async def get_cached_suggested_questions(session_id: str):
    """Lấy câu hỏi gợi ý đã cache trong session."""
    session = session_manager.get_session(session_id)
    suggested = session.get("suggested_question", [])
    timestamp = session.get("question_timestamp", datetime.datetime.now().isoformat())
    return SuggestedQuestionsResponse(
        session_id=session_id,
        member_id=session.get("current_member"),
        suggested_questions=suggested,
        timestamp=timestamp
    )

# --- Chat History ---
@app.get("/chat_history/{member_id}")
async def get_member_chat_history(member_id: str):
    """Lấy lịch sử chat của một thành viên."""
    if member_id in chat_history:
        return chat_history[member_id][:10]
    return []

@app.get("/chat_history/session/{session_id}")
async def get_session_chat_history(session_id: str):
    """Lấy lịch sử chat theo session_id (có thể chậm)."""
    session_chats = []
    for member_id, histories in chat_history.items():
        for history in histories:
            if history.get("session_id") == session_id:
                history_with_member = history.copy()
                history_with_member["member_id"] = member_id
                if member_id in family_data:
                    history_with_member["member_name"] = family_data[member_id].get("name", "")
                session_chats.append(history_with_member)
    session_chats.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return session_chats


# --- Multimedia Endpoints ---
@app.post("/analyze_image")
async def analyze_image_endpoint(
    file: UploadFile = File(...),
    openai_api_key: str = Form(...),
    member_id: Optional[str] = Form(None),
    prompt: Optional[str] = Form("Mô tả chi tiết hình ảnh này bằng tiếng Việt."),
    content_type: str = Form("image")
):
    """Phân tích hình ảnh sử dụng OpenAI Vision."""
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File tải lên không phải là hình ảnh.")
    if not openai_api_key or "sk-" not in openai_api_key:
         raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ.")

    try:
        image_content = await file.read()
        img = Image.open(BytesIO(image_content))
        img_base64_url = get_image_base64(img)

        if not img_base64_url:
             raise HTTPException(status_code=500, detail="Không thể xử lý ảnh thành base64.")

        client = OpenAI(api_key=openai_api_key)
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model="gpt-4o-mini",
             messages=[
                 {"role": "system", "content": "Bạn là chuyên gia phân tích hình ảnh. Mô tả chi tiết, nếu là món ăn, nêu tên và gợi ý công thức/nguyên liệu. Nếu là hoạt động, mô tả hoạt động đó."},
                 {"role": "user", "content": [
                     {"type": "text", "text": prompt},
                     {"type": "image_url", "image_url": {"url": img_base64_url}}
                 ]}
             ],
             max_tokens=1000
        )

        analysis_text = response.choices[0].message.content
        audio_response = text_to_speech_google(analysis_text)

        return {
            "analysis": analysis_text,
            "member_id": member_id,
            "content_type": content_type,
            "audio_response": audio_response
        }

    except Exception as e:
        logger.error(f"Lỗi khi phân tích hình ảnh: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi khi phân tích hình ảnh: {str(e)}")

@app.post("/transcribe_audio")
async def transcribe_audio_endpoint(
    file: UploadFile = File(...),
    openai_api_key: str = Form(...)
):
    """Chuyển đổi audio thành text."""
    if not file.content_type.startswith("audio/"):
         logger.warning(f"Content-Type file audio không chuẩn: {file.content_type}. Vẫn thử xử lý.")
    if not openai_api_key or "sk-" not in openai_api_key:
         raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ.")

    temp_audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}_{file.filename}")
    try:
        audio_content = await file.read()
        with open(temp_audio_path, "wb") as f:
            f.write(audio_content)

        client = OpenAI(api_key=openai_api_key)
        with open(temp_audio_path, "rb") as audio_file_obj:
            transcript = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model="whisper-1",
                file=audio_file_obj
            )

        return {"text": transcript.text}

    except Exception as e:
        logger.error(f"Lỗi khi xử lý file audio: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý file audio: {str(e)}")
    finally:
        if os.path.exists(temp_audio_path):
            try: os.remove(temp_audio_path)
            except OSError: pass


@app.post("/tts")
async def text_to_speech_endpoint(
    text: str = Form(...),
    lang: str = Form(default="vi"),
    slow: bool = Form(default=False)
):
    """Chuyển đổi text thành audio base64 dùng gTTS."""
    try:
        if not text:
            raise HTTPException(status_code=400, detail="Thiếu nội dung văn bản.")

        audio_base64 = text_to_speech_google(text, lang, slow)
        if audio_base64:
            return {
                "audio_data": audio_base64,
                "format": "mp3",
                "lang": lang,
                "provider": "Google TTS"
            }
        else:
            raise HTTPException(status_code=500, detail="Không thể tạo file âm thanh.")
    except Exception as e:
        logger.error(f"Lỗi trong text_to_speech_endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý TTS: {str(e)}")

# Removed /weather/{location} endpoint

# ----- Server Startup/Shutdown Hooks -----
@app.on_event("startup")
async def startup_event():
    """Các tác vụ cần thực hiện khi khởi động server."""
    logger.info("Khởi động Family Assistant API server (Tool Calling, No Weather)")
    logger.info("Đã tải dữ liệu và sẵn sàng hoạt động.")

@app.on_event("shutdown")
async def shutdown_event():
    """Các tác vụ cần thực hiện khi đóng server."""
    logger.info("Đóng Family Assistant API server...")
    save_data(FAMILY_DATA_FILE, family_data)
    save_data(EVENTS_DATA_FILE, events_data)
    save_data(NOTES_DATA_FILE, notes_data)
    save_data(CHAT_HISTORY_FILE, chat_history)
    session_manager._save_sessions()
    logger.info("Đã lưu dữ liệu. Server tắt.")


# ----- Main Execution Block -----
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trợ lý Gia đình API (Tool Calling - No Weather)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Auto reload server on code changes")
    args = parser.parse_args()

    log_level = "debug" if args.reload else "info"

    logger.info(f"Khởi động Trợ lý Gia đình API (Tool Calling - No Weather) trên http://{args.host}:{args.port}")

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=log_level.lower()
    )
