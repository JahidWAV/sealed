import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, session
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'CLE_SECURE_A_CHANGER_EN_PROD' # Change ça pour la sécurité des sessions

# --- CONFIGURATION STRIPE ---
stripe.api_key = "sk_test_..." # Mets ta clé secrète Stripe ici

# --- CONFIGURATION FIREBASE ---
# Cette partie gère soit le fichier local, soit la variable d'env sur Render
firebase_config_json = os.environ.get('FIREBASE_CONFIG')

if firebase_config_json:
    # Pour Render : utilise la variable d'environnement
    cred_dict = json.loads(firebase_config_json)
    cred = credentials.Certificate(cred_dict)
else:
    # Pour ton ordi (local) : utilise le fichier JSON
    # Assure-toi que le fichier est bien à la racine du projet
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
    except Exception as e:
        print("Erreur : Fichier serviceAccountKey.json manquant et variable FIREBASE_CONFIG non trouvée.")
        cred = None

if cred:
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    print("Firebase n'a pas pu être initialisé.")

# --- ROUTES ---

@app.route('/')
def index():
    # On vérifie si l'utilisateur est connecté, sinon on peut simuler un ID pour le test
    if 'user_id' not in session:
        session['user_id'] = 'test_user_1' # À remplacer par ton système de login plus tard
    return render_template('dashboard.html')

@app.route('/api/countries')
def get_countries():
    """Récupère les données de tous les pays déjà achetés"""
    try:
        countries_ref = db.collection('territories').stream()
        countries_data = {c.id: c.to_dict() for c in countries_ref}
        return jsonify(countries_data)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/buy-country', methods=['POST'])
def buy_country():
    """Gère l'achat d'un pays avec surenchère de 20%"""
    if 'user_id' not in session:
        return jsonify(error="Vous devez être connecté"), 401
    
    data = request.json
    iso_code = data.get('code') # ex: "FRA"
    new_image_url = data.get('image_url')
    user_id = session['user_id']
    
    if not iso_code or not new_image_url:
        return jsonify(error="Code pays ou URL d'image manquant"), 400

    user_ref = db.collection('users').document(user_id)
    country_ref = db.collection('territories').document(iso_code)

    # Utilisation d'une transaction Firestore pour éviter les conflits si 2 personnes achètent en même temps
    @firestore.transactional
    def update_in_transaction(transaction, u_ref, c_ref):
        u_snapshot = u_ref.get(transaction=transaction)
        c_snapshot = c_ref.get(transaction=transaction)
        
        if not u_snapshot.exists:
            raise Exception("Utilisateur non trouvé en base. Créez votre profil d'abord.")
            
        u_data = u_snapshot.to_dict()
        
        # Déterminer le prix (1.0€ par défaut, sinon prix actuel en base)
        price = 1.0
        if c_snapshot.exists:
            price = round(c_snapshot.to_dict().get('price', 1.0), 2)
            
        # Vérification du solde
        if u_data.get('balance', 0) < price:
            raise Exception(f"Solde insuffisant. Il vous faut {price}€.")
            
        # 1. Débiter l'acheteur
        new_balance = round(u_data['balance'] - price, 2)
        transaction.update(u_ref, {'balance': new_balance})
        
        # 2. Mettre à jour le pays (Nouveau prix +20%)
        new_price = round(price * 1.2, 2)
        transaction.set(c_ref, {
            'owner': user_id,
            'price': new_price,
            'image_url': new_image_url,
            'last_update': datetime.utcnow()
        })
        
        # 3. (Optionnel) Ajouter une trace dans l'historique
        history_ref = db.collection('history').document()
        transaction.set(history_ref, {
            'user': user_id,
            'action': 'purchase',
            'country': iso_code,
            'amount': price,
            'timestamp': datetime.utcnow()
        })
        
        return price

    try:
        transaction = db.transaction()
        final_price = update_in_transaction(transaction, user_ref, country_ref)
        return jsonify(success=True, message=f"Pays conquis pour {final_price}€ !")
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    # Le port est géré automatiquement par Gunicorn sur Render
    app.run(debug=True)
