import os
import hashlib
import ecdsa
import firebase_admin
import json
import stripe
from firebase_admin import credentials, firestore
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from flask_bcrypt import Bcrypt
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sealed_master_2026')
bcrypt = Bcrypt(app)

# --- CONFIG STRIPE ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '').strip()

# --- CONFIG FIREBASE ---
if not firebase_admin._apps:
    fb_config = os.environ.get('FIREBASE_CONFIG')
    if fb_config:
        cred = credentials.Certificate(json.loads(fb_config))
        firebase_admin.initialize_app(cred)
db = firestore.client()

# --- WEBHOOK ---

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    try:
        # 1. On valide la signature avec la méthode officielle
        stripe_event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
        
        # 2. FORCE LA CONVERSION EN DICTIONNAIRE (C'est l'étape cruciale)
        # On transforme l'objet complexe de Stripe en JSON puis en Dict Python
        event = json.loads(json.dumps(stripe_event.to_dict_recursive()))
        
    except Exception as e:
        print(f"❌ Erreur Validation Webhook : {e}")
        return jsonify(success=False), 400

    event_type = event.get('type')
    print(f"📩 Événement reçu : {event_type}")

    if event_type in ['checkout.session.completed', 'checkout.session.async_payment_succeeded']:
        # On accède maintenant aux données en toute sécurité avec .get()
        data_obj = event.get('data', {}).get('object', {})
        session_id = data_obj.get('id')
        metadata = data_obj.get('metadata', {})
        username = metadata.get('username')

        if not username:
            print("⚠️ Aucun username dans les metadata.")
            return jsonify(success=True), 200

        try:
            # Vérification anti-doublon
            tx_check = db.collection('transactions').where('stripe_session_id', '==', session_id).limit(1).get()
            if len(tx_check) > 0:
                print(f"ℹ️ Transaction {session_id} déjà traitée.")
                return jsonify(success=True), 200

            # Calcul du montant net
            amount_total = data_obj.get('amount_total', 0)
            net_amount = round((amount_total / 100) * 0.988 - 0.25, 2)

            user_ref = db.collection('users').document(username)
            user_snap = user_ref.get()

            if user_snap.exists:
                user_data = user_snap.to_dict()
                old_bal = float(user_data.get('balance', 0) or 0)
                new_bal = round(old_bal + net_amount, 2)

                batch = db.batch()
                batch.update(user_ref, {'balance': new_bal})
                
                # Création du reçu de transaction
                batch.set(db.collection('transactions').document(), {
                    'sender_un': 'STRIPE_SYSTEM',
                    'recipient_addr': user_data.get('wallet_address'),
                    'amount': net_amount,
                    'type': 'deposit',
                    'stripe_session_id': session_id,
                    'timestamp': datetime.utcnow()
                })
                batch.commit()
                print(f"✅ CRÉDIT RÉUSSI : {username} (+{net_amount}€)")
            
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour Firebase : {e}")
            return jsonify(success=False), 500

    return jsonify(success=True), 200

# --- ROUTES DASHBOARD ---

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        amount = float(request.form.get('amount'))
        username = session['user_id']
        user_ref = db.collection('users').document(username)
        user_data = user_ref.get().to_dict()
        
        cust_id = user_data.get('stripe_customer_id')
        if not cust_id:
            customer = stripe.Customer.create(description=f"User: {username}", metadata={'username': username})
            cust_id = customer.id
            user_ref.update({'stripe_customer_id': cust_id})

        checkout_session = stripe.checkout.Session.create(
            customer=cust_id,
            payment_method_types=['card', 'customer_balance'],
            payment_method_options={'customer_balance': {'funding_type': 'bank_transfer', 'bank_transfer': {'type': 'eu_bank_transfer', 'eu_bank_transfer': {'country': 'FR'}}}},
            line_items=[{'price_data': {'currency': 'eur', 'product_data': {'name': 'Dépôt Sealed'}, 'unit_amount': int(amount * 100)}, 'quantity': 1}],
            mode='payment',
            success_url=url_for('dashboard', _external=True) + "?status=pending",
            cancel_url=url_for('dashboard', _external=True),
            metadata={'username': username}
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Erreur : {e}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_data = db.collection('users').document(session['user_id']).get().to_dict()
    tx_docs = db.collection('transactions').where('sender_un', '==', session['user_id']).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10).get()
    return render_template('dashboard.html', user=user_data, transactions=[t.to_dict() for t in tx_docs])

# --- AUTH ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        un = request.form.get('username').lower().strip()
        if db.collection('users').document(un).get().exists:
            flash("Pseudo déjà pris.", "danger")
            return redirect(url_for('register'))
        hashed = bcrypt.generate_password_hash(request.form.get('password')).decode('utf-8')
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        addr = f"0x{hashlib.sha256(sk.get_verifying_key().to_string()).hexdigest()[:40]}"
        db.collection('users').document(un).set({'username': un, 'password': hashed, 'wallet_address': addr, 'private_key': sk.to_string().hex(), 'balance': 0.0, 'created_at': datetime.utcnow()})
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        un = request.form.get('username').lower().strip()
        doc = db.collection('users').document(un).get()
        if doc.exists and bcrypt.check_password_hash(doc.to_dict()['password'], request.form.get('password')):
            session['user_id'] = un
            return redirect(url_for('dashboard'))
        flash("Identifiants incorrects.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
