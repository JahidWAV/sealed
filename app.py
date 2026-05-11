import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, session
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'conquest_secret_777')

# --- CONFIGURATION FIREBASE ---
firebase_config_json = os.environ.get('FIREBASE_CONFIG')
if firebase_config_json:
    cred = credentials.Certificate(json.loads(firebase_config_json))
else:
    # Pour le dev local, assure-toi d'avoir ce fichier
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route('/')
def index():
    if 'user_id' not in session:
        session['user_id'] = 'test_user_1' # Simule un utilisateur connecté
    return render_template('dashboard.html')

@app.route('/api/countries')
def get_countries():
    try:
        countries_ref = db.collection('territories').stream()
        return jsonify({c.id: c.to_dict() for c in countries_ref})
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/buy-country', methods=['POST'])
def buy_country():
    if 'user_id' not in session:
        return jsonify(error="Auth required"), 401
    
    data = request.json
    iso_code = data.get('code')
    proposed_price = round(float(data.get('price', 0)), 2)
    new_image_url = data.get('image_url')
    user_id = session['user_id']

    user_ref = db.collection('users').document(user_id)
    country_ref = db.collection('territories').document(iso_code)

    @firestore.transactional
    def update_in_transaction(transaction, u_ref, c_ref):
        u_snap = u_ref.get(transaction=transaction).to_dict()
        c_snap = c_ref.get(transaction=transaction)
        
        # LOGIQUE DE PRIX LIBRE MAIS MINIMUM STRIPE (1.00$)
        if c_snap.exists:
            current_price = c_snap.to_dict().get('price', 0)
            # Minimum = le plus élevé entre 1.00$ et l'ancienne offre + 20%
            min_allowed = max(1.00, round(current_price * 1.2, 2))
        else:
            # Premier achat : minimum 1.00$ direct
            min_allowed = 1.00

        if proposed_price < min_allowed:
            raise Exception(f"Minimum bid required: {min_allowed}$")

        if u_snap.get('balance', 0) < proposed_price:
            raise Exception("Insufficient funds in your account.")

        # Exécution de la conquête
        transaction.update(u_ref, {'balance': round(u_snap['balance'] - proposed_price, 2)})
        transaction.set(c_ref, {
            'owner': user_id,
            'price': proposed_price,
            'image_url': new_image_url,
            'last_update': datetime.utcnow()
        })
        return proposed_price

    try:
        transaction = db.transaction()
        final_price = update_in_transaction(transaction, user_ref, country_ref)
        return jsonify(success=True, price=final_price)
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    # Indispensable pour Render : écouter sur 0.0.0.0 et utiliser le port de l'environnement
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
