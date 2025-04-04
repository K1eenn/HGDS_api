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
              description="API cho Trợ lý Gia đình thông minh với khả năng xử lý text, hình ảnh, âm thanh và sử dụng Tool Calling",
              version="1.1.0") # Version bump

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

openai_model = "gpt-4o-mini" # Or your preferred model supporting Tool Calling

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
                    # Basic validation: ensure it's a dictionary
                    if isinstance(loaded_sessions, dict):
                         # Optional: Further validation of individual session structure
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
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.sessions_file) or '.', exist_ok=True)
            with open(self.sessions_file, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, ensure_ascii=False, indent=2)
            logger.info(f"Đã lưu {len(self.sessions)} session vào {self.sessions_file}")
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
                "created_at": datetime.datetime.now().isoformat(), # Use ISO format
                "last_updated": datetime.datetime.now().isoformat() # Use ISO format
            }
            self._save_sessions() # Save immediately after creation
        # Always update last access time? Maybe not needed if last_updated is on modification.
        # self.sessions[session_id]["last_accessed"] = datetime.datetime.now().isoformat()
        return self.sessions[session_id]

    def update_session(self, session_id, data):
        """Cập nhật dữ liệu session"""
        if session_id in self.sessions:
            try:
                self.sessions[session_id].update(data)
                self.sessions[session_id]["last_updated"] = datetime.datetime.now().isoformat() # Use ISO format
                # Save frequently? Or batch saves? Saving on every update can be slow.
                # Consider saving less often if performance is an issue.
                if not self._save_sessions():
                     logger.error(f"Cập nhật session {session_id} thành công trong bộ nhớ nhưng LƯU THẤT BẠI.")
                     # Should we revert the update in memory? Depends on desired behavior.
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
                    # Parse ISO format string
                    last_updated_date = datetime.datetime.fromisoformat(last_updated_str)
                    # Make it timezone-aware if it's naive (assume local timezone if naive, or UTC if stored as UTC)
                    if last_updated_date.tzinfo is None:
                        # Assuming stored time is UTC, make it aware
                        last_updated_date = last_updated_date.replace(tzinfo=datetime.timezone.utc)
                        # Or if assuming local time:
                        # last_updated_date = last_updated_date.astimezone(datetime.timezone.utc)

                    time_inactive = now - last_updated_date
                    if time_inactive.days > days_threshold:
                        sessions_to_remove.append(session_id)
                except ValueError:
                    logger.error(f"Định dạng last_updated không hợp lệ ('{last_updated_str}') cho session {session_id}. Xem xét xóa.")
                    # Optionally remove sessions with invalid dates too
                    # sessions_to_remove.append(session_id)
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý thời gian cho session {session_id}: {e}", exc_info=True)

        # Xóa các session cũ
        if sessions_to_remove:
             for session_id in sessions_to_remove:
                 if session_id in self.sessions: # Check again in case of concurrent modification
                     del self.sessions[session_id]
                     removed_count += 1
             if removed_count > 0:
                 self._save_sessions()
                 logger.info(f"Đã xóa {removed_count} session cũ (quá {days_threshold} ngày không hoạt động).")
             else:
                  logger.info("Không có session cũ nào cần xóa.")
        else:
            logger.info("Không có session cũ nào cần xóa.")


