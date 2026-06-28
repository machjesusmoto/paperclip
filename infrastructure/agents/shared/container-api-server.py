#!/usr/bin/env python3
"""
Hermes Container API Server

This server runs inside each containerized Hermes agent and receives
work from Paperclip via the hermes_container adapter.

It provides:
- POST /api/execute - Execute a task from Paperclip
- GET /health - Health check endpoint
- GET /api/status - Agent status information
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Configuration
PORT = int(os.getenv("PORT", "8080"))
HERMES_AGENT_ID = os.getenv("AGENT_ID", os.getenv("HERMES_AGENT_ID", "unknown"))
HERMES_AGENT_NAME = os.getenv("TAYA_AGENT_NAME", os.getenv("AGENT_NAME", os.getenv("HERMES_AGENT_NAME", "Unknown Agent")))
HERMES_AGENT_ROLE = os.getenv("TAYA_AGENT_ROLE", os.getenv("AGENT_ROLE", os.getenv("HERMES_AGENT_ROLE", "General")))
PAPERCLIP_API_URL = os.getenv("PAPERCLIP_API_URL", "http://paperclip:3100/api")

# State
active_sessions: dict[str, dict] = {}
server_start_time = time.time()


class HermesContainerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the container API."""
    
    def log_message(self, format, *args):
        """Override to add agent info to logs."""
        agent_info = f"[{HERMES_AGENT_NAME}]"
        print(f"{agent_info} {self.address_string()} - {format % args}")
    
    def send_json_response(self, status: int, data: dict):
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self.handle_health_check()
        elif self.path == "/api/status":
            self.handle_status()
        else:
            self.send_json_response(404, {"error": "Not found"})
    
    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/api/execute":
            self.handle_execute()
        else:
            self.send_json_response(404, {"error": "Not found"})
    
    def handle_health_check(self):
        """Health check endpoint."""
        uptime = time.time() - server_start_time
        self.send_json_response(200, {
            "status": "healthy",
            "agent": HERMES_AGENT_NAME,
            "agentId": HERMES_AGENT_ID,
            "role": HERMES_AGENT_ROLE,
            "uptime": uptime,
            "activeSessions": len(active_sessions),
        })
    
    def handle_status(self):
        """Agent status endpoint."""
        uptime = time.time() - server_start_time
        self.send_json_response(200, {
            "agent": HERMES_AGENT_NAME,
            "agentId": HERMES_AGENT_ID,
            "role": HERMES_AGENT_ROLE,
            "uptime": uptime,
            "activeSessions": len(active_sessions),
            "paperclipApiUrl": PAPERCLIP_API_URL,
        })
    
    def handle_execute(self):
        """Execute a task from Paperclip."""
        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            request = json.loads(body)
        except Exception as e:
            self.send_json_response(400, {"error": f"Invalid request: {e}"})
            return
        
        prompt = request.get("prompt", "")
        run_id = request.get("runId", str(uuid.uuid4()))
        agent_id = request.get("agentId", HERMES_AGENT_ID)
        issue_id = request.get("issueId")
        
        if not prompt:
            self.send_json_response(400, {"error": "No prompt provided"})
            return
        
        print(f"[{HERMES_AGENT_NAME}] Executing task for run {run_id}")
        if issue_id:
            print(f"[{HERMES_AGENT_NAME}] Issue: {issue_id}")
        
        # Track active session
        session_id = f"session-{run_id}"
        active_sessions[session_id] = {
            "runId": run_id,
            "agentId": agent_id,
            "issueId": issue_id,
            "startedAt": time.time(),
        }
        
        try:
            # Execute the task using Hermes
            result = self.execute_with_hermes(prompt, run_id, request)
            
            # Clean up session
            active_sessions.pop(session_id, None)
            
            self.send_json_response(200, {
                "success": True,
                "response": result.get("response", ""),
                "sessionId": session_id,
                "metadata": result.get("metadata", {}),
            })
        except Exception as e:
            active_sessions.pop(session_id, None)
            print(f"[{HERMES_AGENT_NAME}] Execution error: {e}")
            self.send_json_response(500, {
                "success": False,
                "error": str(e),
                "exitCode": 1,
            })
    
    def execute_with_hermes(self, prompt: str, run_id: str, request: dict) -> dict:
        """Execute a task using the Hermes CLI."""
        process = None
        try:
            # Build hermes command
            cmd = [
                "hermes", "chat",
                "-q", prompt,
                "-Q",  # Quiet mode
            ]
            
            # Add session resume if available
            session_id = request.get("sessionId")
            if session_id:
                cmd.extend(["-r", session_id])
            
            # Set environment variables for Paperclip integration
            env = os.environ.copy()
            env.update({
                "PAPERCLIP_RUN_ID": run_id,
                "PAPERCLIP_AGENT_ID": request.get("agentId", HERMES_AGENT_ID),
                "PAPERCLIP_COMPANY_ID": request.get("companyId", ""),
                "PAPERCLIP_API_URL": request.get("paperclipApiUrl", PAPERCLIP_API_URL),
                "PAPERCLIP_ISSUE_ID": request.get("issueId", ""),
                "PAPERCLIP_TASK_ID": request.get("taskId", ""),
            })
            
            # Execute command
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            
            stdout, stderr = process.communicate(timeout=300)
            
            if process.returncode != 0:
                raise RuntimeError(f"Hermes execution failed: {stderr}")
            
            return {
                "response": stdout,
                "metadata": {
                    "exitCode": process.returncode,
                    "agent": HERMES_AGENT_NAME,
                },
            }
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
            raise RuntimeError("Hermes execution timed out after 300 seconds")
        except Exception as e:
            raise RuntimeError(f"Hermes execution error: {e}")


def run_server():
    """Run the HTTP server."""
    server = HTTPServer(("0.0.0.0", PORT), HermesContainerHandler)
    print(f"[{HERMES_AGENT_NAME}] Container API server starting on port {PORT}")
    print(f"[{HERMES_AGENT_NAME}] Agent ID: {HERMES_AGENT_ID}")
    print(f"[{HERMES_AGENT_NAME}] Role: {HERMES_AGENT_ROLE}")
    print(f"[{HERMES_AGENT_NAME}] Paperclip API: {PAPERCLIP_API_URL}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{HERMES_AGENT_NAME}] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    run_server()