import os
import hashlib
import ecdsa
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt

app = Flask(__name__)

# --- CONFIGURATION ---
# La clé secrète sécurise les sessions (indispensable pour les flash messages)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'votre_phrase_secrete_ultra_securisee')

# Configuration du chemin de la base de données SQLite
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'sealed.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# --- MODÈLE DE DONNÉES ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    wallet_address = db.Column(db.String(100), unique=True)
    private_key = db.Column(db.String(200))
    balance = db.Column(db.Float, default=100.0)

# --- INITIALISATION DE LA BASE ---
# Cette partie s'assure que les tables sont créées sur Render dès le lancement
with app.app_context():
    db.create_all()
    print("Base de données initialisée et tables vérifiées !")

# --- ROUTES ---

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        if not username or not password:
            flash('Veuillez remplir tous les champs.', 'danger')
            return redirect(url_for('register'))

        user_exists = User.query.filter_by(username=username).first()
        if user_exists:
            flash('Ce pseudo est déjà pris.', 'danger')
            return redirect(url_for('register'))
        
        # Hachage et Génération Wallet (Cryptographie SECP256k1)
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        
        try:
            # Correction ici : sk.to_string().hex() au lieu de to_ascii_string()
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
            
            flash('Compte créé avec succès ! Connectez-vous.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Erreur DB lors de l'inscription : {e}")
            flash("Erreur technique lors de la création du compte.", "danger")
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            flash(f'Bienvenue, {user.username} !', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Pseudo ou mot de passe incorrect.', 'danger')
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    # Protection de la route : vérifie si l'utilisateur est bien connecté
    if 'user_id' not in session:
        flash('Veuillez vous connecter pour accéder au dashboard.', 'warning')
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.clear()
    flash('Vous avez été déconnecté.', 'info')
    return redirect(url_for('login'))

# --- LANCEMENT ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)