# --- Weather Service ---
class WeatherService:
    VIETNAMESE_WEEKDAY_MAP = {
        "thứ 2": 0, "thứ hai": 0, "t2": 0,
        "thứ 3": 1, "thứ ba": 1, "t3": 1,
        "thứ 4": 2, "thứ tư": 2, "t4": 2,
        "thứ 5": 3, "thứ năm": 3, "t5": 3,
        "thứ 6": 4, "thứ sáu": 4, "t6": 4,
        "thứ 7": 5, "thứ bảy": 5, "t7": 5,
        "chủ nhật": 6, "cn": 6,
    }

    def __init__(self, openweather_api_key: str = None):
        self.openweather_api_key = openweather_api_key or os.getenv("OPENWEATHER_API_KEY", "")
        self.cache = {}
        self.cache_duration = 30 * 60 # 30 minutes

        if not self.openweather_api_key or len(self.openweather_api_key) < 10:
             logger.warning(f"OpenWeatherMap API key không hợp lệ hoặc chưa được cấu hình. Dịch vụ thời tiết sẽ không hoạt động.")


    def _get_cache_key(self, location: str, forecast_days: int = 1) -> str:
        return f"{location.lower()}_{forecast_days}_{datetime.datetime.now().strftime('%Y-%m-%d_%H')}" # Cache per hour

    def _is_cache_valid(self, timestamp: float) -> bool:
        return (time.time() - timestamp) < self.cache_duration

    async def _make_api_request(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Helper function to make requests and handle common errors."""
        if not self.openweather_api_key or len(self.openweather_api_key) < 10:
             logger.error("Bỏ qua gọi API thời tiết do thiếu API key hợp lệ.")
             return None
        try:
            # Use asyncio compatible requests or httpx
            async with requests.Session() as session: # Use session for potential reuse
                 # Need an async request library like httpx or aiohttp
                 # Using requests synchronously here will block the event loop
                 # Let's use requests for now, but ideally replace with httpx
                 response = await asyncio.to_thread(session.get, url, params=params, timeout=10) # Run sync requests in thread
                 response.raise_for_status()
                 return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"Yêu cầu API thời tiết tới {url} bị timeout.")
            return None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            if status_code == 401:
                logger.error("Lỗi API thời tiết: API key không hợp lệ hoặc đã hết hạn.")
            elif status_code == 404:
                logger.error(f"Lỗi API thời tiết: Không tìm thấy dữ liệu cho vị trí (Params: {params}).")
            elif status_code == 429:
                logger.error("Lỗi API thời tiết: Vượt quá giới hạn gọi API.")
            else:
                logger.error(f"Lỗi HTTP API thời tiết ({status_code}): {e}. URL: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Lỗi kết nối API thời tiết: {e}. URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Lỗi không xác định khi gọi API thời tiết: {e}", exc_info=True)
            return None

    async def get_weather(self, location: str, forecast_days: int = 1, language: str = "vi", target_date: str = None) -> Dict[str, Any]:
        """Lấy thông tin thời tiết, ưu tiên OneCall API, fallback về basic API."""
        if not self.openweather_api_key or len(self.openweather_api_key) < 10:
            return {
                "error": True, "message": "API key OpenWeatherMap không hợp lệ hoặc chưa được cấu hình.",
                "recommendation": "Vui lòng bổ sung OPENWEATHER_API_KEY hợp lệ vào file .env"
            }

        calculated_days = forecast_days
        if target_date:
            try:
                target_date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
                today = datetime.datetime.now().date()
                days_difference = (target_date_obj - today).days
                if days_difference >= 0:
                    calculated_days = max(forecast_days, days_difference + 1)
                    logger.info(f"Điều chỉnh forecast_days thành {calculated_days} để bao gồm ngày {target_date}")
                else:
                    logger.warning(f"Ngày yêu cầu {target_date} là trong quá khứ, không thể lấy dự báo.")
                    # Return an error or just current weather? Let's return error for past dates.
                    return {"error": True, "message": f"Không thể lấy dự báo cho ngày quá khứ ({target_date})."}
            except ValueError:
                 logger.error(f"Định dạng target_date không hợp lệ: {target_date}. Sử dụng forecast_days mặc định.")
                 target_date = None # Reset target_date if invalid
            except Exception as e:
                 logger.error(f"Lỗi khi phân tích target_date {target_date}: {e}")
                 target_date = None # Reset on error

        calculated_days = min(calculated_days, 7) # Max 7 days forecast typically

        cache_key = self._get_cache_key(location, calculated_days)
        if cache_key in self.cache and self._is_cache_valid(self.cache[cache_key].get("timestamp", 0)):
            logger.info(f"Sử dụng dữ liệu thời tiết từ cache cho {location}")
            return self.cache[cache_key].get("data", {})

        # --- Try OneCall API First ---
        geo_data = await self._get_coordinates(location)
        if geo_data:
            lat, lon = geo_data['lat'], geo_data['lon']
            onecall_data = await self._get_weather_from_onecall_api(lat, lon, calculated_days, language)
            if onecall_data:
                standardized_data = self._parse_onecall_data(onecall_data, geo_data, calculated_days)
                if standardized_data:
                    self._update_cache(cache_key, standardized_data)
                    logger.info(f"Lấy dữ liệu thời tiết thành công từ OneCall API cho {location}")
                    return standardized_data
                else:
                    logger.error("Không thể phân tích dữ liệu từ OneCall API.")
            else:
                 logger.warning("Thất bại khi gọi OneCall API. Thử API cơ bản.")
        else:
             logger.warning(f"Không tìm thấy tọa độ cho {location}. Thử API cơ bản.")


        # --- Fallback to Basic APIs ---
        logger.info(f"Thử lấy thời tiết bằng API cơ bản cho {location}")
        current_data = await self._get_current_weather_basic(location, language)
        forecast_data = await self._get_forecast_basic(location, calculated_days, language)

        if current_data and forecast_data:
            combined_data = self._combine_basic_weather_data(current_data, forecast_data, calculated_days)
            if combined_data:
                 self._update_cache(cache_key, combined_data)
                 logger.info(f"Lấy dữ liệu thời tiết thành công từ API cơ bản cho {location}")
                 return combined_data
            else:
                 logger.error("Không thể kết hợp dữ liệu từ API thời tiết cơ bản.")
        else:
            logger.error("Thất bại khi lấy dữ liệu từ cả current và forecast API cơ bản.")


        # --- Final Error ---
        error_msg = f"Không thể lấy thông tin thời tiết cho {location}"
        if target_date: error_msg += f" vào ngày {target_date}"
        return {
            "error": True, "message": f"{error_msg}. Có lỗi khi kết nối hoặc xử lý dữ liệu từ OpenWeatherMap.",
            "recommendation": "Kiểm tra kết nối mạng, API key và tên vị trí."
        }

    def _update_cache(self, key: str, data: Dict[str, Any]) -> None:
        """Cập nhật cache với dữ liệu mới và timestamp"""
        self.cache[key] = {"data": data, "timestamp": time.time()}
        # Optional: Limit cache size if necessary
        # MAX_CACHE_SIZE = 100
        # if len(self.cache) > MAX_CACHE_SIZE:
        #     oldest_key = min(self.cache, key=lambda k: self.cache[k]['timestamp'])
        #     del self.cache[oldest_key]

    async def _get_coordinates(self, location: str) -> Optional[Dict[str, Any]]:
        """Lấy tọa độ (lat, lon) từ tên vị trí."""
        url = "https://api.openweathermap.org/geo/1.0/direct"
        params = {"q": location, "limit": 1, "appid": self.openweather_api_key}
        logger.info(f"Gọi Geocoding API cho {location}")
        geo_response = await self._make_api_request(url, params)
        if geo_response and len(geo_response) > 0:
            logger.info(f"Tìm thấy tọa độ cho {location}: {geo_response[0]['lat']}, {geo_response[0]['lon']}")
            return geo_response[0]
        else:
            logger.error(f"Không tìm thấy tọa độ cho {location}.")
            return None

    async def _get_weather_from_onecall_api(self, lat: float, lon: float, days: int, language: str) -> Optional[Dict[str, Any]]:
        """Gọi OpenWeatherMap OneCall API v3.0."""
        url = "https://api.openweathermap.org/data/3.0/onecall"
        params = {
            "lat": lat, "lon": lon,
            "exclude": "minutely,alerts", # Exclude less used parts
            "units": "metric", "lang": language,
            "appid": self.openweather_api_key
        }
        logger.info(f"Gọi OneCall API cho tọa độ {lat}, {lon}")
        return await self._make_api_request(url, params)

    def _parse_onecall_data(self, data: Dict[str, Any], geo_data: Dict[str, Any], forecast_days: int) -> Optional[Dict[str, Any]]:
        """Chuyển đổi dữ liệu OneCall API sang định dạng chuẩn."""
        try:
            current_dt = data.get("current", {}).get("dt")
            if not current_dt:
                 logger.error("Dữ liệu OneCall thiếu thông tin 'current'.")
                 return None

            standardized = {
                "location": {
                    "name": geo_data.get("local_names", {}).get("vi", geo_data.get("name", "Không rõ")),
                    "country": geo_data.get("country", ""),
                    "lat": geo_data.get("lat"), "lon": geo_data.get("lon"),
                    "localtime": datetime.datetime.fromtimestamp(current_dt, tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") # Show local time with zone
                },
                "current": {
                    "temp_c": data["current"]["temp"],
                    "temp_f": data["current"]["temp"] * 9/5 + 32,
                    "is_day": 1 if data["current"].get("sunrise", 0) < current_dt < data["current"].get("sunset", float('inf')) else 0,
                    "condition": {
                        "text": data["current"]["weather"][0]["description"],
                        "icon": f"https://openweathermap.org/img/wn/{data['current']['weather'][0]['icon']}@2x.png"
                    },
                    "wind_kph": data["current"]["wind_speed"] * 3.6,
                    "wind_dir": self._get_wind_direction(data["current"].get("wind_deg", 0)),
                    "humidity": data["current"]["humidity"],
                    "feelslike_c": data["current"]["feels_like"],
                    "uv": data["current"].get("uvi", 0),
                    "pressure_mb": data["current"].get("pressure"),
                    "visibility_km": data["current"].get("visibility", 10000) / 1000, # Meters to km
                },
                "forecast": []
            }

            # Parse daily forecast
            if "daily" in data:
                for day_data in data["daily"][:forecast_days]:
                    day_dt = day_data.get("dt")
                    if not day_dt: continue

                    day_forecast = {
                        "date": datetime.datetime.fromtimestamp(day_dt, tz=datetime.timezone.utc).strftime("%Y-%m-%d"),
                        "max_temp_c": day_data["temp"]["max"],
                        "min_temp_c": day_data["temp"]["min"],
                        "condition": {
                            "text": day_data["weather"][0]["description"],
                            "icon": f"https://openweathermap.org/img/wn/{day_data['weather'][0]['icon']}@2x.png"
                        },
                        "chance_of_rain": day_data.get("pop", 0) * 100,
                        "sunrise": datetime.datetime.fromtimestamp(day_data["sunrise"], tz=datetime.timezone.utc).astimezone().strftime("%H:%M"),
                        "sunset": datetime.datetime.fromtimestamp(day_data["sunset"], tz=datetime.timezone.utc).astimezone().strftime("%H:%M"),
                        "humidity": day_data.get("humidity"),
                        "uv": day_data.get("uvi"),
                        "wind_kph": day_data.get("wind_speed", 0) * 3.6,
                        "summary": day_data.get("summary", "") # Daily summary if available
                    }
                    standardized["forecast"].append(day_forecast)

            # Parse hourly forecast for the first day (or relevant period)
            if "hourly" in data:
                 # Find the first relevant forecast day to attach hourly data
                if standardized["forecast"]:
                     first_forecast_date = standardized["forecast"][0]["date"]
                     hourly_list = []
                     added_hours = 0
                     max_hours_to_show = 24 # Show up to 24 hours
                     now_local = datetime.datetime.now().astimezone()

                     for hour_data in data["hourly"]:
                         hour_dt_obj = datetime.datetime.fromtimestamp(hour_data["dt"], tz=datetime.timezone.utc).astimezone()
                         # Add only future hours or very recent past for context
                         if hour_dt_obj >= now_local - datetime.timedelta(hours=1) and added_hours < max_hours_to_show:
                             hour_forecast = {
                                 "time": hour_dt_obj.strftime("%H:%M"), # Local time
                                 "temp_c": hour_data["temp"],
                                 "condition": {
                                     "text": hour_data["weather"][0]["description"],
                                     "icon": f"https://openweathermap.org/img/wn/{hour_data['weather'][0]['icon']}@2x.png"
                                 },
                                 "chance_of_rain": hour_data.get("pop", 0) * 100,
                                 "humidity": hour_data.get("humidity"),
                                 "wind_kph": hour_data.get("wind_speed", 0) * 3.6
                             }
                             hourly_list.append(hour_forecast)
                             added_hours += 1

                     if hourly_list:
                          # Find which day to attach it to (usually the first one)
                          # This assumes hourly data starts from near current time
                          if standardized["forecast"][0]["date"] == now_local.strftime("%Y-%m-%d"):
                              standardized["forecast"][0]["hourly"] = hourly_list
                          elif len(standardized["forecast"]) > 1 and standardized["forecast"][1]["date"] == now_local.strftime("%Y-%m-%d"):
                               # If forecast starts tomorrow, but we have today's hourly
                               pass # Or decide where to put it
                          else: # Fallback: attach to first day anyway
                               standardized["forecast"][0]["hourly"] = hourly_list

            return standardized

        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Lỗi khi phân tích dữ liệu OneCall: {e}", exc_info=True)
            logger.error(f"Dữ liệu lỗi: {json.dumps(data, indent=2)}")
            return None

    async def _get_current_weather_basic(self, location: str, language: str) -> Optional[Dict[str, Any]]:
        """Lấy thời tiết hiện tại bằng API cơ bản."""
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": location, "units": "metric", "lang": language, "appid": self.openweather_api_key}
        logger.info(f"Gọi API thời tiết cơ bản (current) cho {location}")
        return await self._make_api_request(url, params)

    async def _get_forecast_basic(self, location: str, days: int, language: str) -> Optional[Dict[str, Any]]:
        """Lấy dự báo bằng API cơ bản (5 day / 3 hour)."""
        url = "https://api.openweathermap.org/data/2.5/forecast"
        # cnt = days * 8 # Approximate number of 3-hour intervals, max is 40 for free tier
        params = {"q": location, "units": "metric", "lang": language, "appid": self.openweather_api_key} # Let API decide count
        logger.info(f"Gọi API thời tiết cơ bản (forecast) cho {location}")
        return await self._make_api_request(url, params)

    def _combine_basic_weather_data(self, current_data: Dict[str, Any], forecast_data: Dict[str, Any], days: int) -> Optional[Dict[str, Any]]:
        """Kết hợp dữ liệu từ API cơ bản thành định dạng chuẩn."""
        try:
            current_dt = current_data.get("dt")
            if not current_dt: return None

            standardized = {
                "location": {
                    "name": current_data.get("name", ""),
                    "country": current_data.get("sys", {}).get("country", ""),
                    "lat": current_data.get("coord", {}).get("lat"),
                    "lon": current_data.get("coord", {}).get("lon"),
                    "localtime": datetime.datetime.fromtimestamp(current_dt, tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                },
                "current": {
                    "temp_c": current_data["main"]["temp"],
                    "temp_f": current_data["main"]["temp"] * 9/5 + 32,
                    "is_day": 1 if current_data.get("sys", {}).get("sunrise", 0) < current_dt < current_data.get("sys", {}).get("sunset", float('inf')) else 0,
                    "condition": {
                        "text": current_data["weather"][0]["description"],
                        "icon": f"https://openweathermap.org/img/wn/{current_data['weather'][0]['icon']}@2x.png"
                    },
                    "wind_kph": current_data.get("wind", {}).get("speed", 0) * 3.6,
                    "wind_dir": self._get_wind_direction(current_data.get("wind", {}).get("deg", 0)),
                    "humidity": current_data["main"]["humidity"],
                    "feelslike_c": current_data["main"]["feels_like"],
                    # Basic API doesn't provide UV reliably
                    "uv": None,
                    "pressure_mb": current_data["main"].get("pressure"),
                    "visibility_km": current_data.get("visibility", 10000) / 1000,
                },
                "forecast": []
            }

            # Process forecast data (group by day)
            daily_forecasts = {} # { "YYYY-MM-DD": {temps:[], conditions:[], rain_chances:[], hourly:[]} }
            if "list" in forecast_data:
                for item in forecast_data["list"]:
                    item_dt_obj = datetime.datetime.fromtimestamp(item["dt"], tz=datetime.timezone.utc)
                    date_str = item_dt_obj.strftime("%Y-%m-%d")

                    if date_str not in daily_forecasts:
                         # Limit number of forecast days collected
                         if len(daily_forecasts) >= days: break
                         daily_forecasts[date_str] = {"temps": [], "conditions": [], "icons": [], "rain_chances": [], "hourly": []}

                    daily_forecasts[date_str]["temps"].append(item["main"]["temp"])
                    daily_forecasts[date_str]["conditions"].append(item["weather"][0]["description"])
                    daily_forecasts[date_str]["icons"].append(item["weather"][0]["icon"])
                    daily_forecasts[date_str]["rain_chances"].append(item.get("pop", 0) * 100)

                    # Add hourly detail
                    hour_data = {
                        "time": item_dt_obj.astimezone().strftime("%H:%M"),
                        "temp_c": item["main"]["temp"],
                        "condition": {
                            "text": item["weather"][0]["description"],
                            "icon": f"https://openweathermap.org/img/wn/{item['weather'][0]['icon']}@2x.png"
                        },
                        "chance_of_rain": item.get("pop", 0) * 100
                    }
                    daily_forecasts[date_str]["hourly"].append(hour_data)

            # Aggregate daily forecasts
            for date_str, data in daily_forecasts.items():
                if not data["temps"]: continue # Skip empty days

                # Determine dominant condition/icon for the day (simple mode)
                main_condition = max(set(data["conditions"]), key=data["conditions"].count) if data["conditions"] else ""
                main_icon = max(set(data["icons"]), key=data["icons"].count) if data["icons"] else "01d" # Default icon

                day_forecast = {
                    "date": date_str,
                    "max_temp_c": max(data["temps"]),
                    "min_temp_c": min(data["temps"]),
                    "condition": {
                        "text": main_condition,
                        "icon": f"https://openweathermap.org/img/wn/{main_icon}@2x.png"
                    },
                    "chance_of_rain": max(data["rain_chances"]) if data["rain_chances"] else 0,
                    "hourly": data["hourly"] # Add all collected hourly data for the day
                    # Sunrise/sunset not available in basic forecast API
                }
                standardized["forecast"].append(day_forecast)

            return standardized

        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Lỗi khi kết hợp dữ liệu thời tiết cơ bản: {e}", exc_info=True)
            return None

    def _get_wind_direction(self, degrees: float) -> str:
        """Chuyển đổi góc gió (độ) sang hướng gió."""
        if degrees is None: return "Không rõ"
        directions = ["Bắc", "Đông Bắc", "Đông", "Đông Nam", "Nam", "Tây Nam", "Tây", "Tây Bắc"]
        index = round(degrees / 45) % 8
        return directions[index]

    @staticmethod
    def format_weather_message(weather_data: Dict[str, Any], location: str, days: int = 1, target_date: str = None, advice_only: bool = False) -> str:
        """Định dạng dữ liệu thời tiết thành thông điệp HTML (cập nhật để sử dụng dữ liệu mới)."""
        if weather_data.get("error"):
            # ... (error handling remains the same) ...
             title = f"Thông tin thời tiết cho {location}"
             if target_date:
                try:
                    date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                    formatted_date = date_obj.strftime("%d/%m/%Y")
                    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                    weekday = weekday_names[date_obj.weekday()]
                    title = f"Thông tin thời tiết cho {location} vào {weekday} ({formatted_date})"
                except:
                    title = f"Thông tin thời tiết cho {location} vào {target_date}"

             return f"""
             <h3>{title}</h3>
             <p>{weather_data.get('message', 'Đang gặp sự cố khi lấy dữ liệu thời tiết.')}</p>
             <p><i>{weather_data.get('recommendation', '')}</i></p>
             """

        current = weather_data.get("current", {})
        location_info = weather_data.get("location", {})
        actual_location = location_info.get("name", location)
        forecast = weather_data.get("forecast", [])

        target_forecast = None
        if target_date and forecast:
            for day_forecast in forecast:
                if day_forecast.get("date") == target_date:
                    target_forecast = day_forecast
                    break

        # Advice Only Mode
        if advice_only:
            temp_desc = ""
            weather_cond = ""
            rain_info = ""
            date_str = ""

            if target_forecast:
                min_temp = target_forecast.get("min_temp_c", 0)
                max_temp = target_forecast.get("max_temp_c", 0)
                temp_desc = f"nhiệt độ từ {min_temp:.1f}°C đến {max_temp:.1f}°C"
                weather_cond = target_forecast.get("condition", {}).get("text", "")
                rain_chance = target_forecast.get("chance_of_rain", 0)
                if rain_chance > 30:
                    rain_info = f", {rain_chance:.0f}% khả năng mưa"
                date_str = f" vào ngày {target_date}"
            elif current: # Use current if no target forecast
                temp_c = current.get("temp_c", 0)
                feels_like = current.get("feelslike_c", temp_c) # Use feels_like if available
                temp_desc = f"nhiệt độ hiện tại {temp_c:.1f}°C, cảm giác như {feels_like:.1f}°C"
                weather_cond = current.get("condition", {}).get("text", "")
                humidity = current.get("humidity", 0)
                if humidity > 70:
                    rain_info = f", độ ẩm khá cao ({humidity}%)"
                date_str = " hiện tại"
            else: # No data available
                 return f"Không có đủ dữ liệu thời tiết tại {actual_location} để đưa ra tư vấn."


            return f"Thời tiết tại {actual_location}{date_str}: {temp_desc}, trời {weather_cond}{rain_info}."

        # Normal Formatting Mode
        weather_emoji = ""
        title = ""

        if target_forecast:
            weather_emoji = WeatherService._get_weather_emoji(target_forecast.get("condition", {}).get("text", "").lower())
            try:
                date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                formatted_date = date_obj.strftime("%d/%m/%Y")
                weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                weekday = weekday_names[date_obj.weekday()]
                title = f"Thời tiết {actual_location} - {weekday}, {formatted_date} {weather_emoji}"
            except:
                title = f"Thời tiết {actual_location} - {target_date} {weather_emoji}"
        elif current:
            weather_emoji = WeatherService._get_weather_emoji(current.get("condition", {}).get("text", "").lower())
            title = f"Thời tiết hiện tại ở {actual_location} {weather_emoji}"
        else:
             return f"<h3>Không có dữ liệu thời tiết cho {location}</h3>"


        result = f"<h3>{title}</h3>\n"

        # Display main info (target day or current)
        if target_forecast:
            result += f"<p><b>Nhiệt độ:</b> {target_forecast.get('min_temp_c', 'N/A'):.1f}°C - {target_forecast.get('max_temp_c', 'N/A'):.1f}°C</p>\n"
            result += f"<p><b>Thời tiết:</b> {target_forecast.get('condition', {}).get('text', 'N/A').capitalize()}</p>\n"
            result += f"<p><b>Khả năng mưa:</b> {target_forecast.get('chance_of_rain', 'N/A'):.0f}%</p>\n"
            if target_forecast.get('humidity') is not None:
                result += f"<p><b>Độ ẩm:</b> {target_forecast.get('humidity'):.0f}%</p>\n"
            if target_forecast.get('wind_kph') is not None:
                 wind_dir = WeatherService._get_wind_direction(target_forecast.get('wind_deg', 0)) # Assuming wind_deg is available
                 result += f"<p><b>Gió:</b> {target_forecast.get('wind_kph'):.1f} km/h (hướng {wind_dir})</p>\n" # Need wind direction too
            if "sunrise" in target_forecast and "sunset" in target_forecast:
                result += f"<p><b>Mặt trời:</b> mọc {target_forecast.get('sunrise', 'N/A')} - lặn {target_forecast.get('sunset', 'N/A')}</p>\n"
            if target_forecast.get("summary"):
                 result += f"<p><b>Tóm tắt:</b> {target_forecast['summary']}</p>\n"

        elif current:
            result += f"<p><b>Hiện tại:</b> {current.get('temp_c', 'N/A'):.1f}°C (cảm giác như {current.get('feelslike_c', 'N/A'):.1f}°C)</p>\n"
            result += f"<p><b>Thời tiết:</b> {current.get('condition', {}).get('text', 'N/A').capitalize()}</p>\n"
            result += f"<p><b>Độ ẩm:</b> {current.get('humidity', 'N/A')}%</p>\n"
            result += f"<p><b>Gió:</b> {current.get('wind_kph', 'N/A'):.1f} km/h (hướng {current.get('wind_dir', 'N/A')})</p>\n"
            if current.get('uv') is not None:
                result += f"<p><b>Chỉ số UV:</b> {current.get('uv'):.1f}</p>\n"


        # Display Hourly Forecast (if available for the displayed day)
        hourly_data = None
        if target_forecast and target_forecast.get("hourly"):
            hourly_data = target_forecast["hourly"]
            result += f"<h4>Dự báo theo giờ ({target_forecast['date']}):</h4>\n"
        elif not target_date and forecast and forecast[0].get("date") == datetime.datetime.now().strftime("%Y-%m-%d") and forecast[0].get("hourly"):
             hourly_data = forecast[0]["hourly"]
             result += f"<h4>Dự báo theo giờ (Hôm nay):</h4>\n"

        if hourly_data:
            result += "<ul>\n"
            max_hourly_entries = 8 # Limit displayed hours
            count = 0
            for hour in hourly_data:
                if count >= max_hourly_entries: break
                hour_emoji = WeatherService._get_weather_emoji(hour.get("condition", {}).get("text", "").lower())
                rain_text = f"{hour.get('chance_of_rain', 0):.0f}% mưa"
                result += f"<li><b>{hour.get('time', '')}:</b> {hour_emoji} {hour.get('temp_c', 'N/A'):.1f}°C, {hour.get('condition', {}).get('text', '').capitalize()}, {rain_text}</li>\n"
                count += 1
            result += "</ul>\n"


        # Display Forecast for subsequent days
        # Start index depends on whether target_date was shown
        start_forecast_index = 0
        if target_date:
            # Find the index after the target date
            for i, day in enumerate(forecast):
                if day.get("date") == target_date:
                    start_forecast_index = i + 1
                    break
        elif forecast: # If showing current, forecast starts from index 0 (today) or 1 (if today is not in forecast)
             if forecast[0].get("date") != datetime.datetime.now().strftime("%Y-%m-%d"):
                  start_forecast_index = 0 # Forecast data starts tomorrow
             else:
                  start_forecast_index = 1 # Skip today if already shown hourly/current

        if forecast and len(forecast) > start_forecast_index and days > 1 :
            result += "<h4>Dự báo các ngày tới:</h4>\n<ul>\n"
            days_shown = 0
            max_forecast_days_to_show = days -1 # Show remaining days requested

            for i in range(start_forecast_index, len(forecast)):
                if days_shown >= max_forecast_days_to_show: break
                day = forecast[i]
                try:
                    day_date_obj = datetime.datetime.strptime(day.get("date", ""), "%Y-%m-%d")
                    day_date = day_date_obj.strftime("%d/%m")
                    weekday_short = ["T2","T3","T4","T5","T6","T7","CN"][day_date_obj.weekday()]
                    day_display = f"{weekday_short} {day_date}"
                except:
                    day_display = day.get("date", "")

                day_emoji = WeatherService._get_weather_emoji(day.get("condition", {}).get("text", "").lower())
                rain_text = f"{day.get('chance_of_rain', 0):.0f}% mưa"
                temp_text = f"{day.get('min_temp_c', '?'):.0f}-{day.get('max_temp_c', '?'):.0f}°C"

                result += f"<li><b>{day_display}:</b> {day_emoji} {day.get('condition', {}).get('text', '').capitalize()}, {temp_text}, {rain_text}</li>\n"
                days_shown += 1

            result += "</ul>\n"

        # Add update time
        result += f"<p><i>Cập nhật lúc: {location_info.get('localtime', 'Không rõ')}</i></p>"

        return result

    @staticmethod
    def _get_weather_emoji(condition: str) -> str:
        """Trả về emoji phù hợp với điều kiện thời tiết (lowercase input)."""
        condition = condition.lower() # Ensure lowercase comparison
        if any(word in condition for word in ["mưa", "rain", "shower", "drizzle", "dông"]):
            return "🌧️"
        if any(word in condition for word in ["giông", "bão", "thunder", "storm"]):
            return "⛈️"
        if any(word in condition for word in ["tuyết", "snow"]):
            return "❄️"
        if any(word in condition for word in ["nắng", "sunny", "clear", "quang"]):
            return "☀️"
        if any(word in condition for word in ["mây", "cloud", "cloudy", "overcast", "âm u"]):
            return "☁️"
        if any(word in condition for word in ["sương mù", "fog", "mist"]):
            return "🌫️"
        return "🌤️" # Default: Partly cloudy


    def get_date_from_relative_term(self, term: str) -> Optional[str]:
        """
        Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế (YYYY-MM-DD).
        Hỗ trợ: hôm nay, ngày mai, ngày kia, hôm qua, thứ X tuần sau, thứ X.
        """
        if not term: return None

        term = term.lower().strip()
        today = datetime.date.today()
        logger.debug(f"Calculating date for term: '{term}', today is: {today.strftime('%Y-%m-%d %A')}")

        # Basic relative terms
        if term in ["hôm nay", "today"]: return today.strftime("%Y-%m-%d")
        if term in ["ngày mai", "mai", "tomorrow"]: return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if term in ["ngày kia", "day after tomorrow"]: return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        if term in ["hôm qua", "yesterday"]: return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        # Specific weekdays
        target_weekday = -1
        is_next_week = False
        term_for_weekday_search = term

        for kw in ["tuần sau", "tuần tới", "next week"]:
            if kw in term:
                is_next_week = True
                term_for_weekday_search = term.replace(kw, "").strip()
                break

        for day_str, day_num in self.VIETNAMESE_WEEKDAY_MAP.items():
            if re.search(r'\b' + re.escape(day_str) + r'\b', term_for_weekday_search):
                target_weekday = day_num
                logger.debug(f"Found target weekday: {day_str} ({target_weekday}) in '{term_for_weekday_search}'")
                break

        if target_weekday != -1:
            today_weekday = today.weekday() # Monday is 0, Sunday is 6
            if is_next_week:
                days_to_next_monday = (7 - today_weekday) % 7 # Days until next Monday (0 if today is Monday)
                if days_to_next_monday == 0: days_to_next_monday = 7 # If today is Monday, jump to next week's Monday
                next_monday_date = today + datetime.timedelta(days=days_to_next_monday)
                final_date = next_monday_date + datetime.timedelta(days=target_weekday)
            else: # Upcoming weekday
                days_ahead = (target_weekday - today_weekday + 7) % 7
                if days_ahead == 0: days_ahead = 7 # If asking for today's weekday, mean next week's
                final_date = today + datetime.timedelta(days=days_ahead)

            logger.info(f"Calculated date for '{term}': {final_date.strftime('%Y-%m-%d %A')}")
            return final_date.strftime("%Y-%m-%d")

        # Fallback general terms
        if any(kw in term for kw in ["tuần sau", "tuần tới", "next week"]):
            days_to_next_monday = (7 - today.weekday()) % 7
            if days_to_next_monday == 0: days_to_next_monday = 7
            calculated_date = today + datetime.timedelta(days=days_to_next_monday) # Next Monday
            logger.info(f"Calculated date for general 'next week': {calculated_date.strftime('%Y-%m-%d')} (Next Monday)")
            return calculated_date.strftime("%Y-%m-%d")
        if "tháng tới" in term or "tháng sau" in term or "next month" in term:
             # Calculate first day of next month
             next_month_date = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
             logger.info(f"Calculated date for 'next month': {next_month_date.strftime('%Y-%m-%d')}")
             return next_month_date.strftime("%Y-%m-%d")


        # Check explicit date formats
        try:
            if re.match(r'\d{4}-\d{2}-\d{2}', term):
                 parsed_date = datetime.datetime.strptime(term, "%Y-%m-%d").date()
                 logger.info(f"Term '{term}' is YYYY-MM-DD format.")
                 return parsed_date.strftime("%Y-%m-%d")
            if re.match(r'\d{1,2}/\d{1,2}/\d{4}', term):
                 parsed_date = datetime.datetime.strptime(term, "%d/%m/%Y").date()
                 logger.info(f"Term '{term}' is DD/MM/YYYY format, normalized.")
                 return parsed_date.strftime("%Y-%m-%d")
            if re.match(r'\d{1,2}/\d{1,2}', term): # e.g., 20/7
                 day, month = map(int, term.split('/'))
                 current_year = today.year
                 parsed_date = datetime.date(current_year, month, day)
                 # If date is in the past, assume next year
                 if parsed_date < today:
                      parsed_date = datetime.date(current_year + 1, month, day)
                 logger.info(f"Term '{term}' is DD/MM format, assumed year {parsed_date.year}.")
                 return parsed_date.strftime("%Y-%m-%d")

        except ValueError:
            logger.warning(f"Term '{term}' looks like a date but is invalid.")
            pass # Not a valid date format or value

        logger.warning(f"Could not interpret relative date term: '{term}'. Returning None.")
        return None

    @staticmethod
    def detect_weather_query(text: str) -> Tuple[bool, Optional[str], Optional[int], Optional[str]]:
        """Phát hiện câu hỏi thời tiết, trích xuất vị trí, số ngày, cụm từ thời gian."""
        text_lower = text.lower()
        weather_keywords = [
            "thời tiết", "dự báo", "nhiệt độ", "nắng", "mưa", "gió", "bão",
            "giông", "nóng", "lạnh", "độ ẩm", "mấy độ", "bao nhiêu độ"
        ]
        # More specific time keywords
        time_keywords = {
            # Relative days
            "hôm nay": 1, "nay": 1,
            "ngày mai": 2, "mai": 2,
            "ngày kia": 3, "mốt": 3, "ngày mốt": 3,
            "hôm qua": 0, # Indicate past, maybe return error or current
            # Week references
            "cuối tuần": 3, # Approx days needed to cover weekend
            "tuần này": 7,
            "tuần tới": 7, "tuần sau": 7,
            # Specific weekdays (handled by get_date_from_relative_term later)
            # Number of days
            "3 ngày tới": 3, "ba ngày tới": 3,
            "5 ngày tới": 5, "năm ngày tới": 5,
            "7 ngày tới": 7, "bảy ngày tới": 7, "một tuần tới": 7,
        }

        is_weather_query = any(keyword in text_lower for keyword in weather_keywords)
        if not is_weather_query:
            return False, None, None, None

        days = 1
        time_term = "hôm nay" # Default

        # Check for specific day counts first
        day_count_match = re.search(r'(\d+|một|hai|ba|bốn|năm|sáu|bảy)\s+ngày\s+tới', text_lower)
        num_map = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7}
        if day_count_match:
             count_str = day_count_match.group(1)
             try:
                  days = int(count_str)
             except ValueError:
                  days = num_map.get(count_str, 1)
             time_term = f"{days} ngày tới"

        # Check other time keywords if no day count found
        if time_term == "hôm nay": # Only override default if found
             # Sort keywords by length descending to match longer phrases first
            sorted_time_keywords = sorted(time_keywords.keys(), key=len, reverse=True)
            for term, days_value in time_keywords.items(): # Use original map for days_value
                if term in text_lower:
                    time_term = term
                    # Use days_value only if it's larger than current 'days' (from potential day count)
                    days = max(days, days_value if days_value > 0 else 1) # Ensure days >= 1
                    break

        # Check for specific weekdays like "thứ 6 tuần sau"
        weekday_match = re.search(r'(thứ\s+[2-7]|thứ\s+(hai|ba|tư|năm|sáu|bảy)|chủ\s+nhật|cn)(\s+tuần\s+(này|sau|tới))?', text_lower)
        if weekday_match:
             # Extract the full term like "thứ 6 tuần sau"
             time_term = weekday_match.group(0).strip()
             # Estimate days needed (max 7 for a week)
             days = max(days, 7)


        # Extract location (improved)
        location = None
        # Look for patterns like "thời tiết ở/tại [Location]"
        loc_pattern1 = r'(thời\s+tiết|dự\s+báo)\s+(ở|tại)\s+([^?.,!\n]+)'
        match1 = re.search(loc_pattern1, text_lower)
        if match1:
            location = match1.group(3).strip().title()
        else:
            # Look for location at the end, possibly after time phrase
            loc_pattern2 = r'\b(ở|tại)\s+([^?.,!\n]+)$' # Location at the very end
            match2 = re.search(loc_pattern2, text_lower)
            if match2:
                 location = match2.group(2).strip().title()
            else:
                 # Try common locations
                 popular_locations = [
                    "hà nội", "hồ chí minh", "tp hcm", "sài gòn", "đà nẵng", "hải phòng",
                    "cần thơ", "huế", "nha trang", "đà lạt", "vũng tàu", "quy nhơn",
                    "phú quốc", "hội an", "nam định", "hà giang", "lào cai", "sapa",
                    "bắc ninh", "thái nguyên", "vinh", "thanh hóa", "buôn ma thuột", "cà mau"
                 ]
                 # Check from longest to shortest to avoid partial matches e.g. "hà" in "hà nội"
                 sorted_locations = sorted(popular_locations, key=len, reverse=True)
                 for loc in sorted_locations:
                      # Use word boundary to avoid matching parts of words
                     if re.search(r'\b' + re.escape(loc) + r'\b', text_lower):
                         location = loc.title()
                         # Specific mapping
                         if location == "Tp Hcm": location = "Hồ Chí Minh"
                         if location == "Sài Gòn": location = "Hồ Chí Minh"
                         break

        # Default location if none found
        if not location:
            location = "Hà Nội"
            logger.info("Không tìm thấy vị trí thời tiết, mặc định là Hà Nội.")

        logger.info(f"Phát hiện truy vấn thời tiết: Vị trí='{location}', Cụm từ thời gian='{time_term}', Số ngày ước tính={days}")
        return True, location, days, time_term


# --- Weather Advisor ---
class WeatherAdvisor:
    CLOTHING_TEMP_RANGES = { # Use tuples for keys for ordering/comparison if needed
        (-float('inf'), 15): "rất lạnh",
        (15, 20): "lạnh",
        (20, 25): "mát mẻ",
        (25, 29): "ấm áp",
        (29, 35): "nóng",
        (35, float('inf')): "rất nóng"
    }
    WEATHER_CONDITIONS_KEYWORDS = {
        "mưa": ["mưa", "rain", "shower", "drizzle", "dông"],
        "giông bão": ["giông", "bão", "thunder", "storm"],
        "tuyết": ["tuyết", "snow"],
        "nắng": ["nắng", "sunny", "clear", "quang"],
        "mây": ["mây", "cloud", "cloudy", "overcast", "u ám"],
        "gió": ["gió", "wind", "windy"],
        "sương mù": ["sương mù", "fog", "mist"],
    }

    def __init__(self):
        pass

    @staticmethod
    def detect_advice_query(text: str) -> Tuple[bool, str, Optional[str]]:
        """Phát hiện câu hỏi xin tư vấn dựa trên thời tiết."""
        text_lower = text.lower()

        # Keywords MUST indicate asking for advice *in relation to weather*
        advice_trigger = ["thời tiết", "ngày mai", "hôm nay", "cuối tuần", "đi chơi", "ra ngoài"] # Context words
        if not any(trigger in text_lower for trigger in advice_trigger):
             # If none of the context words are present, it's less likely weather advice
             # Check if temperature/rain mentioned explicitly
             if not any(cond in text_lower for cond in ["nóng", "lạnh", "mưa", "nắng", "độ"]):
                  return False, "", None # Not clearly weather related advice

        # Advice types
        clothing_keywords = ["mặc gì", "trang phục", "quần áo", "ăn mặc", "đồ gì"]
        activity_keywords = ["làm gì", "đi đâu", "chơi gì", "hoạt động", "nên đi"]
        item_keywords = ["mang gì", "mang theo", "chuẩn bị gì", "đem theo", "cần gì"]

        advice_type = ""
        if any(keyword in text_lower for keyword in clothing_keywords): advice_type = "clothing"
        elif any(keyword in text_lower for keyword in activity_keywords): advice_type = "activity"
        elif any(keyword in text_lower for keyword in item_keywords): advice_type = "items"

        if not advice_type:
            return False, "", None # No specific advice type keyword found

        # Find time term (similar to weather detection, maybe reuse?)
        time_term = None
        # Prioritize specific weekdays first
        weekday_match = re.search(r'(thứ\s+[2-7]|thứ\s+(hai|ba|tư|năm|sáu|bảy)|chủ\s+nhật|cn)(\s+tuần\s+(này|sau|tới))?', text_lower)
        if weekday_match:
             time_term = weekday_match.group(0).strip()
        else:
             # Check relative terms (longest first)
            time_keywords = [
                "ngày kia", "ngày mốt", "cuối tuần này", "cuối tuần sau", "tuần tới", "tuần sau",
                "ngày mai", "mai", "tối nay", "sáng mai", "chiều mai", "tối mai", "hôm nay", "nay"
            ]
            for keyword in time_keywords:
                if keyword in text_lower:
                    time_term = keyword
                    break

        # Default to "hôm nay" if asking generally or no time found
        if not time_term:
            time_term = "hôm nay"

        logger.info(f"Phát hiện truy vấn tư vấn: Loại={advice_type}, Thời gian='{time_term}'")
        return True, advice_type, time_term

    def _get_relevant_weather(self, weather_data: Dict[str, Any], target_date: Optional[str]) -> Optional[Dict[str, Any]]:
        """Extracts weather info for the target date or current conditions."""
        if target_date and weather_data.get("forecast"):
            for day in weather_data["forecast"]:
                if day.get("date") == target_date:
                    # Return a combined structure for easier access
                    return {
                        "temp_min": day.get("min_temp_c"),
                        "temp_max": day.get("max_temp_c"),
                        "condition_text": day.get("condition", {}).get("text", "").lower(),
                        "rain_chance": day.get("chance_of_rain"),
                        "humidity": day.get("humidity"),
                        "uv": day.get("uv")
                    }
        elif weather_data.get("current"): # Use current if no target date or forecast not found
            current = weather_data["current"]
            return {
                "temp_c": current.get("temp_c"),
                "feels_like": current.get("feelslike_c"),
                "condition_text": current.get("condition", {}).get("text", "").lower(),
                "rain_chance": 0, # Assume 0 chance for current 'pop' usually not in basic current weather
                "humidity": current.get("humidity"),
                "uv": current.get("uv")
            }
        return None # No relevant data found

    def _get_temp_category(self, temp_info: Dict[str, Any]) -> str:
        """Determines the temperature category (rất lạnh, lạnh, etc.)."""
        temp_to_check = None
        if temp_info.get("temp_min") is not None and temp_info.get("temp_max") is not None:
            temp_to_check = (temp_info["temp_min"] + temp_info["temp_max"]) / 2 # Average for forecast
        elif temp_info.get("feels_like") is not None:
             temp_to_check = temp_info["feels_like"] # Use feels_like for current
        elif temp_info.get("temp_c") is not None:
             temp_to_check = temp_info["temp_c"] # Use actual temp if feels_like unavailable

        if temp_to_check is None: return "ôn hòa" # Default if no temp data

        for (min_t, max_t), category in self.CLOTHING_TEMP_RANGES.items():
            if min_t <= temp_to_check < max_t:
                return category
        return "ôn hòa" # Fallback

    def _get_dominant_condition(self, condition_text: str, rain_chance: float) -> str:
        """Determines the most significant weather condition."""
        condition_text = condition_text.lower()
        if rain_chance > 60: return "mưa" # High chance of rain overrides others
        if rain_chance > 30: return "có thể mưa"

        for state, keywords in self.WEATHER_CONDITIONS_KEYWORDS.items():
            if any(keyword in condition_text for keyword in keywords):
                return state # Return the first matching category

        return "quang đãng" # Default if nothing significant matches


    def get_clothing_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """Đưa ra lời khuyên về trang phục."""
        weather_info = self._get_relevant_weather(weather_data, target_date)
        if not weather_info:
            return "Không đủ thông tin thời tiết để đưa ra lời khuyên trang phục."

        temp_category = self._get_temp_category(weather_info)
        condition_text = weather_info.get("condition_text", "")
        rain_chance = weather_info.get("rain_chance", 0)
        dominant_condition = self._get_dominant_condition(condition_text, rain_chance)

        return self._generate_clothing_advice(temp_category, dominant_condition)


    def get_activity_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """Đưa ra lời khuyên về hoạt động."""
        weather_info = self._get_relevant_weather(weather_data, target_date)
        if not weather_info:
            return "Không đủ thông tin thời tiết để đưa ra lời khuyên hoạt động."

        temp_category = self._get_temp_category(weather_info)
        condition_text = weather_info.get("condition_text", "")
        rain_chance = weather_info.get("rain_chance", 0)
        dominant_condition = self._get_dominant_condition(condition_text, rain_chance)

        return self._generate_activity_advice(temp_category, dominant_condition)

    def get_items_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """Đưa ra lời khuyên về đồ dùng cần mang theo."""
        weather_info = self._get_relevant_weather(weather_data, target_date)
        if not weather_info:
            return "Không đủ thông tin thời tiết để đưa ra lời khuyên vật dụng."

        temp_category = self._get_temp_category(weather_info)
        condition_text = weather_info.get("condition_text", "")
        rain_chance = weather_info.get("rain_chance", 0)
        dominant_condition = self._get_dominant_condition(condition_text, rain_chance)
        uv_index = weather_info.get("uv")
        humidity = weather_info.get("humidity")

        return self._generate_items_advice(temp_category, dominant_condition, uv_index, humidity)


    def _generate_clothing_advice(self, temp_category: str, dominant_condition: str) -> str:
        """Tạo lời khuyên chi tiết về trang phục."""
        advice = f"Với thời tiết được dự báo là **{temp_category}** và **{dominant_condition}**, bạn nên cân nhắc:"
        items = []

        # Temperature based advice
        if temp_category == "rất lạnh": items.extend(["Áo khoác dày, áo len", "Mũ, khăn, găng tay", "Quần dày, giày ấm"])
        elif temp_category == "lạnh": items.extend(["Áo khoác vừa", "Áo dài tay", "Quần dài"])
        elif temp_category == "mát mẻ": items.extend(["Áo khoác mỏng hoặc cardigan", "Áo thun/sơ mi dài tay", "Quần dài thoải mái"])
        elif temp_category == "ấm áp": items.extend(["Áo thun/sơ mi ngắn tay", "Quần lửng hoặc quần dài mỏng"])
        elif temp_category == "nóng": items.extend(["Áo thun/ba lỗ mỏng, sáng màu", "Quần short/váy", "Chất liệu thoáng mát (cotton, linen)"])
        elif temp_category == "rất nóng": items.extend(["Trang phục mỏng, rộng, sáng màu nhất có thể", "Ưu tiên vải thấm hút mồ hôi", "Sandal hoặc dép thoáng khí"])

        # Condition based advice
        if dominant_condition == "mưa": items.extend(["**Ô hoặc áo mưa**", "**Giày chống nước**"])
        elif dominant_condition == "có thể mưa": items.append("Mang theo ô dự phòng")
        elif dominant_condition == "nắng": items.extend(["Mũ/nón rộng vành", "Kính râm", "Áo chống nắng nếu cần"])
        elif dominant_condition == "gió": items.append("Áo khoác coupe-vent (chắn gió)")
        elif dominant_condition == "giông bão": items.append("Hạn chế ra ngoài nếu không cần thiết!")

        if items:
            advice += "\n<ul>\n" + "".join([f"<li>{item}</li>" for item in items]) + "</ul>"
        else:
            advice += " Mặc đồ thoải mái là được."

        advice += "\n<p><i>Hãy điều chỉnh theo cảm nhận thực tế của bạn nhé!</i></p>"
        return advice


    def _generate_activity_advice(self, temp_category: str, dominant_condition: str) -> str:
        """Tạo lời khuyên chi tiết về hoạt động."""
        advice = f"Thời tiết **{temp_category}** và **{dominant_condition}** khá thích hợp cho:"
        activities = []

        # Indoor options (always possible)
        indoor = ["Xem phim tại rạp/nhà", "Đọc sách/ghé thư viện", "Thăm bảo tàng/triển lãm", "Đi cafe cùng bạn bè", "Nấu ăn/học món mới", "Chơi board game"]
        # Outdoor options (weather dependent)
        outdoor_good = ["Đi dạo công viên/bờ hồ", "Dã ngoại", "Đạp xe", "Chơi thể thao ngoài trời", "Tham quan khu du lịch"]
        outdoor_hot = ["Đi bơi", "Đến khu vui chơi nước", "Hoạt động trong nhà có điều hòa", "Đi dạo vào sáng sớm/chiều tối"]

        if dominant_condition in ["mưa", "giông bão", "tuyết"]:
            activities.extend(random.sample(indoor, min(len(indoor), 3)))
            advice += " các hoạt động trong nhà:"
        elif temp_category in ["nóng", "rất nóng"]:
            activities.extend(random.sample(outdoor_hot + indoor, min(len(outdoor_hot + indoor), 4)))
            advice += " các hoạt động tránh nóng hoặc trong nhà:"
        elif temp_category in ["mát mẻ", "ấm áp"] and dominant_condition not in ["mưa", "có thể mưa", "giông bão"]:
             activities.extend(random.sample(outdoor_good, min(len(outdoor_good), 2)))
             activities.extend(random.sample(indoor, min(len(indoor), 1)))
             advice += " cả hoạt động ngoài trời và trong nhà:"
        else: # Cold or default weather
             activities.extend(random.sample(indoor, min(len(indoor), 2))) # Focus indoor for cold
             if temp_category not in ["rất lạnh", "lạnh"]:
                  activities.extend(random.sample(outdoor_good, min(len(outdoor_good), 1)))
             advice += " các hoạt động trong nhà hoặc ngoài trời (nếu bạn thích):"


        if activities:
             advice += "\n<ul>\n" + "".join([f"<li>{act}</li>" for act in activities]) + "</ul>"
        else:
             advice += " nhiều hoạt động khác nhau tùy sở thích của bạn."

        return advice

    def _generate_items_advice(self, temp_category: str, dominant_condition: str, uv_index: Optional[float], humidity: Optional[float]) -> str:
        """Tạo lời khuyên chi tiết về đồ dùng."""
        advice = "Khi ra ngoài, bạn có thể cần mang theo:"
        items = ["Điện thoại, ví tiền, chìa khóa"] # Basics

        if dominant_condition == "mưa": items.extend(["**Ô hoặc áo mưa**", "Túi chống nước (nếu cần)"])
        elif dominant_condition == "có thể mưa": items.append("Ô dự phòng")

        if dominant_condition == "nắng": items.extend(["Kính râm", "Mũ/nón"])
        if uv_index is not None and uv_index >= 3: items.append(f"**Kem chống nắng (UV {uv_index:.1f})**")

        if temp_category == "rất lạnh": items.extend(["Găng tay", "Khăn quàng", "Mũ ấm"])
        elif temp_category == "lạnh": items.extend(["Găng tay mỏng", "Khăn quàng nhẹ"])

        if temp_category in ["nóng", "rất nóng"] or (humidity is not None and humidity > 75):
             items.append("Chai nước")
             items.append("Khăn giấy/khăn tay")

        if dominant_condition == "giông bão": items.append("**Chú ý an toàn, hạn chế ra đường!**")


        if items:
             # Remove duplicates while preserving order (sort of)
            seen = set()
            unique_items = []
            for item in items:
                if item not in seen:
                    unique_items.append(item)
                    seen.add(item)

            advice += "\n<ul>\n" + "".join([f"<li>{item}</li>" for item in unique_items]) + "</ul>"
        else:
             advice += " các vật dụng cá nhân cần thiết."

        return advice


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
                        "properties": { # Allow dynamic properties or define common ones
                            "food": {"type": "string", "description": "Món ăn yêu thích."},
                            "hobby": {"type": "string", "description": "Sở thích chính."},
                            "color": {"type": "string", "description": "Màu sắc yêu thích."}
                        },
                         "additionalProperties": {"type": "string"} # Allow other preferences
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


# ------- Load/Save Data & Verification (Moved Data Loading below function defs) --------
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Ensure data is dict (or list for chat history?) -> dict for most
                if not isinstance(data, dict):
                     # Special case for chat history which might be list per member?
                     # No, structure is { member_id: [ {chat_entry}, ... ] } which is dict
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
        # Create a temporary file and write, then rename to make it more atomic
        temp_file_path = file_path + ".tmp"
        with open(temp_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False) # Use indent=2 for smaller files
        # Replace the original file with the temporary file
        shutil.move(temp_file_path, file_path)
        # logger.info(f"Đã lưu dữ liệu vào {file_path}") # Reduce log noise?
        return True
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}", exc_info=True)
        # Clean up temp file if it exists
        if os.path.exists(temp_file_path):
             try:
                 os.remove(temp_file_path)
             except OSError as rm_err:
                  logger.error(f"Không thể xóa file tạm {temp_file_path}: {rm_err}")
        return False

def verify_data_structure():
    """Kiểm tra và đảm bảo cấu trúc dữ liệu ban đầu."""
    global family_data, events_data, notes_data, chat_history
    needs_save = False

    if not isinstance(family_data, dict):
        logger.warning("family_data không phải từ điển. Khởi tạo lại.")
        family_data = {}
        needs_save = True
    # Check internal structure if needed (e.g., ensure each member is a dict)

    if not isinstance(events_data, dict):
        logger.warning("events_data không phải từ điển. Khởi tạo lại.")
        events_data = {}
        needs_save = True
    # Ensure events have IDs and basic structure

    if not isinstance(notes_data, dict):
        logger.warning("notes_data không phải từ điển. Khởi tạo lại.")
        notes_data = {}
        needs_save = True

    if not isinstance(chat_history, dict):
        logger.warning("chat_history không phải từ điển. Khởi tạo lại.")
        chat_history = {}
        needs_save = True
    # Ensure chat history format {member_id: list}

    if needs_save:
        logger.info("Lưu lại cấu trúc dữ liệu mặc định do phát hiện lỗi.")
        save_data(FAMILY_DATA_FILE, family_data)
        save_data(EVENTS_DATA_FILE, events_data)
        save_data(NOTES_DATA_FILE, notes_data)
        save_data(CHAT_HISTORY_FILE, chat_history)

# --- Data Management Functions ---
# (add_family_member, update_preference, add_event, update_event, delete_event, add_note)
# Define these functions *before* mapping them in tool_functions

def add_family_member(details):
    """Thêm thành viên mới."""
    global family_data
    try:
        member_id = str(uuid.uuid4()) # Use UUID
        # Basic validation
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
             if member_id in family_data: del family_data[member_id] # Rollback memory add
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
                family_data[member_id]["preferences"] = {} # Initialize if missing/invalid

            original_value = family_data[member_id]["preferences"].get(preference_key)
            family_data[member_id]["preferences"][preference_key] = preference_value
            family_data[member_id]["last_updated"] = datetime.datetime.now().isoformat()

            if save_data(FAMILY_DATA_FILE, family_data):
                logger.info(f"Đã cập nhật sở thích '{preference_key}' cho thành viên {member_id}")
                return True
            else:
                 logger.error(f"Lưu thất bại sau khi cập nhật sở thích cho {member_id}.")
                 # Rollback memory change
                 if original_value is not None:
                      family_data[member_id]["preferences"][preference_key] = original_value
                 else: # Value didn't exist before
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
        event_id = str(uuid.uuid4()) # Use UUID for more robust IDs
        if not details.get('title') or 'date' not in details or details.get('date') is None:
            logger.error(f"Thiếu title hoặc date đã tính toán khi thêm sự kiện: {details}")
            return False

        events_data[event_id] = {
            "id": event_id,
            "title": details.get("title"),
            "date": details.get("date"), # Use the calculated date
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
             if event_id in events_data: del events_data[event_id] # Rollback memory add
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
                if key == 'date' and not value: # Don't update date to empty if calculation failed
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
    event_id_to_delete = str(details.get("event_id")) # Get ID from args
    if not event_id_to_delete:
         logger.error("Thiếu event_id để xóa sự kiện.")
         return False
    try:
        if event_id_to_delete in events_data:
            deleted_event_copy = events_data.pop(event_id_to_delete) # Remove and get copy
            if save_data(EVENTS_DATA_FILE, events_data):
                 logger.info(f"Đã xóa sự kiện ID {event_id_to_delete}")
                 return True
            else:
                 logger.error(f"Lưu sau khi xóa sự kiện ID {event_id_to_delete} thất bại.")
                 # Rollback memory delete
                 events_data[event_id_to_delete] = deleted_event_copy
                 logger.info(f"Đã rollback xóa trong bộ nhớ cho event ID {event_id_to_delete}.")
                 return False
        else:
            logger.warning(f"Không tìm thấy sự kiện ID {event_id_to_delete} để xóa.")
            return False # Indicate not found implicitly
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
             if note_id in notes_data: del notes_data[note_id] # Rollback memory add
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

# Initialize Session Manager (Should be done after data loading if it depends on it)
session_manager = SessionManager(SESSIONS_DATA_FILE)

# Initialize Weather Service & Advisor
weather_service = WeatherService(openweather_api_key=os.getenv("OPENWEATHER_API_KEY"))
weather_advisor = WeatherAdvisor()

# ------- Date/Time/Cron Helper Functions --------
VIETNAMESE_WEEKDAY_MAP = weather_service.VIETNAMESE_WEEKDAY_MAP # Use the one from WeatherService
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

def get_date_from_relative_term(term):
    """Wrapper to use the method from WeatherService instance."""
    return weather_service.get_date_from_relative_term(term)


def date_time_to_cron(date_str, time_str="19:00"):
    """Chuyển ngày giờ cụ thể sang Quartz cron expression."""
    try:
        # Ensure time_str is valid HH:MM
        if not time_str or ':' not in time_str: time_str = "19:00"
        hour, minute = map(int, time_str.split(":"))
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        # Quartz: Seconds Minute Hour DayOfMonth Month DayOfWeek Year
        quartz_cron = f"0 {minute} {hour} {date_obj.day} {date_obj.month} ? {date_obj.year}"
        logger.info(f"Generated Quartz cron ONCE for {date_str} {time_str}: {quartz_cron}")
        return quartz_cron
    except (ValueError, TypeError) as e:
        logger.error(f"Lỗi tạo cron expression ONCE cho date='{date_str}', time='{time_str}': {e}")
        return "" # Return empty string on error

def determine_repeat_type(description, title):
    """Xác định kiểu lặp lại dựa trên mô tả và tiêu đề."""
    combined_text = (str(description) + " " + str(title)).lower()
    for keyword in RECURRING_KEYWORDS:
        # Use word boundaries for keywords like 'daily' to avoid matching 'conditionally'
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
        # Quartz: 1=SUN, 2=MON, ..., 7=SAT
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
            # Assume weekly if a specific day is mentioned with recurring keywords
             is_weekly = any(kw in combined_text for kw in ["hàng tuần", "mỗi tuần", "weekly", "every"])
             if is_weekly or any(kw in combined_text for kw in RECURRING_KEYWORDS): # Check general recurring keywords too
                 quartz_cron = f"0 {minute} {hour} ? * {found_day_num} *"
                 logger.info(f"Tạo cron Quartz HÀNG TUẦN vào Thứ {found_day_text} ({found_day_num}) lúc {time_str}: {quartz_cron}")
                 return quartz_cron
             else:
                  logger.warning(f"Tìm thấy '{found_day_text}' nhưng không rõ là hàng tuần. Không tạo cron lặp lại.")


        # 3. Monthly (Example: day 15 or last day)
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

        # 4. Fallback for recurring (e.g., "hàng tuần" without specific day) -> default daily
        logger.warning(f"Không thể xác định lịch lặp lại cụ thể từ '{combined_text[:100]}...'. Cron sẽ rỗng.")
        return "" # Return empty if cannot determine specific recurring schedule

    except Exception as e:
        logger.error(f"Lỗi khi tạo cron Quartz lặp lại: {e}", exc_info=True)
        return "" # Return empty string on error


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
    message: MessageContent # The new message from user
    content_type: str = "text" # "text", "image", "audio"
    openai_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    messages: Optional[List[Message]] = None # Optional full history from client


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
        # Handle potential empty arguments string
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

            # For update, get potentially existing title/description if not provided in args
            if function_name == "update_event":
                event_id = arguments.get("event_id")
                if event_id and str(event_id) in events_data: # Ensure ID is string for lookup
                     event_id_str = str(event_id)
                     if not title: title = events_data[event_id_str].get("title", "")
                     if not description: description = events_data[event_id_str].get("description", "")
                     # Store string ID back into args for the function call
                     arguments["id"] = event_id_str
                else:
                    return None, f"Lỗi: Không tìm thấy sự kiện ID '{event_id}' để cập nhật."

            # Calculate the actual date using Python
            if date_description:
                final_date_str = get_date_from_relative_term(date_description)
                if final_date_str:
                    logger.info(f"Calculated date '{final_date_str}' from description '{date_description}'")
                    arguments["date"] = final_date_str # Use calculated date for the function call
                else:
                    logger.warning(f"Could not calculate date from '{date_description}'. Event date will be empty or unchanged.")
                    # Remove date field if calculation failed, otherwise it might try to save None/empty
                    if "date" in arguments: del arguments["date"]

            # Remove date_description as it's processed
            if "date_description" in arguments:
                del arguments["date_description"]

            # Determine recurrence and generate cron AFTER calculating the final date
            repeat_type = determine_repeat_type(description, title)
            arguments['repeat_type'] = repeat_type # Store for potential use in add/update logic
            is_recurring_event = (repeat_type == "RECURRING")

            # Use final_date_str (if calculated) or existing date (for update) for cron generation
            date_for_cron = final_date_str
            if function_name == "update_event" and not date_for_cron:
                event_id = arguments.get("id") # Use the potentially updated string ID
                if event_id and event_id in events_data:
                    date_for_cron = events_data[event_id].get('date')

            if is_recurring_event:
                cron_expression = generate_recurring_cron(description, title, time_str)
            elif date_for_cron: # ONCE with a valid date
                cron_expression = date_time_to_cron(date_for_cron, time_str)

            logger.info(f"Event type: {repeat_type}, Cron generated: '{cron_expression}'")

            # Prepare optional data for frontend if needed (e.g., for calendar updates)
            event_action_data = {
                "action": "add" if function_name == "add_event" else "update",
                "id": arguments.get("id") if function_name == "update_event" else None, # Use the string ID
                "title": title,
                "description": description,
                "cron_expression": cron_expression,
                "repeat_type": repeat_type,
                "original_date": final_date_str, # The specific date instance calculated/used
                "original_time": time_str,
                "participants": arguments.get("participants", [])
            }

        # --- Assign creator/updater ID ---
        if current_member_id:
            if function_name == "add_event" or function_name == "add_note":
                arguments["created_by"] = current_member_id
            elif function_name == "add_family_member":
                 pass # ID assigned by function
            elif function_name == "update_event":
                 arguments["updated_by"] = current_member_id # Add if needed

        # --- Execute the function ---
        if function_name in tool_functions:
            func_to_call = tool_functions[function_name]
            try:
                result = func_to_call(arguments) # Pass the modified arguments
                if result is False:
                     tool_result_content = f"Thất bại khi thực thi {function_name}. Chi tiết lỗi đã được ghi lại."
                     logger.error(f"Execution failed for tool {function_name} with args {arguments}")
                     event_action_data = None # Clear frontend data on failure
                else:
                     tool_result_content = f"Đã thực thi thành công {function_name}."
                     logger.info(f"Successfully executed tool {function_name}")
                     # Specific message/data for delete
                     if function_name == "delete_event":
                         deleted_event_id = arguments.get("event_id") # ID should be in args now
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
    # Load history from client if provided and session is empty
    if chat_request.messages is not None and not session.get("messages"):
         logger.info(f"Loading message history from client for session {chat_request.session_id}")
         # Validate client messages before accepting?
         session["messages"] = [msg.dict(exclude_none=True) for msg in chat_request.messages]

    # Process the new incoming message
    message_content_model = chat_request.message
    message_dict = message_content_model.dict(exclude_none=True)

    logger.info(f"Nhận request với content_type: {chat_request.content_type}")
    processed_content_list = [] # Store content in the format OpenAI expects (list of dicts)

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
        processed_content_list.append({"type": "image_url", "image_url": message_dict["image_url"]})
        # Add accompanying text if provided in the *same* message content object
        if message_dict.get("text"):
             processed_content_list.append({"type": "text", "text": message_dict["text"]})

    elif message_dict.get("type") == "html":
         clean_text = re.sub(r'<[^>]*>', ' ', message_dict["html"])
         clean_text = unescape(clean_text) # Handle HTML entities like &amp;
         clean_text = re.sub(r'\s+', ' ', clean_text).strip()
         processed_content_list.append({"type": "text", "text": clean_text})
         logger.info(f"Đã xử lý HTML thành text: {clean_text[:50]}...")

    elif message_dict.get("type") == "text": # Default text type
        processed_content_list.append({"type": "text", "text": message_dict["text"]})

    else: # Handle unknown types?
         logger.warning(f"Loại nội dung không xác định trong request: {message_dict.get('type')}")
         processed_content_list.append({"type": "text", "text": "[Nội dung không hỗ trợ]"})


    # Add the processed user message to the session history
    if processed_content_list:
         session["messages"].append({
             "role": "user",
             "content": processed_content_list
         })
    else:
         logger.error("Không thể xử lý nội dung tin nhắn người dùng.")
         # Maybe raise error or return immediately?


    # --- Tool Calling Flow ---
    final_event_data_to_return: Optional[Dict[str, Any]] = None

    try:
        client = OpenAI(api_key=openai_api_key)
        system_prompt_content = build_system_prompt(current_member_id)

        # Prepare messages for OpenAI API from session history
        openai_messages = [{"role": "system", "content": system_prompt_content}]
        for msg in session["messages"]:
             # Ensure content format is compatible with OpenAI API
             message_for_api = {
                 "role": msg["role"],
                 # Add tool calls/id if they exist from previous assistant/tool turns
                 **({ "tool_calls": msg["tool_calls"] } if msg.get("tool_calls") else {}),
                 **({ "tool_call_id": msg.get("tool_call_id") } if msg.get("tool_call_id") else {}),
             }
             # Handle content: should be string for text-only, list for multimodal/tool results
             msg_content = msg.get("content")
             if isinstance(msg_content, list):
                  # Assume list content is correctly formatted [{type:..., ...}]
                  message_for_api["content"] = msg_content
             elif isinstance(msg_content, str):
                  # Simple text content
                  message_for_api["content"] = msg_content
             elif msg.get("role") == "tool":
                  # Tool results have content as string
                  message_for_api["content"] = str(msg_content) if msg_content is not None else ""
             else:
                  # Handle unexpected content format
                  logger.warning(f"Định dạng content không mong đợi cho role {msg['role']}: {type(msg_content)}. Sử dụng chuỗi rỗng.")
                  message_for_api["content"] = ""


             openai_messages.append(message_for_api)


        # --- Check Search Need ---
        search_result_for_prompt = await check_search_need(openai_messages, openai_api_key, tavily_api_key)
        if search_result_for_prompt:
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
        # Add assistant's response (potentially with tool calls) to history
        session["messages"].append(response_message.dict(exclude_none=True))


        # --- Handle Tool Calls ---
        tool_calls = response_message.tool_calls
        if tool_calls:
            logger.info(f"--- Tool Calls Detected: {len(tool_calls)} ---")
            messages_for_second_call = openai_messages + [response_message.dict(exclude_none=True)] # History for next call

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
                session["messages"].append(tool_result_message) # Add tool result to session log

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
            # Assistant message already added above

        # --- Final Processing & Response ---
        final_html_content = final_response_content if final_response_content else "Tôi đã thực hiện xong yêu cầu của bạn." # Fallback message

        audio_response_b64 = text_to_speech_google(final_html_content)

        # Save history & Update session AFTER the full interaction
        if current_member_id:
             summary = generate_chat_summary(session["messages"], openai_api_key)
             save_chat_history(current_member_id, session["messages"], summary, chat_request.session_id)

        session_manager.update_session(chat_request.session_id, {"messages": session["messages"]})

        # Get the *very last* message (should be assistant's final summary or direct response)
        last_message_dict = session["messages"][-1] if session["messages"] else {}
        last_assistant_msg_obj = None
        if last_message_dict.get("role") == "assistant":
             # Convert dict to Pydantic Message model for response structure
             try:
                  # Ensure content is in the right format (list for multimodal/text, str is okay too)
                  content = last_message_dict.get("content")
                  content_for_model = []
                  if isinstance(content, str):
                       content_for_model.append(MessageContent(type="html", html=content))
                  elif isinstance(content, list):
                       # Basic check if items look like MessageContent structure
                       if all(isinstance(item, dict) and 'type' in item for item in content):
                           content_for_model = [MessageContent(**item) for item in content]
                       else: # Attempt to force into text/html if structure is wrong
                            logger.warning("Assistant message content list has unexpected structure. Converting to text.")
                            content_text = " ".join(map(str, content))
                            content_for_model.append(MessageContent(type="html", html=content_text))

                  else: # Handle None or other types
                       content_for_model.append(MessageContent(type="html", html=""))


                  last_assistant_msg_obj = Message(
                      role="assistant",
                      content=content_for_model,
                      tool_calls=last_message_dict.get("tool_calls") # Include tool_calls if they were part of this message
                  )
             except Exception as model_err:
                  logger.error(f"Error creating response Message object: {model_err}", exc_info=True)


        if not last_assistant_msg_obj:
             fallback_content = MessageContent(type="html", html="Đã có lỗi xảy ra hoặc không có phản hồi.")
             last_assistant_msg_obj = Message(role="assistant", content=[fallback_content])

        return ChatResponse(
            session_id=chat_request.session_id,
            messages=[last_assistant_msg_obj], # Return only the last assistant message
            audio_response=audio_response_b64,
            response_format="html",
            content_type=chat_request.content_type,
            event_data=final_event_data_to_return
        )

    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng trong /chat endpoint: {str(e)}", exc_info=True)
        # Try saving session even on error
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
    # Load history from client if provided and session is empty
    if chat_request.messages is not None and not session.get("messages"):
         logger.info(f"Stream: Loading message history from client for session {chat_request.session_id}")
         session["messages"] = [msg.dict(exclude_none=True) for msg in chat_request.messages]

    # Process the new incoming message
    message_content_model = chat_request.message
    message_dict = message_content_model.dict(exclude_none=True)
    logger.info(f"Stream: Nhận request với content_type: {chat_request.content_type}")
    processed_content_list = [] # Store content in the format OpenAI expects

    # --- Process incoming message based on content_type ---
    if chat_request.content_type == "audio" and message_dict.get("type") == "audio" and message_dict.get("audio_data"): # <<< SỬA ĐÂY
        processed_audio = process_audio(message_dict, openai_api_key)
        if processed_audio and processed_audio.get("text"):
             processed_content_list.append({"type": "text", "text": processed_audio["text"]})
             logger.info(f"Stream: Đã xử lý audio thành text: {processed_audio['text'][:50]}...")
        else:
             logger.error("Stream: Xử lý audio thất bại.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý audio]"})

    elif chat_request.content_type == "image" and message_dict.get("type") == "image_url": # <<< SỬA ĐÂY
        logger.info(f"Stream: Đã nhận hình ảnh: {message_dict.get('image_url', {}).get('url', '')[:60]}...")
        # Ensure image_url exists before appending
        if message_dict.get("image_url"):
            processed_content_list.append({"type": "image_url", "image_url": message_dict["image_url"]})
        else:
            logger.error("Stream: Content type là image nhưng thiếu image_url.")
            processed_content_list.append({"type": "text", "text": "[Lỗi xử lý ảnh: thiếu URL]"})

        # Add accompanying text if provided
        if message_dict.get("text"):
             processed_content_list.append({"type": "text", "text": message_dict["text"]})

    elif message_dict.get("type") == "html": # <<< SỬA ĐÂY
         if message_dict.get("html"):
            clean_text = re.sub(r'<[^>]*>', ' ', message_dict["html"])
            clean_text = unescape(clean_text) # Handle HTML entities
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            processed_content_list.append({"type": "text", "text": clean_text})
            logger.info(f"Stream: Đã xử lý HTML thành text: {clean_text[:50]}...")
         else:
             logger.warning("Stream: Loại nội dung là html nhưng thiếu trường 'html'.")
             processed_content_list.append({"type": "text", "text": "[Lỗi xử lý HTML: thiếu nội dung]"})


    elif message_dict.get("type") == "text": # <<< SỬA ĐÂY (Thêm kiểm tra text tồn tại)
        text_content = message_dict.get("text")
        if text_content:
            processed_content_list.append({"type": "text", "text": text_content})
        else:
             logger.warning("Stream: Loại nội dung là text nhưng thiếu trường 'text'.")
             # Decide if an empty text message is valid or an error
             # processed_content_list.append({"type": "text", "text": ""}) # Allow empty text?

    else: # <<< SỬA ĐÂY
         logger.warning(f"Stream: Loại nội dung không xác định hoặc thiếu dữ liệu: {message_dict.get('type')}")
         processed_content_list.append({"type": "text", "text": "[Nội dung không hỗ trợ hoặc bị lỗi]"})
    # --- End of message processing ---

    # Add the processed user message to the session history only if content was processed
    if processed_content_list:
         session["messages"].append({
             "role": "user",
             "content": processed_content_list
         })
    else:
         # If message processing failed completely, maybe don't proceed?
         logger.error("Stream: Không thể xử lý nội dung tin nhắn đến. Không thêm vào lịch sử.")
         # Consider how to handle this - maybe return an error response immediately?
         # For now, we let the generator handle it, but it might start with bad history.

    # --- Streaming Generator (Bắt đầu từ đây) ---
    async def response_stream_generator():
        # ... (Phần còn lại của hàm generator giữ nguyên như phiên bản trước) ...
        # Initialize necessary variables within the generator's scope
        final_event_data_to_return: Optional[Dict[str, Any]] = None # <<< Initialize event_data
        client = OpenAI(api_key=openai_api_key)
        system_prompt_content = build_system_prompt(current_member_id) # Build fresh prompt content

        # --- Prepare initial messages list from session ---
        # (This assumes session["messages"] was correctly updated just before calling this generator)
        openai_messages = [{"role": "system", "content": system_prompt_content}]
        for msg in session["messages"]:
             # Adapt message format for API call (same logic as non-stream)
             message_for_api = {
                 "role": msg["role"],
                 **({ "tool_calls": msg["tool_calls"] } if msg.get("tool_calls") else {}),
                 **({ "tool_call_id": msg.get("tool_call_id") } if msg.get("tool_call_id") else {}),
             }
             msg_content = msg.get("content")
             if isinstance(msg_content, list): message_for_api["content"] = msg_content
             elif isinstance(msg_content, str): message_for_api["content"] = msg_content
             elif msg.get("role") == "tool": message_for_api["content"] = str(msg_content) if msg_content is not None else ""
             else: message_for_api["content"] = "" # Default empty content if format is wrong
             openai_messages.append(message_for_api)

        # --- Check Search Need ---
        try:
             search_result_for_prompt = await check_search_need(openai_messages, openai_api_key, tavily_api_key)
             if search_result_for_prompt:
                  # Update system prompt in the list
                  openai_messages[0] = {"role": "system", "content": system_prompt_content + search_result_for_prompt}
        except Exception as search_err:
             logger.error(f"Error during search need check: {search_err}", exc_info=True)
             # Proceed without search results, maybe log or yield an error?

        # Variables for streaming state management
        accumulated_tool_calls = []
        accumulated_assistant_content = ""
        assistant_message_dict_for_session = {"role": "assistant", "content": None, "tool_calls": None}
        tool_call_chunks = {} # {index: {"id": ..., "type": ..., "function": {"name":..., "arguments": ...}}}

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
                delta = chunk.choices[0].delta if chunk.choices else None # Handle empty choices list
                if not delta: continue # Skip empty deltas

                finish_reason = chunk.choices[0].finish_reason

                # Accumulate content and yield chunks
                if delta.content:
                    accumulated_assistant_content += delta.content
                    yield json.dumps({"chunk": delta.content, "type": "html", "content_type": chat_request.content_type}) + "\n"
                    await asyncio.sleep(0) # Yield control to event loop

                # Accumulate tool call parts
                if delta.tool_calls:
                    for tc_chunk in delta.tool_calls:
                        index = tc_chunk.index
                        if index not in tool_call_chunks:
                            tool_call_chunks[index] = {"function": {"arguments": ""}}
                        # Safely update fields
                        if tc_chunk.id: tool_call_chunks[index]["id"] = tc_chunk.id
                        if tc_chunk.type: tool_call_chunks[index]["type"] = tc_chunk.type
                        if tc_chunk.function:
                             if tc_chunk.function.name: tool_call_chunks[index]["function"]["name"] = tc_chunk.function.name
                             if tc_chunk.function.arguments: tool_call_chunks[index]["function"]["arguments"] += tc_chunk.function.arguments

                # Process when the first call finishes
                if finish_reason:
                    if finish_reason == "tool_calls":
                        logger.info("--- Stream detected tool_calls ---")
                        # Reconstruct full tool calls
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

                    break # Exit the first stream processing loop

            # --- Execute Tools and Second Stream (if needed) ---
            if accumulated_tool_calls:
                logger.info(f"--- Executing {len(accumulated_tool_calls)} Tool Calls (Non-Streamed) ---")
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

                session["messages"].append({"role": "assistant", "content": final_summary_content})
                final_response_for_tts = final_summary_content if final_summary_content else "Đã xử lý xong."

            else:
                session["messages"].append(assistant_message_dict_for_session)
                final_response_for_tts = accumulated_assistant_content if accumulated_assistant_content else "Vâng."

            # --- Post-Streaming Processing ---
            logger.info("Generating final audio response...")
            audio_response_b64 = text_to_speech_google(final_response_for_tts)

            if current_member_id:
                 summary = generate_chat_summary(session["messages"], openai_api_key)
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

    # Return the StreamingResponse object, wrapping the generator
    return StreamingResponse(
        response_stream_generator(),
        media_type="application/x-ndjson" # Use newline-delimited JSON for streaming
    )


# ------- Other Helper Functions --------

# --- Audio Processing ---
def process_audio(message_dict, api_key):
    """Chuyển đổi audio base64 sang text dùng Whisper."""
    try:
        if not message_dict.get("audio_data"):
            logger.error("process_audio: Thiếu audio_data.")
            return None
        audio_data = base64.b64decode(message_dict["audio_data"])

        # Save to a temporary file (consider file extension if known, else use .wav or .mp3)
        file_extension = ".wav" # Default, adjust if possible
        temp_audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}{file_extension}")

        with open(temp_audio_path, "wb") as f:
            f.write(audio_data)

        client = OpenAI(api_key=api_key)
        with open(temp_audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        os.remove(temp_audio_path) # Clean up temp file

        return {"type": "text", "text": transcript.text}

    except base64.binascii.Error as b64_err:
        logger.error(f"Lỗi giải mã Base64 audio: {b64_err}")
        return None
    except Exception as e:
        logger.error(f"Lỗi khi xử lý audio: {e}", exc_info=True)
        # Clean up temp file if it exists and error occurred after creation
        if 'temp_audio_path' in locals() and os.path.exists(temp_audio_path):
             try: os.remove(temp_audio_path)
             except OSError: pass
        return None

# --- System Prompt Builder ---
def build_system_prompt(current_member_id=None):
    """Xây dựng system prompt cho trợ lý gia đình (sử dụng Tool Calling)."""
    # Start with the base persona and instructions
    system_prompt_parts = [
        "Bạn là trợ lý gia đình thông minh, đa năng và thân thiện tên là HGDS. Nhiệm vụ của bạn là giúp quản lý thông tin gia đình, sự kiện, ghi chú, trả lời câu hỏi, tìm kiếm thông tin, phân tích hình ảnh và đưa ra lời khuyên hữu ích.",
        "Giao tiếp tự nhiên, lịch sự và theo phong cách trò chuyện bằng tiếng Việt.",
        "Sử dụng định dạng HTML đơn giản cho phản hồi văn bản (thẻ p, b, i, ul, li, h3, h4, br).",
        f"Hôm nay là {datetime.datetime.now().strftime('%A, %d/%m/%Y')}.", # Vietnamese day name
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
        "3.  **Tìm kiếm & Thời tiết:** Sử dụng thông tin tìm kiếm hoặc thời tiết được cung cấp trong context (đánh dấu bằng --- THÔNG TIN ---) để trả lời các câu hỏi liên quan. Đừng gọi công cụ nếu thông tin đã có sẵn.",
        "4.  **Phân tích hình ảnh:** Khi nhận được hình ảnh, hãy mô tả nó và liên kết với thông tin gia đình nếu phù hợp.",
        "5.  **Xác nhận:** Sau khi sử dụng công cụ thành công (nhận được kết quả từ 'tool role'), hãy thông báo ngắn gọn cho người dùng biết hành động đã được thực hiện dựa trên kết quả đó. Nếu tool thất bại, hãy thông báo lỗi một cách lịch sự."
        "6. **Độ dài phản hồi:** Giữ phản hồi cuối cùng cho người dùng tương đối ngắn gọn và tập trung vào yêu cầu chính, trừ khi được yêu cầu chi tiết."
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


    # Add data context (Limit size if data grows large)
    # Maybe summarize data instead of dumping everything?
    data_summary = {
         "Thành viên": list(family_data.keys()),
         "Số sự kiện": len(events_data),
         "Số ghi chú": len(notes_data)
    }
    # Example: Show details only for a few recent events?
    recent_events_summary = {}
    try:
         # Sort events by created_on (assuming ISO format) descending
         sorted_event_ids = sorted(
             events_data.keys(),
             key=lambda eid: events_data[eid].get("created_on", ""),
             reverse=True
         )
         for eid in sorted_event_ids[:3]: # Show last 3 added
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
    # Alternative: Full data dump (use with caution for large data)
    # data_context = f"""
    # \n**Dữ Liệu Hiện Tại:**
    # *   Thành viên gia đình: {json.dumps(family_data, ensure_ascii=False, indent=2, default=str)}
    # *   Sự kiện: {json.dumps(events_data, ensure_ascii=False, indent=2, default=str)}
    # *   Ghi chú: {json.dumps(notes_data, ensure_ascii=False, indent=2, default=str)}
    # """
    system_prompt_parts.append(data_context)

    return "\n".join(system_prompt_parts)


# --- Search & Summarize Helpers ---
async def check_search_need(messages: List[Dict], openai_api_key: str, tavily_api_key: str) -> str:
    """Kiểm tra nhu cầu tìm kiếm, thời tiết, tư vấn từ tin nhắn cuối của người dùng."""
    if not tavily_api_key: return "" # Need Tavily for web search part

    last_user_message_content = None
    # Find the last message from the user
    for message in reversed(messages):
        if message["role"] == "user":
            last_user_message_content = message["content"]
            break

    if not last_user_message_content: return ""

    # Extract text content from the last user message
    last_user_text = ""
    if isinstance(last_user_message_content, str):
        last_user_text = last_user_message_content
    elif isinstance(last_user_message_content, list):
        # Find the first text part
        for item in last_user_message_content:
             if isinstance(item, dict) and item.get("type") == "text":
                  last_user_text = item.get("text", "")
                  break # Use the first text part found

    if not last_user_text: return "" # No text found to analyze

    logger.info(f"Checking search/weather/advice need for: '{last_user_text[:100]}...'")

    # 1. Check for Advice Query
    is_advice_query, advice_type, time_term = weather_advisor.detect_advice_query(last_user_text)
    if is_advice_query:
        logger.info(f"Phát hiện truy vấn tư vấn: loại={advice_type}, thời gian={time_term}")
        target_date = get_date_from_relative_term(time_term) if time_term else None

        # Extract location for advice context
        location = "Hà Nội" # Default
        loc_pattern = r'(ở|tại)\s+([^?.,!\n]+)'
        loc_match = re.search(loc_pattern, last_user_text.lower())
        if loc_match: location = loc_match.group(2).strip().title()

        try:
            weather_data = await weather_service.get_weather(location, forecast_days=7, target_date=target_date) # Get enough forecast data
            if not weather_data or weather_data.get("error"):
                 logger.warning(f"Không thể lấy dữ liệu thời tiết cho tư vấn tại {location}.")
                 # Fall through to general search if weather fails
            else:
                 advice_result = ""
                 if advice_type == "clothing": advice_result = weather_advisor.get_clothing_advice(weather_data, target_date)
                 elif advice_type == "activity": advice_result = weather_advisor.get_activity_advice(weather_data, target_date)
                 elif advice_type == "items": advice_result = weather_advisor.get_items_advice(weather_data, target_date)

                 # Get weather summary for context, marked as non-displayable
                 weather_context_summary = weather_service.format_weather_message(weather_data, location, 1, target_date, advice_only=True)

                 advice_prompt_addition = f"""
                 \n\n--- THÔNG TIN TƯ VẤN THỜI TIẾT (DÙNG ĐỂ THAM KHẢO) ---
                 Người dùng hỏi: "{last_user_text}" (Loại: {advice_type}, Thời gian: {time_term}, Vị trí: {location})
                 Tóm tắt thời tiết liên quan: {weather_context_summary}
                 Lời khuyên được tạo:
                 {advice_result}
                 --- KẾT THÚC TƯ VẤN ---
                 Hãy trả lời người dùng bằng cách diễn đạt lại phần "Lời khuyên được tạo" một cách tự nhiên. KHÔNG đưa ra các chỉ số thời tiết cụ thể trừ khi lời khuyên có đề cập.
                 """
                 return advice_prompt_addition
        except Exception as advice_err:
            logger.error(f"Lỗi khi xử lý truy vấn tư vấn: {advice_err}", exc_info=True)
            # Fall through to general search on error

    # 2. Check for Weather Query (if not advice)
    is_weather_query, location, days, time_term_weather = weather_service.detect_weather_query(last_user_text)
    if is_weather_query and location:
        logger.info(f"Phát hiện truy vấn thời tiết: vị trí={location}, cụm từ='{time_term_weather}'")
        target_date = get_date_from_relative_term(time_term_weather) if time_term_weather else None
        try:
            weather_data = await weather_service.get_weather(location, days=7, target_date=target_date) # Get enough data
            if not weather_data or weather_data.get("error"):
                 logger.warning(f"Không thể lấy dữ liệu thời tiết cho {location} từ API.")
                 # Fall through to search as backup
            else:
                 weather_html = weather_service.format_weather_message(weather_data, location, days, target_date) # Format requested days
                 weather_prompt_addition = f"""
                 \n\n--- THÔNG TIN THỜI TIẾT (DÙNG ĐỂ TRẢ LỜI) ---
                 Người dùng hỏi: "{last_user_text}"
                 Dự báo chi tiết:
                 {weather_html}
                 --- KẾT THÚC THÔNG TIN THỜI TIẾT ---
                 Hãy trình bày thông tin thời tiết chi tiết ở trên cho người dùng một cách rõ ràng, thân thiện.
                 """
                 return weather_prompt_addition
        except Exception as weather_err:
             logger.error(f"Lỗi khi lấy/định dạng thông tin thời tiết: {weather_err}", exc_info=True)
             # Fall through to general search on error

    # 3. Check for General Search Intent (if not weather/advice)
    if tavily_api_key: # Only proceed if Tavily key exists
         need_search, search_query, is_news_query = await detect_search_intent(last_user_text, openai_api_key) # Changed to await
         if need_search:
             logger.info(f"Phát hiện nhu cầu tìm kiếm: query='{search_query}', is_news={is_news_query}")
             domains_to_include = VIETNAMESE_NEWS_DOMAINS if is_news_query else None
             try:
                search_summary = await search_and_summarize( # Changed to await
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

    # No specific need detected
    logger.info("Không phát hiện nhu cầu tìm kiếm/thời tiết/tư vấn đặc biệt.")
    return ""

# Make Tavily functions async if they use async libs, or wrap sync calls
# Assuming Tavily client might be sync, wrap with asyncio.to_thread
# For simplicity, keeping them sync for now, but mark as needing async upgrade

async def tavily_extract(api_key, urls, include_images=False, extract_depth="basic"):
    """Trích xuất nội dung từ URL (Wrap sync call)."""
    # ... (keep internal sync logic) ...
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
    # ... (keep internal sync logic) ...
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
    """Tìm kiếm và tổng hợp (đã là async do gọi các hàm async con)."""
    if not tavily_api_key or not openai_api_key or not query:
        return "Thiếu thông tin API key hoặc câu truy vấn."

    try:
        logger.info(f"Bắt đầu tìm kiếm Tavily cho: '{query}'" + (f" (Domains: {include_domains})" if include_domains else ""))
        search_results = await tavily_search(
            tavily_api_key, query, include_domains=include_domains, max_results=5 # Limit results
        )

        if not search_results or not search_results.get("results"):
            logger.warning(f"Không tìm thấy kết quả Tavily cho '{query}'")
            return f"Xin lỗi, tôi không tìm thấy kết quả nào cho '{query}'" + (f" trong các trang tin tức được chỉ định." if include_domains else ".")

        # Extract content from top results more efficiently
        urls_to_extract = [result["url"] for result in search_results["results"][:3]] # Extract top 3
        if not urls_to_extract:
            logger.warning(f"Không có URL nào để trích xuất từ kết quả Tavily cho '{query}'.")
            return f"Đã tìm thấy một số tiêu đề liên quan đến '{query}' nhưng không thể trích xuất nội dung."

        logger.info(f"Trích xuất nội dung từ URLs: {urls_to_extract}")
        # Use tavily_extract which now supports list of URLs
        extract_result = await tavily_extract(tavily_api_key, urls_to_extract) # Pass list directly

        extracted_contents = []
        if extract_result and extract_result.get("results"):
             for res in extract_result["results"]:
                  content = res.get("raw_content", "")
                  if content: # Only add if content exists
                       # Limit length per source
                       max_len_per_source = 4000
                       content = content[:max_len_per_source] + "..." if len(content) > max_len_per_source else content
                       extracted_contents.append({"url": res.get("url"), "content": content})
                  else:
                       logger.warning(f"Nội dung trống rỗng từ URL: {res.get('url')}")
        else:
             logger.warning(f"Trích xuất nội dung thất bại từ Tavily cho URLs: {urls_to_extract}")
             # Fallback: use titles/snippets from search results?
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

        # Summarize using OpenAI
        logger.info(f"Tổng hợp {len(extracted_contents)} nguồn trích xuất cho '{query}'.")
        client = OpenAI(api_key=openai_api_key)

        # Prepare content string for prompt, limiting total length
        content_for_prompt = ""
        total_len = 0
        max_total_len = 15000 # Limit total context length for summarization
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
            response = await asyncio.to_thread( # Run sync completion in thread
                 client.chat.completions.create,
                 model=openai_model, # Use a model good at summarization
                 messages=[
                     {"role": "system", "content": "Bạn là một trợ lý tổng hợp thông tin chuyên nghiệp. Nhiệm vụ của bạn là tổng hợp nội dung từ các nguồn được cung cấp để tạo ra một bản tóm tắt chính xác, tập trung vào yêu cầu của người dùng và trích dẫn nguồn nếu có thể."},
                     {"role": "user", "content": prompt}
                 ],
                 temperature=0.3,
                 max_tokens=1500 # Allow longer summary if needed
            )
            summarized_info = response.choices[0].message.content

            # Optionally add a footer with all sources if not included in summary
            # sources_footer = "\n\n<p><b>Nguồn tham khảo:</b></p><ul>" + "".join([f"<li><a href='{c['url']}' target='_blank'>{c['url']}</a></li>" for c in extracted_contents]) + "</ul>"
            # if not any(c['url'] in summarized_info for c in extracted_contents):
            #      final_response = f"{summarized_info}{sources_footer}"
            # else:
            #      final_response = summarized_info

            return summarized_info.strip()

        except Exception as summary_err:
             logger.error(f"Lỗi khi gọi OpenAI để tổng hợp: {summary_err}", exc_info=True)
             # Fallback: return combined raw content? Or just error?
             # return "Lỗi khi tổng hợp thông tin. Nội dung gốc:\n" + content_for_prompt
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
        # Prompt remains the same, instructing JSON output
        system_prompt = f"""
Bạn là một hệ thống phân loại và tinh chỉnh câu hỏi thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có cần tìm kiếm thông tin thực tế, tin tức mới hoặc dữ liệu cập nhật không (`need_search`). Câu hỏi về kiến thức chung, định nghĩa đơn giản thường không cần tìm kiếm.
2. Nếu cần tìm kiếm, hãy tinh chỉnh câu hỏi thành một truy vấn tìm kiếm tối ưu (`search_query`), bao gồm yếu tố thời gian nếu có (hôm nay, 26/03...).
3. Xác định xem câu hỏi có chủ yếu về tin tức, thời sự, thể thao, sự kiện hiện tại không (`is_news_query`). Câu hỏi về thời tiết cũng coi là tin tức. Câu hỏi về giá cả, sản phẩm, hướng dẫn KHÔNG phải là tin tức.

Hôm nay là ngày: {current_date_str}.

Ví dụ:
- User: "tin tức covid hôm nay" -> {{ "need_search": true, "search_query": "tin tức covid mới nhất ngày {current_date_str}", "is_news_query": true }}
- User: "kết quả trận MU tối qua" -> {{ "need_search": true, "search_query": "kết quả Manchester United tối qua", "is_news_query": true }}
- User: "có phim gì hay tuần này?" -> {{ "need_search": true, "search_query": "phim chiếu rạp hay tuần này", "is_news_query": false }}
- User: "giá vàng SJC" -> {{ "need_search": true, "search_query": "giá vàng SJC mới nhất", "is_news_query": false }}
- User: "thủ đô nước Pháp là gì?" -> {{ "need_search": false, "search_query": "thủ đô nước Pháp là gì?", "is_news_query": false }}
- User: "thời tiết Hà Nội ngày mai" -> {{ "need_search": true, "search_query": "dự báo thời tiết Hà Nội ngày mai", "is_news_query": true }}
- User: "cách làm bánh cuốn" -> {{ "need_search": true, "search_query": "cách làm bánh cuốn ngon", "is_news_query": false }}
- User: "sin(pi/2) bằng mấy?" -> {{ "need_search": false, "search_query": "sin(pi/2)", "is_news_query": false }}


Trả lời DƯỚI DẠNG JSON HỢP LỆ với 3 trường: need_search (boolean), search_query (string), is_news_query (boolean).
"""
        # Run synchronous API call in a thread
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model=openai_model, # Or a faster model if available
             messages=[
                 {"role": "system", "content": system_prompt},
                 {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\""}
             ],
             temperature=0.1,
             max_tokens=150, # Shorter response needed
             response_format={"type": "json_object"}
        )

        result_str = response.choices[0].message.content
        logger.info(f"Kết quả detect_search_intent (raw): {result_str}")

        try:
            result = json.loads(result_str)
            need_search = result.get("need_search", False)
            search_query = query
            is_news_query = False

            if need_search:
                search_query = result.get("search_query", query)
                if not search_query: search_query = query # Fallback if empty
                is_news_query = result.get("is_news_query", False)

            logger.info(f"Phân tích truy vấn '{query}': need_search={need_search}, search_query='{search_query}', is_news_query={is_news_query}")
            return need_search, search_query, is_news_query

        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Lỗi giải mã JSON từ detect_search_intent: {e}. Raw: {result_str}")
            # Fallback: assume search needed for safety? Or not? Let's assume not.
            return False, query, False
    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_search_intent: {e}", exc_info=True)
        return False, query, False # Default to no search on error

# --- Suggested Questions ---
def generate_dynamic_suggested_questions(api_key, member_id=None, max_questions=5):
    """Tạo câu hỏi gợi ý động (sử dụng mẫu câu)."""
    # This function can remain largely the same as the template-based one
    # if calling OpenAI here is too slow or costly.
    # Re-pasting the template logic here for completeness.
    logger.info("Sử dụng phương pháp mẫu câu để tạo câu hỏi gợi ý")

    random_seed = int(hashlib.md5(f"{datetime.datetime.now().strftime('%Y-%m-%d_%H')}_{member_id or 'guest'}".encode()).hexdigest(), 16)
    random.seed(random_seed)

    question_templates = {
        "news": [ "Tin tức {topic} mới nhất?", "Có gì mới về {topic} hôm nay?", "Điểm tin {topic} sáng nay?", "Cập nhật tình hình {topic}?" ],
        "weather": [ "Thời tiết {location} ngày mai thế nào?", "Dự báo thời tiết {location} cuối tuần?", "{location} hôm nay có mưa không?" ],
        "events": [ "Sự kiện nổi bật tuần này?", "Lịch chiếu phim {cinema}?", "Trận đấu {team} tối nay mấy giờ?" ],
        "food": [ "Công thức nấu món {dish}?", "Quán {dish} ngon ở {district}?", "Cách làm {dessert} đơn giản?" ],
        "hobbies": [ "Sách hay về chủ đề {genre}?", "Mẹo chụp ảnh đẹp bằng điện thoại?", "Bài tập yoga giảm căng thẳng?" ],
        "general": [ "Kể một câu chuyện cười?", "Đố vui về {category}?", "Hôm nay có ngày gì đặc biệt?" ]
    }

    # Get member preferences if available
    prefs = {}
    if member_id and member_id in family_data:
         prefs = family_data[member_id].get("preferences", {})

    # Variables for templates
    replacements = {
        "topic": ["thế giới", "kinh tế", "thể thao", "giải trí", "công nghệ", "giáo dục", "y tế", prefs.get("hobby", "khoa học")],
        "location": [prefs.get("location", "Hà Nội"), "TP HCM", "Đà Nẵng", "nơi bạn ở"],
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
        # Simple replacement - find first placeholder and replace
        placeholder_match = re.search(r'\{(\w+)\}', question)
        while placeholder_match:
             key = placeholder_match.group(1)
             if key in replacements:
                  replacement = random.choice(replacements[key])
                  question = question.replace(placeholder_match.group(0), replacement, 1)
             else:
                  question = question.replace(placeholder_match.group(0), "...", 1) # Fallback
             placeholder_match = re.search(r'\{(\w+)\}', question) # Find next
        all_questions.append(question)

    # Select unique questions up to max_questions
    final_suggestions = []
    seen_suggestions = set()
    for q in all_questions:
         if len(final_suggestions) >= max_questions: break
         if q not in seen_suggestions:
              final_suggestions.append(q)
              seen_suggestions.add(q)

    # Ensure minimum number of questions if generation fails
    while len(final_suggestions) < max_questions and len(final_suggestions) < len(all_questions):
         q = random.choice(all_questions)
         if q not in seen_suggestions:
              final_suggestions.append(q)
              seen_suggestions.add(q)


    logger.info(f"Đã tạo {len(final_suggestions)} câu hỏi gợi ý bằng mẫu.")
    return final_suggestions


# --- Chat History ---
def generate_chat_summary(messages, api_key):
    """Tạo tóm tắt từ lịch sử trò chuyện (async wrapper)."""
    if not api_key or not messages or len(messages) < 2:
        return "Chưa đủ nội dung để tóm tắt."

    # Prepare conversation text, focusing on user and final assistant messages
    conversation_text = ""
    for msg in messages[-10:]: # Limit context for summary
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
             # Include tool result briefly
             text_content = f"[Tool {msg.get('name')} result: {str(content)[:50]}...]"

        if role and text_content:
             conversation_text += f"{role.capitalize()}: {text_content.strip()}\n"


    if not conversation_text: return "Không có nội dung text để tóm tắt."

    try:
        client = OpenAI(api_key=api_key)
        # Run sync completion in thread
        response = asyncio.run(asyncio.to_thread( # Need await here? No, run sync
             client.chat.completions.create,
             model=openai_model, # Use a fast model?
             messages=[
                 {"role": "system", "content": "Tóm tắt cuộc trò chuyện sau thành 1 câu ngắn gọn bằng tiếng Việt, nêu bật yêu cầu chính hoặc kết quả cuối cùng."},
                 {"role": "user", "content": conversation_text}
             ],
             temperature=0.2,
             max_tokens=100
        ))
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Lỗi khi tạo tóm tắt chat: {e}", exc_info=True)
        return "[Lỗi tóm tắt]"


def save_chat_history(member_id, messages, summary=None, session_id=None):
    """Lưu lịch sử chat cho member_id."""
    global chat_history
    if not member_id: return # Cannot save without member ID

    # Ensure the member_id key exists and is a list
    if member_id not in chat_history or not isinstance(chat_history[member_id], list):
        chat_history[member_id] = []

    history_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": messages, # Store the full message list for this session turn
        "summary": summary or "",
        "session_id": session_id
    }

    chat_history[member_id].insert(0, history_entry)

    # Limit history size per member
    max_history_per_member = 20
    if len(chat_history[member_id]) > max_history_per_member:
        chat_history[member_id] = chat_history[member_id][:max_history_per_member]

    if not save_data(CHAT_HISTORY_FILE, chat_history):
        logger.error(f"Lưu lịch sử chat cho member {member_id} thất bại.")


# --- Text to Speech ---
def text_to_speech_google(text, lang='vi', slow=False, max_length=5000):
    """Chuyển text thành audio base64 dùng gTTS."""
    try:
        # Clean HTML and limit length
        clean_text = re.sub(r'<[^>]*>', ' ', text)
        clean_text = unescape(clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()

        if not clean_text:
             logger.warning("TTS: Văn bản rỗng sau khi làm sạch.")
             return None

        if len(clean_text) > max_length:
            logger.warning(f"TTS: Văn bản quá dài ({len(clean_text)}), cắt ngắn còn {max_length}.")
            # Find last sentence end before max_length for cleaner cut
            cut_pos = clean_text.rfind('.', 0, max_length)
            if cut_pos == -1: cut_pos = clean_text.rfind('?', 0, max_length)
            if cut_pos == -1: cut_pos = clean_text.rfind('!', 0, max_length)
            if cut_pos == -1 or cut_pos < max_length // 2: cut_pos = max_length # Fallback to hard cut
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
        # Determine format, default to JPEG if unknown or problematic
        img_format = image_raw.format if image_raw.format else "JPEG"
        if img_format not in ["JPEG", "PNG", "GIF", "WEBP"]:
             logger.warning(f"Định dạng ảnh không được hỗ trợ trực tiếp '{img_format}', chuyển đổi sang JPEG.")
             img_format = "JPEG"
             # Ensure image is in RGB mode for JPEG saving
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
        "name": "Trợ lý Gia đình API (Tool Calling)", "version": "1.1.0",
        "description": "API cho ứng dụng Trợ lý Gia đình thông minh",
        "endpoints": ["/chat", "/chat/stream", "/suggested_questions", "/family_members", "/events", "/notes", "/search", "/session", "/weather/{location}", "/analyze_image", "/transcribe_audio", "/tts", "/chat_history/{member_id}"]
    }

# --- Family Members ---
@app.get("/family_members")
async def get_family_members():
    return family_data

@app.post("/family_members")
async def add_family_member_endpoint(member: MemberModel):
    """Thêm thành viên (qua endpoint trực tiếp)."""
    # This uses direct model binding, not tool calling
    details = member.dict()
    if add_family_member(details): # Call the same internal function
         # Find the newly added member's ID (assuming add_family_member sets it)
         # This is a bit indirect, maybe add_family_member should return the ID?
         new_member_id = None
         for mid, mdata in family_data.items():
              if mdata.get("name") == member.name and mdata.get("age") == member.age: # Simple match
                   new_member_id = mid
                   break
         if new_member_id:
             return {"id": new_member_id, "member": family_data[new_member_id]}
         else:
             # Should not happen if add succeeded
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
    # This uses direct model binding, not tool calling
    details = event.dict()
    details["created_by"] = member_id # Assign creator if provided via query param
    details["repeat_type"] = determine_repeat_type(details.get("description"), details.get("title")) # Determine type

    # Note: This endpoint expects YYYY-MM-DD date directly
    if add_event(details):
         # Find the newly added event ID
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
         # Find the newly added note ID
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
    # Sort by last_updated descending?
    sorted_sessions = sorted(sessions_info.items(), key=lambda item: item[1].get('last_updated', ''), reverse=True)
    return dict(sorted_sessions)


@app.delete("/cleanup_sessions")
async def cleanup_old_sessions_endpoint(days: int = 30): # Renamed endpoint
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
    openai_api_key: Optional[str] = None # Keep option to pass key if needed
):
    """Lấy câu hỏi gợi ý."""
    api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
    session = session_manager.get_session(session_id)
    current_member_id = member_id or session.get("current_member")

    # Using template-based generation for now
    suggested_questions = generate_dynamic_suggested_questions(api_key, current_member_id, max_questions=5)
    current_timestamp = datetime.datetime.now().isoformat()

    # Cache the suggestions in session
    session["suggested_question"] = suggested_questions
    session["question_timestamp"] = current_timestamp
    session_manager.update_session(session_id, session) # Save updated session

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
async def get_member_chat_history(member_id: str): # Renamed path param
    """Lấy lịch sử chat của một thành viên."""
    if member_id in chat_history:
        # Return limited number of history entries?
        return chat_history[member_id][:10] # Return last 10 conversations
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
    content_type: str = Form("image") # Keep for consistency?
):
    """Phân tích hình ảnh sử dụng OpenAI Vision."""
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File tải lên không phải là hình ảnh.")
    if not openai_api_key or "sk-" not in openai_api_key:
         raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ.")

    try:
        image_content = await file.read()
        # Use BytesIO directly without saving to temp file if possible
        img = Image.open(BytesIO(image_content))
        img_base64_url = get_image_base64(img)

        if not img_base64_url:
             raise HTTPException(status_code=500, detail="Không thể xử lý ảnh thành base64.")

        client = OpenAI(api_key=openai_api_key)
        # Run sync completion in thread
        response = await asyncio.to_thread(
             client.chat.completions.create,
             model="gpt-4o-mini", # Or gpt-4-vision-preview / gpt-4o
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
            "content_type": content_type, # Return the provided content type
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
         # Allow common non-standard types too? e.g., application/octet-stream
         logger.warning(f"Content-Type file audio không chuẩn: {file.content_type}. Vẫn thử xử lý.")
         # raise HTTPException(status_code=400, detail="File tải lên không phải là audio.")
    if not openai_api_key or "sk-" not in openai_api_key:
         raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ.")

    # Save to temp file as Whisper works best with files
    temp_audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}_{file.filename}")
    try:
        # Read content async
        audio_content = await file.read()
        with open(temp_audio_path, "wb") as f:
            f.write(audio_content)

        client = OpenAI(api_key=openai_api_key)
        # Run sync transcription in thread
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
        # Ensure temp file cleanup
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
                "format": "mp3", # gTTS typically outputs mp3
                "lang": lang,
                "provider": "Google TTS"
            }
        else:
            raise HTTPException(status_code=500, detail="Không thể tạo file âm thanh.")
    except Exception as e:
        logger.error(f"Lỗi trong text_to_speech_endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý TTS: {str(e)}")

