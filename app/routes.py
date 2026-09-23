from flask import render_template, request, redirect, session
from app import app, mysql
from app.models import insert_user, validate_user

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        insert_user(name, email, password)
        return redirect('/')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = validate_user(email, password)
        if user:
            session['loggedin'] = True
            return redirect('/')
        else:
            return "Login Failed"
    return render_template('login.html')

@app.route("/health")
def health():
    return {"status": "healthy"}, 200


@app.route("/ready")
def ready():
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()

        return {
            "status": "ready",
            "database": "connected"
        }, 200

    except Exception:
        return {
            "status": "not_ready",
            "database": "unavailable"
        }, 503