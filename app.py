from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Union
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
from openai import OpenAI
import shutil
import tempfile
from gtts import gTTS
from croniter import croniter
import re
from typing import Optional, Dict, Any, Tuple
# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập log
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler()])
logger = logging.getLogger('family_assistant_api')

logger = logging.getLogger('date_calculator')
logger = logging.getLogger('cron_generator')
logger = logging.getLogger('family_assistant_api.response_processor')
logger = logging.getLogger('weather_service')
logger = logging.getLogger('weather_advisor')
# Khởi tạo API
app = FastAPI(title="Trợ lý Gia đình API", 
              description="API cho Trợ lý Gia đình thông minh với khả năng xử lý text, hình ảnh và âm thanh",
              version="1.0.0")

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
    "vnexpress.net",    # VnExpress
    "tuoitre.vn",       # Tuổi Trẻ
    "thanhnien.vn",     # Thanh Niên
    "vietnamnet.vn",    # VietNamNet
    "vtv.vn",           # Đài Truyền hình Việt Nam
    "nhandan.vn",       # Báo Nhân Dân
    "baochinhphu.vn",   # Cổng Thông tin điện tử Chính phủ
    "laodong.vn",       # Báo Lao Động
    "tienphong.vn",     # Báo Tiền Phong
    "zingnews.vn",      # Cân nhắc nếu muốn thêm ZingNews
    "cand.com.vn",      # Công an Nhân dân
    "kenh14.vn",
    "baophapluat.vn",   # Báo Pháp luật Việt Nam
]

# Mô hình OpenAI
openai_model = "gpt-4o-mini"

# ------- Classes & Models -------------