# --- Endpoint Thời tiết Riêng biệt ---
@app.get("/weather/{location}")
async def get_weather_endpoint(
    location: str,
    days: int = 1,
    target_date: Optional[str] = None, # Add target_date parameter
    openweather_api_key: Optional[str] = None
):
    """Endpoint riêng biệt để lấy thông tin thời tiết."""
    api_key = openweather_api_key or os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
         raise HTTPException(status_code=400, detail="Thiếu API key cho OpenWeatherMap.")

    # Use the global weather_service instance or create temporary if needed?
    # Using global instance is better for caching
    if not weather_service.openweather_api_key:
         # Update the key of the global instance if provided via query
         weather_service.openweather_api_key = api_key
         logger.info("Cập nhật API key cho WeatherService từ request.")


    try:
        weather_data = await weather_service.get_weather(location, days, target_date=target_date)
        if weather_data.get("error"):
             raise HTTPException(status_code=404, detail=weather_data.get("message", "Không thể lấy dữ liệu thời tiết."))

        # Format message based on parameters
        weather_html = weather_service.format_weather_message(weather_data, location, days, target_date)

        return {
            "location": location_info.get("name", location) if (location_info := weather_data.get("location")) else location,
            "days_requested": days,
            "target_date": target_date,
            "formatted_html": weather_html,
            "raw_data": weather_data, # Optionally return raw data
            "status": "success"
        }
    except HTTPException as http_exc:
         raise http_exc # Re-raise exceptions from weather_service
    except Exception as e:
        logger.error(f"Lỗi không xác định trong get_weather_endpoint cho {location}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi máy chủ khi lấy thời tiết: {str(e)}")


