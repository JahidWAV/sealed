import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, session
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'SUPER_SECRET_KEY_THE_DOLLAR_MAP' # À changer en production

# --- STRIPE CONFIG ---
stripe.api_key = "sk_test_..." 

# --- FIREBASE CONFIG (Render + Local) ---
firebase_config_json = os.environ.get('FIREBASE_CONFIG')

if firebase_config_json:
    cred_dict = json.loads(firebase_config_json)
    cred = credentials.Certificate(cred_dict)
else:
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
    except Exception as e:
        print("Error: serviceAccountKey.json not found.")
        cred = None

if cred:
    # On évite d'initialiser deux fois si Flask reload
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    db = firestore.client()

# --- ROUTES ---

@app.route('/')
def index():
    # Simulation d'un utilisateur pour le test (à lier à ton système de login plus tard)
    if 'user_id' not in session:
        session['user_id'] = 'test_user_1'
    return render_template('dashboard.html')

@app.route('/api/countries')
def get_countries():
    """Récupère les données de la map pour le leaderboard et les visuels"""
    try:
        countries_ref = db.collection('territories').stream()
        countries_data = {c.id: c.to_dict() for c in countries_ref}
        return jsonify(countries_data)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/buy-country', methods=['POST'])
def buy_country():
    """Gère l'achat d'un pays avec PRIX LIBRE (minimum +20%)"""
    if 'user_id' not in session:
        return jsonify(error="Authentication required"), 401
    
    data = request.json
    iso_code = data.get('code') # ex: "USA"
    proposed_price = float(data.get('price', 0)) # Le prix choisi par l'utilisateur
    new_image_url = data.get('image_url')
    user_id = session['user_id']
    
    if not iso_code or not new_image_url or proposed_price <= 0:
        return jsonify(error="Missing data or invalid price"), 400

    user_ref = db.collection('users').document(user_id)
    country_ref = db.collection('territories').document(iso_code)

    @firestore.transactional
    def update_in_transaction(transaction, u_ref, c_ref):
        u_snapshot = u_ref.get(transaction=transaction)
        c_snapshot = c_ref.get(transaction=transaction)
        
        if not u_snapshot.exists:
            raise Exception("User profile not found in database.")
            
        u_data = u_snapshot.to_dict()
        
        # Calcul du prix MINIMUM requis
        current_price = 1.0
        if c_snapshot.exists:
            current_price = c_snapshot.to_dict().get('price', 1.0)
        
        min_allowed = round(current_price * 1.2, 2)
        
        # Vérification 1 : Le prix proposé est-il suffisant ?
        if proposed_price < min_allowed:
            raise Exception(f"Bid too low. Minimum required: {min_allowed}$")

        # Vérification 2 : L'utilisateur a-t-il assez d'argent ?
        if u_data.get('balance', 0) < proposed_price:
            raise Exception(f"Insufficient balance. You need {proposed_price}$")
            
        # --- EXECUTION DE LA TRANSACTION ---
        
        # 1. On débite l'acheteur
        new_balance = round(u_data['balance'] - proposed_price, 2)
        transaction.update(u_ref, {'balance': new_balance})
        
        # 2. On met à jour le pays avec le NOUVEAU prix choisi
        transaction.set(c_ref, {
            'owner': user_id,
            'price': proposed_price, # On stocke le prix libre
            'image_url': new_image_url,
            'last_update': datetime.utcnow()
        })
        
        # 3. Historique
        history_ref = db.collection('history').document()
        transaction.set(history_ref, {
            'user': user_id,
            'type': 'conquest',
            'country': iso_code,
            'amount': proposed_price,
            'timestamp': datetime.utcnow()
        })
        
        return proposed_price

    try:
        transaction = db.transaction()
        final_price = update_in_transaction(transaction, user_ref, country_ref)
        return jsonify(success=True, message=f"Conquered for {final_price}$")
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    # Flask utilise le port 5000 par défaut ou celui de l'OS
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
