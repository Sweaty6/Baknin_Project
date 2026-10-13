"""Run the application with a bounded production WSGI server."""
import os
from waitress import serve
from baknin import create_app

app = create_app()

if __name__ == "__main__":
    serve(app, host=os.environ.get("BAKNIN_HOST", "127.0.0.1"),
          port=int(os.environ.get("PORT", "5000")), threads=4)
