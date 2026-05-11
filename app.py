import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, redirect
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'global_conquest_key_99')

# --- CONFIG STRIPE ---
# Utilise ta clé sk_test habituelle ici
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_51...') 
DOMAIN = "https://sealed-votre-domaine.onrender.com" # Ton domaine Render actuel

# --- CONFIG FIREBASE ---
firebase_config_json = os.environ.get('FIREBASE_CONFIG')
if firebase_config_json:
    cred = credentials.Certificate(json.loads(firebase_config_json))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/countries')
def get_countries():
    try:
        countries_ref = db.collection('territories').stream()
        return jsonify({c.id: c.to_dict() for c in countries_ref})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = request.json
    iso_code = data.get('code')
    proposed_price = round(float(data.get('price', 0)), 2)
    image_url = data.get('image_url')

    # Vérification sécurité prix
    doc = db.collection('territories').document(iso_code).get()
    min_allowed = 1.00
    if doc.exists:
        min_allowed = max(1.00, round(doc.to_dict().get('price', 0) * 1.2, 2))

    if proposed_price < min_allowed:
        return jsonify(error=f"Price too low. Minimum: {min_allowed}$"), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"CONQUEST: {iso_code}",
                        'description': f"Propaganda URL: {image_url}",
                    },
                    'unit_amount': int(proposed_price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            # On passe les infos dans l'URL de succès pour mettre à jour la DB
            success_url=DOMAIN + f"/success?code={iso_code}&price={proposed_price}&img={image_url}",
            cancel_url=DOMAIN + "/",
        )
        return jsonify({'id': checkout_session.id})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/success')
def success():
    iso_code = request.args.get('code')
    price = float(request.args.get('price'))
    image_url = request.args.get('img')

    if iso_code and price and image_url:
        db.collection('territories').document(iso_code).set({
            'price': price,
            'image_url': image_url,
            'last_update': datetime.utcnow()
        })
    return redirect("/")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
