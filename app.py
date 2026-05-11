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

# --- CONFIGURATION ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '').strip()

# --- FIREBASE ---
if not firebase_admin._apps:
    fb_config = os.environ.get('FIREBASE_CONFIG')
    if fb_config:
        cred = credentials.Certificate(json.loads(fb_config))
        firebase_admin.initialize_app(cred)
db = firestore.client()

# --- WEBHOOK (BÉTONNÉ) ---

@app.route('/webhook', methods=['POST'])
def webhook():
    # Utilisation de get_data() pour récupérer le flux binaire exact
    payload = request.get_data(as_text=False)
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        print("❌ Signature manquante dans les headers.")
        return jsonify(success=False), 400

    try:
        # Reconstruction de l'événement
        event_obj = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
        # Nettoyage de l'objet pour Python 3.14
        event = json.loads(json.dumps(event_obj.to_dict_recursive()))
    except ValueError as e:
        # Payload invalide
        print(f"❌ Payload invalide : {e}")
        return jsonify(success=False), 400
    except stripe.error.SignatureVerificationError as e:
        # Signature invalide
        print(f"❌ SIGNATURE INVALIDE : {e}")
        print(f"DEBUG: Ta clé whsec commence par : {endpoint_secret[:8]}...")
        return jsonify(success=False), 400
    except Exception as e:
        print(f"❌ Autre erreur Webhook : {e}")
        return jsonify(success=False), 400

    print(f"📩 Webhook Validé : {event.get('type')}")

    if event.get('type') in ['checkout.session.completed', 'checkout.session.async_payment_succeeded']:
        data_obj = event.get('data', {}).get('object', {})
        
        # Identification du user
        metadata = data_obj.get('metadata', {})
        username = metadata.get('username')
        
        if not username:
            cust_id = data_obj.get('customer')
            users = db.collection('users').where(filter=firestore.FieldFilter('stripe_customer_id', '==', cust_id)).limit(1).get()
            if users:
                username = users[0].id
            else:
                return jsonify(success=True), 200

        # Crédit du compte
        try:
            amount_total = data_obj.get('amount_total', 0)
            net_amount = round((amount_total / 100) * 0.988 - 0.25, 2)
            
            user_ref = db.collection('users').document(username)
            
            @firestore.transactional
            def update_balance(transaction, user_ref, amount):
                snap = user_ref.get(transaction=transaction)
                if not snap.exists: return False
                
                u_data = snap.to_dict()
                old_bal = float(u_data.get('balance', 0.0) or 0.0)
                new_bal = round(old_bal + amount, 2)
                
                transaction.update(user_ref, {'balance': new_bal})
                
                transaction.set(db.collection('transactions').document(), {
                    'sender_un': 'STRIPE_SYSTEM',
                    'recipient_addr': u_data.get('wallet_address'),
                    'amount': amount,
                    'type': 'deposit',
                    'timestamp': datetime.utcnow()
                })
                return True

            if update_balance(db.transaction(), user_ref, net_amount):
                print(f"💰 SOLDE MIS À JOUR : {username} (+{net_amount}€)")
        except Exception as e:
            print(f"❌ Erreur Firestore : {e}")

    return jsonify(success=True), 200

# --- ROUTES DASHBOARD / AUTH ---

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
    tx_docs = db.collection('transactions').where(filter=firestore.FieldFilter('sender_un', '==', session['user_id'])).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10).get()
    return render_template('dashboard.html', user=user_data, transactions=[t.to_dict() for t in tx_docs])

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
            flash("Inconnu.", "danger")
            return redirect(url_for('dashboard'))
        batch = db.batch()
        batch.update(sender_ref, {'balance': sender_data['balance'] - amount})
        batch.update(rec_query[0].reference, {'balance': rec_query[0].to_dict()['balance'] + amount})
        batch.set(db.collection('transactions').document(), {'sender_un': session['user_id'], 'recipient_addr': recipient_addr, 'amount': amount, 'timestamp': datetime.utcnow(), 'type': 'transfer'})
        batch.commit()
        flash("Argent envoyé !", "success")
    except: flash("Erreur.", "danger")
    return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        un = request.form.get('username').lower().strip()
        if db.collection('users').document(un).get().exists:
            flash("Pris.", "danger"); return redirect(url_for('register'))
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
        flash("Erreur login.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
def home(): return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
