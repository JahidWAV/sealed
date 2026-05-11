import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, redirect
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'conquest_2026_key')

# --- CONFIG STRIPE ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_...') # Ta clé secrète
DOMAIN = "https://votre-domaine.onrender.com"

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

    doc = db.collection('territories').document(iso_code).get()
    min_allowed = 1.00
    if doc.exists:
        min_allowed = max(1.00, round(doc.to_dict().get('price', 0) * 1.2, 2))

    if proposed_price < min_allowed:
        return jsonify(error=f"Price too low. Min: {min_allowed}$"), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"CONQUEST: {iso_code}",
                        'description': f"Direct territory acquisition",
                    },
                    'unit_amount': int(proposed_price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=DOMAIN + f"/success?code={iso_code}&price={proposed_price}",
            cancel_url=DOMAIN + "/",
        )
        return jsonify({'id': checkout_session.id})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/success')
def success():
    iso_code = request.args.get('code')
    price = request.args.get('price')

    if iso_code and price:
        db.collection('territories').document(iso_code).set({
            'price': float(price),
            'last_update': datetime.utcnow()
        })
    return redirect("/")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
