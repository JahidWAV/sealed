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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sealed_master_key_2024_prod')
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

# --- ROUTES DE PAIEMENT STRIPE (ARGENT RÉEL) ---

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        # Montant saisi par l'utilisateur (ex: 50.50)
        user_amount = float(request.form.get('amount'))
        if user_amount < 1.0: # Minimum 1€ pour Stripe
            flash("Le montant minimum est de 1€.", "danger")
            return redirect(url_for('dashboard'))
            
        # Stripe utilise les centimes (ex: 10€ = 1000)
        amount_in_cents = int(user_amount * 100)
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': 'Dépôt sur compte Sealed'},
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

@app.route('/payment-success')
def payment_success():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    session_id = request.args.get('session_id')
    if not session_id: return redirect(url_for('dashboard'))
    
    try:
        stripe_session = stripe.checkout.Session.retrieve(session_id)
        
        if stripe_session.payment_status == 'paid':
            amount_paid = stripe_session.amount_total / 100
            username = stripe_session.metadata['username']
            
            # Mise à jour du solde dans Firebase (Mode Réel)
            user_ref = db.collection('users').document(username)
            user_doc = user_ref.get()
            
            if user_doc.exists:
                current_balance = user_doc.to_dict().get('balance', 0.0)
                new_balance = current_balance + amount_paid
                
                # Update Firestore
                user_ref.update({'balance': new_balance})
                
                # Log de la transaction de dépôt
                tx_ref = db.collection('transactions').document()
                tx_ref.set({
                    'sender_un': 'STRIPE_DEPOSIT',
                    'recipient_addr': user_doc.to_dict()['wallet_address'],
                    'amount': amount_paid,
                    'timestamp': datetime.utcnow()
                })
                
                flash(f"Succès ! {amount_paid}€ ont été ajoutés à votre solde.", "success")
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"Erreur vérification paiement : {e}")
        return redirect(url_for('dashboard'))

# --- LOGIQUE DE TRANSFERT ENTRE UTILISATEURS ---

@app.route('/send', methods=['POST'])
def send_money():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    sender_un = session['user_id']
    recipient_addr = request.form.get('recipient_address').strip()
    
    try:
        amount = float(request.form.get('amount'))
        if amount <= 0: raise ValueError
    except:
        flash("Montant invalide.", "danger")
        return redirect(url_for('dashboard'))

    sender_ref = db.collection('users').document(sender_un)
    sender_doc = sender_ref.get()
    sender_data = sender_doc.to_dict()

    if sender_data['balance'] < amount:
        flash("Solde insuffisant.", "danger")
        return redirect(url_for('dashboard'))

    recipient_query = db.collection('users').where('wallet_address', '==', recipient_addr).limit(1).get()
    if not recipient_query:
        flash("Destinataire introuvable.", "danger")
        return redirect(url_for('dashboard'))

    recipient_ref = recipient_query[0].reference
    recipient_data = recipient_query[0].to_dict()

    # Transaction Atomique
    try:
        batch = db.batch()
        batch.update(sender_ref, {'balance': sender_data['balance'] - amount})
        batch.update(recipient_ref, {'balance': recipient_data['balance'] + amount})
        
        tx_ref = db.collection('transactions').document()
        batch.set(tx_ref, {
            'sender_un': sender_un,
            'recipient_addr': recipient_addr,
            'amount': amount,
            'timestamp': datetime.utcnow()
        })
        batch.commit()
        flash(f"Transfert de {amount}€ réussi.", "success")
    except Exception as e:
        flash("Erreur technique durant le transfert.", "danger")

    return redirect(url_for('dashboard'))

