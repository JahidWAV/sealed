import os
import hashlib
import ecdsa
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION STRICTE ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'CLE_REELLE_A_DEFINIR_DANS_RENDER')
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'sealed.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# --- MODÈLES DE DONNÉES (LE REGISTRE) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    wallet_address = db.Column(db.String(100), unique=True, nullable=False)
    private_key = db.Column(db.String(200), nullable=False)
    balance = db.Column(db.Float, default=0.0) # REEL: Départ à zéro
    
    # Lien vers l'historique
    sent_txs = db.relationship('Transaction', foreign_keys='Transaction.sender_id', backref='author', lazy=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_address = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Initialisation forcée des tables
with app.app_context():
    db.create_all()

# --- LOGIQUE DE TRANSACTION ---

@app.route('/send', methods=['POST'])
def send_money():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    sender = User.query.get(session['user_id'])
    recipient_addr = request.form.get('recipient_address').strip()
    
    try:
        amount = float(request.form.get('amount'))
    except (ValueError, TypeError):
        flash("Montant invalide.", "danger")
        return redirect(url_for('dashboard'))

    # Sécurité 1: Vérifier le solde
    if amount <= 0:
        flash("Le montant doit être supérieur à zéro.", "danger")
        return redirect(url_for('dashboard'))
    
    if sender.balance < amount:
        flash("Solde insuffisant pour cette transaction.", "danger")
        return redirect(url_for('dashboard'))

    # Sécurité 2: Vérifier le destinataire
    recipient = User.query.filter_by(wallet_address=recipient_addr).first()
    if not recipient:
        flash("Adresse destinataire introuvable sur le réseau Sealed.", "danger")
        return redirect(url_for('dashboard'))

    if recipient.wallet_address == sender.wallet_address:
        flash("Impossible d'envoyer à votre propre adresse.", "danger")
        return redirect(url_for('dashboard'))

    # EXECUTION DE LA TRANSACTION (ATOMIQUE)
    try:
        sender.balance -= amount
        recipient.balance += amount
        
        new_tx = Transaction(
            sender_id=sender.id,
            recipient_address=recipient_addr,
            amount=amount
        )
        
        db.session.add(new_tx)
        db.session.commit()
        flash(f"Transfert de {amount}€ effectué avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Erreur technique lors du transfert.", "danger")
        print(f"Erreur: {e}")

    return redirect(url_for('dashboard'))

# --- AUTHENTIFICATION ---

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Ce nom d\'utilisateur est déjà déposé.', 'danger')
            return redirect(url_for('register'))
        
        # Crypto
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        pk = sk.get_verifying_key()
        address = f"0x{hashlib.sha256(pk.to_string()).hexdigest()[:40]}"
        
        new_user = User(
            username=username,
            password=hashed_pw,
            wallet_address=address,
            private_key=sk.to_string().hex()
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Compte créé. Votre solde est de 0.00€.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        flash('Identifiants invalides.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    # On récupère aussi l'historique des envois
    transactions = Transaction.query.filter_by(sender_id=user.id).order_by(Transaction.timestamp.desc()).all()
    return render_template('dashboard.html', user=user, transactions=transactions)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)