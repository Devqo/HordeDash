import pytest
from src.app import create_app

@pytest.fixture
def client():
    app = create_app(ui_password="test_password")
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        yield client

def test_login_redirect_for_unauthenticated(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location

def test_api_unauthorized_for_unauthenticated(client):
    response = client.get('/api/stats')
    assert response.status_code == 401
    assert b'Unauthorized' in response.data

def test_failed_login(client):
    response = client.post('/login', data={'password': 'wrong_password'})
    assert response.status_code == 200
    assert b'Invalid password' in response.data

def test_successful_login(client):
    response = client.post('/login', data={'password': 'test_password'}, follow_redirects=True)
    assert response.status_code == 200
    # API should now be accessible
    response_api = client.get('/api/stats')
    assert response_api.status_code == 200
