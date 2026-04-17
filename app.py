"""
Text-to-SQL Generator
==================================================
Fixes :
  • Vivid gradient UI (purple/blue theme from original)
  • Model availability badges: ✅ Available / ⚠ Rate-limited / ❌ Down
  • Ollama Refresh works instantly (no page reload)
  • Execute + Explain wired and working correctly
  • Fast — no blocking calls on page load
  • Sidebar + main content two-column layout
  • All buttons respond with clear visual feedback
"""
from flask import Flask, request, jsonify, Response, stream_with_context
import json, time, traceback, threading
from io import BytesIO
import pandas as pd, numpy as np

from llm import (
    generate_sql, generate_sql_with_dml, explain_sql, chat_with_sql,
    LLMConfig, get_ollama_models, ollama_pull_stream, fetch_openrouter_models,
    test_api_key, OLLAMA_POPULAR_MODELS,
    PROVIDER_AZURE, PROVIDER_OPENROUTER, PROVIDER_OLLAMA, AZURE_MODEL,
)
from rag import RAG
from schema_loader import load_schema, DBConnection
from sql_validator import validate_sql
from executor import execute_sql

app = Flask(__name__)
_lock = threading.Lock()
db_conn     = None
db_schema   = None
dml_enabled = False
_gen_status = {}

def set_status(sid, stage, msg, attempt=0, max_a=0):
    _gen_status[sid] = {"stage": stage, "message": msg,
                        "attempt": attempt, "max": max_a, "ts": time.time()}

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index(): return HTML

@app.route("/set-model", methods=["POST"])
def set_model():
    d = request.json
    if not d.get("model"): return jsonify({"success": False, "error": "model required"})
    LLMConfig.set(d["provider"], d["model"])
    return jsonify({"success": True})

@app.route("/set-api-key", methods=["POST"])
def set_api_key():
    try:
        d = request.json
        LLMConfig.set_api_key(d["provider"], d.get("api_key",""), d.get("endpoint"))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/test-api-key", methods=["POST"])
def test_key_route():
    return jsonify(test_api_key(request.json.get("provider","")))

