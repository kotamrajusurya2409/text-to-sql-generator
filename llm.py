"""
SQL Generator Module - v3
Multi-provider: Azure OpenAI | OpenRouter (dynamic models) | Ollama (local + download)
"""

import re, json, os, time, requests
from typing import Tuple, Callable, Optional
from dotenv import load_dotenv

load_dotenv()

PROVIDER_AZURE      = "azure"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OLLAMA     = "ollama"

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", PROVIDER_OLLAMA)

AZURE_API_KEY  = os.getenv("AZURE_API_KEY", "")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "")
AZURE_MODEL    = os.getenv("AZURE_MODEL", "gpt-4o")
AZURE_VERSION  = os.getenv("AZURE_VERSION", "2024-12-01-preview")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Curated popular Ollama models for SQL tasks
OLLAMA_POPULAR_MODELS = [
    {"id": "sqlcoder:7b",              "name": "SQLCoder 7B",         "tag": "🏆 SQL Specialist"},
    {"id": "sqlcoder:15b",             "name": "SQLCoder 15B",        "tag": "🏆 SQL Specialist"},
    {"id": "deepseek-coder:6.7b",      "name": "DeepSeek Coder 6.7B", "tag": "💻 Code"},
    {"id": "deepseek-coder:33b",       "name": "DeepSeek Coder 33B",  "tag": "💻 Code"},
    {"id": "qwen2.5-coder:7b",         "name": "Qwen2.5 Coder 7B",   "tag": "💻 Code"},
    {"id": "qwen2.5-coder:32b",        "name": "Qwen2.5 Coder 32B",  "tag": "💻 Code"},
    {"id": "codellama:7b",             "name": "Code Llama 7B",       "tag": "💻 Code"},
    {"id": "codellama:13b",            "name": "Code Llama 13B",      "tag": "💻 Code"},
    {"id": "codellama:34b",            "name": "Code Llama 34B",      "tag": "💻 Code"},
    {"id": "llama3.2:3b",              "name": "Llama 3.2 3B",        "tag": "⚡ Fast"},
    {"id": "llama3.2:1b",              "name": "Llama 3.2 1B",        "tag": "⚡ Fast"},
    {"id": "llama3.1:8b",              "name": "Llama 3.1 8B",        "tag": "🌟 General"},
    {"id": "llama3.1:70b",             "name": "Llama 3.1 70B",       "tag": "🌟 General"},
    {"id": "mistral:7b",               "name": "Mistral 7B",          "tag": "🌟 General"},
    {"id": "mixtral:8x7b",             "name": "Mixtral 8x7B",        "tag": "🌟 General"},
    {"id": "phi3:mini",                "name": "Phi-3 Mini",          "tag": "⚡ Fast"},
    {"id": "phi3:medium",              "name": "Phi-3 Medium",        "tag": "🌟 General"},
    {"id": "phi3.5:latest",            "name": "Phi-3.5",             "tag": "⚡ Fast"},
    {"id": "gemma2:9b",                "name": "Gemma 2 9B",          "tag": "🌟 General"},
    {"id": "gemma2:27b",               "name": "Gemma 2 27B",         "tag": "🌟 General"},
    {"id": "deepseek-r1:7b",           "name": "DeepSeek R1 7B",      "tag": "🧠 Reasoning"},
    {"id": "deepseek-r1:14b",          "name": "DeepSeek R1 14B",     "tag": "🧠 Reasoning"},
    {"id": "deepseek-r1:32b",          "name": "DeepSeek R1 32B",     "tag": "🧠 Reasoning"},
    {"id": "qwen3:8b",                 "name": "Qwen3 8B",            "tag": "🧠 Reasoning"},
    {"id": "qwen3:14b",                "name": "Qwen3 14B",           "tag": "🧠 Reasoning"},
    {"id": "qwen3:32b",                "name": "Qwen3 32B",           "tag": "🧠 Reasoning"},
    {"id": "starcoder2:7b",            "name": "StarCoder2 7B",       "tag": "💻 Code"},
    {"id": "starcoder2:15b",           "name": "StarCoder2 15B",      "tag": "💻 Code"},
    {"id": "wizardcoder:python-34b",   "name": "WizardCoder 34B",     "tag": "💻 Code"},
]