class SessionManager:
    """Quản lý session và trạng thái cho mỗi client với khả năng lưu trạng thái"""
    
    def __init__(self, sessions_file="sessions_data.json"):
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
                        logger.warning(f"Dữ liệu session trong {self.sessions_file} không hợp lệ, khởi tạo lại")
        except Exception as e:
            logger.error(f"Lỗi khi tải session: {e}")
    
    def _save_sessions(self):
        """Lưu dữ liệu session vào file"""
        try:
            with open(self.sessions_file, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, ensure_ascii=False, indent=2)
            logger.info(f"Đã lưu {len(self.sessions)} session vào {self.sessions_file}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi lưu session: {e}")
            return False
        
    def get_session(self, session_id):
        """Lấy session hoặc tạo mới nếu chưa tồn tại"""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "current_member": None,
                "suggested_question": None,
                "process_suggested": False,
                "question_cache": {},
                "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._save_sessions()
        return self.sessions[session_id]
    
    def update_session(self, session_id, data):
        """Cập nhật dữ liệu session"""
        if session_id in self.sessions:
            self.sessions[session_id].update(data)
            # Cập nhật thời gian sửa đổi gần nhất
            self.sessions[session_id]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_sessions()
            return True
        return False
    
    def delete_session(self, session_id):
        """Xóa session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save_sessions()
            return True
        return False
    
    def cleanup_old_sessions(self, days_threshold=30):
        """Xóa các session cũ không hoạt động sau số ngày nhất định"""
        now = datetime.datetime.now()
        sessions_to_remove = []
        
        for session_id, session_data in self.sessions.items():
            last_updated = session_data.get("last_updated")
            if last_updated:
                try:
                    last_updated_date = datetime.datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
                    days_inactive = (now - last_updated_date).days
                    if days_inactive > days_threshold:
                        sessions_to_remove.append(session_id)
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý thời gian cho session {session_id}: {e}")
        
        # Xóa các session cũ
        for session_id in sessions_to_remove:
            del self.sessions[session_id]
        
        if sessions_to_remove:
            self._save_sessions()
            logger.info(f"Đã xóa {len(sessions_to_remove)} session cũ")

# Khởi tạo session manager
#session_manager = SessionManager()
session_manager = SessionManager(SESSIONS_DATA_FILE)

# Weather service

class WeatherService:
    """
    Dịch vụ lấy thông tin thời tiết chính xác sử dụng OpenWeatherMap API
    """
    # Bảng chuyển đổi tên ngày tiếng Việt sang số ngày trong tuần
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
        """
        Khởi tạo dịch vụ thời tiết với API key
        
        Args:
            openweather_api_key: OpenWeatherMap API key
        """
        self.openweather_api_key = openweather_api_key or os.getenv("OPENWEATHER_API_KEY", "")
        self.cache = {}  # Cache đơn giản để lưu trữ dữ liệu thời tiết
        self.cache_duration = 30 * 60  # 30 phút (tính bằng giây)
        
    def _get_cache_key(self, location: str, forecast_days: int = 1) -> str:
        """Tạo khóa cache duy nhất cho vị trí và số ngày dự báo"""
        return f"{location.lower()}_{forecast_days}_{datetime.datetime.now().strftime('%Y-%m-%d')}"
    
    def _is_cache_valid(self, timestamp: float) -> bool:
        """Kiểm tra xem cache có còn hiệu lực không"""
        return (datetime.datetime.now().timestamp() - timestamp) < self.cache_duration
        
    async def get_weather(self, location: str, forecast_days: int = 1, language: str = "vi", target_date: str = None) -> Dict[str, Any]:
        """
        Lấy thông tin thời tiết cho một vị trí cụ thể và ngày cụ thể
        
        Args:
            location: Tên thành phố/vị trí (ví dụ: "Hà Nội", "TP.HCM")
            forecast_days: Số ngày dự báo (1-7)
            language: Ngôn ngữ dữ liệu ("vi" cho tiếng Việt)
            target_date: Ngày cụ thể cần thông tin thời tiết (YYYY-MM-DD)
            
        Returns:
            Dict với dữ liệu thời tiết đã được chuẩn hóa
        """
        # Nếu có target_date, tính lại forecast_days để đảm bảo bao gồm ngày đó
        if target_date:
            try:
                target_date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
                today = datetime.datetime.now().date()
                days_difference = (target_date_obj - today).days
                
                # Đảm bảo forecast_days đủ để bao gồm ngày mục tiêu
                if days_difference >= 0:  # Ngày trong tương lai
                    forecast_days = max(forecast_days, days_difference + 1)
                    logger.info(f"Điều chỉnh forecast_days thành {forecast_days} để bao gồm ngày {target_date}")
                else:
                    logger.warning(f"Ngày yêu cầu {target_date} là trong quá khứ, không thể lấy dự báo")
            except Exception as e:
                logger.error(f"Lỗi khi phân tích target_date {target_date}: {e}")
        
        # Giới hạn forecast_days không quá 7 ngày (giới hạn của hầu hết API thời tiết)
        forecast_days = min(forecast_days, 7)
        
        # Kiểm tra cache trước
        cache_key = self._get_cache_key(location, forecast_days)
        if cache_key in self.cache and self._is_cache_valid(self.cache[cache_key].get("timestamp", 0)):
            logger.info(f"Sử dụng dữ liệu thời tiết từ cache cho {location}")
            return self.cache[cache_key].get("data", {})
        
        # Kiểm tra API key
        if not self.openweather_api_key or len(self.openweather_api_key) < 10:
            logger.error(f"API key không hợp lệ: {self.openweather_api_key}")
            return {
                "error": True,
                "message": f"API key OpenWeatherMap không hợp lệ hoặc chưa được cấu hình.",
                "recommendation": "Vui lòng bổ sung OPENWEATHER_API_KEY hợp lệ vào file .env"
            }
        
        # Thử phương pháp API đơn giản trước (weather)
        try:
            weather_data = await self._get_current_weather(location, language)
            forecast_data = await self._get_forecast(location, forecast_days, language)
            
            if weather_data and forecast_data:
                # Kết hợp dữ liệu hiện tại và dự báo
                combined_data = self._combine_weather_data(weather_data, forecast_data, forecast_days)
                self._update_cache(cache_key, combined_data)
                return combined_data
                
        except Exception as e:
            logger.error(f"Lỗi khi lấy dữ liệu thời tiết cơ bản: {str(e)}", exc_info=True)
            # Thử phương pháp API OneCall nếu phương pháp đơn giản thất bại
            
        # Thử với OneCall API (phức tạp hơn nhưng cung cấp nhiều dữ liệu hơn)
        try:
            weather_data = await self._get_weather_from_onecall(location, forecast_days, language)
            if weather_data:
                self._update_cache(cache_key, weather_data)
                return weather_data
        except Exception as e:
            logger.error(f"Lỗi khi lấy dữ liệu từ OpenWeatherMap OneCall: {str(e)}", exc_info=True)
        
        # Phương án dự phòng: Trả về thông báo lỗi
        error_msg = f"Không thể lấy thông tin thời tiết cho {location}"
        if target_date:
            error_msg += f" vào ngày {target_date}"
        
        return {
            "error": True,
            "message": f"{error_msg}. Có lỗi khi kết nối đến OpenWeatherMap API.",
            "recommendation": "Vui lòng kiểm tra kết nối mạng và API key."
        }
    
    def _update_cache(self, key: str, data: Dict[str, Any]) -> None:
        """Cập nhật cache với dữ liệu mới và timestamp"""
        self.cache[key] = {
            "data": data,
            "timestamp": datetime.datetime.now().timestamp()
        }
    
    async def _get_current_weather(self, location: str, language: str = "vi") -> Dict[str, Any]:
        """Lấy thông tin thời tiết hiện tại từ API cơ bản của OpenWeatherMap"""
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": location,
            "units": "metric",
            "lang": language,
            "appid": self.openweather_api_key
        }
        
        try:
            logger.info(f"Gọi API thời tiết hiện tại cho {location}")
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            data = response.json()
            logger.info(f"Nhận phản hồi API thành công cho {location}")
            return data
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            if response.status_code == 401:
                logger.error("API key không hợp lệ hoặc đã hết hạn")
            elif response.status_code == 404:
                logger.error(f"Không tìm thấy dữ liệu cho {location}")
            elif response.status_code == 429:
                logger.error("Vượt quá giới hạn gọi API")
            raise
        except Exception as e:
            logger.error(f"Lỗi khi gọi Weather API: {str(e)}")
            raise
    
    async def _get_forecast(self, location: str, days: int = 5, language: str = "vi") -> Dict[str, Any]:
        """Lấy dự báo thời tiết từ API cơ bản của OpenWeatherMap"""
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "q": location,
            "units": "metric",
            "lang": language,
            "appid": self.openweather_api_key
        }
        
        try:
            logger.info(f"Gọi API dự báo thời tiết cho {location}")
            response = requests.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Nhận phản hồi API dự báo thành công cho {location}")
            return data
        except Exception as e:
            logger.error(f"Lỗi khi gọi Forecast API: {str(e)}")
            raise
    
    def _combine_weather_data(self, current_data: Dict[str, Any], forecast_data: Dict[str, Any], days: int) -> Dict[str, Any]:
        """Kết hợp dữ liệu thời tiết hiện tại và dự báo thành định dạng chuẩn"""
        # Xử lý dữ liệu hiện tại
        try:
            standardized = {
                "location": {
                    "name": current_data.get("name", ""),
                    "country": current_data.get("sys", {}).get("country", ""),
                    "lat": current_data.get("coord", {}).get("lat", 0),
                    "lon": current_data.get("coord", {}).get("lon", 0),
                    "localtime": datetime.datetime.fromtimestamp(current_data.get("dt", 0)).strftime("%Y-%m-%d %H:%M")
                },
                "current": {
                    "temp_c": current_data.get("main", {}).get("temp", 0),
                    "temp_f": current_data.get("main", {}).get("temp", 0) * 9/5 + 32,
                    "is_day": 1 if 6 <= datetime.datetime.now().hour < 18 else 0,
                    "condition": {
                        "text": current_data.get("weather", [{}])[0].get("description", ""),
                        "icon": f"https://openweathermap.org/img/wn/{current_data.get('weather', [{}])[0].get('icon', '01d')}@2x.png"
                    },
                    "wind_kph": current_data.get("wind", {}).get("speed", 0) * 3.6,  # m/s sang km/h
                    "wind_dir": self._get_wind_direction(current_data.get("wind", {}).get("deg", 0)),
                    "humidity": current_data.get("main", {}).get("humidity", 0),
                    "feelslike_c": current_data.get("main", {}).get("feels_like", 0),
                    "uv": current_data.get("uvi", 0)
                },
                "forecast": []
            }
            
            # Xử lý dữ liệu dự báo (5 ngày, 3 giờ một lần)
            # Nhóm dự báo theo ngày
            daily_forecasts = {}
            
            for item in forecast_data.get("list", []):
                date = datetime.datetime.fromtimestamp(item.get("dt", 0)).strftime("%Y-%m-%d")
                
                if date not in daily_forecasts:
                    daily_forecasts[date] = {
                        "date": date,
                        "temps": [],
                        "conditions": [],
                        "rain_chances": [],
                        "hourly": []
                    }
                
                daily_forecasts[date]["temps"].append(item.get("main", {}).get("temp", 0))
                daily_forecasts[date]["conditions"].append(item.get("weather", [{}])[0].get("description", ""))
                # Tính xác suất mưa dựa trên trường pop (probability of precipitation)
                daily_forecasts[date]["rain_chances"].append(item.get("pop", 0) * 100)
                
                # Thêm dữ liệu theo giờ
                hour_data = {
                    "time": datetime.datetime.fromtimestamp(item.get("dt", 0)).strftime("%H:%M"),
                    "temp_c": item.get("main", {}).get("temp", 0),
                    "condition": {
                        "text": item.get("weather", [{}])[0].get("description", ""),
                        "icon": f"https://openweathermap.org/img/wn/{item.get('weather', [{}])[0].get('icon', '01d')}@2x.png"
                    },
                    "chance_of_rain": item.get("pop", 0) * 100
                }
                daily_forecasts[date]["hourly"].append(hour_data)
            
            # Chuyển đổi dữ liệu thành định dạng cuối cùng
            for date, data in daily_forecasts.items():
                if len(standardized["forecast"]) >= days:
                    break
                    
                # Chọn điều kiện thời tiết chính cho ngày này (mode)
                conditions = data["conditions"]
                main_condition = max(set(conditions), key=conditions.count) if conditions else ""
                
                day_forecast = {
                    "date": date,
                    "max_temp_c": max(data["temps"]) if data["temps"] else 0,
                    "min_temp_c": min(data["temps"]) if data["temps"] else 0,
                    "condition": {
                        "text": main_condition,
                        "icon": f"https://openweathermap.org/img/wn/{'02d' if 'cloud' in main_condition else '01d' if 'clear' in main_condition else '10d' if 'rain' in main_condition else '50d'}@2x.png"
                    },
                    "chance_of_rain": max(data["rain_chances"]) if data["rain_chances"] else 0,
                    "hourly": data["hourly"][:8]  # Giới hạn 8 giờ/ngày
                }
                standardized["forecast"].append(day_forecast)
            
            return standardized
            
        except Exception as e:
            logger.error(f"Lỗi khi kết hợp dữ liệu thời tiết: {str(e)}", exc_info=True)
            raise
    
    async def _get_weather_from_onecall(self, location: str, forecast_days: int, language: str) -> Dict[str, Any]:
        """Lấy dữ liệu thời tiết từ OpenWeatherMap OneCall API và chuyển đổi sang định dạng chuẩn"""
        # Trước tiên phải lấy tọa độ từ tên vị trí
        try:
            geo_url = "https://api.openweathermap.org/geo/1.0/direct"
            geo_params = {
                "q": location,
                "limit": 1,
                "appid": self.openweather_api_key
            }
            
            logger.info(f"Gọi Geocoding API cho {location}")
            geo_response = requests.get(geo_url, params=geo_params)
            geo_response.raise_for_status()
            
            geo_data = geo_response.json()
            if not geo_data or len(geo_data) == 0:
                logger.error(f"Không tìm thấy tọa độ cho {location}")
                return {}
                
            geo_data = geo_data[0]
            lat, lon = geo_data.get("lat"), geo_data.get("lon")
            logger.info(f"Đã tìm thấy tọa độ cho {location}: {lat}, {lon}")
            
            # Gọi API thời tiết One Call
            url = "https://api.openweathermap.org/data/3.0/onecall"  # Sử dụng API v3.0
            params = {
                "lat": lat,
                "lon": lon,
                "exclude": "minutely",
                "units": "metric",
                "lang": language,
                "appid": self.openweather_api_key
            }
            
            logger.info(f"Gọi OneCall API cho {location}")
            response = requests.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Nhận phản hồi OneCall API thành công cho {location}")
            
            # Chuyển đổi sang định dạng chuẩn
            standardized = {
                "location": {
                    "name": geo_data.get("name", location),
                    "country": geo_data.get("country", ""),
                    "lat": lat,
                    "lon": lon,
                    "localtime": datetime.datetime.fromtimestamp(data["current"]["dt"]).strftime("%Y-%m-%d %H:%M")
                },
                "current": {
                    "temp_c": data["current"]["temp"],
                    "temp_f": data["current"]["temp"] * 9/5 + 32,
                    "is_day": 1 if 6 <= datetime.datetime.now().hour < 18 else 0,  # Ước tính
                    "condition": {
                        "text": data["current"]["weather"][0]["description"],
                        "icon": f"https://openweathermap.org/img/wn/{data['current']['weather'][0]['icon']}@2x.png"
                    },
                    "wind_kph": data["current"]["wind_speed"] * 3.6,  # m/s sang km/h
                    "wind_dir": self._get_wind_direction(data["current"]["wind_deg"]),
                    "humidity": data["current"]["humidity"],
                    "feelslike_c": data["current"]["feels_like"],
                    "uv": data["current"].get("uvi", 0)
                },
                "forecast": []
            }
            
            # Thêm dự báo theo ngày
            for i, day_data in enumerate(data["daily"][:forecast_days]):
                day_date = datetime.datetime.fromtimestamp(day_data["dt"])
                day_forecast = {
                    "date": day_date.strftime("%Y-%m-%d"),
                    "max_temp_c": day_data["temp"]["max"],
                    "min_temp_c": day_data["temp"]["min"],
                    "condition": {
                        "text": day_data["weather"][0]["description"],
                        "icon": f"https://openweathermap.org/img/wn/{day_data['weather'][0]['icon']}@2x.png"
                    },
                    "chance_of_rain": day_data.get("pop", 0) * 100,  # Xác suất mưa (0-1) sang phần trăm
                    "sunrise": datetime.datetime.fromtimestamp(day_data["sunrise"]).strftime("%H:%M"),
                    "sunset": datetime.datetime.fromtimestamp(day_data["sunset"]).strftime("%H:%M")
                }
                
                # Thêm thông tin giờ (tùy chọn)
                if "hourly" in data and i == 0:  # Chỉ thêm dữ liệu giờ cho ngày đầu tiên
                    day_forecast["hourly"] = []
                    # Giới hạn chỉ lấy những giờ còn lại trong ngày
                    current_hour = datetime.datetime.now().hour
                    hours_remaining = 24 - current_hour
                    
                    for hour_data in data["hourly"][:hours_remaining]:
                        hour_time = datetime.datetime.fromtimestamp(hour_data["dt"])
                        hour_forecast = {
                            "time": hour_time.strftime("%H:%M"),
                            "temp_c": hour_data["temp"],
                            "condition": {
                                "text": hour_data["weather"][0]["description"],
                                "icon": f"https://openweathermap.org/img/wn/{hour_data['weather'][0]['icon']}@2x.png"
                            },
                            "chance_of_rain": hour_data.get("pop", 0) * 100  # Xác suất mưa (0-1) sang phần trăm
                        }
                        day_forecast["hourly"].append(hour_forecast)
                
                standardized["forecast"].append(day_forecast)
                
            return standardized
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error trong OneCall API: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status code: {e.response.status_code}")
                logger.error(f"Response text: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Lỗi không xác định trong OneCall API: {str(e)}", exc_info=True)
            raise
    
    def _get_wind_direction(self, degrees: float) -> str:
        """Chuyển đổi góc gió (độ) sang hướng gió"""
        directions = ["Bắc", "Đông Bắc", "Đông", "Đông Nam", "Nam", "Tây Nam", "Tây", "Tây Bắc"]
        index = round(degrees / 45) % 8
        return directions[index]

    @staticmethod
    def format_weather_message(weather_data: Dict[str, Any], location: str, days: int = 1, target_date: str = None, advice_only: bool = False) -> str:
        """
        Định dạng dữ liệu thời tiết thành thông điệp HTML cho người dùng
        
        Args:
            weather_data: Dữ liệu thời tiết đã chuẩn hóa
            location: Vị trí được yêu cầu
            days: Số ngày dự báo đã yêu cầu
            target_date: Ngày cụ thể cần hiển thị (YYYY-MM-DD)
            advice_only: Chỉ trả về thông tin cần thiết cho tư vấn (mô tả ngắn gọn về thời tiết)
            
        Returns:
            Chuỗi HTML định dạng đẹp với thông tin thời tiết
        """
        # Kiểm tra lỗi
        if weather_data.get("error"):
            title = f"Thông tin thời tiết cho {location}"
            if target_date:
                try:
                    date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                    formatted_date = date_obj.strftime("%d/%m/%Y")
                    # Tên thứ trong tuần tiếng Việt
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
        
        # Định dạng thông tin hiện tại và dự báo
        current = weather_data.get("current", {})
        location_info = weather_data.get("location", {})
        actual_location = location_info.get("name", location)
        forecast = weather_data.get("forecast", [])
        
        # Nếu có target_date, tìm dự báo cho ngày đó để hiển thị thay vì thông tin hiện tại
        target_forecast = None
        if target_date and forecast:
            for day_forecast in forecast:
                if day_forecast.get("date") == target_date:
                    target_forecast = day_forecast
                    break
        
        # Nếu chỉ cần thông tin cho tư vấn, trả về mô tả ngắn gọn
        if advice_only:
            # Lấy thông tin cần thiết cho tư vấn
            temp_desc = ""
            weather_cond = ""
            rain_info = ""
            
            if target_forecast:
                min_temp = target_forecast.get("min_temp_c", 0)
                max_temp = target_forecast.get("max_temp_c", 0)
                temp_desc = f"nhiệt độ từ {min_temp}°C đến {max_temp}°C"
                weather_cond = target_forecast.get("condition", {}).get("text", "")
                rain_chance = target_forecast.get("chance_of_rain", 0)
                if rain_chance > 30:
                    rain_info = f", {rain_chance}% khả năng mưa"
            else:
                temp_c = current.get("temp_c", 0)
                feels_like = current.get("feelslike_c", 0)
                temp_desc = f"nhiệt độ {temp_c}°C, cảm giác như {feels_like}°C"
                weather_cond = current.get("condition", {}).get("text", "")
                humidity = current.get("humidity", 0)
                if humidity > 70:
                    rain_info = f", độ ẩm cao {humidity}%"
            
            # Xác định ngày (nếu có)
            date_str = ""
            if target_date:
                try:
                    date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                    weekday = weekday_names[date_obj.weekday()]
                    date_str = f" vào {weekday}"
                except:
                    date_str = f" vào ngày {target_date}"
            
            # Trả về mô tả ngắn gọn về thời tiết cho tư vấn
            return f"Thời tiết tại {actual_location}{date_str}: {temp_desc}, {weather_cond}{rain_info}"
        
        # Chọn biểu tượng emoji và tiêu đề
        if target_forecast:
            # Sử dụng thông tin dự báo cho ngày cụ thể
            weather_emoji = WeatherService._get_weather_emoji(target_forecast.get("condition", {}).get("text", "").lower())
            
            # Tạo tiêu đề với ngày cụ thể
            try:
                date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                formatted_date = date_obj.strftime("%d/%m/%Y")
                weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                weekday = weekday_names[date_obj.weekday()]
                title = f"Thời tiết tại {actual_location} vào {weekday} ({formatted_date}) {weather_emoji}"
            except:
                title = f"Thời tiết tại {actual_location} vào {target_date} {weather_emoji}"
        else:
            # Sử dụng thông tin hiện tại
            weather_emoji = WeatherService._get_weather_emoji(current.get("condition", {}).get("text", "").lower())
            title = f"Thời tiết tại {actual_location} {weather_emoji}"
            
            # Nếu có ngày cụ thể nhưng không tìm thấy trong dự báo, vẫn hiển thị ngày trong tiêu đề
            if target_date:
                try:
                    date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d")
                    formatted_date = date_obj.strftime("%d/%m/%Y")
                    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                    weekday = weekday_names[date_obj.weekday()]
                    title = f"Thời tiết tại {actual_location} vào {weekday} ({formatted_date}) {weather_emoji}"
                except:
                    title = f"Thời tiết tại {actual_location} vào {target_date} {weather_emoji}"
        
        # Bắt đầu xây dựng kết quả HTML
        result = f"<h3>{title}</h3>\n"
        
        # Hiển thị thông tin thời tiết chính (hiện tại hoặc ngày cụ thể)
        if target_forecast:
            # Hiển thị thông tin dự báo cho ngày cụ thể
            result += f"<p><b>Dự báo nhiệt độ:</b> {target_forecast.get('min_temp_c', 'N/A')}°C ~ {target_forecast.get('max_temp_c', 'N/A')}°C</p>\n"
            result += f"<p><b>Điều kiện:</b> {target_forecast.get('condition', {}).get('text', 'Không có dữ liệu')}</p>\n"
            result += f"<p><b>Khả năng mưa:</b> {target_forecast.get('chance_of_rain', 'N/A')}%</p>\n"
            
            # Thêm thông tin về mặt trời mọc/lặn nếu có
            if "sunrise" in target_forecast and "sunset" in target_forecast:
                result += f"<p><b>Mặt trời mọc:</b> {target_forecast.get('sunrise', 'N/A')}, <b>Mặt trời lặn:</b> {target_forecast.get('sunset', 'N/A')}</p>\n"
            
            # Thêm thông tin theo giờ nếu có
            if "hourly" in target_forecast and target_forecast["hourly"]:
                result += "<h4>Dự báo theo giờ:</h4>\n<ul>\n"
                for hour in target_forecast["hourly"][:6]:  # Giới hạn 6 giờ
                    hour_emoji = WeatherService._get_weather_emoji(hour.get("condition", {}).get("text", "").lower())
                    result += f"<li><b>{hour.get('time', '').split()[1] if ' ' in hour.get('time', '') else hour.get('time', '')}:</b> {hour_emoji} {hour.get('temp_c', 'N/A')}°C, {hour.get('condition', {}).get('text', '')}, {hour.get('chance_of_rain', 'N/A')}% khả năng mưa</li>\n"
                result += "</ul>\n"
        else:
            # Hiển thị thông tin hiện tại nếu không có dự báo cho ngày cụ thể
            result += f"<p><b>Hiện tại:</b> {current.get('temp_c', 'N/A')}°C, cảm giác như {current.get('feelslike_c', 'N/A')}°C</p>\n"
            result += f"<p><b>Điều kiện:</b> {current.get('condition', {}).get('text', 'Không có dữ liệu')}</p>\n"
            result += f"<p><b>Độ ẩm:</b> {current.get('humidity', 'N/A')}%</p>\n"
            result += f"<p><b>Gió:</b> {current.get('wind_kph', 'N/A')} km/h, hướng {current.get('wind_dir', 'N/A')}</p>\n"
        
        # Thêm dự báo cho các ngày tiếp theo
        if forecast and days > 1:
            result += "<h4>Dự báo các ngày tới:</h4>\n<ul>\n"
            
            for day in forecast[:days]:
                day_date = datetime.datetime.strptime(day.get("date", ""), "%Y-%m-%d").strftime("%d/%m")
                day_emoji = WeatherService._get_weather_emoji(day.get("condition", {}).get("text", "").lower())
                
                result += f"<li><b>{day_date}:</b> {day_emoji} {day.get('condition', {}).get('text', '')} - {day.get('min_temp_c', 'N/A')}°C ~ {day.get('max_temp_c', 'N/A')}°C, {day.get('chance_of_rain', 'N/A')}% khả năng mưa</li>\n"
            
            result += "</ul>\n"
        
        # Thêm dự báo theo giờ cho ngày hiện tại nếu chưa hiển thị và có dữ liệu
        if not target_forecast and forecast and forecast[0].get("hourly"):
            result += "<h4>Dự báo theo giờ hôm nay:</h4>\n<ul>\n"
            
            # Giới hạn hiển thị 6 giờ tiếp theo để không quá dài
            for hour in forecast[0]["hourly"][:6]:
                hour_emoji = WeatherService._get_weather_emoji(hour.get("condition", {}).get("text", "").lower())
                
                result += f"<li><b>{hour.get('time', '').split()[1] if ' ' in hour.get('time', '') else hour.get('time', '')}:</b> {hour_emoji} {hour.get('temp_c', 'N/A')}°C, {hour.get('condition', {}).get('text', '')}, {hour.get('chance_of_rain', 'N/A')}% khả năng mưa</li>\n"
            
            result += "</ul>\n"
        
        # Thêm ghi chú thời gian cập nhật
        result += f"<p><i>Cập nhật lúc: {location_info.get('localtime', '')}</i></p>"
        
        return result
    
    @staticmethod
    def _get_weather_emoji(condition: str) -> str:
        """Trả về emoji phù hợp với điều kiện thời tiết"""
        if any(word in condition for word in ["mưa", "rain", "shower"]):
            return "🌧️"
        elif any(word in condition for word in ["giông", "bão", "thunder", "storm"]):
            return "⛈️"
        elif any(word in condition for word in ["nắng", "sunny", "clear"]):
            return "☀️"
        elif any(word in condition for word in ["mây", "clouds", "cloudy"]):
            return "☁️"
        elif any(word in condition for word in ["sương mù", "fog", "mist"]):
            return "🌫️"
        elif any(word in condition for word in ["tuyết", "snow"]):
            return "❄️"
        else:
            return "🌤️"  # Mặc định
    
    def get_date_from_relative_term(self, term: str) -> Optional[str]:
        """
        Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế (YYYY-MM-DD).
        Hỗ trợ: hôm nay, ngày mai, ngày kia, hôm qua, thứ X tuần sau, thứ X.
        
        Args:
            term: Cụm từ mô tả thời gian (ví dụ: "thứ 3 tuần sau", "hôm nay")
            
        Returns:
            Chuỗi ngày theo định dạng YYYY-MM-DD hoặc None nếu không xác định được
        """
        if not term:
            return None
            
        term = term.lower().strip()
        today = datetime.datetime.now().date()
        logger.debug(f"Tính toán ngày cho term: '{term}', hôm nay là: {today.strftime('%Y-%m-%d %A')}")

        # Các từ ngữ tương đối cơ bản
        if term in ["hôm nay", "today"]:
            return today.strftime("%Y-%m-%d")
        elif term in ["ngày mai", "mai", "tomorrow"]:
            return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        elif term in ["ngày kia", "day after tomorrow"]:
             return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        elif term in ["hôm qua", "yesterday"]:
            return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        # --- Xử lý ngày trong tuần cụ thể ---
        target_weekday = -1
        is_next_week = False

        # Kiểm tra xem có đề cập đến tuần sau không
        original_term = term  # Giữ lại term gốc trước khi loại bỏ "tuần sau"
        for kw in ["tuần sau", "tuần tới", "next week"]:
            if kw in term:
                is_next_week = True
                # Chỉ loại bỏ keyword để tìm weekday, không thay đổi term gốc
                term_without_next_week = term.replace(kw, "").strip()
                logger.debug(f"Phát hiện 'tuần sau/tới'. Term để tìm thứ: '{term_without_next_week}'")
                break
        else:  # Nếu không phải next week
            term_without_next_week = term

        # Tìm ngày trong tuần từ term đã sửa đổi
        for day_str, day_num in self.VIETNAMESE_WEEKDAY_MAP.items():
            # Sử dụng regex để khớp từ hoàn chỉnh
            if re.search(r'\b' + re.escape(day_str) + r'\b', term_without_next_week):
                target_weekday = day_num
                logger.debug(f"Tìm thấy thứ trong tuần: {day_str} ({target_weekday})")
                break

        if target_weekday != -1:
            today_weekday = today.weekday()  # Monday is 0, Sunday is 6

            if is_next_week:
                # 1. Tính số ngày cần để đến Thứ Hai của tuần sau
                days_to_next_monday = (6 - today_weekday) + 1
                logger.debug(f"Số ngày từ hôm nay ({today_weekday}) đến thứ Hai tuần sau: {days_to_next_monday}")

                # 2. Tính ngày Thứ Hai tuần sau
                next_monday_date = today + datetime.timedelta(days=days_to_next_monday)
                logger.debug(f"Ngày thứ Hai tuần sau: {next_monday_date.strftime('%Y-%m-%d')}")

                # 3. Tính ngày mục tiêu
                final_date = next_monday_date + datetime.timedelta(days=target_weekday)
                logger.info(f"Đã tính toán ngày cho '{original_term}': {final_date.strftime('%Y-%m-%d %A')}")
                return final_date.strftime("%Y-%m-%d")

            else:  # Hỏi về "thứ X" không xác định tuần (giả định sắp tới)
                # Tính số ngày cần để đến target_weekday *sắp tới*
                days_ahead = target_weekday - today_weekday
                logger.debug(f"Tính ngày trong tuần sắp tới: target={target_weekday}, today={today_weekday}, days_ahead={days_ahead}")
                
                # Nếu ngày đã qua trong tuần này hoặc là hôm nay, cộng thêm 7 ngày
                if days_ahead <= 0:
                     days_to_add = days_ahead + 7
                     logger.debug("Ngày đã qua hoặc là hôm nay, cộng thêm 7 ngày.")
                else:  # Ngày nằm sau trong tuần này
                     days_to_add = days_ahead
                     logger.debug("Ngày nằm sau trong tuần này.")

                final_date = today + datetime.timedelta(days=days_to_add)
                logger.info(f"Đã tính toán ngày sắp tới cho '{original_term}': {final_date.strftime('%Y-%m-%d %A')}")
                return final_date.strftime("%Y-%m-%d")

        # --- Kết thúc xử lý ngày trong tuần ---

        # Fallback cho các cụm từ không chính xác
        if any(kw in term for kw in ["tuần sau", "tuần tới", "next week"]):
            days_to_next_monday = (6 - today.weekday()) + 1
            calculated_date = today + datetime.timedelta(days=days_to_next_monday)  # Thứ Hai tuần sau
            logger.info(f"Tính toán ngày cho 'tuần sau' chung: {calculated_date.strftime('%Y-%m-%d')} (Thứ Hai tuần sau)")
            return calculated_date.strftime("%Y-%m-%d")
        elif "tháng tới" in term or "tháng sau" in term or "next month" in term:
            # Ước lượng đơn giản: thêm 30 ngày
            calculated_date = today + datetime.timedelta(days=30)
            logger.info(f"Tính toán ngày cho 'tháng sau': {calculated_date.strftime('%Y-%m-%d')} (ước lượng +30 ngày)")
            return calculated_date.strftime("%Y-%m-%d")

        # Kiểm tra xem term có phải là định dạng ngày hợp lệ không
        try:
            parsed_date = None
            if re.match(r'\d{4}-\d{2}-\d{2}', term):
                 parsed_date = datetime.datetime.strptime(term, "%Y-%m-%d").date()
            elif re.match(r'\d{2}/\d{2}/\d{4}', term):
                 parsed_date = datetime.datetime.strptime(term, "%d/%m/%Y").date()

            if parsed_date:
                 logger.info(f"Term '{term}' là chuỗi ngày hợp lệ, trả về dạng chuẩn hóa.")
                 return parsed_date.strftime("%Y-%m-%d")  # Trả về định dạng chuẩn
        except ValueError:
            pass  # Không phải định dạng ngày hợp lệ

        logger.warning(f"Không thể hiểu cụm từ ngày tương đối: '{term}'. Trả về None.")
        return None

    @staticmethod
    def detect_weather_query(text: str) -> Tuple[bool, Optional[str], Optional[int], Optional[str]]:
        """
        Phát hiện nếu một câu hỏi là về thời tiết, và trích xuất vị trí, số ngày và cụm từ thời gian
        
        Args:
            text: Câu hỏi của người dùng
            
        Returns:
            Tuple (is_weather_query, location, days, time_term)
                - is_weather_query: True nếu là câu hỏi về thời tiết
                - location: Vị trí được đề cập (hoặc None)
                - days: Số ngày dự báo (hoặc None)
                - time_term: Cụm từ thời gian được trích xuất (để phân tích sau)
        """
        # Từ khóa liên quan đến thời tiết
        weather_keywords = [
            "thời tiết", "dự báo", "nhiệt độ", "nắng", "mưa", "gió", "bão", 
            "giông", "nhiệt độ", "nóng", "lạnh", "độ ẩm", "cảm giác"
        ]
        
        # Từ khóa liên quan đến thời gian
        time_keywords = {
            "hôm nay": 1,
            "ngày mai": 2,
            "ngày kia": 3,
            "tuần này": 7,
            "tuần tới": 7,
            "tuần sau": 7,
            "3 ngày tới": 3,
            "5 ngày tới": 5,
            "7 ngày tới": 7
        }
        
        # Chuyển câu hỏi về chữ thường
        text_lower = text.lower()
        
        # Kiểm tra xem có phải câu hỏi về thời tiết không
        is_weather_query = any(keyword in text_lower for keyword in weather_keywords)
        
        # Nếu không phải câu hỏi về thời tiết, trả về ngay
        if not is_weather_query:
            return False, None, None, None
            
        # Trích xuất số ngày dự báo và cụm từ thời gian
        days = 1  # Mặc định 1 ngày
        time_term = "hôm nay"  # Mặc định là hôm nay
        
        # Kiểm tra các mẫu thời gian phức tạp trước
        # Mẫu "thứ X tuần sau/tới"
        for day in ["thứ 2", "thứ 3", "thứ 4", "thứ 5", "thứ 6", "thứ 7", "chủ nhật"]:
            for week_term in ["tuần sau", "tuần tới"]:
                if f"{day} {week_term}" in text_lower or f"{day} {week_term}" in text_lower:
                    time_term = f"{day} {week_term}"
                    days = 7  # Một tuần
                    break
        
        # Nếu không tìm thấy mẫu phức tạp, kiểm tra từ khóa đơn giản
        if time_term == "hôm nay":
            for term, days_value in time_keywords.items():
                if term in text_lower:
                    time_term = term
                    days = days_value
                    break
                
        # Thử trích xuất vị trí (danh sách các thành phố/tỉnh phổ biến)
        popular_locations = [
            "hà nội", "thành phố hồ chí minh", "tp hcm", "sài gòn", "đà nẵng", 
            "huế", "nha trang", "đà lạt", "hải phòng", "cần thơ", "hạ long",
            "vũng tàu", "quy nhơn", "phú quốc", "hội an", "nam định", "hà giang",
            "lào cai", "sapa", "bắc ninh", "thái nguyên", "vinh", "thanh hóa", 
            "buôn ma thuột", "cà mau"
        ]
        
        # Tìm vị trí trong danh sách
        location = None
        for loc in popular_locations:
            if loc in text_lower:
                location = loc.title()  # Viết hoa chữ cái đầu của mỗi từ
                break
                
        # Nếu chưa tìm thấy vị trí, thử phương pháp đơn giản hơn - giả định vị trí nằm sau "ở", "tại"
        if not location:
            for prefix in ["ở ", "tại ", "tại thành phố ", "tại tỉnh "]:
                if prefix in text_lower:
                    parts = text_lower.split(prefix, 1)
                    if len(parts) > 1:
                        # Lấy từ sau prefix cho đến dấu câu hoặc hết chuỗi
                        loc_part = parts[1].split("?")[0].split(".")[0].split(",")[0].split("!")[0].strip()
                        if loc_part:
                            location = loc_part.title()
                            break
        
        # Mặc định là Hà Nội nếu không tìm thấy vị trí
        if not location:
            location = "Hà Nội"
            
        return True, location, days, time_term
    
OPENWEATHER_API_KEY = "94c94ebc644d803eef31af2f1d399bd2"
weather_service = WeatherService(openweather_api_key=OPENWEATHER_API_KEY)

class WeatherAdvisor:
    """
    Lớp cung cấp các tư vấn thông minh dựa trên dữ liệu thời tiết như:
    - Gợi ý trang phục phù hợp với thời tiết
    - Gợi ý hoạt động phù hợp với thời tiết
    - Gợi ý đồ dùng cần mang theo 
    """
    
    # Mapping nhiệt độ với gợi ý trang phục
    CLOTHING_TEMP_RANGES = {
        "rất lạnh": (-100, 15),
        "lạnh": (15, 20),
        "mát mẻ": (20, 25),
        "ấm áp": (25, 29),
        "nóng": (29, 35),
        "rất nóng": (35, 100)
    }
    
    # Mapping trạng thái thời tiết với các từ khóa
    WEATHER_CONDITIONS = {
        "mưa": ["mưa", "rain", "shower", "drizzle", "thunderstorm", "mưa rào", "mưa dông", "mưa nhẹ", "mưa to"],
        "nắng": ["nắng", "sunny", "clear", "nắng gắt", "trời nắng", "clear sky"],
        "mây": ["mây", "cloud", "cloudy", "overcast", "mây đen", "u ám", "nhiều mây"],
        "gió": ["gió", "wind", "windy", "gió mạnh", "gió lớn"],
        "sương mù": ["sương mù", "fog", "mist", "sương", "foggy"],
        "tuyết": ["tuyết", "snow", "snowy"]
    }
    
    def __init__(self):
        """Khởi tạo lớp WeatherAdvisor"""
        pass
        
    @staticmethod
    def detect_advice_query(text: str) -> Tuple[bool, str, Optional[str]]:
        """
        Phát hiện câu hỏi xin tư vấn dựa trên thời tiết
        
        Args:
            text: Câu hỏi của người dùng
            
        Returns:
            Tuple (is_advice_query, advice_type, time_term)
                - is_advice_query: True nếu là câu hỏi xin tư vấn dựa trên thời tiết
                - advice_type: Loại tư vấn ("clothing", "activity", "items")
                - time_term: Cụm từ thời gian được đề cập (nếu có)
        """
        text_lower = text.lower()
        
        # Xác định loại tư vấn
        clothing_keywords = [
            "nên mặc gì", "mặc gì", "trang phục", "mặc áo", "mặc quần", "nên mặc", 
            "ăn mặc", "nên ăn mặc", "mặc đồ", "nên mặc đồ", "quần áo", "thời trang"
        ]
        
        activity_keywords = [
            "nên làm gì", "nên đi đâu", "đi chơi", "nên đi chơi", "hoạt động", 
            "nên tham gia", "nên tổ chức", "địa điểm", "đi đâu", "chơi gì"
        ]
        
        item_keywords = [
            "nên mang", "mang theo", "cần mang", "cần chuẩn bị", "cần đem theo",
            "nên đem", "đem theo", "nên chuẩn bị", "cần đem", "vật dụng"
        ]
        
        # Các từ khóa thời gian
        time_keywords = [
            "hôm nay", "ngày mai", "ngày mốt", "tối nay", "sáng mai", "chiều mai", 
            "tối mai", "cuối tuần", "tuần này", "tuần sau", "thứ ", "chủ nhật"
        ]
        
        # Phát hiện loại tư vấn
        advice_type = None
        for keyword in clothing_keywords:
            if keyword in text_lower:
                advice_type = "clothing"
                break
                
        if not advice_type:
            for keyword in activity_keywords:
                if keyword in text_lower:
                    advice_type = "activity"
                    break
        
        if not advice_type:
            for keyword in item_keywords:
                if keyword in text_lower:
                    advice_type = "items"
                    break
        
        # Nếu không phát hiện loại tư vấn, không phải câu hỏi tư vấn
        if not advice_type:
            return False, "", None
            
        # Tìm cụm từ thời gian
        time_term = None
        for keyword in time_keywords:
            if keyword in text_lower:
                # Nếu từ khóa là "thứ ", cần lấy cả "thứ X"
                if keyword == "thứ ":
                    # Tìm kiểu "thứ 2", "thứ hai", v.v.
                    thứ_matches = re.findall(r'thứ\s+(\d|hai|ba|tư|năm|sáu|bảy|chủ nhật)', text_lower)
                    if thứ_matches:
                        time_term = "thứ " + thứ_matches[0]
                else:
                    time_term = keyword
                break
        
        # Nếu không tìm thấy từ khóa thời gian, giả định là "ngày mai"
        if not time_term:
            time_term = "ngày mai"
            
        return True, advice_type, time_term
    
    def get_clothing_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """
        Đưa ra lời khuyên về trang phục dựa trên dữ liệu thời tiết
        
        Args:
            weather_data: Dữ liệu thời tiết đã chuẩn hóa
            target_date: Ngày cụ thể (YYYY-MM-DD) để lấy dữ liệu dự báo, None cho thời tiết hiện tại
            
        Returns:
            Lời khuyên về trang phục phù hợp
        """
        try:
            # Lấy dữ liệu thời tiết cho ngày cụ thể hoặc hiện tại
            temp_c = None
            conditions = []
            feels_like = None
            
            if target_date and "forecast" in weather_data:
                # Tìm dự báo cho ngày cụ thể
                for day in weather_data["forecast"]:
                    if day.get("date") == target_date:
                        temp_c = (day.get("min_temp_c", 0) + day.get("max_temp_c", 0)) / 2  # Lấy nhiệt độ trung bình
                        conditions.append(day.get("condition", {}).get("text", "").lower())
                        break
            
            if temp_c is None:
                # Sử dụng dữ liệu hiện tại
                temp_c = weather_data.get("current", {}).get("temp_c", 25)  # Mặc định 25°C nếu không có dữ liệu
                feels_like = weather_data.get("current", {}).get("feelslike_c")
                conditions.append(weather_data.get("current", {}).get("condition", {}).get("text", "").lower())
            
            # Nếu không có conditions, sử dụng giá trị mặc định
            if not conditions or not conditions[0]:
                conditions = ["mây rải rác"]  # Giá trị mặc định
            
            # Xác định trạng thái thời tiết từ mô tả
            weather_state = "bình thường"  # Mặc định
            for state, keywords in self.WEATHER_CONDITIONS.items():
                if any(keyword in condition for keyword in keywords for condition in conditions):
                    weather_state = state
                    break
            
            # Xác định phạm vi nhiệt độ
            temp_range = "ấm áp"  # Mặc định
            for description, (min_temp, max_temp) in self.CLOTHING_TEMP_RANGES.items():
                if min_temp <= temp_c < max_temp:
                    temp_range = description
                    break
            
            # Đưa ra lời khuyên về trang phục
            advice = self._generate_clothing_advice(temp_range, weather_state, temp_c, feels_like)
            return advice
            
        except Exception as e:
            logger.error(f"Lỗi khi tư vấn trang phục: {e}", exc_info=True)
            return "Tôi khuyên bạn nên mặc trang phục thoải mái, phù hợp với thời tiết hiện tại."
    
    def get_activity_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """
        Đưa ra lời khuyên về hoạt động dựa trên dữ liệu thời tiết
        
        Args:
            weather_data: Dữ liệu thời tiết đã chuẩn hóa
            target_date: Ngày cụ thể (YYYY-MM-DD) để lấy dữ liệu dự báo, None cho thời tiết hiện tại
            
        Returns:
            Lời khuyên về hoạt động phù hợp
        """
        try:
            # Lấy dữ liệu thời tiết tương tự như get_clothing_advice
            temp_c = None
            conditions = []
            rain_chance = 0
            
            if target_date and "forecast" in weather_data:
                for day in weather_data["forecast"]:
                    if day.get("date") == target_date:
                        temp_c = (day.get("min_temp_c", 0) + day.get("max_temp_c", 0)) / 2
                        conditions.append(day.get("condition", {}).get("text", "").lower())
                        rain_chance = day.get("chance_of_rain", 0)
                        break
            
            if temp_c is None:
                temp_c = weather_data.get("current", {}).get("temp_c", 25)
                conditions.append(weather_data.get("current", {}).get("condition", {}).get("text", "").lower())
                rain_chance = 0  # Hiện tại không có thông tin xác suất mưa, mặc định 0
            
            if not conditions or not conditions[0]:
                conditions = ["mây rải rác"]
            
            # Xác định trạng thái thời tiết
            weather_state = "bình thường"
            for state, keywords in self.WEATHER_CONDITIONS.items():
                if any(keyword in condition for keyword in keywords for condition in conditions):
                    weather_state = state
                    break
            
            # Nếu xác suất mưa cao, ưu tiên trạng thái mưa
            if rain_chance > 50 and weather_state != "mưa":
                weather_state = "mưa sắp tới"
            
            # Đưa ra lời khuyên về hoạt động
            advice = self._generate_activity_advice(weather_state, temp_c, rain_chance)
            return advice
            
        except Exception as e:
            logger.error(f"Lỗi khi tư vấn hoạt động: {e}", exc_info=True)
            return "Bạn có thể cân nhắc các hoạt động trong nhà như xem phim, đọc sách, hoặc các hoạt động ngoài trời nếu thời tiết đẹp."
    
    def get_items_advice(self, weather_data: Dict[str, Any], target_date: str = None) -> str:
        """
        Đưa ra lời khuyên về đồ dùng cần mang theo dựa trên dữ liệu thời tiết
        
        Args:
            weather_data: Dữ liệu thời tiết đã chuẩn hóa
            target_date: Ngày cụ thể (YYYY-MM-DD) để lấy dữ liệu dự báo, None cho thời tiết hiện tại
            
        Returns:
            Lời khuyên về đồ dùng cần mang theo
        """
        try:
            # Lấy dữ liệu thời tiết tương tự như các hàm trên
            temp_c = None
            conditions = []
            rain_chance = 0
            humidity = 0
            uv_index = 0
            
            if target_date and "forecast" in weather_data:
                for day in weather_data["forecast"]:
                    if day.get("date") == target_date:
                        temp_c = (day.get("min_temp_c", 0) + day.get("max_temp_c", 0)) / 2
                        conditions.append(day.get("condition", {}).get("text", "").lower())
                        rain_chance = day.get("chance_of_rain", 0)
                        break
            
            if temp_c is None:
                temp_c = weather_data.get("current", {}).get("temp_c", 25)
                conditions.append(weather_data.get("current", {}).get("condition", {}).get("text", "").lower())
                humidity = weather_data.get("current", {}).get("humidity", 50)
                uv_index = weather_data.get("current", {}).get("uv", 0)
                rain_chance = 0
            
            if not conditions or not conditions[0]:
                conditions = ["mây rải rác"]
            
            # Xác định trạng thái thời tiết
            weather_state = "bình thường"
            for state, keywords in self.WEATHER_CONDITIONS.items():
                if any(keyword in condition for keyword in keywords for condition in conditions):
                    weather_state = state
                    break
            
            # Nếu xác suất mưa cao, ưu tiên trạng thái mưa
            if rain_chance > 50 and weather_state != "mưa":
                weather_state = "mưa sắp tới"
            
            # Đưa ra lời khuyên về đồ dùng
            advice = self._generate_items_advice(weather_state, temp_c, rain_chance, humidity, uv_index)
            return advice
            
        except Exception as e:
            logger.error(f"Lỗi khi tư vấn đồ dùng: {e}", exc_info=True)
            return "Bạn nên mang theo các vật dụng cá nhân cần thiết và kiểm tra thời tiết trước khi ra ngoài."
    
    def _generate_clothing_advice(self, temp_range: str, weather_state: str, temp_c: float, feels_like: float = None) -> str:
        """
        Tạo lời khuyên chi tiết về trang phục
        
        Args:
            temp_range: Phạm vi nhiệt độ ("rất lạnh", "lạnh", "mát mẻ", "ấm áp", "nóng", "rất nóng")
            weather_state: Trạng thái thời tiết ("mưa", "nắng", "mây", "gió", "sương mù", "tuyết")
            temp_c: Nhiệt độ thực tế (°C)
            feels_like: Nhiệt độ cảm giác (°C), nếu có
            
        Returns:
            Lời khuyên chi tiết về trang phục
        """
        clothing_advice = f"Với thời tiết {temp_range}"
        if feels_like and abs(feels_like - temp_c) > 2:
            clothing_advice += f" (nhiệt độ {temp_c}°C, cảm giác như {feels_like}°C)"
        else:
            clothing_advice += f" (nhiệt độ {temp_c}°C)"
        
        if weather_state != "bình thường":
            clothing_advice += f" và {weather_state}"
        
        clothing_advice += ", bạn nên: \n\n"
        
        # Tư vấn dựa trên nhiệt độ
        if temp_range == "rất lạnh":
            clothing_advice += "- Mặc áo ấm dày, áo khoác hoặc áo phao\n"
            clothing_advice += "- Đội mũ, đeo găng tay và khăn quàng cổ\n"
            clothing_advice += "- Mặc nhiều lớp để giữ ấm tốt hơn\n"
        elif temp_range == "lạnh":
            clothing_advice += "- Mặc áo khoác nhẹ hoặc áo len dày\n"
            clothing_advice += "- Chọn quần dài, có thể mặc thêm áo giữ nhiệt bên trong\n"
        elif temp_range == "mát mẻ":
            clothing_advice += "- Mặc áo sơ mi dài tay hoặc áo thun dài tay\n"
            clothing_advice += "- Quần dài vải thoáng khí\n"
            clothing_advice += "- Có thể mang theo áo khoác mỏng khi ra ngoài vào buổi sáng sớm hoặc tối\n"
        elif temp_range == "ấm áp":
            clothing_advice += "- Mặc áo thun, áo sơ mi ngắn tay bằng vải cotton thoáng mát\n"
            clothing_advice += "- Quần dài hoặc quần short thoải mái\n"
        elif temp_range == "nóng":
            clothing_advice += "- Mặc áo thun nhẹ, rộng rãi, làm từ vải thoáng khí\n"
            clothing_advice += "- Quần short hoặc váy để thoải mái\n"
            clothing_advice += "- Chọn trang phục màu sáng để phản chiếu nhiệt\n"
        elif temp_range == "rất nóng":
            clothing_advice += "- Mặc áo không tay, áo thun mỏng nhẹ làm từ vải linen hoặc cotton\n"
            clothing_advice += "- Quần short rộng hoặc váy thoáng mát\n"
            clothing_advice += "- Trang phục rộng rãi, thoáng khí và màu sáng\n"
        
        # Tư vấn dựa trên trạng thái thời tiết
        if weather_state == "mưa" or weather_state == "mưa sắp tới":
            clothing_advice += "- Mang theo áo mưa hoặc ô\n"
            clothing_advice += "- Mặc giày không thấm nước\n"
            clothing_advice += "- Tránh mặc quần áo quá mỏng hoặc dễ thấm nước\n"
        elif weather_state == "nắng":
            clothing_advice += "- Đội mũ rộng vành để che nắng\n"
            clothing_advice += "- Mặc áo chống nắng (UPF) nếu ra ngoài lâu\n"
            clothing_advice += "- Đeo kính râm để bảo vệ mắt\n"
        elif weather_state == "gió":
            clothing_advice += "- Mặc áo khoác chắn gió\n"
            clothing_advice += "- Tránh mặc váy quá rộng hoặc quần áo quá rộng dễ bay\n"
        elif weather_state == "sương mù":
            clothing_advice += "- Mặc quần áo có màu sắc sáng dễ nhìn thấy\n"
            clothing_advice += "- Mang theo khăn quàng cổ để bảo vệ đường hô hấp\n"
        
        return clothing_advice
    
    def _generate_activity_advice(self, weather_state: str, temp_c: float, rain_chance: float) -> str:
        """
        Tạo lời khuyên chi tiết về hoạt động
        
        Args:
            weather_state: Trạng thái thời tiết
            temp_c: Nhiệt độ (°C)
            rain_chance: Xác suất mưa (%)
            
        Returns:
            Lời khuyên chi tiết về hoạt động
        """
        # Xác định mức nhiệt độ
        if temp_c < 15:
            temp_desc = "lạnh"
        elif temp_c < 25:
            temp_desc = "mát mẻ"
        elif temp_c < 30:
            temp_desc = "ấm áp"
        else:
            temp_desc = "nóng"
        
        # Bắt đầu lời khuyên
        activity_advice = f"Với thời tiết {temp_desc} ({temp_c}°C)"
        
        if rain_chance > 0:
            activity_advice += f" và xác suất mưa {rain_chance}%"
        
        if weather_state != "bình thường":
            activity_advice += f", {weather_state}"
        
        activity_advice += ", tôi gợi ý: \n\n"
        
        # Hoạt động trong nhà
        indoor_activities = [
            "Đi xem phim tại rạp",
            "Tham quan bảo tàng hoặc triển lãm nghệ thuật",
            "Thử món ăn tại nhà hàng mới",
            "Mua sắm tại trung tâm thương mại",
            "Khám phá thư viện, đọc sách",
            "Tham gia lớp học nấu ăn hoặc workshop thủ công",
            "Chơi board game tại quán cà phê",
            "Thư giãn tại spa hoặc massage"
        ]
        
        # Hoạt động ngoài trời - thời tiết đẹp
        good_outdoor_activities = [
            "Dã ngoại tại công viên",
            "Đi bộ dạo quanh hồ hoặc khu vực xanh trong thành phố",
            "Đạp xe khám phá thành phố",
            "Tham quan các điểm du lịch nổi tiếng",
            "Đi chơi ở khu vui chơi giải trí ngoài trời",
            "Tổ chức tiệc BBQ ngoài trời",
            "Chèo thuyền kayak hoặc sup trên hồ",
            "Đi cắm trại qua đêm tại khu vực ngoại ô"
        ]
        
        # Hoạt động ngoài trời - thời tiết nóng
        hot_outdoor_activities = [
            "Bơi lội tại hồ bơi công cộng",
            "Đi công viên nước",
            "Tham quan các địa điểm có điều hòa như bảo tàng",
            "Đi dạo buổi sáng sớm hoặc chiều tối khi mát mẻ hơn",
            "Tận hưởng đồ uống mát lạnh tại quán cà phê có không gian thoáng",
            "Đi chơi ở những khu vực có nhiều cây xanh, bóng mát"
        ]
        
        # Hoạt động cho thời tiết mưa
        rainy_activities = [
            "Đi cà phê và ngắm mưa",
            "Tham quan các trung tâm mua sắm",
            "Thưởng thức ẩm thực tại nhà hàng ấm cúng",
            "Đi xem phim tại rạp",
            "Ghé thăm các bảo tàng hoặc triển lãm trong nhà",
            "Học nấu món ăn mới tại lớp dạy nấu ăn"
        ]
        
        # Chọn hoạt động dựa trên thời tiết
        if weather_state == "mưa" or weather_state == "mưa sắp tới" or rain_chance > 60:
            activity_advice += "## Hoạt động trong nhà:\n"
            selected_activities = rainy_activities + indoor_activities[:3]
            
        elif weather_state == "nắng" and temp_desc == "nóng":
            activity_advice += "## Hoạt động giúp bạn tránh nóng:\n"
            selected_activities = hot_outdoor_activities + indoor_activities[:2]
            
        elif weather_state == "nắng" or (temp_desc in ["mát mẻ", "ấm áp"] and rain_chance < 30):
            activity_advice += "## Hoạt động ngoài trời:\n"
            selected_activities = good_outdoor_activities[:5]
            activity_advice += "\n## Hoạt động trong nhà (nếu muốn):\n"
            selected_activities += indoor_activities[:3]
            
        else:
            # Kết hợp cả hai loại
            activity_advice += "## Hoạt động phù hợp:\n"
            selected_activities = good_outdoor_activities[:3] + indoor_activities[:3]
        
        # Thêm lời khuyên cụ thể vào danh sách
        import random
        random.shuffle(selected_activities)
        for activity in selected_activities[:5]:  # Giới hạn 5 gợi ý
            activity_advice += f"- {activity}\n"
        
        # Lời khuyên bổ sung
        if weather_state == "mưa" or rain_chance > 60:
            activity_advice += "\n**Lưu ý:** Mang theo ô hoặc áo mưa nếu phải di chuyển giữa các địa điểm."
        elif weather_state == "nắng" and temp_desc in ["nóng", "rất nóng"]:
            activity_advice += "\n**Lưu ý:** Uống nhiều nước và tránh hoạt động ngoài trời vào thời gian nắng gắt (11h-15h)."
        
        return activity_advice
    
    def _generate_items_advice(self, weather_state: str, temp_c: float, rain_chance: float, humidity: float, uv_index: float) -> str:
        """
        Tạo lời khuyên chi tiết về đồ dùng cần mang theo
        
        Args:
            weather_state: Trạng thái thời tiết
            temp_c: Nhiệt độ (°C)
            rain_chance: Xác suất mưa (%)
            humidity: Độ ẩm (%)
            uv_index: Chỉ số UV
            
        Returns:
            Lời khuyên chi tiết về đồ dùng
        """
        # Đồ dùng cơ bản luôn cần mang theo
        basic_items = [
            "Điện thoại và sạc dự phòng",
            "Ví/tiền",
            "Thẻ căn cước/giấy tờ tùy thân",
            "Chìa khóa nhà/xe"
        ]
        
        # Đồ dùng cho thời tiết mưa
        rain_items = [
            "Ô hoặc áo mưa",
            "Giày không thấm nước",
            "Túi chống nước cho điện thoại/thiết bị điện tử",
            "Khăn lau khô"
        ]
        
        # Đồ dùng cho thời tiết nắng
        sun_items = [
            "Kem chống nắng (SPF 30+)",
            "Kính râm",
            "Mũ rộng vành",
            "Chai nước uống",
            "Quạt cầm tay hoặc quạt mini"
        ]
        
        # Đồ dùng cho thời tiết lạnh
        cold_items = [
            "Găng tay",
            "Mũ len hoặc mũ trùm đầu",
            "Khăn quàng cổ",
            "Bình giữ nhiệt đựng đồ uống nóng"
        ]
        
        # Bắt đầu lời khuyên
        items_advice = "Đồ dùng nên mang theo:\n\n"
        
        # Luôn thêm đồ dùng cơ bản
        items_advice += "## Vật dụng cơ bản:\n"
        for item in basic_items:
            items_advice += f"- {item}\n"
        
        # Thêm đồ dùng theo điều kiện thời tiết
        if weather_state == "mưa" or weather_state == "mưa sắp tới" or rain_chance > 50:
            items_advice += "\n## Đồ dùng cho thời tiết mưa:\n"
            for item in rain_items:
                items_advice += f"- {item}\n"
                
        if weather_state == "nắng" or uv_index > 3:
            items_advice += "\n## Đồ dùng cho thời tiết nắng:\n"
            for item in sun_items:
                items_advice += f"- {item}\n"
                
        if temp_c < 20:
            items_advice += "\n## Đồ dùng cho thời tiết lạnh:\n"
            for item in cold_items:
                items_advice += f"- {item}\n"
        
        # Lời khuyên bổ sung dựa trên độ ẩm
        if humidity > 70:
            items_advice += "\n**Lời khuyên thêm:** Độ ẩm cao, nên mang thêm khăn lau mồ hôi và quần áo thay thế nếu cần."
            
        # Lời khuyên bổ sung dựa trên chỉ số UV
        if uv_index > 7:
            items_advice += "\n**Lưu ý quan trọng:** Chỉ số UV rất cao, nhớ bôi kem chống nắng thường xuyên và tránh tiếp xúc trực tiếp với ánh nắng mặt trời."
        
        return items_advice

weather_advisor = WeatherAdvisor()

# Tải dữ liệu ban đầu
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Đảm bảo dữ liệu là một từ điển
                if not isinstance(data, dict):
                    print(f"Dữ liệu trong {file_path} không phải từ điển. Khởi tạo lại.")
                    return {}
                return data
        except Exception as e:
            print(f"Lỗi khi đọc {file_path}: {e}")
            return {}
    return {}

def save_data(file_path, data):
    try:
        # Đảm bảo thư mục tồn tại
        os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Đã lưu dữ liệu vào {file_path}: {len(data)} mục")
        return True
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}")
        return False

# Tải dữ liệu và lưu vào biến toàn cục để tái sử dụng
family_data = load_data(FAMILY_DATA_FILE)
events_data = load_data(EVENTS_DATA_FILE)
notes_data = load_data(NOTES_DATA_FILE)
chat_history = load_data(CHAT_HISTORY_FILE)

# Kiểm tra và đảm bảo cấu trúc dữ liệu đúng
def verify_data_structure():
    global family_data, events_data, notes_data, chat_history
    
    # Đảm bảo tất cả dữ liệu là từ điển
    if not isinstance(family_data, dict):
        print("family_data không phải từ điển. Khởi tạo lại.")
        family_data = {}
        
    if not isinstance(events_data, dict):
        print("events_data không phải từ điển. Khởi tạo lại.")
        events_data = {}
        
    if not isinstance(notes_data, dict):
        print("notes_data không phải từ điển. Khởi tạo lại.")
        notes_data = {}
        
    if not isinstance(chat_history, dict):
        print("chat_history không phải từ điển. Khởi tạo lại.")
        chat_history = {}
    
    # Kiểm tra và sửa các dữ liệu thành viên
    members_to_fix = []
    for member_id, member in family_data.items():
        if not isinstance(member, dict):
            members_to_fix.append(member_id)
    
    # Xóa các mục không hợp lệ
    for member_id in members_to_fix:
        del family_data[member_id]
        
    # Lưu lại dữ liệu đã sửa
    save_data(FAMILY_DATA_FILE, family_data)
    save_data(EVENTS_DATA_FILE, events_data)
    save_data(NOTES_DATA_FILE, notes_data)
    save_data(CHAT_HISTORY_FILE, chat_history)

# Thực hiện kiểm tra dữ liệu khi khởi động
verify_data_structure()

def date_time_to_cron(date_str, time_str="19:00"):
    """
    Chuyển đổi ngày và giờ thành cron expression định dạng Quartz (6 hoặc 7 trường).
    Dùng cho sự kiện xảy ra MỘT LẦN vào ngày cụ thể.

    Args:
        date_str (str): Ngày dạng "YYYY-MM-DD"
        time_str (str): Thời gian dạng "HH:MM"

    Returns:
        str: Quartz cron expression (e.g., "0 MM HH DD MM ? YYYY")
             hoặc một cron mặc định hàng ngày nếu lỗi.
    """
    try:
        if not time_str or ':' not in time_str:
            time_str = "19:00"  # Giờ mặc định

        hour, minute = map(int, time_str.split(":")) # Chuyển sang số nguyên
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        # Tạo cron expression Quartz: Seconds Minute Hour DayOfMonth Month DayOfWeek Year
        # Vì đã chỉ định DayOfMonth, DayOfWeek phải là '?'
        # Năm là tùy chọn nhưng hữu ích cho ngày cụ thể
        quartz_cron = f"0 {minute} {hour} {date_obj.day} {date_obj.month} ? {date_obj.year}"
        logger.info(f"Generated Quartz cron for specific date {date_str} {time_str}: {quartz_cron}")
        return quartz_cron

    except Exception as e:
        logger.error(f"Lỗi khi tạo cron expression Quartz cho ngày cụ thể: {e}")
        # Fallback: Chạy hàng ngày lúc 19:00 theo định dạng Quartz
        return "0 0 19 ? * * *"
    
RECURRING_KEYWORDS = [
    # ... (keep the existing list) ...
    "hàng ngày", "mỗi ngày",
    "hàng tuần", "mỗi tuần",
    "hàng tháng", "mỗi tháng",
    "hàng năm", "mỗi năm",
    "định kỳ", "lặp lại",
    "mỗi sáng thứ", "mỗi trưa thứ", "mỗi chiều thứ", "mỗi tối thứ", # Chung chung + buổi
    "thứ 2 hàng tuần", "mỗi thứ 2", "mỗi t2",
    "thứ 3 hàng tuần", "mỗi thứ 3", "mỗi t3",
    "thứ 4 hàng tuần", "mỗi thứ 4", "mỗi t4",
    "thứ 5 hàng tuần", "mỗi thứ 5", "mỗi t5",
    "thứ 6 hàng tuần", "mỗi thứ 6", "mỗi t6", # Quan trọng cho ví dụ của bạn
    "thứ 7 hàng tuần", "mỗi thứ 7", "mỗi t7",
    "chủ nhật hàng tuần", "mỗi chủ nhật", "mỗi cn",
    # Tiếng Anh (phòng trường hợp)
    "daily", "every day",
    "weekly", "every week",
    "monthly", "every month",
    "yearly", "annually", "every year",
    "recurring", "repeating",
    "every monday", "every tuesday", "every wednesday", "every thursday",
    "every friday", "every saturday", "every sunday",
]

# Hàm xác định lặp lại (không thay đổi)
def determine_repeat_type(description, title):
    """
    Xác định kiểu lặp lại dựa trên mô tả và tiêu đề bằng cách kiểm tra từ khóa mở rộng.

    Args:
        description (str): Mô tả sự kiện
        title (str): Tiêu đề sự kiện

    Returns:
        str: "RECURRING" hoặc "ONCE"
    """
    if not description: description = ""
    if not title: title = ""

    combined_text = (description + " " + title).lower()

    for keyword in RECURRING_KEYWORDS:
        if keyword in combined_text:
            logger.info(f"Phát hiện từ khóa lặp lại '{keyword}' trong: '{combined_text}' -> RECURRING")
            return "RECURRING"

    logger.info(f"Không tìm thấy từ khóa lặp lại trong: '{combined_text}' -> ONCE")
    return "ONCE"  # Mặc định là chạy một lần

def generate_recurring_cron(description, title, time_str="19:00"):
    """
    Tạo cron expression định dạng Quartz cho các sự kiện lặp lại.
    Ưu tiên xử lý lặp lại hàng ngày và hàng tuần theo thứ.

    Args:
        description (str): Mô tả sự kiện
        title (str): Tiêu đề sự kiện
        time_str (str): Thời gian dạng "HH:MM"

    Returns:
        str: Quartz cron expression cho sự kiện lặp lại,
             hoặc cron mặc định hàng ngày nếu không xác định được.
    """
    try:
        if not time_str or ':' not in time_str:
            time_str = "19:00"
        hour, minute = map(int, time_str.split(":")) # Chuyển sang số nguyên

        combined_text = (str(description) + " " + str(title)).lower()

        # 1. Kiểm tra lặp lại hàng ngày
        if "hàng ngày" in combined_text or "mỗi ngày" in combined_text or "daily" in combined_text:
            # Quartz format: Seconds Minute Hour DayOfMonth Month DayOfWeek Year(optional)
            # Chạy hàng ngày: ? cho DayOfMonth, * cho DayOfWeek
            quartz_cron = f"0 {minute} {hour} ? * * *"
            logger.info(f"Tạo cron Quartz hàng ngày lúc {time_str}: {quartz_cron}")
            return quartz_cron

        # 2. Kiểm tra lặp lại hàng tuần theo thứ
        # Ánh xạ tiếng Việt sang số ngày trong tuần của Quartz (1=SUN, 2=MON, ..., 7=SAT)
        quartz_day_map = {
            "chủ nhật": 1, "cn": 1, "sunday": 1,
            "thứ 2": 2, "t2": 2, "monday": 2,
            "thứ 3": 3, "t3": 3, "tuesday": 3,
            "thứ 4": 4, "t4": 4, "wednesday": 4,
            "thứ 5": 5, "t5": 5, "thursday": 5,
            "thứ 6": 6, "t6": 6, "friday": 6, # Quan trọng
            "thứ 7": 7, "t7": 7, "saturday": 7
        }

        found_day_num = None
        found_day_text = ""
        for day_text, day_num in quartz_day_map.items():
            if re.search(r'\b' + re.escape(day_text) + r'\b', combined_text):
                found_day_num = day_num
                found_day_text = day_text
                logger.info(f"Tìm thấy ngày lặp lại: {found_day_text} (Quartz: {found_day_num})")
                break # Tìm thấy ngày đầu tiên là đủ

        if found_day_num is not None:
            # Kiểm tra xem có phải là hàng tuần không (để chắc chắn hơn)
            is_weekly = any(kw in combined_text for kw in ["hàng tuần", "mỗi tuần", "weekly", "every"])
            if is_weekly:
                # Quartz format: Chỉ định DayOfWeek, nên DayOfMonth là '?'
                quartz_cron = f"0 {minute} {hour} ? * {found_day_num} *"
                logger.info(f"Tạo cron Quartz hàng tuần vào thứ {found_day_text} ({found_day_num}) lúc {time_str}: {quartz_cron}")
                return quartz_cron
            else:
                # Nếu chỉ nói "thứ 6" mà không có "hàng tuần", có thể chỉ là 1 lần?
                # Tuy nhiên, hàm này chỉ nên được gọi khi determine_repeat_type đã là RECURRING
                # nên ta vẫn giả định là hàng tuần.
                logger.warning(f"Không rõ 'hàng tuần' nhưng vẫn tạo cron Quartz tuần vào thứ {found_day_text} ({found_day_num})")
                quartz_cron = f"0 {minute} {hour} ? * {found_day_num} *"
                return quartz_cron

        # 3. (Tùy chọn) Xử lý lặp lại hàng tháng (ví dụ đơn giản)
        # Ví dụ: "ngày 15 hàng tháng", "ngày cuối cùng hàng tháng"
        monthly_match = re.search(r"(ngày\s+(\d{1,2})|ngày\s+cuối\s+cùng)\s+(hàng\s+tháng|mỗi\s+tháng)", combined_text)
        if monthly_match:
            day_specifier = monthly_match.group(1)
            day_of_month = ""
            if "cuối cùng" in day_specifier:
                day_of_month = "L" # Quartz: L = Last day of month
            else:
                day_num_match = re.search(r'\d{1,2}', day_specifier)
                if day_num_match:
                    day_of_month = day_num_match.group(0)

            if day_of_month:
                # Quartz format: Chỉ định DayOfMonth, nên DayOfWeek là '?'
                quartz_cron = f"0 {minute} {hour} {day_of_month} * ? *"
                logger.info(f"Tạo cron Quartz hàng tháng vào ngày {day_of_month} lúc {time_str}: {quartz_cron}")
                return quartz_cron

        # 4. Fallback: Nếu không xác định được lịch cụ thể -> trả về cron hàng ngày
        logger.warning(f"Không thể xác định lịch lặp lại cụ thể từ '{combined_text}'. Dùng cron Quartz mặc định hàng ngày.")
        return f"0 {minute} {hour} ? * * *" # Fallback: lặp lại hàng ngày

    except Exception as e:
        logger.error(f"Lỗi khi tạo cron Quartz lặp lại: {e}")
        return "0 0 19 ? * * *" # Cron Quartz mặc định an toàn: 7PM hàng ngày

# ------- Request & Response Models ------------

class MessageContent(BaseModel):
    type: str  # "text", "image_url", "audio", "html" - Thêm loại "html"
    text: Optional[str] = None
    html: Optional[str] = None  # Thêm trường này để chứa nội dung HTML
    image_url: Optional[Dict[str, str]] = None
    audio_data: Optional[str] = None

class Message(BaseModel):
    role: str  # "user" hoặc "assistant"
    content: List[MessageContent]

class ChatRequest(BaseModel):
    session_id: str  # ID phiên làm việc
    member_id: Optional[str] = None  # ID thành viên (nếu có)
    message: MessageContent  # Tin nhắn mới nhất
    content_type: str = "text"  # THÊM TRƯỜNG MỚI: "text", "image", "audio"
    openai_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    messages: Optional[List[Message]] = None  # Optional để tương thích ngược


class ChatResponse(BaseModel):
    session_id: str
    messages: List[Message]
    audio_response: Optional[str] = None
    response_format: Optional[str] = "html"
    content_type: Optional[str] = "text"
    event_data: Optional[Dict[str, Any]] = None  # Thêm trường event_data

class MemberModel(BaseModel):
    name: str
    age: Optional[str] = None
    preferences: Optional[Dict[str, str]] = None

class EventModel(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
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
@app.post("/chat")
async def chat_endpoint(chat_request: ChatRequest):
    """
    Endpoint chính cho trò chuyện với trợ lý gia đình.
    Xử lý đầu vào là text, hình ảnh hoặc âm thanh.
    """
    # Xác thực API keys
    openai_api_key = chat_request.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    tavily_api_key = chat_request.tavily_api_key or os.getenv("TAVILY_API_KEY", "")
    
    if not openai_api_key or "sk-" not in openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ")
    
    # Lấy hoặc tạo session
    session = session_manager.get_session(chat_request.session_id)
    
    # Cập nhật member_id nếu có sự thay đổi
    if chat_request.member_id != session["current_member"]:
        session["current_member"] = chat_request.member_id
    
    # Nếu client cung cấp messages mới và messages hiện tại trống, cập nhật
    if chat_request.messages is not None and not session["messages"]:
        session["messages"] = [msg.dict() for msg in chat_request.messages]
    
    # Xử lý tin nhắn mới
    message_dict = chat_request.message.dict()
    
    # Ghi log loại content để debug
    logger.info(f"Nhận request với content_type: {chat_request.content_type}")
    
    # Xử lý âm thanh nếu có
    if chat_request.content_type == "audio" and message_dict.get("type") == "audio" and message_dict.get("audio_data"):
        message_dict = process_audio(message_dict, openai_api_key)
        logger.info(f"Đã xử lý audio thành text: {message_dict.get('text', '')[:50]}...")
    
    # Xử lý hình ảnh - không thay đổi message_dict nhưng ghi log
    elif chat_request.content_type == "image" and message_dict.get("type") == "image_url":
        logger.info(f"Đã nhận hình ảnh để xử lý: {message_dict.get('image_url', {}).get('url', '')[:50]}...")
    
    # Thêm tin nhắn vào danh sách messages
    session["messages"].append({
        "role": "user",
        "content": [message_dict]
    })
    
    # Lưu phiên ngay sau khi cập nhật tin nhắn người dùng
    session_manager.update_session(chat_request.session_id, {"messages": session["messages"]})
    
    # Xử lý phản hồi từ assistant
    try:
        # Xây dựng system prompt
        system_prompt = build_system_prompt(session["current_member"])
        
        # Lấy tin nhắn cuối cùng của người dùng để kiểm tra nhu cầu tìm kiếm
        search_result_for_prompt = await check_search_need(session["messages"], openai_api_key, tavily_api_key)
        if search_result_for_prompt:
            system_prompt += search_result_for_prompt
        
        # Khởi tạo OpenAI client
        client = OpenAI(api_key=openai_api_key)
        
        # Chuẩn bị messages cho OpenAI API
        openai_messages = [{"role": "system", "content": system_prompt}]
        
        # Thêm tất cả tin nhắn trước đó
        for message in session["messages"]:
            # Xử lý các tin nhắn hình ảnh hoặc đa phương tiện
            if any(content.get("type") == "image_url" for content in message["content"]):
                message_content = []
                
                # Thêm hình ảnh
                for content in message["content"]:
                    if content.get("type") == "image_url":
                        message_content.append({
                            "type": "image_url",
                            "image_url": {"url": content["image_url"]["url"]}
                        })
                    elif content.get("type") == "text":
                        message_content.append({
                            "type": "text",
                            "text": content["text"]
                        })
                
                openai_messages.append({
                    "role": message["role"],
                    "content": message_content
                })
            else:
                # Đối với tin nhắn chỉ có văn bản
                text_content = message["content"][0].get("text", "") if message["content"] else ""
                openai_messages.append({
                    "role": message["role"],
                    "content": text_content
                })
        
        # Gọi OpenAI API
        response = client.chat.completions.create(
            model=openai_model,
            messages=openai_messages,
            temperature=0.7,
            max_tokens=2048
        )
        
        # Lấy kết quả phản hồi
        assistant_response = response.choices[0].message.content
        
        # Xử lý lệnh đặc biệt trong phản hồi và làm sạch HTML
        cleaned_html, event_data = process_assistant_response(assistant_response, session["current_member"])
        
        # Thêm phản hồi đã làm sạch vào danh sách tin nhắn
        session["messages"].append({
            "role": "assistant",
            "content": [{"type": "html", "html": cleaned_html}]
        })
        
        # Lưu lịch sử chat nếu có current_member
        if session["current_member"]:
            summary = generate_chat_summary(session["messages"], openai_api_key)
            save_chat_history(session["current_member"], session["messages"], summary)
        
        # Cập nhật lại session lần cuối với tin nhắn mới nhất
        session_manager.update_session(chat_request.session_id, {"messages": session["messages"]})
        
        # Chuyển đổi văn bản thành giọng nói
        audio_response = text_to_speech_google(cleaned_html)
        
        # THAY ĐỔI: Chỉ giữ lại tin nhắn từ assistant trong response
        assistant_messages = [msg for msg in session["messages"] if msg["role"] == "assistant"]
        
        # Trả về kết quả với event_data nếu có
        return ChatResponse(
            session_id=chat_request.session_id,
            messages=assistant_messages,  # Chỉ trả về tin nhắn của trợ lý
            audio_response=audio_response,
            response_format="html",
            content_type=chat_request.content_type,  # Trả về loại content đã nhận
            event_data=event_data  # Trả về dữ liệu sự kiện nếu có
        )
        
    except Exception as e:
        logger.error(f"Lỗi trong quá trình xử lý chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {str(e)}")

@app.post("/chat/stream")
async def chat_stream_endpoint(chat_request: ChatRequest):
    """
    Endpoint streaming cho trò chuyện với trợ lý gia đình.
    Trả về phản hồi dạng stream.
    """
    # Xác thực API keys
    openai_api_key = chat_request.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    tavily_api_key = chat_request.tavily_api_key or os.getenv("TAVILY_API_KEY", "")
    
    if not openai_api_key or "sk-" not in openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ")
    
    # Lấy hoặc tạo session
    session = session_manager.get_session(chat_request.session_id)
    
    # Cập nhật member_id nếu có sự thay đổi
    if chat_request.member_id != session["current_member"]:
        session["current_member"] = chat_request.member_id
        session["messages"] = []
    
    # Nếu client cung cấp messages mới, cập nhật
    if chat_request.messages is not None:
        session["messages"] = [msg.dict() for msg in chat_request.messages]
    
    # Xử lý tin nhắn mới
    message_dict = chat_request.message.dict()
    
    # Ghi log loại content để debug
    logger.info(f"Nhận streaming request với content_type: {chat_request.content_type}")
    
    # Xử lý âm thanh nếu có
    if chat_request.content_type == "audio" and message_dict.get("type") == "audio" and message_dict.get("audio_data"):
        message_dict = process_audio(message_dict, openai_api_key)
        logger.info(f"Đã xử lý audio thành text: {message_dict.get('text', '')[:50]}...")
    
    # Xử lý hình ảnh - không thay đổi message_dict nhưng ghi log
    elif chat_request.content_type == "image" and message_dict.get("type") == "image_url":
        logger.info(f"Đã nhận hình ảnh để xử lý: {message_dict.get('image_url', {}).get('url', '')[:50]}...")
    
    # Thêm tin nhắn vào danh sách messages
    session["messages"].append({
        "role": "user",
        "content": [message_dict]
    })
    
    # Tạo generator để stream phản hồi
    async def response_stream_generator():
        try:
            # Xây dựng system prompt
            system_prompt = build_system_prompt(session["current_member"])
            
            # Kiểm tra nhu cầu search
            search_result_for_prompt = await check_search_need(session["messages"], openai_api_key, tavily_api_key)
            if search_result_for_prompt:
                system_prompt += search_result_for_prompt
            
            # Khởi tạo OpenAI client
            client = OpenAI(api_key=openai_api_key)
            
            # Chuẩn bị messages cho OpenAI API
            openai_messages = [{"role": "system", "content": system_prompt}]
            
            # Thêm tất cả tin nhắn trước đó
            for message in session["messages"]:
                # Xử lý tin nhắn đa phương tiện
                if any(content.get("type") == "image_url" for content in message["content"]):
                    message_content = []
                    
                    for content in message["content"]:
                        if content.get("type") == "image_url":
                            message_content.append({
                                "type": "image_url",
                                "image_url": {"url": content["image_url"]["url"]}
                            })
                        elif content.get("type") == "text":
                            message_content.append({
                                "type": "text",
                                "text": content["text"]
                            })
                    
                    openai_messages.append({
                        "role": message["role"],
                        "content": message_content
                    })
                else:
                    # Tin nhắn chỉ có văn bản
                    text_content = message["content"][0].get("text", "") if message["content"] else ""
                    openai_messages.append({
                        "role": message["role"],
                        "content": text_content
                    })
            
            # Gọi OpenAI API với stream=True
            stream = client.chat.completions.create(
                model=openai_model,
                messages=openai_messages,
                temperature=0.7,
                max_tokens=2048,
                stream=True
            )
            
            full_response = ""
            
            # Stream từng phần phản hồi
            for chunk in stream:
                chunk_text = chunk.choices[0].delta.content or ""
                full_response += chunk_text
                
                # Trả về từng phần phản hồi dưới dạng JSON lines
                if chunk_text:
                    yield json.dumps({"chunk": chunk_text, "type": "html", "content_type": chat_request.content_type}) + "\n"
                    
                    # Đảm bảo chunk được gửi ngay lập tức
                    await asyncio.sleep(0)
            
            # Khi stream kết thúc, xử lý phản hồi đầy đủ
            cleaned_html, event_data = process_assistant_response(full_response, session["current_member"])
            
            # Lưu phản hồi đã làm sạch vào session
            session["messages"].append({
                "role": "assistant",
                "content": [{"type": "html", "html": cleaned_html}]
            })
            
            # Lưu lịch sử chat
            if session["current_member"]:
                summary = generate_chat_summary(session["messages"], openai_api_key)
                save_chat_history(session["current_member"], session["messages"], summary)
            
            # THAY ĐỔI: Chỉ giữ lại tin nhắn từ assistant trong response
            assistant_messages = [msg for msg in session["messages"] if msg["role"] == "assistant"]
            
            # Gửi tin nhắn phản hồi cuối cùng kèm event_data nếu có
            complete_response = {
                "complete": True,
                "messages": assistant_messages,  # Chỉ trả về tin nhắn của trợ lý
                "audio_response": text_to_speech_google(cleaned_html),
                "content_type": chat_request.content_type
            }
            
            # Thêm event_data nếu có
            if event_data:
                complete_response["event_data"] = event_data
            
            yield json.dumps(complete_response) + "\n"
            
        except Exception as e:
            logger.error(f"Lỗi trong quá trình stream: {str(e)}")
            error_msg = f"Có lỗi xảy ra: {str(e)}"
            yield json.dumps({"error": error_msg, "content_type": chat_request.content_type}) + "\n"
    
    # Trả về StreamingResponse
    return StreamingResponse(
        response_stream_generator(),
        media_type="application/x-ndjson"
    )

@app.get("/family_members")
async def get_family_members():
    """Trả về danh sách thành viên gia đình"""
    return family_data

@app.post("/family_members")
async def add_family_member_endpoint(member: MemberModel):
    """Thêm thành viên gia đình mới"""
    member_id = str(len(family_data) + 1)
    family_data[member_id] = {
        "name": member.name,
        "age": member.age or "",
        "preferences": member.preferences or {},
        "added_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(FAMILY_DATA_FILE, family_data)
    return {"id": member_id, "member": family_data[member_id]}

@app.get("/events")
async def get_events(member_id: Optional[str] = None):
    """Lấy danh sách sự kiện, có thể lọc theo thành viên"""
    if member_id:
        filtered_events = filter_events_by_member(member_id)
        return filtered_events
    return events_data

@app.post("/events")
async def add_event_endpoint(event: EventModel, member_id: Optional[str] = None):
    """Thêm sự kiện mới"""
    event_id = str(len(events_data) + 1)
    events_data[event_id] = {
        "title": event.title,
        "date": event.date,
        "time": event.time or "19:00",
        "description": event.description or "",
        "participants": event.participants or [],
        "created_by": member_id,
        "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(EVENTS_DATA_FILE, events_data)
    return {"id": event_id, "event": events_data[event_id]}

@app.get("/notes")
async def get_notes(member_id: Optional[str] = None):
    """Lấy danh sách ghi chú, có thể lọc theo thành viên"""
    if member_id:
        filtered_notes = {note_id: note for note_id, note in notes_data.items() 
                        if note.get("created_by") == member_id}
        return filtered_notes
    return notes_data

@app.post("/notes")
async def add_note_endpoint(note: NoteModel, member_id: Optional[str] = None):
    """Thêm ghi chú mới"""
    note_id = str(len(notes_data) + 1)
    notes_data[note_id] = {
        "title": note.title,
        "content": note.content,
        "tags": note.tags or [],
        "created_by": member_id,
        "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(NOTES_DATA_FILE, notes_data)
    return {"id": note_id, "note": notes_data[note_id]}

@app.post("/search")
async def search_endpoint(search_request: SearchRequest):
    """Tìm kiếm thông tin thời gian thực"""
    if not search_request.tavily_api_key or not search_request.openai_api_key:
        raise HTTPException(status_code=400, detail="API keys không hợp lệ")
    
    # Xác định có giới hạn domain không
    domains_to_include = VIETNAMESE_NEWS_DOMAINS if search_request.is_news_query else None
    
    # Thực hiện tìm kiếm
    result = search_and_summarize(
        search_request.tavily_api_key,
        search_request.query,
        search_request.openai_api_key,
        include_domains=domains_to_include
    )
    
    return {"query": search_request.query, "result": result}

@app.post("/session")
async def create_session():
    """Tạo phiên làm việc mới"""
    session_id = str(uuid.uuid4())
    session_manager.get_session(session_id)
    return {"session_id": session_id}

@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Xóa phiên làm việc"""
    if session_manager.delete_session(session_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Phiên làm việc không tồn tại")

@app.get("/suggested_questions")
async def get_suggested_questions(
    session_id: str,
    member_id: Optional[str] = None,
    openai_api_key: Optional[str] = None
):
    """
    Endpoint riêng biệt để lấy câu hỏi gợi ý cho người dùng
    """
    # Xác thực API key
    api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
    
    if not api_key or "sk-" not in api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key không hợp lệ")
    
    # Lấy session nếu tồn tại
    session = session_manager.get_session(session_id)
    
    # Nếu member_id được cung cấp, sử dụng nó. Nếu không, thử dùng member_id từ session
    current_member_id = member_id or session.get("current_member")
    
    # Tạo câu hỏi gợi ý
    suggested_questions = generate_dynamic_suggested_questions(
        api_key,
        current_member_id,
        max_questions=5
    )
    
    # Tạo timestamp hiện tại
    current_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Lưu câu hỏi gợi ý vào session (để có thể tái sử dụng nếu cần)
    session["suggested_question"] = suggested_questions
    session["question_timestamp"] = current_timestamp
    
    # Trả về kết quả
    return SuggestedQuestionsResponse(
        session_id=session_id,
        member_id=current_member_id,
        suggested_questions=suggested_questions,
        timestamp=current_timestamp
    )

# 6. Thêm endpoint để lấy các câu hỏi gợi ý đã tạo trước đó (nếu có)

@app.get("/cached_suggested_questions")
async def get_cached_suggested_questions(session_id: str):
    """
    Lấy câu hỏi gợi ý đã tạo trước đó trong session, nếu có
    """
    session = session_manager.get_session(session_id)
    
    suggested_questions = session.get("suggested_question", [])
    timestamp = session.get("question_timestamp", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    if not suggested_questions:
        # Nếu không có câu hỏi đã lưu, trả về danh sách trống
        return SuggestedQuestionsResponse(
            session_id=session_id,
            member_id=session.get("current_member"),
            suggested_questions=[],
            timestamp=timestamp
        )
    
    # Nếu có câu hỏi đã lưu, trả về chúng
    return SuggestedQuestionsResponse(
        session_id=session_id,
        member_id=session.get("current_member"),
        suggested_questions=suggested_questions,
        timestamp=timestamp
    )

# ------- Các hàm xử lý từ ứng dụng Streamlit gốc -------------

# Hàm xử lý audio và chuyển thành text
def process_audio(message_dict, api_key):
    try:
        # Giải mã dữ liệu audio base64
        audio_data = base64.b64decode(message_dict["audio_data"])
        
        # Lưu tạm vào file
        temp_audio_file = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.wav")
        with open(temp_audio_file, "wb") as f:
            f.write(audio_data)
        
        # Chuyển đổi âm thanh thành văn bản
        client = OpenAI(api_key=api_key)
        with open(temp_audio_file, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
            )
        
        # Xóa file tạm
        os.remove(temp_audio_file)
        
        # Trả về message dạng text
        return {
            "type": "text",
            "text": transcript.text
        }
    except Exception as e:
        logger.error(f"Lỗi khi xử lý audio: {str(e)}")
        # Trả về thông báo lỗi nếu xử lý audio thất bại
        return {
            "type": "text",
            "text": f"[Không thể xử lý audio: {str(e)}]"
        }

# Hàm xây dựng system prompt
def build_system_prompt(current_member_id=None):
    """Xây dựng system prompt cho trợ lý gia đình"""
    system_prompt = f"""
    Bạn là trợ lý gia đình thông minh. Nhiệm vụ của bạn là giúp quản lý thông tin về các thành viên trong gia đình, 
    sở thích của họ, các sự kiện, ghi chú, và phân tích hình ảnh liên quan đến gia đình.
    
    ĐỊNH DẠNG PHẢN HỒI:
    Phản hồi của bạn phải được định dạng bằng HTML đơn giản. Sử dụng các thẻ HTML thích hợp để định dạng:
    - Sử dụng thẻ <p> cho đoạn văn
    - Sử dụng thẻ <b> hoặc <strong> cho văn bản in đậm
    - Sử dụng thẻ <i> hoặc <em> cho văn bản in nghiêng
    - Sử dụng thẻ <h3>, <h4> cho tiêu đề
    - Sử dụng thẻ <ul> và <li> cho danh sách không có thứ tự
    - Sử dụng thẻ <ol> và <li> cho danh sách có thứ tự
    - Sử dụng thẻ <br> để xuống dòng trong đoạn văn
    
    Khi người dùng yêu cầu, bạn phải thực hiện ngay các hành động sau:
    
    1. Thêm thông tin về thành viên gia đình (tên, tuổi, sở thích)
    2. Cập nhật sở thích của thành viên gia đình
    3. Thêm, cập nhật, hoặc xóa sự kiện
    4. Thêm ghi chú
    5. Phân tích hình ảnh người dùng đưa ra (món ăn, hoạt động gia đình, v.v.)
    6. Tìm kiếm thông tin thực tế khi được hỏi về tin tức, thời tiết, thể thao, và sự kiện hiện tại
    7. Tư vấn về trang phục, hoạt động hoặc đồ dùng dựa trên thời tiết
    
    QUAN TRỌNG: Khi cần thực hiện các hành động trên, bạn PHẢI sử dụng đúng cú pháp lệnh đặc biệt này (người dùng sẽ không nhìn thấy):
    
    - Thêm thành viên: ##ADD_FAMILY_MEMBER:{{"name":"Tên","age":"Tuổi","preferences":{{"food":"Món ăn","hobby":"Sở thích","color":"Màu sắc"}}}}##
    - Cập nhật sở thích: ##UPDATE_PREFERENCE:{{"id":"id_thành_viên","key":"loại_sở_thích","value":"giá_trị"}}##
    - Thêm sự kiện: ##ADD_EVENT:{{"title":"Tiêu đề","date":"YYYY-MM-DD","time":"HH:MM","description":"Mô tả","participants":["Tên1","Tên2"]}}##
    - Cập nhật sự kiện: ##UPDATE_EVENT:{{"id":"id_sự_kiện","title":"Tiêu đề mới","date":"YYYY-MM-DD","time":"HH:MM","description":"Mô tả mới","participants":["Tên1","Tên2"]}}##
    - Xóa sự kiện: ##DELETE_EVENT:id_sự_kiện##
    - Thêm ghi chú: ##ADD_NOTE:{{"title":"Tiêu đề","content":"Nội dung","tags":["tag1","tag2"]}}##
    
    QUY TẮC THÊM/CẬP NHẬT SỰ KIỆN ĐƠN GIẢN (SỰ KIỆN MỘT LẦN):
    1. Khi được yêu cầu thêm/cập nhật sự kiện MỘT LẦN, hãy thực hiện NGAY LẬP TỨC bằng lệnh ##ADD_EVENT## hoặc ##UPDATE_EVENT##.
    2. **TÍNH TOÁN NGÀY CHÍNH XÁC:** Khi người dùng nói "ngày mai", "hôm qua", "thứ 2 tuần sau", "chủ nhật tới", v.v., bạn PHẢI tự động tính toán và điền ngày chính xác theo định dạng YYYY-MM-DD vào trường `date`.
        - **Ví dụ:** Nếu hôm nay là Thứ Sáu 04/04/2025, và người dùng yêu cầu "tối thứ 2 tuần sau", bạn phải tính ra ngày Thứ Hai tuần kế tiếp là 07/04/2025 và điền `"date":"2025-04-07"`.
        - **Ví dụ:** Nếu hôm nay là Thứ Hai 07/04/2025, và người dùng yêu cầu "chiều thứ 6", bạn phải tính ra ngày Thứ Sáu sắp tới trong tuần là 11/04/2025 và điền `"date":"2025-04-11"`.
    3. Nếu không có thời gian cụ thể, sử dụng thời gian mặc định là 19:00 trong trường `time` (HH:MM).
    4. Sử dụng mô tả ngắn gọn từ yêu cầu của người dùng trong trường `description`.
    5. Chỉ hỏi thêm thông tin nếu thực sự cần thiết và không thể suy luận được (ví dụ: tiêu đề sự kiện không rõ).
    6. Sau khi thêm/cập nhật/xóa sự kiện, tóm tắt ngắn gọn hành động đã thực hiện trong phần văn bản trả lời cho người dùng.

    ***LƯU Ý ĐẶC BIỆT VỀ SỰ KIỆN LẶP LẠI (RECURRING):***
    - Chỉ coi là sự kiện lặp lại nếu người dùng sử dụng các từ khóa rõ ràng như "hàng tuần", "mỗi ngày", "hàng tháng", "ngày 15 hàng tháng", "mỗi tối thứ 6", "định kỳ", v.v.
    - **KHÔNG** coi "thứ 2 tuần sau" là lặp lại. Đó là sự kiện MỘT LẦN.
    - Khi tạo sự kiện lặp lại:
        - **KHÔNG** đặt một ngày cụ thể (YYYY-MM-DD) cố định vào trường `date`.
        - Thay vào đó, hãy đặt một ngày **diễn ra gần nhất** của sự kiện đó vào trường `date`. Ví dụ, yêu cầu "mỗi tối thứ 6 hàng tuần", nếu hôm nay là thứ 2, đặt ngày thứ 6 sắp tới vào `date`.
        - **QUAN TRỌNG NHẤT:** Đảm bảo mô tả chi tiết về sự lặp lại nằm trong trường `description` (ví dụ: "Học tiếng Anh vào mỗi tối thứ 6 hàng tuần."). Hệ thống backend sẽ dùng mô tả này để xử lý.
    - Ví dụ yêu cầu lặp lại: "Thêm lịch học tiếng anh vào tối thứ 6 hàng tuần"
    - Ví dụ LỆNH ĐÚNG (giả sử hôm nay là T2 07/04/2025): `##ADD_EVENT:{{"title":"Lịch học tiếng Anh","date":"2025-04-11","time":"19:00","description":"Học tiếng Anh vào mỗi tối thứ 6 hàng tuần.","participants":[]}}##`

    ***QUY TẮC TƯ VẤN DỰA TRÊN THỜI TIẾT:***
    Khi người dùng hỏi những câu như "Ngày mai nên mặc gì?", "Cuối tuần nên đi chơi ở đâu?", "Tôi nên mang theo gì khi đi Đà Lạt?", hãy tư vấn dựa trên thông tin thời tiết mà không cần hiển thị chi tiết dự báo thời tiết.
    
    Ví dụ phản hồi tốt cho câu hỏi tư vấn trang phục: "Dựa vào thông tin thời tiết, với nhiệt độ từ 18°C đến 26°C và tình hình mây đen, bạn nên chọn trang phục thoải mái cho ngày mai. Một gợi ý là hãy mặc áo sơ mi hoặc áo thun dài tay, kết hợp với quần dài làm từ vải nhẹ và thoáng khí. Đừng quên mang theo một chiếc áo khoác mỏng để giữ ấm vào buổi sáng sớm hoặc tối nhé!"
    
    Ví dụ ứng dụng cho các loại tư vấn:
    - Tư vấn trang phục: khi người dùng hỏi "nên mặc gì", "trang phục", "quần áo"...
    - Tư vấn hoạt động: khi người dùng hỏi "nên làm gì", "nên đi đâu", "đi chơi"...
    - Tư vấn đồ dùng: khi người dùng hỏi "nên mang", "mang theo", "cần chuẩn bị"...

    Hôm nay là {datetime.datetime.now().strftime("%d/%m/%Y (%A)")}.


    CẤU TRÚC JSON PHẢI CHÍNH XÁC như trên. Đảm bảo dùng dấu ngoặc kép cho cả keys và values. Đảm bảo các dấu ngoặc nhọn và vuông được đóng đúng cách.

    QUAN TRỌNG: Khi người dùng yêu cầu tạo sự kiện mới, hãy luôn sử dụng lệnh ##ADD_EVENT:...## trong phản hồi của bạn mà không cần quá nhiều bước xác nhận.

    Đối với hình ảnh:
    - Nếu người dùng gửi hình ảnh món ăn, hãy mô tả món ăn, và đề xuất cách nấu hoặc thông tin dinh dưỡng nếu phù hợp
    - Nếu là hình ảnh hoạt động gia đình, hãy mô tả hoạt động và đề xuất cách ghi nhớ khoảnh khắc đó
    - Với bất kỳ hình ảnh nào, hãy giúp người dùng liên kết nó với thành viên gia đình hoặc sự kiện nếu phù hợp
    """

    # Thêm thông tin về người dùng hiện tại (giữ nguyên)
    if current_member_id and current_member_id in family_data:
        current_member = family_data[current_member_id]
        system_prompt += f"""
        THÔNG TIN NGƯỜI DÙNG HIỆN TẠI:
        Bạn đang trò chuyện với: {current_member.get('name')}
        Tuổi: {current_member.get('age', '')}
        Sở thích: {json.dumps(current_member.get('preferences', {}), ensure_ascii=False)}

        QUAN TRỌNG: Hãy điều chỉnh cách giao tiếp và đề xuất phù hợp với người dùng này. Các sự kiện và ghi chú sẽ được ghi danh nghĩa người này tạo.
        """

    # Thêm thông tin dữ liệu (giữ nguyên)
    system_prompt += f"""
    Thông tin hiện tại về gia đình:
    {json.dumps(family_data, ensure_ascii=False, indent=2)}

    Sự kiện sắp tới:
    {json.dumps(events_data, ensure_ascii=False, indent=2)}

    Ghi chú:
    {json.dumps(notes_data, ensure_ascii=False, indent=2)}

    Hãy hiểu và đáp ứng nhu cầu của người dùng một cách tự nhiên và hữu ích. Không hiển thị các lệnh đặc biệt
    trong phản hồi của bạn, chỉ sử dụng chúng để thực hiện các hành động được yêu cầu.
    """

    return system_prompt

# Kiểm tra nhu cầu tìm kiếm và thực hiện tìm kiếm
async def check_search_need(messages, openai_api_key, tavily_api_key):
    """
    Kiểm tra nhu cầu tìm kiếm thông tin từ tin nhắn của người dùng, bao gồm:
    - Thông tin thời tiết (với hỗ trợ các ngày trong tương lai)
    - Tư vấn dựa trên thời tiết (trang phục, hoạt động, đồ dùng)
    - Tìm kiếm thông tin thực tế qua web
    """
    if not tavily_api_key:
        return ""
    
    try:
        # Lấy tin nhắn người dùng mới nhất
        last_user_message = ""
        for message in reversed(messages):
            if message["role"] == "user" and message["content"][0]["type"] == "text":
                last_user_message = message["content"][0]["text"]
                break
        
        if not last_user_message:
            return ""
        
        # PHẦN MỚI: Phát hiện câu hỏi tư vấn dựa trên thời tiết
        is_advice_query, advice_type, time_term = weather_advisor.detect_advice_query(last_user_message)
        
        if is_advice_query:
            logger.info(f"Phát hiện truy vấn tư vấn: loại={advice_type}, thời gian={time_term}")
            
            # Tính toán ngày cụ thể từ cụm từ thời gian
            target_date = None
            if time_term and time_term != "hôm nay":
                target_date = weather_service.get_date_from_relative_term(time_term)
                logger.info(f"Đã tính toán ngày từ '{time_term}': {target_date}")
            
            # Lấy thông tin vị trí (mặc định là Hà Nội)
            # Tách vị trí từ câu hỏi nếu có
            location = "Hà Nội"  # Mặc định
            location_patterns = [r'ở\s+([^?.,!]+)', r'tại\s+([^?.,!]+)']
            for pattern in location_patterns:
                location_match = re.search(pattern, last_user_message, re.IGNORECASE)
                if location_match:
                    location = location_match.group(1).strip()
                    break
            
            try:
                # Lấy dữ liệu thời tiết
                days_needed = 7 if target_date else 1  # Đảm bảo có đủ dữ liệu dự báo
                weather_data = await weather_service.get_weather(
                    location=location, 
                    forecast_days=days_needed,
                    target_date=target_date
                )
                
                # Lấy tư vấn phù hợp với loại câu hỏi
                advice_result = ""
                if advice_type == "clothing":
                    advice_result = weather_advisor.get_clothing_advice(weather_data, target_date)
                elif advice_type == "activity":
                    advice_result = weather_advisor.get_activity_advice(weather_data, target_date)
                elif advice_type == "items":
                    advice_result = weather_advisor.get_items_advice(weather_data, target_date)
                
                # Định dạng kết quả thời tiết để tham khảo trong system prompt
                # Nhưng KHÔNG hiển thị cho người dùng
                weather_html = weather_service.format_weather_message(
                    weather_data,
                    location,
                    1,  # Chỉ hiển thị 1 ngày
                    target_date
                )
                
                # Chuẩn bị thông tin để thêm vào system prompt
                date_info = f" vào ngày {target_date}" if target_date else ""
                time_term_display = f" ({time_term})" if time_term and time_term != "hôm nay" else ""
                
                # THAY ĐỔI: Format phản hồi chỉ trả về phần tư vấn, không hiển thị thời tiết
                advice_result_for_prompt = f"""
                \n\n--- TƯ VẤN DỰA TRÊN THỜI TIẾT ---
                Người dùng đã hỏi: "{last_user_message}"
                
                Dưới đây là thông tin thời tiết tại {location}{date_info}{time_term_display} (CHỈ ĐỂ THAM KHẢO, KHÔNG HIỂN THỊ CHO NGƯỜI DÙNG):
                
                {weather_html}
                
                Dựa vào thông tin thời tiết trên, đây là tư vấn cho người dùng:
                
                {advice_result}
                --- KẾT THÚC TƯ VẤN ---

                Hãy trả lời người dùng CHỈ với phần tư vấn dựa trên thông tin thời tiết. KHÔNG ĐƯỢC hiển thị dữ liệu thời tiết chi tiết (nhiệt độ, điều kiện, dự báo theo giờ), mà chỉ trình bày phần tư vấn liên quan. 
                
                Ví dụ phản hồi tốt: "Dựa vào thông tin thời tiết, với nhiệt độ từ 18°C đến 26°C và tình hình mây đen, bạn nên chọn trang phục thoải mái cho ngày mai. Một gợi ý là hãy mặc áo sơ mi hoặc áo thun dài tay, kết hợp với quần dài làm từ vải nhẹ và thoáng khí. Đừng quên mang theo một chiếc áo khoác mỏng để giữ ấm vào buổi sáng sớm hoặc tối nhé!"
                
                Hãy kết hợp các thông số cần thiết về thời tiết (như nhiệt độ, điều kiện mưa/nắng) vào lời khuyên của mình, nhưng KHÔNG liệt kê các thông số dự báo chi tiết.
                """
                
                return advice_result_for_prompt
                
            except Exception as weather_err:
                logger.error(f"Lỗi khi lấy thông tin thời tiết cho tư vấn: {weather_err}", exc_info=True)
                # Nếu lỗi, chuyển sang tìm kiếm thông thường
        
        # PHẦN HIỆN TẠI: Phát hiện câu hỏi thời tiết thông thường
        is_weather_query, location, days, time_term = weather_service.detect_weather_query(last_user_message)
        
        if is_weather_query and location:
            logger.info(f"Phát hiện truy vấn thời tiết: vị trí={location}, cụm từ thời gian='{time_term}'")
            
            # Tính toán ngày cụ thể từ cụm từ thời gian
            target_date = None
            if time_term and time_term != "hôm nay":
                target_date = weather_service.get_date_from_relative_term(time_term)
                logger.info(f"Đã tính toán ngày từ '{time_term}': {target_date}")
            
            try:
                # Gọi dịch vụ thời tiết để lấy dữ liệu
                weather_data = await weather_service.get_weather(
                    location=location, 
                    forecast_days=days,
                    target_date=target_date
                )
                
                # Định dạng kết quả thành HTML đẹp
                weather_html = weather_service.format_weather_message(
                    weather_data,
                    location,
                    days,
                    target_date
                )
                
                # Chuẩn bị thông tin để thêm vào system prompt
                date_info = f" vào ngày {target_date}" if target_date else ""
                time_term_display = f" ({time_term})" if time_term and time_term != "hôm nay" else ""
                
                weather_result_for_prompt = f"""
                \n\n--- THÔNG TIN THỜI TIẾT ---
                Người dùng đã hỏi về thời tiết: "{last_user_message}"
                
                Dưới đây là dự báo thời tiết chính xác cho {location}{date_info}{time_term_display}:
                
                {weather_html}
                --- KẾT THÚC THÔNG TIN THỜI TIẾT ---

                Hãy trả lời người dùng sử dụng thông tin thời tiết chính xác ở trên. Hãy TRÌNH BÀY THÔNG TIN này theo văn phong tự nhiên, thân thiện và ngắn gọn. 
                KHÔNG ĐƯỢC copy nguyên văn, hãy diễn đạt lại nhưng vẫn giữ đúng tất cả các thông số về nhiệt độ, điều kiện, gió, độ ẩm và dự báo.
                Nếu người dùng hỏi về ngày cụ thể như "thứ 3 tuần sau", hãy ĐẢM BẢO đề cập rõ ràng đó là thông tin thời tiết cho ngày đó.
                """
                
                return weather_result_for_prompt
                
            except Exception as weather_err:
                logger.error(f"Lỗi khi lấy thông tin thời tiết: {weather_err}", exc_info=True)
                # Nếu lỗi, thì vẫn tiến hành tìm kiếm thông thường như Plan B
        
        # Phát hiện ý định tìm kiếm thông thường
        need_search, search_query, is_news_query = detect_search_intent(last_user_message, openai_api_key)
        
        if need_search:
            # Quyết định có lọc domain hay không dựa trên is_news_query
            domains_to_include = VIETNAMESE_NEWS_DOMAINS if is_news_query else None
            
            # Thực hiện tìm kiếm
            logger.info(f"Tìm kiếm thông tin cho: '{search_query}'")
            search_result = search_and_summarize(
                tavily_api_key,
                search_query,
                openai_api_key,
                include_domains=domains_to_include
            )
            
            # Chuẩn bị thông tin để thêm vào system prompt
            search_result_for_prompt = f"""
            \n\n--- THÔNG TIN TÌM KIẾM THAM KHẢO ---
            Người dùng đã hỏi: "{last_user_message}"
            Truy vấn tìm kiếm được sử dụng: "{search_query}"
            {'Tìm kiếm giới hạn trong các trang tin tức uy tín.' if is_news_query else ''}

            Kết quả tổng hợp từ tìm kiếm:
            {search_result}
            --- KẾT THÚC THÔNG TIN TÌM KIẾM ---

            Hãy sử dụng kết quả tổng hợp này để trả lời câu hỏi của người dùng một cách tự nhiên. Đảm bảo thông tin bạn cung cấp dựa trên kết quả này và đề cập nguồn nếu có thể.
            """
            
            return search_result_for_prompt
        
        return ""
    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra nhu cầu tìm kiếm: {str(e)}", exc_info=True)
        return ""


# Định nghĩa lại các hàm từ ứng dụng gốc

# TAVILY API FUNCTIONS
def tavily_extract(api_key, urls, include_images=False, extract_depth="basic"):
    """
    Trích xuất nội dung từ URL sử dụng Tavily Extract API
    
    Args:
        api_key (str): Tavily API Key
        urls (str/list): URL hoặc danh sách URL cần trích xuất
        include_images (bool): Có bao gồm hình ảnh hay không
        extract_depth (str): Độ sâu trích xuất ('basic' hoặc 'advanced')
        
    Returns:
        dict: Kết quả trích xuất hoặc None nếu có lỗi
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "urls": urls,
        "include_images": include_images,
        "extract_depth": extract_depth
    }
    
    try:
        response = requests.post(
            "https://api.tavily.com/extract",
            headers=headers,
            json=data
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Lỗi Tavily Extract: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Lỗi khi gọi Tavily API: {e}")
        return None

def tavily_search(api_key, query, search_depth="advanced", max_results=5, include_domains=None, exclude_domains=None):
    """
    Thực hiện tìm kiếm thời gian thực sử dụng Tavily Search API

    Args:
        api_key (str): Tavily API Key
        query (str): Câu truy vấn tìm kiếm
        search_depth (str): Độ sâu tìm kiếm ('basic' hoặc 'advanced')
        max_results (int): Số lượng kết quả tối đa
        include_domains (list, optional): Danh sách domain muốn bao gồm. Defaults to None.
        exclude_domains (list, optional): Danh sách domain muốn loại trừ. Defaults to None.

    Returns:
        dict: Kết quả tìm kiếm hoặc None nếu có lỗi
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results
    }

    if include_domains:
        data["include_domains"] = include_domains
        logger.info(f"Tavily Search giới hạn trong domains: {include_domains}")

    if exclude_domains:
        data["exclude_domains"] = exclude_domains

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers=headers,
            json=data
        )

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Lỗi Tavily Search: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Lỗi khi gọi Tavily Search API: {e}")
        return None

