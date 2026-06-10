"""
ZEC WhatsApp Webhook Server
Reçoit les messages WhatsApp entrants et répond via Claude (Awa)
Workflow superviseur : si Awa ne sait pas → notifie Wallid → Wallid répond → Awa améliore → envoie au client
"""
import os
import json
import httpx
import asyncio
import random
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

app = FastAPI()

# Historique des conversations par numéro (en mémoire)
conversation_history: dict = {}

# File d'attente superviseur : client_number -> question posée
# Quand Wallid répond, on sait à quel client répondre
pending_supervisor: dict = {}  # { client_number: question }

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "zec_webhook_2024")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1131891220009937")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = "https://graph.facebook.com/v25.0"

SUPERVISOR_NUMBER = os.environ.get("SUPERVISOR_NUMBER", "2250777632164")

# Phrase déclencheur : quand Awa dit ça, on notifie Wallid
ESCALADE_TRIGGER = "Je transmets votre demande"

SYSTEM_PROMPT = """Tu es Awa, commerciale chez ZEC, une entreprise de packaging à Abidjan, Côte d'Ivoire.
Tu écris comme une vraie personne sur WhatsApp — naturel, chaleureux, simple. Pas trop formel, pas scolaire.
Ne mentionne jamais que tu es un robot, une IA ou un assistant virtuel. Tu es simplement Awa.

STYLE D'ÉCRITURE :
- Écris comme tu parlerais à quelqu'un sur WhatsApp : phrases courtes, ton naturel
- Exemple de premier message : "Bonjour, je suis Awa commerciale ZEC, comment pourrais-je vous aider svp"
- Exemple de réponse rapide : "Bien sûr, pour les sachets zip ça dépend de la quantité souhaitée. Vous voulez combien ?"
- Exemple de commande : "D'accord. Pour finaliser, j'aurais besoin de votre nom, l'adresse de livraison et la quantité exacte."
- Pas de formules robotiques comme "Je serais ravie de vous assister" ou "N'hésitez pas à me contacter"
- Pas de listes à puces dans les messages au client
- Maximum 3-4 lignes par message

INFORMATIONS ZEC :
- Nom : ZEC
- Activité : Vente de produits de packaging
- Adresse : Abidjan, Yopougon Aneraie, Carrefour Oasis
- Horaires : Lundi - Samedi, 9h00 - 17h00
- WhatsApp commercial humain : +225 05 08 31 63 32

PRODUITS DISPONIBLES :
- Sacs cabas
- Cartes de remerciement
- Sachets zip
- Sachets personnalisés
- Sacs cabas personnalisés
(D'autres produits seront ajoutés prochainement)

TARIFS :
Les prix varient selon le produit et la quantité. Demande au client de préciser le produit et la quantité pour lui communiquer le tarif exact.

COMMANDE :
- Acompte de 75% requis à la commande
- Livraison sous 7 jours ouvrables
- Frais de livraison à la charge du client

REGLES IMPORTANTES :
- Tu ne réponds QUE aux questions liées à ZEC : produits, commandes, tarifs, livraison, horaires, localisation.
- Si un client pose une question qui ne concerne pas notre activité (politique, météo, blagues, conseils personnels, etc.), réponds exactement : "Je transmets votre demande à notre équipe qui vous répondra dans les plus brefs délais."
- Si tu ne connais pas la réponse à une question liée à ZEC, réponds exactement : "Je transmets votre demande à notre équipe qui vous répondra dans les plus brefs délais."
- Ne jamais inventer des prix ou des informations que tu ne connais pas
- Ne jamais dire "Bonjour" plus d'une fois par conversation — si tu as déjà salué, ne répète plus "Bonjour" dans les messages suivants
- "Bonjour" uniquement au tout premier message si le client vient de saluer
- Réponses courtes et directes — maximum 3-4 lignes
- Zéro emoji dans les messages
- Style professionnel et courtois
- Si le client veut commander, collecte : nom complet, produit souhaité, quantité, adresse de livraison"""

