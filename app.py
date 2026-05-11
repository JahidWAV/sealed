import os
import hashlib
import ecdsa
import firebase_admin
import json
import stripe
from firebase_admin import credentials, firestore
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_bcrypt import Bcrypt
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sealed_master_key_2026_prod')
bcrypt = Bcrypt(app)

# --- CONFIGURATION STRIPE ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# --- CONNEXION FIREBASE ---
if os.path.exists("firebase-key.json"):
    cred = credentials.Certificate("firebase-key.json")
else:
    fb_config_env = os.environ.get('FIREBASE_CONFIG')
    if fb_config_env:
        cred = credentials.Certificate(json.loads(fb_config_env))
    else:
        print("ERREUR : Variable FIREBASE_CONFIG manquante sur Render")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# --- LOGIQUE DE DÉPÔT (STRIPE) ---

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if 'user_id' not in session: return redirect(url_for('login'))
    try:
        user_amount = float(request.form.get('amount'))
        if user_amount < 1.0:
            flash("Le montant minimum est de 1€.", "danger")
            return redirect(url_for('dashboard'))
            
        amount_in_cents = int(user_amount * 100)
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': 'Dépôt Sealed'},
                    'unit_amount': amount_in_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('dashboard', _external=True),
            metadata={'username': session['user_id']}
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Erreur Stripe : {str(e)}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/payment_success')
def payment_success():
    if 'user_id' not in session: return redirect(url_for('login'))
    session_id = request.args.get('session_id')
    try:
        stripe_session = stripe.checkout.Session.retrieve(session_id)
        if stripe_session.payment_status == 'paid':
            amount_paid = stripe_session.amount_total / 100
            username = stripe_session.metadata['username']
            user_ref = db.collection('users').document(username)
            user_doc = user_ref.get()
            if user_doc.exists:
                new_balance = user_doc.to_dict().get('balance', 0.0) + amount_paid
                user_ref.update({'balance': new_balance})
                
                # Log Ledger
                db.collection('transactions').document().set({
                    'sender_un': 'STRIPE_SYSTEM',
                    'recipient_addr': user_doc.to_dict()['wallet_address'],
                    'amount': amount_paid,
                    'timestamp': datetime.utcnow(),
                    'type': 'deposit'
                })
                flash(f"Compte crédité de {amount_paid}€ !", "success")
        return redirect(url_for('dashboard'))
    except Exception as e:
        return redirect(url_for('dashboard'))

# --- LOGIQUE DE RETRAIT (WITHDRAW) ---

@app.route('/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    try:
        amount = float(request.form.get('amount'))
        iban = request.form.get('iban').strip().upper()
        
        if amount <= 0: raise ValueError
        
        user_ref = db.collection('users').document(session['user_id'])
        user_data = user_ref.get().to_dict()

        if user_data['balance'] < amount:
            flash("Solde insuffisant pour ce retrait.", "danger")
            return redirect(url_for('dashboard'))

        # Débit immédiat du solde réel
        new_balance = user_data['balance'] - amount
        user_ref.update({'balance': new_balance})

        # Enregistrement de la demande de virement
        db.collection('transactions').document().set({
            'sender_un': session['user_id'],
            'recipient_addr': f"RETRAIT IBAN: {iban[:4]}...",
            'amount': amount,
            'timestamp': datetime.utcnow(),
            'status': 'pending_withdrawal',
            'type': 'withdraw'
        })

        flash(f"Demande de retrait de {amount}€ validée. Virement en cours vers {iban[:4]}...", "success")
    except:
        flash("Erreur lors de la demande de retrait.", "danger")
        
    return redirect(url_for('dashboard'))

# --- LOGIQUE DE TRANSFERT ---

@app.route('/send', methods=['POST'])
def send_money():
    if 'user_id' not in session: return redirect(url_for('login'))
    sender_un = session['user_id']
    recipient_addr = request.form.get('recipient_address').strip()
    try:
        amount = float(request.form.get('amount'))
        if amount <= 0: raise ValueError
        
        sender_ref = db.collection('users').document(sender_un)
        sender_data = sender_ref.get().to_dict()

        if sender_data['balance'] < amount:
            flash("Solde insuffisant.", "danger")
            return redirect(url_for('dashboard'))

        recipient_query = db.collection('users').where('wallet_address', '==', recipient_addr).limit(1).get()
        if not recipient_query:
            flash("Destinataire inconnu.", "danger")
            return redirect(url_for('dashboard'))

        recipient_ref = recipient_query[0].reference
        recipient_data = recipient_query[0].to_dict()

        batch = db.batch()
        batch.update(sender_ref, {'balance': sender_data['balance'] - amount})
        batch.update(recipient_ref, {'balance': recipient_data['balance'] + amount})
        tx_ref = db.collection('transactions').document()
        batch.set(tx_ref, {
            'sender_un': sender_un,
            'recipient_addr': recipient_addr,
            'amount': amount,
            'timestamp': datetime.utcnow(),
            'type': 'transfer'
        })
        batch.commit()
        flash(f"Transfert de {amount}€ réussi.", "success")
    except:
        flash("Données invalides.", "danger")
    return redirect(url_for('dashboard'))

# --- AUTH & DASHBOARD ---

@app.route('/')
def index():
    return redirect(url_for('dashboard')) if 'user_id' in session else redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        user_ref = db.collection('users').document(username)
        if user_ref.get().exists:
            flash('Pseudo déjà pris.', 'danger')
            return redirect(url_for('register'))
        
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        address = f"0x{hashlib.sha256(sk.get_verifying_key().to_string()).hexdigest()[:40]}"
        
        user_ref.set({
            'username': username, 'password': hashed_pw, 'wallet_address': address,
            'private_key': sk.to_string().hex(), 'balance': 0.0, 'created_at': datetime.utcnow()
        })
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        user_doc = db.collection('users').document(username).get()
        if user_doc.exists and bcrypt.check_password_hash(user_doc.to_dict()['password'], password):
            session['user_id'] = username
            return redirect(url_for('dashboard'))
        flash('Erreur de connexion.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_data = db.collection('users').document(session['user_id']).get().to_dict()
    # On récupère toutes les transactions liées (envoyées)
    tx_docs = db.collection('transactions').where('sender_un', '==', session['user_id']).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(15).get()
    transactions = [t.to_dict() for t in tx_docs]
    return render_template('dashboard.html', user=user_data, transactions=transactions)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