def search_and_summarize(tavily_api_key, query, openai_api_key, include_domains=None):
    """
    Tìm kiếm (có thể giới hạn domain) và tổng hợp thông tin từ kết quả tìm kiếm.

    Args:
        tavily_api_key (str): Tavily API Key
        query (str): Câu truy vấn tìm kiếm
        openai_api_key (str): OpenAI API Key
        include_domains (list, optional): Danh sách domain để giới hạn tìm kiếm. Defaults to None.

    Returns:
        str: Thông tin đã được tổng hợp
    """
    if not tavily_api_key or not openai_api_key or not query:
        return "Thiếu thông tin để thực hiện tìm kiếm hoặc tổng hợp."

    try:
        # Thực hiện tìm kiếm với Tavily, truyền include_domains
        search_results = tavily_search(
            tavily_api_key,
            query,
            include_domains=include_domains
        )

        if not search_results or "results" not in search_results or not search_results["results"]:
            return f"Không tìm thấy kết quả nào cho truy vấn '{query}'" + (f" trong các trang tin tức được chỉ định." if include_domains else ".")


        # Trích xuất thông tin từ top kết quả
        urls_to_extract = [result["url"] for result in search_results["results"][:3]]
        extracted_contents = []

        # Tối ưu: Chỉ trích xuất từ các domain mong muốn nếu đã lọc
        valid_urls_for_extraction = []
        if include_domains:
             for url in urls_to_extract:
                 if any(domain in url for domain in include_domains):
                     valid_urls_for_extraction.append(url)
                 else:
                      logger.warning(f"URL {url} không thuộc domain được lọc, bỏ qua trích xuất.")
             if not valid_urls_for_extraction:
                 logger.warning("Không còn URL hợp lệ nào sau khi lọc domain để trích xuất.")
                 sources_info_only = "\n\n**Nguồn tham khảo (chưa trích xuất được nội dung):**\n" + "\n".join([f"- {result['url']}" for result in search_results["results"][:3]])
                 return f"Đã tìm thấy một số nguồn liên quan đến '{query}' nhưng không thể trích xuất nội dung từ các trang tin tức được chỉ định.{sources_info_only}"
        else:
             valid_urls_for_extraction = urls_to_extract

        logger.info(f"Các URL sẽ được trích xuất: {valid_urls_for_extraction}")

        for url in valid_urls_for_extraction:
            extract_result = tavily_extract(tavily_api_key, url)
            if extract_result and "results" in extract_result and len(extract_result["results"]) > 0:
                content = extract_result["results"][0].get("raw_content", "")
                # Giới hạn độ dài nội dung để tránh token quá nhiều
                if len(content) > 5000:
                    content = content[:5000] + "..."
                extracted_contents.append({
                    "url": url,
                    "content": content
                })
            else:
                logger.warning(f"Không thể trích xuất nội dung từ URL: {url}")


        if not extracted_contents:
             # Thử trả về thông tin cơ bản từ kết quả search nếu không trích xuất được
             basic_info = ""
             for res in search_results.get("results", [])[:3]:
                 basic_info += f"- **{res.get('title', 'Không có tiêu đề')}**: {res.get('url')}\n"
             if basic_info:
                  return f"Không thể trích xuất chi tiết nội dung, nhưng đây là một số kết quả tìm thấy cho '{query}':\n{basic_info}"
             else:
                 return f"Không thể trích xuất nội dung từ các kết quả tìm kiếm cho '{query}'."


        # Tổng hợp thông tin sử dụng OpenAI
        client = OpenAI(api_key=openai_api_key)

        prompt = f"""
        Dưới đây là nội dung trích xuất từ các trang tin tức liên quan đến câu hỏi: "{query}"

        Nguồn dữ liệu:
        {json.dumps(extracted_contents, ensure_ascii=False, indent=2)}

        Nhiệm vụ của bạn:
        1.  **Tổng hợp thông tin chính:** Phân tích và tổng hợp các thông tin quan trọng nhất từ các nguồn trên để trả lời cho câu hỏi "{query}".
        2.  **Tập trung vào ngày cụ thể (nếu có):** Nếu câu hỏi đề cập đến một ngày cụ thể (ví dụ: hôm nay, 26/03,...), hãy ưu tiên các sự kiện và tin tức diễn ra vào ngày đó được đề cập trong các bài viết.
        3.  **Trình bày rõ ràng:** Viết một bản tóm tắt mạch lạc, có cấu trúc như một bản tin ngắn gọn.
        4.  **Xử lý mâu thuẫn:** Nếu có thông tin trái ngược giữa các nguồn, hãy nêu rõ điều đó.
        5.  **Nêu nguồn:** Luôn trích dẫn nguồn (URL) cho thông tin bạn tổng hợp, tốt nhất là đặt ngay sau đoạn thông tin tương ứng hoặc cuối bản tóm tắt.
        6.  **Phạm vi:** Chỉ sử dụng thông tin từ các nguồn được cung cấp ở trên. Không bịa đặt hoặc thêm kiến thức bên ngoài.

        Hãy bắt đầu bản tóm tắt của bạn.
        """

        response = client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": "Bạn là một trợ lý tổng hợp tin tức chuyên nghiệp. Nhiệm vụ của bạn là tổng hợp thông tin từ các nguồn được cung cấp để tạo ra một bản tin chính xác, tập trung vào yêu cầu của người dùng và luôn trích dẫn nguồn."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1500
        )

        summarized_info = response.choices[0].message.content

        # Thêm thông báo về nguồn
        sources_footer = "\n\n**Nguồn thông tin đã tham khảo:**\n" + "\n".join([f"- {content['url']}" for content in extracted_contents])

        # Kiểm tra xem summarized_info đã chứa nguồn chưa
        if not any(content['url'] in summarized_info for content in extracted_contents):
             final_response = f"{summarized_info}{sources_footer}"
        else:
             final_response = summarized_info

        return final_response

    except Exception as e:
        logger.error(f"Lỗi trong quá trình tìm kiếm và tổng hợp: {e}")
        return f"Có lỗi xảy ra trong quá trình tìm kiếm và tổng hợp thông tin: {str(e)}"

