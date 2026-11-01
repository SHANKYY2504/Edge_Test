from fastapi.testclient import TestClient

from demo.main import app


def test_demo_api_supports_user_crud() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/users",
            json={"name": "Katherine Johnson", "email": "katherine@example.test"},
        )
        assert created.status_code == 201
        user_id = created.json()["id"]

        assert client.get("/users/{}".format(user_id)).json()["name"] == "Katherine Johnson"

        replaced = client.put(
            "/users/{}".format(user_id),
            json={"name": "Katherine Johnson", "email": "kj@example.test"},
        )
        assert replaced.status_code == 200
        assert replaced.json()["email"] == "kj@example.test"

        patched = client.patch(
            "/users/{}".format(user_id),
            json={"name": "K. Johnson"},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "K. Johnson"

        assert client.delete("/users/{}".format(user_id)).status_code == 204
        assert client.get("/users/{}".format(user_id)).status_code == 404


def test_demo_api_exposes_delay_and_error_endpoints() -> None:
    with TestClient(app) as client:
        assert client.get("/delay?seconds=0").json() == {"delayed_seconds": 0.0}
        assert client.get("/errors/404").status_code == 404
        assert client.get("/errors/500").status_code == 500
