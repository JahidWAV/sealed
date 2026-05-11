from flask import Flask, render_template, request, jsonify, session, redirect
import stripe
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'TON_SECRET_KEY_ULTRA_SECURE'

# Config Stripe & Firebase
stripe.api_key = "sk_test_..."
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/countries')
def get_countries():
    # Récupère tous les pays possédés pour colorer la carte
    countries = db.collection('territories').stream()
    return jsonify({c.id: c.to_dict() for c in countries})

@app.route('/buy-country', methods=['POST'])
def buy_country():
    if 'user_id' not in session: return jsonify(error="Connectez-vous"), 401
    
    data = request.json
    iso_code = data.get('code') # ex: FRA
    new_image_url = data.get('image_url')
    user_id = session['user_id']
    
    user_ref = db.collection('users').document(user_id)
    country_ref = db.collection('territories').document(iso_code)
    
    # Logique de transaction atomique
    @firestore.transactional
    def update_in_transaction(transaction, u_ref, c_ref):
        u_snapshot = u_ref.get(transaction=transaction).to_dict()
        c_snapshot = c_ref.get(transaction=transaction)
        
        # Calcul du prix
        price = 1.0
        if c_snapshot.exists:
            price = round(c_snapshot.to_dict().get('price', 1.0), 2)
            
        if u_snapshot.get('balance', 0) < price:
            raise Exception("Solde insuffisant")
            
        # Mise à jour
        new_price = round(price * 1.2, 2)
        transaction.update(u_ref, {'balance': round(u_snapshot['balance'] - price, 2)})
        transaction.set(c_ref, {
            'owner': user_id,
            'price': new_price,
            'image_url': new_image_url,
            'last_update': datetime.utcnow()
        })
        return price

    try:
        transaction = db.transaction()
        final_price = update_in_transaction(transaction, user_ref, country_ref)
        return jsonify(success=True, price=final_price)
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    app.run(debug=True)