# Phát hiện câu hỏi cần search thông tin thực tế
def detect_search_intent(query, api_key):
    """
    Phát hiện xem câu hỏi có cần tìm kiếm thông tin thực tế hay không,
    tinh chỉnh câu truy vấn (bao gồm yếu tố thời gian), và xác định xem có phải là truy vấn tin tức không.
    Bổ sung: xử lý đặc biệt cho truy vấn thời tiết.

    Args:
        query (str): Câu hỏi của người dùng
        api_key (str): OpenAI API key

    Returns:
        tuple: (need_search, search_query, is_news_query)
               need_search: True/False
               search_query: Câu truy vấn đã được tinh chỉnh
               is_news_query: True nếu là tin tức/thời sự, False nếu khác
    """
    # Trước hết, kiểm tra xem có phải là truy vấn thời tiết không
    is_weather_query, location, days, time_term = weather_service.detect_weather_query(query)
    
    if is_weather_query:
        logger.info(f"Phát hiện truy vấn thời tiết: vị trí={location}, cụm từ thời gian='{time_term}'")
        # Đối với câu hỏi thời tiết, ta vẫn cần search làm backup nếu API thời tiết không hoạt động
        # Đảm bảo truy vấn search tích hợp cụm từ thời gian nếu có
        search_term = time_term if time_term and time_term != "hôm nay" else ""
        search_query = f"dự báo thời tiết {location} {search_term}".strip()
        return True, search_query, False
    
    # Mã cho các truy vấn không phải thời tiết
    try:
        client = OpenAI(api_key=api_key)
        current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        system_prompt = f"""
Bạn là một hệ thống phân loại và tinh chỉnh câu hỏi thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có cần tìm kiếm thông tin thực tế, tin tức mới hoặc dữ liệu cập nhật không (`need_search`).
2. Nếu cần tìm kiếm, hãy tinh chỉnh câu hỏi thành một truy vấn tìm kiếm tối ưu (`search_query`), ĐẶC BIỆT CHÚ Ý và kết hợp các yếu tố thời gian (hôm nay, hôm qua, tuần này, 26/03, năm 2023...).
3. Xác định xem câu hỏi có chủ yếu về tin tức, thời sự, sự kiện hiện tại không (`is_news_query`). Các câu hỏi về thời tiết, kết quả thể thao, sự kiện đang diễn ra cũng được coi là tin tức. Các câu hỏi về giá cả, thông tin sản phẩm, đánh giá KHÔNG được coi là tin tức trừ khi hỏi về tin tức liên quan đến chúng.

Hôm nay là ngày: {current_date_str}.

Ví dụ:
- User: "tin tức covid hôm nay" -> need_search: true, search_query: "tin tức covid mới nhất ngày {current_date_str}", is_news_query: true
- User: "kết quả trận MU tối qua" -> need_search: true, search_query: "kết quả Manchester United tối qua", is_news_query: true
- User: "có phim gì hay tuần này?" -> need_search: true, search_query: "phim chiếu rạp hay tuần này", is_news_query: false
- User: "giá vàng SJC" -> need_search: true, search_query: "giá vàng SJC mới nhất", is_news_query: false
- User: "thủ đô nước Pháp là gì?" -> need_search: false, search_query: "thủ đô nước Pháp là gì?", is_news_query: false
- User: "thời tiết Hà Nội ngày mai" -> need_search: true, search_query: "dự báo thời tiết Hà Nội ngày mai", is_news_query: true

Trả lời DƯỚI DẠNG JSON với 3 trường:
- need_search (boolean)
- search_query (string: câu truy vấn tối ưu, bao gồm thời gian nếu có)
- is_news_query (boolean: true nếu là tin tức/thời sự, false nếu khác)
"""

        response = client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\"\n\nHãy phân tích và trả về JSON theo yêu cầu."}
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )

        result_str = response.choices[0].message.content
        logger.info(f"Kết quả detect_search_intent (raw): {result_str}")

        try:
            result = json.loads(result_str)
            need_search = result.get("need_search", False)
            search_query = query  # Default là query gốc
            is_news_query = False  # Default là false

            if need_search:
                search_query = result.get("search_query", query)
                # Đảm bảo search_query không rỗng nếu cần search
                if not search_query:
                    search_query = query
                is_news_query = result.get("is_news_query", False)

            logger.info(f"Phân tích truy vấn: need_search={need_search}, search_query='{search_query}', is_news_query={is_news_query}")
            return need_search, search_query, is_news_query

        except json.JSONDecodeError as e:
            logger.error(f"Lỗi giải mã JSON từ detect_search_intent: {e}")
            logger.error(f"Nội dung JSON không hợp lệ: {result_str}")
            return False, query, False
    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_search_intent: {e}", exc_info=True)
        return False, query, False

