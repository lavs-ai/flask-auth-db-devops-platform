from app import mysql


def insert_user(name, email, password):
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO users(name, email, password) VALUES(%s, %s, %s)", (name, email, password))
    mysql.connection.commit()
    cur.close()

def validate_user(email, password):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email, password))
    user = cur.fetchone()
    cur.close()
    return user