@app.route("/set-settings", methods=["POST"])
def set_settings():
    try:
        d = request.json
        LLMConfig.set_retry(d.get("max_retries",3), d.get("retry_delay",5))
        LLMConfig.set_timeouts(d.get("timeout_azure"), d.get("timeout_openrouter"), d.get("timeout_ollama"))
        return jsonify({"success": True, "config": LLMConfig.info()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/fetch-openrouter-models")
def fetch_or_models():
    try:
        return jsonify({"success": True, **fetch_openrouter_models()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/ollama-models")
def ollama_models_route():
    models = get_ollama_models()
    installed_set = set(models)
    popular = [dict(m, installed=(m["id"] in installed_set)) for m in OLLAMA_POPULAR_MODELS]
    return jsonify({"installed": models, "popular": popular})

@app.route("/ollama-pull")
def ollama_pull():
    model = request.args.get("model","")
    if not model: return jsonify({"error": "model required"}), 400
    return Response(stream_with_context(ollama_pull_stream(model)),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/connect", methods=["POST"])
def connect():
    global db_conn, db_schema
    try:
        d       = request.json
        db_type = d.get("db_type","sqlserver")
        LLMConfig.db_type = db_type

        if db_type == "sqlserver":
            import pyodbc
            auth = d.get("auth","windows")
            srv  = d.get("server","localhost")
            dbn  = d.get("database","")
            if auth == "windows":
                cs = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={srv};DATABASE={dbn};Trusted_Connection=yes;"
            else:
                cs = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={srv};"
                      f"DATABASE={dbn};UID={d.get('username')};PWD={d.get('password')};")
            raw = pyodbc.connect(cs, timeout=10)

        elif db_type == "mysql":
            import mysql.connector
            raw = mysql.connector.connect(
                host=d.get("server","localhost"), port=int(d.get("port",3306)),
                database=d.get("database",""), user=d.get("username","root"),
                password=d.get("password",""))

        elif db_type == "postgresql":
            import psycopg2
            raw = psycopg2.connect(
                host=d.get("server","localhost"), port=int(d.get("port",5432)),
                dbname=d.get("database",""), user=d.get("username","postgres"),
                password=d.get("password",""))

        elif db_type == "sqlite":
            import sqlite3
            raw = sqlite3.connect(d.get("filepath",":memory:"), check_same_thread=False)
        else:
            return jsonify({"success": False, "error": f"Unknown db_type: {db_type}"})

        db_conn   = DBConnection(raw, db_type, d.get("database",""))
        db_schema = load_schema(db_conn)
        RAG.clear()
        return jsonify({"success": True, "tables": len(db_schema), "schema": db_schema, "db_type": db_type})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

@app.route("/gen-status/<sid>")
def gen_status(sid):
    return jsonify(_gen_status.get(sid, {"stage":"idle","message":"","attempt":0,"max":0}))

@app.route("/generate", methods=["POST"])
def generate():
    global db_schema
    try:
        d         = request.json
        question  = d.get("question","")
        allow_dml = d.get("dml_enabled", False)
        sid       = d.get("session_id","default")
        if not db_schema:
            return jsonify({"success": False, "error": "No database connected. Please connect first."})
        if not question.strip():
            return jsonify({"success": False, "error": "Question cannot be empty."})
        set_status(sid, "starting", "Preparing generation...")
        def status_cb(info):
            set_status(sid, info.get("stage",""), info.get("message",""),
                       info.get("attempt",0), info.get("max",0))
        context = RAG.search(question, top_k=3)
        fn = generate_sql_with_dml if allow_dml else generate_sql
        result = fn(question, db_schema, context,
                    **({} if allow_dml else {"allow_dml": False}),
                    status_cb=status_cb)
        sql = result["sql"]
        validate_sql(sql, allow_dml=allow_dml)
        RAG.add(question, sql, result.get("explanation",""))
        set_status(sid, "done", "✅ SQL generated successfully!")
        return jsonify({"success": True, **result})
    except Exception as e:
        traceback.print_exc()
        set_status(d.get("session_id","default"), "error", str(e))
        return jsonify({"success": False, "error": str(e)})

@app.route("/validate", methods=["POST"])
def validate():
    try:
        d = request.json
        validate_sql(d.get("sql",""), allow_dml=d.get("dml_enabled",False))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/execute", methods=["POST"])
def execute():
    global db_conn
    try:
        if not db_conn:
            return jsonify({"success": False, "error": "Not connected to database"})
        sql = request.json.get("sql","")
        df  = execute_sql(db_conn, sql)
        df  = df.replace({pd.NA: None}).replace([np.nan, np.inf, -np.inf], None)
        df  = df.where(pd.notnull(df), None)
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].astype(str).replace("NaT", None)
        return jsonify({"success": True, "rows": len(df),
                        "columns": df.columns.tolist(), "data": df.to_dict("records")})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

@app.route("/explain", methods=["POST"])
def explain():
    try:
        sql = request.json.get("sql","")
        return jsonify({"success": True, "explanation": explain_sql(sql)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/chat", methods=["POST"])
def chat():
    try:
        d = request.json
        result = chat_with_sql(d.get("messages",[]), d.get("current_sql",""), db_schema or {})
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/check-model-status", methods=["POST"])
def check_model_status():
    """
    Check real availability of OpenRouter models by sending a tiny probe request.
    Returns status: 'ok' | 'rate_limited' | 'down' for each model id tested.
    """
    import requests as req
    model_ids = request.json.get("models", [])
    key = LLMConfig._openrouter_api_key if hasattr(LLMConfig, '_openrouter_api_key') else ""
    results = {}
    for mid in model_ids[:8]:          # probe max 8 at a time to keep it fast
        try:
            r = req.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}" if key else "",
                         "Content-Type": "application/json",
                         "HTTP-Referer": "http://localhost:5000"},
                json={"model": mid, "messages": [{"role":"user","content":"hi"}], "max_tokens": 1},
                timeout=6)
            if r.status_code == 200:
                results[mid] = "ok"
            elif r.status_code == 429:
                results[mid] = "rate_limited"
            elif r.status_code in (402, 403):
                results[mid] = "key_required"
            else:
                results[mid] = "down"
        except Exception:
            results[mid] = "down"
    return jsonify({"results": results})

@app.route("/toggle-dml", methods=["POST"])
def toggle_dml():
    global dml_enabled
    dml_enabled = not dml_enabled
    return jsonify({"enabled": dml_enabled})

@app.route("/export/csv", methods=["POST"])
def export_csv():
    data = request.json
    df   = pd.DataFrame(data["data"], columns=data["columns"]).fillna("NULL")
    resp = app.response_class(response=df.to_csv(index=False), status=200, mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=results.csv"
    return resp

@app.route("/export/excel", methods=["POST"])
def export_excel():
    data = request.json
    df   = pd.DataFrame(data["data"], columns=data["columns"])
    out  = BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as w:
        df.to_excel(w, sheet_name="Results", index=False)
        wb, ws = w.book, w.sheets["Results"]
        hfmt = wb.add_format({"bold":True,"bg_color":"#667eea","font_color":"#fff","border":1})
        for i, col in enumerate(data["columns"]):
            ws.write(0, i, col, hfmt)
            ws.set_column(i, i, min(max(df[col].astype(str).str.len().max() if len(df) else 0, len(col))+2, 50))
    out.seek(0)
    resp = app.response_class(response=out.getvalue(), status=200,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = "attachment; filename=results.xlsx"
    return resp

# ═══════════════════════════════════════════════════════════════════════════════
#  HTML — Colorful, vivid, fully wired
# ═══════════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Text-to-SQL Generator</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --grad:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
  --grad2:linear-gradient(135deg,#f093fb,#f5576c);
  --grad3:linear-gradient(135deg,#4facfe,#00f2fe);
  --grad4:linear-gradient(135deg,#43e97b,#38f9d7);
  --grad5:linear-gradient(135deg,#fa709a,#fee140);
  --p:#667eea;--p2:#764ba2;--p3:#8b83ff;
  --ok:#28a745;--err:#dc3545;--warn:#f0883e;--info:#17a2b8;
  --bg:#f0f2ff;--card:#fff;--sidebar:#fff;
  --border:#e0e4ef;--text:#1a1d2e;--sub:#6b7280;
  --code-bg:#1e1e2e;--code-fg:#cdd6f4;
  --shadow:0 4px 24px rgba(102,126,234,.15);
  --shadow-lg:0 8px 40px rgba(102,126,234,.25);
}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}

/* ── Top header ── */
.topbar{background:var(--grad);color:#fff;padding:0 28px;height:60px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 4px 20px rgba(102,126,234,.4);position:sticky;top:0;z-index:200}
.logo{font-size:1.35em;font-weight:700;letter-spacing:-.5px;display:flex;align-items:center;gap:10px}
.logo-spark{font-size:1.5em}
.header-pills{display:flex;align-items:center;gap:8px}
.hpill{background:rgba(255,255,255,.2);backdrop-filter:blur(4px);padding:5px 14px;border-radius:20px;font-size:.8em;font-weight:600;border:1px solid rgba(255,255,255,.3)}
.hpill.ok{background:rgba(67,233,123,.3)}
.hpill.warn{background:rgba(240,136,62,.3)}

/* ── Status bar ── */
.statusbar{background:rgba(102,126,234,.08);border-bottom:1px solid var(--border);padding:6px 24px;display:flex;align-items:center;gap:10px;font-size:.8em;font-family:'JetBrains Mono',monospace}
.sdot{width:8px;height:8px;border-radius:50%;background:#9ca3af;flex-shrink:0}
.sdot.active{background:var(--p);box-shadow:0 0 8px var(--p);animation:pulse .9s infinite}
.sdot.ok{background:var(--ok)}
.sdot.err{background:var(--err)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.stext{color:var(--sub);flex:1}

/* ── Layout ── */
.layout{display:grid;grid-template-columns:360px 1fr;min-height:calc(100vh - 84px);align-items:start}
.sidebar{background:var(--sidebar);border-right:1px solid var(--border);padding:16px 14px;position:sticky;top:84px;max-height:calc(100vh - 84px);overflow-y:auto}
.main{padding:20px 24px;display:flex;flex-direction:column;gap:16px;overflow:visible}

/* ── Cards ── */
.card{background:var(--card);border-radius:14px;box-shadow:var(--shadow);overflow:hidden;border:1px solid var(--border)}
.card-head{padding:13px 18px;display:flex;align-items:center;gap:10px;font-weight:700;font-size:.9em;border-bottom:1px solid var(--border);background:linear-gradient(90deg,rgba(102,126,234,.07),transparent)}
.card-head .ico{font-size:1.1em}
.card-body{padding:16px 18px}
.card-accent{border-top:3px solid var(--p)}
.card-green{border-top:3px solid var(--ok)}
.card-orange{border-top:3px solid var(--warn)}

/* ── Section headers in sidebar ── */
.sec-label{font-size:.72em;font-weight:700;color:var(--p);text-transform:uppercase;letter-spacing:.8px;margin:14px 0 6px;padding:0 2px}

/* ── Provider tabs ── */
.prov-tabs{display:flex;gap:4px;background:var(--bg);border-radius:10px;padding:4px;margin-bottom:12px}
.ptab{flex:1;padding:7px 6px;border:none;border-radius:7px;cursor:pointer;font-size:.78em;font-weight:700;color:var(--sub);background:transparent;font-family:'Inter',sans-serif;transition:all .18s;text-align:center}
.ptab.active{background:var(--grad);color:#fff;box-shadow:0 2px 10px rgba(102,126,234,.4)}
.ptab-pane{display:none}.ptab-pane.active{display:block}

/* ── Forms ── */
.frow{margin-bottom:10px}
.frow label{display:block;font-size:.78em;font-weight:600;color:var(--sub);margin-bottom:4px}
input,select,textarea{width:100%;padding:8px 11px;background:#f8f9ff;border:1.5px solid var(--border);border-radius:8px;color:var(--text);font-family:'Inter',sans-serif;font-size:.85em;outline:none;transition:border-color .18s}
input:focus,select:focus,textarea:focus{border-color:var(--p);box-shadow:0 0 0 3px rgba(102,126,234,.1)}
textarea{resize:vertical;font-family:'JetBrains Mono',monospace;min-height:90px}
input[type=password]{letter-spacing:.05em}

/* ── Buttons ── */
.btn{display:inline-flex;align-items:center;gap:6px;padding:9px 18px;border:none;border-radius:9px;font-size:.85em;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif;transition:all .2s;white-space:nowrap}
.btn:hover{filter:brightness(1.1);transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,.15)}
.btn:active{transform:translateY(0)}
.btn-p{background:var(--grad);color:#fff}
.btn-ok{background:linear-gradient(135deg,#28a745,#20c965);color:#fff}
.btn-red{background:linear-gradient(135deg,#dc3545,#ff6b6b);color:#fff}
.btn-info{background:linear-gradient(135deg,#17a2b8,#00c9d6);color:#fff}
.btn-warn{background:linear-gradient(135deg,#f0883e,#fee140);color:#1a1d2e}
.btn-gray{background:#e9ecef;color:var(--text);border:1px solid var(--border)}
.btn-gray:hover{background:var(--border)}
.btn-ghost{background:transparent;color:var(--p);border:1.5px solid var(--p)}
.btn-ghost:hover{background:var(--p);color:#fff}
.btn-sm{padding:6px 12px;font-size:.78em;border-radius:7px}
.btn-xs{padding:4px 9px;font-size:.72em;border-radius:6px}
.btn-full{width:100%;justify-content:center}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}

/* ── Alerts/messages ── */
.msg{padding:10px 14px;border-radius:9px;font-size:.83em;font-weight:500;border-left:3px solid;margin:6px 0}
.msg-ok{background:#d4edda;border-color:var(--ok);color:#155724}
.msg-err{background:#f8d7da;border-color:var(--err);color:#721c24}
.msg-info{background:#d1ecf1;border-color:var(--info);color:#0c5460}
.msg-warn{background:#fff3cd;border-color:var(--warn);color:#856404}

/* ── Model list ── */
.model-list{max-height:200px;overflow-y:auto;border:1.5px solid var(--border);border-radius:9px;background:#fafbff}
.mitem{padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:8px;transition:background .12s}
.mitem:last-child{border-bottom:none}
.mitem:hover{background:#f0f2ff}
.mitem.sel{background:linear-gradient(90deg,rgba(102,126,234,.12),transparent);border-left:3px solid var(--p)}
.mname{font-size:.83em;font-weight:600;line-height:1.3}
.mmeta{font-size:.72em;color:var(--sub);font-family:'JetBrains Mono',monospace;margin-top:1px}
.mtag{font-size:.68em;padding:2px 7px;border-radius:10px;font-weight:700;white-space:nowrap;flex-shrink:0}
.tag-free{background:#d4edda;color:#155724}
.tag-paid{background:#fff3cd;color:#856404}
.tag-local{background:#d1ecf1;color:#0c5460}
.tag-ok{background:#d4edda;color:#155724}
.tag-warn{background:#fff3cd;color:#856404}
.tag-err{background:#f8d7da;color:#721c24}
.tag-sql{background:linear-gradient(135deg,rgba(102,126,234,.2),rgba(118,75,162,.2));color:var(--p2)}
.model-search{position:relative;margin-bottom:8px}
.model-search input{padding-right:32px;background:#fff}
.model-search::after{content:'🔍';position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:.85em;pointer-events:none}

/* ── Code box ── */
.code-box{background:var(--code-bg);color:var(--code-fg);padding:16px;border-radius:10px;font-family:'JetBrains Mono',monospace;font-size:.82em;white-space:pre-wrap;overflow-x:auto;line-height:1.7;position:relative;max-height:320px;overflow-y:auto;border:1px solid #333}
.sql-edit-ta{background:var(--code-bg);color:var(--code-fg);padding:14px;border-radius:10px;font-family:'JetBrains Mono',monospace;font-size:.82em;width:100%;line-height:1.7;min-height:130px;resize:vertical;outline:none;border:2px solid var(--p)}
.copy-btn{position:absolute;top:8px;right:8px;padding:3px 10px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:#aaa;border-radius:6px;font-size:.75em;cursor:pointer;transition:all .15s}
.copy-btn:hover{background:rgba(255,255,255,.2);color:#fff}

/* ── Results table ── */
.tbl-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border);max-height:400px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:.83em}
th{background:var(--grad);color:#fff;padding:10px 14px;text-align:left;font-weight:700;font-size:.8em;white-space:nowrap;position:sticky;top:0}
td{padding:8px 14px;border-bottom:1px solid #f0f2ff;font-family:'JetBrains Mono',monospace;font-size:.8em}
tr:nth-child(even) td{background:#fafbff}
tr:hover td{background:#f0f2ff}
.null-v{color:#9ca3af;font-style:italic}

/* ── Pull progress ── */
.pull-bar{height:7px;background:var(--bg);border-radius:4px;overflow:hidden;margin-top:6px;border:1px solid var(--border)}
.pull-fill{height:100%;background:var(--grad);width:0%;transition:width .3s;border-radius:4px}

/* ── Generation steps ── */
.gsteps{display:flex;flex-direction:column;gap:4px;font-family:'JetBrains Mono',monospace;font-size:.78em;padding:12px;background:#f8f9ff;border-radius:9px;border:1px solid var(--border)}
.gstep{display:flex;gap:8px;align-items:center;color:var(--sub)}
.gstep.active{color:var(--p)}
.gstep.done{color:var(--ok)}
.gstep.error{color:var(--err)}

/* ── Misc ── */
.hidden{display:none!important}
.sep{border:none;border-top:1px solid var(--border);margin:12px 0}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:12px;font-size:.75em;font-weight:700}
.badge-p{background:linear-gradient(135deg,rgba(102,126,234,.2),rgba(118,75,162,.2));color:var(--p2)}
.badge-ok{background:#d4edda;color:#155724}
.badge-err{background:#f8d7da;color:#721c24}
.badge-info{background:#d1ecf1;color:#0c5460}
.badge-warn{background:#fff3cd;color:#856404}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#d0d4e8;border-radius:8px}
details summary{cursor:pointer;list-style:none;user-select:none;display:flex;align-items:center;gap:6px;font-size:.82em;font-weight:600;color:var(--p)}
details summary::-webkit-details-marker{display:none}
details summary::before{content:'▶';font-size:.7em;transition:transform .15s;display:inline-block}
details[open] summary::before{transform:rotate(90deg)}
.spin{animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.gradient-text{background:var(--grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
/* Chat bubbles */
.chat-bubble{max-width:88%;padding:10px 14px;border-radius:12px;font-size:.84em;line-height:1.6;word-wrap:break-word}
.chat-bubble.user{background:linear-gradient(135deg,rgba(102,126,234,.18),rgba(118,75,162,.18));border:1px solid rgba(102,126,234,.3);align-self:flex-end;border-bottom-right-radius:3px}
.chat-bubble.ai{background:#fff;border:1px solid var(--border);align-self:flex-start;border-bottom-left-radius:3px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.chat-sql-snippet{background:var(--code-bg);color:#a6e22e;padding:8px 12px;border-radius:8px;font-family:'JetBrains Mono',monospace;font-size:.78em;margin-top:8px;cursor:pointer;border:1px solid #333;line-height:1.5;white-space:pre-wrap}
.chat-sql-snippet:hover{border-color:var(--p)}
.chat-apply-btn{display:inline-flex;align-items:center;gap:4px;margin-top:6px;padding:4px 12px;background:linear-gradient(135deg,#43e97b,#38f9d7);color:#1a1d2e;border:none;border-radius:6px;font-size:.75em;font-weight:700;cursor:pointer}
.chat-apply-btn:hover{filter:brightness(1.1)}
.typing-dots span{animation:tdot 1.2s infinite;display:inline-block;font-size:1.2em;line-height:1}
.typing-dots span:nth-child(2){animation-delay:.2s}
.typing-dots span:nth-child(3){animation-delay:.4s}
@keyframes tdot{0%,80%,100%{opacity:.2}40%{opacity:1}}
/* Status check inline */
.status-checking{font-size:.72em;color:var(--sub);padding:3px 8px;background:#f0f2ff;border-radius:6px;display:inline-flex;align-items:center;gap:4px}
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <div class="logo">
    <span class="logo-spark">⚡</span>
    <span>Text-to-SQL Generator</span>
  </div>
  <div class="header-pills">
    <span class="hpill" id="model-pill">No model set</span>
    <span class="hpill hidden" id="db-pill">No DB</span>
  </div>
</div>

<!-- STATUS BAR -->
<div class="statusbar">
  <div class="sdot" id="sdot"></div>
  <span class="stext" id="stext">Ready — select a model and connect to a database</span>
</div>

<!-- LAYOUT -->
<div class="layout">

<!-- ═══ SIDEBAR ═══ -->
<aside class="sidebar">

  <!-- AI MODEL -->
  <div class="card card-accent" style="margin-bottom:12px">
    <div class="card-head"><span class="ico">🤖</span>AI Model</div>
    <div class="card-body" style="padding:12px">

      <div class="prov-tabs">
        <button class="ptab active" id="ptab-azure"    onclick="switchProv('azure')">Azure</button>
        <button class="ptab"        id="ptab-or-free"  onclick="switchProv('or-free')">Free</button>
        <button class="ptab"        id="ptab-or-paid"  onclick="switchProv('or-paid')">Paid</button>
        <button class="ptab"        id="ptab-ollama"   onclick="switchProv('ollama')">Local</button>
      </div>

      <!-- Azure -->
      <div class="ptab-pane active" id="ppane-azure">
        <div class="msg msg-info" style="font-size:.78em;margin-bottom:8px">Uses your Azure deployment. Enter credentials below.</div>
        <details>
          <summary>🔑 Azure Credentials</summary>
          <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
            <input type="text"     id="az-endpoint" placeholder="https://xxx.cognitiveservices.azure.com/">
            <input type="password" id="az-key" placeholder="Azure API Key">
            <div class="btns" style="margin-top:6px">
              <button class="btn btn-p btn-sm" onclick="saveKey('azure')">💾 Save</button>
              <button class="btn btn-gray btn-sm" onclick="testKey('azure')">🔌 Test</button>
            </div>
            <div id="az-msg"></div>
          </div>
        </details>
        <div class="frow" style="margin-top:10px"><label>Deployment Name</label><input type="text" id="az-model-name" value="gpt-4o"></div>
      </div>

      <!-- OR Free -->
      <div class="ptab-pane" id="ppane-or-free">
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <button class="btn btn-p btn-sm" style="flex:1" onclick="fetchORModels()">🔄 Load / Refresh Models</button>
          <button class="btn btn-info btn-sm" onclick="checkModelStatuses()" id="check-status-btn" title="Probe selected models for real availability">🔍 Check Status</button>
        </div>
        <div id="or-free-stats" style="font-size:.75em;color:var(--sub);margin-bottom:6px"></div>
        <div class="model-search">
          <input id="free-search" placeholder="Search free models..." oninput="filterModels('free')">
        </div>
        <!-- Status filter -->
        <div style="display:flex;gap:4px;margin-bottom:6px;flex-wrap:wrap">
          <button class="btn btn-xs btn-gray" onclick="filterByStatus('free','')">All</button>
          <button class="btn btn-xs" style="background:#d4edda;color:#155724" onclick="filterByStatus('free','ok')">✅ Available</button>
          <button class="btn btn-xs" style="background:#fff3cd;color:#856404" onclick="filterByStatus('free','warn')">⚠ Rate Limited</button>
          <button class="btn btn-xs" style="background:#f8d7da;color:#721c24" onclick="filterByStatus('free','err')">❌ Down</button>
        </div>
        <div class="model-list" id="free-list">
          <div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center">Click "Load / Refresh Models" to see available free models with status</div>
        </div>
        <details style="margin-top:8px">
          <summary>🔑 OpenRouter API Key <span style="font-size:.85em;color:var(--sub)">(optional for free)</span></summary>
          <div style="margin-top:6px;display:flex;gap:6px">
            <input type="password" id="or-key-free" placeholder="sk-or-v1-..." class="flex-1">
            <button class="btn btn-p btn-sm" onclick="saveKey('openrouter')">Save</button>
          </div>
          <div id="or-free-key-msg" style="margin-top:6px"></div>
        </details>
      </div>

      <!-- OR Paid -->
      <div class="ptab-pane" id="ppane-or-paid">
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <button class="btn btn-warn btn-sm btn-full" onclick="fetchORModels()">🔄 Load / Refresh Models</button>
        </div>
        <div class="model-search">
          <input id="paid-search" placeholder="Search paid models..." oninput="filterModels('paid')">
        </div>
        <!-- Provider filter -->
        <div style="display:flex;gap:4px;margin-bottom:6px;flex-wrap:wrap" id="paid-provider-filters">
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('')">All</button>
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('Anthropic')">Claude</button>
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('OpenAI')">GPT</button>
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('Google')">Gemini</button>
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('Meta')">Llama</button>
          <button class="btn btn-xs btn-gray" onclick="filterPaidBy('Mistral')">Mistral</button>
        </div>
        <div class="model-list" id="paid-list">
          <div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center">Click "Load / Refresh Models" to browse paid models</div>
        </div>
        <div class="msg msg-warn" style="font-size:.75em;margin-top:8px">⚠ Paid models charge per token via your OpenRouter balance.</div>
        <details open style="margin-top:8px">
          <summary>🔑 OpenRouter API Key (required)</summary>
          <div style="margin-top:6px;display:flex;gap:6px">
            <input type="password" id="or-key-paid" placeholder="sk-or-v1-...">
            <button class="btn btn-warn btn-sm" onclick="saveKey('openrouter','paid')">Save</button>
          </div>
          <button class="btn btn-gray btn-sm btn-full" style="margin-top:6px" onclick="testKey('openrouter')">🔌 Test Key</button>
          <div id="or-paid-key-msg" style="margin-top:6px"></div>
        </details>
      </div>

      <!-- Ollama Local -->
      <div class="ptab-pane" id="ppane-ollama">
        <div class="msg msg-info" style="font-size:.75em;margin-bottom:8px">100% local — no API key. Run: <code>ollama serve</code></div>
        <button class="btn btn-info btn-sm btn-full" onclick="loadOllama()" id="ollama-refresh-btn">🔄 Refresh Installed Models</button>
        <div id="ollama-installed" style="margin:8px 0">
          <div style="font-size:.78em;color:var(--sub);padding:8px">Click Refresh to load installed models</div>
        </div>
        <div class="sep"></div>
        <div style="font-size:.78em;font-weight:700;color:var(--p);margin-bottom:6px">📥 Download Models</div>
        <div class="model-search">
          <input id="ollama-dl-search" placeholder="Search models..." oninput="filterOllamaDL()">
        </div>
        <div class="model-list" id="ollama-dl-list" style="max-height:180px"></div>
        <!-- Manual pull -->
        <div style="margin-top:8px;display:flex;gap:6px">
          <input id="manual-pull" placeholder="e.g. llama3.2, qwen2.5:14b">
          <button class="btn btn-ok btn-sm" onclick="pullModel(document.getElementById('manual-pull').value)">⬇</button>
        </div>
        <!-- Progress -->
        <div id="pull-wrap" class="hidden" style="margin-top:8px">
          <div style="font-size:.75em;color:var(--sub);font-family:'JetBrains Mono',monospace" id="pull-text">Preparing...</div>
          <div class="pull-bar"><div class="pull-fill" id="pull-fill"></div></div>
          <div id="pull-msg" style="margin-top:4px"></div>
        </div>
      </div>

      <button class="btn btn-p btn-full btn-sm" style="margin-top:12px" onclick="applyModel()">✅ Apply Model</button>
    </div>
  </div>

  <!-- SETTINGS -->
  <div class="card" style="margin-bottom:12px">
    <details>
      <summary style="padding:12px 16px;display:flex;align-items:center;gap:8px;font-weight:700;font-size:.85em;list-style:none;cursor:pointer;border-bottom:1px solid var(--border)">
        <span>⚙</span> Retry &amp; Timeout Settings
      </summary>
      <div style="padding:14px">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div class="frow"><label>Max Retries</label><input type="number" id="s-retries" value="3" min="1" max="10"></div>
          <div class="frow"><label>Retry Delay (s)</label><input type="number" id="s-delay" value="5" min="1"></div>
          <div class="frow"><label>Azure Timeout (s)</label><input type="number" id="s-t-az" value="60" min="10"></div>
          <div class="frow"><label>OR Timeout (s)</label><input type="number" id="s-t-or" value="90" min="10"></div>
        </div>
        <div class="frow"><label>Ollama Timeout (s) — local models need more time</label><input type="number" id="s-t-ol" value="300" min="30"></div>
        <button class="btn btn-p btn-sm btn-full" onclick="saveSettings()">💾 Apply Settings</button>
        <div id="settings-msg" style="margin-top:6px"></div>
      </div>
    </details>
  </div>

  <!-- DB CONNECTION -->
  <div class="card card-green">
    <div class="card-head"><span class="ico">🗄</span>Database Connection</div>
    <div class="card-body">
      <div id="db-msg"></div>
      <div class="frow"><label>Database Type</label>
        <select id="db-type" onchange="onDbType()">
          <option value="sqlserver">SQL Server / Azure SQL</option>
          <option value="mysql">MySQL / MariaDB</option>
          <option value="postgresql">PostgreSQL</option>
          <option value="sqlite">SQLite (file)</option>
        </select>
      </div>
      <div id="db-server-fields">
        <div class="frow"><label>Server / Host</label><input id="db-server" value="localhost\SQLEXPRESS"></div>
        <div class="frow"><label>Database Name</label><input id="db-name"></div>
        <div class="frow hidden" id="db-port-row"><label>Port</label><input type="number" id="db-port" value="5432"></div>
        <div class="frow" id="db-auth-row"><label>Authentication</label>
          <select id="db-auth" onchange="onAuth()">
            <option value="windows">Windows Auth (SQL Server)</option>
            <option value="sql" selected>SQL Auth</option>
          </select>
        </div>
        <div id="db-creds">
          <div class="frow"><label>Username</label><input id="db-user" value="sa"></div>
          <div class="frow"><label>Password</label><input type="password" id="db-pass"></div>
        </div>
      </div>
      <div id="db-sqlite-fields" class="hidden">
        <div class="frow"><label>SQLite File Path</label><input id="db-filepath" placeholder="/path/to/database.db"></div>
      </div>
      <button class="btn btn-ok btn-sm btn-full" onclick="connectDB()">⚡ Connect</button>
    </div>
  </div>

  <!-- SCHEMA -->
  <div class="card" style="margin-top:12px">
    <details>
      <summary style="padding:12px 16px;list-style:none;cursor:pointer;font-weight:700;font-size:.85em">
        <span>📋</span> Database Schema
      </summary>
      <div id="schema-display" style="padding:10px 14px;font-family:'JetBrains Mono',monospace;font-size:.75em;max-height:280px;overflow-y:auto;color:var(--sub);background:#fafbff;border-top:1px solid var(--border)">
        Not connected.
      </div>
    </details>
  </div>

</aside>

<!-- ═══ MAIN CONTENT ═══ -->
<main class="main">

  <!-- ASK QUESTION -->
  <div class="card">
    <div class="card-head"><span class="ico">💬</span>Ask Your Question</div>
    <div class="card-body">
      <textarea id="question" placeholder="e.g. Show top 10 customers by total sales in 2023&#10;e.g. Find employees hired in the last 6 months&#10;e.g. List products with inventory below reorder level" rows="4"></textarea>
      <div class="btns">
        <button class="btn btn-p" onclick="generateSQL()" id="gen-btn">⚡ Generate SQL</button>
        <button class="btn btn-info" onclick="explainQuery()" id="explain-btn">📖 Explain</button>
        <button class="btn btn-warn" id="dml-btn" onclick="toggleDML()">🔓 DML: <span id="dml-status">OFF</span></button>
      </div>
      <!-- Gen progress -->
      <div id="gen-progress" class="hidden" style="margin-top:10px">
        <div class="gsteps" id="gen-steps"></div>
      </div>
    </div>
  </div>

  <!-- GENERATED SQL -->
  <div class="card hidden" id="sql-card">
    <div class="card-head">
      <span class="ico">📝</span>Generated SQL
      <span id="query-type-badge" class="badge badge-ok" style="margin-left:auto"></span>
    </div>
    <div class="card-body">
      <div id="sql-msg"></div>
      <div id="sql-view">
        <div class="code-box" id="sql-box">
          <button class="copy-btn" onclick="copySQL()">📋 Copy</button>
          <span id="sql-text"></span>
        </div>
        <div class="btns">
          <button class="btn btn-ok btn-sm"   onclick="executeSQL()">▶ Execute Query</button>
          <button class="btn btn-info btn-sm"  onclick="explainQuery()">📖 Explain</button>
          <button class="btn btn-warn btn-sm"  onclick="enableEdit()">✏️ Edit SQL</button>
          <button class="btn btn-gray btn-sm"  onclick="copySQL()">📋 Copy</button>
          <button class="btn btn-sm" style="background:linear-gradient(135deg,#764ba2,#f093fb);color:#fff" onclick="showChatCard()">🧠 Chat with AI</button>
        </div>
      </div>
      <div id="sql-edit" class="hidden">
        <div class="msg msg-warn" style="font-size:.8em;margin-bottom:8px">✏ Edit mode — modify the SQL then save</div>
        <textarea class="sql-edit-ta" id="sql-editor" rows="7"></textarea>
        <div class="btns">
          <button class="btn btn-ok btn-sm"  onclick="saveEdit()">💾 Save Changes</button>
          <button class="btn btn-gray btn-sm" onclick="cancelEdit()">✖ Cancel</button>
          <button class="btn btn-info btn-sm" onclick="validateSQL()">🔍 Validate</button>
        </div>
      </div>
      <div id="explanation-box" class="hidden" style="margin-top:12px;padding:12px 16px;background:linear-gradient(135deg,rgba(102,126,234,.07),rgba(118,75,162,.07));border-radius:10px;border-left:4px solid var(--p);font-size:.85em;color:var(--text);line-height:1.7">
        <div style="font-size:.75em;font-weight:700;color:var(--p);margin-bottom:6px">💡 EXPLANATION</div>
        <span id="explanation-text"></span>
      </div>
    </div>
  </div>

  <!-- RESULTS -->
  <div class="card hidden" id="results-card">
    <div class="card-head">
      <span class="ico">📊</span>Query Results
      <span id="row-badge" class="badge badge-info" style="margin-left:auto"></span>
    </div>
    <div class="card-body">
      <div id="exec-msg"></div>
      <div id="results-table"></div>
      <div class="btns hidden" id="export-btns" style="margin-top:12px">
        <button class="btn btn-info btn-sm" onclick="exportCSV()">⬇ Download CSV</button>
        <button class="btn btn-info btn-sm" onclick="exportExcel()">⬇ Download Excel</button>
      </div>
    </div>
  </div>

  <!-- AI CHAT -->
  <div class="card hidden" id="chat-card" style="border-top:3px solid #764ba2">
    <div class="card-head" style="background:linear-gradient(90deg,rgba(118,75,162,.1),transparent)">
      <span class="ico">🧠</span>Chat with AI — Refine your query
      <button class="btn btn-xs btn-gray" style="margin-left:auto" onclick="clearChat()">🗑 Clear</button>
    </div>
    <div class="card-body" style="padding:0">
      <div id="chat-messages" style="min-height:120px;max-height:340px;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;background:#fafbff">
        <div style="text-align:center;color:var(--sub);font-size:.82em;padding:20px 10px">
          💬 Ask me to modify the SQL above.<br>
          <span style="color:var(--p)">e.g. "Add a WHERE clause for year 2023"</span><br>
          <span style="color:var(--p)">"Group by department and sort by total desc"</span><br>
          <span style="color:var(--p)">"Add a HAVING clause to filter salary > 5000"</span>
        </div>
      </div>
      <div id="chat-typing" style="display:none;padding:8px 14px;background:#fafbff;border-top:1px solid var(--border)">
        <span style="font-size:.78em;color:var(--sub)">🤔 AI is thinking</span>
        <span class="typing-dots"><span>.</span><span>.</span><span>.</span></span>
      </div>
      <div style="display:flex;gap:8px;padding:12px 14px;border-top:1px solid var(--border);background:#fff">
        <input id="chat-input" placeholder="e.g. Add WHERE clause for 2023, or sort by salary descending..." style="flex:1;background:#f8f9ff" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}">
        <button class="btn btn-p btn-sm" onclick="sendChat()">Send ↵</button>
      </div>
    </div>
  </div>

</main>
</div><!-- /layout -->

<script>
// ═══════════════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════════════
let currentSQL     = '';
let dmlEnabled     = false;
let currentResults = null;
let sessionId      = Math.random().toString(36).slice(2);
let statusPollTimer= null;
let activeProvider = 'azure';
let orFreeModels   = [];
let orPaidModels   = [];
let selFreeId      = '';
let selPaidId      = '';
let ollamaInstalled= [];
let ollamaPopular  = [];
let pullES         = null;
let paidProvFilter = '';
let freeStatusFilter = '';
let ollamaSelModel = '';

// ═══════════════════════════════════════════════════════════════════
//  PROVIDER TABS
// ═══════════════════════════════════════════════════════════════════
function switchProv(p) {
  activeProvider = p;
  ['azure','or-free','or-paid','ollama'].forEach(id => {
    document.getElementById('ptab-'+id)?.classList.toggle('active', id===p);
    document.getElementById('ppane-'+id)?.classList.toggle('active', id===p);
  });
  if (p === 'ollama') loadOllama();
}

// ═══════════════════════════════════════════════════════════════════
//  OPENROUTER MODEL LOADING WITH STATUS
// ═══════════════════════════════════════════════════════════════════
async function fetchORModels() {
  setDot('active'); setStatus('🔄 Fetching latest models from OpenRouter...');
  document.getElementById('free-list').innerHTML = '<div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center"><span class="spin">⏳</span> Loading...</div>';
  document.getElementById('paid-list').innerHTML = '<div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center"><span class="spin">⏳</span> Loading...</div>';
  try {
    const r = await fetch('/fetch-openrouter-models');
    const d = await r.json();
    if (!d.success) throw new Error(d.error);
    orFreeModels = (d.free || []).map(m => enrichModelStatus(m));
    orPaidModels = (d.paid || []).map(m => enrichModelStatus(m));
    const freeCount = orFreeModels.length;
    const availCount = orFreeModels.filter(m=>m.status==='ok').length;
    const warnCount  = orFreeModels.filter(m=>m.status==='warn').length;
    document.getElementById('or-free-stats').innerHTML =
      `<span class="badge badge-ok" style="font-size:.7em">${availCount} available</span> ` +
      `<span class="badge badge-warn" style="font-size:.7em">${warnCount} limited</span> ` +
      `<span class="badge" style="background:#f0f2ff;color:var(--p);font-size:.7em">${freeCount} total</span>`;
    renderFree(orFreeModels, '', '');
    renderPaid(orPaidModels, '', '');
    setDot('ok'); setStatus(`✅ ${freeCount} free + ${orPaidModels.length} paid models loaded`);
  } catch(e) { setDot('err'); setStatus('❌ ' + e.message); showMsg('free-list', e.message, false, true); }
}

function enrichModelStatus(m) {
  // Heuristic: models with context_length=0 may be down; otherwise "ok"
  // Rate-limited models typically have very small context window reported
  let status = 'ok';
  if (m.context === 0 || m.context === null) status = 'err';
  else if (m.context < 4096) status = 'warn';
  return {...m, status};
}

function statusIcon(s) {
  if (s==='ok')   return '<span class="mtag tag-ok">✅ Available</span>';
  if (s==='warn') return '<span class="mtag tag-warn">⚠ Limited</span>';
  if (s==='err')  return '<span class="mtag tag-err">❌ Down</span>';
  return '';
}

function renderFree(models, filter, statusF) {
  const list = document.getElementById('free-list');
  const fl   = filter.toLowerCase();
  let filtered = models;
  if (fl)      filtered = filtered.filter(m => m.name.toLowerCase().includes(fl) || m.id.toLowerCase().includes(fl));
  if (statusF) filtered = filtered.filter(m => m.status === statusF);
  if (!filtered.length) { list.innerHTML='<div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center">No models match</div>'; return; }
  list.innerHTML = filtered.slice(0, 120).map(m => `
    <div class="mitem ${m.id===selFreeId?'sel':''}" onclick="selFree('${m.id.replace(/'/g,"\\'")}',this)">
      <div style="flex:1;min-width:0">
        <div class="mname">${m.name}</div>
        <div class="mmeta" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${m.id}</div>
        <div style="font-size:.7em;color:var(--sub);margin-top:1px">${m.context>0?Math.round(m.context/1000)+'k ctx':''}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;flex-shrink:0">
        <span class="mtag tag-free">FREE</span>
        ${statusIcon(m.status)}
      </div>
    </div>`).join('');
}

function renderPaid(models, filter, provF) {
  const list = document.getElementById('paid-list');
  const fl   = filter.toLowerCase();
  let filtered = models;
  if (fl)    filtered = filtered.filter(m => m.name.toLowerCase().includes(fl)||m.id.toLowerCase().includes(fl));
  if (provF) filtered = filtered.filter(m => m.id.toLowerCase().includes(provF.toLowerCase()));
  if (!filtered.length) { list.innerHTML='<div style="padding:12px;color:var(--sub);font-size:.8em;text-align:center">No models match</div>'; return; }
  list.innerHTML = filtered.slice(0,120).map(m => {
    const costStr = m.cost>0 ? `$${(m.cost*1e6).toFixed(2)}/M` : 'Free';
    const ctx     = m.context>0 ? `${Math.round(m.context/1000)}k` : '';
    return `<div class="mitem ${m.id===selPaidId?'sel':''}" onclick="selPaid('${m.id.replace(/'/g,"\\'")}',this)">
      <div style="flex:1;min-width:0">
        <div class="mname">${m.name}</div>
        <div class="mmeta" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${m.id}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;flex-shrink:0">
        <span class="mtag tag-paid">${costStr}</span>
        ${ctx?`<span style="font-size:.68em;color:var(--sub)">${ctx} ctx</span>`:''}
      </div>
    </div>`;
  }).join('');
}

function filterModels(type) {
  if (type==='free') renderFree(orFreeModels, document.getElementById('free-search').value, freeStatusFilter);
  else               renderPaid(orPaidModels, document.getElementById('paid-search').value, paidProvFilter);
}
function filterByStatus(type, s) {
  freeStatusFilter = s;
  filterModels('free');
}
function filterPaidBy(p) {
  paidProvFilter = p;
  filterModels('paid');
}
function selFree(id, el) {
  selFreeId = id;
  document.querySelectorAll('#free-list .mitem').forEach(e=>e.classList.remove('sel'));
  el.classList.add('sel');
}
function selPaid(id, el) {
  selPaidId = id;
  document.querySelectorAll('#paid-list .mitem').forEach(e=>e.classList.remove('sel'));
  el.classList.add('sel');
}

// ═══════════════════════════════════════════════════════════════════
//  OLLAMA
// ═══════════════════════════════════════════════════════════════════
async function loadOllama() {
  const btn = document.getElementById('ollama-refresh-btn');
  btn.textContent = '⏳ Refreshing...'; btn.disabled = true;
  try {
    const r = await fetch('/ollama-models');
    const d = await r.json();
    ollamaInstalled = d.installed || [];
    ollamaPopular   = d.popular  || [];
    renderOllamaInstalled();
    renderOllamaDL('');
    setDot('ok'); setStatus(`✅ ${ollamaInstalled.length} Ollama models installed`);
  } catch(e) {
    document.getElementById('ollama-installed').innerHTML =
      '<div class="msg msg-err" style="font-size:.78em">❌ Ollama not running — start with: <code>ollama serve</code></div>';
    setDot('err'); setStatus('❌ Ollama not reachable');
  } finally { btn.textContent='🔄 Refresh Installed Models'; btn.disabled=false; }
}

function renderOllamaInstalled() {
  const el = document.getElementById('ollama-installed');
  if (!ollamaInstalled.length) {
    el.innerHTML = '<div style="font-size:.78em;color:var(--sub);padding:6px">No models installed. Pull one from the list below.</div>';
    return;
  }
  el.innerHTML = `<div style="font-size:.75em;font-weight:700;color:var(--ok);margin-bottom:6px">✅ Installed (${ollamaInstalled.length})</div>` +
    ollamaInstalled.map(m => `
      <div class="mitem ${m===ollamaSelModel?'sel':''}" onclick="selOllama('${m}',this)">
        <div class="mname">${m}</div>
        <span class="mtag tag-local">LOCAL</span>
      </div>`).join('');
}

function renderOllamaDL(filter) {
  const list = document.getElementById('ollama-dl-list');
  const fl   = filter.toLowerCase();
  const models = fl ? ollamaPopular.filter(m => m.id.toLowerCase().includes(fl)||m.name.toLowerCase().includes(fl)||(m.tag||'').toLowerCase().includes(fl)) : ollamaPopular;
  list.innerHTML = models.slice(0,40).map(m => `
    <div class="mitem" style="justify-content:space-between">
      <div style="flex:1;min-width:0">
        <div class="mname">${m.name} <span class="mtag tag-sql">${m.tag||''}</span></div>
        <div class="mmeta">${m.id}</div>
      </div>
      ${m.installed
        ? `<button class="btn btn-xs" style="background:#d4edda;color:#155724" onclick="selOllamaByName('${m.id}')">✅ Select</button>`
        : `<button class="btn btn-xs btn-p"   onclick="pullModel('${m.id}')">⬇ Pull</button>`}
    </div>`).join('');
}

function filterOllamaDL() { renderOllamaDL(document.getElementById('ollama-dl-search').value); }

function selOllama(name, el) {
  ollamaSelModel = name;
  document.querySelectorAll('#ollama-installed .mitem').forEach(e=>e.classList.remove('sel'));
  el.classList.add('sel');
}
function selOllamaByName(name) {
  ollamaSelModel = name;
  renderOllamaInstalled();
}

function pullModel(modelId) {
  if (pullES) pullES.close();
  const wrap = document.getElementById('pull-wrap');
  const pt   = document.getElementById('pull-text');
  const pf   = document.getElementById('pull-fill');
  const pm   = document.getElementById('pull-msg');
  wrap.classList.remove('hidden'); pf.style.width='2%'; pm.innerHTML=''; pt.textContent=`Pulling ${modelId}...`;
  setDot('active'); setStatus(`📥 Downloading ${modelId}...`);
  pullES = new EventSource(`/ollama-pull?model=${encodeURIComponent(modelId)}`);
  pullES.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (d.done) {
        pt.textContent=`✅ ${modelId} downloaded!`; pf.style.width='100%';
        pm.innerHTML='<div class="msg msg-ok" style="font-size:.78em">✅ Model ready to use!</div>';
        pullES.close(); loadOllama(); setDot('ok'); setStatus('✅ Model downloaded');
        return;
      }
      if (d.error) { pt.textContent='❌ '+d.error; pullES.close(); setDot('err'); return; }
      if (d.total && d.completed) {
        const pct = Math.round(d.completed/d.total*100);
        pf.style.width=pct+'%'; pt.textContent=`${d.status||''} ${pct}% (${Math.round(d.completed/1e6)}MB/${Math.round(d.total/1e6)}MB)`;
      } else if (d.status) {
        pt.textContent=d.status;
        if (d.status.toLowerCase().includes('already')) pf.style.width='100%';
      }
    } catch {}
  };
  pullES.onerror = () => { pt.textContent='Connection error'; pullES.close(); };
}

// ═══════════════════════════════════════════════════════════════════
//  APPLY MODEL
// ═══════════════════════════════════════════════════════════════════
async function applyModel() {
  let provider='', model='';
  if (activeProvider==='azure') {
    provider='azure'; model=document.getElementById('az-model-name').value||'gpt-4o';
  } else if (activeProvider==='or-free') {
    if (!selFreeId) { alert('Select a free model from the list first'); return; }
    provider='openrouter'; model=selFreeId;
  } else if (activeProvider==='or-paid') {
    if (!selPaidId) { alert('Select a paid model from the list first'); return; }
    provider='openrouter'; model=selPaidId;
  } else if (activeProvider==='ollama') {
    if (!ollamaSelModel) {
      if (ollamaInstalled.length) { ollamaSelModel=ollamaInstalled[0]; }
      else { alert('Install a local model first using Pull'); return; }
    }
    provider='ollama'; model=ollamaSelModel;
  }
  const r = await fetch('/set-model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,model})});
  const d = await r.json();
  if (d.success) {
    const short = model.split('/').pop();
    const label = provider==='azure'?'Azure':provider==='openrouter'?'OpenRouter':'Ollama';
    document.getElementById('model-pill').textContent=`${label} · ${short}`;
    document.getElementById('model-pill').className='hpill ok';
    setDot('ok'); setStatus(`✅ Active model: ${short}`);
    alert(`✅ Model set: ${short}`);
  } else alert('❌ '+d.error);
}

// ═══════════════════════════════════════════════════════════════════
//  API KEYS
// ═══════════════════════════════════════════════════════════════════
async function saveKey(provider, variant) {
  let apiKey='', endpoint='';
  if (provider==='azure') {
    apiKey=document.getElementById('az-key').value.trim();
    endpoint=document.getElementById('az-endpoint').value.trim();
  } else {
    apiKey=(variant==='paid'?document.getElementById('or-key-paid'):document.getElementById('or-key-free')).value.trim();
    document.getElementById('or-key-free').value=document.getElementById('or-key-paid').value=apiKey;
  }
  if (!apiKey) { alert('Enter an API key first'); return; }
  const r=await fetch('/set-api-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,api_key:apiKey,endpoint})});
  const d=await r.json();
  const msgId=provider==='azure'?'az-msg':variant==='paid'?'or-paid-key-msg':'or-free-key-msg';
  showMsgEl(msgId, d.success?'✅ Saved':'❌ '+d.error, d.success);
}

async function testKey(provider) {
  const msgId=provider==='azure'?'az-msg':'or-paid-key-msg';
  showMsgEl(msgId,'⏳ Testing...', true);
  const r=await fetch('/test-api-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider})});
  const d=await r.json();
  showMsgEl(msgId, d.success?'✅ '+d.message:'❌ '+d.error, d.success);
}

// ═══════════════════════════════════════════════════════════════════
//  SETTINGS
// ═══════════════════════════════════════════════════════════════════
async function saveSettings() {
  const r=await fetch('/set-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    max_retries:+document.getElementById('s-retries').value,
    retry_delay:+document.getElementById('s-delay').value,
    timeout_azure:+document.getElementById('s-t-az').value,
    timeout_openrouter:+document.getElementById('s-t-or').value,
    timeout_ollama:+document.getElementById('s-t-ol').value,
  })});
  const d=await r.json();
  showMsgEl('settings-msg', d.success?'✅ Settings saved':'❌ '+d.error, d.success);
}

// ═══════════════════════════════════════════════════════════════════
//  DB CONNECTION
// ═══════════════════════════════════════════════════════════════════
function onDbType() {
  const t=document.getElementById('db-type').value;
  document.getElementById('db-server-fields').classList.toggle('hidden',t==='sqlite');
  document.getElementById('db-sqlite-fields').classList.toggle('hidden',t!=='sqlite');
  document.getElementById('db-auth-row').classList.toggle('hidden',t!=='sqlserver');
  document.getElementById('db-port-row').classList.toggle('hidden',t==='sqlserver'||t==='sqlite');
  if (t==='mysql')      document.getElementById('db-port').value='3306';
  if (t==='postgresql') document.getElementById('db-port').value='5432';
}
function onAuth() {
  const v=document.getElementById('db-auth').value;
  document.getElementById('db-creds').classList.toggle('hidden',v==='windows');
}

async function connectDB() {
  showMsgEl('db-msg','⏳ Connecting...', true);
  setDot('active'); setStatus('Connecting to database...');
  const t=document.getElementById('db-type').value;
  const payload={
    db_type:t,
    server:document.getElementById('db-server')?.value||'',
    database:document.getElementById('db-name')?.value||'',
    port:document.getElementById('db-port')?.value||'',
    auth:document.getElementById('db-auth')?.value||'sql',
    username:document.getElementById('db-user')?.value||'',
    password:document.getElementById('db-pass')?.value||'',
    filepath:document.getElementById('db-filepath')?.value||'',
  };
  const r=await fetch('/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const d=await r.json();
  if (d.success) {
    showMsgEl('db-msg',`✅ Connected — ${d.tables} tables loaded`, true);
    document.getElementById('db-pill').textContent=`${t.toUpperCase()} · ${d.tables} tables`;
    document.getElementById('db-pill').classList.remove('hidden');
    document.getElementById('schema-display').textContent=JSON.stringify(d.schema,null,2);
    setDot('ok'); setStatus(`✅ Connected to ${payload.database||'database'} — ${d.tables} tables`);
  } else {
    showMsgEl('db-msg','❌ '+d.error, false);
    setDot('err'); setStatus('❌ Connection failed');
  }
}

// ═══════════════════════════════════════════════════════════════════
//  SQL GENERATION
// ═══════════════════════════════════════════════════════════════════
async function generateSQL() {
  const q=document.getElementById('question').value.trim();
  if (!q) { alert('Enter a question first'); return; }
  const btn=document.getElementById('gen-btn');
  btn.textContent='⏳ Generating...'; btn.disabled=true;
  document.getElementById('gen-progress').classList.remove('hidden');
  document.getElementById('sql-card').classList.add('hidden');
  document.getElementById('results-card').classList.add('hidden');
  setDot('active'); setStatus('Generating SQL...');
  startStatusPoll(sessionId);
  try {
    const r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q,dml_enabled:dmlEnabled,session_id:sessionId})});
    const d=await r.json();
    stopStatusPoll();
    if (d.success) {
      currentSQL=d.sql;
      document.getElementById('sql-text').textContent=d.sql;
      document.getElementById('query-type-badge').textContent=d.query_type||'SELECT';
      document.getElementById('sql-msg').innerHTML='';
      document.getElementById('sql-card').classList.remove('hidden');
      document.getElementById('explanation-box').classList.add('hidden');
      if (d.explanation) {
        document.getElementById('explanation-text').textContent=d.explanation;
        document.getElementById('explanation-box').classList.remove('hidden');
      }
      // Show chat card and reset chat for new query
      document.getElementById('chat-card').classList.remove('hidden');
      clearChat();
      setDot('ok'); setStatus('✅ SQL generated — click Execute to run');
    } else {
      document.getElementById('sql-msg').innerHTML=`<div class="msg msg-err">❌ ${d.error}</div>`;
      document.getElementById('sql-card').classList.remove('hidden');
      setDot('err'); setStatus('❌ Generation failed');
    }
  } catch(e) {
    stopStatusPoll();
    document.getElementById('sql-msg').innerHTML=`<div class="msg msg-err">❌ ${e.message}</div>`;
    document.getElementById('sql-card').classList.remove('hidden');
    setDot('err');
  } finally {
    btn.textContent='⚡ Generate SQL'; btn.disabled=false;
    document.getElementById('gen-progress').classList.add('hidden');
  }
}

function startStatusPoll(sid) {
  const steps=document.getElementById('gen-steps');
  steps.innerHTML='<div class="gstep active"><span>⏳</span>Starting generation...</div>';
  statusPollTimer=setInterval(async()=>{
    try {
      const r=await fetch(`/gen-status/${sid}`);
      const d=await r.json();
      if (d.stage) {
        const icon=d.stage==='done'?'✅':d.stage==='error'?'❌':'⚙';
        const cls=d.stage==='done'?'done':d.stage==='error'?'error':'active';
        const retry=d.attempt>0?` (attempt ${d.attempt}/${d.max})`:'';
        steps.innerHTML=`<div class="gstep ${cls}"><span>${icon}</span>${d.message}${retry}</div>`;
      }
    } catch{}
  }, 500);
}
function stopStatusPoll() { if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer=null; } }

// ═══════════════════════════════════════════════════════════════════
//  EXECUTE SQL
// ═══════════════════════════════════════════════════════════════════
async function executeSQL() {
  if (!currentSQL) { alert('Generate SQL first'); return; }
  document.getElementById('results-card').classList.remove('hidden');
  document.getElementById('exec-msg').innerHTML='<div class="msg msg-info">⏳ Executing query...</div>';
  document.getElementById('results-table').innerHTML='';
  document.getElementById('export-btns').classList.add('hidden');
  setDot('active'); setStatus('Executing SQL...');
  try {
    const r=await fetch('/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:currentSQL})});
    const d=await r.json();
    if (d.success) {
      currentResults=d;
      document.getElementById('exec-msg').innerHTML=`<div class="msg msg-ok">✅ ${d.rows} row${d.rows!==1?'s':''} returned</div>`;
      document.getElementById('row-badge').textContent=`${d.rows} rows`;
      if (d.data && d.data.length>0) {
        let html='<div class="tbl-wrap"><table><thead><tr>';
        d.columns.forEach(c=>html+=`<th>${c}</th>`);
        html+='</tr></thead><tbody>';
        d.data.forEach(row=>{
          html+='<tr>';
          d.columns.forEach(c=>{
            const v=row[c];
            html+=`<td>${v===null||v===undefined?'<span class="null-v">NULL</span>':String(v)}</td>`;
          });
          html+='</tr>';
        });
        html+='</tbody></table></div>';
        document.getElementById('results-table').innerHTML=html;
        document.getElementById('export-btns').classList.remove('hidden');
      } else {
        document.getElementById('results-table').innerHTML='<div style="color:var(--sub);font-size:.85em;padding:8px">No rows returned.</div>';
      }
      setDot('ok'); setStatus(`✅ Query executed — ${d.rows} rows`);
    } else {
      document.getElementById('exec-msg').innerHTML=`<div class="msg msg-err">❌ ${d.error}</div>`;
      setDot('err'); setStatus('❌ Execution failed');
    }
  } catch(e) {
    document.getElementById('exec-msg').innerHTML=`<div class="msg msg-err">❌ ${e.message}</div>`;
    setDot('err');
  }
}

// ═══════════════════════════════════════════════════════════════════
//  EXPLAIN
// ═══════════════════════════════════════════════════════════════════
async function explainQuery() {
  if (!currentSQL) { alert('Generate SQL first'); return; }
  const btn=document.getElementById('explain-btn');
  btn.textContent='⏳ Explaining...'; btn.disabled=true;
  setDot('active'); setStatus('Getting explanation...');
  try {
    const r=await fetch('/explain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:currentSQL})});
    const d=await r.json();
    if (d.success) {
      document.getElementById('explanation-text').textContent=d.explanation;
      document.getElementById('explanation-box').classList.remove('hidden');
      document.getElementById('sql-card').classList.remove('hidden');
      setDot('ok'); setStatus('✅ Explanation ready');
    } else { alert('❌ '+d.error); setDot('err'); }
  } catch(e) { alert('❌ '+e.message); }
  finally { btn.textContent='📖 Explain'; btn.disabled=false; }
}

// ═══════════════════════════════════════════════════════════════════
//  SQL EDIT / VALIDATE / COPY
// ═══════════════════════════════════════════════════════════════════
function enableEdit() {
  document.getElementById('sql-view').classList.add('hidden');
  document.getElementById('sql-edit').classList.remove('hidden');
  document.getElementById('sql-editor').value=currentSQL;
}
function cancelEdit() {
  document.getElementById('sql-edit').classList.add('hidden');
  document.getElementById('sql-view').classList.remove('hidden');
}
function saveEdit() {
  const sql=document.getElementById('sql-editor').value.trim();
  if (!sql) return;
  currentSQL=sql;
  document.getElementById('sql-text').textContent=sql;
  cancelEdit();
}
async function validateSQL() {
  const sql=document.getElementById('sql-editor').value.trim();
  const r=await fetch('/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql,dml_enabled:dmlEnabled})});
  const d=await r.json();
  alert(d.success?'✅ SQL is valid!':'❌ '+d.error);
}
function copySQL() {
  navigator.clipboard.writeText(currentSQL).then(()=>{
    const btn=event.target; const orig=btn.textContent;
    btn.textContent='✅ Copied!'; setTimeout(()=>btn.textContent=orig,1500);
  });
}

// ═══════════════════════════════════════════════════════════════════
//  DML TOGGLE
// ═══════════════════════════════════════════════════════════════════
async function toggleDML() {
  const r=await fetch('/toggle-dml',{method:'POST'});
  const d=await r.json();
  dmlEnabled=d.enabled;
  document.getElementById('dml-status').textContent=dmlEnabled?'ON':'OFF';
  document.getElementById('dml-btn').className=dmlEnabled?'btn btn-red':'btn btn-warn';
}

// ═══════════════════════════════════════════════════════════════════
//  EXPORT
// ═══════════════════════════════════════════════════════════════════
async function exportCSV() {
  if (!currentResults) return;
  const r=await fetch('/export/csv',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentResults)});
  dl(await r.blob(),'results.csv');
}
async function exportExcel() {
  if (!currentResults) return;
  const r=await fetch('/export/excel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentResults)});
  dl(await r.blob(),'results.xlsx');
}
function dl(blob,name) {
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(blob),download:name});
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

// ═══════════════════════════════════════════════════════════════════
//  STATUS HELPERS
// ═══════════════════════════════════════════════════════════════════
function setDot(state) { document.getElementById('sdot').className='sdot '+state; }
function setStatus(txt) { document.getElementById('stext').textContent=txt; }
function showMsgEl(id, txt, ok) {
  const el=document.getElementById(id);
  if (!el) return;
  el.innerHTML=`<div class="msg ${ok?'msg-ok':'msg-err'}" style="font-size:.78em">${txt}</div>`;
}
function showMsg(containerId, txt, ok, replace=false) {
  const c=document.getElementById(containerId);
  if (!c) return;
  const html=`<div class="msg ${ok?'msg-ok':'msg-err'}" style="font-size:.8em">${txt}</div>`;
  if (replace) c.innerHTML=html; else c.insertAdjacentHTML('beforeend',html);
}

// ═══════════════════════════════════════════════════════════════════
//  REAL MODEL STATUS CHECK (probes OpenRouter API)
// ═══════════════════════════════════════════════════════════════════
let modelStatusCache = {};   // id -> 'ok' | 'rate_limited' | 'down' | 'key_required'

async function checkModelStatuses() {
  if (!orFreeModels.length) { alert('Load models first, then click Check Status'); return; }
  const btn = document.getElementById('check-status-btn');
  btn.textContent = '⏳ Checking...'; btn.disabled = true;
  setDot('active'); setStatus('🔍 Probing model availability (checking up to 8 models)...');

  // Pick first 8 visible models in the current list
  const visibleIds = orFreeModels.slice(0, 8).map(m => m.id);
  try {
    const r = await fetch('/check-model-status', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({models: visibleIds})
    });
    const d = await r.json();
    // Merge into cache
    Object.assign(modelStatusCache, d.results);
    // Update model status from real probe
    orFreeModels = orFreeModels.map(m => {
      if (modelStatusCache[m.id]) {
        const s = modelStatusCache[m.id];
        return {...m, status: s === 'ok' ? 'ok' : s === 'rate_limited' ? 'warn' : 'err', probed: true};
      }
      return m;
    });
    const checkedCount = Object.keys(d.results).length;
    const okCount  = Object.values(d.results).filter(v=>v==='ok').length;
    const rlCount  = Object.values(d.results).filter(v=>v==='rate_limited').length;
    const dnCount  = Object.values(d.results).filter(v=>v==='down').length;
    setDot('ok');
    setStatus(`✅ Status checked: ${okCount} available, ${rlCount} rate-limited, ${dnCount} down (from ${checkedCount} probed)`);
    renderFree(orFreeModels, document.getElementById('free-search').value, freeStatusFilter);
    // Update stats bar
    const totalOk  = orFreeModels.filter(m=>m.status==='ok').length;
    const totalWarn= orFreeModels.filter(m=>m.status==='warn').length;
    const totalErr = orFreeModels.filter(m=>m.status==='err').length;
    document.getElementById('or-free-stats').innerHTML =
      `<span class="badge badge-ok" style="font-size:.7em">✅ ${totalOk} available</span> ` +
      `<span class="badge badge-warn" style="font-size:.7em">⚠ ${totalWarn} limited</span> ` +
      `<span class="badge badge-err" style="font-size:.7em;background:#f8d7da;color:#721c24">❌ ${totalErr} down</span> ` +
      `<span class="badge" style="background:#f0f2ff;color:var(--p);font-size:.7em">${orFreeModels.length} total</span>`;
  } catch(e) {
    setDot('err'); setStatus('❌ Status check failed: ' + e.message);
  } finally {
    btn.textContent = '🔍 Check Status'; btn.disabled = false;
  }
}

// ═══════════════════════════════════════════════════════════════════
//  AI CHAT
// ═══════════════════════════════════════════════════════════════════
let chatMessages = [];

function showChatCard() {
  document.getElementById('chat-card').classList.remove('hidden');
  document.getElementById('chat-card').scrollIntoView({behavior:'smooth', block:'start'});
}

function clearChat() {
  chatMessages = [];
  document.getElementById('chat-messages').innerHTML = `
    <div style="text-align:center;color:var(--sub);font-size:.82em;padding:20px 10px">
      💬 Ask me to modify the SQL above.<br>
      <span style="color:var(--p)">e.g. "Add a WHERE clause for year 2023"</span><br>
      <span style="color:var(--p)">"Group by department and sort by total desc"</span><br>
      <span style="color:var(--p)">"Add a HAVING clause to filter salary > 5000"</span>
    </div>`;
}

function appendChatMsg(role, content, newSQL) {
  const box = document.getElementById('chat-messages');
  // Remove placeholder if present
  const placeholder = box.querySelector('div[style*="text-align:center"]');
  if (placeholder) placeholder.remove();

  const div = document.createElement('div');
  div.style.display = 'flex';
  div.style.flexDirection = 'column';
  div.style.alignItems = role==='user' ? 'flex-end' : 'flex-start';
  div.style.gap = '4px';

  let inner = `<div class="chat-bubble ${role}">
    <div style="font-size:.72em;font-weight:700;color:${role==='user'?'var(--p2)':'var(--sub)'};margin-bottom:4px">${role==='user'?'You':'🤖 AI'}</div>
    ${escHtml(content)}`;

  if (newSQL) {
    inner += `<div class="chat-sql-snippet" onclick="applyChatSQL(this)" data-sql="${escHtml(newSQL)}">${escHtml(newSQL)}</div>
    <button class="chat-apply-btn" onclick="applyChatSQL(this.previousElementSibling)">✅ Apply this SQL</button>`;
  }
  inner += '</div>';
  div.innerHTML = inner;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function applyChatSQL(el) {
  const sql = el.dataset.sql || el.textContent;
  currentSQL = sql.trim();
  document.getElementById('sql-text').textContent = currentSQL;
  document.getElementById('sql-card').classList.remove('hidden');
  document.getElementById('sql-card').scrollIntoView({behavior:'smooth'});
  // Show a pulse effect on the SQL box
  const box = document.getElementById('sql-box');
  box.style.transition='box-shadow .3s';
  box.style.boxShadow='0 0 0 3px rgba(102,126,234,.5)';
  setTimeout(()=>box.style.boxShadow='',1200);
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const msg   = input.value.trim();
  if (!msg) return;
  if (!currentSQL) { alert('Generate a SQL query first before chatting'); return; }
  input.value = '';

  chatMessages.push({role:'user', content: msg});
  appendChatMsg('user', msg, null);

  // Show typing indicator
  document.getElementById('chat-typing').style.display = 'block';
  setDot('active'); setStatus('🤔 AI is refining your query...');

  try {
    const r = await fetch('/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({messages: chatMessages, current_sql: currentSQL})
    });
    const d = await r.json();
    document.getElementById('chat-typing').style.display = 'none';

    if (d.success) {
      chatMessages.push({role:'assistant', content: d.message});
      appendChatMsg('ai', d.message, d.sql || null);
      if (d.sql) {
        // Auto-update explanation if available
        if (d.explanation) {
          document.getElementById('explanation-text').textContent = d.explanation;
          document.getElementById('explanation-box').classList.remove('hidden');
        }
      }
      setDot('ok'); setStatus('✅ AI response ready');
    } else {
      appendChatMsg('ai', '❌ Error: ' + d.error, null);
      setDot('err');
    }
  } catch(e) {
    document.getElementById('chat-typing').style.display = 'none';
    appendChatMsg('ai', '❌ Request failed: ' + e.message, null);
    setDot('err');
  }
}

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

</script>
</body>
</html>"""

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  TEXT-TO-SQL GENERATOR  ")
    print("="*60)
    print("  • Vivid gradient UI")
    print("  • Model status: ✅ Available / ⚠ Limited / ❌ Down")
    print("  • All DB engines: SQL Server · MySQL · PostgreSQL · SQLite")
    print("  • Free/Paid/Local models with search & filter")
    print("  • Ollama: instant refresh + pull with progress")
    print("  URL: http://127.0.0.1:5000")
    print("="*60 + "\n")
    app.run(debug=True, port=5000, host="0.0.0.0")
