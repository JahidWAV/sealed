import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, redirect, url_for
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'glory_to_nations_2026')

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

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

@app.route('/api/check-username/<username>')
def check_username(username):
    # Vérifie si le pseudo existe déjà dans la base
    users = db.collection('users').where('username', '==', username).limit(1).get()
    return jsonify(available=len(users) == 0)

@app.route('/api/top-contributors/<iso>')
def get_top_contributors(iso):
    try:
        # Récupère toutes les contributions pour agréger par UID (évite le duplicata vu dans image_d2f315.png)
        docs = db.collection('territories').document(iso).collection('contributions').stream()
        totals = {}
        for d in docs:
            data = d.to_dict()
            uid = data['uid']
            if uid not in totals:
                totals[uid] = {'username': data['username'], 'amount': 0}
            totals[uid]['amount'] += data['amount']
        
        # Trie par montant décroissant
        sorted_contribs = sorted(totals.values(), key=lambda x: x['amount'], reverse=True)
        return jsonify(sorted_contribs[:10])
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/api/user-stats/<uid>')
def get_user_stats(uid):
    try:
        # Historique 100% fonctionnel via collection_group
        docs = db.collection_group('contributions').where('uid', '==', uid).order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
        return jsonify([d.to_dict() for d in docs])
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/success')
def success():
    iso_code = request.args.get('code')
    amount = float(request.args.get('price'))
    uid = request.args.get('uid')
    username = request.args.get('user')
    
    if iso_code and amount and uid:
        country_ref = db.collection('territories').document(iso_code)
        user_ref = db.collection('users').document(uid)
        
        # Enregistre/Met à jour l'utilisateur pour le verrouillage du pseudo
        user_ref.set({'username': username}, merge=True)

        # Vérifie si c'est un nouveau contributeur pour ce pays
        prev = country_ref.collection('contributions').where('uid', '==', uid).limit(1).get()
        is_new = len(prev) == 0

        update_data = {'total_invested': firestore.Increment(amount), 'last_update': datetime.utcnow()}
        if is_new: update_data['contributor_count'] = firestore.Increment(1)
        
        country_ref.set(update_data, merge=True)
        country_ref.collection('contributions').add({
            'uid': uid, 'username': username, 'amount': amount, 
            'timestamp': datetime.utcnow(), 'country_iso': iso_code
        })
    return redirect(url_for('index'))

# ... (reste du code Stripe identique)
