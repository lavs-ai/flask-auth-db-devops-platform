import os

from dotenv import load_dotenv
from flask import Flask
from flask_mysqldb import MySQL
from prometheus_flask_exporter import PrometheusMetrics

load_dotenv()

app = Flask(__name__, template_folder='../templates')  # 👈 force template path

app.secret_key = os.getenv("SECRET_KEY")

# MySQL Config
app.config['MYSQL_HOST'] = os.getenv("MYSQL_HOST")
app.config['MYSQL_PORT'] = int(os.getenv("MYSQL_PORT", "3306"))
app.config['MYSQL_USER'] = os.getenv("MYSQL_USER")
app.config['MYSQL_PASSWORD'] = os.getenv("MYSQL_PASSWORD")
app.config['MYSQL_DB'] = os.getenv("MYSQL_DB")

mysql = MySQL(app)
metrics = PrometheusMetrics(app)

# Register routes after the application and database have been initialized.
from app import routes as routes  # noqa: E402
