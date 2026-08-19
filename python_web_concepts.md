# Python Web Server Concepts

## Proxy and Reverse Proxy

### Forward Proxy

A **forward proxy** sits between the client and the internet.

```text
Client ---> Proxy ---> Internet Server
```

The client sends a request to the proxy, and the proxy forwards it to the destination server.

Common uses:
- Hide the client's IP address
- Content filtering
- Access control
- Caching
- Corporate internet access

### Reverse Proxy

A **reverse proxy** sits between clients and backend servers.

```text
Client ---> Reverse Proxy ---> Backend Servers
```

The client communicates with the reverse proxy, which forwards the request to an appropriate backend server.

Common uses:
- Load balancing
- SSL/TLS termination
- Security
- Hiding backend servers
- Caching
- Routing requests to different services

Example:

```text
                +----------------+
Users --------->| Reverse Proxy  |
                +----------------+
                 /      |                       v       v        v
          Web Server1 Web Server2 Web Server3
```

### Forward Proxy vs Reverse Proxy

| Feature | Forward Proxy | Reverse Proxy |
|---|---|---|
| Represents | Client | Server |
| Sits in front of | Clients | Servers |
| Hides | Client identity | Server identity |
| Main users | Users/organizations | Websites/applications |
| Common purpose | Access control, filtering, privacy | Load balancing, security, performance |

**Memory tip:**
- **Forward Proxy = represents/protects the client**
- **Reverse Proxy = represents/protects the server**

---

# Uvicorn

**Uvicorn** is a lightweight, high-performance **ASGI server for Python**.

In simple terms:

> Uvicorn is the program that runs your Python web application and listens for HTTP requests.

## Where Uvicorn Fits

For example, with FastAPI:

```text
Browser
   |
   | HTTP request
   v
Uvicorn
   |
   | ASGI
   v
FastAPI application
   |
   v
Your Python code
```

A simple FastAPI application might look like:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def hello():
    return {"message": "Hello"}
```

You can start it with:

```bash
uvicorn main:app --reload
```

Here:

- `main` = the Python file `main.py`
- `app` = the FastAPI object inside that file
- `--reload` = automatically restarts the server when your code changes; useful during development

## Why Do We Need Uvicorn?

FastAPI is **not itself a web server**.

FastAPI defines what should happen when a request arrives, while Uvicorn handles the server/network side.

```text
                 Uvicorn
                    |
Browser ---> HTTP ---> |
                    |
                    v
                 FastAPI
                    |
                    v
              Your function
```

For example, when someone requests:

```text
GET /users
```

Uvicorn receives the HTTP request and passes it to FastAPI through the **ASGI interface**.

FastAPI processes the request and returns a response, which Uvicorn sends back to the client.

## What Is ASGI?

**ASGI** stands for **Asynchronous Server Gateway Interface**.

It is a standard interface between Python web servers and Python web applications/frameworks.

Think of it as a common language:

```text
Uvicorn  <-- ASGI -->  FastAPI
```

Uvicorn implements the server side of ASGI, while FastAPI is an ASGI application.

## Uvicorn vs FastAPI

| | Uvicorn | FastAPI |
|---|---|---|
| Type | ASGI server | Web framework |
| Main job | Handle network/HTTP connections | Build API/application logic |
| Defines routes | No | Yes |
| Runs the application | Yes | No |
| ASGI | Implements ASGI server | Provides an ASGI application |

## Nginx + Uvicorn + FastAPI

When combined with the reverse-proxy concept:

```text
Internet
   |
   v
Nginx
(reverse proxy)
   |
   v
Uvicorn
(ASGI server)
   |
   v
FastAPI
(application)
```

A useful mental model:

> **Nginx = traffic manager / reverse proxy**  
> **Uvicorn = Python web server**  
> **FastAPI = your web application/framework**