# ----- Server Startup/Shutdown Hooks -----
@app.on_event("startup")
async def startup_event():
    """Các tác vụ cần thực hiện khi khởi động server."""
    logger.info("Khởi động Family Assistant API server (Tool Calling version)")
    # Data is loaded after function definitions now
    logger.info("Đã tải dữ liệu và sẵn sàng hoạt động.")

@app.on_event("shutdown")
async def shutdown_event():
    """Các tác vụ cần thực hiện khi đóng server."""
    logger.info("Đóng Family Assistant API server...")
    # Ensure all data is saved
    save_data(FAMILY_DATA_FILE, family_data)
    save_data(EVENTS_DATA_FILE, events_data)
    save_data(NOTES_DATA_FILE, notes_data)
    save_data(CHAT_HISTORY_FILE, chat_history)
    session_manager._save_sessions() # Save sessions explicitly on shutdown
    logger.info("Đã lưu dữ liệu. Server tắt.")


# ----- Main Execution Block -----
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trợ lý Gia đình API (Tool Calling)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Auto reload server on code changes")
    args = parser.parse_args()

    log_level = "debug" if args.reload else "info" # More logs during dev

    logger.info(f"Khởi động Trợ lý Gia đình API (Tool Calling) trên http://{args.host}:{args.port}")

    # Use uvicorn.run directly for better control potentially
    uvicorn.run(
        "app:app", # Reference the FastAPI app instance
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=log_level.lower() # Set log level for uvicorn
    )
