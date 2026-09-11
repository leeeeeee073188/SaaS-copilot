"""Start the standalone FlowForge support workspace."""
import os
from dotenv import load_dotenv
from api.demo import create_demo_app

load_dotenv()
app = create_demo_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host=os.getenv("API_HOST", "127.0.0.1"),
                port=int(os.getenv("API_PORT", "8000")))
