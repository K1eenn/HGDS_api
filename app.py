import json
import os
import base64
from datetime import datetime
import hashlib
import random
import logging
import re
from io import BytesIO
from typing import Optional, List, Dict, Any, Union
import uuid

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, BackgroundTasks, Response, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import requests
from PIL import Image
import dotenv
from openai import OpenAI
import uvicorn

# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('family_assistant_api')

# Đường dẫn các file dữ liệu
FAMILY_DATA_FILE = "family_data.json"
EVENTS_DATA_FILE = "events_data.json"
NOTES_DATA_FILE = "notes_data.json"
CHAT_HISTORY_FILE = "chat_history.json"
SESSION_DATA_FILE = "session_data.json"

# Model OpenAI
OPENAI_MODEL = "gpt-4o-mini"

# Tạo ứng dụng FastAPI
app = FastAPI(
    title="Trợ lý Gia đình API",
    description="API cho ứng dụng Trợ lý Gia đình hỗ trợ đầu vào dạng text, image và audio",
    version="1.0.0"
)

# Thêm CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Các model dữ liệu ---------
class MessageContent(BaseModel):
    type: str  # "text", "image_url"
    text: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None

class Message(BaseModel):
    role: str
    content: List[MessageContent]

class SessionState(BaseModel):
    session_id: str
    current_member: Optional[str] = None
    messages: List[Message] = []
    suggested_question: Optional[str] = None
    process_suggested: bool = False
    question_cache: Dict[str, List[str]] = {}

class TextRequest(BaseModel):
    session_id: str
    text: str
    current_member: Optional[str] = None

class FamilyMember(BaseModel):
    name: str
    age: Optional[str] = None
    preferences: Dict[str, str] = {}

