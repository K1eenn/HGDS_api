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

# Tải biến môi trường
dotenv.load_dotenv()

# Thiết lập log
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[logging.StreamHandler()])
logger = logging.getLogger('family_assistant_api')

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

# Đường dẫn file lưu trữ dữ liệu
FAMILY_DATA_FILE = "family_data.json"
EVENTS_DATA_FILE = "events_data.json"
NOTES_DATA_FILE = "notes_data.json"
CHAT_HISTORY_FILE = "chat_history.json"

# Thư mục lưu trữ tạm thời
TEMP_DIR = "temp_files"
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
    """Quản lý session và trạng thái cho mỗi client"""
    
    def __init__(self):
        self.sessions = {}
        
    def get_session(self, session_id):
        """Lấy session hoặc tạo mới nếu chưa tồn tại"""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "current_member": None,
                "suggested_question": None,
                "process_suggested": False,
                "question_cache": {}
            }
        return self.sessions[session_id]
    
    def update_session(self, session_id, data):
        """Cập nhật dữ liệu session"""
        if session_id in self.sessions:
            self.sessions[session_id].update(data)
            return True
        return False
    
    def delete_session(self, session_id):
        """Xóa session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

# Khởi tạo session manager
session_manager = SessionManager()

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
    openai_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    messages: Optional[List[Message]] = None  # Optional để tương thích ngược

class ChatResponse(BaseModel):
    session_id: str
    messages: List[Message]
    # suggested_questions: Optional[List[str]] = None
    audio_response: Optional[str] = None
    response_format: Optional[str] = "html"  # Thêm trường chỉ định format

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
        session["messages"] = []
    
    # Nếu client cung cấp messages mới, cập nhật
    if chat_request.messages is not None:
        session["messages"] = [msg.dict() for msg in chat_request.messages]
    
    # Xử lý tin nhắn mới
    message_dict = chat_request.message.dict()
    
    # Xử lý âm thanh nếu có
    if message_dict["type"] == "audio" and message_dict.get("audio_data"):
        message_dict = process_audio(message_dict, openai_api_key)
    
    # Thêm tin nhắn vào danh sách messages
    session["messages"].append({
        "role": "user",
        "content": [message_dict]
    })
    
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
        
        # Xử lý lệnh đặc biệt trong phản hồi
        process_assistant_response(assistant_response, session["current_member"])
        
        # Thêm phản hồi vào danh sách tin nhắn
        session["messages"].append({
            "role": "assistant",
            "content": [{"type": "html", "html": assistant_response}]
        })
        
        # Lưu lịch sử chat nếu có current_member
        if session["current_member"]:
            summary = generate_chat_summary(session["messages"], openai_api_key)
            save_chat_history(session["current_member"], session["messages"], summary)
        
        # Chuyển đổi văn bản thành giọng nói
        audio_response = text_to_speech_google(assistant_response)
        
        # Trả về kết quả (không còn suggested_questions)
        return ChatResponse(
            session_id=chat_request.session_id,
            messages=session["messages"],
            audio_response=audio_response,
            response_format="html"
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
    
    # Xử lý âm thanh nếu có
    if message_dict["type"] == "audio" and message_dict.get("audio_data"):
        message_dict = process_audio(message_dict, openai_api_key)
    
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
                    yield json.dumps({"chunk": chunk_text, "type": "html"}) + "\n"
                    
                    # Đảm bảo chunk được gửi ngay lập tức
                    await asyncio.sleep(0)
            
            # Khi stream kết thúc, xử lý phản hồi đầy đủ
            process_assistant_response(full_response, session["current_member"])
            
            # Lưu phản hồi vào session
            session["messages"].append({
                "role": "assistant",
                "content": [{"type": "html", "html": full_response}]
            })
            
            # Lưu lịch sử chat
            if session["current_member"]:
                summary = generate_chat_summary(session["messages"], openai_api_key)
                save_chat_history(session["current_member"], session["messages"], summary)
            
            
            # Gửi câu hỏi gợi ý ở cuối
            yield json.dumps({
                "complete": True,
                # "suggested_questions": suggested_questions,
                "audio_response": text_to_speech_google(full_response)
            }) + "\n"
            
        except Exception as e:
            logger.error(f"Lỗi trong quá trình stream: {str(e)}")
            error_msg = f"Có lỗi xảy ra: {str(e)}"
            yield json.dumps({"error": error_msg}) + "\n"
    
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
    
    TÌM KIẾM THÔNG TIN THỜI GIAN THỰC:
    1. Khi người dùng hỏi về tin tức, thời tiết, thể thao, sự kiện hiện tại, thông tin sản phẩm mới, hoặc bất kỳ dữ liệu cập nhật nào, hệ thống đã tự động tìm kiếm thông tin thực tế cho bạn.
    2. Hãy sử dụng thông tin tìm kiếm này để trả lời người dùng một cách chính xác và đầy đủ.
    3. Luôn đề cập đến nguồn thông tin khi sử dụng kết quả tìm kiếm.
    4. Nếu không có thông tin tìm kiếm, hãy trả lời dựa trên kiến thức của bạn và lưu ý rằng thông tin có thể không cập nhật.
    
    Hôm nay là {datetime.datetime.now().strftime("%d/%m/%Y")}.
    
    CẤU TRÚC JSON PHẢI CHÍNH XÁC như trên. Đảm bảo dùng dấu ngoặc kép cho cả keys và values. Đảm bảo các dấu ngoặc nhọn và vuông được đóng đúng cách.
    
    QUAN TRỌNG: Khi người dùng yêu cầu tạo sự kiện mới, hãy luôn sử dụng lệnh ##ADD_EVENT:...## trong phản hồi của bạn mà không cần quá nhiều bước xác nhận.
    
    Đối với hình ảnh:
    - Nếu người dùng gửi hình ảnh món ăn, hãy mô tả món ăn, và đề xuất cách nấu hoặc thông tin dinh dưỡng nếu phù hợp
    - Nếu là hình ảnh hoạt động gia đình, hãy mô tả hoạt động và đề xuất cách ghi nhớ khoảnh khắc đó
    - Với bất kỳ hình ảnh nào, hãy giúp người dùng liên kết nó với thành viên gia đình hoặc sự kiện nếu phù hợp
    """
    
    # Thêm thông tin về người dùng hiện tại
    if current_member_id and current_member_id in family_data:
        current_member = family_data[current_member_id]
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
    
    return system_prompt

