from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os, json, base64, uuid, asyncio
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import re


load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/callback")
SECRET_KEY           = os.getenv("SECRET_KEY", "change-this-secret")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# In-memory stores (use a DB in production)
user_tokens    = {}   # session_id -> credentials dict
scheduled_jobs = {}   # job_id -> job info

scheduler = AsyncIOScheduler()
scheduler.start()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── System Prompts ────────────────────────────────────────────────────────────

SYSTEM_PROMPTS = {
    "email": """You are an expert AI email writing assistant that works in clear steps.

When given a task:
1. "Step 1 — Understanding your request:" briefly restate what's needed.
2. "Step 2 — Analyzing tone & context:" note the appropriate tone and key points.
3. "Step 3 — Generating draft:" write the FULL email. Format it exactly like this:

TO: recipient@example.com
SUBJECT: Your subject here

Body of the email here...

4. "Step 4 — Refining:" note 1-2 improvements you applied.
5. End with "✓ Delivered" and a one-line tip.

Always include the TO:, SUBJECT: and full body in that exact format.""",

    "summary": """You are an expert summarization agent that works in clear steps.
1. "Step 1 — Understanding:" restate what needs summarizing.
2. "Step 2 — Extracting key points:" list the 3-5 most important points.
3. "Step 3 — Structuring the summary:" write a clean concise summary.
4. "Step 4 — Refining:" note simplifications made.
5. End with "✓ Delivered".""",

    "plan": """You are an expert planning agent that creates clear, actionable plans.
1. "Step 1 — Understanding the goal:" restate the objective.
2. "Step 2 — Breaking it down:" identify 3-5 key phases.
3. "Step 3 — Building the plan:" create a detailed week-by-week action plan.
4. "Step 4 — Refining:" add tips and resources.
5. End with "✓ Plan Delivered".""",

    "report": """You are an expert report writing agent.
1. "Step 1 — Understanding the brief:" restate what report is needed.
2. "Step 2 — Creating outline:" list the main sections.
3. "Step 3 — Drafting the report:" write the full report with headings.
4. "Step 4 — Refining:" note improvements made.
5. End with "✓ Report Delivered".""",

    "custom": """You are a versatile AI automation agent.
1. "Step 1 — Understanding:" restate the task.
2. "Step 2 — Analyzing:" break down what's needed.
3. "Step 3 — Generating output:" produce the main deliverable.
4. "Step 4 — Refining:" note improvements.
5. End with "✓ Delivered"."""
}

# ── Request / Response Models ─────────────────────────────────────────────────

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
    send_at: str  # ISO format datetime string

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_credentials(session_id: str) -> Optional[Credentials]:
    token_data = user_tokens.get(session_id)
    if not token_data:
        return None
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
    msg["To"]      = to
    msg["From"]    = sender
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()

def parse_email_from_response(text: str) -> dict:
    import re

    to_match = re.search(r"TO:\s*(.+)", text)
    subject_match = re.search(r"SUBJECT:\s*(.+)", text)

    to = to_match.group(1).strip() if to_match else ""
    subject = subject_match.group(1).strip() if subject_match else ""

    body_start = text.find("SUBJECT:")
    body = text[body_start + len(subject) + 8:] if body_start != -1 else ""

    return {
        "to": to,
        "subject": subject,
        "body": body.strip()
    }

async def send_gmail(session_id: str, to: str, subject: str, body: str):
    creds = get_credentials(session_id)
    if not creds:
        raise Exception("Not authenticated with Gmail")
    service     = build("gmail", "v1", credentials=creds)
    user_info   = service.users().getProfile(userId="me").execute()
    sender      = user_info["emailAddress"]
    raw_message = build_email(to, subject, body, sender)
    service.users().messages().send(
        userId="me",
        body={"raw": raw_message}
    ).execute()
    return sender

def is_valid_email(email: str) -> bool:
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    return re.match(pattern, email) is not None

# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def login(session_id: str):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=session_id,
        prompt="consent"
    )
    return {"auth_url": auth_url}

@app.get("/auth/callback")
async def callback(code: str, state: str):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    user_tokens[state] = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
    }
    return RedirectResponse(url=f"/?session_id={state}&auth=success")

@app.get("/auth/status")
async def auth_status(session_id: str):
    if session_id not in user_tokens:
        return {"authenticated": False}
    try:
        creds   = get_credentials(session_id)
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return {"authenticated": True, "email": profile["emailAddress"]}
    except:
        return {"authenticated": False}

@app.get("/auth/logout")
async def logout(session_id: str):
    user_tokens.pop(session_id, None)
    return {"success": True}

# ── Chat Route ────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not request.history:
        raise HTTPException(status_code=400, detail="No messages provided.")
    try:
        system_prompt = SYSTEM_PROMPTS.get(request.mode, SYSTEM_PROMPTS["custom"])
        model         = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_prompt
        )
        gemini_history = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [m.content]}
            for m in request.history[:-1]
        ]
        chat_session = model.start_chat(history=gemini_history)
        response     = chat_session.send_message(request.history[-1].content)
        reply        = response.text or ""

        # If email mode, try to parse email fields for preview
        email_data = None
        if request.mode == "email":
            parsed = parse_email_from_response(reply)
            if parsed["subject"] or parsed["to"]:
                email_data = parsed

        return {"reply": reply, "email_data": email_data}

    except Exception as e:
        print(f"Gemini API error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Email Routes ──────────────────────────────────────────────────────────────

@app.post("/api/send-email")
async def send_email(request: SendEmailRequest):

    if not request.to or "@" not in request.to:
        raise HTTPException(
            status_code=400,
            detail="Invalid recipient email address"
        )

    try:
        sender = await send_gmail(
            request.session_id,
            request.to,
            request.subject,
            request.body
        )

        return {
            "success": True,
            "message": f"Email sent successfully from {sender} to {request.to}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/schedule-email")
async def schedule_email(request: ScheduleEmailRequest):
    try:
        send_time = datetime.fromisoformat(request.send_at)
        job_id    = str(uuid.uuid4())

        async def send_job():
            try:
                await send_gmail(
                    request.session_id,
                    request.to,
                    request.subject,
                    request.body
                )
                scheduled_jobs[job_id]["status"] = "sent"
            except Exception as e:
                scheduled_jobs[job_id]["status"] = f"failed: {str(e)}"

        scheduler.add_job(send_job, DateTrigger(run_date=send_time), id=job_id)

        scheduled_jobs[job_id] = {
            "id":       job_id,
            "to":       request.to,
            "subject":  request.subject,
            "send_at":  request.send_at,
            "status":   "scheduled"
        }

        return {"success": True, "job_id": job_id, "scheduled_for": request.send_at}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/scheduled-emails")
async def get_scheduled(session_id: str):
    return {"jobs": list(scheduled_jobs.values())}

@app.delete("/api/scheduled-emails/{job_id}")
async def cancel_scheduled(job_id: str):
    try:
        scheduler.remove_job(job_id)
        if job_id in scheduled_jobs:
            scheduled_jobs[job_id]["status"] = "cancelled"
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

# ── Serve Frontend ────────────────────────────────────────────────────────────

app.mount("/public", StaticFiles(directory="public"), name="public")

@app.get("/style.css")
async def css():
    return FileResponse("public/style.css", media_type="text/css")

@app.get("/agent.js")
async def js():
    return FileResponse("public/agent.js", media_type="application/javascript")

@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str = ""):
    return FileResponse("public/index.html")
