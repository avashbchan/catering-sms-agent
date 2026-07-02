"""
Flask app: Twilio SMS webhook for the after-hours catering coordinator.

Flow per inbound text:
  1. Validate the Twilio request signature (reject anything else).
  2. Load recent conversation history for this phone number from SQLite.
  3. Append the new user message, call the LLM.
  4. If the LLM calls submit_catering_lead, email staff a summary.
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
from email_sender import send_lead_email

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

    def on_lead_submitted(lead_args: dict) -> bool:
        transcript = storage.get_full_transcript(from_number)
        return send_lead_email(lead_args, transcript)

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
