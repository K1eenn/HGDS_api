from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Body, Query, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Union
from openai import OpenAI
import os
import json
import datetime
import base64
import logging
import random
import hashlib
import requests
import time
from io import BytesIO
from PIL import Image
import dotenv

# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập log để debug
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler()])
logger = logging.getLogger('family_assistant_api')

# Đường dẫn file lưu trữ dữ liệu
FAMILY_DATA_FILE = "family_data.json"
EVENTS_DATA_FILE = "events_data.json"
NOTES_DATA_FILE = "notes_data.json"
CHAT_HISTORY_FILE = "chat_history.json"

# Mô hình OpenAI mặc định
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Tạo ứng dụng FastAPI
app = FastAPI(
    title="Family Assistant API",
    description="API for Family Assistant with AI integration",
    version="1.0.0",
)

# Cho phép CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tải dữ liệu ban đầu
def load_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
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

# --- Models ---

class Preference(BaseModel):
    food: Optional[str] = None
    hobby: Optional[str] = None
    color: Optional[str] = None

class FamilyMember(BaseModel):
    name: str
    age: Optional[str] = None
    preferences: Optional[Preference] = None

class FamilyMemberResponse(FamilyMember):
    id: str
    added_on: str

class PreferenceUpdate(BaseModel):
    id: str
    key: str
    value: str

