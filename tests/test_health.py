import os

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DB", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app import app


def test_health_endpoint():
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "healthy"}


def test_unknown_endpoint_returns_404():
    client = app.test_client()

    response = client.get("/does-not-exist")

    assert response.status_code == 404


def test_health_does_not_connect_to_database(monkeypatch):
    def fail_connection(_self):
        raise AssertionError("Health probe must not open a database connection")

    from app import mysql

    monkeypatch.setattr(type(mysql), "connection", property(fail_connection))
    response = app.test_client().get("/health")
    assert response.status_code == 200


def test_pages_render():
    client = app.test_client()
    for path in ("/", "/register", "/login"):
        assert client.get(path).status_code == 200


def test_successful_login_sets_session(monkeypatch):
    monkeypatch.setattr("app.routes.validate_user", lambda email, password: (1,))
    with app.test_client() as client:
        response = client.post("/login", data={"email": "test@example.com", "password": "test"})
        assert response.status_code == 302
        with client.session_transaction() as session:
            assert session["loggedin"] is True
