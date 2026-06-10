"""
ZEC WhatsApp Webhook Server
Reçoit les messages WhatsApp entrants et répond via Claude (Awa)
Workflow superviseur : escalade texte + transfert images (captures Yango)
"""
import os
import httpx
import asyncio
import random
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

app = FastAPI()

# Historique des conversations par numéro (en mémoire)
conversation_history: dict = {}

# Escalade texte : client_number -> question posée
pending_supervisor: dict = {}

# Transfert image : client en attente d'une capture (livraison Yango)
pending_image_transfer: str = ""  # numéro du dernier client qui attend une capture

# Catalogue photos produits : { "sac cabas": media_id, ... }
# Wallid alimente ce catalogue en envoyant une image avec caption "PHOTO: nom produit"
product_images: dict = {}

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "zec_webhook_2024")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1131891220009937")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = "https://graph.facebook.com/v25.0"

SUPERVISOR_NUMBER = os.environ.get("SUPERVISOR_NUMBER", "2250777632164")

ESCALADE_TRIGGER = "Je transmets votre demande"
LIVRAISON_TRIGGER = "localisation"  # mot clé dans la réponse d'Awa pour activer le suivi livraison

SYSTEM_PROMPT = """Tu es Awa, commerciale chez ZEC, une entreprise de packaging à Abidjan, Côte d'Ivoire.
Tu écris comme une vraie personne sur WhatsApp — naturel, chaleureux, simple. Pas trop formel, pas scolaire.
Ne mentionne jamais que tu es un robot, une IA ou un assistant virtuel. Tu es simplement Awa.

STYLE D'ÉCRITURE :
- Écris comme une vraie personne sur WhatsApp — court, naturel, direct
- Maximum 2-3 lignes par message, jamais plus
- Pas de listes à puces, pas de blocs séparés, pas de formules robotiques
- Tout doit tenir en une seule idée fluide

EXEMPLES DE BONNES RÉPONSES :
- Premier contact : "Bonjour, je suis Awa de ZEC, comment puis-je vous aider ?"
- Produit + prix : "Les sacs A2 couleur sont à 2500 F l'unité, vous en voulez combien ?"
- Commande : "Parfait. Votre nom complet et votre adresse de livraison svp ?"
- Paiement : "Vous pouvez payer par Wave ou OM au 07 78 84 07 57, les frais sont à votre charge."
- Ne sait pas : "Je transmets votre demande à notre équipe qui vous répondra dans les plus brefs délais."

EXEMPLES DE MAUVAISES RÉPONSES (à éviter absolument) :
- "Je m'appelle Awa, je suis commerciale chez ZEC. Nous sommes spécialisés dans la vente de produits de packaging à Abidjan. Comment puis-je vous aider ?" → trop long
- "D'accord pour du A2 couleur. Le prix est 2500 F l'unité.\n\nVous voulez combien d'unités ? Le minimum est 100." → deux blocs séparés, robotique

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

QUANTITES :
- Commande minimum : 100 unités
- Les quantités disponibles sont : 100, 200, 300, 400, 500... (multiples de 100)
- Si un client demande une quantité inférieure à 100, informe-le que le minimum de commande est 100 unités

TARIFS SACS CABAS (vendus par centaine) :
Les sacs cabas sont disponibles en 4 tailles. Prix à l'unité :

- A5 : blanc 800 F | couleur 1000 F
- A4 : blanc 1000 F | couleur 1850 F
- A3 : blanc 1500 F | couleur 2300 F
- A2 : blanc 1700 F | couleur 2500 F

Vendu par tranche de 100 unités minimum (100, 200, 300...).
Pour les autres produits (sachets zip, sachets personnalisés, cartes de remerciement), demande au client le produit et la quantité pour lui transmettre le tarif.

PAIEMENT :
- Wave ou Orange Money au : +225 07 78 84 07 57
- Le client prend en charge les frais Mobile Money

COMMANDE :
- Acompte de 75% requis à la commande
- Livraison sous 7 jours ouvrables
- Frais de livraison à la charge du client

LIVRAISON :
Les livraisons se font uniquement via Yango Livraison. Voici le processus exact :

Etape 1 — Collecte de la localisation :
Demande au client de partager sa localisation de préférence via WhatsApp (bouton localisation), ou un lien Google Maps ou Yango. Une fois reçue, transmets-la à l'équipe.

Etape 2 — Capture Yango :
L'équipe envoie une capture avec deux options : Express ou 3H. Quand tu reçois la capture, transmets-la au client avec : "Voici les options de livraison pour votre adresse. Vous préférez Express ou 3H ?"

Etape 3 — Choix du client :
Quand le client choisit, confirme à l'équipe : "Le client a choisi [Express ou 3H]."

Livreur personnel :
Si le client préfère envoyer son propre livreur, informe-le : "Pas de problème. Merci d'appeler le +225 05 08 31 63 32 pour confirmer notre disponibilité avant d'envoyer votre livreur."

REGLES IMPORTANTES :
- Tu ne réponds QUE aux questions liées à ZEC : produits, commandes, tarifs, livraison, horaires, localisation.
- Si un client pose une question qui ne concerne pas notre activité, réponds exactement : "Je transmets votre demande à notre équipe qui vous répondra dans les plus brefs délais."
- Si tu ne connais pas la réponse à une question liée à ZEC, réponds exactement : "Je transmets votre demande à notre équipe qui vous répondra dans les plus brefs délais."
- Ne jamais inventer des prix ou des informations que tu ne connais pas
- Ne jamais dire "Bonjour" plus d'une fois par conversation
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
    """Envoie un message texte WhatsApp"""
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


async def send_whatsapp_image(to: str, media_id: str, caption: str = ""):
    """Transfert une image WhatsApp via son media_id"""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": {"id": media_id}
    }
    if caption:
        payload["image"]["caption"] = caption

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/{PHONE_NUMBER_ID}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload
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
    prompt = f"Question du client : {original_question}\n\nÉbauche de réponse : {draft}\n\nAméliore cette réponse pour l'envoyer au client."

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

    if client_number not in conversation_history:
        conversation_history[client_number] = []
    conversation_history[client_number].append({"role": "assistant", "content": improved})
    return improved


async def notify_supervisor(client_number: str, question: str):
    """Notifie Wallid qu'un client attend une réponse texte"""
    msg = (
        f"CLIENT EN ATTENTE\n"
        f"Numero : +{client_number}\n"
        f"Question : {question}\n\n"
        f"Reponds-moi directement avec ta reponse et je la transmettrai au client."
    )
    await send_whatsapp_message(SUPERVISOR_NUMBER, msg)
    print(f"Superviseur notifié pour le client {client_number}")


