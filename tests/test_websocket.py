import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_websocket_connection():
    """Test WebSocket connection"""
    client = TestClient(app)
    
    with client.websocket_connect("/ws") as websocket:
        # Test connection
        data = websocket.receive_json()
        assert "type" in data
        print(f"✅ WebSocket connected: {data}")
        
        # Test subscribing
        websocket.send_json({
            "action": "subscribe",
            "symbols": ["SPY"]
        })
        
        response = websocket.receive_json()
        assert response["status"] == "subscribed"
        print(f"✅ Subscribed to SPY")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])