# Catering SMS Agent

An after-hours catering coordinator for a single restaurant, reachable by SMS. Customers text the restaurant's Twilio number; an LLM-backed agent answers menu/catering questions from a fixed knowledge base, gathers order details, and emails staff a qualified lead summary for follow-up during business hours.

## How it works

- **`app.py`** — Flask app. `/sms` is the Twilio webhook (validates the request signature, runs one conversation turn, replies with TwiML). `/health` is a plain health check.
- **`knowledge_base.py`** — the menu, allergen/dietary tags, and catering policies. This is injected into the system prompt on every request (no RAG/vector DB — the menu is small and stable). **This is the file a non-developer edits to update the menu.**
- **`storage.py`** — SQLite-backed conversation history, keyed by phone number, so it survives restarts and works across multiple worker processes. Also logs each extracted `OrderSummary` (see `order_extraction.py`) so a wrong-looking summary can be debugged later.
- **`llm.py`** — Azure OpenAI client, system prompt construction, and the tool-calling loop (`submit_catering_lead`).
- **`order_extraction.py`** — a second, dedicated Azure OpenAI call that runs right before the lead email is sent. It re-reads the *entire* transcript and produces a schema-guaranteed `OrderSummary` (via structured outputs / `.parse()`), independent of whatever the live `submit_catering_lead` tool call captured mid-conversation. This catches customer corrections (wrong date, changed order) that happened after the tool already fired. Falls back to plain JSON mode if the deployment doesn't support structured outputs, and to the original tool-call data if extraction fails entirely — a failed extraction never blocks the lead email.
- **`email_sender.py`** — builds and sends the branded ("Caterable") HTML staff notification email via SMTP or SendGrid, using the `OrderSummary` (summary fields, itemized order table + total, "needs staff follow-up" callout for open questions) when available. The full conversation is no longer inlined in the email body — it's attached as a PDF instead (see `transcript_pdf.py`).
- **`transcript_pdf.py`** — renders the full conversation as a PDF (customer/assistant visually distinguished), attached to the lead email.
- **`config.py`** — all configuration from environment variables.

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python 3.11+.

### 2. Configure environment variables

```bash
cp .env.example .env
```

Fill in `.env`. See the table below for what each variable is for.

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI resource + deployment used for chat completions |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Twilio account; auth token is used to validate inbound webhook signatures |
| `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET` | Used for any outbound SMS the app sends itself (Twilio's recommended way to authenticate API calls, instead of the account SID/auth token) |
| `TWILIO_PHONE_NUMBER` | The restaurant's Twilio number |
| `STAFF_EMAIL` | Where qualified lead emails are sent |
| `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM` | SMTP email delivery (used if `SENDGRID_API_KEY` isn't set) |
| `SENDGRID_API_KEY` | SendGrid email delivery (used instead of SMTP if set) |
| `DATABASE_PATH` | SQLite file path (default `conversations.db`) |
| `RESTAURANT_NAME` | Shown in the system prompt and email subject |
| `SKIP_TWILIO_SIGNATURE_VALIDATION` | Set `true` only for local testing without a real Twilio signature |

### 3. Create the Azure OpenAI deployment

1. In the [Azure Portal](https://portal.azure.com), create (or use an existing) Azure OpenAI resource.
2. In Azure AI Foundry (or the Azure OpenAI Studio), deploy a chat model that supports both tool calling and structured outputs (e.g. a GPT-4o-class model). Give the deployment a name — that name is what you put in `AZURE_OPENAI_DEPLOYMENT`. If your deployment predates structured-outputs support, `order_extraction.py` automatically falls back to JSON mode, but that's a lower-reliability path (a model-recommended `.parse()`-capable deployment is preferred).
3. Copy the resource's endpoint (e.g. `https://your-resource-name.openai.azure.com`) into `AZURE_OPENAI_ENDPOINT` — **no path or api-version suffix**, the app appends `/openai/v1` itself.
4. Copy an API key from "Keys and Endpoint" into `AZURE_OPENAI_API_KEY`.

### 4. Configure the Twilio number's SMS webhook

1. In the [Twilio Console](https://console.twilio.com), open **Phone Numbers → Manage → Active Numbers** and select your number.
2. Under **Messaging Configuration**, set "A message comes in" to **Webhook**, method **HTTP POST**, and point it at `https://<your-public-url>/sms`.
3. Copy the number's Account SID and Auth Token into `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`.
4. If you want the app to send SMS proactively (rather than only replying via TwiML), create an API Key/Secret pair under **Account → API keys & tokens** and put them in `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET`.

### 5. Run locally with ngrok

```bash
source .venv/bin/activate
python app.py
# in another terminal:
ngrok http 5000
```

Take the `https://...ngrok-free.app` URL ngrok prints, append `/sms`, and paste it into the Twilio webhook config from step 4. Text the Twilio number to test.

For production, run with gunicorn instead of the Flask dev server, e.g. `gunicorn -w 2 -b 0.0.0.0:8000 app:app` (multiple workers are safe — conversation history lives in SQLite, not process memory).

### 6. Editing the menu

Open `knowledge_base.py`. Each menu item is a dictionary in the `MENU` list — copy an existing entry and edit the fields (`name`, `category`, `description`, `price_per_person`, `allergens`, `dietary`). Catering policies (order minimum, lead time, delivery radius, etc.) are in the `CATERING_POLICY` dictionary just below. No other code needs to change — the file is re-read and injected into the system prompt on every request.

**The seeded menu and policies are placeholder data — replace them with your restaurant's real menu before going live.**

## Security

- Never commit `.env` — it holds live secrets (Azure key, Twilio auth token/API secret, SMTP/SendGrid credentials). `.gitignore` already excludes it.
- If a secret is ever accidentally committed, **deleting the file is not enough** — it stays in git history. The fix is to rotate/revoke that credential (regenerate the Azure key, roll the Twilio auth token and API key, reset the SMTP password or SendGrid key) and only then clean up history if needed.
- All inbound `/sms` requests are validated against Twilio's `X-Twilio-Signature` header using `TWILIO_AUTH_TOKEN`; requests that fail validation are rejected with a 403. Only disable this (`SKIP_TWILIO_SIGNATURE_VALIDATION=true`) for local testing, never in production.