# DB dialect hints for SQL generation
DB_DIALECTS = {
    "sqlserver":  "Microsoft SQL Server T-SQL. Use TOP N (not LIMIT), GETDATE(), NVARCHAR, square brackets for identifiers.",
    "mysql":      "MySQL. Use LIMIT N (not TOP), NOW(), VARCHAR, backticks for identifiers.",
    "postgresql": "PostgreSQL. Use LIMIT N (not TOP), NOW(), VARCHAR, double quotes for identifiers, ILIKE for case-insensitive search.",
    "sqlite":     "SQLite. Use LIMIT N (not TOP), date() functions, TEXT type. No stored procedures.",
}


class SQLGenerationError(Exception):
    pass


class LLMConfig:
    provider:           str = DEFAULT_PROVIDER
    model:              str = "llama3.2:3b"
    max_retries:        int = 3
    retry_delay:        int = 5
    timeout_azure:      int = 60
    timeout_openrouter: int = 90
    timeout_ollama:     int = 300
    db_type:            str = "sqlserver"
    _azure_api_key:     str = AZURE_API_KEY
    _azure_endpoint:    str = AZURE_ENDPOINT
    _openrouter_api_key:str = OPENROUTER_API_KEY

    @classmethod
    def set(cls, provider, model):
        cls.provider = provider
        cls.model    = model

    @classmethod
    def set_retry(cls, max_retries, retry_delay):
        cls.max_retries = max(1, int(max_retries))
        cls.retry_delay = max(1, int(retry_delay))

    @classmethod
    def set_timeouts(cls, azure=None, openrouter=None, ollama=None):
        if azure      is not None: cls.timeout_azure      = max(10, int(azure))
        if openrouter is not None: cls.timeout_openrouter = max(10, int(openrouter))
        if ollama     is not None: cls.timeout_ollama     = max(30, int(ollama))

    @classmethod
    def set_api_key(cls, provider, api_key, endpoint=None):
        if provider == "azure":
            cls._azure_api_key  = api_key.strip()
            if endpoint: cls._azure_endpoint = endpoint.strip()
        elif provider == "openrouter":
            cls._openrouter_api_key = api_key.strip()

    @classmethod
    def info(cls):
        return {k: getattr(cls, k) for k in
                ("provider","model","max_retries","retry_delay",
                 "timeout_azure","timeout_openrouter","timeout_ollama")}


def _call_llm_once(system_prompt: str, user_prompt: str) -> str:
    p = LLMConfig.provider

    if p == PROVIDER_AZURE:
        from openai import AzureOpenAI
        api_key  = LLMConfig._azure_api_key  or AZURE_API_KEY
        endpoint = LLMConfig._azure_endpoint or AZURE_ENDPOINT
        if not api_key or not endpoint:
            raise SQLGenerationError("Azure API key and endpoint required. Enter in AI Model panel.")
        client = AzureOpenAI(api_key=api_key, api_version=AZURE_VERSION,
                             azure_endpoint=endpoint, timeout=LLMConfig.timeout_azure)
        resp = client.chat.completions.create(
            model=AZURE_MODEL,
            messages=[{"role":"system","content":system_prompt},
                      {"role":"user","content":user_prompt}],
            max_completion_tokens=4096,
        )
        return resp.choices[0].message.content

    elif p == PROVIDER_OPENROUTER:
        api_key = LLMConfig._openrouter_api_key or OPENROUTER_API_KEY
        if not api_key:
            raise SQLGenerationError("OpenRouter API key required. Enter in AI Model panel.")
        r = requests.post(OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost:5000",
                     "X-Title": "Text-to-SQL"},
            json={"model": LLMConfig.model,
                  "messages": [{"role":"system","content":system_prompt},
                                {"role":"user","content":user_prompt}],
                  "max_tokens": 4096},
            timeout=LLMConfig.timeout_openrouter)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise SQLGenerationError(f"OpenRouter: {data['error'].get('message', data['error'])}")
        return data["choices"][0]["message"]["content"]

    elif p == PROVIDER_OLLAMA:
        r = requests.post(f"{OLLAMA_URL}/api/chat",
            json={"model": LLMConfig.model,
                  "messages": [{"role":"system","content":system_prompt},
                                {"role":"user","content":user_prompt}],
                  "stream": False},
            timeout=LLMConfig.timeout_ollama)
        r.raise_for_status()
        return r.json()["message"]["content"]

    raise SQLGenerationError(f"Unknown provider: {p}")


