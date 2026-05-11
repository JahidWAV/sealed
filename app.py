import os
from flask import Flask, render_template, request, redirect, session, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import ecdsa
import hashlib

app = Flask(__name__)
# Clé secrète pour les sessions
app.config['SECRET_KEY'] = 'sealed_super_secret_key_123'
# Base de données SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sealed.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# Modèle Utilisateur
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    wallet_address = db.Column(db.String(100), unique=True)
    private_key = db.Column(db.String(200))
    balance = db.Column(db.Float, default=100.0) # Bonus de bienvenue

@app.route('/')
def home():
    return redirect(url_for('register'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user_exists = User.query.filter_by(username=request.form['username']).first()
        if user_exists:
            return "L'utilisateur existe déjà !"
        
        hashed_pw = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        
        # Génération des clés cryptographiques
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        pk = sk.get_verifying_key()
        address = f"0x{hashlib.sha256(pk.to_string()).hexdigest()[:40]}"
        
        new_user = User(
            username=request.form['username'], 
            password=hashed_pw,
            wallet_address=address,
            private_key=sk.to_ascii_string().hex()
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and bcrypt.check_password_hash(user.password, request.form['password']):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
    return "Identifiants incorrects"

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Configuration pour Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)