async def notify_supervisor_location(client_number: str, location_info: str):
    """Notifie Wallid qu'un client a partagé sa localisation — en attente de capture Yango"""
    global pending_image_transfer
    pending_image_transfer = client_number
    msg = (
        f"LOCALISATION RECUE\n"
        f"Client : +{client_number}\n"
        f"Localisation : {location_info}\n\n"
        f"Envoie-moi la capture Yango et je la transmettrai directement au client."
    )
    await send_whatsapp_message(SUPERVISOR_NUMBER, msg)
    print(f"Localisation transmise à Wallid pour le client {client_number}")


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return Response(status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request):
    global pending_image_transfer
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

        print(f"Message recu de {from_number}, type: {msg_type}")

        # ── CAS 1 : MESSAGE DE WALLID ──────────────────────────────────────
        if from_number == SUPERVISOR_NUMBER:

            # 1a. Wallid envoie une IMAGE
            if msg_type == "image":
                media_id = message["image"]["id"]
                caption = message["image"].get("caption", "").strip()

                # Cas A : ajout d'un photo produit au catalogue  →  "PHOTO: sac cabas"
                if caption.upper().startswith("PHOTO:"):
                    product_name = caption[6:].strip().lower()
                    product_images[product_name] = media_id
                    await send_whatsapp_message(
                        SUPERVISOR_NUMBER,
                        f"Photo enregistree pour le produit : {product_name}.\nCatalogue actuel : {', '.join(product_images.keys())}"
                    )
                    print(f"Photo produit ajoutee : {product_name} -> {media_id}")

                # Cas B : capture Yango à transférer au client
                elif pending_image_transfer:
                    client_number = pending_image_transfer
                    pending_image_transfer = ""

                    await asyncio.sleep(random.uniform(1, 3))
                    await send_whatsapp_image(
                        client_number,
                        media_id,
                        caption="Voici les options de livraison pour votre adresse. Vous préférez Express ou 3H ?"
                    )
                    if client_number not in conversation_history:
                        conversation_history[client_number] = []
                    conversation_history[client_number].append({
                        "role": "assistant",
                        "content": "J'ai envoyé la capture Yango au client avec les options Express et 3H."
                    })
                    await send_whatsapp_message(SUPERVISOR_NUMBER, f"Capture transmise au client +{client_number}.")
                    print(f"Capture Yango transmise au client {client_number}")

                else:
                    print("Image de Wallid non reconnue (pas de PHOTO: et pas de client en attente).")
                return {"status": "ok"}

            # 1b. Wallid envoie un TEXTE → réponse améliorée pour client en attente
            if msg_type == "text":
                user_text = message["text"]["body"]
                if pending_supervisor:
                    client_number, original_question = next(iter(pending_supervisor.items()))
                    del pending_supervisor[client_number]

                    await asyncio.sleep(random.uniform(2, 4))
                    improved_reply = await improve_supervisor_draft(client_number, original_question, user_text)
                    await send_whatsapp_message(client_number, improved_reply)
                    await send_whatsapp_message(SUPERVISOR_NUMBER, f"Reponse transmise au client +{client_number}.")
                    print(f"Réponse améliorée envoyée au client {client_number}")
                else:
                    print("Texte de Wallid sans client en attente, ignoré.")
            return {"status": "ok"}

        # ── CAS 2 : MESSAGE D'UN CLIENT ────────────────────────────────────

        # 2a. Client envoie sa LOCALISATION WhatsApp
        if msg_type == "location":
            location = message["location"]
            lat = location.get("latitude", "")
            lng = location.get("longitude", "")
            name = location.get("name", "")
            address = location.get("address", "")
            location_info = f"lat:{lat}, lng:{lng}"
            if name:
                location_info += f", {name}"
            if address:
                location_info += f", {address}"

            await send_whatsapp_message(from_number, "Merci, j'ai bien reçu votre localisation. Je reviens vers vous avec les options de livraison.")
            await notify_supervisor_location(from_number, location_info)
            return {"status": "ok"}

        # 2b. Client envoie un TEXTE
        if msg_type == "text":
            user_text = message["text"]["body"]
            print(f"Texte client {from_number}: {user_text}")

            await asyncio.sleep(random.uniform(2, 5))
            reply = await get_claude_response(from_number, user_text)
            await send_whatsapp_message(from_number, reply)
            print(f"Réponse envoyée à {from_number}: {reply[:60]}...")

            # Envoi automatique de photo si le client demande un visuel produit
            if product_images:
                texte_lower = user_text.lower()
                for product_name, media_id in product_images.items():
                    if any(mot in texte_lower for mot in ["photo", "image", "visuel", "voir", "montre", "exemple"]):
                        if product_name in texte_lower or any(mot in texte_lower for mot in ["produit", "sac", "sachet", "carte"]):
                            await asyncio.sleep(1)
                            await send_whatsapp_image(from_number, media_id)
                            print(f"Photo produit '{product_name}' envoyée à {from_number}")
                            break

            # Escalade texte si Awa ne sait pas
            if ESCALADE_TRIGGER in reply:
                pending_supervisor[from_number] = user_text
                await notify_supervisor(from_number, user_text)

            # Livraison : si Awa vient de demander la localisation → prépare le suivi
            if LIVRAISON_TRIGGER in reply.lower():
                pending_image_transfer = from_number

            return {"status": "ok"}

        # 2c. Client envoie une IMAGE (modèle souhaité)
        if msg_type == "image":
            media_id = message["image"]["id"]
            caption = message["image"].get("caption", "")

            await asyncio.sleep(random.uniform(2, 4))

            # Répondre au client
            await send_whatsapp_message(
                from_number,
                "Bien reçu. Je transmets le modèle à notre équipe qui vous confirmera la disponibilité."
            )

            # Transférer la photo à Wallid avec contexte
            await send_whatsapp_message(
                SUPERVISOR_NUMBER,
                f"MODELE CLIENT\nNumero : +{from_number}\nLe client souhaite ce modèle. Voir photo ci-dessous."
            )
            await send_whatsapp_image(SUPERVISOR_NUMBER, media_id, caption=caption)
            print(f"Photo modèle du client {from_number} transférée à Wallid")
            return {"status": "ok"}

        # Autres types ignorés
        print(f"Type non géré: {msg_type}")

    except Exception as e:
        print(f"Erreur: {e}")

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ZEC WhatsApp Webhook actif"}
