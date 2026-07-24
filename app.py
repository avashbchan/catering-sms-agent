"""
Flask app: Twilio SMS webhook for the after-hours catering coordinator.

Flow per inbound text:
  1. Validate the Twilio request signature (reject anything else).
  2. Load recent conversation history for this phone number from SQLite.
  3. Append the new user message, call the LLM.
  4. If the LLM calls submit_catering_lead, run the submission guard
     (lead_gate) and only if it passes, log the order summary and email staff.
  5. Save the assistant's reply and respond with TwiML.

Any failure in steps 2-4 is caught so the customer still gets a polite
fallback SMS instead of a broken webhook response.
"""
import logging

from flask import Flask, request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config
import storage
import llm
import lead_gate
import order_extraction
from email_sender import send_lead_email
from knowledge_base import get_knowledge_base_text

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Trust the X-Forwarded-Proto/Host headers from ngrok (or any reverse proxy
# in front of this app) so request.url reflects the public https:// URL
# Twilio actually signed — otherwise it reconstructs as http://, and every
# signature validation fails even though the number/tunnel are configured
# correctly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

HISTORY_LIMIT = 12
FALLBACK_MESSAGE = (
    "Sorry, we're having a technical hiccup on our end. "
    "Your message has been noted and our staff will follow up during business hours."
)


def _validate_twilio_request() -> bool:
    if config.SKIP_TWILIO_SIGNATURE_VALIDATION:
        logger.warning("Twilio signature validation is DISABLED (local testing only)")
        return True

    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    # ProxyFix (see app setup above) makes request.url reflect the public
    # https:// URL Twilio actually signed, even though ngrok/a load balancer
    # forwards to this process over plain http internally.
    return validator.validate(request.url, request.form.to_dict(), signature)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/sms", methods=["POST"])
def sms_webhook():
    if not _validate_twilio_request():
        logger.warning("Rejected inbound SMS webhook with invalid Twilio signature")
        return Response(status=403)

    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    reply_text = FALLBACK_MESSAGE
    try:
        reply_text = _handle_message(from_number, body)
    except Exception:
        logger.exception("Unhandled error processing inbound SMS from %s", from_number)

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return Response(str(twiml), mimetype="text/xml")


def _handle_message(from_number: str, body: str) -> str:
    storage.add_message(from_number, "user", body)
    history = storage.get_recent_history(from_number, limit=HISTORY_LIMIT)

    def on_lead_submitted(lead_args: dict) -> llm.LeadResult:
        # Transcript scoped to the CURRENT lead (messages since this number's
        # last submitted lead) so a repeat customer's older conversation doesn't
        # bleed into this lead's summary/email. Fetched before add_order_summary
        # below, so this lead's own summary can't become its own boundary.
        transcript = storage.get_transcript_since_last_lead(from_number)

        # Re-derive a clean order summary from the scoped transcript rather than
        # trusting only the live tool-call snapshot - catches customer
        # corrections (wrong date, changed items) made after the tool fired.
        order_summary = order_extraction.extract_order_summary(transcript, get_knowledge_base_text())

        # --- Submission guard (code-level safety net) ---
        # Both checks must pass before ANY DB write or email goes out.
        missing = lead_gate.missing_required_fields(order_summary)
        if missing:
            logger.info("Lead blocked for %s: missing required field(s) %s", from_number, missing)
            return llm.LeadResult(False, lead_gate.missing_fields_guidance(missing))

        # `history` (captured from the enclosing scope) ends with the customer's
        # latest message - the one the readiness check judges.
        if not lead_gate.customer_ready_to_submit(history):
            logger.info("Lead blocked for %s: customer has not confirmed they are done", from_number)
            return llm.LeadResult(False, lead_gate.NEEDS_CONFIRMATION_GUIDANCE)

        # Guard passed: persist the boundary (order_summaries row) + email staff.
        storage.add_order_summary(from_number, order_summary.model_dump_json())
        if send_lead_email(lead_args, transcript, order_summary):
            return llm.LeadResult(True, "Lead successfully sent to staff.")
        return llm.LeadResult(
            False,
            "Lead could not be sent to staff due to a technical issue - let the customer "
            "know staff will still be notified and to expect a follow-up call.",
        )

    try:
        reply_text = llm.get_assistant_reply(history, from_number, on_lead_submitted)
    except Exception:
        logger.exception("LLM call failed for %s", from_number)
        return FALLBACK_MESSAGE

    storage.add_message(from_number, "assistant", reply_text)
    return reply_text


storage.init_db()

if __name__ == "__main__":
    config.validate_for_startup()
    app.run(host="0.0.0.0", port=5000, debug=False)
