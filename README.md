# AI Workflow Agent

A real AI agent powered by **Gemini 2.5 Flash** and **Python FastAPI** that generates content, sends real emails via Gmail, schedules emails, and lets you download outputs.

## Features

- 5 task modes: Write Email, Summarize, Action Plan, Draft Report, Custom Task
- Real-time pipeline step tracker
- **Send real emails via Gmail OAuth**
- **Schedule emails** to send at a future date/time
- **Download** any generated content as a .txt file
- Email preview before sending
- Status notifications in chat

## Tech Stack

- **Frontend**: HTML, CSS, Vanilla JavaScript
- **Backend**: Python, FastAPI, Uvicorn
- **AI**: Google Gemini API (`gemini-2.5-flash`)
- **Email**: Gmail API via Google OAuth 2.0
- **Scheduling**: APScheduler

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/Archana-verse/AI-Workflow-Agent.git
cd AI-Workflow-Agent
```

### 2. Create virtual environment

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Get your API keys

**Gemini API Key (Free)**
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Click Create API key

**Google OAuth Credentials (for Gmail)**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project
3. Go to APIs & Services → Enable APIs → enable **Gmail API**
4. Go to APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
5. Application type: **Web application**
6. Add Authorized redirect URI: `http://localhost:8000/auth/callback`
7. Copy the Client ID and Client Secret

### 5. Create your `.env` file

```
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
REDIRECT_URI=http://localhost:8000/auth/callback
SECRET_KEY=any-random-string
```

### 6. Run the app

```bash
uvicorn main:app --reload
```

Visit [http://localhost:8000](http://localhost:8000)

---

## Project Structure

```
AI-Workflow-Agent/
├── public/
│   ├── index.html      # App UI
│   ├── style.css       # Styles
│   └── agent.js        # Frontend logic
├── main.py             # FastAPI backend + Gemini + Gmail API
├── requirements.txt    # Python dependencies
├── .env.example
├── .gitignore
└── README.md
```

---

## License

MIT
