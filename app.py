import os
import hashlib
import ecdsa
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt

app = Flask(__name__)

# CONFIGURATION SÉCURISÉE
# Change cette clé par une phrase complexe pour sécuriser les sessions
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'votre_cle_secrete_ultra_securisee_123')

# Configuration de la base de données (Chemin absolu pour éviter les erreurs sur Render)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'sealed.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# MODÈLE UTILISATEUR (La structure de ta table SQL)
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    wallet_address = db.Column(db.String(100), unique=True)
    private_key = db.Column(db.String(200))
    balance = db.Column(db.Float, default=100.0)

# --- ROUTES AUTHENTIFICATION ---

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
        
        # Vérification si le pseudo existe déjà
        user_exists = User.query.filter_by(username=username).first()
        if user_exists:
            flash('Ce pseudo est déjà utilisé.', 'danger')
            return redirect(url_for('register'))
        
        # Hachage du mot de passe
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        
        # GÉNÉRATION DU WALLET (Cryptographie réelle)
        # Création d'une clé privée ECDSA (Bitcoin style)
        sk = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        pk = sk.get_verifying_key()
        # Création d'une adresse publique courte à partir du hash de la clé publique
        address = f"0x{hashlib.sha256(pk.to_string()).hexdigest()[:40]}"
        
        # Sauvegarde
        new_user = User(
            username=username, 
            password=hashed_pw,
            wallet_address=address,
            private_key=sk.to_ascii_string().hex()
        )
        db.session.add(new_user)
        db.session.commit()
        
        flash('Compte créé avec succès ! Connectez-vous.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        # Comparaison sécurisée du hash
        if user and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            flash('Pseudo ou mot de passe incorrect.', 'danger')
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    # Protection de la route : si pas de session, retour au login
    if 'user_id' not in session:
        flash('Veuillez vous connecter pour accéder au portefeuille.', 'warning')
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
    with app.app_context():
        # Création automatique des tables si elles n'existent pas
        db.create_all()
    
    # Configuration du port pour Render ou local
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)