# Kiểm tra nhu cầu tìm kiếm và thực hiện tìm kiếm
async def check_search_need(messages, openai_api_key, tavily_api_key):
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
        
        # Phát hiện ý định tìm kiếm
        need_search, search_query, is_news_query = detect_search_intent(last_user_message, openai_api_key)
        
        if need_search:
            # Quyết định có lọc domain hay không dựa trên is_news_query
            domains_to_include = VIETNAMESE_NEWS_DOMAINS if is_news_query else None
            
            # Thực hiện tìm kiếm
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
        logger.error(f"Lỗi khi kiểm tra nhu cầu tìm kiếm: {str(e)}")
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

    Args:
        query (str): Câu hỏi của người dùng
        api_key (str): OpenAI API key

    Returns:
        tuple: (need_search, search_query, is_news_query)
               need_search: True/False
               search_query: Câu truy vấn đã được tinh chỉnh
               is_news_query: True nếu là tin tức/thời sự, False nếu khác
    """
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
            search_query = query # Default là query gốc
            is_news_query = False # Default là false

            if need_search:
                search_query = result.get("search_query", query)
                # Đảm bảo search_query không rỗng nếu cần search
                if not search_query:
                    search_query = query
                is_news_query = result.get("is_news_query", False)

            logger.info(f"Phân tích truy vấn: need_search={need_search}, search_query='{search_query}', is_news_query={is_news_query}")
            return need_search, search_query, is_news_query

        except json.JSONDecodeError as json_err:
            logger.error(f"Lỗi giải mã JSON từ detect_search_intent: {json_err}")
            logger.error(f"Chuỗi JSON không hợp lệ: {result_str}")
            return False, query, False # Fallback
        except Exception as e:
            logger.error(f"Lỗi không xác định trong detect_search_intent: {e}")
            return False, query, False # Fallback

    except Exception as e:
        logger.error(f"Lỗi khi gọi OpenAI trong detect_search_intent: {e}")
        return False, query, False # Fallback

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

# Hàm chuyển đổi text thành speech sử dụng facebook/mms-tts-vie từ Hugging Face
def text_to_speech_huggingface(text, speed=1.0, max_length=1000):
    """
    Chuyển đổi văn bản thành giọng nói sử dụng mô hình facebook/mms-tts-vie
    
    Args:
        text (str): Văn bản cần chuyển đổi
        speed (float): Hệ số tốc độ (0.5-2.0)
        max_length (int): Độ dài tối đa của văn bản
        
    Returns:
        str: Base64 encoded audio data
    """
    try:
        # Giới hạn độ dài văn bản
        if len(text) > max_length:
            text = text[:max_length] + "..."
        
        # Import thư viện cần thiết
        from transformers import VitsModel, AutoTokenizer
        import torch
        import io
        import soundfile as sf
        import numpy as np
        
        # Tải mô hình và tokenizer
        model = VitsModel.from_pretrained("facebook/mms-tts-vie")
        tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-vie")
        
        # Tokenize và chuyển đổi thành waveform
        inputs = tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            output = model(**inputs).waveform
        
        # Chuyển đổi tốc độ (resampling)
        if speed != 1.0:
            # Chuyển về numpy array để xử lý
            waveform_np = output.squeeze().numpy()
            
            # Số lượng mẫu mới dựa trên tốc độ
            new_length = int(len(waveform_np) / speed)
            
            # Resampling đơn giản
            indices = np.linspace(0, len(waveform_np) - 1, new_length)
            waveform_np_resampled = np.interp(indices, np.arange(len(waveform_np)), waveform_np)
            
            # Chuyển lại thành tensor để xử lý tiếp
            waveform_resampled = torch.from_numpy(waveform_np_resampled).unsqueeze(0)
        else:
            waveform_resampled = output
        
        # Chuẩn bị buffer để lưu dữ liệu
        audio_buffer = io.BytesIO()
        
        # Lấy thông tin từ waveform
        sample_rate = 16000  # Sample rate mặc định của mô hình
        waveform_np = waveform_resampled.squeeze().numpy()
        
        # Lưu vào buffer dưới dạng WAV
        sf.write(audio_buffer, waveform_np, sample_rate, format='WAV')
        
        # Chuyển con trỏ về đầu buffer
        audio_buffer.seek(0)
        
        # Lấy dữ liệu và mã hóa base64
        audio_data = audio_buffer.read()
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        return audio_base64
        
    except Exception as e:
        logger.error(f"Lỗi khi sử dụng mô hình Hugging Face TTS: {str(e)}")
        logger.error(f"Chi tiết lỗi:", exc_info=True)
        return None

# Hàm chuyển đổi text thành speech sử dụng OpenAI API (giữ để backup)
def text_to_speech(text, api_key, voice="alloy"):
    """
    Chuyển đổi văn bản thành giọng nói sử dụng OpenAI TTS API
    
    Args:
        text (str): Văn bản cần chuyển đổi
        api_key (str): OpenAI API key
        voice (str): Giọng nói (alloy, echo, fable, onyx, nova, shimmer)
        
    Returns:
        str: Base64 encoded audio data
    """
    try:
        client = OpenAI(api_key=api_key)
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )
        
        # Lấy dữ liệu audio dưới dạng bytes
        audio_data = response.content
        
        # Chuyển đổi thành base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        return audio_base64
    except Exception as e:
        logger.error(f"Lỗi khi chuyển đổi văn bản thành giọng nói: {str(e)}")
        return None


# Hàm chuyển đổi text thành speech
def text_to_speech(text, api_key, voice="nova", speed=0.8, max_length=4096):
    """
    Chuyển đổi văn bản thành giọng nói sử dụng OpenAI TTS API
    
    Args:
        text (str): Văn bản cần chuyển đổi
        api_key (str): OpenAI API key
        voice (str): Giọng nói (alloy, echo, fable, onyx, nova, shimmer)
        speed (float): Tốc độ nói (0.5-1.5, mặc định 0.8 hơi chậm hơn bình thường)
        max_length (int): Độ dài tối đa của văn bản (tính bằng ký tự)
        
    Returns:
        str: Base64 encoded audio data
    """
    try:
        # Giới hạn độ dài văn bản để tránh lỗi
        if len(text) > max_length:
            text = text[:max_length] + "..."
            
        client = OpenAI(api_key=api_key)
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            speed=speed  # Thêm tham số tốc độ nói
        )
        
        # Lấy dữ liệu audio dưới dạng bytes
        audio_data = response.content
        
        # Chuyển đổi thành base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        return audio_base64
    except Exception as e:
        logger.error(f"Lỗi khi chuyển đổi văn bản thành giọng nói: {str(e)}")
        return None

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
        # Đơn giản hóa bằng cách thêm 30 ngày
        return today + datetime.timedelta(days=30)
    
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
def process_assistant_response(response, current_member=None):
    """Hàm xử lý lệnh từ phản hồi của trợ lý"""
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
                    add_event(details)
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
                    update_event(details)
            except json.JSONDecodeError as e:
                logger.error(f"Lỗi khi phân tích JSON cho UPDATE_EVENT: {e}")
        
        # Các lệnh xử lý khác tương tự
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
                        delete_event(event_id)
                    else:
                        details = json.loads(cmd)
                        if isinstance(details, dict):
                            if cmd_type == "ADD_FAMILY_MEMBER":
                                add_family_member(details)
                            elif cmd_type == "UPDATE_PREFERENCE":
                                update_preference(details)
                            elif cmd_type == "ADD_NOTE":
                                # Thêm thông tin về người tạo ghi chú
                                if current_member:
                                    details['created_by'] = current_member
                                add_note(details)
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý lệnh {cmd_type}: {e}")
    
    except Exception as e:
        logger.error(f"Lỗi khi xử lý phản hồi của trợ lý: {e}")
        logger.error(f"Phản hồi gốc: {response[:100]}...")

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
    prompt: Optional[str] = Form("Describe what you see in this image")
):
    """Endpoint phân tích hình ảnh"""
    try:
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
        
        # Trả về kết quả phân tích
        return {
            "analysis": response.choices[0].message.content,
            "member_id": member_id
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