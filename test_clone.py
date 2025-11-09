"""Simple test script to test repository cloning functionality."""

import requests
import json

# API endpoint
BASE_URL = "http://localhost:8000"
INGEST_URL = f"{BASE_URL}/ingestion/ingest"

# Test repository (using a small public repo for testing)
TEST_REPO = "https://github.com/ariwalapratham/EDA.git"

def test_clone():
    """Test cloning a repository."""
    print(f"Testing repository cloning...")
    print(f"Repository: {TEST_REPO}")
    print("-" * 50)
    
    # Prepare request payload
    payload = {
        "repo_url": TEST_REPO,
        "branch": None,  # Use default branch
        "cleanup": False  # Keep the cloned repo so we can verify it
    }
    
    try:
        print("Sending request to API...")
        response = requests.post(INGEST_URL, json=payload, timeout=300)
        
        print(f"Status Code: {response.status_code}")
        print("-" * 50)
        
        if response.status_code == 202:
            result = response.json()
            print("✅ SUCCESS!")
            print(f"Message: {result.get('message')}")
            print("\nMetadata:")
            if result.get('metadata'):
                metadata = result['metadata']
                print(f"  - Repository ID: {metadata.get('repo_id')}")
                print(f"  - Owner: {metadata.get('owner')}")
                print(f"  - Repo Name: {metadata.get('repo_name')}")
                print(f"  - Status: {metadata.get('status')}")
                print(f"  - Commit Hash: {metadata.get('commit_hash')}")
                print(f"  - Repository Path: {metadata.get('repo_path')}")
                print(f"\n📁 Check the repository at: {metadata.get('repo_path')}")
        else:
            print("❌ FAILED!")
            print(f"Error: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ ERROR: Could not connect to the API.")
        print("Make sure the FastAPI server is running:")
        print("  cd backend && python main.py")
    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    test_clone()