# --- AUTHENTIFICATION ---

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
            flash('Ce nom d\'utilisateur existe déjà.', 'danger')
            return redirect(url_for('register'))
        
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        address = f"0x{hashlib.sha256(sk.get_verifying_key().to_string()).hexdigest()[:40]}"
        
        user_ref.set({
            'username': username,
            'password': hashed_pw,
            'wallet_address': address,
            'private_key': sk.to_string().hex(),
            'balance': 0.0,
            'created_at': datetime.utcnow()
        })
        flash('Compte créé avec succès !', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        user_doc = db.collection('users').document(username).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            if bcrypt.check_password_hash(user_data['password'], password):
                session['user_id'] = username
                return redirect(url_for('dashboard'))
        flash('Identifiants incorrects.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    user_data = db.collection('users').document(session['user_id']).get().to_dict()
    # Récupération des transactions (nécessite l'index Firestore créé précédemment)
    tx_docs = db.collection('transactions').where('sender_un', '==', session['user_id']).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10).get()
    transactions = [t.to_dict() for t in tx_docs]
    
    return render_template('dashboard.html', user=user_data, transactions=transactions)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
# --- LOGIQUE DE TRANSACTION BANCAIRE ---

@app.route('/send', methods=['POST'])
def send_money():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    sender_un = session['user_id']
    recipient_addr = request.form.get('recipient_address').strip()
    
    try:
        amount = float(request.form.get('amount'))
        if amount <= 0:
            flash("Le montant doit être positif.", "danger")
            return redirect(url_for('dashboard'))
    except (ValueError, TypeError):
        flash("Montant invalide.", "danger")
        return redirect(url_for('dashboard'))

    # 1. Vérifier l'expéditeur
    sender_ref = db.collection('users').document(sender_un)
    sender_doc = sender_ref.get()
    
    if not sender_doc.exists:
        return redirect(url_for('logout'))
    
    sender_data = sender_doc.to_dict()

    if sender_data['balance'] < amount:
        flash("Solde insuffisant.", "danger")
        return redirect(url_for('dashboard'))

    # 2. Vérifier le destinataire par son adresse de portefeuille
    recipient_query = db.collection('users').where('wallet_address', '==', recipient_addr).limit(1).get()
    
    if not recipient_query:
        flash("Adresse destinataire inconnue sur le réseau.", "danger")
        return redirect(url_for('dashboard'))

    recipient_ref = recipient_query[0].reference
    recipient_data = recipient_query[0].to_dict()

    if recipient_data['wallet_address'] == sender_data['wallet_address']:
        flash("Action impossible : vous êtes l'expéditeur et le destinataire.", "danger")
        return redirect(url_for('dashboard'))

    # 3. TRANSACTION ATOMIQUE (Batch)
    try:
        batch = db.batch()
        # Débit expéditeur
        batch.update(sender_ref, {'balance': sender_data['balance'] - amount})
        # Crédit destinataire
        batch.update(recipient_ref, {'balance': recipient_data['balance'] + amount})
        
        # Enregistrement dans l'historique
        tx_ref = db.collection('transactions').document()
        batch.set(tx_ref, {
            'sender_un': sender_un,
            'recipient_addr': recipient_addr,
            'amount': amount,
            'timestamp': datetime.utcnow()
        })
        
        batch.commit()
        flash(f"Transfert de {amount}€ réussi !", "success")
    except Exception as e:
        print(f"Erreur Transaction : {e}")
        flash("Erreur lors de la transaction. Fonds protégés.", "danger")

    return redirect(url_for('dashboard'))

# --- AUTHENTIFICATION ---

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        
        if not username or not password:
            flash("Champs incomplets.", "danger")
            return redirect(url_for('register'))

        user_ref = db.collection('users').document(username)
        if user_ref.get().exists:
            flash('Ce nom d\'utilisateur est déjà pris.', 'danger')
            return redirect(url_for('register'))
        
        # Sécurité & Cryptographie
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        address = f"0x{hashlib.sha256(sk.get_verifying_key().to_string()).hexdigest()[:40]}"
        
        # Création du document utilisateur dans Firestore
        user_ref.set({
            'username': username,
            'password': hashed_pw,
            'wallet_address': address,
            'private_key': sk.to_string().hex(),
            'balance': 0.0, # Réel : Solde à zéro au départ
            'created_at': datetime.utcnow()
        })
        
        flash('Compte créé avec succès sur le Cloud Firebase !', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        
        user_doc = db.collection('users').document(username).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            if bcrypt.check_password_hash(user_data['password'], password):
                session['user_id'] = username
                return redirect(url_for('dashboard'))
        
        flash('Identifiants incorrects.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Récupérer les données utilisateur en temps réel
    user_ref = db.collection('users').document(session['user_id'])
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        return redirect(url_for('logout'))
        
    user_data = user_doc.to_dict()
    
    # Récupérer l'historique des 10 dernières transactions envoyées
    txs = db.collection('transactions')\
            .where('sender_un', '==', session['user_id'])\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(10).get()
            
    transactions = [t.to_dict() for t in txs]
    
    return render_template('dashboard.html', user=user_data, transactions=transactions)

@app.route('/logout')
def logout():
    session.clear()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for('login'))

# --- LANCEMENT ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
