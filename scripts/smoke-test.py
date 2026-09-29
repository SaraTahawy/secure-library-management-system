import time
from random import randint

import httpx

BASE = "https://nginx"


def show(name, response):
    print(f"{name}: {response.status_code} {response.text[:180]}")


def request_with_retry(client, method, url, **kwargs):
    last_response = None
    for _ in range(20):
        try:
            response = client.request(method, url, **kwargs)
            last_response = response
            if response.status_code not in {502, 503, 504}:
                return response
        except httpx.HTTPError:
            pass
        time.sleep(1)
    if last_response is not None:
        return last_response
    raise RuntimeError(f"{method} {url} did not respond")


def json_or_raise(name, response):
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{name} returned non-JSON response: {response.status_code} {response.text[:300]}") from exc


with httpx.Client(base_url=BASE, verify=False, timeout=10) as client:
    gateway = request_with_retry(client, "GET", "/")
    show("gateway", gateway)

    register = request_with_retry(
        client,
        "POST",
        "/auth/register",
        json={
            "email": "admin@example.com",
            "password": "StrongPass123",
            "full_name": "Admin User",
        },
    )
    show("register", register)
    if register.status_code not in {201, 409}:
        json_or_raise("register", register)
        raise RuntimeError("register failed")

    login = request_with_retry(
        client,
        "POST",
        "/auth/login",
        json={"email": "admin@example.com", "password": "StrongPass123"},
    )
    show("login", login)
    token = json_or_raise("login", login)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    show("protected missing token", request_with_retry(client, "GET", "/auth/me"))
    show("protected valid token", request_with_retry(client, "GET", "/auth/me", headers=headers))

    book = request_with_retry(
        client,
        "POST",
        "/library/books",
        headers=headers,
        json={
            "title": "Distributed Systems",
            "author": "Tanenbaum",
            "isbn": str(randint(1000000000, 9999999999)),
            "total_copies": 3,
        },
    )
    show("create book", book)

    bad_book = request_with_retry(
        client,
        "POST",
        "/library/books",
        headers=headers,
        json={"title": "Bad", "author": "Bad", "isbn": "bad", "total_copies": -1},
    )
    show("invalid input rejected", bad_book)

    upload = request_with_retry(
        client,
        "POST",
        "/library/documents/upload",
        headers=headers,
        files={"file": ("sample.txt", b"safe document", "text/plain")},
    )
    show("upload", upload)

    time.sleep(2)
    metrics = request_with_retry(client, "GET", "/audit/admin/metrics", headers=headers)
    show("metrics", metrics)