# Thêm hàm tạo câu hỏi gợi ý động

def generate_dynamic_suggested_questions(api_key, member_id=None, max_questions=5):
    """
    Tạo câu hỏi gợi ý cá nhân hóa và linh động dựa trên thông tin thành viên, 
    lịch sử trò chuyện và thời điểm hiện tại
    """
    # Kiểm tra cache để tránh tạo câu hỏi mới quá thường xuyên
    cache_key = f"suggested_questions_{member_id}_{datetime.datetime.now().strftime('%Y-%m-%d_%H')}"
    
    # Xác định trạng thái người dùng hiện tại
    member_info = {}
    if member_id and member_id in family_data:
        member = family_data[member_id]
        member_info = {
            "name": member.get("name", ""),
            "age": member.get("age", ""),
            "preferences": member.get("preferences", {})
        }
    
    # Thu thập dữ liệu về các sự kiện sắp tới
    upcoming_events = []
    today = datetime.datetime.now().date()
    
    for event_id, event in events_data.items():
        try:
            event_date = datetime.datetime.strptime(event.get("date", ""), "%Y-%m-%d").date()
            if event_date >= today:
                date_diff = (event_date - today).days
                if date_diff <= 14:  # Chỉ quan tâm sự kiện trong 2 tuần tới
                    upcoming_events.append({
                        "title": event.get("title", ""),
                        "date": event.get("date", ""),
                        "days_away": date_diff
                    })
        except Exception as e:
            logger.error(f"Lỗi khi xử lý ngày sự kiện: {e}")
            continue
    
    # Lấy dữ liệu về chủ đề từ lịch sử trò chuyện gần đây
    recent_topics = []
    if member_id and member_id in chat_history and chat_history[member_id]:
        # Lấy tối đa 3 cuộc trò chuyện gần nhất
        recent_chats = chat_history[member_id][:3]
        
        for chat in recent_chats:
            summary = chat.get("summary", "")
            if summary:
                recent_topics.append(summary)
    
    questions = []
    
    # Phương thức 1: Sử dụng OpenAI API để sinh câu hỏi thông minh nếu có API key
    if api_key and api_key.startswith("sk-"):
        try:
            # Tạo nội dung prompt cho OpenAI
            context = {
                "member": member_info,
                "upcoming_events": upcoming_events,
                "recent_topics": recent_topics,
                "current_time": datetime.datetime.now().strftime("%H:%M"),
                "current_day": datetime.datetime.now().strftime("%A"),
                "current_date": datetime.datetime.now().strftime("%Y-%m-%d")
            }
            
            prompt = f"""
            Hãy tạo {max_questions} câu gợi ý đa dạng và cá nhân hóa cho người dùng trợ lý gia đình dựa trên thông tin sau:
            
            Thông tin người dùng: {json.dumps(member_info, ensure_ascii=False)}
                        
            
            Yêu cầu:
            1. Mỗi câu gợi ý nên tập trung vào MỘT sở thích cụ thể, không kết hợp nhiều sở thích
            2. KHÔNG kết thúc câu gợi ý bằng bất kỳ cụm từ nào như "bạn có biết không?", "bạn có muốn không?", v.v.
            3. Đưa ra thông tin cụ thể, chi tiết và chính xác như thể bạn đang viết một bài đăng trên mạng xã hội
            4. Mục đích là cung cấp thông tin hữu ích, không phải bắt đầu cuộc trò chuyện
            5. Chỉ trả về danh sách các câu gợi ý, mỗi câu trên một dòng
            6. Không thêm đánh số hoặc dấu gạch đầu dòng
            7. KHÔNG sử dụng dấu ngoặc kép (") bao quanh câu hỏi
            
            Ví dụ tốt:
            - Top 5 phim hành động hay nhất 2023?
            - Công thức bánh mì nguyên cám giảm cân?
            - Kết quả Champions League?
            - 5 bài tập cardio giảm mỡ bụng hiệu quả?
            
            Ví dụ không tốt:
            - "Bạn đã biết bộ phim 'The Goal' vừa được phát hành và nhận nhiều phản hồi tích cực từ khán giả chưa?" (Kết hợp phim + bóng đá)
            - "Kết quả trận đấu Champions League: Man City 3-1 Real Madrid, bạn có theo dõi không?" (Kết thúc bằng câu hỏi)
            - "Bạn có muốn xem những phát hiện mới về dinh dưỡng không?" (Không cung cấp thông tin cụ thể)
            
            Trả về chính xác {max_questions} câu gợi ý.
            """
            
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=openai_model,
                messages=[
                    {"role": "system", "content": "Bạn là trợ lý tạo câu hỏi gợi ý cá nhân hóa."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=300
            )
            
            # Xử lý phản hồi từ OpenAI
            generated_content = response.choices[0].message.content.strip()
            raw_questions = [q.strip() for q in generated_content.split('\n') if q.strip()]
            
            # Xử lý và làm sạch các câu hỏi
            questions = []
            for q in raw_questions:
                # Loại bỏ dấu gạch đầu dòng nếu có
                if q.startswith('- '):
                    q = q[2:]
                # Loại bỏ dấu ngoặc kép ở đầu và cuối nếu có
                if q.startswith('"') and q.endswith('"'):
                    q = q[1:-1]
                elif q.startswith('"'):
                    q = q[1:]
                elif q.endswith('"'):
                    q = q[:-1]
                # Loại bỏ các trường hợp khác
                q = q.replace('"', '')
                questions.append(q)
            
            # Lấy số lượng câu hỏi theo yêu cầu
            questions = questions[:max_questions]
            
            logger.info(f"Đã tạo {len(questions)} câu hỏi gợi ý bằng OpenAI API")
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo câu hỏi với OpenAI: {e}")
            # Tiếp tục với phương thức 2 (dự phòng)
    
    # Phương thức 2: Dùng mẫu câu + thông tin cá nhân nếu không thể sử dụng OpenAI API
    if not questions:
        logger.info("Sử dụng phương pháp mẫu câu để tạo câu hỏi gợi ý")
        
        # Tạo seed dựa trên ngày và ID thành viên để tạo sự đa dạng
        random_seed = int(hashlib.md5(f"{datetime.datetime.now().strftime('%Y-%m-%d_%H')}_{member_id or 'guest'}".encode()).hexdigest(), 16) % 10000
        random.seed(random_seed)
        
        # Mẫu câu thông tin cụ thể theo nhiều chủ đề khác nhau
        question_templates = {
            "food": [
                "Top 10 món {food} ngon nhất Việt Nam?",
                "Công thức làm món {food} ngon tại nhà?",
                "5 biến tấu món {food} cho bữa {meal}?",
                "Bí quyết làm món {food} ngon như nhà hàng 5 sao?",
                "Cách làm món {food} chuẩn vị {season}?",
                "3 cách chế biến món {food} giảm 50% calo?"
            ],
            "movies": [
                "Top 5 phim chiếu rạp tuần này: {movie1}, {movie2}, {movie3} - Đặt vé ngay để nhận ưu đãi.",
                "Phim mới ra mắt {movie1}?",
                "Đánh giá phim {movie1}?",
                "{actor} vừa giành giải Oscar cho vai diễn trong phim {movie1}, đánh bại 4 đối thủ nặng ký khác.",
                "5 bộ phim kinh điển mọi thời đại?",
                "Lịch chiếu phim {movie1} cuối tuần này?"
            ],
            "football": [
                "Kết quả Champions League?",
                "BXH Ngoại hạng Anh sau vòng 30?",
                "Chuyển nhượng bóng đá?",
                "Lịch thi đấu vòng tứ kết World Cup?",
                "Tổng hợp bàn thắng đẹp nhất tuần?",
                "Thống kê {player1} mùa này?"
            ],
            "technology": [
                "So sánh iPhone 16 Pro và Samsung S24 Ultra?",
                "5 tính năng AI mới trên smartphone 2024?",
                "Đánh giá laptop gaming {laptop_model}?",
                "Cách tối ưu hóa pin điện thoại tăng 30% thời lượng?",
                "3 ứng dụng quản lý công việc tốt nhất 2024?",
                "Tin công nghệ?"
            ],
            "health": [
                "5 loại thực phẩm tăng cường miễn dịch mùa {season}?",
                "Chế độ ăn Địa Trung Hải giúp giảm 30% nguy cơ bệnh tim mạch?",
                "3 bài tập cardio đốt mỡ bụng hiệu quả trong 15 phút?",
                "Nghiên cứu mới?",
                "Cách phòng tránh cảm cúm mùa {season}?",
                "Thực đơn 7 ngày giàu protein?"
            ],
            "family": [
                "10 hoạt động cuối tuần gắn kết gia đình?",
                "5 trò chơi phát triển IQ cho trẻ 3-6 tuổi?.",
                "Bí quyết dạy trẻ quản lý tài chính?",
                "Lịch trình khoa học cho trẻ?",
                "Cách giải quyết mâu thuẫn anh chị em?",
                "5 dấu hiệu trẻ gặp khó khăn tâm lý cần hỗ trợ?"
            ],
            "travel": [
                "Top 5 điểm du lịch Việt Nam mùa {season}?",
                "Kinh nghiệm du lịch tiết kiệm?",
                "Lịch trình du lịch Đà Nẵng 3 ngày?",
                "5 món đặc sản không thể bỏ qua khi đến Huế?",
                "Cách chuẩn bị hành lý cho chuyến du lịch 5 ngày?",
                "Kinh nghiệm đặt phòng khách sạn?"
            ],
            "news": [
                "Tin kinh tế?",
                "Tin thời tiết?",
                "Tin giáo dục?",
                "Tin giao thông?",
                "Tin y tế?",
                "Tin văn hóa?"
            ]
        }
        
        # Các biến thay thế trong mẫu câu
        replacements = {
            "food": ["phở", "bánh mì", "cơm rang", "gỏi cuốn", "bún chả", "bánh xèo", "mì Ý", "sushi", "pizza", "món Hàn Quốc"],
            "meal": ["sáng", "trưa", "tối", "xế"],
            "event": ["sinh nhật", "họp gia đình", "dã ngoại", "tiệc", "kỳ nghỉ"],
            "days": ["vài", "2", "3", "7", "10"],
            "hobby": ["đọc sách", "nấu ăn", "thể thao", "làm vườn", "vẽ", "âm nhạc", "nhiếp ảnh"],
            "time_of_day": ["sáng", "trưa", "chiều", "tối"],
            "day": ["thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ Nhật", "cuối tuần"],
            "season": ["xuân", "hạ", "thu", "đông"],
            "weather": ["nóng", "lạnh", "mưa", "nắng", "gió"],
            "music_artist": ["Sơn Tùng M-TP", "Mỹ Tâm", "BTS", "Taylor Swift", "Adele", "Coldplay", "BlackPink"],
            "actor": ["Ngô Thanh Vân", "Trấn Thành", "Tom Cruise", "Song Joong Ki", "Scarlett Johansson", "Leonardo DiCaprio"],
            "movie1": ["The Beekeeper", "Dune 2", "Godzilla x Kong", "Deadpool 3", "Inside Out 2", "Twisters", "Bad Boys 4"],
            "movie2": ["The Fall Guy", "Kingdom of the Planet of the Apes", "Furiosa", "Borderlands", "Alien: Romulus"],
            "movie3": ["Gladiator 2", "Wicked", "Sonic the Hedgehog 3", "Mufasa", "Moana 2", "Venom 3"],
            "team1": ["Manchester City", "Arsenal", "Liverpool", "Real Madrid", "Barcelona", "Bayern Munich", "PSG", "Việt Nam"],
            "team2": ["Chelsea", "Tottenham", "Inter Milan", "Juventus", "Atletico Madrid", "Dortmund", "Thái Lan"],
            "team3": ["Manchester United", "Newcastle", "AC Milan", "Napoli", "Porto", "Ajax", "Indonesia"],
            "team4": ["West Ham", "Aston Villa", "Roma", "Lazio", "Sevilla", "Leipzig", "Malaysia"],
            "player1": ["Haaland", "Salah", "Saka", "Bellingham", "Mbappe", "Martinez", "Quang Hải", "Tiến Linh"],
            "player2": ["De Bruyne", "Odegaard", "Kane", "Vinicius", "Lewandowski", "Griezmann", "Công Phượng"],
            "player3": ["Rodri", "Rice", "Son", "Kroos", "Pedri", "Messi", "Văn Hậu", "Văn Lâm"],
            "score1": ["1", "2", "3", "4", "5"],
            "score2": ["0", "1", "2", "3"],
            "minute1": ["12", "23", "45+2", "56", "67", "78", "89+1"],
            "minute2": ["34", "45", "59", "69", "80", "90+3"],
            "gameday": ["thứ Bảy", "Chủ nhật", "20/4", "27/4", "4/5", "11/5", "18/5"],
            "laptop_model": ["Asus ROG Zephyrus G14", "Lenovo Legion Pro 7", "MSI Titan GT77", "Acer Predator Helios", "Alienware m18"]
        }
        
        # Thay thế các biến bằng thông tin cá nhân nếu có
        if member_id and member_id in family_data:
            preferences = family_data[member_id].get("preferences", {})
            
            if preferences.get("food"):
                replacements["food"].insert(0, preferences["food"])
            
            if preferences.get("hobby"):
                replacements["hobby"].insert(0, preferences["hobby"])
        
        # Thêm thông tin từ sự kiện sắp tới
        if upcoming_events:
            for event in upcoming_events:
                replacements["event"].insert(0, event["title"])
                replacements["days"].insert(0, str(event["days_away"]))
        
        # Xác định mùa hiện tại (đơn giản hóa)
        current_month = datetime.datetime.now().month
        if 3 <= current_month <= 5:
            current_season = "xuân"
        elif 6 <= current_month <= 8:
            current_season = "hạ"
        elif 9 <= current_month <= 11:
            current_season = "thu"
        else:
            current_season = "đông"
        
        replacements["season"].insert(0, current_season)
        
        # Thêm ngày hiện tại
        current_day_name = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"][datetime.datetime.now().weekday()]
        replacements["day"].insert(0, current_day_name)
        
        # Thêm bữa ăn phù hợp với thời điểm hiện tại
        current_hour = datetime.datetime.now().hour
        if 5 <= current_hour < 10:
            current_meal = "sáng"
        elif 10 <= current_hour < 14:
            current_meal = "trưa"
        elif 14 <= current_hour < 17:
            current_meal = "xế"
        else:
            current_meal = "tối"
        
        replacements["meal"].insert(0, current_meal)
        replacements["time_of_day"].insert(0, current_meal)
        
        # Tạo danh sách các chủ đề ưu tiên theo sở thích người dùng
        priority_categories = []
        user_preferences = {}
        
        # Phân tích sở thích người dùng
        if member_id and member_id in family_data:
            preferences = family_data[member_id].get("preferences", {})
            user_preferences = preferences
            
            # Ưu tiên các chủ đề dựa trên sở thích
            if preferences.get("food"):
                priority_categories.append("food")
            
            if preferences.get("hobby"):
                hobby = preferences["hobby"].lower()
                if any(keyword in hobby for keyword in ["đọc", "sách", "học", "nghiên cứu"]):
                    priority_categories.append("education")
                elif any(keyword in hobby for keyword in ["du lịch", "đi", "khám phá", "phiêu lưu"]):
                    priority_categories.append("travel")
                elif any(keyword in hobby for keyword in ["âm nhạc", "nghe", "hát", "nhạc"]):
                    priority_categories.append("entertainment")
                elif any(keyword in hobby for keyword in ["phim", "xem", "điện ảnh", "movie"]):
                    priority_categories.append("movies")
                elif any(keyword in hobby for keyword in ["bóng đá", "thể thao", "bóng rổ", "thể hình", "gym", "bóng", "đá", "tennis"]):
                    priority_categories.append("football")
                elif any(keyword in hobby for keyword in ["công nghệ", "máy tính", "điện thoại", "game", "tech"]):
                    priority_categories.append("technology")
                
        # Luôn đảm bảo có tin tức trong các gợi ý
        priority_categories.append("news")
        
        # Thêm các chủ đề còn lại
        remaining_categories = [cat for cat in question_templates.keys() if cat not in priority_categories]
        
        # Đảm bảo tách riêng phim và bóng đá nếu người dùng thích cả hai
        if "movies" not in priority_categories and "football" not in priority_categories:
            # Nếu cả hai chưa được thêm, thêm cả hai
            remaining_categories = ["movies", "football"] + [cat for cat in remaining_categories if cat not in ["movies", "football"]]
        
        # Kết hợp để có tất cả chủ đề
        all_categories = priority_categories + remaining_categories
        
        # Chọn tối đa max_questions chủ đề, đảm bảo ưu tiên các sở thích
        selected_categories = all_categories[:max_questions]
        
        # Tạo câu gợi ý cho mỗi chủ đề
        for category in selected_categories:
            if len(questions) >= max_questions:
                break
                
            # Chọn một mẫu câu ngẫu nhiên từ chủ đề
            template = random.choice(question_templates[category])
            
            # Điều chỉnh mẫu câu dựa trên sở thích người dùng
            if category == "food" and user_preferences.get("food"):
                # Nếu người dùng có món ăn yêu thích, thay thế biến {food} bằng sở thích
                template = template.replace("{food}", user_preferences["food"])
            elif category == "football" and "hobby" in user_preferences and any(keyword in user_preferences["hobby"].lower() for keyword in ["bóng đá", "thể thao"]):
                # Nếu người dùng thích bóng đá, ưu tiên thông tin cụ thể hơn
                pass  # Giữ nguyên template vì đã đủ cụ thể
            
            # Thay thế các biến còn lại trong mẫu câu
            question = template
            for key in replacements:
                if "{" + key + "}" in question:
                    replacement = random.choice(replacements[key])
                    question = question.replace("{" + key + "}", replacement)
            
            questions.append(question)
        
        # Đảm bảo đủ số lượng câu hỏi
        if len(questions) < max_questions:
            # Ưu tiên thêm từ tin tức và thông tin giải trí
            more_templates = []
            more_templates.extend(question_templates["news"])
            more_templates.extend(question_templates["movies"])
            more_templates.extend(question_templates["football"])
            
            random.shuffle(more_templates)
            
            while len(questions) < max_questions and more_templates:
                template = more_templates.pop(0)
                
                # Thay thế các biến trong mẫu câu
                question = template
                for key in replacements:
                    if "{" + key + "}" in question:
                        replacement = random.choice(replacements[key])
                        question = question.replace("{" + key + "}", replacement)
                
                # Tránh trùng lặp
                if question not in questions:
                    questions.append(question)
    
    return questions

# Hàm tạo tóm tắt lịch sử chat
def generate_chat_summary(messages, api_key):
    """Tạo tóm tắt từ lịch sử trò chuyện"""
    if not messages or len(messages) < 3:  # Cần ít nhất một vài tin nhắn để tạo tóm tắt
        return "Chưa có đủ tin nhắn để tạo tóm tắt."
    
    # Chuẩn bị dữ liệu cho API
    content_texts = []
    for message in messages:
        if "content" in message:
            # Xử lý cả tin nhắn văn bản và hình ảnh
            if isinstance(message["content"], list):
                for content in message["content"]:
                    if content.get("type") == "text":
                        content_texts.append(f"{message['role'].upper()}: {content['text']}")
            else:
                content_texts.append(f"{message['role'].upper()}: {message['content']}")
    
    # Ghép tất cả nội dung lại
    full_content = "\n".join(content_texts)
    
    # Gọi API để tạo tóm tắt
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": "Bạn là trợ lý tạo tóm tắt. Hãy tóm tắt cuộc trò chuyện dưới đây thành 1-3 câu ngắn gọn, tập trung vào các thông tin và yêu cầu chính."},
                {"role": "user", "content": f"Tóm tắt cuộc trò chuyện sau:\n\n{full_content}"}
            ],
            temperature=0.3,
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Lỗi khi tạo tóm tắt: {e}")
        return "Không thể tạo tóm tắt vào lúc này."

