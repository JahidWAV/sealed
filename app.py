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

# --- STRIPE ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# --- FIREBASE ---
if not firebase_admin._apps:
    fb_config = os.environ.get('FIREBASE_CONFIG')
    if fb_config:
        cred = credentials.Certificate(json.loads(fb_config))
        firebase_admin.initialize_app(cred)
db = firestore.client()

# --- WEBHOOK : VERSION TEST (SANS VÉRIFICATION DE SIGNATURE) ---

@app.route('/webhook', methods=['POST'])
def webhook():
    # On récupère le JSON brut envoyé par Stripe
    data = request.json
    
    if not data:
        print("❌ Webhook reçu mais le JSON est vide.")
        return jsonify(success=False), 400

    event_type = data.get('type')
    print(f"📩 Webhook reçu : {event_type}")

    # On traite les succès de paiement
    if event_type in ['checkout.session.completed', 'checkout.session.async_payment_succeeded']:
        session_obj = data.get('data', {}).get('object', {})
        
        # Récupération du username (Metadata)
        metadata = session_obj.get('metadata', {})
        username = metadata.get('username')
        
        # Fallback par ID Client si metadata absente
        if not username:
            cust_id = session_obj.get('customer')
            print(f"🔍 Metadata vide, recherche via ID Client : {cust_id}")
            users = db.collection('users').where(filter=firestore.FieldFilter('stripe_customer_id', '==', cust_id)).limit(1).get()
            if users:
                username = users[0].id
            else:
                print("❌ Impossible de trouver l'utilisateur.")
                return jsonify(success=True), 200

        # Calcul financier (Montant net après frais Stripe)
        amount_total = session_obj.get('amount_total', 0)
        net_amount = round((amount_total / 100) * 0.988 - 0.25, 2)

        # Mise à jour Firebase
        try:
            user_ref = db.collection('users').document(username)
            user_snap = user_ref.get()
            
            if user_snap.exists:
                current_bal = float(user_snap.to_dict().get('balance', 0.0) or 0.0)
                new_bal = round(current_bal + net_amount, 2)
                
                # Update solde
                user_ref.update({'balance': new_bal})
                
                # Log transaction
                db.collection('transactions').document().set({
                    'sender_un': 'STRIPE_SYSTEM',
                    'recipient_addr': user_snap.to_dict().get('wallet_address'),
                    'amount': net_amount,
                    'type': 'deposit',
                    'timestamp': datetime.utcnow()
                })
                print(f"✅ CRÉDIT RÉUSSI : {username} (+{net_amount}€)")
            else:
                print(f"❌ User {username} n'existe pas en DB.")
        except Exception as e:
            print(f"❌ Erreur Firebase : {e}")
            return jsonify(success=False), 500

    return jsonify(success=True), 200

# --- ROUTES DASHBOARD & PAIEMENT ---

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
            metadata={'username': username} # Très important pour le Webhook
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Erreur Stripe : {e}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_data = db.collection('users').document(session['user_id']).get().to_dict()
    tx_docs = db.collection('transactions').where(filter=firestore.FieldFilter('sender_un', '==', session['user_id'])).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10).get()
    return render_template('dashboard.html', user=user_data, transactions=[t.to_dict() for t in tx_docs])

# --- TRANSFERT & AUTH ---

@app.route('/send', methods=['POST'])
def send_money():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        amount = float(request.form.get('amount'))
        recipient_addr = request.form.get('recipient_address').strip()
        sender_ref = db.collection('users').document(session['user_id'])
        sender_data = sender_ref.get().to_dict()
        if sender_data['balance'] < amount:
            flash("Solde insuffisant.", "danger")
            return redirect(url_for('dashboard'))
        rec_query = db.collection('users').where(filter=firestore.FieldFilter('wallet_address', '==', recipient_addr)).limit(1).get()
        if not rec_query:
            flash("Destinataire inconnu.", "danger")
            return redirect(url_for('dashboard'))
        batch = db.batch()
        batch.update(sender_ref, {'balance': sender_data['balance'] - amount})
        batch.update(rec_query[0].reference, {'balance': rec_query[0].to_dict()['balance'] + amount})
        batch.set(db.collection('transactions').document(), {'sender_un': session['user_id'], 'recipient_addr': recipient_addr, 'amount': amount, 'timestamp': datetime.utcnow(), 'type': 'transfer'})
        batch.commit()
        flash("Succès !", "success")
    except: flash("Erreur.", "danger")
    return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        un = request.form.get('username').lower().strip()
        if db.collection('users').document(un).get().exists:
            flash("Pseudo pris.", "danger"); return redirect(url_for('register'))
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
            session['user_id'] = un; return redirect(url_for('dashboard'))
        flash("Login incorrect.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
def home(): return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
