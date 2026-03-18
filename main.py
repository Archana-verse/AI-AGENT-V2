from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import google.generativeai as genai                   
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os, base64, uuid, re
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

load_dotenv()

# -- Config --
# Railway injects variables; keep these calls as-is.
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))   

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# Update this variable in Railway to: https://your-railway-url.up.railway.app/auth/callback
REDIRECT_URI         = os.getenv("REDIRECT_URI")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# NOTE: user_tokens is in-memory. On Railway, this stays alive until the service restarts.
user_tokens    = {}
scheduled_jobs = {}

# -- Scheduler Setup --
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not scheduler.running:
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# -- System Prompts --
SYSTEM_PROMPTS = {
    "email": """You are an expert AI email writing assistant... (Keep your original full prompt here)""",
    "summary": "You are an expert summarization agent...",
    "plan": "You are an expert planning agent...",
    "report": "You are an expert report writing agent...",
    "custom": "You are a versatile AI automation agent..."
}

# -- Models --
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    mode: str = "custom"
    history: List[Message]
    session_id: Optional[str] = None

class SendEmailRequest(BaseModel):
    session_id: str
    to: str
    subject: str
    body: str

class ScheduleEmailRequest(BaseModel):
    session_id: str
    to: str
    subject: str
    body: str
    send_at: str

# -- Helpers --
def get_credentials(session_id: str) -> Optional[Credentials]:
    token_data = user_tokens.get(session_id)
    if not token_data: return None
    return Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )

def build_email(to: str, subject: str, body: str, sender: str) -> str:
    msg = MIMEMultipart("alternative")
    msg["To"], msg["From"], msg["Subject"] = to, sender, subject
    msg.attach(MIMEText(body, "plain"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()

def parse_email_from_response(text: str) -> dict:
    to_m = re.search(r"TO:\s*(.+)", text)
    sub_m = re.search(r"SUBJECT:\s*(.+)", text)
    to = to_m.group(1).strip() if to_m else ""
    sub = sub_m.group(1).strip() if sub_m else ""
    body_idx = text.find("SUBJECT:")
    body = text[body_idx + len(sub) + 8:].strip() if body_idx != -1 else ""
    return {"to": to, "subject": sub, "body": body}

async def send_gmail(session_id: str, to: str, subject: str, body: str):
    creds = get_credentials(session_id)
    if not creds: raise Exception("Not authenticated with Gmail")
    service = build("gmail", "v1", credentials=creds)
    user_info = service.users().getProfile(userId="me").execute()
    sender = user_info["emailAddress"]
    raw_message = build_email(to, subject, body, sender)
    service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
    return sender

# -- Auth Routes --
@app.get("/auth/login")
async def login(session_id: str):
    flow = Flow.from_client_config(
        {"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, 
                 "redirect_uris": [REDIRECT_URI], "auth_uri": "https://accounts.google.com/o/oauth2/auth", 
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(access_type="offline", state=session_id, prompt="consent")
    return {"auth_url": auth_url}

@app.get("/auth/callback")
async def callback(code: str, state: str):
    flow = Flow.from_client_config(
        {"web": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, 
                 "redirect_uris": [REDIRECT_URI], "auth_uri": "https://accounts.google.com/o/oauth2/auth", 
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    user_tokens[state] = {"token": creds.token, "refresh_token": creds.refresh_token}
    return RedirectResponse(url=f"/?session_id={state}&auth=success")

# -- Chat Route --
@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not request.history:
        raise HTTPException(status_code=400, detail="No messages provided.")
    try:
        system_prompt = SYSTEM_PROMPTS.get(request.mode, SYSTEM_PROMPTS["custom"])
        
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_prompt
        )

        gemini_history = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [m.content]}
            for m in request.history[:-1]
        ]
        
        chat_session = model.start_chat(history=gemini_history)
        
        response = chat_session.send_message(
            request.history[-1].content,
            generation_config=genai.types.GenerationConfig(
                candidate_count=1,
                stop_sequences=["✓ Delivered", "✓ Plan Delivered", "✓ Report Delivered"],
                max_output_tokens=2048,
                temperature=0.7,
            )
        )
        
        reply = response.text or ""

        email_data = None
        if request.mode == "email":
            parsed = parse_email_from_response(reply)
            if parsed["subject"] or parsed["to"]:
                email_data = parsed

        return {"reply": reply, "email_data": email_data}

    except Exception as e:
        print(f"Gemini API error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -- Email Routes --
@app.post("/api/send-email")
async def send_email_api(request: SendEmailRequest):
    try:
        sender = await send_gmail(request.session_id, request.to, request.subject, request.body)
        return {"success": True, "message": f"Sent via {sender}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/schedule-email")
async def schedule_email(request: ScheduleEmailRequest):
    try:
        send_time = datetime.fromisoformat(request.send_at)
        job_id = str(uuid.uuid4())
        async def send_job():
            try:
                await send_gmail(request.session_id, request.to, request.subject, request.body)
                scheduled_jobs[job_id]["status"] = "sent"
            except:
                scheduled_jobs[job_id]["status"] = "failed"
        scheduler.add_job(send_job, DateTrigger(run_date=send_time), id=job_id)
        scheduled_jobs[job_id] = {"id": job_id, "to": request.to, "status": "scheduled"}
        return {"success": True, "job_id": job_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -- Static Files --
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/style.css")
async def css():
    return FileResponse(os.path.join(BASE_DIR, "public", "style.css"))

@app.get("/agent.js")
async def js():
    return FileResponse(os.path.join(BASE_DIR, "public", "agent.js"))

@app.get("/")
@app.get("/{path:path}")
async def serve(path: str = None):
    return FileResponse(os.path.join(BASE_DIR, "public", "index.html"))

# -- Start Command for Railway --
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)