# Hàm lưu lịch sử trò chuyện cho người dùng hiện tại
def save_chat_history(member_id, messages, summary=None, session_id=None):
    """Lưu lịch sử chat cho một thành viên cụ thể và liên kết với session_id"""
    if member_id not in chat_history:
        chat_history[member_id] = []
    
    # Tạo bản ghi mới với session_id
    history_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": messages,
        "summary": summary if summary else "",
        "session_id": session_id  # Thêm session_id vào bản ghi
    }
    
    # Thêm vào lịch sử và giới hạn số lượng
    chat_history[member_id].insert(0, history_entry)  # Thêm vào đầu danh sách
    
    # Giới hạn lưu tối đa 10 cuộc trò chuyện gần nhất
    if len(chat_history[member_id]) > 10:
        chat_history[member_id] = chat_history[member_id][:10]
    
    # Lưu vào file
    save_data(CHAT_HISTORY_FILE, chat_history)

@app.get("/chat_history/session/{session_id}")
async def get_session_chat_history(session_id: str):
    """Lấy lịch sử trò chuyện theo session_id"""
    session_history = []
    
    # Tìm trong tất cả lịch sử trò chuyện của tất cả thành viên
    for member_id, histories in chat_history.items():
        for history in histories:
            if history.get("session_id") == session_id:
                # Thêm thông tin về thành viên
                history_with_member = history.copy()
                history_with_member["member_id"] = member_id
                if member_id in family_data:
                    history_with_member["member_name"] = family_data[member_id].get("name", "")
                session_history.append(history_with_member)
    
    # Sắp xếp theo thời gian
    session_history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return session_history

