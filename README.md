# ⚡ Text-to-SQL Generator

> **Turn plain English questions into SQL queries — instantly.**
> Supports any database, any AI model (local or cloud), with zero data leaving your machine if you choose.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![AI](https://img.shields.io/badge/AI-LLM-green)
![License](https://img.shields.io/badge/License-MIT-yellow)




<img width="956" height="475" alt="image" src="https://github.com/user-attachments/assets/14ebc851-2b96-45f4-af01-e799ed3d787e" />





## ✨ What This Does

Type a question like **"Get top 10 customers by total sales"** and the app:

1. 🤖 Sends it to an AI model (your choice — free, paid, or local)
2. 📝 Generates a correct SQL query for your database
3. ▶ Executes it and shows the results in a table
4. 💬 Lets you refine the query by chatting with AI
5. ⬇ Exports results to CSV or Excel

No SQL knowledge required.

---

## 🖥️ Screenshots

| Ask a question | Generated SQL | Query Results |
|---|---|---|
|<img width="683" height="169" alt="image" src="https://github.com/user-attachments/assets/4567abb2-d1e4-417e-818c-364994a148d8" />| <img width="691" height="272" alt="image" src="https://github.com/user-attachments/assets/a0727d4b-5f18-4e10-8ad3-03b314b532f0" />|<img width="682" height="256" alt="image" src="https://github.com/user-attachments/assets/47d38772-d293-40b1-8f67-af370deaff60" />|

---

## 🚀 Quick Start (5 minutes)

### Step 1 — Download the code

Click the green **Code** button at the top of this page → **Download ZIP** → extract it.

Or using Git:
```bash
git clone https://github.com/YOUR_USERNAME/text-to-sql-generator.git
cd text-to-sql-generator
```

### Step 2 — Create a virtual environment

**Windows** (double-click or run in terminal):
```bash
setup_venv.bat
```

**Mac/Linux:**
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Run the app

```bash
# Windows
venv\Scripts\activate
python app.py

# Mac/Linux
source venv/bin/activate
python app.py
```

### Step 4 — Open in browser

Go to: **http://127.0.0.1:5000**

---

## 🗄️ Supported Databases

| Database | Driver | Notes |
|---|---|---|
| **SQL Server** / Azure SQL | `pyodbc` | Requires ODBC Driver 17 |
| **MySQL** / MariaDB | `mysql-connector-python` | |
| **PostgreSQL** | `psycopg2-binary` | |
| **SQLite** | Built-in | No installation needed |

---

## 🤖 Supported AI Models

### 🆓 Free Models (via OpenRouter — no credit card needed)
| Model | Best For |
|---|---|
| DeepSeek R1 | Complex reasoning, best free SQL |
| Llama 4 Maverick | General queries |
| Qwen3 235B | Multi-step analysis |
| Qwen3 Coder 480B | Code & SQL generation |
| GPT OSS 120B | Strong all-rounder |
| Gemma 3 27B | Fast, lightweight |
| Mistral 7B | Simple queries |

### 💳 Paid Models (via OpenRouter — requires balance)
| Model | Provider | Strength |
|---|---|---|
| Claude Opus / Sonnet | Anthropic | Best overall SQL accuracy |
| GPT-5 / GPT-4o | OpenAI | Industry standard |
| Gemini 2.5 Pro | Google | Long context |
| Grok 3 | xAI | Fast reasoning |
| DeepSeek R1 | DeepSeek | Cost-effective |

### 🖥️ Local Models (Ollama — 100% offline)
| Model | Size | Best For |
|---|---|---|
| **SQLCoder 15B** | 9 GB | ⭐ Best for SQL generation |
| DeepSeek R1 7B | 4.7 GB | Good reasoning |
| Qwen2.5 Coder 14B | 9 GB | Code & SQL |
| Llama 3.1 8B | 4.7 GB | General use |
| Phi-4 | 9.1 GB | Compact + capable |

---

## 🔑 How to Add API Keys

### Azure OpenAI
1. Open the app → click **Azure** tab in the AI Model panel
2. Enter your **Endpoint URL** (e.g. `https://xxx.cognitiveservices.azure.com/`)
3. Enter your **API Key**
4. Enter your **Deployment Name** (e.g. `gpt-4o`)
5. Click **💾 Save** → then **🔌 Test** to verify

### OpenRouter (Free + Paid Models)
1. Get a free API key at [openrouter.ai/keys](https://openrouter.ai/keys)
2. Open the app → click **Free** or **Paid** tab
3. Expand **OpenRouter API Key** section
4. Paste your key and click **Save**
5. Free models work with or without a key (anonymous quota)

### Ollama (Local — No Key Needed)
1. Download Ollama from [ollama.ai](https://ollama.ai)
2. Run: `ollama serve`
3. In the app → click **Local** tab → **Refresh Installed Models**
4. Pull a model using the **⬇ Pull** buttons
5. Select a model and click **✅ Apply Model**

---

## ➕ How to Add a New Model

### Adding a new OpenRouter model
Open `llm.py` and find the `FREE_MODELS` or `PAID_MODELS` list. Add a new entry:

```python
# Format: (openrouter_id, display_name, provider, tag, description)
FREE_MODELS = [
    ...
    ("your-provider/your-model:free", "My New Model", "Provider", "💬 General", "Description here"),
]
```

Find the model ID from [openrouter.ai/models](https://openrouter.ai/models).

### Adding a new Ollama model
Open `llm.py` and find `OLLAMA_POPULAR_MODELS`:

```python
OLLAMA_POPULAR_MODELS = [
    ...
    {"id": "mymodel:7b", "name": "My New Model 7B", "tag": "💬 General", "size": "4.5 GB"},
]
```

Or simply type the model name directly in the **"pull by name"** box in the app.

### Adding a completely new provider
1. Add a new constant in `llm.py`:
   ```python
   PROVIDER_MYSERVICE = "myservice"
   ```
2. Add a branch in `_call_once()`:
   ```python
   elif p == PROVIDER_MYSERVICE:
       # Your API call here
       return response_text
   ```
3. Add a tab in the UI section of `app.py`

---

## 📁 Project Structure

```
text-to-sql-generator/
│
├── app.py              ← Main Flask web app + all HTML/CSS/JS
├── llm.py              ← AI model integrations (Azure, OpenRouter, Ollama)
├── schema_loader.py    ← Reads database structure (tables & columns)
├── executor.py         ← Runs SQL queries, returns DataFrames
├── sql_validator.py    ← Safety checks (blocks DROP, ALTER, etc.)
├── rag.py              ← Remembers past queries to improve accuracy
├── requirements.txt    ← Python package dependencies
├── setup_venv.bat      ← One-click Windows setup script
└── docs/
    └── screenshots/    ← App screenshots for README
```

---

## 📦 Libraries Used

### Backend (Python)

| Library | Purpose | How It Works |
|---|---|---|
| **Flask** | Web server | Serves the HTML page and handles API routes like `/generate`, `/execute`, `/connect` |
| **pyodbc** | SQL Server connector | Opens a connection to SQL Server using an ODBC driver installed on your OS |
| **mysql-connector-python** | MySQL connector | Native Python driver for MySQL and MariaDB databases |
| **psycopg2-binary** | PostgreSQL connector | C-based PostgreSQL adapter, bundled with binary so no build tools needed |
| **openai** | Azure OpenAI client | Official Python SDK — handles auth, retries, token streaming for Azure deployments |
| **requests** | HTTP client | Sends API calls to OpenRouter and Ollama REST endpoints |
| **pandas** | Data manipulation | Wraps SQL query results into DataFrames, handles NULL values, exports to CSV/Excel |
| **numpy** | Numerical support | Used with pandas to replace NaN/Inf values before JSON serialization |
| **sentence-transformers** | Semantic search | Converts questions to vector embeddings; used by RAG to find similar past queries |
| **python-dotenv** | Config management | Loads `AZURE_API_KEY`, `OPENROUTER_API_KEY` etc. from a `.env` file at startup |
| **xlsxwriter** | Excel export | Creates formatted `.xlsx` files with styled column headers |

### How the AI Integration Works

```
User Question
     │
     ▼
schema_loader.py   ← Reads your DB structure (table names, column names, types)
     │
     ▼
rag.py             ← Searches past similar queries to add as context
     │
     ▼
llm.py             ← Builds a detailed prompt, calls the AI model
     │
     ├── Azure OpenAI  → openai SDK → Azure REST API
     ├── OpenRouter    → requests   → openrouter.ai/api/v1/chat/completions
     └── Ollama        → requests   → localhost:11434/api/chat
     │
     ▼
SQL extracted from AI response (JSON parsing + fallback regex)
     │
     ▼
sql_validator.py   ← Blocks dangerous operations (DROP, TRUNCATE, etc.)
     │
     ▼
executor.py        ← Runs query via pandas.read_sql() → returns rows
     │
     ▼
JSON response → Browser renders table
```

### Frontend (in-browser, no build step)

| Technology | Purpose |
|---|---|
| **Vanilla JavaScript** | All interactivity — no React/Vue needed |
| **CSS Custom Properties** | Theming with `--grad`, `--p`, `--ok` color variables |
| **EventSource (SSE)** | Streams Ollama pull progress in real time |
| **Fetch API** | All HTTP calls to Flask backend |
| **Inter + JetBrains Mono** | Fonts loaded from Google Fonts |

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
# Provider to use at startup
LLM_PROVIDER=azure          # azure | openrouter | ollama

# Azure OpenAI
AZURE_API_KEY=your_key_here
AZURE_ENDPOINT=https://xxx.cognitiveservices.azure.com/
AZURE_MODEL=gpt-4o
AZURE_VERSION=2024-12-01-preview

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...

# Ollama
OLLAMA_URL=http://localhost:11434
```

You can also enter all keys directly in the **AI Model** panel inside the app — no file editing needed.

---

## 🛡️ Safety Features

- **SQL Injection protection** — multiple statements separated by `;` are blocked
- **Dangerous operations blocked** — `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT` are never executed
- **DML mode off by default** — `INSERT`, `UPDATE`, `DELETE` only work when you explicitly toggle DML ON
- **UPDATE/DELETE require WHERE clause** — naked updates/deletes are blocked

---

## 🐛 Troubleshooting

| Error | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside your venv |
| `pyodbc connection failed` | Install [ODBC Driver 17 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| `Ollama not running` | Run `ollama serve` in a separate terminal |
| `OpenRouter 401` | Check your API key in the Free/Paid tab |
| `Azure auth error` | Verify endpoint URL ends with `/` and key is correct |
| `Port already in use` | Change `port=5000` to `port=5001` in `app.py` |
| Slow generation | Increase Ollama timeout in Settings panel (default 300s) |

---

## 🤝 Contributing

1. Fork this repository
2. Create a branch: `git checkout -b feature/my-improvement`
3. Make your changes
4. Push and open a Pull Request

Ideas welcome: new DB drivers, new model providers, UI improvements, better SQL prompts.

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🙏 Credits

Built with [Flask](https://flask.palletsprojects.com/), [OpenRouter](https://openrouter.ai/), [Ollama](https://ollama.ai/), and the open-source AI community.