def _call_llm(system_prompt: str, user_prompt: str,
              status_cb: Optional[Callable[[str], None]] = None) -> str:
    last_error = None
    for attempt in range(1, LLMConfig.max_retries + 1):
        try:
            msg = (f"Attempt {attempt}/{LLMConfig.max_retries}: calling "
                   f"{LLMConfig.provider}/{LLMConfig.model}...")
            if status_cb: status_cb({"stage": "calling", "attempt": attempt,
                                      "max": LLMConfig.max_retries, "message": msg})
            result = _call_llm_once(system_prompt, user_prompt)
            if status_cb: status_cb({"stage": "parsing", "attempt": attempt,
                                      "max": LLMConfig.max_retries,
                                      "message": "Response received, parsing..."})
            return result
        except SQLGenerationError:
            raise
        except Exception as e:
            last_error = e
            if attempt < LLMConfig.max_retries:
                wait = LLMConfig.retry_delay * attempt
                msg = f"Attempt {attempt} failed: {e}. Retrying in {wait}s..."
                if status_cb: status_cb({"stage": "retry", "attempt": attempt,
                                          "max": LLMConfig.max_retries, "message": msg})
                time.sleep(wait)

    raise SQLGenerationError(
        f"All {LLMConfig.max_retries} attempts failed.\n"
        f"Last error: {last_error}\n"
        f"Tips: increase timeout in Settings, or try a different model.")


def fetch_openrouter_models() -> dict:
    """Fetch all current models from OpenRouter API."""
    headers = {"Content-Type": "application/json"}
    key = LLMConfig._openrouter_api_key or OPENROUTER_API_KEY
    if key: headers["Authorization"] = f"Bearer {key}"
    r = requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    free_models, paid_models = [], []
    for m in data.get("data", []):
        mid  = m.get("id", "")
        name = m.get("name", mid)
        ctx  = m.get("context_length", 0)
        pricing = m.get("pricing", {})
        cost = float(pricing.get("prompt", 0) or 0)
        info = {"id": mid, "name": name, "context": ctx, "cost": cost}
        if mid.endswith(":free") or cost == 0:
            free_models.append(info)
        else:
            paid_models.append(info)
    free_models.sort(key=lambda x: x["name"].lower())
    paid_models.sort(key=lambda x: x["cost"])
    return {"free": free_models, "paid": paid_models}