def text_to_speech_google(text, lang='vi', slow=False, max_length=5000):
    """
    Chuyển đổi văn bản thành giọng nói sử dụng Google Text-to-Speech
    
    Args:
        text (str): Văn bản cần chuyển đổi (có thể chứa HTML)
        lang (str): Ngôn ngữ (mặc định: 'vi' cho tiếng Việt)
        slow (bool): True để nói chậm hơn, False cho tốc độ bình thường
        max_length (int): Độ dài tối đa của văn bản
        
    Returns:
        str: Base64 encoded audio data
    """
    try:
        # Loại bỏ các thẻ HTML từ văn bản
        import re
        from html import unescape
        
        # Loại bỏ các thẻ HTML
        clean_text = re.sub(r'<[^>]*>', ' ', text)
        
        # Thay thế các ký tự đặc biệt như &nbsp;, &quot;, &amp;, ...
        clean_text = unescape(clean_text)
        
        # Loại bỏ khoảng trắng thừa
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        logger.info(f"Đã chuyển đổi văn bản HTML thành plain text để TTS")
        
        # Giới hạn độ dài văn bản
        if len(clean_text) > max_length:
            clean_text = clean_text[:max_length] + "..."
        
        # Tạo buffer để lưu audio
        audio_buffer = BytesIO()
        
        # Khởi tạo gTTS
        tts = gTTS(text=clean_text, lang=lang, slow=slow)
        
        # Lưu vào buffer
        tts.write_to_fp(audio_buffer)
        
        # Chuyển con trỏ về đầu buffer
        audio_buffer.seek(0)
        
        # Lấy dữ liệu và mã hóa base64
        audio_data = audio_buffer.read()
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        return audio_base64
        
    except Exception as e:
        logger.error(f"Lỗi khi sử dụng Google TTS: {str(e)}")
        logger.error(f"Chi tiết lỗi:", exc_info=True)
        return None

# # Hàm chuyển đổi text thành speech sử dụng facebook/mms-tts-vie từ Hugging Face
# def text_to_speech_huggingface(text, speed=1.0, max_length=1000):
#     """
#     Chuyển đổi văn bản thành giọng nói sử dụng mô hình facebook/mms-tts-vie
    
#     Args:
#         text (str): Văn bản cần chuyển đổi
#         speed (float): Hệ số tốc độ (0.5-2.0)
#         max_length (int): Độ dài tối đa của văn bản
        
#     Returns:
#         str: Base64 encoded audio data
#     """
#     try:
#         # Giới hạn độ dài văn bản
#         if len(text) > max_length:
#             text = text[:max_length] + "..."
        
#         # Import thư viện cần thiết
#         from transformers import VitsModel, AutoTokenizer
#         import torch
#         import io
#         import soundfile as sf
#         import numpy as np
        
#         # Tải mô hình và tokenizer
#         model = VitsModel.from_pretrained("facebook/mms-tts-vie")
#         tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-vie")
        
#         # Tokenize và chuyển đổi thành waveform
#         inputs = tokenizer(text, return_tensors="pt")
#         with torch.no_grad():
#             output = model(**inputs).waveform
        
#         # Chuyển đổi tốc độ (resampling)
#         if speed != 1.0:
#             # Chuyển về numpy array để xử lý
#             waveform_np = output.squeeze().numpy()
            
#             # Số lượng mẫu mới dựa trên tốc độ
#             new_length = int(len(waveform_np) / speed)
            
#             # Resampling đơn giản
#             indices = np.linspace(0, len(waveform_np) - 1, new_length)
#             waveform_np_resampled = np.interp(indices, np.arange(len(waveform_np)), waveform_np)
            
#             # Chuyển lại thành tensor để xử lý tiếp
#             waveform_resampled = torch.from_numpy(waveform_np_resampled).unsqueeze(0)
#         else:
#             waveform_resampled = output
        
#         # Chuẩn bị buffer để lưu dữ liệu
#         audio_buffer = io.BytesIO()
        
#         # Lấy thông tin từ waveform
#         sample_rate = 16000  # Sample rate mặc định của mô hình
#         waveform_np = waveform_resampled.squeeze().numpy()
        
#         # Lưu vào buffer dưới dạng WAV
#         sf.write(audio_buffer, waveform_np, sample_rate, format='WAV')
        
#         # Chuyển con trỏ về đầu buffer
#         audio_buffer.seek(0)
        
#         # Lấy dữ liệu và mã hóa base64
#         audio_data = audio_buffer.read()
#         audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
#         return audio_base64
        
#     except Exception as e:
#         logger.error(f"Lỗi khi sử dụng mô hình Hugging Face TTS: {str(e)}")
#         logger.error(f"Chi tiết lỗi:", exc_info=True)
#         return None

# # Hàm chuyển đổi text thành speech sử dụng OpenAI API (giữ để backup)
# def text_to_speech(text, api_key, voice="alloy"):
#     """
#     Chuyển đổi văn bản thành giọng nói sử dụng OpenAI TTS API
    
#     Args:
#         text (str): Văn bản cần chuyển đổi
#         api_key (str): OpenAI API key
#         voice (str): Giọng nói (alloy, echo, fable, onyx, nova, shimmer)
        
#     Returns:
#         str: Base64 encoded audio data
#     """
#     try:
#         client = OpenAI(api_key=api_key)
#         response = client.audio.speech.create(
#             model="tts-1",
#             voice=voice,
#             input=text
#         )
        
#         # Lấy dữ liệu audio dưới dạng bytes
#         audio_data = response.content
        
#         # Chuyển đổi thành base64
#         audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
#         return audio_base64
#     except Exception as e:
#         logger.error(f"Lỗi khi chuyển đổi văn bản thành giọng nói: {str(e)}")
#         return None


# # Hàm chuyển đổi text thành speech
# def text_to_speech(text, api_key, voice="nova", speed=0.8, max_length=4096):
#     """
#     Chuyển đổi văn bản thành giọng nói sử dụng OpenAI TTS API
    
#     Args:
#         text (str): Văn bản cần chuyển đổi
#         api_key (str): OpenAI API key
#         voice (str): Giọng nói (alloy, echo, fable, onyx, nova, shimmer)
#         speed (float): Tốc độ nói (0.5-1.5, mặc định 0.8 hơi chậm hơn bình thường)
#         max_length (int): Độ dài tối đa của văn bản (tính bằng ký tự)
        
#     Returns:
#         str: Base64 encoded audio data
#     """
#     try:
#         # Giới hạn độ dài văn bản để tránh lỗi
#         if len(text) > max_length:
#             text = text[:max_length] + "..."
            
#         client = OpenAI(api_key=api_key)
#         response = client.audio.speech.create(
#             model="tts-1",
#             voice=voice,
#             input=text,
#             speed=speed  # Thêm tham số tốc độ nói
#         )
        
#         # Lấy dữ liệu audio dưới dạng bytes
#         audio_data = response.content
        
#         # Chuyển đổi thành base64
#         audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
#         return audio_base64
#     except Exception as e:
#         logger.error(f"Lỗi khi chuyển đổi văn bản thành giọng nói: {str(e)}")
#         return None

# Hàm chuyển đổi hình ảnh sang base64
def get_image_base64(image_raw):
    buffered = BytesIO()
    image_raw.save(buffered, format=image_raw.format)
    img_byte = buffered.getvalue()
    return base64.b64encode(img_byte).decode('utf-8')

# Hàm lọc sự kiện theo người dùng
def filter_events_by_member(member_id=None):
    """Lọc sự kiện theo thành viên cụ thể"""
    if not member_id:
        return events_data  # Trả về tất cả sự kiện nếu không có ID
    
    filtered_events = {}
    for event_id, event in events_data.items():
        # Lọc những sự kiện mà thành viên tạo hoặc tham gia
        if (event.get("created_by") == member_id or 
            (member_id in family_data and 
             family_data[member_id].get("name") in event.get("participants", []))):
            filtered_events[event_id] = event
    
    return filtered_events

# Thêm các hàm tiện ích cho việc tính toán ngày tháng
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


def get_date_from_relative_term(term):
    """
    Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế (YYYY-MM-DD).
    Hỗ trợ: hôm nay, ngày mai, ngày kia, hôm qua, thứ X tuần sau, thứ X.
    """
    term = term.lower().strip()
    today = datetime.date.today()
    logger.debug(f"Calculating date for term: '{term}', today is: {today.strftime('%Y-%m-%d %A')}")

    # Basic relative terms
    if term in ["hôm nay", "today"]:
        return today.strftime("%Y-%m-%d")
    elif term in ["ngày mai", "mai", "tomorrow"]:
        return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    elif term in ["ngày kia", "day after tomorrow"]:
         return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    elif term in ["hôm qua", "yesterday"]:
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # --- Handle specific weekdays ---
    target_weekday = -1
    is_next_week = False

    # Check if it refers to next week
    original_term_for_weekday_search = term # Giữ lại term gốc trước khi loại bỏ "tuần sau"
    for kw in NEXT_WEEK_KEYWORDS:
        if kw in term:
            is_next_week = True
            # Chỉ loại bỏ keyword để tìm weekday, không thay đổi term gốc dùng cho tìm weekday
            term_without_next_week = term.replace(kw, "").strip()
            logger.debug(f"'Next week' detected. Term for weekday search: '{term_without_next_week}'")
            break
    else: # Nếu không phải next week
        term_without_next_week = term

    # Find the target weekday from the modified term
    for day_str, day_num in VIETNAMESE_WEEKDAY_MAP.items():
        # Use regex to match whole word
        if re.search(r'\b' + re.escape(day_str) + r'\b', term_without_next_week):
            target_weekday = day_num
            logger.debug(f"Found target weekday: {day_str} ({target_weekday})")
            break

    if target_weekday != -1:
        today_weekday = today.weekday() # Monday is 0, Sunday is 6

        if is_next_week:
            # *** SỬA LOGIC TÍNH NGÀY TUẦN SAU ***
            # 1. Tính số ngày cần để đến Thứ Hai của tuần sau
            # Số ngày từ hôm nay đến Chủ Nhật tuần này: 6 - today_weekday
            # Số ngày đến Thứ Hai tuần sau: (6 - today_weekday) + 1
            days_to_next_monday = (6 - today_weekday) + 1
            logger.debug(f"Days from today ({today_weekday}) to next Monday: {days_to_next_monday}")

            # 2. Tính ngày Thứ Hai tuần sau
            next_monday_date = today + datetime.timedelta(days=days_to_next_monday)
            logger.debug(f"Next Monday's date: {next_monday_date.strftime('%Y-%m-%d')}")

            # 3. Tính ngày mục tiêu bằng cách cộng thêm target_weekday (0=Mon, 1=Tue, ...) vào ngày Thứ Hai đó
            # Lưu ý: target_weekday là số ngày cần cộng thêm từ Thứ Hai (0)
            final_date = next_monday_date + datetime.timedelta(days=target_weekday)
            logger.info(f"Calculated date for '{original_term_for_weekday_search}': {final_date.strftime('%Y-%m-%d %A')}")
            return final_date.strftime("%Y-%m-%d")

        else: # Asking for "thứ X" without specifying week (assume *upcoming*)
            # Tính số ngày cần để đến target_weekday *sắp tới*
            days_ahead = target_weekday - today_weekday
            logger.debug(f"Calculating upcoming weekday: target={target_weekday}, today={today_weekday}, days_ahead={days_ahead}")
            # Nếu ngày đó đã qua trong tuần này (days_ahead < 0),
            # hoặc nếu là hôm nay nhưng muốn lần tới (days_ahead == 0), ta cần cộng thêm 7 ngày
            if days_ahead <= 0:
                 days_to_add = days_ahead + 7
                 logger.debug("Target day passed or is today, adding 7 days.")
            else: # Ngày đó ở phía sau trong tuần này
                 days_to_add = days_ahead
                 logger.debug("Target day is later this week.")

            final_date = today + datetime.timedelta(days=days_to_add)
            logger.info(f"Calculated date for upcoming '{original_term_for_weekday_search}': {final_date.strftime('%Y-%m-%d %A')}")
            return final_date.strftime("%Y-%m-%d")

    # --- End specific weekdays ---

    # Fallback for imprecise terms (giữ nguyên)
    if any(kw in term for kw in NEXT_WEEK_KEYWORDS):
        days_to_next_monday = (6 - today.weekday()) + 1
        calculated_date = today + datetime.timedelta(days=days_to_next_monday) # Next Monday
        logger.info(f"Calculated date for general 'next week': {calculated_date.strftime('%Y-%m-%d')} (Next Monday)")
        return calculated_date.strftime("%Y-%m-%d")
    elif "tháng tới" in term or "tháng sau" in term or "next month" in term:
        # Simple approximation: add 30 days
        calculated_date = today + datetime.timedelta(days=30)
        logger.info(f"Calculated date for 'next month': {calculated_date.strftime('%Y-%m-%d')} (Approx +30 days)")
        return calculated_date.strftime("%Y-%m-%d")

    # Check if the term itself is a valid date format (Thêm lại phần này để linh hoạt)
    try:
        parsed_date = None
        if re.match(r'\d{4}-\d{2}-\d{2}', term):
             parsed_date = datetime.datetime.strptime(term, "%Y-%m-%d").date()
        elif re.match(r'\d{2}/\d{2}/\d{4}', term):
             parsed_date = datetime.datetime.strptime(term, "%d/%m/%Y").date()

        if parsed_date:
             logger.info(f"Term '{term}' is a valid date string, returning as is (normalized).")
             return parsed_date.strftime("%Y-%m-%d") # Trả về định dạng chuẩn
    except ValueError:
        pass # Không phải định dạng ngày hợp lệ

    logger.warning(f"Could not interpret relative date term: '{term}'. Returning None.")
    return None

