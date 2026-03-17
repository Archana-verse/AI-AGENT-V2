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

load_dotenv()

# -- Setup Base Directory for Vercel --
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# -- Config --
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))   

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# NOTE: user_tokens is in-memory. It WILL reset on Vercel cold starts.
user_tokens = {}

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# -- System Prompts --
SYSTEM_PROMPTS = {
    "email": """You are an expert AI email writing assistant... (Full prompt here)""",
    "summary": """You are an expert summarization agent...""",
    "plan": """You are an expert planning agent...""",
    "report": """You are an expert report writing agent...""",
    "custom": """You are a versatile AI automation agent..."""
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
    if not creds: raise Exception("Not authenticated")
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
    try:
        sys_p = SYSTEM_PROMPTS.get(request.mode, SYSTEM_PROMPTS["custom"])
        model = genai.GenerativeModel(model_name="gemini-1.5-flash", system_instruction=sys_p)
        history = [{"role": "model" if m.role == "assistant" else "user", "parts": [m.content]} for m in request.history[:-1]]
        chat_sess = model.start_chat(history=history)
        resp = chat_sess.send_message(request.history[-1].content)
        reply = resp.text or ""
        email_data = parse_email_from_response(reply) if request.mode == "email" else None
        return {"reply": reply, "email_data": email_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/send-email")
async def send_email_api(request: SendEmailRequest):
    try:
        sender = await send_gmail(request.session_id, request.to, request.subject, request.body)
        return {"success": True, "message": f"Sent via {sender}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -- Static Asset Servicing --
@app.get("/style.css")
async def get_css():
    return FileResponse(os.path.join(BASE_DIR, "public", "style.css"))

@app.get("/agent.js")
async def get_js():
    return FileResponse(os.path.join(BASE_DIR, "public", "agent.js"))

@app.get("/")
@app.get("/{path:path}")
async def catch_all(path: str = None):
    return FileResponse(os.path.join(BASE_DIR, "public", "index.html"))