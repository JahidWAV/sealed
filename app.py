import os
import json
import stripe
from flask import Flask, render_template, request, jsonify, redirect, url_for
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'glory_to_nations_2026')

# --- STRIPE CONFIG ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# --- FIREBASE CONFIG ---
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

@app.route('/api/top-contributors/<iso>')
def get_top_contributors(iso):
    try:
        # Récupère les 10 plus grosses contributions pour ce pays
        contribs = db.collection('territories').document(iso).collection('contributions')\
                     .order_by('amount', direction=firestore.Query.DESCENDING).limit(10).stream()
        return jsonify([c.to_dict() for c in contribs])
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/api/user-stats/<uid>')
def get_user_stats(uid):
    try:
        # Récupère toutes les contributions d'un utilisateur spécifique
        docs = db.collection_group('contributions').where('uid', '==', uid).stream()
        stats = []
        for d in docs:
            stats.append(d.to_dict())
        return jsonify(stats)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.json
        iso_code = data.get('code')
        amount = round(float(data.get('price', 0)), 2)
        user_id = data.get('user_id')
        username = data.get('username')

        if amount < 1.00:
            return jsonify(error="Minimum contribution is $1.00"), 400

        base_url = request.host_url.rstrip('/')
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"NATIONAL CONTRIBUTION: {iso_code}",
                        'description': f"Support from {username}",
                    },
                    'unit_amount': int(amount * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{base_url}/success?code={iso_code}&price={amount}&uid={user_id}&user={username}",
            cancel_url=f"{base_url}/",
        )
        return jsonify({'id': checkout_session.id})
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
        
        # Vérifier si l'utilisateur a déjà contribué à ce pays
        prev_contribs = country_ref.collection('contributions').where('uid', '==', uid).limit(1).get()
        is_new_contributor = len(prev_contribs) == 0

        # Mise à jour du pays
        update_data = {
            'total_invested': firestore.Increment(amount),
            'last_update': datetime.utcnow()
        }
        if is_new_contributor:
            update_data['contributor_count'] = firestore.Increment(1)

        country_ref.set(update_data, merge=True)

        # Log la nouvelle contribution
        country_ref.collection('contributions').add({
            'uid': uid,
            'username': username,
            'amount': amount,
            'timestamp': datetime.utcnow(),
            'country_iso': iso_code
        })
        
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