IMPROVE_PROMPT = """Tu es Awa, commerciale chez ZEC (packaging, Abidjan).
On t'a transmis une ébauche de réponse à envoyer à un client.
Améliore ce texte : rends-le plus professionnel, naturel et courtois, sans changer le fond.
Réponse courte (3-4 lignes max), zéro emoji, zéro "Bonjour" si ce n'est pas le premier message.
Réponds uniquement avec le texte amélioré, rien d'autre."""


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


async def get_claude_response(from_number: str, user_message: str) -> str:
    """Appelle Claude avec l'historique de conversation"""
    if from_number not in conversation_history:
        conversation_history[from_number] = []

    conversation_history[from_number].append({"role": "user", "content": user_message})
    messages = conversation_history[from_number][-20:]

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
                "messages": messages
            }
        )
    data = response.json()
    reply = data["content"][0]["text"]

    conversation_history[from_number].append({"role": "assistant", "content": reply})
    return reply


async def improve_supervisor_draft(client_number: str, original_question: str, draft: str) -> str:
    """Améliore le brouillon de Wallid avant de l'envoyer au client"""
    prompt = f"""Question du client : {original_question}

Ébauche de réponse de notre équipe : {draft}

Améliore cette réponse pour l'envoyer au client."""

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
                "system": IMPROVE_PROMPT,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
    data = response.json()
    improved = data["content"][0]["text"]

    # Ajouter la réponse améliorée à l'historique du client
    if client_number not in conversation_history:
        conversation_history[client_number] = []
    conversation_history[client_number].append({"role": "assistant", "content": improved})

    return improved


async def notify_supervisor(client_number: str, question: str):
    """Notifie Wallid qu'un client attend une réponse"""
    msg = (
        f"CLIENT EN ATTENTE\n"
        f"Numero : +{client_number}\n"
        f"Question : {question}\n\n"
        f"Reponds-moi directement avec ta reponse et je la transmettrai au client."
    )
    await send_whatsapp_message(SUPERVISOR_NUMBER, msg)
    print(f"Superviseur notifié pour le client {client_number}")


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
        from_number = message["from"]
        msg_type = message.get("type", "")

        if msg_type != "text":
            return {"status": "non_text_ignored"}

        user_text = message["text"]["body"]
        print(f"Message recu de {from_number}: {user_text}")

        # --- CAS 1 : MESSAGE DE WALLID (superviseur) ---
        if from_number == SUPERVISOR_NUMBER:
            if pending_supervisor:
                # Prendre le premier client en attente
                client_number, original_question = next(iter(pending_supervisor.items()))
                del pending_supervisor[client_number]

                print(f"Wallid a repondu pour le client {client_number}, amelioration en cours...")

                # Délai naturel
                await asyncio.sleep(random.uniform(2, 4))

                # Améliorer le brouillon de Wallid
                improved_reply = await improve_supervisor_draft(client_number, original_question, user_text)

                # Envoyer au client
                await send_whatsapp_message(client_number, improved_reply)
                print(f"Reponse amelioree envoyee au client {client_number}")

                # Confirmer à Wallid
                await send_whatsapp_message(
                    SUPERVISOR_NUMBER,
                    f"Reponse transmise au client +{client_number}."
                )
            else:
                # Wallid écrit sans client en attente — message normal ignoré
                print("Message de Wallid sans client en attente, ignore.")
            return {"status": "ok"}

        # --- CAS 2 : MESSAGE D'UN CLIENT ---
        await asyncio.sleep(random.uniform(2, 5))

        reply = await get_claude_response(from_number, user_text)

        # Envoyer la réponse au client
        await send_whatsapp_message(from_number, reply)
        print(f"Reponse envoyee a {from_number}: {reply[:60]}...")

        # Si Awa ne sait pas → notifier Wallid
        if ESCALADE_TRIGGER in reply:
            pending_supervisor[from_number] = user_text
            await notify_supervisor(from_number, user_text)
            print(f"Client {from_number} mis en attente superviseur")

    except Exception as e:
        print(f"Erreur: {e}")

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ZEC WhatsApp Webhook actif"}