# Các hàm quản lý thông tin gia đình
def add_family_member(details):
    member_id = details.get("id") or str(len(family_data) + 1)
    family_data[member_id] = {
        "name": details.get("name", ""),
        "age": details.get("age", ""),
        "preferences": details.get("preferences", {}),
        "added_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(FAMILY_DATA_FILE, family_data)

def update_preference(details):
    member_id = details.get("id")
    preference_key = details.get("key")
    preference_value = details.get("value")
    
    if member_id in family_data and preference_key:
        if "preferences" not in family_data[member_id]:
            family_data[member_id]["preferences"] = {}
        family_data[member_id]["preferences"][preference_key] = preference_value
        save_data(FAMILY_DATA_FILE, family_data)

def add_event(details):
    """Thêm một sự kiện mới vào danh sách sự kiện"""
    try:
        event_id = str(len(events_data) + 1)
        events_data[event_id] = {
            "title": details.get("title", ""),
            "date": details.get("date", ""),
            "time": details.get("time", ""),
            "description": details.get("description", ""),
            "participants": details.get("participants", []),
            "created_by": details.get("created_by", ""),  # Thêm người tạo sự kiện
            "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_data(EVENTS_DATA_FILE, events_data)
        logger.info(f"Đã thêm sự kiện: {details.get('title', '')} vào {EVENTS_DATA_FILE}")
        logger.info(f"Tổng số sự kiện hiện tại: {len(events_data)}")
        return True
    except Exception as e:
        logger.error(f"Lỗi khi thêm sự kiện: {e}")
        return False

def update_event(details):
    """Cập nhật thông tin về một sự kiện"""
    try:
        event_id = details.get("id")
        if event_id in events_data:
            # Cập nhật các trường được cung cấp
            for key, value in details.items():
                if key != "id" and value is not None:
                    events_data[event_id][key] = value
            
            # Đảm bảo trường created_on được giữ nguyên
            if "created_on" not in events_data[event_id]:
                events_data[event_id]["created_on"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            save_data(EVENTS_DATA_FILE, events_data)
            logger.info(f"Đã cập nhật sự kiện ID={event_id}: {details}")
            return True
        else:
            logger.warning(f"Không tìm thấy sự kiện ID={event_id}")
            return False
    except Exception as e:
        logger.error(f"Lỗi khi cập nhật sự kiện: {e}")
        return False

def delete_event(event_id):
    if event_id in events_data:
        del events_data[event_id]
        save_data(EVENTS_DATA_FILE, events_data)

# Các hàm quản lý ghi chú
def add_note(details):
    note_id = str(len(notes_data) + 1)
    notes_data[note_id] = {
        "title": details.get("title", ""),
        "content": details.get("content", ""),
        "tags": details.get("tags", []),
        "created_by": details.get("created_by", ""),  # Thêm người tạo ghi chú
        "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(NOTES_DATA_FILE, notes_data)

# Hàm xử lý lệnh từ phản hồi của trợ lý
def process_assistant_response(response: str, current_member: Optional[str] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Xử lý phản hồi từ assistant, trích xuất các lệnh, làm sạch HTML,
    tính toán ngày chính xác bằng Python, và tạo event_data cho sự kiện.

    Args:
        response (str): Phản hồi thô từ assistant (có thể chứa lệnh).
        current_member (str, optional): ID thành viên hiện tại để gán quyền tạo.

    Returns:
        tuple: (cleaned_html, event_data)
            - cleaned_html: HTML đã được làm sạch (không còn lệnh).
            - event_data: Dữ liệu sự kiện được trích xuất (nếu có hành động liên quan đến sự kiện).
                          Format: {'action': 'add'/'update'/'delete', 'id': ..., 'title': ..., ...}
    """
    try:
        logger.debug(f"Bắt đầu xử lý phản hồi assistant, độ dài: {len(response)}")
        cleaned_html = response
        event_data: Optional[Dict[str, Any]] = None

        # --- Xử lý ADD_EVENT ---
        add_event_match = re.search(r"##ADD_EVENT:(.*?)##", cleaned_html, re.DOTALL)
        if add_event_match:
            cmd_raw = add_event_match.group(0)
            cmd_content = add_event_match.group(1).strip()
            # Loại bỏ lệnh khỏi HTML ngay lập tức
            cleaned_html = cleaned_html.replace(cmd_raw, "").strip()
            logger.info("Tìm thấy lệnh ADD_EVENT")
            logger.info(f"Nội dung lệnh ADD_EVENT nhận từ LLM (trước khi parse): {cmd_content}")

            try:
                details = json.loads(cmd_content)
                if isinstance(details, dict):
                    # Gán người tạo nếu có
                    if current_member:
                        details['created_by'] = current_member

                    # --- BẮT ĐẦU LOGIC XỬ LÝ NGÀY MỚI ---
                    llm_date_input = details.get('date', '') # Đây có thể là 'YYYY-MM-DD', 'DD/MM/YYYY' hoặc 'thứ 2 tuần sau', v.v.
                    logger.info(f"Giá trị 'date' nhận từ LLM: '{llm_date_input}'")
                    llm_time_str = details.get('time', '19:00')
                    llm_description = details.get('description', '')
                    llm_title = details.get('title', '')

                    # 1. Xác định kiểu lặp lại dựa trên mô tả và tiêu đề
                    repeat_type = determine_repeat_type(llm_description, llm_title)
                    details['repeat_type'] = repeat_type # Lưu lại để dùng sau nếu cần
                    logger.debug(f"Xác định repeat_type: {repeat_type}")
                    is_recurring_event = (repeat_type == "RECURRING")

                    # 2. Tính toán ngày cuối cùng (Ưu tiên Python)
                    final_date_str = None

                    # Kiểm tra xem LLM có cung cấp ngày cụ thể không
                    try:
                        if llm_date_input:
                            if re.match(r'\d{4}-\d{2}-\d{2}', llm_date_input):
                                datetime.datetime.strptime(llm_date_input, "%Y-%m-%d") # Chỉ để kiểm tra valid
                                final_date_str = llm_date_input
                                logger.info(f"LLM cung cấp ngày cụ thể hợp lệ (YYYY-MM-DD): {final_date_str}")
                            elif re.match(r'\d{2}/\d{2}/\d{4}', llm_date_input):
                                parsed_dt = datetime.datetime.strptime(llm_date_input, "%d/%m/%Y")
                                final_date_str = parsed_dt.strftime("%Y-%m-%d") # Chuẩn hóa
                                logger.info(f"LLM cung cấp ngày cụ thể hợp lệ (DD/MM/YYYY), chuẩn hóa thành: {final_date_str}")
                    except ValueError:
                        logger.warning(f"Ngày LLM cung cấp '{llm_date_input}' không phải định dạng ngày cụ thể hợp lệ. Sẽ thử tính bằng Python.")
                        final_date_str = None # Reset nếu parse lỗi

                    # Nếu không có ngày cụ thể từ LLM HOẶC là sự kiện lặp lại (cần ngày gần nhất từ mô tả)
                    # thì sử dụng Python để tính từ mô tả tương đối trong llm_date_input
                    # hoặc từ mô tả sự kiện nếu date input không hữu ích cho lặp lại
                    date_input_for_python = llm_date_input
                    # Nếu là lặp lại và date_input không chứa thông tin ngày cụ thể (như 'hàng ngày')
                    # thì có thể thử dùng description để tìm ngày (ví dụ: 'thứ 6 hàng tuần')
                    if is_recurring_event and not final_date_str:
                         # Kiểm tra nếu llm_date_input không mang thông tin ngày lặp cụ thể
                         # Ví dụ đơn giản: kiểm tra nếu nó chỉ là 'hàng ngày', 'hàng tuần'
                         if llm_date_input.lower() in ["hàng ngày", "hàng tuần", "hàng tháng", "hàng năm", "định kỳ", "lặp lại"]:
                              # Thử lấy thông tin ngày từ description
                              logger.info(f"Sự kiện lặp lại với date='{llm_date_input}', thử tìm ngày cụ thể trong description: '{llm_description}'")
                              # Kết hợp title và description để tìm ngày
                              search_text_for_date = (str(llm_title) + " " + str(llm_description)).lower()
                              # Cập nhật lại biến để đưa vào hàm tính toán Python
                              date_input_for_python = search_text_for_date
                         else:
                             # Giữ nguyên llm_date_input nếu nó chứa thông tin ngày (ví dụ: 'thứ 6 hàng tuần')
                             logger.info(f"Sự kiện lặp lại, sử dụng date='{llm_date_input}' để tìm ngày gần nhất.")


                    if not final_date_str and date_input_for_python:
                        logger.info(f"Thực hiện tính toán ngày bằng Python từ: '{date_input_for_python}'")
                        calculated_python_date_str = get_date_from_relative_term(date_input_for_python)
                        if calculated_python_date_str:
                            final_date_str = calculated_python_date_str
                            logger.info(f"Hàm Python tính được ngày: {final_date_str}")
                        else:
                            logger.warning(f"Không thể tính ngày từ '{date_input_for_python}' bằng hàm Python. Ngày sẽ bị bỏ trống.")
                            final_date_str = None # Hoặc đặt ngày mặc định nếu muốn

                    # Xử lý trường hợp lặp lại không có ngày cụ thể nào (ví dụ: 'hàng ngày')
                    # sau khi đã thử tính từ date_input và description
                    if is_recurring_event and not final_date_str:
                         if "hàng ngày" in str(llm_description).lower() or "mỗi ngày" in str(llm_description).lower():
                              final_date_str = datetime.date.today().strftime("%Y-%m-%d")
                              logger.info(f"Sự kiện lặp lại hàng ngày, đặt ngày bắt đầu gần nhất là hôm nay: {final_date_str}")
                         # Thêm các logic khác cho lặp lại hàng tháng/năm nếu cần ngày bắt đầu gần nhất
                         # ...

                    # Cập nhật lại details với ngày và giờ cuối cùng đã xác định
                    details['date'] = final_date_str if final_date_str else ""
                    details['time'] = llm_time_str
                    details['title'] = llm_title # Đảm bảo title/desc cũng đúng
                    details['description'] = llm_description
                    # --- KẾT THÚC LOGIC XỬ LÝ NGÀY MỚI ---


                    # 3. Sanity Check (TÙY CHỌN - kiểm tra logic Python)
                    if final_date_str and (llm_title or llm_description):
                        try:
                            parsed_final_date = datetime.datetime.strptime(final_date_str, "%Y-%m-%d").date()
                            mentioned_weekday_str = None
                            mentioned_weekday_num = -1
                            combined_text_for_check = (str(llm_title) + " " + str(llm_description)).lower()
                            # Tìm ngày trong tuần được đề cập trong text
                            for day_str, day_num in VIETNAMESE_WEEKDAY_MAP.items():
                                if re.search(r'\b' + re.escape(day_str) + r'\b', combined_text_for_check):
                                    mentioned_weekday_num = day_num
                                    mentioned_weekday_str = day_str
                                    break # Tìm thấy là đủ

                            if mentioned_weekday_num != -1:
                                actual_weekday_num = parsed_final_date.weekday() # Monday is 0, Sunday is 6
                                if actual_weekday_num != mentioned_weekday_num:
                                    actual_weekday_str_map = {0: "Thứ 2", 1: "Thứ 3", 2: "Thứ 4", 3: "Thứ 5", 4: "Thứ 6", 5: "Thứ 7", 6: "Chủ Nhật"}
                                    actual_day_name = actual_weekday_str_map.get(actual_weekday_num, "Không xác định")
                                    mentioned_day_name = mentioned_weekday_str
                                    logger.warning(
                                        f"SANITY CHECK WARNING (sau khi Python tính): Ngày Python tính {final_date_str} ({actual_day_name}) "
                                        f"KHÔNG KHỚP với ngày được đề cập trong mô tả/tiêu đề ({mentioned_day_name}). "
                                        f"Kiểm tra lại logic `get_date_from_relative_term` hoặc mô tả của LLM."
                                    )
                        except Exception as sanity_e:
                            logger.error(f"Lỗi trong quá trình Sanity Check: {sanity_e}")


                    # 4. Tạo cron expression
                    cron_expression = ""
                    if is_recurring_event:
                        # Sử dụng thông tin đã chuẩn hóa để tạo cron
                        cron_expression = generate_recurring_cron(llm_description, llm_title, llm_time_str)
                        logger.info(f"Tạo cron RECURRING: {cron_expression}")
                    else: # ONCE
                        if final_date_str: # Chỉ tạo cron một lần nếu có ngày hợp lệ
                            cron_expression = date_time_to_cron(final_date_str, llm_time_str)
                            logger.info(f"Tạo cron ONCE: {cron_expression} cho ngày {final_date_str}")
                        else:
                            logger.error("Không thể tạo cron ONCE vì thiếu ngày hợp lệ.")
                            cron_expression = ""

                    # 5. Tạo event_data để trả về cho client (frontend)
                    event_data = {
                        "action": "add",
                        "title": llm_title,
                        "description": llm_description,
                        "cron_expression": cron_expression, # Biểu thức cron đã tạo
                        "repeat_type": repeat_type, # Loại lặp lại
                        "original_date": final_date_str if final_date_str else None, # Ngày cuối cùng đã xác định
                        "original_time": llm_time_str, # Thời gian đã xác định
                        "participants": details.get('participants', []) # Lấy participants từ details
                    }
                    logger.debug(f"Event data được tạo cho client: {event_data}")

                    # 6. Thực hiện thêm sự kiện vào hệ thống (lưu vào file JSON)
                    # Hàm add_event nên nhận 'details' đã được cập nhật đầy đủ
                    if add_event(details):
                        logger.info(f"Đã thực thi lệnh ADD_EVENT thành công và lưu vào data store cho: '{llm_title}'")
                    else:
                        logger.error(f"Thực thi lệnh ADD_EVENT (lưu vào data store) thất bại cho: '{llm_title}'")
                        event_data = None # Không trả về event_data cho client nếu lưu lỗi

                else:
                    logger.error(f"Dữ liệu JSON cho ADD_EVENT không phải là dictionary. Raw data: {cmd_content}")

            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho ADD_EVENT: {e}")
                logger.error(f"Chuỗi JSON lỗi: {cmd_content}")
            except Exception as e_proc:
                 logger.error(f"Lỗi không xác định khi xử lý ADD_EVENT: {e_proc}", exc_info=True)


        # --- Xử lý UPDATE_EVENT ---
        update_event_match = re.search(r"##UPDATE_EVENT:(.*?)##", cleaned_html, re.DOTALL)
        if update_event_match:
            cmd_raw = update_event_match.group(0)
            cmd_content = update_event_match.group(1).strip()
            cleaned_html = cleaned_html.replace(cmd_raw, "").strip()
            logger.info("Tìm thấy lệnh UPDATE_EVENT")
            logger.info(f"Nội dung lệnh UPDATE_EVENT nhận từ LLM: {cmd_content}")

            try:
                details_update = json.loads(cmd_content)
                if isinstance(details_update, dict) and 'id' in details_update:
                    event_id_str = str(details_update['id']) # Đảm bảo ID là chuỗi
                    old_event = events_data.get(event_id_str)
                    if not old_event:
                         logger.warning(f"Không tìm thấy sự kiện ID={event_id_str} để cập nhật. Bỏ qua lệnh.")
                    else:
                        logger.info(f"Tìm thấy sự kiện cũ ID={event_id_str} để cập nhật.")
                        if current_member:
                            details_update['updated_by'] = current_member

                        # Lấy thông tin CẬP NHẬT từ LLM, dùng thông tin CŨ làm mặc định nếu LLM không cung cấp
                        llm_date_input = details_update.get('date', old_event.get('date', '')) # Input ngày từ LLM
                        llm_time_str = details_update.get('time', old_event.get('time', '19:00'))
                        llm_description = details_update.get('description', old_event.get('description', ''))
                        llm_title = details_update.get('title', old_event.get('title', ''))
                        llm_participants = details_update.get('participants', old_event.get('participants', []))

                        logger.info(f"Thông tin cập nhật nhận được: date_input='{llm_date_input}', time='{llm_time_str}', title='{llm_title}', desc='{llm_description[:50]}...'")

                        # --- BẮT ĐẦU LOGIC XỬ LÝ NGÀY MỚI (UPDATE) ---
                        # 1. Xác định lại kiểu lặp lại dựa trên thông tin MỚI
                        repeat_type = determine_repeat_type(llm_description, llm_title)
                        details_update['repeat_type'] = repeat_type
                        logger.debug(f"Xác định repeat_type (update): {repeat_type}")
                        is_recurring_event = (repeat_type == "RECURRING")

                        # 2. Tính toán ngày cuối cùng (Ưu tiên Python)
                        final_date_str = None
                        # Kiểm tra xem LLM có cung cấp ngày cụ thể không
                        try:
                            if llm_date_input:
                                if re.match(r'\d{4}-\d{2}-\d{2}', llm_date_input):
                                    datetime.datetime.strptime(llm_date_input, "%Y-%m-%d")
                                    final_date_str = llm_date_input
                                    logger.info(f"LLM cung cấp ngày cụ thể hợp lệ (update, YYYY-MM-DD): {final_date_str}")
                                elif re.match(r'\d{2}/\d{2}/\d{4}', llm_date_input):
                                    parsed_dt = datetime.datetime.strptime(llm_date_input, "%d/%m/%Y")
                                    final_date_str = parsed_dt.strftime("%Y-%m-%d")
                                    logger.info(f"LLM cung cấp ngày cụ thể hợp lệ (update, DD/MM/YYYY), chuẩn hóa: {final_date_str}")
                        except ValueError:
                            logger.warning(f"Ngày LLM cung cấp (update) '{llm_date_input}' không phải định dạng hợp lệ. Thử tính bằng Python.")
                            final_date_str = None

                        # Tương tự ADD_EVENT, chuẩn bị input cho hàm Python
                        date_input_for_python = llm_date_input
                        if is_recurring_event and not final_date_str:
                              if llm_date_input.lower() in ["hàng ngày", "hàng tuần", "hàng tháng", "hàng năm", "định kỳ", "lặp lại"]:
                                   search_text_for_date = (str(llm_title) + " " + str(llm_description)).lower()
                                   date_input_for_python = search_text_for_date
                                   logger.info(f"Update sự kiện lặp lại, thử tìm ngày trong description: '{search_text_for_date}'")
                              else:
                                  logger.info(f"Update sự kiện lặp lại, sử dụng date='{llm_date_input}' để tìm ngày gần nhất.")


                        # Nếu không có ngày cụ thể từ LLM hoặc là lặp lại, dùng Python tính
                        if not final_date_str and date_input_for_python:
                            logger.info(f"Thực hiện tính toán ngày (update) bằng Python từ: '{date_input_for_python}'")
                            calculated_python_date_str = get_date_from_relative_term(date_input_for_python)
                            if calculated_python_date_str:
                                final_date_str = calculated_python_date_str
                                logger.info(f"Hàm Python tính được ngày (update): {final_date_str}")
                            else:
                                logger.warning(f"Không thể tính ngày (update) từ '{date_input_for_python}' bằng Python.")
                                final_date_str = None # Giữ ngày cũ hay bỏ trống? -> Bỏ trống/None để thể hiện không xác định được ngày mới

                        # Xử lý lặp lại không có ngày cụ thể (update)
                        if is_recurring_event and not final_date_str:
                             if "hàng ngày" in str(llm_description).lower() or "mỗi ngày" in str(llm_description).lower():
                                  final_date_str = datetime.date.today().strftime("%Y-%m-%d")
                                  logger.info(f"Update sự kiện lặp lại hàng ngày, đặt ngày bắt đầu gần nhất là hôm nay: {final_date_str}")
                             # ... (logic khác)

                        # Cập nhật lại details_update với dữ liệu cuối cùng trước khi lưu
                        # Nếu final_date_str là None (không tính được ngày mới), thì KHÔNG cập nhật trường date cũ
                        if final_date_str is not None:
                             details_update['date'] = final_date_str
                        elif 'date' in details_update: # Nếu LLM đưa 'date' nhưng tính không ra ngày mới -> loại bỏ khỏi update
                            del details_update['date']
                            logger.warning("Không tính được ngày mới từ input, sẽ không cập nhật trường 'date' của sự kiện.")

                        details_update['time'] = llm_time_str
                        details_update['title'] = llm_title
                        details_update['description'] = llm_description
                        details_update['participants'] = llm_participants
                        # ID đã có sẵn trong details_update['id']
                        # --- KẾT THÚC LOGIC XỬ LÝ NGÀY MỚI (UPDATE) ---


                        # 3. Sanity Check (TÙY CHỌN - giữ nguyên logic kiểm tra)
                        # Sử dụng final_date_str nếu nó được tính toán, nếu không thì bỏ qua check này
                        if final_date_str and (llm_title or llm_description):
                             try:
                                  # ... (logic sanity check như trong ADD_EVENT) ...
                                  logger.warning(
                                       f"SANITY CHECK WARNING (UPDATE - sau khi Python tính): Ngày Python tính {final_date_str} ({actual_day_name}) "
                                       f"KHÔNG KHỚP với ngày được đề cập ({mentioned_day_name}). "
                                       # ...
                                  )
                             except Exception as sanity_e_update:
                                  logger.error(f"Lỗi trong Sanity Check (Update): {sanity_e_update}")


                        # 4. Tạo cron expression (LOGIC TƯƠNG TỰ ADD_EVENT)
                        cron_expression = ""
                        # Phải dùng ngày cuối cùng đã xác định (final_date_str) nếu có
                        date_for_cron = final_date_str if final_date_str is not None else old_event.get('date') # Ưu tiên ngày mới, nếu không dùng ngày cũ

                        if is_recurring_event:
                            cron_expression = generate_recurring_cron(llm_description, llm_title, llm_time_str)
                            logger.info(f"Tạo cron RECURRING (update): {cron_expression}")
                        else: # ONCE
                            if date_for_cron: # Chỉ tạo cron một lần nếu có ngày hợp lệ (mới hoặc cũ)
                                cron_expression = date_time_to_cron(date_for_cron, llm_time_str)
                                logger.info(f"Tạo cron ONCE (update): {cron_expression} cho ngày {date_for_cron}")
                            else:
                                logger.error("Không thể tạo cron ONCE (update) vì thiếu ngày hợp lệ (cả mới và cũ).")
                                cron_expression = ""

                        # 5. Tạo event_data để trả về cho client
                        event_data = {
                            "action": "update",
                            "id": event_id_str, # ID của sự kiện cần cập nhật
                            "title": llm_title,
                            "description": llm_description,
                            "cron_expression": cron_expression,
                            "repeat_type": repeat_type,
                            "original_date": final_date_str if final_date_str is not None else old_event.get('date'), # Ngày cuối cùng (mới hoặc cũ nếu mới không có)
                            "original_time": llm_time_str,
                            "participants": llm_participants
                        }
                        logger.debug(f"Event data (update) được tạo cho client: {event_data}")

                        # 6. Thực hiện cập nhật sự kiện trong data store
                        # Hàm update_event nên nhận 'details_update' chỉ chứa các trường cần cập nhật
                        if update_event(details_update): # details_update đã được chuẩn bị ở trên
                            logger.info(f"Đã thực thi lệnh UPDATE_EVENT thành công và lưu vào data store cho ID: {event_id_str}")
                        else:
                            logger.error(f"Thực thi lệnh UPDATE_EVENT (lưu vào data store) thất bại cho ID: {event_id_str}")
                            event_data = None # Không trả về event_data nếu lưu lỗi
                else:
                    logger.error(f"Dữ liệu JSON cho UPDATE_EVENT không phải dictionary hoặc thiếu 'id'. Raw data: {cmd_content}")

            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho UPDATE_EVENT: {e}")
                logger.error(f"Chuỗi JSON lỗi: {cmd_content}")
            except Exception as e_proc:
                 logger.error(f"Lỗi không xác định khi xử lý UPDATE_EVENT: {e_proc}", exc_info=True)


        # --- Xử lý DELETE_EVENT (Giữ nguyên logic) ---
        delete_event_match = re.search(r"##DELETE_EVENT:(.*?)##", cleaned_html)
        if delete_event_match:
            cmd_raw = delete_event_match.group(0)
            event_id_to_delete = delete_event_match.group(1).strip()
            cleaned_html = cleaned_html.replace(cmd_raw, "").strip()
            logger.info(f"Tìm thấy lệnh DELETE_EVENT cho ID: {event_id_to_delete}")

            event_info_before_delete = events_data.get(str(event_id_to_delete), {})

            if delete_event(event_id_to_delete):
                logger.info(f"Đã thực thi lệnh DELETE_EVENT thành công trong data store cho ID: {event_id_to_delete}")
                event_data = {
                    "action": "delete",
                    "id": event_id_to_delete,
                    "title": event_info_before_delete.get('title', '[không rõ]'),
                    "description": event_info_before_delete.get('description', '')
                }
                logger.debug(f"Event data (delete) được tạo cho client: {event_data}")
            else:
                logger.error(f"Thực thi lệnh DELETE_EVENT thất bại cho ID: {event_id_to_delete} (có thể không tồn tại).")


        # --- Xử lý các lệnh khác (Giữ nguyên logic) ---
        other_commands_to_process = ["ADD_FAMILY_MEMBER", "UPDATE_PREFERENCE", "ADD_NOTE"]
        for cmd_prefix in other_commands_to_process:
            cmd_pattern = f"##{cmd_prefix}:(.*?)##"
            match = re.search(cmd_pattern, cleaned_html, re.DOTALL)
            while match:
                cmd_raw_other = match.group(0)
                cmd_content_other = match.group(1).strip()
                temp_cleaned_html = cleaned_html.replace(cmd_raw_other, "", 1)

                logger.info(f"Tìm thấy lệnh {cmd_prefix}")
                logger.debug(f"Nội dung lệnh {cmd_prefix}: {cmd_content_other}")
                try:
                    details_other = json.loads(cmd_content_other)
                    if isinstance(details_other, dict):
                        action_successful = False
                        if cmd_prefix == "ADD_FAMILY_MEMBER":
                            add_family_member(details_other)
                            action_successful = True
                            logger.info(f"Đã thực thi ADD_FAMILY_MEMBER cho: {details_other.get('name')}")
                        elif cmd_prefix == "UPDATE_PREFERENCE":
                            update_preference(details_other)
                            action_successful = True
                            logger.info(f"Đã thực thi UPDATE_PREFERENCE cho ID: {details_other.get('id')}")
                        elif cmd_prefix == "ADD_NOTE":
                            if current_member:
                                details_other['created_by'] = current_member
                            add_note(details_other)
                            action_successful = True
                            logger.info(f"Đã thực thi ADD_NOTE cho tiêu đề: {details_other.get('title')}")

                        if action_successful:
                             cleaned_html = temp_cleaned_html
                        else:
                             logger.warning(f"Hành động {cmd_prefix} có thể đã thất bại, giữ nguyên lệnh.")
                             break
                    else:
                        logger.error(f"Dữ liệu JSON cho {cmd_prefix} không phải dict. Raw: {cmd_content_other}")
                        break
                except json.JSONDecodeError as e:
                    logger.error(f"Lỗi JSON {cmd_prefix}: {e}. Raw: {cmd_content_other}")
                    break
                except Exception as e_proc_other:
                    logger.error(f"Lỗi xử lý {cmd_prefix}: {e_proc_other}", exc_info=True)
                    break
                match = re.search(cmd_pattern, cleaned_html, re.DOTALL)

        logger.debug(f"Kết thúc xử lý phản hồi. Độ dài HTML cuối cùng: {len(cleaned_html)}. Event data trả về: {'Có' if event_data else 'Không'}")
        return cleaned_html.strip(), event_data

    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng không xác định trong process_assistant_response: {e}", exc_info=True)
        return response, None # Trả về gốc nếu có lỗi lớn

@app.get("/weather/{location}")
async def get_weather_endpoint(
    location: str,
    days: int = 1,
    openweather_api_key: Optional[str] = None
):
    """
    Endpoint riêng biệt để lấy thông tin thời tiết
    """
    # Sử dụng API key từ tham số hoặc biến môi trường
    api_key = openweather_api_key or OPENWEATHER_API_KEY
    
    # Khởi tạo dịch vụ thời tiết tạm thời với API key cung cấp
    temp_weather_service = WeatherService(openweather_api_key=api_key)
    
    try:
        # Lấy dữ liệu thời tiết
        weather_data = await temp_weather_service.get_weather(location, days)
        
        # Tạo tin nhắn HTML
        weather_html = temp_weather_service.format_weather_message(weather_data, location, days)
        
        # Trả về cả dữ liệu thô và HTML đã định dạng
        return {
            "raw_data": weather_data,
            "formatted_html": weather_html,
            "location": location,
            "days": days,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Lỗi khi lấy thông tin thời tiết cho {location}: {e}")
        return {
            "error": str(e),
            "location": location,
            "days": days,
            "status": "error"
        }

@app.on_event("startup")
async def startup_event():
    """Các tác vụ cần thực hiện khi khởi động server"""
    logger.info("Khởi động Family Assistant API server")
    # Đảm bảo tất cả thư mục cần thiết đã được tạo
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

@app.on_event("shutdown")
async def shutdown_event():
    """Các tác vụ cần thực hiện khi đóng server"""
    logger.info("Đóng Family Assistant API server")
    # Lưu lại tất cả dữ liệu
    save_data(FAMILY_DATA_FILE, family_data)
    save_data(EVENTS_DATA_FILE, events_data)
    save_data(NOTES_DATA_FILE, notes_data)
    save_data(CHAT_HISTORY_FILE, chat_history)

# Thêm endpoint mới để quản lý session
@app.get("/sessions")
async def list_sessions():
    """Liệt kê tất cả session đang tồn tại"""
    sessions_info = {}
    for session_id, session_data in session_manager.sessions.items():
        sessions_info[session_id] = {
            "created_at": session_data.get("created_at", "unknown"),
            "last_updated": session_data.get("last_updated", "unknown"),
            "member_id": session_data.get("current_member"),
            "message_count": len(session_data.get("messages", [])),
        }
    return sessions_info

@app.delete("/cleanup_sessions")
async def cleanup_old_sessions(days: int = 30):
    """Xóa các session cũ không hoạt động quá số ngày chỉ định"""
    session_manager.cleanup_old_sessions(days_threshold=days)
    return {"status": "success", "message": f"Đã xóa các session không hoạt động trên {days} ngày"}

# ----- Thêm các endpoint bổ sung -----

@app.get("/")
async def root():
    """Endpoint chào mừng"""
    return {
        "name": "Trợ lý Gia đình API",
        "version": "1.0.0",
        "description": "API cho ứng dụng Trợ lý Gia đình",
        "endpoints": [
            "/chat - Endpoint chính để tương tác với trợ lý",
            "/chat/stream - Phiên bản streaming của endpoint chat",
            "/suggested_questions - Tạo và lấy câu hỏi gợi ý cho người dùng",
            "/cached_suggested_questions - Lấy câu hỏi gợi ý đã tạo trước đó",
            "/family_members - Quản lý thành viên gia đình",
            "/events - Quản lý sự kiện",
            "/notes - Quản lý ghi chú",
            "/search - Tìm kiếm thông tin thời gian thực",
            "/session - Quản lý phiên làm việc"
        ]
    }

@app.get("/chat_history/{member_id}")
async def get_chat_history(member_id: str):
    """Lấy lịch sử trò chuyện của một thành viên"""
    if member_id in chat_history:
        return chat_history[member_id]
    return []

@app.post("/analyze_image")
async def analyze_image_endpoint(
    file: UploadFile = File(...),
    openai_api_key: str = Form(...),
    member_id: Optional[str] = Form(None),
    prompt: Optional[str] = Form("Describe what you see in this image"),
    content_type: str = Form("image")  # THÊM TRƯỜNG MỚI
):
    """Endpoint phân tích hình ảnh"""
    try:
        # Ghi log nội dung loại request
        logger.info(f"Nhận yêu cầu phân tích ảnh với content_type: {content_type}")
        
        # Đọc file hình ảnh
        image_content = await file.read()
        
        # Lưu tạm file để xử lý
        temp_img_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{file.filename.split('.')[-1]}")
        with open(temp_img_path, "wb") as f:
            f.write(image_content)
        
        # Đọc và chuyển đổi sang base64
        img = Image.open(temp_img_path)
        img_base64 = get_image_base64(img)
        
        # Xử lý với OpenAI API
        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": "Phân tích chi tiết về hình ảnh này. Nếu là món ăn, hãy mô tả món ăn và đề xuất công thức. Nếu là hoạt động gia đình, hãy mô tả hoạt động."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}
                ]}
            ],
            max_tokens=1000
        )
        
        # Xóa file tạm sau khi xử lý
        os.remove(temp_img_path)
        
        # Chuyển đổi phân tích thành âm thanh nếu cần
        analysis_text = response.choices[0].message.content
        audio_response = None
        
        # Tạo phản hồi âm thanh nếu cần
        try:
            audio_response = text_to_speech_google(analysis_text)
            logger.info("Đã tạo âm thanh từ phân tích hình ảnh")
        except Exception as audio_err:
            logger.error(f"Không thể tạo âm thanh từ phân tích: {str(audio_err)}")
        
        # Trả về kết quả phân tích
        return {
            "analysis": analysis_text,
            "member_id": member_id,
            "content_type": content_type,
            "audio_response": audio_response
        }
        
    except Exception as e:
        logger.error(f"Lỗi khi phân tích hình ảnh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi phân tích hình ảnh: {str(e)}")

@app.post("/transcribe_audio")
async def transcribe_audio_endpoint(
    file: UploadFile = File(...),
    openai_api_key: str = Form(...)
):
    """Endpoint chuyển đổi âm thanh thành văn bản"""
    try:
        # Đọc file âm thanh
        audio_content = await file.read()
        
        # Lưu tạm file để xử lý
        temp_audio_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.wav")
        with open(temp_audio_path, "wb") as f:
            f.write(audio_content)
        
        # Chuyển đổi âm thanh thành văn bản
        client = OpenAI(api_key=openai_api_key)
        with open(temp_audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        
        # Xóa file tạm sau khi xử lý
        os.remove(temp_audio_path)
        
        # Trả về kết quả
        return {"text": transcript.text}
        
    except Exception as e:
        logger.error(f"Lỗi khi xử lý file âm thanh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý file âm thanh: {str(e)}")

@app.post("/tts")
async def text_to_speech_endpoint(
    text: str = Form(...),
    lang: str = Form(default="vi"),
    slow: bool = Form(default=False)
):
    """Endpoint chuyển đổi văn bản thành giọng nói sử dụng Google TTS"""
    try:
        audio_base64 = text_to_speech_google(text, lang, slow)
        if audio_base64:
            return {
                "audio_data": audio_base64,
                "format": "mp3",
                "lang": lang,
                "provider": "Google TTS"
            }
        else:
            raise HTTPException(status_code=500, detail="Không thể chuyển đổi văn bản thành giọng nói")
    except Exception as e:
        logger.error(f"Lỗi trong text_to_speech_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {str(e)}")


# ----- Khởi động server -----
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Trợ lý Gia đình API")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Auto reload server on code changes")
    args = parser.parse_args()
    
    logger.info(f"Khởi động Trợ lý Gia đình API trên {args.host}:{args.port}")
    
    if args.reload:
        uvicorn.run("app:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port)

# ----- Khởi động server -----
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Trợ lý Gia đình API")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host IP")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Auto reload server on code changes")
    args = parser.parse_args()
    
    logger.info(f"Khởi động Trợ lý Gia đình API trên {args.host}:{args.port}")
    
    if args.reload:
        uvicorn.run("app:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port)