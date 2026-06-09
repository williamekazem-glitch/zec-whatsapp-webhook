"""
ZEC WhatsApp Webhook Server
Reçoit les messages WhatsApp entrants et répond via Claude
"""
import os
import json
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

app = FastAPI()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "zec_webhook_2024")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1131891220009937")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = "https://graph.facebook.com/v25.0"

SYSTEM_PROMPT = """Tu es l'assistant virtuel de ZEC (Zone d'Échange Commercial), une entreprise basée en Côte d'Ivoire.
Tu réponds aux clients de manière professionnelle, chaleureuse et concise en français.
Tu peux aider avec : informations sur les produits, prix, disponibilité, horaires, localisation.
Si tu ne sais pas quelque chose, dis-le poliment et propose de transmettre la demande à l'équipe ZEC."""


async def send_whatsapp_message(to: str, message: str):
    """Envoie un message WhatsApp"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/{PHONE_NUMBER_ID}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": message}
            }
        )
    return response.json()


async def get_claude_response(user_message: str) -> str:
    """Appelle l'API Claude pour générer une réponse"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}]
            }
        )
    data = response.json()
    return data["content"][0]["text"]


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Vérification du webhook par Meta"""
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return Response(status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request):
    """Reçoit les messages WhatsApp entrants"""
    body = await request.json()

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return {"status": "no_message"}

        message = value["messages"][0]
        from_number = message["from"]  # wa_id exact du client
        msg_type = message.get("type", "")

        if msg_type == "text":
            user_text = message["text"]["body"]
            print(f"Message reçu de {from_number}: {user_text}")

            # Générer réponse avec Claude
            reply = await get_claude_response(user_text)

            # Envoyer la réponse
            await send_whatsapp_message(from_number, reply)
            print(f"Réponse envoyée à {from_number}: {reply[:50]}...")

    except Exception as e:
        print(f"Erreur: {e}")

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ZEC WhatsApp Webhook actif ✅"}