class Event(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    description: Optional[str] = None
    participants: List[str] = []

class Note(BaseModel):
    title: str
    content: str
    tags: List[str] = []

class ApiKeys(BaseModel):
    openai_api_key: str
    tavily_api_key: Optional[str] = None

# --------- Quản lý phiên ---------
# Lưu trữ session state
session_states: Dict[str, SessionState] = {}

# Tải dữ liệu session nếu có
def load_session_data():
    global session_states
    if os.path.exists(SESSION_DATA_FILE):
        try:
            with open(SESSION_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for session_id, state in data.items():
                    session_states[session_id] = SessionState(**state)
        except Exception as e:
            logger.error(f"Lỗi khi tải dữ liệu session: {e}")

# Lưu dữ liệu session
def save_session_data():
    try:
        session_data = {s_id: state.dict() for s_id, state in session_states.items()}
        with open(SESSION_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu session: {e}")

# --------- Hàm hữu ích ---------
# Di chuyển tất cả hàm tiện ích từ app.py
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Đảm bảo dữ liệu là một từ điển
                if not isinstance(data, dict):
                    logger.error(f"Dữ liệu trong {file_path} không phải từ điển. Khởi tạo lại.")
                    return {}
                return data
        except Exception as e:
            logger.error(f"Lỗi khi đọc {file_path}: {e}")
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

# Tải dữ liệu
family_data = load_data(FAMILY_DATA_FILE)
events_data = load_data(EVENTS_DATA_FILE)
notes_data = load_data(NOTES_DATA_FILE)
chat_history = load_data(CHAT_HISTORY_FILE)

# Hàm xử lý hình ảnh
def get_image_base64(image_raw):
    buffered = BytesIO()
    image_raw.save(buffered, format=image_raw.format or "JPEG")
    img_byte = buffered.getvalue()
    return base64.b64encode(img_byte).decode('utf-8')

# Hàm tạo và cập nhật phiên
def get_or_create_session(session_id: Optional[str] = None) -> str:
    if not session_id or session_id not in session_states:
        # Tạo session mới
        new_session_id = session_id or str(uuid.uuid4())
        session_states[new_session_id] = SessionState(session_id=new_session_id)
        save_session_data()
        return new_session_id
    return session_id

# ----- Triển khai lại các hàm từ app.py -----

# TAVILY API INTEGRATION
def tavily_extract(api_key, urls, include_images=False, extract_depth="advanced"):
    """Trích xuất nội dung từ URL sử dụng Tavily Extract API"""
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
    """Thực hiện tìm kiếm thời gian thực sử dụng Tavily Search API"""
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

def search_and_summarize(tavily_api_key, query, openai_api_key):
    """Tìm kiếm và tổng hợp thông tin từ kết quả tìm kiếm"""
    if not tavily_api_key or not openai_api_key or not query:
        return "Thiếu thông tin để thực hiện tìm kiếm hoặc tổng hợp."
    
    try:
        # Thực hiện tìm kiếm với Tavily
        search_results = tavily_search(tavily_api_key, query)
        
        if not search_results or "results" not in search_results:
            return "Không tìm thấy kết quả nào."
        
        # Trích xuất thông tin từ top kết quả
        urls_to_extract = [result["url"] for result in search_results["results"][:3]]
        extracted_contents = []
        
        for url in urls_to_extract:
            extract_result = tavily_extract(tavily_api_key, url)
            if extract_result and "results" in extract_result and len(extract_result["results"]) > 0:
                content = extract_result["results"][0].get("raw_content", "")
                # Giới hạn độ dài nội dung để tránh token quá nhiều
                if len(content) > 8000:
                    content = content[:8000] + "..."
                extracted_contents.append({
                    "url": url,
                    "content": content
                })
        
        if not extracted_contents:
            return "Không thể trích xuất nội dung từ các kết quả tìm kiếm."
        
        # Tổng hợp thông tin sử dụng OpenAI
        client = OpenAI(api_key=openai_api_key)
        
        # Chuẩn bị prompt cho việc tổng hợp
        prompt = f"""
        Dưới đây là các nội dung trích xuất từ internet liên quan đến câu hỏi: "{query}"
        
        {json.dumps(extracted_contents, ensure_ascii=False)}
        
        Hãy tổng hợp thông tin từ các nguồn trên để trả lời câu hỏi một cách đầy đủ và chính xác.
        Hãy trình bày thông tin một cách rõ ràng, có cấu trúc.
        Nếu thông tin từ các nguồn khác nhau mâu thuẫn, hãy đề cập đến điều đó.
        Hãy ghi rõ nguồn thông tin (URL) ở cuối mỗi phần thông tin.
        """
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Bạn là trợ lý tổng hợp thông tin. Nhiệm vụ của bạn là tổng hợp thông tin từ nhiều nguồn để cung cấp câu trả lời đầy đủ, chính xác và có cấu trúc."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1500
        )
        
        summarized_info = response.choices[0].message.content
        
        # Thêm thông báo về nguồn
        sources_info = "\n\n**Nguồn thông tin:**\n" + "\n".join([f"- {result['url']}" for result in search_results["results"][:3]])
        
        return f"{summarized_info}\n{sources_info}"
    
    except Exception as e:
        logger.error(f"Lỗi trong quá trình tìm kiếm và tổng hợp: {e}")
        return f"Có lỗi xảy ra trong quá trình tìm kiếm và tổng hợp thông tin: {str(e)}"

# Thêm các hàm tiện ích cho việc tính toán ngày tháng
def get_date_from_relative_term(term):
    """Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế"""
    today = datetime.now().date()
    
    if term in ["hôm nay", "today"]:
        return today
    elif term in ["ngày mai", "mai", "tomorrow"]:
        return today + datetime.timedelta(days=1)
    elif term in ["ngày kia", "day after tomorrow"]:
        return today + datetime.timedelta(days=2)
    elif term in ["hôm qua", "yesterday"]:
        return today - datetime.timedelta(days=1)
    elif "tuần tới" in term or "tuần sau" in term or "next week" in term:
        return today + datetime.timedelta(days=7)
    elif "tuần trước" in term or "last week" in term:
        return today - datetime.timedelta(days=7)
    elif "tháng tới" in term or "tháng sau" in term or "next month" in term:
        # Đơn giản hóa bằng cách thêm 30 ngày
        return today + datetime.timedelta(days=30)
    
    return None

# Thêm hàm tạo câu hỏi gợi ý động
def generate_dynamic_suggested_questions(api_key, member_id=None, max_questions=5):
    """
    Tạo câu hỏi gợi ý cá nhân hóa và linh động dựa trên thông tin thành viên, 
    lịch sử trò chuyện và thời điểm hiện tại
    """
    # Kiểm tra cache để tránh tạo câu hỏi mới quá thường xuyên
    cache_key = f"suggested_questions_{member_id}_{datetime.now().strftime('%Y-%m-%d_%H')}"
    
    for session in session_states.values():
        if cache_key in session.question_cache:
            return session.question_cache[cache_key]
    
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
    today = datetime.now().date()
    
    for event_id, event in events_data.items():
        try:
            event_date = datetime.strptime(event.get("date", ""), "%Y-%m-%d").date()
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
                "current_time": datetime.now().strftime("%H:%M"),
                "current_day": datetime.now().strftime("%A"),
                "current_date": datetime.now().strftime("%Y-%m-%d")
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
            
            Ví dụ tốt:
            - "Top 5 phim hành động hay nhất 2023?"
            - "Công thức bánh mì nguyên cám giảm cân?"
            - "Kết quả Champions League?"
            - "5 bài tập cardio giảm mỡ bụng hiệu quả?"
            
            Ví dụ không tốt:
            - "Bạn đã biết bộ phim 'The Goal' vừa được phát hành và nhận nhiều phản hồi tích cực từ khán giả chưa?" (Kết hợp phim + bóng đá)
            - "Kết quả trận đấu Champions League: Man City 3-1 Real Madrid, bạn có theo dõi không?" (Kết thúc bằng câu hỏi)
            - "Bạn có muốn xem những phát hiện mới về dinh dưỡng không?" (Không cung cấp thông tin cụ thể)
            
            Trả về chính xác {max_questions} câu gợi ý.
            """
            
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Bạn là trợ lý tạo câu hỏi gợi ý cá nhân hóa."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=300
            )
            
            # Xử lý phản hồi từ OpenAI
            generated_content = response.choices[0].message.content.strip()
            questions = [q.strip() for q in generated_content.split('\n') if q.strip()]
            
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
        random_seed = int(hashlib.md5(f"{datetime.now().strftime('%Y-%m-%d_%H')}_{member_id or 'guest'}".encode()).hexdigest(), 16) % 10000
        random.seed(random_seed)
        
        # Mẫu câu thông tin cụ thể theo nhiều chủ đề khác nhau (không có câu hỏi cuối câu)
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
            # Các mẫu câu khác từ code gốc
            # ...
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
            # Các biến thay thế khác từ code gốc
            # ...
        }
        
        # Thay thế các biến bằng thông tin cá nhân nếu có
        if member_id and member_id in family_data:
            preferences = family_data[member_id].get("preferences", {})
            
            if preferences.get("food"):
                replacements["food"].insert(0, preferences["food"])
            
            if preferences.get("hobby"):
                replacements["hobby"].insert(0, preferences["hobby"])
        
        # Xác định mùa hiện tại (đơn giản hóa)
        current_month = datetime.now().month
        if 3 <= current_month <= 5:
            current_season = "xuân"
        elif 6 <= current_month <= 8:
            current_season = "hạ"
        elif 9 <= current_month <= 11:
            current_season = "thu"
        else:
            current_season = "đông"
        
        replacements["season"] = [current_season]
        
        # Thêm ngày hiện tại
        current_day_name = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"][datetime.now().weekday()]
        replacements["day"] = [current_day_name]
        
        # Thêm bữa ăn phù hợp với thời điểm hiện tại
        current_hour = datetime.now().hour
        if 5 <= current_hour < 10:
            current_meal = "sáng"
        elif 10 <= current_hour < 14:
            current_meal = "trưa"
        elif 14 <= current_hour < 17:
            current_meal = "xế"
        else:
            current_meal = "tối"
        
        replacements["meal"] = [current_meal]
        replacements["time_of_day"] = [current_meal]
        
        # Tạo danh sách các chủ đề ưu tiên theo sở thích người dùng
        priority_categories = []
        
        # Logic từ code gốc để tạo danh sách câu hỏi từ template
        # ...
        
        # Chọn ngẫu nhiên từ các mẫu câu
        if len(questions) < max_questions:
            categories = list(question_templates.keys())
            random.shuffle(categories)
            
            for category in categories:
                if len(questions) >= max_questions:
                    break
                template = random.choice(question_templates[category])
                # Thay thế các placeholder
                question = template
                for key, values in replacements.items():
                    if "{" + key + "}" in question:
                        question = question.replace("{" + key + "}", random.choice(values))
                # Thêm câu hỏi
                if question not in questions:
                    questions.append(question)
    
    # Lưu vào cache cho tất cả phiên
    for session_id, session in session_states.items():
        session.question_cache[cache_key] = questions
    
    save_session_data()
    return questions

# Phát hiện câu hỏi cần search thông tin thực tế
def detect_search_intent(query, api_key):
    """
    Phát hiện xem câu hỏi có cần tìm kiếm thông tin thực tế hay không
    và tinh chỉnh câu truy vấn để bao gồm các yếu tố thời gian.
    """
    try:
        client = OpenAI(api_key=api_key)
        current_date_str = datetime.now().strftime("%Y-%m-%d") # Lấy ngày hiện tại

        system_prompt = f"""
Bạn là một hệ thống phân loại và tinh chỉnh câu hỏi thông minh. Nhiệm vụ của bạn là:
1. Xác định xem câu hỏi có cần tìm kiếm thông tin thực tế, tin tức mới hoặc dữ liệu cập nhật không.
2. Nếu cần tìm kiếm, hãy tinh chỉnh câu hỏi thành một truy vấn tìm kiếm tối ưu cho search engine. ĐẶC BIỆT CHÚ Ý đến các yếu tố thời gian (ví dụ: hôm nay, hôm qua, tuần này, tháng trước, năm 2023, tối qua, sáng nay...).
3. Hãy kết hợp các yếu tố thời gian này vào `search_query` để kết quả tìm kiếm được chính xác hơn về mặt thời gian.

Hôm nay là ngày: {current_date_str}.

Câu hỏi cần search khi:
- Liên quan đến tin tức, sự kiện hiện tại hoặc gần đây (ví dụ: "tin tức hôm nay", "kết quả bóng đá tối qua").
- Yêu cầu dữ liệu thực tế, số liệu thống kê cập nhật (ví dụ: "giá vàng tuần này").
- Hỏi về kết quả thể thao, giải đấu đang diễn ra hoặc vừa kết thúc.
- Cần thông tin về giá cả, sản phẩm mới ra mắt.
- Liên quan đến thời tiết, tình hình giao thông hiện tại.

Câu hỏi KHÔNG cần search khi:
- Liên quan đến quản lý gia đình trong ứng dụng này (thêm thành viên, sự kiện, ghi chú).
- Hỏi ý kiến, lời khuyên cá nhân không dựa trên dữ liệu thực tế.
- Yêu cầu công thức nấu ăn phổ biến, kiến thức phổ thông không thay đổi nhanh.
- Yêu cầu hỗ trợ sử dụng ứng dụng.

Trả lời DƯỚI DẠNG JSON với 2 trường:
- need_search (boolean: true hoặc false)
- search_query (string: câu truy vấn tìm kiếm đã được tối ưu, bao gồm cả yếu tố thời gian nếu có và cần thiết). Nếu need_search là false, trường này có thể là chuỗi rỗng hoặc câu truy vấn gốc.
"""

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Câu hỏi của người dùng: \"{query}\"\n\nHãy phân tích và trả về JSON theo yêu cầu."}
            ],
            temperature=0.1,
            max_tokens=250,
            response_format={"type": "json_object"}
        )

        result_str = response.choices[0].message.content
        logger.info(f"Kết quả detect_search_intent (raw): {result_str}")

        try:
            result = json.loads(result_str)
            need_search = result.get("need_search", False)
            search_query = result.get("search_query", query) if need_search else query
            if need_search and not search_query:
                 search_query = query

            logger.info(f"Phân tích truy vấn: need_search={need_search}, search_query='{search_query}'")
            return need_search, search_query

        except json.JSONDecodeError as json_err:
            logger.error(f"Lỗi giải mã JSON từ detect_search_intent: {json_err}")
            return False, query
        except Exception as e:
            logger.error(f"Lỗi không xác định trong detect_search_intent: {e}")
            return False, query

    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_search_intent: {e}")
        return False, query

# Hàm tạo tóm tắt lịch sử chat
def generate_chat_summary(messages, api_key):
    """Tạo tóm tắt từ lịch sử trò chuyện"""
    if not messages or len(messages) < 3:
        return "Chưa có đủ tin nhắn để tạo tóm tắt."
    
    # Chuẩn bị dữ liệu cho API
    content_texts = []
    for message in messages:
        if "content" in message:
            # Xử lý cả tin nhắn văn bản và hình ảnh
            for content in message["content"]:
                if content["type"] == "text":
                    content_texts.append(f"{message['role'].upper()}: {content['text']}")
    
    # Ghép tất cả nội dung lại
    full_content = "\n".join(content_texts)
    
    # Gọi API để tạo tóm tắt
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
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
def save_chat_history(member_id, messages, summary=None):
    """Lưu lịch sử chat cho một thành viên cụ thể"""
    if member_id not in chat_history:
        chat_history[member_id] = []
    
    # Tạo bản ghi mới
    history_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": messages,
        "summary": summary if summary else ""
    }
    
    # Thêm vào lịch sử và giới hạn số lượng
    chat_history[member_id].insert(0, history_entry)  # Thêm vào đầu danh sách
    
    # Giới hạn lưu tối đa 10 cuộc trò chuyện gần nhất
    if len(chat_history[member_id]) > 10:
        chat_history[member_id] = chat_history[member_id][:10]
    
    # Lưu vào file
    save_data(CHAT_HISTORY_FILE, chat_history)

# Lọc sự kiện theo người dùng
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

# Các hàm quản lý thông tin gia đình
def add_family_member(details):
    member_id = details.get("id") or str(len(family_data) + 1)
    family_data[member_id] = {
        "name": details.get("name", ""),
        "age": details.get("age", ""),
        "preferences": details.get("preferences", {}),
        "added_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(FAMILY_DATA_FILE, family_data)
    return member_id

def update_preference(details):
    member_id = details.get("id")
    preference_key = details.get("key")
    preference_value = details.get("value")
    
    if member_id in family_data and preference_key:
        if "preferences" not in family_data[member_id]:
            family_data[member_id]["preferences"] = {}
        family_data[member_id]["preferences"][preference_key] = preference_value
        save_data(FAMILY_DATA_FILE, family_data)
        return True
    return False

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
            "created_by": details.get("created_by", ""),
            "created_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_data(EVENTS_DATA_FILE, events_data)
        logger.info(f"Đã thêm sự kiện: {details.get('title', '')} vào {EVENTS_DATA_FILE}")
        return event_id
    except Exception as e:
        logger.error(f"Lỗi khi thêm sự kiện: {e}")
        return None

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
                events_data[event_id]["created_on"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
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
        return True
    return False

# Các hàm quản lý ghi chú
def add_note(details):
    note_id = str(len(notes_data) + 1)
    notes_data[note_id] = {
        "title": details.get("title", ""),
        "content": details.get("content", ""),
        "tags": details.get("tags", []),
        "created_by": details.get("created_by", ""),
        "created_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(NOTES_DATA_FILE, notes_data)
    return note_id

def delete_note(note_id):
    if note_id in notes_data:
        del notes_data[note_id]
        save_data(NOTES_DATA_FILE, notes_data)
        return True
    return False

# Hàm xử lý phản hồi trợ lý
def process_assistant_response(response, current_member=None):
    """Hàm xử lý lệnh từ phản hồi của trợ lý"""
    try:
        logger.info(f"Xử lý phản hồi của trợ lý, độ dài: {len(response)}")
        results = []
        
        # Xử lý lệnh thêm sự kiện
        if "##ADD_EVENT:" in response:
            logger.info("Tìm thấy lệnh ADD_EVENT")
            cmd_start = response.index("##ADD_EVENT:") + len("##ADD_EVENT:")
            cmd_end = response.index("##", cmd_start)
            cmd = response[cmd_start:cmd_end].strip()
            
            try:
                details = json.loads(cmd)
                if isinstance(details, dict):
                    # Xử lý các từ ngữ tương đối về thời gian
                    if details.get('date') and not details['date'][0].isdigit():
                        relative_date = get_date_from_relative_term(details['date'].lower())
                        if relative_date:
                            details['date'] = relative_date.strftime("%Y-%m-%d")
                    
                    # Thêm thông tin về người tạo sự kiện
                    if current_member:
                        details['created_by'] = current_member
                    
                    event_id = add_event(details)
                    if event_id:
                        results.append({
                            "action": "add_event",
                            "success": True,
                            "event_id": event_id,
                            "title": details.get('title', '')
                        })
            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho ADD_EVENT: {e}")
        
        # Xử lý lệnh UPDATE_EVENT
        if "##UPDATE_EVENT:" in response:
            logger.info("Tìm thấy lệnh UPDATE_EVENT")
            cmd_start = response.index("##UPDATE_EVENT:") + len("##UPDATE_EVENT:")
            cmd_end = response.index("##", cmd_start)
            cmd = response[cmd_start:cmd_end].strip()
            
            try:
                details = json.loads(cmd)
                if isinstance(details, dict):
                    # Xử lý các từ ngữ tương đối về thời gian
                    if details.get('date') and not details['date'][0].isdigit():
                        relative_date = get_date_from_relative_term(details['date'].lower())
                        if relative_date:
                            details['date'] = relative_date.strftime("%Y-%m-%d")
                    
                    success = update_event(details)
                    results.append({
                        "action": "update_event",
                        "success": success,
                        "event_id": details.get('id'),
                        "title": details.get('title', '')
                    })
            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho UPDATE_EVENT: {e}")
        
        # Các lệnh xử lý khác
        for cmd_type in ["ADD_FAMILY_MEMBER", "UPDATE_PREFERENCE", "DELETE_EVENT", "ADD_NOTE"]:
            cmd_pattern = f"##{cmd_type}:"
            if cmd_pattern in response:
                logger.info(f"Tìm thấy lệnh {cmd_type}")
                try:
                    cmd_start = response.index(cmd_pattern) + len(cmd_pattern)
                    cmd_end = response.index("##", cmd_start)
                    cmd = response[cmd_start:cmd_end].strip()
                    
                    if cmd_type == "DELETE_EVENT":
                        event_id = cmd.strip()
                        success = delete_event(event_id)
                        results.append({
                            "action": "delete_event",
                            "success": success,
                            "event_id": event_id
                        })
                    else:
                        details = json.loads(cmd)
                        if isinstance(details, dict):
                            if cmd_type == "ADD_FAMILY_MEMBER":
                                member_id = add_family_member(details)
                                results.append({
                                    "action": "add_family_member",
                                    "success": True,
                                    "member_id": member_id,
                                    "name": details.get('name', '')
                                })
                            elif cmd_type == "UPDATE_PREFERENCE":
                                success = update_preference(details)
                                results.append({
                                    "action": "update_preference",
                                    "success": success,
                                    "member_id": details.get('id'),
                                    "preference": f"{details.get('key')}: {details.get('value')}"
                                })
                            elif cmd_type == "ADD_NOTE":
                                # Thêm thông tin về người tạo ghi chú
                                if current_member:
                                    details['created_by'] = current_member
                                note_id = add_note(details)
                                results.append({
                                    "action": "add_note",
                                    "success": True,
                                    "note_id": note_id,
                                    "title": details.get('title', '')
                                })
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý lệnh {cmd_type}: {e}")
                    results.append({
                        "action": cmd_type.lower(),
                        "success": False,
                        "error": str(e)
                    })
        
        return results
    
    except Exception as e:
        logger.error(f"Lỗi khi xử lý phản hồi của trợ lý: {e}")
        return [{"action": "error", "success": False, "error": str(e)}]

# Tạo phản hồi từ LLM
async def generate_llm_response(api_key, tavily_api_key, messages, current_member=None):
    """Hàm tạo phản hồi từ OpenAI API với xử lý ý định tìm kiếm"""
    
    # Lấy tin nhắn người dùng mới nhất
    last_user_message = ""
    last_message = None
    for message in reversed(messages):
        if message["role"] == "user":
            for content in message["content"]:
                if content["type"] == "text":
                    last_user_message = content["text"]
                    last_message = message
                    break
            if last_user_message:
                break
    
    # System prompt cho trợ lý
    system_prompt = f"""
    Bạn là trợ lý gia đình thông minh. Nhiệm vụ của bạn là giúp quản lý thông tin về các thành viên trong gia đình, 
    sở thích của họ, các sự kiện, ghi chú, và phân tích hình ảnh liên quan đến gia đình. Khi người dùng yêu cầu, bạn phải thực hiện ngay các hành động sau:
    
    1. Thêm thông tin về thành viên gia đình (tên, tuổi, sở thích)
    2. Cập nhật sở thích của thành viên gia đình
    3. Thêm, cập nhật, hoặc xóa sự kiện
    4. Thêm ghi chú
    5. Phân tích hình ảnh người dùng đưa ra (món ăn, hoạt động gia đình, v.v.)
    6. Tìm kiếm thông tin thực tế khi được hỏi về tin tức, thời tiết, thể thao, và sự kiện hiện tại
    
    QUAN TRỌNG: Khi cần thực hiện các hành động trên, bạn PHẢI sử dụng đúng cú pháp lệnh đặc biệt này (người dùng sẽ không nhìn thấy):
    
    - Thêm thành viên: ##ADD_FAMILY_MEMBER:{{"name":"Tên","age":"Tuổi","preferences":{{"food":"Món ăn","hobby":"Sở thích","color":"Màu sắc"}}}}##
    - Cập nhật sở thích: ##UPDATE_PREFERENCE:{{"id":"id_thành_viên","key":"loại_sở_thích","value":"giá_trị"}}##
    - Thêm sự kiện: ##ADD_EVENT:{{"title":"Tiêu đề","date":"YYYY-MM-DD","time":"HH:MM","description":"Mô tả","participants":["Tên1","Tên2"]}}##
    - Cập nhật sự kiện: ##UPDATE_EVENT:{{"id":"id_sự_kiện","title":"Tiêu đề mới","date":"YYYY-MM-DD","time":"HH:MM","description":"Mô tả mới","participants":["Tên1","Tên2"]}}##
    - Xóa sự kiện: ##DELETE_EVENT:id_sự_kiện##
    - Thêm ghi chú: ##ADD_NOTE:{{"title":"Tiêu đề","content":"Nội dung","tags":["tag1","tag2"]}}##
    
    QUY TẮC THÊM SỰ KIỆN ĐƠN GIẢN:
    1. Khi được yêu cầu thêm sự kiện, hãy thực hiện NGAY LẬP TỨC mà không cần hỏi thêm thông tin không cần thiết.
    2. Khi người dùng nói "ngày mai" hoặc "tuần sau", hãy tự động tính toán ngày trong cú pháp YYYY-MM-DD.
    3. Nếu không có thời gian cụ thể, sử dụng thời gian mặc định là 8:00.
    4. Sử dụng mô tả ngắn gọn từ yêu cầu của người dùng.
    5. Chỉ hỏi thông tin nếu thực sự cần thiết, tránh nhiều bước xác nhận.
    
    TÌM KIẾM THÔNG TIN THỜI GIAN THỰC:
    1. Khi người dùng hỏi về tin tức, thời tiết, thể thao, sự kiện hiện tại, thông tin sản phẩm mới, hoặc bất kỳ dữ liệu cập nhật nào, hệ thống đã tự động tìm kiếm thông tin thực tế cho bạn.
    2. Hãy sử dụng thông tin tìm kiếm này để trả lời người dùng một cách chính xác và đầy đủ.
    3. Luôn đề cập đến nguồn thông tin khi sử dụng kết quả tìm kiếm.
    4. Nếu không có thông tin tìm kiếm, hãy trả lời dựa trên kiến thức của bạn và lưu ý rằng thông tin có thể không cập nhật.
    
    Hôm nay là {datetime.now().strftime("%d/%m/%Y")}.
    
    CẤU TRÚC JSON PHẢI CHÍNH XÁC như trên. Đảm bảo dùng dấu ngoặc kép cho cả keys và values. Đảm bảo các dấu ngoặc nhọn và vuông được đóng đúng cách.
    
    QUAN TRỌNG: Khi người dùng yêu cầu tạo sự kiện mới, hãy luôn sử dụng lệnh ##ADD_EVENT:...## trong phản hồi của bạn mà không cần quá nhiều bước xác nhận.
    
    Đối với hình ảnh:
    - Nếu người dùng gửi hình ảnh món ăn, hãy mô tả món ăn, và đề xuất cách nấu hoặc thông tin dinh dưỡng nếu phù hợp
    - Nếu là hình ảnh hoạt động gia đình, hãy mô tả hoạt động và đề xuất cách ghi nhớ khoảnh khắc đó
    - Với bất kỳ hình ảnh nào, hãy giúp người dùng liên kết nó với thành viên gia đình hoặc sự kiện nếu phù hợp
    """
    
    # Thêm thông tin về người dùng hiện tại
    if current_member and current_member in family_data:
        member = family_data[current_member]
        system_prompt += f"""
        THÔNG TIN NGƯỜI DÙNG HIỆN TẠI:
        Bạn đang trò chuyện với: {member.get('name')}
        Tuổi: {member.get('age', '')}
        Sở thích: {json.dumps(member.get('preferences', {}), ensure_ascii=False)}
        
        QUAN TRỌNG: Hãy điều chỉnh cách giao tiếp và đề xuất phù hợp với người dùng này. Các sự kiện và ghi chú sẽ được ghi danh nghĩa người này tạo.
        """
    
    # Thêm thông tin dữ liệu
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
    
    # Phát hiện ý định tìm kiếm
    need_search = False
    search_query = ""
    search_result = ""
    
    if last_user_message and tavily_api_key:
        need_search, search_query = detect_search_intent(last_user_message, api_key)
        
        if need_search:
            logger.info(f"Tìm kiếm thông tin về: '{search_query}'...")
            search_result = search_and_summarize(tavily_api_key, search_query, api_key)
            
            # Thêm kết quả tìm kiếm vào hệ thống prompt
            search_info = f"""
            THÔNG TIN TÌM KIẾM:
            Câu hỏi: {search_query}
            
            Kết quả:
            {search_result}
            
            Hãy sử dụng thông tin này để trả lời câu hỏi của người dùng. Đảm bảo đề cập đến nguồn thông tin.
            """
            
            system_prompt += "\n\n" + search_info
    
    # Tạo tin nhắn API với system prompt
    api_messages = [{"role": "system", "content": system_prompt}]
    
    # Thêm tất cả tin nhắn trước đó vào cuộc trò chuyện
    for message in messages:
        # Xử lý các tin nhắn trước chuyển đổi sang định dạng API
        api_message = {"role": message["role"]}
        
        contents = []
        for content_item in message["content"]:
            if content_item["type"] == "text":
                contents.append({"type": "text", "text": content_item["text"]})
            elif content_item["type"] == "image_url":
                contents.append({
                    "type": "image_url",
                    "image_url": {"url": content_item["image_url"]["url"]}
                })
        
        api_message["content"] = contents
        api_messages.append(api_message)
    
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=api_messages,
            temperature=0.7,
            max_tokens=2048
        )
        
        response_text = response.choices[0].message.content
        
        # Xử lý phản hồi để trích xuất lệnh
        actions = process_assistant_response(response_text, current_member)
        
        return {
            "response": response_text,
            "actions": actions,
            "search_performed": need_search,
            "search_query": search_query if need_search else None
        }
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo phản hồi từ OpenAI: {e}")
        error_message = f"Có lỗi xảy ra: {str(e)}"
        return {
            "response": error_message,
            "actions": [],
            "search_performed": False,
            "search_query": None
        }

# --------- API Endpoints ---------

@app.on_event("startup")
async def startup_event():
    """Khởi chạy khi ứng dụng bắt đầu"""
    # Tải dữ liệu phiên
    load_session_data()

@app.get("/")
async def read_root():
    """Endpoint kiểm tra API có hoạt động không"""
    return {"status": "API Trợ lý Gia đình đang hoạt động"}

@app.post("/session/create")
async def create_session():
    """Tạo phiên mới"""
    session_id = get_or_create_session()
    return {"session_id": session_id}

@app.post("/session/set_member")
async def set_session_member(session_id: str, member_id: Optional[str] = None):
    """Thiết lập thành viên cho phiên"""
    if session_id not in session_states:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên")
    
    session_states[session_id].current_member = member_id
    session_states[session_id].messages = []  # Reset tin nhắn khi thay đổi người dùng
    save_session_data()
    
    return {"session_id": session_id, "current_member": member_id}

@app.post("/chat/text")
async def chat_text(text_request: TextRequest, api_keys: ApiKeys):
    """Endpoint xử lý tin nhắn văn bản"""
    session_id = text_request.session_id
    text = text_request.text
    current_member = text_request.current_member
    
    # Kiểm tra và tạo phiên nếu cần
    session_id = get_or_create_session(session_id)
    session = session_states[session_id]
    
    # Cập nhật member nếu có
    if current_member:
        session.current_member = current_member
    
    # Thêm tin nhắn người dùng vào lịch sử
    session.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": text}]
    })
    
    # Tạo phản hồi từ LLM
    response_data = await generate_llm_response(
        api_keys.openai_api_key,
        api_keys.tavily_api_key,
        session.messages,
        session.current_member
    )
    
    # Thêm phản hồi trợ lý vào lịch sử
    session.messages.append({
        "role": "assistant",
        "content": [{"type": "text", "text": response_data["response"]}]
    })
    
    # Lưu lịch sử nếu có người dùng hiện tại
    if session.current_member:
        summary = generate_chat_summary(session.messages, api_keys.openai_api_key)
        save_chat_history(session.current_member, session.messages, summary)
    
    # Lưu phiên
    save_session_data()
    
    # Tạo câu hỏi gợi ý mới nếu cần
    suggested_questions = generate_dynamic_suggested_questions(
        api_key=api_keys.openai_api_key,
        member_id=session.current_member,
        max_questions=5
    )
    
    return {
        "session_id": session_id,
        "response": response_data["response"],
        "actions": response_data["actions"],
        "search_performed": response_data["search_performed"],
        "search_query": response_data["search_query"],
        "suggested_questions": suggested_questions
    }

@app.post("/chat/image")
async def chat_image(
    session_id: str = Form(...),
    current_member: Optional[str] = Form(None),
    caption: Optional[str] = Form(None),
    file: UploadFile = File(...),
    openai_api_key: str = Form(...),
    tavily_api_key: Optional[str] = Form(None)
):
    """Endpoint xử lý tin nhắn hình ảnh"""
    # Đọc và xử lý hình ảnh
    try:
        image_content = await file.read()
        img = Image.open(BytesIO(image_content))
        img_base64 = get_image_base64(img)
        img_type = file.content_type or "image/jpeg"
        
        # Kiểm tra và tạo phiên nếu cần
        session_id = get_or_create_session(session_id)
        session = session_states[session_id]
        
        # Cập nhật member nếu có
        if current_member:
            session.current_member = current_member
        
        # Thêm tin nhắn hình ảnh vào lịch sử
        message_content = [{
            "type": "image_url",
            "image_url": {"url": f"data:{img_type};base64,{img_base64}"}
        }]
        
        # Thêm caption nếu có
        if caption:
            message_content.append({"type": "text", "text": caption})
        
        session.messages.append({
            "role": "user",
            "content": message_content
        })
        
        # Tạo phản hồi từ LLM
        response_data = await generate_llm_response(
            openai_api_key,
            tavily_api_key,
            session.messages,
            session.current_member
        )
        
        # Thêm phản hồi trợ lý vào lịch sử
        session.messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": response_data["response"]}]
        })
        
        # Lưu lịch sử nếu có người dùng hiện tại
        if session.current_member:
            summary = generate_chat_summary(session.messages, openai_api_key)
            save_chat_history(session.current_member, session.messages, summary)
        
        # Lưu phiên
        save_session_data()
        
        # Tạo câu hỏi gợi ý mới
        suggested_questions = generate_dynamic_suggested_questions(
            api_key=openai_api_key,
            member_id=session.current_member,
            max_questions=5
        )
        
        return {
            "session_id": session_id,
            "response": response_data["response"],
            "actions": response_data["actions"],
            "suggested_questions": suggested_questions
        }
    
    except Exception as e:
        logger.error(f"Lỗi khi xử lý hình ảnh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý hình ảnh: {str(e)}")

@app.post("/chat/audio")
async def chat_audio(
    session_id: str = Form(...),
    current_member: Optional[str] = Form(None),
    file: UploadFile = File(...),
    openai_api_key: str = Form(...),
    tavily_api_key: Optional[str] = Form(None)
):
    """Endpoint xử lý tin nhắn âm thanh"""
    try:
        # Đọc file âm thanh
        audio_content = await file.read()
        
        # Phiên âm với Whisper API
        client = OpenAI(api_key=openai_api_key)
        
        # Lưu tạm file để gửi đến API
        temp_audio_path = f"temp_audio_{session_id}.wav"
        with open(temp_audio_path, "wb") as temp_file:
            temp_file.write(audio_content)
        
        # Gọi API chuyển âm thanh thành văn bản
        with open(temp_audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        
        # Xóa file tạm
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        
        # Lấy nội dung văn bản từ âm thanh
        transcribed_text = transcript.text
        
        if not transcribed_text:
            return JSONResponse(
                status_code=400,
                content={"detail": "Không thể chuyển đổi âm thanh thành văn bản"}
            )
        
        # Kiểm tra và tạo phiên nếu cần
        session_id = get_or_create_session(session_id)
        session = session_states[session_id]
        
        # Cập nhật member nếu có
        if current_member:
            session.current_member = current_member
        
        # Thêm tin nhắn văn bản (từ âm thanh) vào lịch sử
        session.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": transcribed_text}]
        })
        
        # Tạo phản hồi từ LLM
        response_data = await generate_llm_response(
            openai_api_key,
            tavily_api_key,
            session.messages,
            session.current_member
        )
        
        # Thêm phản hồi trợ lý vào lịch sử
        session.messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": response_data["response"]}]
        })
        
        # Lưu lịch sử nếu có người dùng hiện tại
        if session.current_member:
            summary = generate_chat_summary(session.messages, openai_api_key)
            save_chat_history(session.current_member, session.messages, summary)
        
        # Lưu phiên
        save_session_data()
        
        # Tạo câu hỏi gợi ý mới
        suggested_questions = generate_dynamic_suggested_questions(
            api_key=openai_api_key,
            member_id=session.current_member,
            max_questions=5
        )
        
        return {
            "session_id": session_id,
            "transcribed_text": transcribed_text,
            "response": response_data["response"],
            "actions": response_data["actions"],
            "suggested_questions": suggested_questions
        }
    
    except Exception as e:
        logger.error(f"Lỗi khi xử lý âm thanh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý âm thanh: {str(e)}")

@app.get("/family/members")
async def get_family_members():
    """Lấy danh sách thành viên gia đình"""
    return family_data

@app.post("/family/members")
async def create_family_member(member: FamilyMember):
    """Tạo thành viên gia đình mới"""
    member_dict = member.dict()
    member_id = add_family_member(member_dict)
    return {"member_id": member_id, "member": member_dict}

@app.get("/events")
async def get_events(member_id: Optional[str] = None):
    """Lấy danh sách sự kiện, có thể lọc theo thành viên"""
    if member_id:
        return filter_events_by_member(member_id)
    return events_data

@app.post("/events")
async def create_event(event: Event, current_member: Optional[str] = None):
    """Tạo sự kiện mới"""
    event_dict = event.dict()
    if current_member:
        event_dict["created_by"] = current_member
    event_id = add_event(event_dict)
    return {"event_id": event_id, "event": event_dict}

@app.get("/notes")
async def get_notes(member_id: Optional[str] = None):
    """Lấy danh sách ghi chú, có thể lọc theo người tạo"""
    if member_id:
        return {note_id: note for note_id, note in notes_data.items() 
                if note.get("created_by") == member_id}
    return notes_data

@app.post("/notes")
async def create_note(note: Note, current_member: Optional[str] = None):
    """Tạo ghi chú mới"""
    note_dict = note.dict()
    if current_member:
        note_dict["created_by"] = current_member
    note_id = add_note(note_dict)
    return {"note_id": note_id, "note": note_dict}

@app.post("/search")
async def search_info(
    query: str, 
    tavily_api_key: str,
    openai_api_key: str
):
    """Tìm kiếm thông tin thời gian thực"""
    if not tavily_api_key or not openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu API key")
    
    result = search_and_summarize(tavily_api_key, query, openai_api_key)
    return {"query": query, "result": result}

@app.get("/suggestions")
async def get_suggestions(
    member_id: Optional[str] = None,
    openai_api_key: str = None,
    count: int = 5
):
    """Lấy câu hỏi gợi ý"""
    if not openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu OpenAI API key")
    
    suggestions = generate_dynamic_suggested_questions(
        api_key=openai_api_key,
        member_id=member_id,
        max_questions=count
    )
    
    return {"suggestions": suggestions}

# Khởi chạy ứng dụng khi chạy trực tiếp (không qua import)
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)