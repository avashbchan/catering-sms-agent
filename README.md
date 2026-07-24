# Catering SMS Agent

An after-hours catering coordinator for a single restaurant, reachable by SMS. Customers text the restaurant's Twilio number; an LLM-backed agent answers menu/catering questions from a fixed knowledge base, gathers order details, and emails staff a qualified lead summary for follow-up during business hours.

## How it works

- **`app.py`** — Flask app. `/sms` is the Twilio webhook (validates the request signature, runs one conversation turn, replies with TwiML). `/health` is a plain health check.
- **`knowledge_base.py`** — the menu, allergen/dietary tags, and catering policies. This is injected into the system prompt on every request (no RAG/vector DB — the menu is small and stable). **This is the file a non-developer edits to update the menu.**
- **`storage.py`** — SQLite-backed conversation history, keyed by phone number, so it survives restarts and works across multiple worker processes. Also logs each extracted `OrderSummary` (see `order_extraction.py`) so a wrong-looking summary can be debugged later.
- **`llm.py`** — Azure OpenAI client, system prompt construction, and the tool-calling loop (`submit_catering_lead`). The `on_lead_submitted` callback returns a `LeadResult`, so a lead the guard blocks feeds the model guidance to continue the conversation rather than a false "sent" confirmation.
- **`lead_gate.py`** — the code-level submission guard (a deliberate safety net independent of the prompt). Before any lead is written/emailed, it requires (1) all five required fields present — a deterministic check over the extracted `OrderSummary` — and (2) an LLM judgment that the customer's latest message says they need no more help. If either fails, no DB write and no email; the conversation continues.
- **`order_extraction.py`** — a second, dedicated Azure OpenAI call that runs right before a lead is finalized. It re-reads the conversation transcript for the current lead (messages since the customer's last submitted lead; see `storage.get_transcript_since_last_lead`) and produces a schema-guaranteed `OrderSummary` (via structured outputs / `.parse()`), independent of whatever the live `submit_catering_lead` tool call captured mid-conversation. This catches customer corrections (wrong date, changed order) that happened after the tool already fired, and feeds the guard's required-field check. Falls back to plain JSON mode if the deployment doesn't support structured outputs.
- **`email_sender.py`** — builds and sends the HTML staff notification email via SMTP or SendGrid, using the `OrderSummary` (itemized table + "needs staff follow-up" callout for open questions) when available. The lead summary sits at the bottom of the email body; the full (per-lead) conversation transcript is attached as a PDF (`transcript_pdf.py`, rendered locally with fpdf2) rather than dumped inline.
- **`business_info.py`** — a small typed mirror of the structured contact fields (name, phone, website, address) from `kb_data/business.md`, so code can import them without parsing markdown. `business.md` stays the source of truth; a non-fatal startup check warns if the two drift.
- **`transcript_pdf.py`** — renders a conversation transcript to a PDF (in-process, no external service) for the lead-email attachment.
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

## Testing the prompt (promptfoo evals)

The `evals/` directory holds a [promptfoo](https://promptfoo.dev) suite that scores the prompts on four core metrics — **accuracy, groundedness, context relevance, conciseness** — plus catering-specific criteria (allergy safety, policy adherence, lead-capture completeness, order-extraction accuracy) across a matrix of menu fixtures (tiny, large, allergy-heavy, mid-service menu swap). It lets you change a prompt and get a per-metric pass/fail read in one command, so prompt iteration is data-driven rather than by feel.

How it works, briefly:
- **No Twilio, no production changes.** A custom Python provider (`evals/provider.py`) calls the app's real `llm.get_assistant_reply` directly, monkeypatching in the chosen menu fixture and prompt variant for the duration of each call and restoring them after — so evals and production can't drift, and `llm.py` / `knowledge_base.py` / `order_extraction.py` are never edited.
- **Prompt variants** live in `evals/prompt_variants/` (`v1_current.py` is a verbatim copy of the live prompt — the baseline). promptfoo runs the whole suite against every variant listed and renders a comparison matrix.
- **The judge is a separate deployment** (`AZURE_OPENAI_JUDGE_DEPLOYMENT`) so the model under test never grades itself.

### Setup

```bash
cd evals
npm install
```

Requires Node 18+ and Python (the provider imports the app's own modules, so run from a shell where the app's deps are installed — the same `.venv` is fine). Set `AZURE_OPENAI_JUDGE_DEPLOYMENT` in the project `.env` (see `.env.example`); the suite reuses `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` for both the agent and the judge.

### Running

```bash
npm run eval:fast   # core metrics, default menu — run after every prompt tweak
npm run eval:full   # all metrics + all fixtures — run before committing a change
npm run view        # open the results UI (per-metric pass rates, variant matrix)
```

Both eval commands pre-render each fixture's knowledge base to `evals/fixtures/rendered/` (used as the grounding context for the groundedness/relevance graders) and then hit real Azure OpenAI.

### Iterating on the prompt

1. Copy `evals/prompt_variants/v1_current.py` to `vN_yourchange.py`, edit its `build_system_prompt()`, and add a matching `vN_yourchange.txt` (containing just the id `vN_yourchange`).
2. Add that `.txt` to the `prompts:` list in `promptfooconfig.fast.yaml` / `.full.yaml`.
3. `npm run eval:fast` and compare the new column against `v1_current` — each metric aggregates its own pass rate, so you can see e.g. groundedness improve without conciseness regressing.

To add a test case, drop it into the relevant `evals/tests/*.yaml` (each assertion carries a `metric:` tag that rolls up in the report). To add a menu scenario, add a fixture module under `evals/fixtures/` and register it in `evals/fixtures/__init__.py`.

### Multi-menu fixture and selective injection

`evals/fixtures/talkofthetown/` is a real, large, multi-menu fixture (Talk of the Town Catering) used to test the case where a business has **several event-specific menus** and you only want to inject the relevant one(s) to keep token cost down:

- **`business.md`** is always injected — awards, policies, dietary-tag legend, and the phone/contact-page a customer is routed to for **custom or off-menu requests**. This is the cost you always pay.
- **`menus/<key>.md`** (barbecue, brunch, corporate, hors d'oeuvres, seated dinner, buffet, celebration of life) hold each menu's items and are injected **only when active**. A cheap menu index (names + event types, from `manifest.py`) is always injected so the model knows what exists without loading it.
- The loader `render_kb(active_menus)` produces business info + index + full detail for just the active menu(s). In evals, a test sets the `active_menus` var (e.g. `active_menus: buffet`); in production, a small router keyed off the event type picks the set and can switch it as the conversation changes.

The source PDFs are converted with Microsoft's **markitdown** via `evals/fixtures/talkofthetown/convert_pdfs.py` (`pip install "markitdown[pdf]"`, then `python convert_pdfs.py [SOURCE_DIR]`) into `_raw/`; the curated `menus/*.md` are derived from that raw output with the print headers/footers stripped.

The **`v2_multimenu`** prompt variant is the one aware of this setup — it routes by event type, quotes only from loaded menus, offers to pull up a menu that isn't loaded (instead of inventing it), and redirects custom/off-menu/pricing asks to the business contact. `tests/multimenu_injection.yaml` and `tests/contact_redirect.yaml` cover those behaviors; the full config compares `v2_multimenu` against the `v1_current` baseline.

## Security

- Never commit `.env` — it holds live secrets (Azure key, Twilio auth token/API secret, SMTP/SendGrid credentials). `.gitignore` already excludes it.
- If a secret is ever accidentally committed, **deleting the file is not enough** — it stays in git history. The fix is to rotate/revoke that credential (regenerate the Azure key, roll the Twilio auth token and API key, reset the SMTP password or SendGrid key) and only then clean up history if needed.
- All inbound `/sms` requests are validated against Twilio's `X-Twilio-Signature` header using `TWILIO_AUTH_TOKEN`; requests that fail validation are rejected with a 403. Only disable this (`SKIP_TWILIO_SIGNATURE_VALIDATION=true`) for local testing, never in production.
