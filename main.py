from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Language Reminder Server is running")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8000), Handler)
    print("Server running on port 8000")
    server.serve_forever()
