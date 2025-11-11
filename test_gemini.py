try:
    import google.generativeai as genai
except ImportError:
    genai = None
from dotenv import load_dotenv
import os
load_dotenv()

def test_gemini_embedding():
    if genai is None:
        print("❌ google-generativeai is not installed. Install it with: pip install google-generativeai")
        return

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY environment variable not set.")
        return

    try:
        genai.configure(api_key=api_key)
        # Use the embeddings API directly from genai
        response = genai.embed_content(
            model="models/embedding-001",
            content="Hello, this is a test for my Gemini embedding key!",
            task_type="retrieval_document"
        )
        embedding = response.get("embedding")
        if embedding:
            print("✅ Gemini API key is working!")
            print(f"Embedding length: {len(embedding)}")
        else:
            print("⚠️ Gemini API key seems valid but no embedding returned.")

    except Exception as e:
        print("❌ Error occurred while generating embedding:")
        print(e)

if __name__ == "__main__":
    test_gemini_embedding()
