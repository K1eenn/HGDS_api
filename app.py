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

# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập log
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler()])
# Use specific loggers per module/area if desired, otherwise a root logger is fine
logger = logging.getLogger('family_assistant_api')

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
#OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY") 
OPENWEATHERMAP_API_KEY = "94c94ebc644d803eef31af2f1d399bd2"
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


def add_event(details):
    """Thêm một sự kiện mới. Expects 'date' to be calculated YYYY-MM-DD."""
    global events_data
    try:
        event_id = str(uuid.uuid4())
        if not details.get('title') or 'date' not in details or details.get('date') is None:
            logger.error(f"Thiếu title hoặc date đã tính toán khi thêm sự kiện: {details}")
            return False

        events_data[event_id] = {
            "id": event_id,
            "title": details.get("title"),
            "date": details.get("date"),
            "time": details.get("time", "19:00"),
            "description": details.get("description", ""),
            "participants": details.get("participants", []),
            "repeat_type": details.get("repeat_type", "ONCE"),
            "created_by": details.get("created_by"),
            "created_on": datetime.datetime.now().isoformat()
        }
        if save_data(EVENTS_DATA_FILE, events_data):
             logger.info(f"Đã thêm sự kiện ID {event_id}: {details.get('title')}")
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
                if key == 'date' and not value:
                    logger.warning(f"Bỏ qua cập nhật date thành giá trị rỗng cho event ID {event_id_str}")
                    continue
                event_to_update[key] = value
                updated = True
                logger.debug(f"Event {event_id_str}: Updated field '{key}'")

        if updated:
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
    Handles date calculation for event tools.
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

        # --- Special Handling for Event Dates ---
        final_date_str = None
        repeat_type = "ONCE"
        cron_expression = ""
        event_action_data = None

        if function_name in ["add_event", "update_event"]:
            date_description = arguments.get("date_description")
            time_str = arguments.get("time", "19:00")
            description = arguments.get("description", "")
            title = arguments.get("title", "")

            if function_name == "update_event":
                event_id = arguments.get("event_id")
                if event_id and str(event_id) in events_data:
                     event_id_str = str(event_id)
                     if not title: title = events_data[event_id_str].get("title", "")
                     if not description: description = events_data[event_id_str].get("description", "")
                     arguments["id"] = event_id_str
                else:
                    return None, f"Lỗi: Không tìm thấy sự kiện ID '{event_id}' để cập nhật."

            if date_description:
                final_date_str = get_date_from_relative_term(date_description)
                if final_date_str:
                    logger.info(f"Calculated date '{final_date_str}' from description '{date_description}'")
                    arguments["date"] = final_date_str
                else:
                    logger.warning(f"Could not calculate date from '{date_description}'. Event date will be empty or unchanged.")
                    if "date" in arguments: del arguments["date"]

            if "date_description" in arguments:
                del arguments["date_description"]

            repeat_type = determine_repeat_type(description, title)
            arguments['repeat_type'] = repeat_type
            is_recurring_event = (repeat_type == "RECURRING")

            date_for_cron = final_date_str
            if function_name == "update_event" and not date_for_cron:
                event_id = arguments.get("id")
                if event_id and event_id in events_data:
                    date_for_cron = events_data[event_id].get('date')

            if is_recurring_event:
                cron_expression = generate_recurring_cron(description, title, time_str)
            elif date_for_cron: # ONCE with a valid date
                cron_expression = date_time_to_cron(date_for_cron, time_str)

            logger.info(f"Event type: {repeat_type}, Cron generated: '{cron_expression}'")

            event_action_data = {
                "action": "add" if function_name == "add_event" else "update",
                "id": arguments.get("id") if function_name == "update_event" else None,
                "title": title,
                "description": description,
                "cron_expression": cron_expression,
                "repeat_type": repeat_type,
                "original_date": final_date_str,
                "original_time": time_str,
                "participants": arguments.get("participants", [])
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
                result = func_to_call(arguments)
                if result is False:
                     tool_result_content = f"Thất bại khi thực thi {function_name}. Chi tiết lỗi đã được ghi lại."
                     logger.error(f"Execution failed for tool {function_name} with args {arguments}")
                     event_action_data = None
                else:
                     tool_result_content = f"Đã thực thi thành công {function_name}."
                     logger.info(f"Successfully executed tool {function_name}")
                     if function_name == "delete_event":
                         deleted_event_id = arguments.get("event_id")
                         event_action_data = {"action": "delete", "id": deleted_event_id}
                         tool_result_content = f"Đã xóa thành công sự kiện ID {deleted_event_id}."

                return event_action_data, tool_result_content
            except Exception as func_exc:
                 logger.error(f"Error executing tool function {function_name}: {func_exc}", exc_info=True)
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
        "- `update_event`: Để sửa sự kiện. Cung cấp `event_id` và các trường cần thay đổi. Tương tự `add_event` về cách xử lý ngày (`date_description`) và lặp lại (`description`).",
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

    # Check for Weather Query
    is_weather_query, location = await detect_weather_query(last_user_text, openai_api_key)
    if is_weather_query and OPENWEATHERMAP_API_KEY:
        logger.info(f"Phát hiện truy vấn thời tiết cho địa điểm: '{location}'")
        weather_service = WeatherService(OPENWEATHERMAP_API_KEY)
        
        # Sửa đổi: Ưu tiên địa điểm trong câu hỏi, chỉ dùng tọa độ khi không có địa điểm cụ thể
        if location and location.lower() not in ["hanoi", "hà nội"]:
            # Nếu có địa điểm cụ thể (không phải mặc định Hà Nội)
            logger.info(f"Sử dụng địa điểm từ câu hỏi: {location}")
            weather_data = await weather_service.get_current_weather(location=location)
            forecast_data = await weather_service.get_forecast(location=location, days=3)
        elif lat is not None and lon is not None:
            # Nếu không có địa điểm cụ thể trong câu hỏi và có tọa độ
            logger.info(f"Sử dụng tọa độ: lat={lat}, lon={lon}")
            weather_data = await weather_service.get_current_weather(lat=lat, lon=lon)
            forecast_data = await weather_service.get_forecast(lat=lat, lon=lon, days=3)
        else:
            # Nếu không có cả hai, mặc định là Hà Nội
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

    # Check for General Search Intent
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