def test_api_key(provider: str) -> dict:
    """Quick test: send a minimal request to verify credentials work."""
    try:
        if provider == "openrouter":
            key = LLMConfig._openrouter_api_key or OPENROUTER_API_KEY
            if not key: return {"success": False, "error": "No API key set"}
            r = requests.get(OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {key}","Content-Type":"application/json"},
                timeout=10)
            r.raise_for_status()
            count = len(r.json().get("data", []))
            return {"success": True, "message": f"✅ Valid key — {count} models accessible"}
        elif provider == "azure":
            key      = LLMConfig._azure_api_key or AZURE_API_KEY
            endpoint = LLMConfig._azure_endpoint or AZURE_ENDPOINT
            if not key or not endpoint:
                return {"success": False, "error": "Key and endpoint required"}
            from openai import AzureOpenAI
            client = AzureOpenAI(api_key=key, api_version=AZURE_VERSION,
                                 azure_endpoint=endpoint, timeout=10)
            client.models.list()
            return {"success": True, "message": "✅ Azure credentials valid"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": False, "error": "Unknown provider"}


def get_ollama_models() -> list:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def ollama_pull_stream(model_name: str):
    """Generator yielding SSE lines for Ollama pull progress."""
    import json as _json
    try:
        r = requests.post(f"{OLLAMA_URL}/api/pull",
                          json={"name": model_name}, stream=True, timeout=600)
        for line in r.iter_lines():
            if line:
                try:
                    data = _json.loads(line.decode())
                    yield f"data: {_json.dumps(data)}\n\n"
                except Exception:
                    pass
        yield 'data: {"done":true}\n\n'
    except Exception as e:
        yield f'data: {{"error":"{e}"}}\n\n'


def _build_schema_string(schema: dict) -> str:
    lines = []
    for table, columns in schema.items():
        cols = [f"{c.get('column','?')} ({c.get('type','?')})" for c in columns]
        lines.append(f"Table '{table}':\n  Columns: {', '.join(cols)}")
    return "\n".join(lines)


def _build_prompt(question: str, schema_str: str, context: str,
                  allow_dml: bool, db_type: str = "sqlserver") -> str:
    dialect = DB_DIALECTS.get(db_type, DB_DIALECTS["sqlserver"])
    dml = ("\nYou can generate INSERT, UPDATE, or DELETE. Always include WHERE for UPDATE/DELETE.\n"
           if allow_dml else
           "\nGenerate ONLY SELECT queries. No INSERT/UPDATE/DELETE.\n")
    return f"""You are an expert {dialect} database developer.

DATABASE DIALECT: {dialect}

SCHEMA:
{schema_str}

SIMILAR PAST QUERIES:
{context or "None"}

USER QUESTION: "{question}"

RULES:
1. Use exact table/column names from the schema
2. Use correct syntax for {db_type}
{dml}

RESPOND ONLY with this exact JSON (no markdown, no extra text):
{{"sql": "YOUR_SQL_HERE", "explanation": "Brief plain-English explanation"}}
"""


def _extract_sql(text: str) -> Tuple[str, str]:
    text = text.strip()
    for pattern in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"]:
        m = re.search(pattern, text, re.DOTALL)
        if m: text = m.group(1).strip(); break
    try:
        d = json.loads(text)
        sql  = re.sub(r"\s+", " ", d.get("sql","").strip())
        expl = d.get("explanation","").strip()
        if sql: return sql, expl or "SQL generated"
    except Exception:
        pass
    m = re.search(r"((?:WITH\s+.+?\s+AS\s+\(.+?\)\s*)?(?:SELECT|INSERT|UPDATE|DELETE).+?)(?:\n|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(), "SQL extracted from response"
    raise SQLGenerationError("Could not parse SQL from model response.")


def generate_sql(question: str, schema: dict, context: str = "",
                 allow_dml: bool = False,
                 status_cb: Optional[Callable] = None) -> dict:
    if status_cb: status_cb({"stage":"building","message":"Building schema context..."})
    schema_str = _build_schema_string(schema)
    prompt     = _build_prompt(question, schema_str, context, allow_dml, LLMConfig.db_type)

    raw = _call_llm("You are an expert database developer.", prompt, status_cb=status_cb)
    if status_cb: status_cb({"stage":"parsing","message":"Parsing SQL response..."})

    sql, explanation = _extract_sql(raw)
    su = sql.upper()
    qt = ("SELECT_CTE" if su.startswith("WITH") else
          su.split()[0] if su.split()[0] in ("SELECT","INSERT","UPDATE","DELETE") else "UNKNOWN")
    return {"sql": sql, "explanation": explanation, "query_type": qt,
            "has_cte": "WITH" in su, "has_window": "OVER(" in su or "OVER (" in su}


def generate_sql_with_dml(question, schema, context="", status_cb=None):
    return generate_sql(question, schema, context, allow_dml=True, status_cb=status_cb)


def chat_with_sql(messages: list, current_sql: str, schema: dict) -> dict:
    """AI chat to refine generated SQL. messages = [{role, content}]."""
    schema_str = _build_schema_string(schema)[:2000]
    sys_prompt = f"""You are an expert SQL assistant helping refine queries.

Current SQL:
{current_sql}

Database schema (summary):
{schema_str}

When the user asks for SQL changes, respond with JSON:
{{"sql": "UPDATED_SQL", "explanation": "what changed", "message": "friendly response"}}
If no SQL change needed, respond with JSON:
{{"sql": null, "explanation": "", "message": "your response"}}
IMPORTANT: Respond ONLY with valid JSON, no markdown."""

    last_user = messages[-1]["content"] if messages else "Help"
    raw = _call_llm(sys_prompt, last_user)
    try:
        raw_clean = raw.strip()
        for p in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"]:
            m = re.search(p, raw_clean, re.DOTALL)
            if m: raw_clean = m.group(1).strip(); break
        d = json.loads(raw_clean)
        return {"sql": d.get("sql"), "explanation": d.get("explanation",""),
                "message": d.get("message", raw)}
    except Exception:
        return {"sql": None, "explanation": "", "message": raw}


def explain_sql(sql: str) -> str:
    try:
        return _call_llm("Explain SQL queries in simple English.",
                         f"Explain this SQL:\n{sql}").strip()
    except Exception as e:
        return f"Explanation failed: {e}"