class Event(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    description: Optional[str] = None
    participants: Optional[List[str]] = []

class EventResponse(Event):
    id: str
    created_by: Optional[str] = None
    created_on: str

class EventUpdate(BaseModel):
    id: str
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    description: Optional[str] = None
    participants: Optional[List[str]] = None

class Note(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = []

class NoteResponse(Note):
    id: str
    created_by: Optional[str] = None
    created_on: str

class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ChatRequest(BaseModel):
    messages: List[Message]
    member_id: Optional[str] = None

class SearchRequest(BaseModel):
    query: str

class APIKeyRequest(BaseModel):
    openai_api_key: str
    tavily_api_key: Optional[str] = None

class SuggestedQuestionsRequest(BaseModel):
    member_id: Optional[str] = None
    max_questions: Optional[int] = 5

# --- Helper Functions ---

def get_date_from_relative_term(term):
    """Chuyển đổi từ mô tả tương đối về ngày thành ngày thực tế"""
    today = datetime.datetime.now().date()
    
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
        return today + datetime.timedelta(days=30)
    
    return None

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

def process_assistant_response(response, current_member=None):
    """Hàm xử lý lệnh từ phản hồi của trợ lý"""
    actions_performed = []
    
    try:
        logger.info(f"Xử lý phản hồi của trợ lý, độ dài: {len(response)}")
        
        # Xử lý lệnh thêm sự kiện
        if "##ADD_EVENT:" in response:
            logger.info("Tìm thấy lệnh ADD_EVENT")
            cmd_start = response.index("##ADD_EVENT:") + len("##ADD_EVENT:")
            cmd_end = response.index("##", cmd_start)
            cmd = response[cmd_start:cmd_end].strip()
            
            logger.info(f"Nội dung lệnh ADD_EVENT: {cmd}")
            
            try:
                details = json.loads(cmd)
                if isinstance(details, dict):
                    # Xử lý các từ ngữ tương đối về thời gian
                    logger.info(f"Đang xử lý ngày: {details.get('date', '')}")
                    if details.get('date') and not details['date'][0].isdigit():
                        # Nếu ngày không bắt đầu bằng số, có thể là mô tả tương đối
                        relative_date = get_date_from_relative_term(details['date'].lower())
                        if relative_date:
                            details['date'] = relative_date.strftime("%Y-%m-%d")
                            logger.info(f"Đã chuyển đổi ngày thành: {details['date']}")
                    
                    # Thêm thông tin về người tạo sự kiện
                    if current_member:
                        details['created_by'] = current_member
                    
                    logger.info(f"Thêm sự kiện: {details.get('title', 'Không tiêu đề')}")
                    event_id = add_event(details)
                    if event_id:
                        actions_performed.append({
                            "action": "add_event",
                            "success": True,
                            "event_id": event_id,
                            "title": details.get('title', '')
                        })
            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho ADD_EVENT: {e}")
                logger.error(f"Chuỗi JSON gốc: {cmd}")
        
        # Xử lý lệnh UPDATE_EVENT
        if "##UPDATE_EVENT:" in response:
            logger.info("Tìm thấy lệnh UPDATE_EVENT")
            cmd_start = response.index("##UPDATE_EVENT:") + len("##UPDATE_EVENT:")
            cmd_end = response.index("##", cmd_start)
            cmd = response[cmd_start:cmd_end].strip()
            
            logger.info(f"Nội dung lệnh UPDATE_EVENT: {cmd}")
            
            try:
                details = json.loads(cmd)
                if isinstance(details, dict):
                    # Xử lý các từ ngữ tương đối về thời gian
                    if details.get('date') and not details['date'][0].isdigit():
                        # Nếu ngày không bắt đầu bằng số, có thể là mô tả tương đối
                        relative_date = get_date_from_relative_term(details['date'].lower())
                        if relative_date:
                            details['date'] = relative_date.strftime("%Y-%m-%d")
                    
                    logger.info(f"Cập nhật sự kiện: {details.get('title', 'Không tiêu đề')}")
                    success = update_event(details)
                    if success:
                        actions_performed.append({
                            "action": "update_event",
                            "success": True,
                            "event_id": details.get('id', ''),
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
                        if success:
                            actions_performed.append({
                                "action": "delete_event",
                                "success": True,
                                "event_id": event_id
                            })
                    else:
                        details = json.loads(cmd)
                        if isinstance(details, dict):
                            if cmd_type == "ADD_FAMILY_MEMBER":
                                member_id = add_family_member(details)
                                actions_performed.append({
                                    "action": "add_family_member",
                                    "success": True,
                                    "member_id": member_id,
                                    "name": details.get('name', '')
                                })
                            elif cmd_type == "UPDATE_PREFERENCE":
                                success = update_preference(details)
                                if success:
                                    actions_performed.append({
                                        "action": "update_preference",
                                        "success": True,
                                        "member_id": details.get('id', ''),
                                        "key": details.get('key', ''),
                                        "value": details.get('value', '')
                                    })
                            elif cmd_type == "ADD_NOTE":
                                # Thêm thông tin về người tạo ghi chú
                                if current_member:
                                    details['created_by'] = current_member
                                note_id = add_note(details)
                                if note_id:
                                    actions_performed.append({
                                        "action": "add_note",
                                        "success": True,
                                        "note_id": note_id,
                                        "title": details.get('title', '')
                                    })
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý lệnh {cmd_type}: {e}")
                    actions_performed.append({
                        "action": cmd_type.lower(),
                        "success": False,
                        "error": str(e)
                    })
    
    except Exception as e:
        logger.error(f"Lỗi khi xử lý phản hồi của trợ lý: {e}")
        logger.error(f"Phản hồi gốc: {response[:100]}...")
        actions_performed.append({
            "action": "process_response",
            "success": False,
            "error": str(e)
        })
    
    return actions_performed

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
            model=DEFAULT_OPENAI_MODEL,
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

def save_chat_history(member_id, messages, summary=None):
    """Lưu lịch sử chat cho một thành viên cụ thể"""
    if member_id not in chat_history:
        chat_history[member_id] = []
    
    # Tạo bản ghi mới
    history_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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

def tavily_extract(api_key, urls, include_images=False, extract_depth="basic"):
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

def tavily_search(api_key, query, search_depth="advanced", max_results=3, include_domains=None, exclude_domains=None):
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
            model=DEFAULT_OPENAI_MODEL,
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

def detect_search_intent(query, api_key):
    """Phát hiện xem câu hỏi có cần tìm kiếm thông tin thực tế hay không"""
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": """
                    Bạn là một hệ thống phân loại câu hỏi thông minh. Nhiệm vụ của bạn là xác định xem câu hỏi có cần tìm kiếm thông tin thực tế, tin tức mới hoặc dữ liệu cập nhật không.
                    
                    Câu hỏi cần search khi:
                    1. Liên quan đến tin tức, sự kiện hiện tại hoặc gần đây
                    2. Yêu cầu dữ liệu thực tế, số liệu thống kê cập nhật
                    3. Hỏi về kết quả thể thao, giải đấu
                    4. Cần thông tin về giá cả, sản phẩm mới
                    5. Liên quan đến thời tiết, tình hình giao thông hiện tại
                    
                    Câu hỏi KHÔNG cần search khi:
                    1. Liên quan đến quản lý gia đình (thêm thành viên, sự kiện, ghi chú)
                    2. Hỏi ý kiến, lời khuyên cá nhân
                    3. Yêu cầu công thức nấu ăn phổ biến
                    4. Câu hỏi đơn giản về kiến thức phổ thông
                    5. Yêu cầu hỗ trợ sử dụng ứng dụng
                """},
                {"role": "user", "content": f"Câu hỏi: {query}\n\nCâu hỏi này có cần tìm kiếm thông tin thực tế không? Trả lời JSON với 2 trường: need_search (true/false) và search_query (câu truy vấn tìm kiếm tối ưu nếu cần search)."}
            ],
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        return result.get("need_search", False), result.get("search_query", query)
    
    except Exception as e:
        logger.error(f"Lỗi khi phát hiện ý định tìm kiếm: {e}")
        return False, query

def generate_dynamic_suggested_questions(api_key, member_id=None, max_questions=5):
    """Tạo câu hỏi gợi ý cá nhân hóa và linh động"""
    if not api_key:
        # Trả về câu hỏi mặc định nếu không có API key
        return [
            "Thêm sự kiện gia đình?",
            "Thêm thành viên gia đình mới?",
            "Tạo ghi chú mới?",
            "Lịch sự kiện sắp tới?",
            "Tin tức mới nhất?"
        ]
    
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
            model=DEFAULT_OPENAI_MODEL,
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
        
        if not questions or len(questions) < max_questions:
            # Bổ sung câu hỏi mặc định nếu không đủ
            default_questions = [
                "Thêm sự kiện gia đình?",
                "Thêm thành viên gia đình mới?",
                "Tạo ghi chú mới?",
                "Lịch sự kiện sắp tới?",
                "Tin tức mới nhất?"
            ]
            
            # Thêm câu hỏi mặc định cho đến khi đủ số lượng
            while len(questions) < max_questions:
                for q in default_questions:
                    if q not in questions:
                        questions.append(q)
                        break
                    if len(questions) >= max_questions:
                        break
        
        logger.info(f"Đã tạo {len(questions)} câu hỏi gợi ý bằng OpenAI API")
        return questions
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo câu hỏi với OpenAI: {e}")
        # Trả về câu hỏi mặc định khi có lỗi
        return [
            "Thêm sự kiện gia đình?",
            "Thêm thành viên gia đình mới?",
            "Tạo ghi chú mới?",
            "Lịch sự kiện sắp tới?",
            "Tin tức mới nhất?"
        ]

# --- Chức năng thêm/sửa/xóa dữ liệu ---

def add_family_member(details):
    """Thêm thành viên gia đình mới"""
    member_id = str(len(family_data) + 1)
    
    family_data[member_id] = {
        "name": details.get("name", ""),
        "age": details.get("age", ""),
        "preferences": details.get("preferences", {}),
        "added_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    save_data(FAMILY_DATA_FILE, family_data)
    
    return member_id

def update_preference(details):
    """Cập nhật sở thích cho thành viên gia đình"""
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
    """Thêm một sự kiện mới"""
    try:
        event_id = str(len(events_data) + 1)
        events_data[event_id] = {
            "title": details.get("title", ""),
            "date": details.get("date", ""),
            "time": details.get("time", ""),
            "description": details.get("description", ""),
            "participants": details.get("participants", []),
            "created_by": details.get("created_by", ""),
            "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_data(EVENTS_DATA_FILE, events_data)
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
                events_data[event_id]["created_on"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            save_data(EVENTS_DATA_FILE, events_data)
            return True
        return False
    except Exception as e:
        logger.error(f"Lỗi khi cập nhật sự kiện: {e}")
        return False

def delete_event(event_id):
    """Xóa một sự kiện"""
    if event_id in events_data:
        del events_data[event_id]
        save_data(EVENTS_DATA_FILE, events_data)
        return True
    return False

def add_note(details):
    """Thêm một ghi chú mới"""
    note_id = str(len(notes_data) + 1)
    notes_data[note_id] = {
        "title": details.get("title", ""),
        "content": details.get("content", ""),
        "tags": details.get("tags", []),
        "created_by": details.get("created_by", ""),
        "created_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_data(NOTES_DATA_FILE, notes_data)
    return note_id

def delete_note(note_id):
    """Xóa một ghi chú"""
    if note_id in notes_data:
        del notes_data[note_id]
        save_data(NOTES_DATA_FILE, notes_data)
        return True
    return False

# --- API Endpoints ---

@app.get("/")
async def root():
    return {"status": "ok", "message": "Family Assistant API is running"}

# API cho thành viên gia đình
@app.get("/family/members", response_model=Dict[str, FamilyMemberResponse])
async def get_family_members():
    """Lấy danh sách tất cả thành viên gia đình"""
    result = {}
    for member_id, member in family_data.items():
        if isinstance(member, dict):
            result[member_id] = {**member, "id": member_id}
    return result

@app.get("/family/members/{member_id}", response_model=FamilyMemberResponse)
async def get_family_member(member_id: str):
    """Lấy thông tin một thành viên gia đình cụ thể"""
    if member_id not in family_data:
        raise HTTPException(status_code=404, detail="Thành viên không tồn tại")
    
    member = family_data[member_id]
    return {**member, "id": member_id}

@app.post("/family/members", response_model=FamilyMemberResponse)
async def create_family_member(member: FamilyMember):
    """Thêm thành viên gia đình mới"""
    member_dict = member.dict()
    member_id = add_family_member(member_dict)
    
    return {**member_dict, "id": member_id, "added_on": family_data[member_id]["added_on"]}

@app.put("/family/members/{member_id}", response_model=FamilyMemberResponse)
async def update_family_member(member_id: str, member: FamilyMember):
    """Cập nhật thông tin thành viên gia đình"""
    if member_id not in family_data:
        raise HTTPException(status_code=404, detail="Thành viên không tồn tại")
    
    # Giữ lại thông tin added_on
    added_on = family_data[member_id].get("added_on", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Cập nhật thông tin
    member_dict = member.dict()
    family_data[member_id] = {
        **member_dict,
        "added_on": added_on
    }
    
    save_data(FAMILY_DATA_FILE, family_data)
    
    return {**member_dict, "id": member_id, "added_on": added_on}

@app.put("/family/preferences", response_model=Dict[str, Any])
async def update_member_preference(preference: PreferenceUpdate):
    """Cập nhật sở thích cho thành viên gia đình"""
    if preference.id not in family_data:
        raise HTTPException(status_code=404, detail="Thành viên không tồn tại")
    
    success = update_preference(preference.dict())
    
    if not success:
        raise HTTPException(status_code=400, detail="Không thể cập nhật sở thích")
    
    return {"status": "success", "member_id": preference.id, "preference": {preference.key: preference.value}}

# API cho sự kiện
@app.get("/events", response_model=Dict[str, EventResponse])
async def get_all_events(member_id: Optional[str] = None):
    """Lấy danh sách sự kiện, có thể lọc theo thành viên"""
    # Lọc theo thành viên nếu có
    if member_id:
        filtered_events = filter_events_by_member(member_id)
    else:
        filtered_events = events_data
    
    # Chuyển đổi định dạng
    result = {}
    for event_id, event in filtered_events.items():
        result[event_id] = {**event, "id": event_id}
    
    return result

@app.get("/events/{event_id}", response_model=EventResponse)
async def get_event(event_id: str):
    """Lấy thông tin một sự kiện cụ thể"""
    if event_id not in events_data:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    
    event = events_data[event_id]
    return {**event, "id": event_id}

@app.post("/events", response_model=EventResponse)
async def create_event(event: Event, member_id: Optional[str] = None):
    """Tạo một sự kiện mới"""
    event_dict = event.dict()
    
    # Thêm thông tin người tạo nếu có
    if member_id:
        event_dict["created_by"] = member_id
    
    # Xử lý các mô tả ngày tương đối
    if event_dict.get("date") and not event_dict["date"][0].isdigit():
        relative_date = get_date_from_relative_term(event_dict["date"].lower())
        if relative_date:
            event_dict["date"] = relative_date.strftime("%Y-%m-%d")
    
    event_id = add_event(event_dict)
    
    if not event_id:
        raise HTTPException(status_code=400, detail="Không thể tạo sự kiện")
    
    return {**events_data[event_id], "id": event_id}

@app.put("/events/{event_id}", response_model=EventResponse)
async def update_event_endpoint(event_id: str, event: EventUpdate):
    """Cập nhật thông tin sự kiện"""
    if event_id not in events_data:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    
    event_dict = event.dict(exclude_unset=True)
    event_dict["id"] = event_id
    
    # Xử lý các mô tả ngày tương đối
    if event_dict.get("date") and not event_dict["date"][0].isdigit():
        relative_date = get_date_from_relative_term(event_dict["date"].lower())
        if relative_date:
            event_dict["date"] = relative_date.strftime("%Y-%m-%d")
    
    success = update_event(event_dict)
    
    if not success:
        raise HTTPException(status_code=400, detail="Không thể cập nhật sự kiện")
    
    return {**events_data[event_id], "id": event_id}

@app.delete("/events/{event_id}", response_model=Dict[str, Any])
async def delete_event_endpoint(event_id: str):
    """Xóa một sự kiện"""
    if event_id not in events_data:
        raise HTTPException(status_code=404, detail="Sự kiện không tồn tại")
    
    success = delete_event(event_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Không thể xóa sự kiện")
    
    return {"status": "success", "event_id": event_id}

# API cho ghi chú
@app.get("/notes", response_model=Dict[str, NoteResponse])
async def get_all_notes(member_id: Optional[str] = None):
    """Lấy danh sách ghi chú, có thể lọc theo thành viên"""
    # Lọc theo thành viên nếu có
    if member_id:
        filtered_notes = {note_id: note for note_id, note in notes_data.items() 
                         if note.get("created_by") == member_id}
    else:
        filtered_notes = notes_data
    
    # Chuyển đổi định dạng
    result = {}
    for note_id, note in filtered_notes.items():
        result[note_id] = {**note, "id": note_id}
    
    return result

@app.get("/notes/{note_id}", response_model=NoteResponse)
async def get_note(note_id: str):
    """Lấy thông tin một ghi chú cụ thể"""
    if note_id not in notes_data:
        raise HTTPException(status_code=404, detail="Ghi chú không tồn tại")
    
    note = notes_data[note_id]
    return {**note, "id": note_id}

@app.post("/notes", response_model=NoteResponse)
async def create_note(note: Note, member_id: Optional[str] = None):
    """Tạo một ghi chú mới"""
    note_dict = note.dict()
    
    # Thêm thông tin người tạo nếu có
    if member_id:
        note_dict["created_by"] = member_id
    
    note_id = add_note(note_dict)
    
    return {**notes_data[note_id], "id": note_id}

@app.delete("/notes/{note_id}", response_model=Dict[str, Any])
async def delete_note_endpoint(note_id: str):
    """Xóa một ghi chú"""
    if note_id not in notes_data:
        raise HTTPException(status_code=404, detail="Ghi chú không tồn tại")
    
    success = delete_note(note_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Không thể xóa ghi chú")
    
    return {"status": "success", "note_id": note_id}

# API cho lịch sử trò chuyện
@app.get("/chat/history/{member_id}", response_model=List[Dict[str, Any]])
async def get_chat_history(member_id: str):
    """Lấy lịch sử trò chuyện của một thành viên"""
    if member_id not in chat_history:
        return []
    
    return chat_history[member_id]

@app.delete("/chat/history/{member_id}", response_model=Dict[str, Any])
async def clear_chat_history(member_id: str):
    """Xóa lịch sử trò chuyện của một thành viên"""
    if member_id in chat_history:
        chat_history[member_id] = []
        save_data(CHAT_HISTORY_FILE, chat_history)
    
    return {"status": "success", "member_id": member_id}

# API cho tìm kiếm và trợ lý AI
@app.post("/ai/chat", response_model=Dict[str, Any])
async def chat_with_assistant(request: ChatRequest, keys: APIKeyRequest):
    """Trò chuyện với trợ lý AI"""
    if not keys.openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu OpenAI API Key")
    
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
    3. Nếu không có thời gian cụ thể, sử dụng thời gian mặc định là 19:00.
    4. Sử dụng mô tả ngắn gọn từ yêu cầu của người dùng.
    5. Chỉ hỏi thông tin nếu thực sự cần thiết, tránh nhiều bước xác nhận.
    6. Sau khi thêm/cập nhật/xóa sự kiện, tóm tắt ngắn gọn hành động đã thực hiện.
    
    Hôm nay là {datetime.datetime.now().strftime("%d/%m/%Y")}.
    
    CẤU TRÚC JSON PHẢI CHÍNH XÁC như trên. Đảm bảo dùng dấu ngoặc kép cho cả keys và values. Đảm bảo các dấu ngoặc nhọn và vuông được đóng đúng cách.
    
    QUAN TRỌNG: Khi người dùng yêu cầu tạo sự kiện mới, hãy luôn sử dụng lệnh ##ADD_EVENT:...## trong phản hồi của bạn mà không cần quá nhiều bước xác nhận.
    """
    
    # Thêm thông tin về người dùng hiện tại
    if request.member_id and request.member_id in family_data:
        current_member = family_data[request.member_id]
        system_prompt += f"""
        THÔNG TIN NGƯỜI DÙNG HIỆN TẠI:
        Bạn đang trò chuyện với: {current_member.get('name')}
        Tuổi: {current_member.get('age', '')}
        Sở thích: {json.dumps(current_member.get('preferences', {}), ensure_ascii=False)}
        
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
    
    # Tạo tin nhắn với system prompt
    messages = [{"role": "system", "content": system_prompt}]
    
    # Thêm các tin nhắn từ yêu cầu
    for message in request.messages:
        messages.append({
            "role": message.role,
            "content": message.content
        })
    
    try:
        # Lấy tin nhắn người dùng mới nhất
        last_user_message = ""
        for message in reversed(request.messages):
            if message.role == "user":
                if isinstance(message.content, list):
                    for content in message.content:
                        if content.get("type") == "text":
                            last_user_message = content.get("text", "")
                            break
                else:
                    last_user_message = message.content
                break
        
        # Phát hiện ý định tìm kiếm
        need_search = False
        search_query = ""
        
        if last_user_message and keys.tavily_api_key:
            need_search, search_query = detect_search_intent(last_user_message, keys.openai_api_key)
            
            if need_search:
                search_result = search_and_summarize(keys.tavily_api_key, search_query, keys.openai_api_key)
                
                # Thêm kết quả tìm kiếm vào hệ thống prompt
                search_info = f"""
                THÔNG TIN TÌM KIẾM:
                Câu hỏi: {search_query}
                
                Kết quả:
                {search_result}
                
                Hãy sử dụng thông tin này để trả lời câu hỏi của người dùng. Đảm bảo đề cập đến nguồn thông tin.
                """
                
                messages[0]["content"] = system_prompt + "\n\n" + search_info
        
        # Gọi API OpenAI
        client = OpenAI(api_key=keys.openai_api_key)
        response = client.chat.completions.create(
            model=DEFAULT_OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2048
        )
        
        response_text = response.choices[0].message.content
        
        # Xử lý phản hồi để trích xuất lệnh
        actions = process_assistant_response(response_text, request.member_id)
        
        # Lưu lịch sử trò chuyện nếu có ID thành viên
        if request.member_id:
            updated_messages = request.messages + [Message(role="assistant", content=response_text)]
            summary = generate_chat_summary(updated_messages, keys.openai_api_key) if keys.openai_api_key else ""
            save_chat_history(request.member_id, updated_messages, summary)
        
        return {
            "response": response_text,
            "actions": actions,
            "search_performed": need_search,
            "search_query": search_query if need_search else None
        }
    
    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI API: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi gọi OpenAI API: {str(e)}")

@app.post("/ai/search", response_model=Dict[str, str])
async def search_info(request: SearchRequest, keys: APIKeyRequest):
    """Tìm kiếm thông tin thực tế"""
    if not keys.openai_api_key or not keys.tavily_api_key:
        raise HTTPException(status_code=400, detail="Thiếu API Keys")
    
    try:
        result = search_and_summarize(keys.tavily_api_key, request.query, keys.openai_api_key)
        return {"result": result}
    
    except Exception as e:
        logger.error(f"Lỗi khi tìm kiếm: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi tìm kiếm: {str(e)}")

@app.post("/ai/suggested-questions", response_model=List[str])
async def get_suggested_questions(request: SuggestedQuestionsRequest, keys: APIKeyRequest):
    """Lấy danh sách câu hỏi gợi ý"""
    if not keys.openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu OpenAI API Key")
    
    try:
        questions = generate_dynamic_suggested_questions(
            api_key=keys.openai_api_key,
            member_id=request.member_id,
            max_questions=request.max_questions
        )
        
        return questions
    
    except Exception as e:
        logger.error(f"Lỗi khi tạo câu hỏi gợi ý: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi tạo câu hỏi gợi ý: {str(e)}")

@app.post("/ai/speech-to-text")
async def transcribe_audio(audio_file: UploadFile = File(...), keys: APIKeyRequest = Depends()):
    """Chuyển đổi file âm thanh thành văn bản"""
    if not keys.openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu OpenAI API Key")
    
    try:
        # Đọc file âm thanh
        audio_content = await audio_file.read()
        
        # Gọi API OpenAI
        client = OpenAI(api_key=keys.openai_api_key)
        
        # Lưu tạm file
        temp_file_path = f"temp_audio_{int(time.time())}.wav"
        with open(temp_file_path, "wb") as f:
            f.write(audio_content)
        
        try:
            with open(temp_file_path, "rb") as f:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=f,
                )
            
            return {"text": transcript.text}
        
        finally:
            # Xóa file tạm
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
    
    except Exception as e:
        logger.error(f"Lỗi khi chuyển đổi âm thanh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi chuyển đổi âm thanh: {str(e)}")

@app.post("/ai/vision-analysis")
async def analyze_image(image: UploadFile = File(...), keys: APIKeyRequest = Depends()):
    """Phân tích hình ảnh"""
    if not keys.openai_api_key:
        raise HTTPException(status_code=400, detail="Thiếu OpenAI API Key")
    
    try:
        # Đọc nội dung hình ảnh
        image_content = await image.read()
        
        # Chuyển đổi hình ảnh sang base64
        base64_image = base64.b64encode(image_content).decode('utf-8')
        
        # Gọi API OpenAI
        client = OpenAI(api_key=keys.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Bạn là trợ lý phân tích hình ảnh thông minh. Hãy mô tả chi tiết nội dung hình ảnh người dùng gửi lên, bao gồm món ăn, hoạt động, con người, đồ vật, v.v. Đối với món ăn, hãy nhận diện và đưa thêm thông tin về công thức, thành phần, dinh dưỡng nếu có thể."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hãy phân tích hình ảnh này:"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image.content_type};base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.7,
            max_tokens=1000
        )
        
        return {"analysis": response.choices[0].message.content}
    
    except Exception as e:
        logger.error(f"Lỗi khi phân tích hình ảnh: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi phân tích hình ảnh: {str(e)}")

# Khởi động ứng dụng
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)